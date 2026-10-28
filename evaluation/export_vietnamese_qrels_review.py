"""Export blinded two-reviewer CSV queues from a benchmark candidate artifact."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from backend.products.snapshot_repository import SnapshotProductRepository

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "winmart_products.json"

REVIEW_FIELDS = (
    "relevance",
    "category_fit",
    "identity_fit",
    "brand_fit",
    "price_fit",
    "negation_fit",
    "availability_fit",
    "clarification_expected",
    "abstention_expected",
    "confidence_1_to_5",
    "evidence_fields",
    "rationale",
)


def _load_dataset(path: Path) -> dict[str, Any]:
    if path.suffix.casefold() == ".jsonl":
        cases = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {"dataset_id": path.stem, "cases": cases}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
        raise ValueError("Dataset JSON must contain a cases array")
    return value


def _load_candidates(path: Path) -> dict[str, list[str]]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    rows = artifact.get("deterministic_parser_and_catalog", {}).get("case_results", [])
    if not isinstance(rows, list):
        raise ValueError("Benchmark artifact has no deterministic case_results list")
    return {
        str(row["case_id"]): [str(value) for value in row.get("product_ids", [])]
        for row in rows
        if isinstance(row, dict) and row.get("case_id")
    }


def export_review_queues(
    dataset_path: Path,
    results_path: Path,
    output_prefix: Path,
    catalog_path: Path = DEFAULT_CATALOG,
    split: str = "development",
    seed: int = 20261028,
) -> dict[str, str | int]:
    dataset = _load_dataset(dataset_path)
    candidates_by_case = _load_candidates(results_path)
    repository = SnapshotProductRepository(catalog_path)
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    catalog_sha256 = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    review_rows: list[dict[str, Any]] = []

    for case in dataset["cases"]:
        case_id = str(case.get("case_id") or "")
        query = str(case.get("query") or "").strip()
        if not case_id or not query:
            continue
        query_id = hashlib.sha256(f"{dataset_sha256}:{case_id}".encode()).hexdigest()[:16]
        product_ids = candidates_by_case.get(case_id, [])
        random.Random(f"{seed}:{query_id}").shuffle(product_ids)
        if not product_ids:
            product_ids = [""]
        for candidate_id, product_id in enumerate(product_ids, 1):
            product = repository.get_product_snapshot(product_id) if product_id else None
            evidence = (
                {
                    "name": product.name,
                    "brand": product.brand,
                    "category": product.category,
                    "category_hierarchy": product.category_hierarchy,
                    "price_vnd": product.price_vnd,
                    "description": product.description,
                    "ingredients": product.ingredients,
                    "stock_is_current": product.stock_is_current,
                    "data_timestamp": product.data_timestamp.isoformat(),
                    "source": repository.source_name,
                }
                if product
                else {}
            )
            review_rows.append(
                {
                    "query_id": query_id,
                    "candidate_key": hashlib.sha256(
                        f"{query_id}:{product_id or 'NO_CANDIDATE'}".encode()
                    ).hexdigest()[:20],
                    "query_text": query,
                    "product_id": product_id or "",
                    "product_name": product.name if product else "",
                    "product_brand": product.brand or "" if product else "",
                    "product_category": product.category_hierarchy or product.category
                    if product
                    else "",
                    "product_price_vnd": product.price_vnd if product else "",
                    "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    "dataset_id": str(dataset.get("dataset_id", dataset_path.stem)),
                    "dataset_sha256": dataset_sha256,
                    "catalog_sha256": catalog_sha256,
                    "split": split,
                    "reviewer_status": "UNREVIEWED",
                    **{field: "" for field in REVIEW_FIELDS},
                }
            )

    if not review_rows:
        raise ValueError("No reviewable query cases were found")
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    written: dict[str, str | int] = {"row_count": len(review_rows)}
    fields = list(review_rows[0])
    for reviewer in ("annotator_a", "annotator_b"):
        output_path = output_prefix.with_name(f"{output_prefix.name}_{reviewer}.csv")
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(review_rows)
        written[reviewer] = str(output_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--split",
        choices=("development", "validation", "blind_test"),
        default="development",
        help="Only label as blind_test after an independently authored split is frozen.",
    )
    parser.add_argument("--seed", type=int, default=20261028)
    args = parser.parse_args()
    print(
        json.dumps(
            export_review_queues(
                args.dataset,
                args.results,
                args.output_prefix,
                args.catalog,
                args.split,
                args.seed,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
