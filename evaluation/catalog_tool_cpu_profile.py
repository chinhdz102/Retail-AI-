"""Profile the deterministic catalog search path on the local product snapshot."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.metadata
import json
import os
import platform
import pstats
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.retail_tools import execute_catalog_tool

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "winmart_products.json"
DEFAULT_OUTPUT = ROOT / "docs" / "reports" / "artifacts" / "catalog_tool_cpu_profile.json"
QUERIES = (
    "Gợi ý dầu gội",
    "Giới thiệu nước giặt",
    "Gợi ý khăn mặt",
    "Tôi muốn tìm thịt để ăn lẩu",
)


def _git(command: list[str], *, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *command],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    value = result.stdout if binary else result.stdout.decode("utf-8", errors="replace")
    return value.strip() if isinstance(value, str) else value


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile + 0.5)))
    return round(ordered[index], 3)


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _run_one(repository: SnapshotProductRepository, query: str) -> int:
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )
    return len(observation.products)


def run(output: Path, repeats: int) -> dict[str, Any]:
    repository_started = time.perf_counter()
    repository = SnapshotProductRepository(CATALOG)
    repository_load_ms = (time.perf_counter() - repository_started) * 1000

    profiler = cProfile.Profile()
    profiler.enable()
    profiled_counts = [
        _run_one(repository, query)
        for _ in range(repeats)
        for query in QUERIES
    ]
    profiler.disable()

    samples_ms: list[float] = []
    result_counts: list[int] = []
    for _ in range(repeats):
        for query in QUERIES:
            started = time.perf_counter()
            result_counts.append(_run_one(repository, query))
            samples_ms.append((time.perf_counter() - started) * 1000)

    profile_stats = pstats.Stats(profiler)
    profile_rows = []
    for (filename, line, function), (
        primitive_calls,
        total_calls,
        self_seconds,
        cumulative_seconds,
        _callers,
    ) in profile_stats.stats.items():
        absolute_file = Path(filename)
        try:
            source_path = absolute_file.resolve().relative_to(ROOT).as_posix()
        except (OSError, ValueError):
            continue
        profile_rows.append(
            {
                "file": source_path,
                "line": line,
                "function": function,
                "primitive_calls": primitive_calls,
                "total_calls": total_calls,
                "self_seconds": round(self_seconds, 6),
                "cumulative_seconds": round(cumulative_seconds, 6),
            }
        )
    profile_rows.sort(key=lambda row: row["cumulative_seconds"], reverse=True)

    dirty_status = str(_git(["status", "--porcelain", "--untracked-files=no"]))
    tracked_diff = _git(["diff", "--binary", "HEAD"], binary=True)
    query_set_hash = hashlib.sha256(
        json.dumps(QUERIES, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {
        "artifact_id": "retail_ai_deterministic_catalog_tool_cpu_profile",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "commit_sha": str(_git(["rev-parse", "HEAD"])),
        "working_tree": {
            "dirty_tracked_files": bool(dirty_status),
            "tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        },
        "mode": "direct_search_products_tool_local_snapshot_no_provider",
        "catalog": {
            "path": "data/winmart_products.json",
            "sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
            "accepted_product_count": len(repository.products),
            "load_ms": round(repository_load_ms, 3),
        },
        "run_config": {
            "repeats_per_query": repeats,
            "total_profiled_calls": repeats * len(QUERIES),
            "query_set_sha256": query_set_hash,
            "queries": list(QUERIES),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(),
            "dependencies": {
                name: _package_version(name)
                for name in ("pydantic", "fastapi", "httpx")
            },
        },
        "profiled_run": {
            "elapsed_seconds": round(profile_stats.total_tt, 6),
            "total_calls": profile_stats.total_calls,
            "primitive_calls": profile_stats.prim_calls,
            "result_counts": profiled_counts,
            "top_functions_by_cumulative_time": profile_rows[:40],
        },
        "unprofiled_tool_latency_ms": {
            "samples": [round(value, 3) for value in samples_ms],
            "p50": _percentile(samples_ms, 0.50),
            "p95": _percentile(samples_ms, 0.95),
            "p99": _percentile(samples_ms, 0.99),
            "mean": round(statistics.mean(samples_ms), 3) if samples_ms else None,
            "result_counts": result_counts,
        },
        "limitations": [
            (
                "Direct catalog-tool profile excludes HTTP, session state, Groq, STT, TTS and "
                "browser overhead."
            ),
            (
                "The four-query workload is a repeatable representative profile, not a customer "
                "traffic distribution."
            ),
            (
                "Local Windows CPU timing is diagnostic and is not a production SLO or capacity "
                "result."
            ),
            (
                "Profiled wall time includes cProfile instrumentation; use unprofiled latency "
                "for timing comparisons."
            ),
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(args.output, args.repeats), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
