"""Replay cached public controls through native VAD and optionally the real API.

Run with --source-root pointing to the checkout containing cached public samples.
No customer microphone is used. --live sends speech candidates to configured Groq.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx
import numpy as np

from backend.api.main import app, get_whisper_service
from backend.services.speech_activity import analyze_speech_audio
from backend.services.whisper_stt import WhisperSTT, _wav


class DiagnosticWhisper(WhisperSTT):
    def __init__(self):
        super().__init__()
        self.evidence = None

    async def _decode(self, client, wav, keys):
        self.evidence = await super()._decode(client, wav, keys)
        return self.evidence


def cases(root: Path):
    noise = root / ".tmp/noise_controls_20261103"
    manifest = json.loads((noise / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["samples"]:
        yield (
            item["category"],
            "environmental-category",
            (noise / item["filename"]).read_bytes(),
            None,
        )
    speech = root / ".tmp/vietsuperspeech_vi_samples"
    manifest = json.loads((speech / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["samples"]:
        yield (
            item["filename"],
            "human-vietnamese",
            (speech / item["filename"]).read_bytes(),
            item["reference"],
        )
    for index, text in enumerate(
        ["Có.", "Không.", "Dạ.", "Một lít.", "Không đường.", "Sữa.", "Đăng ký kênh."]
    ):
        yield (
            text,
            "synthetic-short-vietnamese",
            (root / f".tmp/vi_stt_rewrite/short/{index}.mp3").read_bytes(),
            text,
        )
    for name, samples in [
        ("silence", np.zeros(32000, dtype=np.float32)),
        ("quiet-white-noise", np.random.default_rng(37).normal(0, 0.002, 32000)),
        ("440Hz-tone", np.sin(np.arange(32000) * 2 * np.pi * 440 / 16000) * 0.1),
    ]:
        yield name, "synthetic-nonspeech", _wav(samples), None


async def run(root: Path, output: Path, live: bool):
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "mode": "real-api-and-live-whisper" if live else "native-acoustic-only",
        "limitations": [
            "No physical microphone, speaker echo, or browser audio processing.",
            "Environmental categories are not independently annotated for background human voices.",
            "VietSuperSpeech uses noisy automatic labels; accuracy is not human verified.",
            "Short Vietnamese clips are Azure synthesis, not human recordings.",
        ],
        "rows": [],
    }
    service = DiagnosticWhisper()
    app.dependency_overrides[get_whisper_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://audit"
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "whisper-audit"})).json()[
            "session_id"
        ]
        try:
            for name, kind, content, reference in cases(root):
                started = perf_counter()
                activity = await asyncio.to_thread(analyze_speech_audio, content)
                row = {
                    "id": name,
                    "kind": kind,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "reference": reference,
                    "acoustic": asdict(activity.activity),
                }
                service.evidence = None
                if live:
                    response = await client.post(
                        "/audio/transcribe",
                        data={"session_id": session, "language": "vi"},
                        files={"file": ("sample.wav", content, "audio/wav")},
                    )
                    row["http_status"] = response.status_code
                    row["result"] = response.json()
                    if service.evidence is not None:
                        row["provider_evidence"] = service.evidence
                row["elapsed_ms"] = round((perf_counter() - started) * 1000)
                report["rows"].append(row)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{kind} {ascii(name)}: "
                    + (
                        f"status={row['http_status']} text={bool(row['result'].get('text'))} "
                        f"reason={row['result'].get('rejection_reason')}"
                        if live
                        else f"speech={activity.activity.speech_detected}"
                    ),
                    flush=True,
                )
        finally:
            await client.post(f"/sessions/{session}/close")
            app.dependency_overrides.pop(get_whisper_service, None)
    report["complete"] = all(r.get("http_status", 200) == 200 for r in report["rows"])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source_root, args.output, args.live))
