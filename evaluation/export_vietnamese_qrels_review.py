"""Export blinded two-reviewer CSV queues from a benchmark candidate artifact."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from backend.data.catalog_import import canonical_dataset_sha256
from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation.split_policy import authorize_dataset_split

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
    "use_case_fit",
    "audience_fit",
    "variant_fit",
    "quantity_unit_fit",
    "budget_fit",
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


def _case_rows(container: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(container, dict):
        return
    for key in ("case_results", "cases"):
        rows = container.get(key)
        if isinstance(rows, list):
            yield from (row for row in rows if isinstance(row, dict) and row.get("case_id"))


def _candidate_sequences(row: dict[str, Any]) -> list[list[str]]:
    sequences = []
    for field, values in row.items():
        normalized_field = str(field).casefold()
        is_candidate_field = normalized_field in {
            "product_ids",
            "candidate_ids",
            "candidate_product_ids",
            "retrieved_product_ids",
        } or bool(re.search(r"_top(?:[1-9][0-9]*)$", normalized_field))
        if not is_candidate_field or not isinstance(values, list):
            continue
        sequences.append(list(dict.fromkeys(str(value) for value in values if value)))
    return sequences


def _load_candidates(
    paths: Sequence[Path], max_candidates_per_query: int
) -> dict[str, list[str]]:
    """Build a rank-interleaved candidate union without exposing source ranks."""
    source_sequences: dict[str, list[list[str]]] = defaultdict(list)
    found_results = False
    for path in paths:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        containers = [artifact]
        deterministic = artifact.get("deterministic_parser_and_catalog")
        if isinstance(deterministic, dict):
            containers.append(deterministic)
        variants = artifact.get("variants")
        if isinstance(variants, dict):
            containers.extend(value for value in variants.values() if isinstance(value, dict))
        elif isinstance(variants, list):
            containers.extend(value for value in variants if isinstance(value, dict))

        for container in containers:
            for row in _case_rows(container):
                case_id = str(row["case_id"])
                sequences = _candidate_sequences(row)
                if sequences:
                    found_results = True
                    source_sequences[case_id].extend(sequences)

    if not found_results:
        raise ValueError("Benchmark artifacts contain no supported candidate result lists")

    candidates_by_case: dict[str, list[str]] = {}
    for case_id, sequences in source_sequences.items():
        candidates: list[str] = []
        seen: set[str] = set()
        for rank in range(max(map(len, sequences), default=0)):
            for sequence in sequences:
                if rank >= len(sequence):
                    continue
                product_id = sequence[rank]
                if product_id not in seen:
                    seen.add(product_id)
                    candidates.append(product_id)
                    if len(candidates) >= max_candidates_per_query:
                        break
            if len(candidates) >= max_candidates_per_query:
                break
        candidates_by_case[case_id] = candidates
    return candidates_by_case


def _independently_shuffle_review_rows(
    rows: list[dict[str, Any]], seed: int, reviewer: str
) -> list[dict[str, Any]]:
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_query[str(row["query_id"])].append(dict(row))
    query_ids = list(by_query)
    random.Random(f"{seed}:{reviewer}:queries").shuffle(query_ids)

    shuffled: list[dict[str, Any]] = []
    for query_id in query_ids:
        group = by_query[query_id]
        random.Random(f"{seed}:{reviewer}:{query_id}").shuffle(group)
        shuffled.extend(group)
    return shuffled


def export_review_queues(
    dataset_path: Path,
    results_path: Path | Sequence[Path],
    output_prefix: Path,
    catalog_path: Path = DEFAULT_CATALOG,
    split: str = "development",
    seed: int = 20261028,
    max_candidates_per_query: int = 50,
) -> dict[str, str | int]:
    split_authorization = authorize_dataset_split(dataset_path, split)
    dataset = _load_dataset(dataset_path)
    results_paths = [results_path] if isinstance(results_path, Path) else list(results_path)
    if not results_paths:
        raise ValueError("At least one benchmark results artifact is required")
    if not 1 <= max_candidates_per_query <= 200:
        raise ValueError("max_candidates_per_query must be between 1 and 200")
    candidates_by_case = _load_candidates(results_paths, max_candidates_per_query)
    repository = SnapshotProductRepository(catalog_path)
    dataset_sha256 = canonical_dataset_sha256(dataset_path.read_bytes())
    catalog_sha256 = canonical_dataset_sha256(catalog_path.read_bytes())
    review_rows: list[dict[str, Any]] = []
    skipped_unmatched_cases = 0

    for case in dataset["cases"]:
        case_id = str(case.get("case_id") or "")
        query = str(case.get("query") or "").strip()
        if not case_id or not query:
            continue
        if case_id not in candidates_by_case:
            skipped_unmatched_cases += 1
            continue
        query_id = hashlib.sha256(f"{dataset_sha256}:{case_id}".encode()).hexdigest()[:16]
        product_ids = candidates_by_case.get(case_id, [])
        if not product_ids:
            product_ids = [""]
        unknown_candidate_ids = [
            product_id
            for product_id in product_ids
            if product_id and repository.get_product_snapshot(product_id) is None
        ]
        if unknown_candidate_ids:
            raise ValueError(
                f"Candidate artifact for case {case_id} contains IDs outside the validated "
                f"catalog: {unknown_candidate_ids}"
            )
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
    written: dict[str, str | int] = {
        "row_count": len(review_rows),
        "query_count": len({row["query_id"] for row in review_rows}),
        "skipped_dataset_cases_without_retrieval_results": skipped_unmatched_cases,
        "max_candidates_per_query": max_candidates_per_query,
    }
    fields = list(review_rows[0])
    reviewer_orders: dict[str, dict[str, list[str]]] = {}
    for reviewer in ("annotator_a", "annotator_b"):
        output_path = output_prefix.with_name(f"{output_prefix.name}_{reviewer}.csv")
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            reviewer_rows = _independently_shuffle_review_rows(review_rows, seed, reviewer)
            writer.writerows(reviewer_rows)
        orders: dict[str, list[str]] = defaultdict(list)
        for row in reviewer_rows:
            orders[str(row["query_id"])].append(str(row["candidate_key"]))
        reviewer_orders[reviewer] = dict(orders)
        written[reviewer] = str(output_path)

    # If the independent seeds happen to create an identical order for every
    # multi-candidate query, rotate one reviewer's first matching query. This keeps
    # reviewers from receiving aligned row positions while remaining repeatable.
    same_order_group = next(
        (
            query_id
            for query_id, candidate_keys in reviewer_orders["annotator_a"].items()
            if len(candidate_keys) > 1
            and reviewer_orders["annotator_b"].get(query_id) == candidate_keys
        ),
        None,
    )
    if same_order_group is not None:
        reviewer_b_path = Path(str(written["annotator_b"]))
        with reviewer_b_path.open(encoding="utf-8-sig", newline="") as handle:
            reviewer_b_rows = list(csv.DictReader(handle))
        group_indexes = [
            index
            for index, row in enumerate(reviewer_b_rows)
            if row["query_id"] == same_order_group
        ]
        if len(group_indexes) > 1:
            rotated = [reviewer_b_rows[index] for index in group_indexes[1:]] + [
                reviewer_b_rows[group_indexes[0]]
            ]
            for index, row in zip(group_indexes, rotated, strict=True):
                reviewer_b_rows[index] = row
            with reviewer_b_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(reviewer_b_rows)

    candidate_counts = {
        str(row["query_id"]): 0
        for row in review_rows
    }
    for row in review_rows:
        if row["product_id"]:
            candidate_counts[str(row["query_id"])] += 1

    manifest_path = output_prefix.with_name(f"{output_prefix.name}_evaluator_only_manifest.json")
    manifest = {
        "dataset_id": str(dataset.get("dataset_id", dataset_path.stem)),
        "dataset_sha256": dataset_sha256,
        "catalog_sha256": catalog_sha256,
        "split": split,
        "split_authorization": split_authorization,
        "seed": seed,
        "candidate_pool_policy": "rank_interleaved_union_from_all_supplied_artifacts",
        "max_candidates_per_query": max_candidates_per_query,
        "candidate_artifact_sha256": [
            canonical_dataset_sha256(path.read_bytes()) for path in results_paths
        ],
        "candidate_count_by_query": candidate_counts,
        "reviewer_queues": {
            "source_and_rank_hidden": True,
            "query_and_candidate_order_randomized_per_reviewer": True,
            "gold_labels_hidden": True,
            "do_not_distribute_manifest_to_annotators": True,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    written["evaluator_manifest"] = str(manifest_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--results",
        type=Path,
        action="append",
        required=True,
        help="Benchmark results artifact; repeat this flag to pool multiple retrieval systems.",
    )
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--split",
        choices=("development", "validation", "blind_test"),
        default="development",
        help=(
            "Non-development splits require exact-hash authorization in "
            "evaluation/dataset_split_registry.json; repo-visible case sets are development-only."
        ),
    )
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--max-candidates-per-query", type=int, default=50)
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
                args.max_candidates_per_query,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
