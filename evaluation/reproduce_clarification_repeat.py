"""Small live API probe for repeated clarification across shopping categories.

It records only HTTP responses and response metadata (never environment values).
The script is intentionally read-only with respect to catalog/cart state: each
journey uses a fresh session and sends text through the production SSE endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
JOURNEYS = {
    "skincare": [
        "tôi dạo này mặt nhiều mụn quá tư vấn cho tôi sản phẩm để cải thiện với",
        "da dầu nha",
        "ưu tiên loại dịu nhẹ, khoảng hai trăm nghìn",
    ],
    "household": [
        "Mình cần đồ dọn bếp cho nhà mới chuyển vào.",
        "bếp nhỏ, ưu tiên gọn và dễ dùng",
        "thêm nước lau sàn mùi nhẹ",
    ],
    "food": [
        "Tối nay ăn lẩu, mình muốn mua thịt để nhúng.",
        "Ưu tiên bò thái lát, khoảng hai trăm nghìn thôi.",
    ],
}


async def stream_turn(client: httpx.AsyncClient, session_id: str, text: str) -> dict:
    started = perf_counter()
    events: list[dict] = []
    event_name: str | None = None
    data: list[str] = []
    async with client.stream(
        "POST",
        "/agent/respond/stream",
        json={
            "session_id": session_id,
            "text": text,
            "language": "vi",
        },
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
            elif not line and event_name:
                events.append({"event": event_name, "data": json.loads("\n".join(data))})
                event_name, data = None, []
    complete = next(
        (e["data"].get("response", {}) for e in reversed(events) if e["event"] == "complete"), {}
    )
    return {
        "query": text,
        "status": response.status_code,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "text": "".join(e["data"].get("text", "") for e in events if e["event"] == "delta"),
        "model": complete.get("model"),
        "state": complete.get("state"),
        "products": complete.get("products", []),
        "tools_used": complete.get("tools_used", []),
        "pending_clarification": complete.get("pending_clarification"),
        "event_names": [e["event"] for e in events],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--journey", action="append", choices=sorted(JOURNEYS))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/reports/artifacts/clarification_repeat_reproduced_20261101.json",
    )
    args = parser.parse_args()
    results = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as client:
        for name in args.journey or JOURNEYS:
            sid = (
                await client.post("/sessions", json={"kiosk_id": "clarification-repeat-probe"})
            ).json()["session_id"]
            turns = []
            for text in JOURNEYS[name]:
                turns.append(await stream_turn(client, sid, text))
            results.append({"name": name, "session_id": sid, "turns": turns})
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ["backend/services/customer_consultation.py", "backend/api/main.py"]
        },
        "journeys": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
