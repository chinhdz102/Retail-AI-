"""Evaluate curated, independently-authored Vietnamese consultation prompts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.query_understanding import understand_catalog_query
from backend.services.retail_tools import execute_catalog_tool
from backend.services.vietnamese_intent import normalize_vietnamese

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluation" / "cases" / "independent_consultation_pilot_v1.json"
DEFAULT_OUTPUT = (
    ROOT / "docs" / "reports" / "artifacts" / "independent_consultation_pilot_20261028.json"
)
DEFAULT_CATALOG = ROOT / "data" / "winmart_products.json"
_SET_FIELDS = {"hard_constraints", "soft_preferences", "negative_constraints", "ambiguity"}


def _in_category(
    product: Any,
    expected_category: str,
    repository: SnapshotProductRepository,
) -> bool:
    product_id = getattr(product, "product_id", None)
    if product_id:
        product = repository.get_product_snapshot(product_id) or product
    expected = normalize_category_text(expected_category)
    segments = [
        normalize_category_text(segment)
        for segment in (product.category_hierarchy or "").split(">")
    ]
    if not segments:
        segments = [normalize_category_text(product.category)]
    return expected in segments


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _reproducibility_metadata(cases_path: Path, catalog_path: Path) -> dict[str, Any]:
    status = _git_output("status", "--porcelain")
    return {
        "command": [sys.executable, *sys.argv],
        "commit_sha": _git_output("rev-parse", "HEAD"),
        "worktree_dirty": bool(status),
        "worktree_status_entry_count": len(status.splitlines()) if status else 0,
        "dataset_sha256": _sha256(cases_path),
        "catalog_sha256": _sha256(catalog_path),
        "pyproject_sha256": _sha256(ROOT / "pyproject.toml"),
        "model": "none; deterministic query parser and snapshot search tool",
        "seed": None,
        "seed_note": "No randomized sampling; cases are evaluated in source order.",
        "configuration": {
            "tool_name": "search_products",
            "candidate_limit": 5,
            "parser": "understand_catalog_query",
            "catalog_source": str(catalog_path.resolve()),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(),
            "dependencies": {
                name: _package_version(name) for name in ("pydantic", "fastapi", "httpx")
            },
        },
    }


def _canonical(value: Any, field: str) -> Any:
    if field in _SET_FIELDS and value is not None:
        return sorted(normalize_vietnamese(str(item)) for item in value)
    if isinstance(value, str):
        return normalize_vietnamese(value)
    return value


def run(cases_path: Path, catalog_path: Path) -> dict[str, Any]:
    source = json.loads(cases_path.read_text(encoding="utf-8"))
    repository = SnapshotProductRepository(catalog_path)
    hierarchies = repository.list_category_hierarchies()
    results: list[dict[str, Any]] = []
    field_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    dimension_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    baseline_latencies: list[float] = []
    tool_latencies: list[float] = []
    baseline_relevant_at_1 = baseline_relevant_at_5 = 0
    tool_relevant_at_1 = tool_relevant_at_5 = 0
    category_precision_at_5 = 0.0
    category_retrieval_cases = 0

    for case in source["cases"]:
        parsed = understand_catalog_query(case["query"], hierarchies)
        expected = case["expected"]
        actual = parsed.model_dump(mode="json")
        field_checks: dict[str, bool] = {}
        for field, expected_value in expected.items():
            matched = _canonical(actual.get(field), field) == _canonical(expected_value, field)
            field_checks[field] = matched
            field_totals[field][0] += int(matched)
            field_totals[field][1] += 1
        case_passed = all(field_checks.values())
        dimension = case["dimension"]
        dimension_totals[dimension][0] += int(case_passed)
        dimension_totals[dimension][1] += 1
        row = {
            "case_id": case["case_id"],
            "dimension": dimension,
            "query": case["query"],
            "expected": expected,
            "actual": {field: actual.get(field) for field in expected},
            "field_checks": field_checks,
            "passed": case_passed,
        }

        expected_type = expected.get("product_type")
        if expected_type:
            started = time.perf_counter_ns()
            baseline = repository.search(case["query"], limit=5)
            baseline_ms = (time.perf_counter_ns() - started) / 1_000_000
            baseline_latencies.append(baseline_ms)

            started = time.perf_counter_ns()
            observation = execute_catalog_tool(
                "search_products",
                json.dumps({"query": case["query"], "limit": 5}, ensure_ascii=False),
                repository=repository,
            )
            tool_ms = (time.perf_counter_ns() - started) / 1_000_000
            tool_latencies.append(tool_ms)
            baseline_products = list(baseline[:5])
            tool_products = list(observation.products[:5])
            baseline_hits = [
                _in_category(product, expected_type, repository) for product in baseline_products
            ]
            tool_hits = [
                _in_category(product, expected_type, repository) for product in tool_products
            ]
            baseline_relevant_at_1 += int(bool(baseline_hits and baseline_hits[0]))
            baseline_relevant_at_5 += int(any(baseline_hits))
            tool_relevant_at_1 += int(bool(tool_hits and tool_hits[0]))
            tool_relevant_at_5 += int(any(tool_hits))
            category_precision_at_5 += sum(tool_hits) / max(1, len(tool_hits))
            category_retrieval_cases += 1
            row.update(
                {
                    "expected_category": expected_type,
                    "baseline_top5": [product.product_id for product in baseline_products],
                    "tool_status": observation.status,
                    "tool_top5": [product.product_id for product in tool_products],
                    "baseline_relevant_at_1": bool(baseline_hits and baseline_hits[0]),
                    "baseline_relevant_at_5": any(baseline_hits),
                    "tool_relevant_at_1": bool(tool_hits and tool_hits[0]),
                    "tool_relevant_at_5": any(tool_hits),
                    "tool_category_precision_at_5": round(
                        sum(tool_hits) / max(1, len(tool_hits)), 4
                    ),
                    "baseline_latency_ms": round(baseline_ms, 4),
                    "tool_latency_ms": round(tool_ms, 4),
                }
            )
        results.append(row)

    total_passed = sum(int(row["passed"]) for row in results)
    return {
        "kind": "independent_vietnamese_consultation_parse_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset_id": source["dataset_id"],
        "authoring_method": source["authoring_method"],
        "evaluation_role": source.get("evaluation_role", "pilot; interpret with limitations"),
        "catalog_file": str(catalog_path),
        "reproducibility": _reproducibility_metadata(cases_path, catalog_path),
        "case_count": len(results),
        "case_exact_match_rate": round(total_passed / max(1, len(results)), 4),
        "field_accuracy": {
            field: {
                "passed": counts[0],
                "total": counts[1],
                "rate": round(counts[0] / counts[1], 4),
            }
            for field, counts in sorted(field_totals.items())
        },
        "dimension_exact_match_rate": {
            dimension: {
                "passed": counts[0],
                "total": counts[1],
                "rate": round(counts[0] / counts[1], 4),
            }
            for dimension, counts in sorted(dimension_totals.items())
        },
        "catalog_retrieval": {
            "query_count": category_retrieval_cases,
            "relevant_at_1": {
                "baseline": round(baseline_relevant_at_1 / max(1, category_retrieval_cases), 4),
                "tool": round(tool_relevant_at_1 / max(1, category_retrieval_cases), 4),
            },
            "relevant_at_5": {
                "baseline": round(baseline_relevant_at_5 / max(1, category_retrieval_cases), 4),
                "tool": round(tool_relevant_at_5 / max(1, category_retrieval_cases), 4),
            },
            "tool_category_precision_at_5": round(
                category_precision_at_5 / max(1, category_retrieval_cases), 4
            ),
            "latency_ms": {
                "baseline_p50": round(statistics.median(baseline_latencies), 4)
                if baseline_latencies
                else None,
                "baseline_p95": _percentile(baseline_latencies, 0.95),
                "tool_p50": round(statistics.median(tool_latencies), 4) if tool_latencies else None,
                "tool_p95": _percentile(tool_latencies, 0.95),
            },
        },
        "failures": [row for row in results if not row["passed"]],
        "results": results,
        "limitations": [
            "This is an authored pilot, not a study with independent external annotators.",
            "It evaluates structured query parsing only, not response naturalness or "
            "end-to-end tool correctness.",
            "Some product attributes are absent from the source snapshot and cannot be "
            "judged for suitability.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.cases, args.catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"results", "failures"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"failures={len(result['failures'])}; report={args.output}")


if __name__ == "__main__":
    main()
