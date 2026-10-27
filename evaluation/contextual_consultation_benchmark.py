"""Exercise the real kiosk SSE endpoint with multi-turn retail conversations."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.products.repository import get_product_repository
from backend.services.vietnamese_intent import normalize_vietnamese

FLOWS = {
    "shampoo_context": [
        "Gợi ý dầu gội",
        "loại nào rẻ nhất",
        "loại nào là best seller",
        "loại nào ngon nhất",
    ],
    "snack_context": [
        "Gợi ý bim bim",
        "cái mô rẻ nhứt",
        "cái nào bán chạy nhất",
        "loại nào ngon nhất",
    ],
    "laundry_context": ["Gợi ý nước giặt", "giá nào thấp nhất", "loại nào là best seller"],
    "preparation_context": [
        "thế t mua thịt nhúng lẩu m tư vấn cho t thịt nào ngon",
        "mình thích thịt bò",
        "giới thiệu khăn mặt cho mình",
    ],
    "checkout_empty": ["cho t thanh toán"],
    "payment_policy": ["Cửa hàng có nhận thanh toán bằng chuyển khoản không?"],
    "price_scope_change": ["Gợi ý dầu gội", "Khăn mặt nào rẻ nhất?"],
    "missing_ranking_context": ["Cái nào bán chạy nhất?"],
}


def parse_stream(raw: str, session_id: str) -> tuple[dict, int]:
    metadata = None
    text_parts = []
    started = completed = False
    for frame in raw.replace("\r\n", "\n").split("\n\n"):
        event = ""
        data = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if not data:
            continue
        value = json.loads("\n".join(data))
        if event in {"start", "complete"}:
            metadata = value["response"]
            if metadata["session_id"] != session_id:
                raise ValueError("response belongs to a different session")
            started |= event == "start"
            completed |= event == "complete"
        elif event == "delta":
            if not started:
                raise ValueError("delta before validated start")
            text_parts.append(value["text"])
    if not started or not completed or metadata is None:
        raise ValueError("incomplete response stream")
    return {**metadata, "text": "".join(text_parts)}, len(text_parts)


async def main(args) -> None:
    repository = get_product_repository()
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=90) as client:

        async def run_flow(case_id, utterances):
            async with semaphore:
                created = await client.post("/sessions", json={"kiosk_id": "context-eval"})
                created.raise_for_status()
                session_id = created.json()["session_id"]
                turns = []
                for utterance in utterances:
                    response = await client.post(
                        "/agent/respond/stream",
                        json={
                            "session_id": session_id,
                            "text": utterance,
                            "language": "vi",
                        },
                    )
                    response.raise_for_status()
                    body, delta_count = parse_stream(response.text, session_id)
                    errors = []
                    if not delta_count:
                        errors.append("missing_stream_text")
                    for item in body.get("products", []):
                        product = repository.get_product_snapshot(item["product_id"])
                        if (
                            not product
                            or product.name != item["name"]
                            or product.price_vnd != item["price_vnd"]
                        ):
                            errors.append("catalog_mismatch")
                    turns.append(
                        {
                            "question": utterance,
                            "response": body,
                            "delta_count": delta_count,
                            "errors": errors,
                        }
                    )

                if case_id in {"shampoo_context", "snack_context", "laundry_context"}:
                    first, cheapest, bestseller = [turn["response"] for turn in turns[:3]]
                    if not first["products"]:
                        turns[0]["errors"].append("missing_initial_candidates")
                    elif not cheapest["products"] or any(
                        product["price_vnd"] != min(p["price_vnd"] for p in first["products"])
                        or product["product_id"] not in first["grounded_product_ids"]
                        for product in cheapest["products"]
                    ):
                        turns[1]["errors"].append("wrong_cheapest_or_scope")
                    if (
                        "bán chạy nhất" not in bestseller["text"]
                        or "nhóm hàng nào" in bestseller["text"]
                    ):
                        turns[2]["errors"].append("lost_ranking_context")
                if case_id == "preparation_context":
                    for turn in turns[:2]:
                        for item in turn["response"]["products"]:
                            if "lau" not in normalize_vietnamese(item["name"]).split():
                                turn["errors"].append("unverified_preparation_match")
                    if not turns[2]["response"]["products"] or any(
                        "khăn mặt" not in item["name"].casefold()
                        for item in turns[2]["response"]["products"]
                    ):
                        turns[2]["errors"].append("failed_category_switch")
                if case_id == "checkout_empty":
                    response = turns[0]["response"]
                    if (
                        "giỏ hàng đang trống" not in response["text"].casefold()
                        or response["order"]
                    ):
                        turns[0]["errors"].append("checkout_not_safe_or_actionable")
                if case_id == "price_scope_change":
                    if turns[1]["response"]["products"]:
                        turns[1]["errors"].append("reused_unrelated_candidates")
                if case_id == "missing_ranking_context":
                    if "nhóm hàng nào" not in turns[0]["response"]["text"]:
                        turns[0]["errors"].append("missing_context_question")
                await client.post(f"/sessions/{session_id}/close")
                return {"case_id": case_id, "turns": turns}

        results = await asyncio.gather(*(run_flow(name, turns) for name, turns in FLOWS.items()))
    turns = [turn for flow in results for turn in flow["turns"]]
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "catalog_source": repository.source_name,
        "flow_count": len(results),
        "turn_count": len(turns),
        "passed_turns": sum(not turn["errors"] for turn in turns),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "results"}))
    for flow in results:
        for turn in flow["turns"]:
            if turn["errors"]:
                print(
                    json.dumps(
                        {
                            "case": flow["case_id"],
                            "question": turn["question"],
                            "errors": turn["errors"],
                        },
                        ensure_ascii=True,
                    )
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(main(parser.parse_args()))
