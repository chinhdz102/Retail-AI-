"""Real HTTP shopping audit; surface checks are not a human-quality verdict."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from evaluation.audit_consultation_30 import surface_flags

QUESTIONS = [
    ("greeting", "hello"),
    ("breakfast", "thế có đồ gì ăn sáng ko"),
    ("night", "có gì ăn đêm không"),
    ("feminine_wash", "t đang cần tìm dung dịch vệ sinh phụ nữ thì có sản phẩm nào ko"),
    ("drinks", "Trời nóng, có đồ uống nào không quá ngọt không?"),
    ("cleaning", "Cuối tuần dọn phòng, có những đồ dùng nào tiện?"),
    ("hair", "Đầu mình nhiều gàu, có dầu gội nào dưới 150 nghìn?"),
    ("laundry", "Mình muốn xem vài loại nước giặt để chọn."),
    ("food", "Mình muốn mua đồ ăn mang đi picnic, giới thiệu vài loại nhé."),
    ("hotpot", "Mình mua thịt ăn lẩu, bò thường hoặc ba chỉ đều được, có những loại nào?"),
    ("skin", "Mặt mình da dầu, đang nhiều mụn, có sản phẩm nào để làm sạch?"),
    ("gift", "Chọn quà cho mẹ tầm 300 nghìn, mẹ thích trà không đường."),
]

FLOW = [
    "hello Sky",
    "Mình muốn mua đồ ăn sáng nhanh gọn, có những gì?",
    "Không lấy bánh ngọt nhé, mình thích món mặn hơn.",
    "Chọn giúp một món thôi, dưới 50 nghìn.",
    "Nếu ăn đêm thì có món nào khác không?",
    "Giờ mình muốn tìm dung dịch vệ sinh phụ nữ, có loại nào?",
    "Chọn một loại cỡ nhỏ để dùng thử thôi.",
    "Loại đó giá bao nhiêu?",
    "Chuyển sang dầu gội chống gàu dưới 150 nghìn cho nam.",
    "Thế mua cho nữ thì dùng loại nào?",
    "Có sản phẩm nào không, giới thiệu cho mình.",
    "Chốt một loại thôi, đừng giới thiệu thêm.",
]


async def run(args):
    catalog = Path("data/winmart_products.json")
    report = {"started_utc": datetime.now(UTC).isoformat(), "mode": "real-http-text",
        "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "questions": [], "flow": [], "complete": False}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=100) as client:
        async def session():
            response = await client.post("/sessions", json={"kiosk_id": "browse-latency-audit"})
            response.raise_for_status()
            return response.json()["session_id"]

        async def ask(sid, key, text):
            started = time.perf_counter()
            response = await client.post("/agent/respond", json={"session_id": sid,
                "text": text, "language": "vi", "client_turn_id": str(uuid4())})
            result = {"id": key, "question": text, "status": response.status_code,
                "seconds": round(time.perf_counter() - started, 3)}
            if response.status_code == 200:
                payload = response.json()
                result.update({field: payload.get(field) for field in (
                    "text", "model", "grounded_product_ids", "tools_used")})
                result["flags"] = surface_flags("recommend", payload.get("text"), 200)
            else:
                detail = response.json().get("detail", {})
                result["error_code"] = (detail.get("code") if isinstance(detail, dict)
                                        else "http_error")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

        for key, question in QUESTIONS[:4] if args.smoke else QUESTIONS:
            sid = await session()
            try:
                report["questions"].append(await ask(sid, key, question))
                save()
                await asyncio.sleep(args.pause_seconds)
            finally:
                await client.post(f"/sessions/{sid}/close")
        if not args.smoke:
            sid = await session()
            try:
                for turn, question in enumerate(FLOW, 1):
                    report["flow"].append(await ask(sid, f"turn_{turn}", question))
                    save()
                    await asyncio.sleep(args.pause_seconds)
            finally:
                await client.post(f"/sessions/{sid}/close")
    report["complete"] = True
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--pause-seconds", type=float, default=3)
    asyncio.run(run(parser.parse_args()))
