"""Compare the Vietnamese category parser hot path with an earlier Git revision."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from backend.services import vietnamese_intent as current_parser

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluation" / "cases" / "independent_consultation_pilot_v1.json"
DEFAULT_CATALOG = ROOT / "data" / "winmart_products.json"
DEFAULT_BASELINE_REF = "295133bcd32e56e00fbbcf64ef5c7c8f75bef05b"
PARSER_FUNCTIONS = (
    "product_type_attribute_modifier",
    "explicit_recommendation_category",
    "explicit_recommendation_categories",
)


def _load_baseline_parser(ref: str) -> ModuleType:
    source = subprocess.run(
        ["git", "show", f"{ref}:backend/services/vietnamese_intent.py"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="retail-parser-baseline-") as directory:
        module_path = Path(directory) / "baseline_vietnamese_intent.py"
        module_path.write_bytes(source)
        spec = importlib.util.spec_from_file_location(
            "retail_ai_baseline_vietnamese_intent", module_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load baseline parser source")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, list):
        raise ValueError("Case dataset must contain a cases array")
    return [case for case in cases if isinstance(case, dict) and case.get("query")]


def _evaluate_module(
    module: ModuleType,
    queries: list[str],
    repeats: int,
) -> tuple[dict[str, list[Any]], float, float]:
    started = time.perf_counter()
    outputs = {
        name: [getattr(module, name)(query) for query in queries] for name in PARSER_FUNCTIONS
    }
    first_pass_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    for _ in range(repeats):
        for query in queries:
            for name in PARSER_FUNCTIONS:
                getattr(module, name)(query)
    repeated_passes_ms = (time.perf_counter() - started) * 1000
    return outputs, first_pass_ms, repeated_passes_ms


def run(
    cases_path: Path,
    catalog_path: Path,
    baseline_ref: str,
    repeats: int,
) -> dict[str, Any]:
    cases = _load_cases(cases_path)
    queries = [str(case["query"]) for case in cases]
    baseline_parser = _load_baseline_parser(baseline_ref)
    baseline_outputs, baseline_first_ms, baseline_repeated_ms = _evaluate_module(
        baseline_parser, queries, repeats
    )
    current_outputs, current_first_ms, current_repeated_ms = _evaluate_module(
        current_parser, queries, repeats
    )

    differences = []
    for index, case in enumerate(cases):
        for function_name in PARSER_FUNCTIONS:
            before = baseline_outputs[function_name][index]
            after = current_outputs[function_name][index]
            if before != after:
                differences.append(
                    {
                        "case_id": case.get("case_id"),
                        "query": case["query"],
                        "function": function_name,
                        "baseline": before,
                        "current": after,
                    }
                )

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
    cases_bytes = cases_path.read_bytes()
    repeated_speedup = baseline_repeated_ms / current_repeated_ms if current_repeated_ms else None

    return {
        "kind": "vietnamese_parser_hot_path_before_after",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "baseline_ref": baseline_ref,
        "current_commit_sha": subprocess.run(
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
        "dataset": {
            "path": str(cases_path),
            "case_count": len(queries),
            "sha256": hashlib.sha256(cases_bytes).hexdigest(),
        },
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(),
        },
        "configuration": {
            "functions": list(PARSER_FUNCTIONS),
            "repeated_passes": repeats,
            "module_import_time_included": False,
        },
        "metrics": {
            "baseline_first_pass_ms": round(baseline_first_ms, 3),
            "current_first_pass_ms": round(current_first_ms, 3),
            "baseline_repeated_passes_ms": round(baseline_repeated_ms, 3),
            "current_repeated_passes_ms": round(current_repeated_ms, 3),
            "repeated_path_speedup": round(repeated_speedup, 3)
            if repeated_speedup is not None
            else None,
            "output_parity_case_function_pairs": len(queries) * len(PARSER_FUNCTIONS)
            - len(differences),
            "output_differences": differences,
        },
        "limitations": [
            "This is a CPU microbenchmark of three parser functions, not end-to-end API latency.",
            "Module import/startup cost is excluded; use the API load probe for warmed requests.",
            "The authored pilot is development data, not an independently human-reviewed test set.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--baseline-ref", default=DEFAULT_BASELINE_REF)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "docs"
            / "reports"
            / "artifacts"
            / "vietnamese_parser_hot_path_20261029.json"
        ),
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    result = run(args.cases, args.catalog, args.baseline_ref, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {key: value for key, value in result.items() if key != "metrics"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
