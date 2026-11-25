"""Capture real API replies for user-authored development conversations."""

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


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="before")
    parser.add_argument("--delay-seconds", type=float, default=4)
    parser.add_argument("--case", type=int, action="append", dest="selected_cases")
    args = parser.parse_args()
    if args.selected_cases and any(index < 1 or index > 35 for index in args.selected_cases):
        parser.error("Case indexes must be between 1 and 35")
    case_path = ROOT / "evaluation/cases/customer_consultation_35_dev_20261031.json"
    cases = json.loads(case_path.read_text(encoding="utf-8"))
    date = datetime.now().strftime("%Y%m%d")
    output = ROOT / f"docs/reports/artifacts/customer_consultation_35_{args.label}_{date}.json"
    rows = []
    source_hashes = {str(path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                     for path in ("backend/services/customer_consultation.py",
                                  "backend/api/main.py")}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=90) as client:
        for index, query in enumerate(cases["queries"], start=1):
            if args.selected_cases and index not in args.selected_cases:
                continue
            session = (await client.post("/sessions", json={"kiosk_id": "customer-review"})).json()[
                "session_id"
            ]
            start = perf_counter()
            try:
                response = await client.post("/agent/respond", json={
                    "session_id": session, "text": query,
                })
                row = {"index": index, "query": query, "status": response.status_code,
                       "elapsed_seconds": round(perf_counter() - start, 3),
                       "response": response.json()}
            except Exception as exc:
                row = {"index": index, "query": query, "error_type": type(exc).__name__}
            rows.append(row)
            output.write_text(json.dumps({
                "created_at": datetime.now(UTC).isoformat(), "provenance": cases["provenance"],
                "context": "Each query begins a fresh session. Deictic questions should clarify.",
                "source_sha256_at_start": source_hashes,
                "selected_cases": args.selected_cases or list(range(1, 36)),
                "rows": rows,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(row, ensure_ascii=True), flush=True)
            if args.delay_seconds > 0:
                await asyncio.sleep(args.delay_seconds)


if __name__ == "__main__":
    asyncio.run(main())
