"""Read-only live consultation development probes with stage costs and transcripts.

These authored examples are development data, not blind customer/human ratings.
No expected SKU, category or canned answer is passed to the serving pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.customer_consultation import safe_consult_customer
from backend.services.groq import GroqService
from backend.services.sessions import InMemorySessionStore

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "backend/services/customer_consultation.py", "backend/services/groq.py",
    "backend/config.py", "data/shopping_guidance.json",
    "backend/services/shopping_guidance.py", "backend/services/shopping_language.py",
    "backend/services/query_understanding.py", "backend/products/snapshot_repository.py",
    "backend/products/catalog.py", "backend/services/retail_tools.py",
    "evaluation/evaluate_employee_consultation.py",
)

JOURNEYS = {
    "breakfast": [
        (
            "Mình cần đồ ăn sáng mang đi làm, có tủ lạnh ở công ty, "
            "chọn giúp mình vài món đỡ lích kích."
        ),
        "Không có lò vi sóng hay nước nóng đâu, chọn một món mở ra ăn luôn thôi.",
    ],
    "pillow": [
        "Mình muốn mua gối ngủ cho phòng trọ, dưới hai trăm nghìn, chọn giúp mình nhé.",
        "Không cần xịn nhất, chọn cái đáng tiền trong mấy cái đó đi.",
    ],
    "cleaning": [
        "Cuối tuần mình dọn bếp với lau nền, có 150 nghìn, cần mua gì cho gọn?",
        "Nước rửa chén còn, bỏ món đó ra, giữ nguyên hạn mức nhé.",
    ],
    "laundry": [
        "Máy giặt nhà mình cửa trước, muốn mua nước giặt dưới 150 nghìn, chọn sao?",
        "Mình không biết loại nào phù hợp máy đâu, tư vấn giúp mình với.",
    ],
    "sensitive-skin": [
        "Da mình dễ kích ứng, đang muốn đổi sữa tắm, tư vấn cách chọn với.",
        "Đừng chọn chỉ vì trên tên có chữ dịu nhẹ nhé, nên xem gì?",
    ],
    "acne": [
        "Có sản phẩm nào điều trị mụn không?",
        "Tư vấn cho mình với, mình chưa biết nên chọn thế nào.",
    ],
    "hotpot": [
        "Mình muốn mua thịt nhúng lẩu, bò bình thường hoặc ba chỉ cũng được, tầm 200 nghìn.",
        "Không cần thái sẵn, nhà mình tự thái được, chọn giúp mình nhé.",
    ],
    "battery": [
        "Điều khiển hết pin rồi, mình không biết mua loại nào, tư vấn với.",
        "Mình vừa xem, dùng hai viên AAA, chọn một vỉ vừa tiền thôi.",
    ],
    "cookware": [
        "Mình cần mua một cái chảo, dùng với bếp từ, tư vấn giúp mình.",
        "Loại inox thì chắc chắn dùng bếp từ được hết đúng không?",
    ],
    "milk": [
        "Chọn giúp mình hai loại sữa tươi không đường, mỗi hộp dưới 50 nghìn.",
        "Nếu chọn một trong hai thì bạn nghiêng về cái nào, vì sao?",
    ],
    "tea-gift": [
        "Bố thích uống trà, mua quà khoảng 250 nghìn nên chọn gì cho thiết thực?",
        "Không cần mua hết tiền, chọn một món thôi nhé.",
    ],
    "pet-cleaning": [
        "Nhà có mèo, mình mua nước lau sàn thì nên chọn và dùng thế nào?",
        "Mình cần loại không mùi nồng, đừng mặc định là an toàn chỉ vì tên có chữ tự nhiên.",
    ],
    "lunch-box": [
        "Mình cần hộp mang cơm, bỏ vào túi đi làm, chọn loại nào cho gọn?",
        "Có hâm lò vi sóng được không, hay phải xem ký hiệu gì?",
    ],
    "toothbrush": [
        "Mình muốn đổi bàn chải đánh răng, mua loại bình dân thôi, chọn giúp mình.",
        "Đầu nhỏ một chút, loại nào đáp ứng thì lấy, không cần bộ nhiều món.",
    ],
    "weekly-food": [
        "Có 500 nghìn mua đồ cho một tuần, nhà hai người, chỉ cần các bữa tối thôi.",
        "Nhà có gạo, dầu ăn với gia vị rồi, mua phần còn lại nhé.",
    ],
    "identity": [
        "Cái hộp màu xanh lần trước mình mua còn loại nào tương tự không?",
        "Là sữa tươi, mình chưa nhớ hãng, có loại không đường nào để tham khảo?",
    ],
}


def hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


async def main():
    # Windows redirected consoles otherwise use a legacy code page and abort
    # after the first Vietnamese reply, leaving an incomplete evaluation.
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--journey", action="append", choices=JOURNEYS)
    parser.add_argument("--reasoning", choices=("none", "low"))
    parser.add_argument("--delay-seconds", type=float, default=35)
    parser.add_argument("--max-turns", type=int, default=32)
    parser.add_argument("--max-unavailable", type=int, default=2)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9_]+", args.label):
        parser.error("label must contain lowercase letters, digits and underscores")
    if not 0 <= args.delay_seconds <= 60 or args.max_turns < 1 or args.max_unavailable < 1:
        parser.error("delay must be 0..60 seconds; limits must be positive")
    options = {"catalog_repository": "snapshot"}
    if args.reasoning:
        options["groq_structured_reasoning_effort"] = args.reasoning
    settings = Settings(**options)
    repository = get_product_repository(settings)
    service = GroqService(settings)
    original = service.chat
    stage_calls: list[dict] = []

    async def traced_chat(messages, **kwargs):
        schema = (kwargs.get("response_format") or {}).get("json_schema", {})
        row = {"stage": schema.get("name", "chat"),
               "context_characters": sum(len(message.get("content", "")) for message in messages),
               "reserved_output_tokens": kwargs.get("max_tokens")}
        stage_calls.append(row)
        started = perf_counter()
        try:
            result = await original(messages, **kwargs)
        except Exception as exc:
            body = getattr(exc.__cause__, "body", None)
            error_text = str(body).casefold()
            row.update(error_type=type(exc).__name__,
                       fallback_trace=getattr(exc, "fallback_trace", []),
                       provider_error_flags={
                           phrase: phrase in error_text for phrase in (
                               "tokens per minute", "tokens per day", "requests per day",
                               "json_validate_failed", "maximum", "reasoning", "max_tokens",
                           )
                       })
            raise
        else:
            row.update(model=result.model, usage=result.usage, fallback_trace=result.fallback_trace)
            try:
                row["decision"] = json.loads(str(result))
            except ValueError:
                row["invalid_json"] = True
            return result
        finally:
            row["elapsed_seconds"] = round(perf_counter() - started, 3)

    service.chat = traced_chat
    selected = args.journey or list(JOURNEYS)
    before = hashes()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": "Authored development probes; no SKU oracle; not independent human ratings.",
        "source_sha256_at_start": before,
        "catalog_sha256": getattr(repository, "dataset_sha256", None),
        "reasoning_effort": settings.groq_structured_reasoning_effort,
        "configured_models": [settings.groq_model, settings.groq_fallback_model,
                              settings.groq_fallback_model_2],
        "requested_journeys": selected, "max_turns": args.max_turns,
        "rows": [], "completed": False,
    }
    output = ROOT / f"docs/reports/artifacts/employee_consultation_{args.label}.json"

    def save():
        report["source_sha256_at_end"] = hashes()
        report["source_stable"] = before == report["source_sha256_at_end"]
        report["summary"] = {
            "turns": len(report["rows"]),
            "routes": dict(Counter(row["route"] for row in report["rows"])),
            "stage_calls": dict(Counter(call["stage"] for call in stage_calls)),
            "successful_usage_tokens": sum(
                call.get("usage", {}).get("total_tokens", 0) for call in stage_calls
            ),
            "note": "Usage excludes failed provider attempts; routes are not quality ratings.",
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    unavailable = 0
    try:
        for name in selected:
            record = InMemorySessionStore().create("employee-development-eval")
            for turn, text in enumerate(JOURNEYS[name], 1):
                if len(report["rows"]) >= args.max_turns:
                    report["stopped_reason"] = "max_turns"
                    return
                call_start = len(stage_calls)
                started = perf_counter()
                result = await safe_consult_customer(
                    text=text, record=record, repository=repository, groq=service,
                    turn_id=f"{name}-{turn}", review_selection=True,
                )
                row = {"journey": name, "turn": turn, "query": text,
                       "elapsed_seconds": round(perf_counter() - started, 3),
                       "calls": stage_calls[call_start:], "manual_review": None}
                if result is None:
                    row.update(route="delegated", text=None, products=[])
                else:
                    row.update(route=result.model, text=result.text,
                               products=[{"id": p.product_id, "name": p.name,
                                          "price_vnd": p.price_vnd, "uom": p.uom}
                                         for p in result.products], memory=result.memory)
                    record.shopping.consultation = result.memory
                    record.shopping.selected_product_ids = [p.product_id for p in result.products]
                    record.messages.extend([{"role": "user", "content": text},
                                            {"role": "assistant", "content": result.text}])
                report["rows"].append(row)
                unavailable = unavailable + 1 if row["route"] == "consultation-fallback" else 0
                save()
                print(json.dumps({key: row[key] for key in (
                    "journey", "turn", "elapsed_seconds", "route", "text",
                )}, ensure_ascii=False), flush=True)
                if unavailable >= args.max_unavailable:
                    report["stopped_reason"] = "consecutive_unavailable"
                    return
                if args.delay_seconds:
                    await asyncio.sleep(args.delay_seconds)
        report["completed"] = True
    finally:
        save()
        for client in service._clients_by_credential.values():
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
