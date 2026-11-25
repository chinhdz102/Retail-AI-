"""Reproducible catalog-wide checks; this is not a human relevance evaluation.

Run: uv run python -m scripts.evaluate_consultation_coverage
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

import backend.api.main as api
from backend.config import get_settings
from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.demo_sales import rank_demo_sales, simulated_units
from backend.services.query_understanding import understand_catalog_query
from backend.services.retail_tools import execute_catalog_tool

ROOT = Path(__file__).resolve().parents[1]


def run_catalog_sweep(repository):
    paths = repository.list_category_hierarchies()
    results = []
    for path in paths:
        category = path[-1]
        members = [
            p
            for p in repository.products
            if normalize_category_text(category)
            in {normalize_category_text(label) for label in p.category_hierarchy.split(" > ")}
        ]
        if not members:
            raise AssertionError(path)
        cap = max(p.price_vnd for p in members) + 1
        templates = [
            ("colloquial_browse", f"t muốn mua {category}", "search_products"),
            ("location", f"t đang cần mua {category} ở đâu vậy", "search_products"),
            ("cheapest", f"Trong các loại {category}, loại nào rẻ nhất?", "recommend_products"),
            ("budget", f"Gợi ý {category} dưới {cap}đ", "recommend_products"),
            ("demo_sales", f"{category} nào là best seller", None),
        ]
        member_ids = {p.product_id for p in members}
        for kind, text, tool in templates:
            error = None
            if tool:
                observation = execute_catalog_tool(
                    tool,
                    json.dumps({"query": text, "limit": 3}, ensure_ascii=False),
                    repository=repository,
                    current_user_text=text,
                )
                products = observation.products
                status = observation.status
            else:
                query = understand_catalog_query(text, paths)
                ranked = rank_demo_sales(repository, query, category)
                products = tuple(item.product for item in ranked)
                status = "SIMULATED" if products else "EMPTY"
            ids = [p.product_id for p in products]
            if not products:
                error = "empty_result"
            elif not set(ids) <= member_ids:
                error = "cross_category"
            elif kind == "cheapest" and products[0].price_vnd != min(
                item.price_vnd for item in members
            ):
                error = "not_global_minimum"
            elif kind == "budget" and any(p.price_vnd >= cap for p in products):
                error = "budget_violation"
            elif (
                kind == "demo_sales"
                and products[0].product_id
                != min(
                    members,
                    key=lambda p: (-simulated_units(p.product_id), p.product_id),
                ).product_id
            ):
                error = "not_global_demo_winner"
            results.append(
                {
                    "path": path,
                    "kind": kind,
                    "query": text,
                    "status": status,
                    "ids": ids,
                    "error": error,
                }
            )
    return results


async def run_api_dialogues(repository):
    class NoProvider:
        async def chat(self, *args, **kwargs):
            raise AssertionError("Catalog conversation unexpectedly called a language model")

    settings = get_settings().model_copy(
        update={"app_env": "development", "demo_sales_enabled": True}
    )
    old_overrides = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    api.app.dependency_overrides[api.get_groq_service] = lambda: NoProvider()
    rows = []
    # One dialogue per department/subdepartment, selected from the live taxonomy.
    scopes = {}
    for path in repository.list_category_hierarchies():
        scopes.setdefault(path[:3], path[-1])
    categories = list(scopes.values())
    try:
        with (
            patch.object(api, "get_product_repository", lambda *args: repository),
            patch(
                "backend.services.retail_tools.get_product_repository",
                lambda *args: repository,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api.app),
                base_url="http://test",
                timeout=60,
            ) as client:
                for index, category in enumerate(categories):
                    session = (
                        await client.post("/sessions", json={"kiosk_id": "coverage"})
                    ).json()["session_id"]
                    previous_ids = set()
                    switched_category = categories[(index + 1) % len(categories)]
                    texts = [
                        f"t muốn mua {category}",
                        "loại nào rẻ nhất",
                        "loại đầu tiên ở đâu",
                        f"{switched_category} nào là best seller",
                        "loại này giá bao nhiêu",
                    ]
                    for turn, text in enumerate(texts):
                        error = None
                        try:
                            response = await client.post(
                                "/agent/respond",
                                json={
                                    "session_id": session,
                                    "text": text,
                                },
                            )
                            payload = response.json()
                            if response.status_code != 200:
                                raise AssertionError(f"HTTP {response.status_code}: {payload}")
                            ids = set(payload["grounded_product_ids"])
                            expected_category = switched_category if turn >= 3 else category
                            if not ids:
                                raise AssertionError("empty_result")
                            for product_id in ids:
                                product = repository.get_product_snapshot(product_id)
                                labels = {
                                    normalize_category_text(label)
                                    for label in product.category_hierarchy.split(" > ")
                                }
                                if normalize_category_text(expected_category) not in labels:
                                    raise AssertionError("cross_category")
                            if turn in {1, 2, 4} and not ids <= previous_ids:
                                raise AssertionError("lost_displayed_context")
                            if payload.get("cart") or payload.get("order"):
                                raise AssertionError("unexpected_purchase_mutation")
                            if turn == 3 and payload["model"] != "demo-sales":
                                raise AssertionError("demo_sales_not_used")
                            previous_ids = ids
                        except Exception as exc:
                            payload = locals().get("payload", {})
                            error = f"{type(exc).__name__}: {exc}"
                        rows.append(
                            {
                                "scope": category,
                                "turn": turn,
                                "query": text,
                                "payload": payload,
                                "error": error,
                            }
                        )
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(old_overrides)
    return rows


def main():
    repository = SnapshotProductRepository(ROOT / "data/thanh_lam_products.json")
    catalog = run_catalog_sweep(repository)
    dialogues = asyncio.run(run_api_dialogues(repository))
    failures = [row for row in catalog + dialogues if row["error"]]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "products": len(repository.products),
        "hierarchy_paths": len(repository.list_category_hierarchies()),
        "catalog_cases": len(catalog),
        "api_turns": len(dialogues),
        "failure_count": len(failures),
        "limits": (
            "Finite generated templates and context invariants; no claim of exhaustive "
            "natural-language or human relevance coverage."
        ),
        "catalog": catalog,
        "dialogues": dialogues,
    }
    output = ROOT / "docs/reports/artifacts/consultation_coverage_20261031.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"catalog", "dialogues"}}
        )
    )
    for row in failures[:20]:
        print(
            json.dumps(
                {key: value for key, value in row.items() if key != "payload"}, ensure_ascii=True
            )
        )
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
