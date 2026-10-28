"""Build deterministic source-labeled catalog cases without consulting model output."""

from __future__ import annotations

import json
import random
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.products.snapshot_repository import SnapshotProductRepository

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "winmart_products.json"
OUTPUT_DIR = ROOT / "evaluation" / "cases"


def _unaccent(text: str) -> str:
    value = unicodedata.normalize("NFD", text)
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return value.replace("đ", "d").replace("Đ", "D")


def _typo(title: str) -> str:
    tokens = list(re.finditer(r"[^\W\d_]{6,}", title, flags=re.UNICODE))
    if not tokens:
        return title
    match = tokens[-1]
    word = match.group(0)
    offset = max(2, len(word) // 2)
    return title[: match.start()] + word[:offset] + word[offset + 1 :] + title[match.end() :]


def _products_by_leaf(repository: SnapshotProductRepository) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for product in repository.products:
        path = tuple(part.strip() for part in (product.category_hierarchy or "").split(">"))
        if path and path[-1] and product.name.strip():
            groups[path[-1]].append(product)
    return groups


def _stratified_products(
    groups: dict[str, list[Any]], count: int, seed: int, excluded_ids: set[str] | None = None
) -> list[Any]:
    rng = random.Random(seed)
    excluded = excluded_ids or set()
    buckets = {
        leaf: [item for item in values if item.product_id not in excluded]
        for leaf, values in groups.items()
    }
    buckets = {leaf: values for leaf, values in buckets.items() if values}
    for values in buckets.values():
        rng.shuffle(values)
    leaves = sorted(buckets)
    rng.shuffle(leaves)
    selected = []
    cursor = 0
    while len(selected) < count and leaves:
        next_leaves = []
        for leaf in leaves:
            if cursor < len(buckets[leaf]):
                selected.append(buckets[leaf][cursor])
                next_leaves.append(leaf)
                if len(selected) >= count:
                    break
        leaves = next_leaves
        cursor += 1
    if len(selected) < count:
        raise ValueError(f"Only {len(selected)} catalog rows available for requested {count}")
    return selected


def _category_labels(repository: SnapshotProductRepository, limit: int, seed: int) -> list[str]:
    groups = _products_by_leaf(repository)
    labels = [
        label for label, products in groups.items() if len(products) >= 3 and len(label.split()) > 1
    ]
    random.Random(seed).shuffle(labels)
    return sorted(labels[:limit])


def _product_cases(products: list[Any], split: str) -> list[dict[str, Any]]:
    cases = []
    for index, product in enumerate(products, start=1):
        variants = (
            ("conversational_exact", f"Mình muốn tìm {product.name}"),
            ("accentless", f"cho mình xem {_unaccent(product.name)}"),
            ("single_typo", f"tìm giúp {_typo(product.name)}"),
        )
        for variant, query in variants:
            cases.append(
                {
                    "case_id": f"{split}_product_{index:03d}_{variant}",
                    "split": split,
                    "kind": "product_resolution",
                    "variant": variant,
                    "query": query,
                    "source_product_name": product.name,
                    "source_category": product.category_hierarchy,
                    "gold_product_ids": [product.product_id],
                    "label_source": "validated catalog row; never system output",
                }
            )
    return cases


def _category_cases(
    labels: list[str], split: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    browse = []
    use_case = []
    for index, label in enumerate(labels, start=1):
        browse.append(
            {
                "case_id": f"{split}_category_{index:03d}",
                "split": split,
                "kind": "category_browse",
                "query": f"Cho mình xem nhóm {label}",
                "expected_category": label,
                "label_source": "catalog taxonomy segment",
            }
        )
        use_case.append(
            {
                "case_id": f"{split}_use_case_{index:03d}",
                "split": split,
                "kind": "use_case_category",
                "query": f"Mình cần nhóm {label} để phục vụ nhu cầu hằng ngày",
                "expected_category": label,
                "expected_use_case": "phục vụ nhu cầu hằng ngày",
                "label_source": "catalog taxonomy segment; purpose is not a suitability label",
            }
        )
    return browse, use_case


def _conversation_cases(labels: list[str], split: str) -> list[dict[str, Any]]:
    cases = []
    for index, label in enumerate(labels, start=1):
        cases.append(
            {
                "conversation_id": f"{split}_conversation_{index:03d}",
                "split": split,
                "expected_category": label,
                "label_source": "catalog taxonomy segment",
                "turns": [
                    {"user": f"Mình muốn xem nhóm {label}", "intent": "product_search"},
                    {
                        "user": "loại nào rẻ nhất",
                        "intent": "product_search",
                        "ranking": "price_ascending",
                    },
                    {"user": "Có loại nào khác không?", "intent": "alternative"},
                ],
            }
        )
    return cases


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def build() -> dict[str, int]:
    repository = SnapshotProductRepository(SOURCE)
    groups = _products_by_leaf(repository)
    dev_products = _stratified_products(groups, 50, 2026102701)
    excluded_ids = {item.product_id for item in dev_products}
    heldout_products = _stratified_products(groups, 120, 2026102702, excluded_ids)
    dev_labels = _category_labels(repository, 20, 2026102703)
    heldout_labels = _category_labels(repository, 60, 2026102704)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dev_product_cases = _product_cases(dev_products, "dev")
    heldout_product_cases = _product_cases(heldout_products, "heldout")
    dev_browse, dev_use_case = _category_cases(dev_labels, "dev")
    heldout_browse, heldout_use_case = _category_cases(heldout_labels, "heldout")
    _write_jsonl(
        OUTPUT_DIR / "generalized_catalog_consultation_dev_v1.jsonl",
        dev_product_cases + dev_browse + dev_use_case,
    )
    _write_jsonl(
        OUTPUT_DIR / "generalized_catalog_consultation_heldout_v1.jsonl",
        heldout_product_cases + heldout_browse + heldout_use_case,
    )
    for split, labels in (("dev", dev_labels), ("heldout", heldout_labels[:50])):
        path = OUTPUT_DIR / f"generalized_catalog_conversations_{split}_v1.json"
        path.write_text(
            json.dumps(_conversation_cases(labels, split), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "dev_product_queries": len(dev_product_cases),
        "dev_category_queries": len(dev_browse),
        "dev_use_case_queries": len(dev_use_case),
        "dev_conversations": len(dev_labels),
        "heldout_product_queries": len(heldout_product_cases),
        "heldout_category_queries": len(heldout_browse),
        "heldout_use_case_queries": len(heldout_use_case),
        "heldout_conversations": min(50, len(heldout_labels)),
        "heldout_total_queries": len(heldout_product_cases)
        + len(heldout_browse)
        + len(heldout_use_case),
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
