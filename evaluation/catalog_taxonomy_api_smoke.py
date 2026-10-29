"""Run deterministic end-to-end catalog-label routing checks through the API.

This is a taxonomy/routing smoke test, not a relevance or suitability benchmark.
It uses the active catalog's category labels and never calls an LLM/provider.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from backend.api import main as api
from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.query_understanding import understand_catalog_query

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "winmart_products.json"
DEFAULT_OUTPUT = ROOT / "docs" / "reports" / "artifacts" / "catalog_taxonomy_api_smoke.json"


class ForbiddenModelCall:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Catalog taxonomy smoke test must not call an LLM")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


async def _run_sweep(catalog_path: Path) -> dict[str, Any]:
    repository = SnapshotProductRepository(catalog_path)
    hierarchies = repository.list_category_hierarchies()
    labels = sorted({path[-1] for path in hierarchies if path})
    taxonomy_labels = {
        normalize_category_text(segment) for path in hierarchies for segment in path
    }
    forbidden_model = ForbiddenModelCall()

    original_repositories = (
        api.get_product_repository,
        retail_tools.get_product_repository,
    )
    api.get_product_repository = lambda *args, **kwargs: repository
    retail_tools.get_product_repository = lambda *args, **kwargs: repository
    previous_override = api.app.dependency_overrides.get(api.get_groq_service)
    api.app.dependency_overrides[api.get_groq_service] = lambda: forbidden_model

    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://retail-ai-eval",
            timeout=30,
        ) as client:
            for index, label in enumerate(labels):
                query = f"Tìm {label}"
                parsed = understand_catalog_query(query, hierarchies)
                before_model_calls = forbidden_model.calls
                started = time.perf_counter()
                error: str | None = None
                payload: dict[str, Any] = {}
                status_code: int | None = None
                try:
                    created = await client.post(
                        "/sessions",
                        json={"kiosk_id": f"taxonomy-smoke-{index}"},
                    )
                    created.raise_for_status()
                    session_id = created.json()["session_id"]
                    response = await client.post(
                        "/agent/respond",
                        json={"session_id": session_id, "text": query},
                    )
                    status_code = response.status_code
                    payload = response.json()
                except Exception as exc:  # record each failed API case and keep the sweep going
                    error = f"{type(exc).__name__}: {str(exc)[:240]}"
                duration_ms = (time.perf_counter() - started) * 1000

                products = payload.get("products", [])
                grounded_ids = set(payload.get("grounded_product_ids", []))
                path_checks: list[bool] = []
                snapshot_checks: list[bool] = []
                product_paths: list[str] = []
                for card in products:
                    product = repository.get_product_snapshot(card.get("product_id", ""))
                    if product is None:
                        path_checks.append(False)
                        snapshot_checks.append(False)
                        continue
                    path = product.category_hierarchy or product.category
                    product_paths.append(path)
                    path_labels = {
                        normalize_category_text(segment) for segment in path.split(">")
                    }
                    path_checks.append(normalize_category_text(label) in path_labels)
                    snapshot_checks.append(
                        card.get("name") == product.name
                        and card.get("price_vnd") == product.price_vnd
                        and product.product_id in grounded_ids
                    )

                checks = {
                    "http_200": status_code == 200,
                    "parsed_catalog_type": bool(parsed.product_type)
                    and normalize_category_text(parsed.product_type) in taxonomy_labels,
                    "lookup_intent_not_price_or_comparison": parsed.intent
                    not in {"price", "comparison"},
                    "products_returned": bool(products),
                    "all_products_inside_requested_taxonomy_path": bool(products)
                    and all(path_checks),
                    "cards_match_snapshot_and_grounded_ids": bool(products)
                    and all(snapshot_checks),
                    "no_llm_call": forbidden_model.calls == before_model_calls,
                }
                results.append(
                    {
                        "category_label": label,
                        "query": query,
                        "parsed_type": parsed.product_type,
                        "intent": parsed.intent,
                        "tools_used": payload.get("tools_used", []),
                        "status_code": status_code,
                        "duration_ms": round(duration_ms, 2),
                        "product_ids": [
                            product.get("product_id") for product in products
                        ],
                        "category_paths": product_paths,
                        "error": error,
                        "checks": checks,
                        "passed": all(checks.values()) and error is None,
                    }
                )
    finally:
        (
            api.get_product_repository,
            retail_tools.get_product_repository,
        ) = original_repositories
        if previous_override is None:
            api.app.dependency_overrides.pop(api.get_groq_service, None)
        else:
            api.app.dependency_overrides[api.get_groq_service] = previous_override

    durations = [row["duration_ms"] for row in results]
    passed = sum(row["passed"] for row in results)
    return {
        "schema_version": "1.0",
        "run_type": "deterministic_local_api_taxonomy_routing_smoke",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "branch": _git("branch", "--show-current"),
        "head_commit": _git("rev-parse", "HEAD"),
        "worktree_status": _git("status", "--short", "--branch"),
        "python_version": platform.python_version(),
        "catalog_path": str(catalog_path.relative_to(ROOT)),
        "catalog_sha256": _sha256(catalog_path),
        "case_count": len(results),
        "passed_cases": passed,
        "pass_rate": round(passed / len(results), 6) if results else 0.0,
        "latency_ms": {
            "p50": round(statistics.median(durations), 2) if durations else None,
            "p95": round(
                sorted(durations)[min(len(durations) - 1, int(len(durations) * 0.95))], 2
            )
            if durations
            else None,
        },
        "llm_calls": forbidden_model.calls,
        "external_provider_calls": 0,
        "human_evaluation": False,
        "independent_or_blind": False,
        "limitations": [
            "Checks taxonomy parsing, API routing, and snapshot grounding only.",
            "Does not evaluate whether a product is semantically suitable for a need.",
            "Does not establish naturalness, human relevance, current stock, "
            "or production latency.",
        ],
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    catalog_path = args.catalog.resolve()
    output_path = args.output.resolve()
    result = asyncio.run(_run_sweep(catalog_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failures = [row for row in result["results"] if not row["passed"]]
    print(
        json.dumps(
            {
                "artifact": str(output_path),
                "case_count": result["case_count"],
                "passed_cases": result["passed_cases"],
                "failed_cases": len(failures),
                "llm_calls": result["llm_calls"],
                "latency_ms": result["latency_ms"],
            },
            ensure_ascii=False,
        )
    )
    for failure in failures[:20]:
        print(
            json.dumps(
                {
                    "category_label": failure["category_label"],
                    "intent": failure["intent"],
                    "error": failure["error"],
                    "checks": failure["checks"],
                    "product_ids": failure["product_ids"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
