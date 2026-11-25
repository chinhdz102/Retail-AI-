"""Development HTTP advice probes with paced requests and unedited checkpoints.

Does not score employee quality automatically. Every failure stays in the artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

CASES = {
    "weekday": ["Cho tao biết hôm nay là thứ mấy."],
    "essentials": [
        "Mới dọn ra ở riêng, mua đồ thiết yếu trong siêu thị thì nên mua gì trước?",
        "Tư vấn chọn giúp t mấy món cần nhất đi",
    ],
    "breakfast": ["Sáng đi làm vội thì mua gì ăn cho nhanh?"],
    "hotpot": [
        "Tối ăn lẩu, tư vấn thịt nhúng tầm 200k đi",
        "Bò bình thường hoặc ba chỉ heo được không, nhà tự thái được",
        "Cho tao biết hôm nay là thứ mấy.",
    ],
    "acne": ["Da mặt t nhiều mụn, nên chọn đồ chăm sóc thế nào?", "tư vấn cho t"],
    "detergent": ["So sánh giúp t nước giặt Omo với Ariel, nhà dùng máy cửa trước"],
    "cleaning": ["Cuối tuần dọn phòng trọ, nên mua những món nào trước cho đỡ tốn?"],
    "pet": ["Nhà nuôi mèo, chọn nước lau sàn thế nào để dùng quanh nó?"],
    "baby": ["Bé 6 tháng 10kg thì nên chọn bỉm như thế nào?"],
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause-seconds", type=float, default=45)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--journeys", nargs="*", choices=CASES)
    args = parser.parse_args()
    output = args.output
    if output.exists():
        raise SystemExit("Choose a new output path; earlier evidence is preserved.")
    rows = []
    root = Path(__file__).resolve().parents[1]
    source_paths = [
        "backend/api/main.py", "backend/services/customer_consultation.py",
        "backend/services/groq.py", "backend/services/shopping_language.py",
        "backend/services/query_understanding.py", "backend/services/retail_tools.py",
        "backend/services/consultation_policy.py", "backend/services/product_form.py",
    ]
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "endpoint": "http://127.0.0.1:8000/agent/respond",
        "kind": "authored development probes, manual assessment required",
        "pause_seconds": args.pause_seconds,
        "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                          for p in source_paths},
        "rows": rows,
    }

    def post(path, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:8000" + path,
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=100) as response:
            return response.status, json.loads(response.read())

    last_provider_turn = 0.0
    for journey, turns in CASES.items():
        if args.journeys and journey not in args.journeys:
            continue
        _, session = post("/sessions", {"kiosk_id": "text-advice-development-probe"})
        for index, prompt in enumerate(turns, 1):
            is_calendar = "thứ mấy" in prompt
            if not is_calendar:
                remaining = args.pause_seconds - (time.monotonic() - last_provider_turn)
                if remaining > 0:
                    time.sleep(remaining)
            started = time.perf_counter()
            row = {"journey": journey, "turn": index, "prompt": prompt,
                   "started_at": datetime.now(UTC).isoformat()}
            try:
                status, body = post("/agent/respond", {
                    "session_id": session["session_id"], "text": prompt, "language": "vi",
                })
                row.update(status=status, response=body)
            except Exception as exc:
                row.update(status=None, error_type=type(exc).__name__)
            row["latency_s"] = round(time.perf_counter() - started, 3)
            if not is_calendar:
                last_provider_turn = time.monotonic()
            rows.append(row)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            response = row.get("response", {})
            print(json.dumps({
                "journey": journey, "turn": index, "status": row["status"],
                "latency_s": row["latency_s"], "model": response.get("model"),
                "text": response.get("text"),
                "products": [p["name"] for p in response.get("products", [])],
            }, ensure_ascii=False), flush=True)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_stable"] = all(
        hashlib.sha256((root / p).read_bytes()).hexdigest() == expected
        for p, expected in report["source_sha256"].items()
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
