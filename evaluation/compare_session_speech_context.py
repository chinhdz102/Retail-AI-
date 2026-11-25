"""Same audio with fresh versus actual prior customer-brand conversation context."""

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


async def run(output):
    if output.exists():
        raise ValueError("Preserve earlier evidence")
    paired = json.loads(
        (ROOT / "docs/reports/artifacts/vietnamese_asr_paired_20261105.json").read_text(
            encoding="utf-8"
        )
    )
    sample = next(r for r in paired["rows"] if r["id"] == "retail-synthetic:2")
    path = ROOT / sample["path"]
    audio = path.read_bytes()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "policy": "Actual customer text states the brands before the audio turn; no gold "
        "transcript is passed to the ASR prompt. One exposed synthetic clip only.",
        "audio_sha256": hashlib.sha256(audio).hexdigest(),
        "prior_customer_text": "Mình muốn so sánh nước giặt OMO với Ariel.",
        "reference": sample["reference"],
        "rows": [],
    }
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=45) as client:
        for contextual in (False, True):
            session = await client.post("/sessions", json={"kiosk_id": "context-ablation"})
            session.raise_for_status()
            sid = session.json()["session_id"]
            if contextual:
                response = await client.post(
                    "/agent/respond",
                    json={
                        "session_id": sid,
                        "language": "vi",
                        "text": report["prior_customer_text"],
                    },
                )
                report["context_setup"] = {
                    "status": response.status_code,
                    "response": response.json(),
                }
            started = perf_counter()
            response = await client.post(
                "/audio/transcribe",
                data={
                    "session_id": sid,
                    "language": "vi",
                    "include_diagnostics": "true",
                },
                files={"file": (path.name, audio, "audio/mpeg")},
            )
            report["rows"].append(
                {
                    "contextual": contextual,
                    "http_status": response.status_code,
                    "response": response.json(),
                    "seconds": round(perf_counter() - started, 3),
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
