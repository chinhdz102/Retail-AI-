"""Measure the configured JSON snapshot repository, not a surrogate ranker.

This is an offline retrieval benchmark. It does not exercise PostgreSQL, a live
store feed, branch inventory, checkout, or a language model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.products.snapshot_repository import SnapshotProductRepository, _score

ROOT = Path(__file__).resolve().parents[1]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _expected_behavior_passes(
    behavior: str, gold_ids: set[str], results: list[dict[str, Any]]
) -> bool | None:
    result_ids = [item["product_id"] for item in results]
    if behavior == "compare_variants":
        return gold_ids.issubset(result_ids)
    if behavior == "select_small_bottle":
        return bool(result_ids) and result_ids[0] in gold_ids
    if behavior == "clarify_pack_size":
        return gold_ids.issubset(result_ids)
    return None


def run(case_path: Path, source_path: Path, top_k: int = 5) -> dict[str, Any]:
    if not 5 <= top_k <= 20:
        raise ValueError("top_k must be between 5 and 20")
    cases = [
        json.loads(line)
        for line in case_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError(f"No product resolution cases found in {case_path}")

    repository = SnapshotProductRepository(source_path)
    outcomes = []
    latencies: list[float] = []
    for case in cases:
        started = time.perf_counter_ns()
        products = repository.search(case["query"], limit=top_k)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        latencies.append(elapsed_ms)
        ranked = []
        for product in products:
            candidate = repository._by_candidate_id[product.product_id]
            score = _score(case["query"], candidate, repository._known_words)
            ranked.append(
                {
                    "product_id": product.product_id,
                    "name": product.name,
                    "uom": product.uom,
                    "score": round(score, 4),
                }
            )
        gold_ids = set(case["gold_product_ids"])
        relevant_ranks = [
            rank
            for rank, item in enumerate(ranked, start=1)
            if item["product_id"] in gold_ids
        ]
        first_rank = min(relevant_ranks) if relevant_ranks else None
        top_scores = [item["score"] for item in ranked[:2]]
        ambiguous = bool(
            len(top_scores) > 1
            and top_scores[0] > 0
            and top_scores[1] >= top_scores[0] * 0.95
        )
        behavior = case.get("expected_resolution_behavior", "single_or_browse")
        behavior_pass = _expected_behavior_passes(behavior, gold_ids, ranked)
        outcomes.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "kind": case["kind"],
                "gold_product_ids": sorted(gold_ids),
                "expected_resolution_behavior": behavior,
                "results": ranked,
                "recall_at_1": first_rank == 1,
                "recall_at_3": first_rank is not None and first_rank <= min(3, top_k),
                "recall_at_5": first_rank is not None and first_rank <= min(5, top_k),
                "mrr": round(1 / first_rank, 4) if first_rank else 0.0,
                "first_relevant_rank": first_rank,
                "gold_set_recall_at_3": round(
                    len(gold_ids.intersection(item["product_id"] for item in ranked[:3]))
                    / len(gold_ids),
                    4,
                ),
                "gold_set_recall_at_5": round(
                    len(gold_ids.intersection(item["product_id"] for item in ranked[:5]))
                    / len(gold_ids),
                    4,
                ),
                "ambiguous_top_result": ambiguous,
                "expected_behavior_pass": behavior_pass,
                "latency_ms": round(elapsed_ms, 4),
            }
        )

    count = len(outcomes)
    behavior_cases = [item for item in outcomes if item["expected_behavior_pass"] is not None]
    return {
        "kind": "runtime_catalog_resolution_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "offline_configured_snapshot_repository",
        "postgresql_integration": "NOT_RUN",
        "source_file": str(source_path.resolve()),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "case_file": str(case_path.resolve()),
        "case_count": count,
        "top_k": top_k,
        "catalog": {
            "repository": repository.source_name,
            "accepted_products": len(repository.products),
            "quarantined_rows": repository.rows_quarantined,
            "current_stock_supported": repository.supports_current_stock,
            "spice_filter_supported": repository.supports_spicy_filter,
        },
        "metrics": {
            "recall_at_1": round(sum(item["recall_at_1"] for item in outcomes) / count, 4),
            "recall_at_3": round(sum(item["recall_at_3"] for item in outcomes) / count, 4),
            "recall_at_5": round(sum(item["recall_at_5"] for item in outcomes) / count, 4),
            "gold_set_recall_at_3": round(
                sum(item["gold_set_recall_at_3"] for item in outcomes) / count, 4
            ),
            "gold_set_recall_at_5": round(
                sum(item["gold_set_recall_at_5"] for item in outcomes) / count, 4
            ),
            "mrr": round(sum(item["mrr"] for item in outcomes) / count, 4),
            "ambiguity_rate": round(
                sum(item["ambiguous_top_result"] for item in outcomes) / count, 4
            ),
            "expected_behavior_passes": sum(
                item["expected_behavior_pass"] is True for item in behavior_cases
            ),
            "expected_behavior_cases": len(behavior_cases),
            "median_latency_ms": round(statistics.median(latencies), 4),
            "p95_latency_ms": _percentile(latencies, 0.95),
        },
        "failures": [
            item
            for item in outcomes
            if not item["recall_at_1"] or item["expected_behavior_pass"] is False
        ],
        "cases": outcomes,
        "limitations": [
            "This measures the repository used by the current snapshot runtime, not PostgreSQL.",
            "Ambiguity is a score-tie proxy and does not replace end-to-end clarification grading.",
            (
                "Snapshot prices and recorded stock are stale; no transaction or "
                "current-availability claim is valid."
            ),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evaluation/cases/product_resolution_v3_offline.jsonl",
    )
    parser.add_argument("--source", type=Path, default=ROOT / "data/winmart_products.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    report = run(args.cases, args.source, args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "metrics": report["metrics"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
