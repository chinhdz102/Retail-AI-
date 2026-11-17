"""Exercise price intent and a subsequent advice turn against the running demo."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx


async def main(prices_only=False):
    cases = [
        "Ờ ờ các loại thịt giá như thế nào?",
        "Thịt heo giá sao?",
        "Cho mình xem giá các loại thịt.",
        "Các loại dầu gội giá như thế nào?",
        "Các loại sữa tắm giá sao?",
        "Giá các loại nước giặt như thế nào?",
        "Các loại rau giá sao?",
        "Các loại sữa giá như thế nào?",
    ]
    rows = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=65) as client:
        session_id = (await client.post("/sessions", json={"kiosk_id": "kiosk-01"})).json()[
            "session_id"
        ]
        try:
            for text in cases:
                started = perf_counter()
                response = await client.post(
                    "/agent/respond",
                    json={
                        "session_id": session_id,
                        "text": text,
                        "language": "vi",
                    },
                )
                payload = response.json()
                rows.append(
                    {
                        "input": text,
                        "status": response.status_code,
                        "elapsed_ms": round((perf_counter() - started) * 1000),
                        "response": payload,
                    }
                )
                print(
                    response.status_code,
                    text,
                    payload.get("text", payload.get("detail")),
                    flush=True,
                )
            if not prices_only:
                text = "Thịt heo để ăn lẩu thì chọn gì?"
                started = perf_counter()
                response = await client.post(
                    "/agent/respond",
                    json={
                        "session_id": session_id,
                        "text": text,
                        "language": "vi",
                    },
                )
                rows.append(
                    {
                        "input": text,
                        "status": response.status_code,
                        "elapsed_ms": round((perf_counter() - started) * 1000),
                        "response": response.json(),
                    }
                )
                print(
                    response.status_code,
                    text,
                    response.json().get("text", response.json().get("detail")),
                    flush=True,
                )
        finally:
            await client.post(f"/sessions/{session_id}/close")
    suffix = "prices_final" if prices_only else "live"
    path = Path(f"docs/reports/artifacts/price_intent_{suffix}_20261115.json")
    path.write_text(
        json.dumps({"complete": True, "cases": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prices-only", action="store_true")
    asyncio.run(main(parser.parse_args().prices_only))
