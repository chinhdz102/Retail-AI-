"""Record real SSE replies: exposed customer examples, plus cross-category journeys.

Automatic checks are transport/grounding checks, not a claim of human-level advice.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from evaluation.reproduce_clarification_repeat import stream_turn

ROOT = Path(__file__).resolve().parents[1]
JOURNEYS = {
    "sales-drinks": ["loại đồ uống nào bán chạy nhất"],
    "hotpot": [
        "tối nay t muốn ăn lẩu m tư vấn cho t với",
        "loại thịt nào để ăn lẩu ý",
        "thịt bò bình thường hoặc thịt ba chỉ cũng oke, tầm 200k thôi",
    ],
    "skin-care": ["có loại nào điều trị mụn ko", "tư vấn cho t với", "da dầu nha"],
    "household": [
        "Em muốn mua đồ để dọn nhà cuối tuần, cần những gì nhỉ?",
        "Dọn bếp với lau nhà thôi, tầm 150k",
    ],
    "breakfast": ["Em muốn mua cái gì đó ăn sáng cho nhanh mà vẫn no, có loại nào ổn không?"],
    "stationery": ["Cần sổ ghi bài mang đi học, gọn gọn mà rẻ chút"],
    "pet-safety": ["Nhà em có nuôi mèo, mua nước lau sàn an toàn quanh thú cưng thì chọn gì?"],
    "laundry-context": [
        "Nhà mình giặt máy cửa trước, tư vấn nước giặt tầm 150 nghìn thôi",
        "loại nào đáng tiền hơn, chọn giúp mình đi",
        "chuyển sang nước rửa chén dưới 50 nghìn nhé",
    ],
    "low-sugar-context": [
        "Mình cần nước uống không đường mang theo đi làm, dưới 30k",
        "còn loại nào khác không, đừng lấy cà phê",
    ],
    "personal-care-context": [
        "Mình cần dầu gội hằng ngày dưới 120 nghìn, tóc nhanh bết",
        "thế tư vấn cho t đi",
    ],
}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--journey", action="append", choices=JOURNEYS)
    parser.add_argument("--full35", action="store_true")
    parser.add_argument("--customer-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--from-case", type=int, default=1)
    parser.add_argument("--to-case", type=int, default=35)
    parser.add_argument("--delay-seconds", type=float, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    jobs = [(name, turns) for name, turns in JOURNEYS.items()
            if not args.journey or name in args.journey]
    if args.customer_only:
        jobs = []
    if args.full35:
        case_file = ROOT / "evaluation/cases/customer_consultation_35_dev_20261031.json"
        questions = json.loads(case_file.read_text(encoding="utf-8"))["queries"]
        jobs += [
            (f"customer-{i + 1:02}", [text])
            for i, text in enumerate(questions)
            if args.from_case <= i + 1 <= args.to_case
        ]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": "Exposed development examples; mechanical checks, not blind quality ratings",
        "expected_journeys": len(jobs),
        "expected_turns": sum(len(turns) for _, turns in jobs),
        "completed": False,
        "sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in ["backend/services/customer_consultation.py",
                                 "backend/services/demo_sales.py", "backend/api/main.py",
                                 "backend/services/shopping_guidance.py",
                                 "backend/services/shopping_language.py",
                                 "backend/services/query_understanding.py",
                                 "data/shopping_guidance.json", "backend/services/groq.py"]},
        "journeys": [],
    }
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous["sources"] != report["sources"]:
            raise ValueError("Cannot resume across a source change; use a separate report.")
        report["journeys"] = previous["journeys"]
        report["created_at"] = previous["created_at"]
        completed = {j["name"] for j in previous["journeys"] if len(j["turns"]) == next(
            (len(turns) for name, turns in jobs if name == j["name"]), -1,
        )}
        if any(j["name"] not in completed for j in previous["journeys"]):
            raise ValueError("An unfinished multi-turn journey must be run in a new report.")
        jobs = [(name, turns) for name, turns in jobs if name not in completed]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=100) as client:
        for name, turns in jobs:
            sid = (await client.post("/sessions", json={"kiosk_id": "retail-advice-evaluation"}))
            sid.raise_for_status()
            journey = {"name": name, "session_id": sid.json()["session_id"], "turns": []}
            report["journeys"].append(journey)
            for text in turns:
                turn = await stream_turn(client, sid.json()["session_id"], text)
                grounding = []
                for card in turn["products"]:
                    response = await client.get(f"/products/{card['product_id']}/snapshot")
                    payload = response.json()
                    snapshot = payload.get("product", payload)
                    grounding.append(
                        response.status_code == 200
                        and card["name"] == snapshot.get("name")
                        and card["price_vnd"] == snapshot.get("price_vnd")
                    )
                turn["checks"] = {
                    "complete": turn["status"] == 200 and "complete" in turn["event_names"],
                    "cards_match_catalog": all(grounding),
                    "sales_bounded": all(
                        p.get("demo_units_sold_30d") is None
                        or 0 <= p["demo_units_sold_30d"] < 1000 for p in turn["products"]
                    ),
                    "no_technical_fallback": not any(word in turn["text"].casefold() for word in (
                        "khớp đủ yêu cầu", "catalog chưa xác nhận", "snapshot",
                        "dữ liệu đã xác minh",
                    )),
                    "no_consultation_fallback": (
                        turn["status"] == 200
                        and turn["model"] is not None
                        and turn["model"] != "consultation:consultation-fallback"
                    ),
                    "no_permission_loop": not re.search(
                        r"bạn (?:có )?muốn mình (?:tìm|tư vấn|gợi ý|chọn|lọc)"
                        r"[^.!?]{0,100}không\s*\?", turn["text"].casefold(),
                    ),
                }
                questions = re.findall(r"[^.!?]*\?", turn["text"].casefold())
                previous_text = journey["turns"][-1]["text"].casefold() if journey["turns"] else ""
                turn["checks"]["no_exact_repeated_question"] = not any(
                    q.strip() and q.strip() in previous_text for q in questions
                )
                turn["requires_content_review"] = True
                journey["turns"].append(turn)
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                print(json.dumps({"journey": name, **turn}, ensure_ascii=True), flush=True)
                if args.delay_seconds > 0:
                    await asyncio.sleep(args.delay_seconds)
    report["completed"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
