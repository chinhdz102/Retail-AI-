"""Check Vietnamese fast-transcription availability before scheduling a benchmark.

One fixed public FLEURS file, no transcript/brand hints in the request. This is
an availability probe, not an accuracy benchmark or a production integration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

from backend.config import Settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reports/artifacts/azure_vietnamese_availability_20261106.json"


async def run():
    if OUTPUT.exists():
        raise ValueError("Preserve previous evidence; choose a new output")
    settings = Settings()
    if not settings.has_azure_speech:
        raise ValueError("Azure Speech is not configured")
    baseline = json.loads((ROOT / (
        "docs/reports/artifacts/vietnamese_asr_paired_20261105.json"
    )).read_text(encoding="utf-8"))
    sample = next(row for row in baseline["rows"] if row["id"].startswith("fleurs:"))
    content = (ROOT / sample["path"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != sample["sha256"]:
        raise ValueError("Input differs from the captured baseline")
    region = settings.azure_speech_region.strip().lower()
    if not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("Invalid Azure region")
    key = settings.azure_speech_key.get_secret_value()
    report = {
        "created_at": datetime.now(UTC).isoformat(), "complete": False,
        "scope": "One public audio file; availability only. No production setting changed.",
        "sample_id": sample["id"], "audio_sha256": sample["sha256"],
        "reference_used_as_prompt": False, "rows": [],
    }

    def save():
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    save()
    async with httpx.AsyncClient(timeout=30) as client:
        for enhanced in (True, False):
            definition = {"locales": ["vi-VN"]}
            if enhanced:
                definition["enhancedMode"] = {"enabled": True, "task": "transcribe"}
            row = {"enhanced": enhanced, "definition": definition}
            started = perf_counter()
            try:
                response = await client.post(
                    f"https://{region}.api.cognitive.microsoft.com"
                    "/speechtotext/transcriptions:transcribe",
                    params={"api-version": "2025-10-15"},
                    headers={"Ocp-Apim-Subscription-Key": key},
                    data={"definition": json.dumps(definition)},
                    files={"audio": ("public_sample.wav", content, "audio/wav")},
                )
                payload = response.json()
                row["http_status"] = response.status_code
                if response.status_code == 200:
                    row["transcript"] = payload.get("combinedPhrases", [])
                else:
                    error = payload.get("error", payload)
                    row["error_code"] = str(error.get("code", ""))[:100]
                    row["error_message"] = str(error.get("message", "")).replace(
                        key, "[REDACTED]"
                    )[:500]
            except (httpx.HTTPError, ValueError) as exc:
                row["error_type"] = type(exc).__name__
            row["seconds"] = round(perf_counter() - started, 3)
            report["rows"].append(row)
            save()
            print(json.dumps(row, ensure_ascii=True), flush=True)
    report["complete"] = True
    save()


if __name__ == "__main__":
    asyncio.run(run())
