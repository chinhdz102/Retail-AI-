"""Exercise price and ranking follow-ups across varied catalog advice requests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.products.repository import get_product_repository
from evaluation.catalog_advice_benchmark import CASES, _check


async def run() -> dict:
    repository = get_product_repository()
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=90) as client:

        async def scenario(case):
            case_id, question, scope, trait = case
            async with semaphore:
                session = (
                    await client.post("/sessions", json={"kiosk_id": "followup-sweep"})
                ).json()["session_id"]

                async def ask(text):
                    response = await client.post(
                        "/agent/respond", json={"session_id": session, "text": text}
                    )
                    response.raise_for_status()
                    return response.json()

                first = await ask(question)
                failures = _check(first, scope, trait, repository)
                cheapest = await ask("loại nào rẻ nhất?")
                displayed = first.get("products", [])
                if displayed:
                    if len(displayed) > 1:
                        expected = min(p["price_vnd"] for p in displayed)
                        if not cheapest.get("products") or any(
                            p["price_vnd"] != expected for p in cheapest["products"]
                        ):
                            failures.append("wrong_cheapest")
                        if any(
                            p["product_id"] not in {x["product_id"] for x in displayed}
                            for p in cheapest["products"]
                        ):
                            failures.append("cheapest_outside_candidates")
                    elif "một lựa chọn" not in cheapest["text"]:
                        failures.append("single_option_not_disclosed")
                best = await ask("loại nào bán chạy nhất?")
                if (
                    best.get("products")
                    or "số liệu bán" not in best["text"]
                    or "nhóm hàng nào" in best["text"]
                ):
                    failures.append("unsupported_or_context_lost_bestseller")
                await client.post(f"/sessions/{session}/close")
                return {
                    "case_id": case_id,
                    "question": question,
                    "first": first,
                    "cheapest": cheapest,
                    "bestseller": best,
                    "failures": failures,
                }

        results = await asyncio.gather(*(scenario(case) for case in CASES))
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "count": len(results),
        "passed": sum(not r["failures"] for r in results),
        "results": results,
    }


if __name__ == "__main__":
    result = asyncio.run(run())
    path = Path("docs/reports/artifacts/catalog_followup_sweep_20261027.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}))
    for row in result["results"]:
        if row["failures"]:
            print(
                json.dumps(
                    {"case_id": row["case_id"], "failures": row["failures"]}, ensure_ascii=False
                )
            )
