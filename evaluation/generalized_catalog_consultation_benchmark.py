"""Compare legacy lexical search with the grounded query compiler and tool path."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.query_understanding import understand_catalog_query
from backend.services.retail_tools import execute_catalog_tool

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "winmart_products.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _in_category(product: Any, expected_category: str) -> bool:
    expected = normalize_category_text(expected_category)
    segments = [
        normalize_category_text(segment)
        for segment in (product.category_hierarchy or "").split(">")
    ]
    if not segments:
        segments = [normalize_category_text(product.category)]
    return expected in segments


def _run_query_cases(
    cases: list[dict[str, Any]], repository: SnapshotProductRepository
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    base_latencies: list[float] = []
    new_latencies: list[float] = []
    product_cases = [row for row in cases if row["kind"] == "product_resolution"]
    category_cases = [row for row in cases if row["kind"] != "product_resolution"]
    product_top1_base = product_top5_base = product_top1_new = product_top5_new = 0
    category_precision_base = category_precision_new = 0.0
    category_errors_base = category_errors_new = 0
    use_case_marked = use_case_total = 0

    for case in cases:
        started = time.perf_counter_ns()
        baseline = repository.search(case["query"], limit=10)
        base_ms = (time.perf_counter_ns() - started) / 1_000_000
        base_latencies.append(base_ms)

        started = time.perf_counter_ns()
        observation = execute_catalog_tool(
            "search_products",
            json.dumps({"query": case["query"], "limit": 10}, ensure_ascii=False),
            repository=repository,
        )
        new_ms = (time.perf_counter_ns() - started) / 1_000_000
        new_latencies.append(new_ms)
        actual = list(observation.products)
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "kind": case["kind"],
            "query": case["query"],
            "baseline_status": "SUCCESS" if baseline else "NOT_FOUND",
            "new_status": observation.status,
            "baseline_top5": [item.product_id for item in baseline[:5]],
            "new_top5": [item.product_id for item in actual[:5]],
            "baseline_latency_ms": round(base_ms, 4),
            "new_latency_ms": round(new_ms, 4),
        }

        if case["kind"] == "product_resolution":
            gold = set(case["gold_product_ids"])
            base_rank = next(
                (index for index, item in enumerate(baseline, 1) if item.product_id in gold), None
            )
            new_rank = next(
                (index for index, item in enumerate(actual, 1) if item.product_id in gold), None
            )
            product_top1_base += int(base_rank == 1)
            product_top5_base += int(base_rank is not None and base_rank <= 5)
            product_top1_new += int(new_rank == 1)
            product_top5_new += int(new_rank is not None and new_rank <= 5)
            row.update(
                {
                    "gold_product_ids": sorted(gold),
                    "baseline_relevant_rank": base_rank,
                    "new_relevant_rank": new_rank,
                }
            )
        else:
            expected_category = case["expected_category"]
            base_relevant = [item for item in baseline[:5] if _in_category(item, expected_category)]
            new_relevant = [item for item in actual[:5] if _in_category(item, expected_category)]
            category_precision_base += len(base_relevant) / max(1, min(5, len(baseline)))
            category_precision_new += len(new_relevant) / max(1, min(5, len(actual)))
            category_errors_base += len(baseline[:5]) - len(base_relevant)
            category_errors_new += len(actual[:5]) - len(new_relevant)
            row.update(
                {
                    "expected_category": expected_category,
                    "baseline_category_precision_at5": len(base_relevant)
                    / max(1, min(5, len(baseline))),
                    "new_category_precision_at5": len(new_relevant) / max(1, min(5, len(actual))),
                    "query_understanding": observation.content.get("query_understanding"),
                }
            )
            if case["kind"] == "use_case_category":
                use_case_total += 1
                query_understanding = observation.content.get("query_understanding") or {}
                parsed_use_case = query_understanding.get("use_case")
                use_case_marked += int(
                    bool(parsed_use_case)
                    and parsed_use_case in query_understanding.get("unverified_constraints", [])
                )
                row["expected_use_case"] = case["expected_use_case"]

        rows.append(row)

    product_count = len(product_cases)
    category_count = len(category_cases)
    metrics = {
        "query_count": len(cases),
        "product_resolution_count": product_count,
        "category_query_count": category_count,
        "product_recall_at_1": {
            "baseline": round(product_top1_base / max(1, product_count), 4),
            "query_compiler": round(product_top1_new / max(1, product_count), 4),
        },
        "product_recall_at_5": {
            "baseline": round(product_top5_base / max(1, product_count), 4),
            "query_compiler": round(product_top5_new / max(1, product_count), 4),
        },
        "category_precision_at_5": {
            "baseline": round(category_precision_base / max(1, category_count), 4),
            "query_compiler": round(category_precision_new / max(1, category_count), 4),
        },
        "category_product_type_error_rate": {
            "baseline": round(
                category_errors_base
                / max(
                    1,
                    sum(
                        min(5, len(repository.search(row["query"], limit=10)))
                        for row in category_cases
                    ),
                ),
                4,
            ),
            "query_compiler": round(
                category_errors_new
                / max(
                    1,
                    sum(
                        min(5, len(row["new_top5"]))
                        for row in rows
                        if row["kind"] != "product_resolution"
                    ),
                ),
                4,
            ),
        },
        "use_case_context_marked_unverified_rate": round(
            use_case_marked / max(1, use_case_total), 4
        ),
        "latency_ms": {
            "baseline_p50": round(statistics.median(base_latencies), 4) if base_latencies else None,
            "baseline_p95": _percentile(base_latencies, 0.95),
            "query_compiler_p50": round(statistics.median(new_latencies), 4)
            if new_latencies
            else None,
            "query_compiler_p95": _percentile(new_latencies, 0.95),
        },
    }
    return metrics, rows


def _run_conversations(
    path: Path, repository: SnapshotProductRepository
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    conversations = json.loads(path.read_text(encoding="utf-8"))
    details = []
    scope_retained = price_ranked = alternative_retained = 0
    for conversation in conversations:
        prior_queries: list[str] = []
        turns = []
        expected = conversation["expected_category"]
        turn_categories = []
        turn2_prices: list[int] = []
        for turn in conversation["turns"]:
            query = turn["user"]
            parsed = understand_catalog_query(
                query,
                repository.list_category_hierarchies(),
                previous_queries=tuple(prior_queries),
            )
            observation = execute_catalog_tool(
                "search_products",
                json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
                repository=repository,
                previous_queries=tuple(prior_queries),
            )
            turn_categories.append(parsed.product_type)
            if len(turns) == 1:
                turn2_prices = [product.price_vnd for product in observation.products]
            turns.append(
                {
                    "query": query,
                    "intent": parsed.intent,
                    "product_type": parsed.product_type,
                    "ranking_criterion": parsed.ranking_criterion,
                    "status": observation.status,
                    "prices": [product.price_vnd for product in observation.products],
                    "category_precision_at5": (
                        round(
                            sum(_in_category(product, expected) for product in observation.products)
                            / len(observation.products),
                            4,
                        )
                        if observation.products
                        else 0.0
                    ),
                }
            )
            prior_queries.append(query)
        retained = all(category is not None for category in turn_categories)
        sorted_prices = turn2_prices == sorted(turn2_prices)
        alternative_kept = (
            len(turn_categories) >= 3
            and turn_categories[2] == turn_categories[0]
            and turn_categories[2] is not None
        )
        scope_retained += int(retained)
        price_ranked += int(sorted_prices)
        alternative_retained += int(alternative_kept)
        details.append(
            {
                "conversation_id": conversation["conversation_id"],
                "expected_category": expected,
                "scope_retained_all_turns": retained,
                "lowest_price_turn_sorted": sorted_prices,
                "alternative_follow_up_retained_scope": alternative_kept,
                "turns": turns,
            }
        )
    count = len(conversations)
    return (
        {
            "conversation_count": count,
            "scope_retention_all_turns": round(scope_retained / max(1, count), 4),
            "lowest_price_follow_up_sorted": round(price_ranked / max(1, count), 4),
            "alternative_follow_up_scope_retention": round(alternative_retained / max(1, count), 4),
        },
        details,
    )


def run(cases_path: Path, conversations_path: Path, source_path: Path) -> dict[str, Any]:
    repository = SnapshotProductRepository(source_path)
    cases = _read_jsonl(cases_path)
    metrics, outcomes = _run_query_cases(cases, repository)
    conversation_metrics, conversation_outcomes = _run_conversations(conversations_path, repository)
    return {
        "kind": "generalized_catalog_consultation_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "offline_snapshot_and_deterministic_catalog_tool",
        "source_file": str(source_path),
        "dataset_sha256": repository.dataset_sha256,
        "case_file": str(cases_path),
        "conversation_file": str(conversations_path),
        "metrics": metrics,
        "conversation_metrics": conversation_metrics,
        "limitations": [
            "No LLM generation/provider latency measured; metrics cover parser and catalog tools.",
            "Snapshot has no attributes to verify use-case suitability.",
            "Catalog-name variants test retrieval, not human preference relevance.",
            "Taxonomy labels come from source data; case generation is model-independent.",
            "Baseline uses direct snapshot search; compiler uses parser and production tools.",
        ],
        "cases": outcomes,
        "conversations": conversation_outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--conversations", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.cases, args.conversations, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "metrics": result["metrics"],
                "conversation_metrics": result["conversation_metrics"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
