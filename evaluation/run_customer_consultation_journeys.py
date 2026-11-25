"""Real multi-turn customer journeys and new phrasings; no target SKU oracle."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "evaluation/cases/customer_consultation_35_dev_20261031.json").read_text(
    encoding="utf-8",
))["queries"]

JOURNEYS = [
    ("milk-comparison", [
        "Mình cần sữa tươi không đường, gợi ý hai loại để so sánh giúp mình.",
        CASES[13], CASES[15], CASES[16], CASES[17], CASES[18],
    ]),
    ("shampoo-value", [
        CASES[8], CASES[10], CASES[11], CASES[12], CASES[32], CASES[33],
    ]),
    ("volume-reference", [
        "Mình muốn mua sữa tươi không đường.", CASES[29], CASES[30],
    ]),
    ("small-space-low-scent", [
        "Mình cần nước lau sàn cho phòng trọ.", CASES[23], CASES[24],
    ]),
    ("household-discovery", [CASES[0], "Ưu tiên đồ dọn bếp, mình mới chuyển vào ở."]),
    ("weekly-budget", [CASES[9], "Hai người, chỉ cần bữa tối, nhà có gạo và gia vị rồi."]),
    ("child-snacks", [CASES[3], "Bé 8 tuổi, không ăn cay, mình muốn loại ghi ít đường."]),
    ("pet-cleaning", [CASES[21], "Mình đang tìm nước lau sàn, mèo hay liếm chân."]),
    ("hotpot-meat", [
        "Tối nay ăn lẩu, mình muốn mua thịt để nhúng.",
        "Ưu tiên bò thái lát, khoảng hai trăm nghìn thôi.",
    ]),
    ("gift-feedback", [CASES[4], "Mẹ thích bánh nhưng không thích ngọt, tránh mùi quá nồng."]),
    ("unseen-stationery", ["Mình muốn mua sổ để ghi việc hằng ngày, mang trong túi cho tiện."]),
    ("unseen-battery", ["Remote điều hòa hết pin, lấy loại nào rẻ mà dùng được nhỉ?"]),
    ("unseen-towel", ["Tìm khăn lau kính đỡ để lại xơ, tầm giá vừa phải thôi."]),
    ("unseen-pet-grooming", ["Có đồ tắm cho chó con mà không gây kích ứng không?"]),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="")
    parser.add_argument("--journey", action="append", choices=[name for name, _ in JOURNEYS])
    parser.add_argument("--delay-seconds", type=float, default=8)
    parser.add_argument("--max-turns", type=int, default=None)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9_]*", args.label) or args.delay_seconds < 0:
        parser.error("Use a lowercase label and non-negative delay")
    if args.max_turns is not None and args.max_turns < 1:
        parser.error("max-turns must be positive")
    suffix = f"_{args.label}" if args.label else ""
    output = ROOT / f"docs/reports/artifacts/customer_consultation_journeys{suffix}_20261101.json"
    results = []
    meta = {
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": "Authored development journeys, no preselected target SKU; not blind data.",
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in ["backend/services/customer_consultation.py",
                                       "backend/api/main.py", "backend/config.py",
                                       "backend/services/product_comparison.py"]},
        "selected_journeys": args.journey or [name for name, _ in JOURNEYS],
        "max_turns_per_journey": args.max_turns,
    }
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=90) as client:
        for name, turns in JOURNEYS:
            if args.journey and name not in args.journey:
                continue
            sid = (await client.post("/sessions", json={"kiosk_id": "journey-review"})).json()[
                "session_id"
            ]
            journey = {"name": name, "turns": []}
            results.append(journey)
            for text in turns:
                if args.max_turns is not None and len(journey["turns"]) >= args.max_turns:
                    break
                start = perf_counter()
                response = await client.post("/agent/respond", json={
                    "session_id": sid, "text": text,
                })
                journey["turns"].append({"query": text, "status": response.status_code,
                                         "elapsed_seconds": round(perf_counter() - start, 3),
                                         "response": response.json()})
                output.write_text(json.dumps({**meta, "journeys": results}, ensure_ascii=False,
                                             indent=2), encoding="utf-8")
                print(json.dumps(journey["turns"][-1], ensure_ascii=True), flush=True)
                await asyncio.sleep(args.delay_seconds)


if __name__ == "__main__":
    asyncio.run(main())
