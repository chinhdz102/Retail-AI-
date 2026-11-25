"""Inspect spoken recommendation wording separately from recommendation relevance.

The offline mode uses the real snapshot and API with provider calls disabled.
Use --base-url to probe an already running demo, including its consultation layer.
An answer without a detected phrase is not an employee-quality or relevance pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = (
    "Có loại thịt cho ăn lẩu không?",
    "Gợi ý đồ uống dưới 10.000đ.",
    "Tìm bánh quy dưới 50 nghìn.",
    "Có dầu gội nào dưới 150 nghìn không?",
    "Tìm nước giặt dưới 150 nghìn.",
    "Gợi ý sữa tươi không đường.",
    "Gợi ý khăn giấy.",
    "Gợi ý mì ăn liền dưới 20 nghìn.",
    "Tìm kem đánh răng dưới 50 nghìn.",
    "Tìm nước rửa chén dưới 50 nghìn.",
    "Mình có 100 nghìn, muốn mua nước với snack cho hai người.",
    "Xà phòng tắm loại nào rẻ?",
    "Đồ uống loại nào ngon nhất?",
)
ROUTINE_LOGS = (
    "Mình chọn theo",
    "Giá tại cửa hàng có thể khác catalog",
    "Tồn hiện tại chưa được xác minh",
    "Mình chưa kiểm tra được hàng còn tại cửa hàng",
    "Catalog chưa xác nhận",
    "Theo giá catalog, các combo",
    "Giá là tham khảo theo catalog, chưa xác minh tại chi nhánh",
    "Dữ liệu snapshot ngày",
)


def audit(client, questions):
    rows = []
    for question in questions:
        started = datetime.now(UTC)
        row = {"question": question}
        try:
            session = client.post("/sessions", json={"kiosk_id": "customer-wording-audit"})
            session.raise_for_status()
            response = client.post("/agent/respond", json={
                "session_id": session.json()["session_id"],
                "text": question,
                "language": "vi",
            })
            response.raise_for_status()
            payload = response.json()
            answer = payload["text"]
            row.update(
                text=answer,
                model=payload["model"],
                grounded_product_ids=payload["grounded_product_ids"],
                data_source=payload["data_source"],
                routine_logs=[p for p in ROUTINE_LOGS if p.casefold() in answer.casefold()],
                service_unavailable=payload["model"] == "consultation:consultation-fallback",
                degraded_lookup=payload["model"] == "consultation:consultation-local-fallback",
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            row["error"] = type(exc).__name__
        row["elapsed_seconds"] = round((datetime.now(UTC) - started).total_seconds(), 3)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--limit", type=int, choices=range(1, len(QUESTIONS) + 1))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.base_url:
        client = httpx.Client(base_url=args.base_url, timeout=45)
    else:
        os.environ["CATALOG_REPOSITORY"] = "snapshot"
        os.environ["CUSTOMER_CONSULTATION_ENABLED"] = "false"
        for name in ("GROQ_API_KEY", *(f"GROQ_API_KEY_FALLBACK_{i}" for i in range(1, 5))):
            os.environ[name] = ""
        from fastapi.testclient import TestClient

        from backend.api.main import app

        client = TestClient(app)
    with client:
        rows = audit(client, QUESTIONS[:args.limit])
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "live_http" if args.base_url else "offline_real_snapshot_api",
        "scope": "Response wording only; not a recommendation relevance or employee-quality score",
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "backend/api/main.py", "backend/services/customer_consultation.py",
                "backend/services/retail_tools.py",
            )
        },
        "case_count": len(rows),
        "routine_log_count": sum(bool(row.get("routine_logs")) for row in rows),
        "with_products_count": sum(bool(row.get("grounded_product_ids")) for row in rows),
        "service_unavailable_count": sum(bool(row.get("service_unavailable")) for row in rows),
        "degraded_lookup_count": sum(bool(row.get("degraded_lookup")) for row in rows),
        "error_count": sum("error" in row for row in rows),
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}))


if __name__ == "__main__":
    main()
