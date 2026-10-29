"""Measure the actual deterministic customer product-search tool on a snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.data.catalog_import import canonical_dataset_sha256
from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation.generalized_retrieval_variants import (
    DEV_CASES,
    SOURCE,
    _production_tool_rank,
    _query_metric_values,
    _relevance_cluster_ids,
    _validated_product_cases,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reports/artifacts/production_tool_search_dev_20261029.json"


def _latency_summary(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"p50": None, "p95": None, "p99": None, "mean": None, "max": None}
    ordered = sorted(samples)

    def percentile(fraction: float) -> float:
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]

    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(percentile(0.95), 3),
        "p99": round(percentile(0.99), 3),
        "mean": round(statistics.mean(ordered), 3),
        "max": round(ordered[-1], 3),
    }


def _metrics(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        raise ValueError("Cannot score an empty group")
    return {
        name: round(statistics.mean(row[name] for row in rows), 4)
        for name in ("recall_at_1", "recall_at_5", "mrr_at_5", "ndcg_at_5")
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def run(
    source: Path = SOURCE,
    cases_path: Path = DEV_CASES,
    output_path: Path = OUTPUT,
) -> dict[str, Any]:
    source = source.resolve()
    cases_path = cases_path.resolve()
    output_path = output_path.resolve()
    catalog_load_started = time.perf_counter_ns()
    repository = SnapshotProductRepository(source)
    catalog_load_ms = (time.perf_counter_ns() - catalog_load_started) / 1_000_000
    accepted_ids = {product.product_id for product in repository.products}
    cases = _validated_product_cases(cases_path, accepted_ids)

    latencies: list[float] = []
    metric_rows: list[dict[str, float]] = []
    metric_rows_by_variant: dict[str, list[dict[str, float]]] = defaultdict(list)
    status_counts: Counter[str] = Counter()
    case_rows = []
    gold_by_case = []
    for case in cases:
        started = time.perf_counter_ns()
        product_ids, status = _production_tool_rank(case["query"], repository)
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        status_counts[status] += 1
        gold_ids = set(case["gold_product_ids"])
        scores = _query_metric_values(product_ids, gold_ids)
        metric_rows.append(scores)
        variant = str(case.get("variant") or "unspecified")
        metric_rows_by_variant[variant].append(scores)
        gold_by_case.append(gold_ids)
        case_rows.append(
            {
                "case_id": case["case_id"],
                "variant": variant,
                "query": case["query"],
                "gold_product_ids": sorted(gold_ids),
                "retrieved_product_ids_top5": product_ids[:5],
                "status": status,
                "metrics": scores,
            }
        )

    catalog_bytes = source.read_bytes()
    case_bytes = cases_path.read_bytes()
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    ).stdout
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    cluster_ids = _relevance_cluster_ids(gold_by_case)
    result = {
        "kind": "production_snapshot_search_tool_dev_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "commit_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
        "working_tree": {
            "dirty_tracked_files": bool(status.strip()),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        },
        "source_file": _relative(source),
        "case_file": _relative(cases_path),
        "catalog": {
            "raw_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "canonical_content_sha256": canonical_dataset_sha256(catalog_bytes),
            "runtime_accepted_products": len(repository.products),
            "quarantined_products": repository.rows_quarantined,
            "repository_load_ms": round(catalog_load_ms, 3),
        },
        "query_set": {
            "kind": "repo-visible catalog-derived product-resolution variants",
            "split": "development only",
            "query_count": len(cases),
            "case_file_canonical_sha256": canonical_dataset_sha256(case_bytes),
            "label_source": "validated catalog identity; not human relevance judgments",
            "relevance_cluster_count": len(set(cluster_ids)),
        },
        "system": {
            "name": "execute_catalog_tool(search_products)",
            "result_limit": 5,
            "path_included": [
                "tool argument validation",
                "Vietnamese query understanding and deterministic routing",
                "snapshot category, brand, price, and audience filters",
                "SnapshotProductRepository candidate retrieval and ranking",
                "tool observation construction",
            ],
            "external_llm_or_provider": False,
        },
        "status_counts": dict(status_counts),
        "metrics": {
            "overall": _metrics(metric_rows),
            "by_query_variant": {
                variant: {"count": len(rows), "metrics": _metrics(rows)}
                for variant, rows in sorted(metric_rows_by_variant.items())
            },
        },
        "latency_ms": {
            "tool_path_end_to_end_excluding_repository_load": _latency_summary(latencies),
            "repository_load_once": round(catalog_load_ms, 3),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or None,
        },
        "limitations": [
            (
                "Development-only, repository-visible queries; the set is generated from "
                "catalog titles."
            ),
            "Identity labels do not measure semantic fit, advice quality, or human relevance.",
            (
                "No LLM response generation, HTTP overhead, voice, live inventory, or "
                "concurrency is measured."
            ),
            "The snapshot exposes no verified product suitability attributes for many use cases.",
        ],
        "cases": case_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--cases", type=Path, default=DEV_CASES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = run(args.source, args.cases, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "query_count": result["query_set"]["query_count"],
                "metrics": result["metrics"],
                "latency_ms": result["latency_ms"],
                "status_counts": result["status_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
