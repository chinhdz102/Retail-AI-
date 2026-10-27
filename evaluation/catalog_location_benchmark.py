"""Check natural shelf questions against the versioned catalog and planogram."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.products.repository import get_product_repository

PRODUCT_IDS = (
    "WIN_10053675",
    "WIN_10170963",
    "WIN_10167097",
    "WIN_10194712",
    "WIN_10161777",
    "WIN_10009734",
    "WIN_10009553",
    "WIN_10012928",
    "WIN_10017633",
    "WIN_10140199",
    "WIN_10322820",
    "WIN_10618196",
)
TEMPLATES = (
    "{name} ở đâu?",
    "Cho mình hỏi {name} nằm ở kệ nào vậy?",
    "Chỉ giúp mình vị trí của {name} nhé",
    "Cho mình biết vị trí {name} với",
    "Mình tìm {name}, kệ nào có vậy?",
    "Cho mình hỏi {name} nằm ở đâu?",
)


async def run(base_url: str, concurrency: int = 1) -> dict:
    repo = get_product_repository()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(base_url=base_url, timeout=90) as client:

        async def case(product_id: str, template: str) -> dict:
            product = repo.get_product_snapshot(product_id)
            question = template.format(name=product.name)
            async with semaphore:
                session = (await client.post("/sessions", json={"kiosk_id": "shelf-eval"})).json()[
                    "session_id"
                ]
                response = await client.post(
                    "/agent/respond", json={"session_id": session, "text": question}
                )
                response.raise_for_status()
                payload = response.json()
                failures = []
                if payload["grounded_product_ids"] != [product_id]:
                    failures.append("wrong_or_ambiguous_product")
                if product.shelf:
                    if product.shelf not in payload["text"]:
                        failures.append("missing_shelf")
                    if (
                        product.shelf_source == "RECOMMENDED_PLANOGRAM"
                        and "đề xuất" not in payload["text"].casefold()
                    ):
                        failures.append("missing_planogram_provenance")
                elif "chưa" not in payload["text"].casefold():
                    failures.append("unverified_location_claim")
                await client.post(f"/sessions/{session}/close")
                return {
                    "product_id": product_id,
                    "question": question,
                    "expected_shelf": product.shelf,
                    "response": payload,
                    "failures": failures,
                }

        results = await asyncio.gather(
            *(case(pid, template) for pid in PRODUCT_IDS for template in TEMPLATES)
        )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "count": len(results),
        "passed": sum(not r["failures"] for r in results),
        "results": results,
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.base_url, args.concurrency))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}))
    for row in result["results"]:
        if row["failures"]:
            print(
                json.dumps(
                    {
                        "question": row["question"],
                        "answer": row["response"]["text"],
                        "failures": row["failures"],
                    },
                    ensure_ascii=False,
                )
            )
