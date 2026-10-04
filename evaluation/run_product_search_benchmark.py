from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.data.catalog_import import analyze_dataset
from backend.products.postgres_repository import PostgresProductRepository

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_VERSION = "1"
DATABASE_SCHEMA_VERSION = "winmart-catalog-search-v2"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return round(ordered[index], 3)


def _case_score(product_ids: list[str], gold_ids: set[str], top_k: int) -> dict[str, Any]:
    ranks = [
        index for index, product_id in enumerate(product_ids, start=1) if product_id in gold_ids
    ]
    first_rank = min(ranks) if ranks else None
    return {
        "recall_at_1": bool(first_rank is not None and first_rank <= 1),
        "recall_at_3": bool(first_rank is not None and first_rank <= min(3, top_k)),
        "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
        "first_relevant_rank": first_rank,
    }


def _metrics(scores: list[dict[str, Any]], latencies: list[float]) -> dict[str, Any]:
    count = len(scores)
    return {
        "queries": count,
        "recall_at_1": round(sum(item["recall_at_1"] for item in scores) / count, 4)
        if count
        else 0.0,
        "recall_at_3": round(sum(item["recall_at_3"] for item in scores) / count, 4)
        if count
        else 0.0,
        "mrr": round(sum(item["reciprocal_rank"] for item in scores) / count, 4)
        if count
        else 0.0,
        "median_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not cases:
        raise ValueError(f"No benchmark cases found in {path}")
    case_ids: set[str] = set()
    for case in cases:
        if (
            not isinstance(case.get("case_id"), str)
            or not case["case_id"]
            or case["case_id"] in case_ids
            or not isinstance(case.get("query"), str)
            or not case["query"]
            or not isinstance(case.get("gold_product_ids"), list)
            or not case["gold_product_ids"]
            or not all(isinstance(value, str) and value for value in case["gold_product_ids"])
            or not isinstance(case.get("kind"), str)
            or not case["kind"]
        ):
            raise ValueError(f"Invalid benchmark case: {case}")
        case_ids.add(case["case_id"])
    return cases


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_benchmark(
    repository: PostgresProductRepository,
    cases: list[dict[str, Any]],
    *,
    iterations: int,
    top_k: int,
) -> dict[str, Any]:
    per_case: dict[str, dict[str, Any]] = {}
    all_scores: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    grouped: dict[str, list[dict[str, Any]]] = {}

    # Warm the local connection/query path once per query, outside measured trials.
    for case in cases:
        repository.search(query=case["query"], limit=top_k)

    for case in cases:
        trial_scores: list[dict[str, Any]] = []
        trial_latencies: list[float] = []
        result_ids: list[list[str]] = []
        gold_ids = set(case["gold_product_ids"])
        for _ in range(iterations):
            started = time.perf_counter_ns()
            products = repository.search(query=case["query"], limit=top_k)
            latency_ms = (time.perf_counter_ns() - started) / 1_000_000
            product_ids = [product.product_id for product in products]
            score = _case_score(product_ids, gold_ids, top_k)
            trial_scores.append(score)
            trial_latencies.append(latency_ms)
            result_ids.append(product_ids)
            all_scores.append(score)
            all_latencies.append(latency_ms)
        per_case[case["case_id"]] = {
            "query": case["query"],
            "kind": case["kind"],
            "gold_product_ids": sorted(gold_ids),
            "result_product_ids_by_trial": result_ids,
            "trial_scores": trial_scores,
            "median_latency_ms": round(statistics.median(trial_latencies), 3),
        }
        grouped.setdefault(case["kind"], []).extend(trial_scores)

    return {
        "overall": _metrics(all_scores, all_latencies),
        "by_query_kind": {
            kind: _metrics(scores, []) for kind, scores in sorted(grouped.items())
        },
        "cases": per_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure PostgreSQL WinMart product search independently of the LLM."
    )
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "evaluation/cases/product_resolution.jsonl"
    )
    parser.add_argument("--source", type=Path, default=ROOT / "data/winmart_products.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation/results/product_search_benchmark.json",
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--database-url", help="Overrides DATABASE_URL; never included in the report."
    )
    args = parser.parse_args()
    if args.iterations < 1 or not 3 <= args.top_k <= 20:
        parser.error("--iterations must be positive and --top-k must be between 3 and 20")

    cases = _load_cases(args.cases)
    _, _, dataset_report = analyze_dataset(args.source)
    database_url = args.database_url or get_settings().database_url
    repository = PostgresProductRepository(database_url)
    try:
        metrics = run_benchmark(repository, cases, iterations=args.iterations, top_k=args.top_k)
    except Exception as exc:
        print(
            "PostgreSQL product search benchmark could not connect or execute. "
            "Check DATABASE_URL, database availability, and that the WinMart import/migration ran. "
            f"Error type: {type(exc).__name__}.",
            file=sys.stderr,
        )
        return 2

    report = {
        "kind": "product_search_benchmark",
        "benchmark_version": BENCHMARK_VERSION,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "database_schema_version": DATABASE_SCHEMA_VERSION,
        "database_schema_sha256": _sha256(ROOT / "backend/data/schema.sql"),
        "tool_schema_version": None,
        "model": None,
        "catalog_repository": repository.source_name,
        "dataset_snapshot": {
            "file": args.source.name,
            "sha256": dataset_report["dataset_version"],
            "rows_total": dataset_report["rows_total"],
            "rows_accepted": dataset_report["rows_accepted"],
            "rows_quarantined": dataset_report["rows_quarantined"],
        },
        "case_set": {"file": args.cases.name, "sha256": _sha256(args.cases)},
        "case_count": len(cases),
        "iterations": args.iterations,
        "top_k": args.top_k,
        "metrics": metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {key: report[key] for key in ("kind", "git_commit", "dataset_snapshot", "metrics")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
