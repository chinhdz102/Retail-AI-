"""Freeze a provisional, catalog-grounded V2 evaluation set before tuning.

The prompts are templated and labels come from the product snapshot/taxonomy.
This is a development artifact, not independent human annotation.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.products.snapshot_repository import SnapshotProductRepository

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "winmart_products.json"
OUTPUT = ROOT / "evaluation" / "cases" / "retail_ai_semantic_intelligence_v2_dev_rev1_20261028.json"
SEED = 2026102801


def _norm(value: str) -> str:
    from backend.products.category_taxonomy import normalize_category_text

    return normalize_category_text(value)


def _used_product_ids() -> set[str]:
    used: set[str] = set()
    for path in (ROOT / "evaluation" / "cases").glob("generalized_catalog_consultation_*_v1.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            used.update(str(value) for value in row.get("gold_product_ids", []))
    return used


def _leaf(product: Any) -> str:
    return (product.category_hierarchy or product.category or "").split(">")[-1].strip()


def _product_queries(products: list[Any]) -> list[dict[str, Any]]:
    prompts = (
        "Mình đang cần mua {name}, bên bạn tra giúp đúng mặt hàng này nhé.",
        "Cho mình hỏi cửa hàng có {name} không? Tìm đúng tên này giúp mình.",
        "Nhà mình sắp hết {name}, mình muốn xem sản phẩm đúng loại đó.",
        "Bạn tìm giúp {name} trong danh mục hiện tại với.",
        "Mình cần xem giá và thông tin của {name}.",
    )
    rows = []
    for i, product in enumerate(products, 1):
        rows.append(
            {
                "case_id": f"V2-P-{i:03d}",
                "dimension": "exact_product_identity",
                "query": prompts[(i - 1) % len(prompts)].format(name=product.name),
                "expected": {
                    "kind": "product_resolution",
                    "product_ids": [
                        item.product_id
                        for item in products
                        if _norm(item.name) == _norm(product.name)
                    ],
                    "category": _leaf(product),
                },
                "provisional_label": (
                    "exact catalog title; duplicate exact titles are all acceptable"
                ),
            }
        )
    return rows


def _category_queries(labels: list[str]) -> list[dict[str, Any]]:
    prompts = (
        "Nhà mình sắp hết {category}, bên bạn còn nhóm này không?",
        "Mình muốn xem vài loại {category} để chọn, chưa cần chốt món cụ thể.",
        "Cửa hàng có bán {category} không, tìm giúp mình với.",
        "Đang tính mua {category} dùng ở nhà, cho mình xem danh mục nhé.",
        "Bạn cho mình xem nhóm hàng {category} hiện có trong catalog.",
    )
    return [
        {
            "case_id": f"V2-C-{i:03d}",
            "dimension": "natural_category_browse",
            "query": prompts[(i - 1) % len(prompts)].format(category=label),
            "expected": {"kind": "category_browse", "category": label},
            "provisional_label": "taxonomy leaf; prompt wording is templated",
        }
        for i, label in enumerate(labels, 1)
    ]


def _constraint_queries(groups: dict[str, list[Any]], rng: random.Random) -> list[dict[str, Any]]:
    choices: list[tuple[str, str, int]] = []
    for category, products in groups.items():
        brands: dict[str, list[Any]] = defaultdict(list)
        for product in products:
            if product.brand and product.brand.strip():
                brands[product.brand.strip()].append(product)
        if len(brands) < 2:
            continue
        brand_names = sorted(brands)
        for excluded in brand_names:
            alternatives = [
                p for brand, values in brands.items() if brand != excluded for p in values
            ]
            if not alternatives:
                continue
            prices = sorted(p.price_vnd for p in alternatives if p.price_vnd > 0)
            if not prices:
                continue
            # Keep one in-scope candidate under the ceiling to test both
            # correct constraint parsing and useful retrieval.
            budget = prices[min(len(prices) - 1, max(0, len(prices) // 2))]
            choices.append((category, excluded, budget))
    rng.shuffle(choices)
    rows = []
    prompts = (
        "Mình cần {category}, giá tối đa {budget} đồng và đừng lấy hãng {brand}.",
        "Cho mình xem {category} tối đa {budget}đ nhé, tránh thương hiệu {brand}.",
        "Tìm giúp {category} tầm {budget} đồng trở xuống, không chọn {brand}.",
        "Có {category} nào không phải hãng {brand}, giá không quá {budget}đ không?",
        "Mình muốn mua {category}; loại {brand} thì bỏ qua, ngân sách tối đa {budget} đồng.",
    )
    for i, (category, brand, budget) in enumerate(choices[:100], 1):
        rows.append(
            {
                "case_id": f"V2-H-{i:03d}",
                "dimension": "multi_constraint_hard_negative",
                "query": prompts[(i - 1) % len(prompts)].format(
                    category=category, brand=brand, budget=f"{budget:,}".replace(",", ".")
                ),
                "expected": {
                    "kind": "hard_constraint",
                    "category": category,
                    "max_price_vnd": budget,
                    "excluded_brand": brand,
                },
                "provisional_label": (
                    "category/brand/price from catalog; language scope needs human review"
                ),
            }
        )
    return rows


def _conversations(labels: list[str], groups: dict[str, list[Any]]) -> list[dict[str, Any]]:
    result = []
    for i, label in enumerate(labels[:50], 1):
        examples = groups.get(label, [])
        if not examples:
            continue
        result.append(
            {
                "conversation_id": f"V2-D-{i:03d}",
                "dimension": "multi_turn_scope_and_ranking",
                "expected_category": label,
                "provisional_label": "taxonomy-based scope; dialogue quality requires human review",
                "turns": [
                    {"user": f"Mình đang cần {label}, cho mình xem vài loại."},
                    {"user": "Trong mấy loại đó, loại nào rẻ nhất?"},
                    {"user": "Còn lựa chọn nào khác cùng nhóm không?"},
                    {"user": "Giá của loại đầu tiên là bao nhiêu?"},
                ],
            }
        )
    return result


def build() -> dict[str, Any]:
    rng = random.Random(SEED)
    repository = SnapshotProductRepository(CATALOG)
    groups: dict[str, list[Any]] = defaultdict(list)
    for item in repository.products:
        if _leaf(item) and item.name.strip():
            groups[_leaf(item)].append(item)

    used = _used_product_ids()
    candidates = [item for item in repository.products if item.product_id not in used]
    rng.shuffle(candidates)
    product_samples: list[Any] = []
    seen_names: set[str] = set()
    for item in candidates:
        key = _norm(item.name)
        if key and key not in seen_names:
            product_samples.append(item)
            seen_names.add(key)
        if len(product_samples) == 100:
            break
    if len(product_samples) < 100:
        raise ValueError(f"Only {len(product_samples)} unused unique products available")

    category_labels = [label for label, products in groups.items() if len(products) >= 4]
    rng.shuffle(category_labels)
    category_labels = sorted(category_labels[:100])
    if len(category_labels) < 100:
        raise ValueError(f"Only {len(category_labels)} categories have enough products")

    products = _product_queries(product_samples)
    categories = _category_queries(category_labels)
    constraints = _constraint_queries(groups, rng)
    if len(constraints) < 100:
        raise ValueError(f"Only {len(constraints)} usable multi-constraint cases found")

    catalog_bytes = CATALOG.read_bytes()
    payload = {
        "dataset_id": "retail_ai_semantic_intelligence_v2_dev_rev1_20261028",
        "seed": SEED,
        "source_catalog": "data/winmart_products.json",
        "source_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "authoring_method": (
            "Provisional synthetic evaluation: templates applied to catalog rows and taxonomy. "
            "Not independently authored or human-validated. Freeze before tuning; review labels."
        ),
        "cases": products + categories + constraints[:100],
        "conversations": _conversations(category_labels, groups),
        "human_review_required": True,
    }
    dataset_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    OUTPUT.write_bytes(dataset_bytes)
    return {
        "output": str(OUTPUT),
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "query_count": len(payload["cases"]),
        "by_kind": {
            kind: sum(row["expected"]["kind"] == kind for row in payload["cases"])
            for kind in ("product_resolution", "category_browse", "hard_constraint")
        },
        "conversation_count": len(payload["conversations"]),
        "unused_product_count": len(product_samples),
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
