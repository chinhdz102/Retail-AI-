"""Replay acoustic preflight with a fake ASR, not a transcription accuracy test.

Base corpora are exposed/licensed diagnostics. Transformations use FLEURS and
ESC-50; audio stays local and only metrics/hashes are written to the report.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import av
import numpy as np

from backend.config import Settings
from backend.services.groq import GroqService
from backend.services.speech_activity import MODEL_PATH, _decode_audio

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reports/artifacts/voice_acoustic_replay_20261103.json"


def encode(samples, *, fmt="wav", codec="pcm_s16le", rate=16000, stereo=False):
    output = io.BytesIO()
    layout = "stereo" if stereo else "mono"
    data = np.tile(samples, (2 if stereo else 1, 1)).astype(np.float32)
    with av.open(output, "w", format=fmt) as container:
        stream = container.add_stream(codec, rate=rate)
        stream.layout = layout
        frame = av.AudioFrame.from_ndarray(data, format="fltp", layout=layout)
        frame.sample_rate = 16000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


async def run():
    base = []
    fleurs = json.loads((ROOT / ".tmp/fleurs_vi_test_samples/manifest.json").read_text("utf-8"))
    for sample in fleurs["samples"]:
        base.append((f"fleurs:{sample['filename']}",
                     ROOT / ".tmp/fleurs_vi_test_samples" / sample["filename"], True))
    regional_path = ROOT / "evaluation/results/live_groq_stt_vivoice34_current_20261026.json"
    regional = json.loads((regional_path).read_text("utf-8"))
    for sample in regional["results"]:
        base.append((f"vivoice:{sample['province']}",
                     ROOT / ".tmp/vivoice34_samples" / sample["file"], True))
    extra = json.loads((ROOT / ".tmp/voice_diagnostics_20261103/manifest.json").read_text("utf-8"))
    for sample in extra["samples"]:
        base.append((sample["id"], ROOT / sample["path"], sample["kind"] == "human-speech"))
    rows = []
    for label, file, expected in base:
        content = file.read_bytes()
        rows.append((label, content, expected, hashlib.sha256(content).hexdigest()))
    # Predetermine variants by manifest order; never select based on gate results.
    selected = base[:4] + [row for row in base if not row[2]]
    for label, file, expected in selected:
        content = file.read_bytes()
        source_hash = hashlib.sha256(content).hexdigest()
        samples = _decode_audio(content, perf_counter() + 5)
        for fmt, codec in [("mp3", "libmp3lame"), ("webm", "libopus"), ("mp4", "aac")]:
            rows.append((f"{label}/{fmt}", encode(samples, fmt=fmt, codec=codec),
                         expected, source_hash))
        if expected:
            for gain in [0.02, 0.05]:
                rows.append((f"{label}/gain-{gain}", encode(samples * gain), True, source_hash))
            rows.append((f"{label}/8khz", encode(samples, rate=8000), True, source_hash))
            rows.append((f"{label}/stereo", encode(samples, stereo=True), True, source_hash))
        else:
            for gain in [0.02, 0.05]:
                rows.append((f"{label}/gain-{gain}", encode(samples * gain), False, source_hash))
    rng = np.random.default_rng(42)
    seconds = np.arange(48000) / 16000
    for label, samples in [
        ("silence", np.zeros(48000)),
        ("tone", 0.15 * np.sin(2 * np.pi * 440 * seconds)),
        ("white-noise", rng.uniform(-0.05, 0.05, 48000)),
        ("am-harmonics", 0.01 * sum(strength * np.sin(2 * np.pi * 155 * harmonic * seconds)
                                   for harmonic, strength in [(1, 1), (2, .4), (3, .2)])
         * (0.7 + 0.3 * np.sin(2 * np.pi * 3 * seconds))),
    ]:
        rows.append((label, encode(samples), False, None))

    calls = []

    async def create(**_kwargs):
        calls.append(True)
        return SimpleNamespace(text="Test speech.", segments=[SimpleNamespace(
            start=0.0, end=1.0, no_speech_prob=0.001, avg_logprob=-0.1, compression_ratio=1.0,
        )])

    service = GroqService(Settings(_env_file=None, groq_api_key="fake-acoustic-test"),
                          client=SimpleNamespace(audio=SimpleNamespace(
                              transcriptions=SimpleNamespace(create=create))))
    files = [ROOT / "backend/services/groq.py", ROOT / "backend/services/speech_activity.py",
             MODEL_PATH]
    hashes = {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
              for file in files}
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "kind": "real-decoder-real-onnx-full-preflight-fake-asr",
        "limitations": ["No ASR accuracy or kiosk microphone claim.",
                        "Exposed, small corpus; variants are correlated, not independent examples.",
                        "Regional metadata is not independent accent verification.",
                        "Environmental labels do not independently exclude background speech."],
        "sources": ["google/fleurs vi_vn test; CC-BY-4.0",
                    "anonymous-vivoice34/ViVoice34 train preview; CC-BY-4.0",
                    "nguyendv02/ViMD_Dataset test preview; CC-BY-NC-ND-4.0",
                    "karolpiczak/ESC-50 fold 5; CC-BY-NC-3.0"],
        "variant_selection": "First four FLEURS manifest files and every cached ESC control.",
        "source_sha256": hashes, "rows": [],
    }
    for label, content, expected, source_hash in rows:
        started, before = perf_counter(), len(calls)
        try:
            result = await service.transcribe("sample.audio", content, "audio/wav")
            row = {"case": label, "expected_speech": expected,
                   "speech_detected": result.speech_detected,
                   "asr_calls": len(calls) - before,
                   "latency_ms": round((perf_counter() - started) * 1000, 1),
                   "source_audio_sha256": source_hash,
                   "input_sha256": hashlib.sha256(content).hexdigest(),
                   "quality": result.quality,
                   "reason": result.fallback_trace[-1].get("reason")}
        except Exception as exc:
            row = {"case": label, "expected_speech": expected,
                   "speech_detected": None, "asr_calls": len(calls) - before,
                   "latency_ms": round((perf_counter() - started) * 1000, 1),
                   "source_audio_sha256": source_hash,
                   "input_sha256": hashlib.sha256(content).hexdigest(),
                   "error_type": type(exc).__name__}
        report["rows"].append(row)
        print(label, "error" if row["speech_detected"] is None else
              "speech" if row["speech_detected"] else "rejected", flush=True)
    report["false_rejects"] = [row["case"] for row in report["rows"]
                               if row["expected_speech"] and row["speech_detected"] is False]
    report["false_accepts"] = [row["case"] for row in report["rows"]
                               if not row["expected_speech"] and row["speech_detected"]]
    report["errors"] = [row["case"] for row in report["rows"] if "error_type" in row]
    report["source_stable"] = all(hashlib.sha256(file.read_bytes()).hexdigest()
                                   == hashes[str(file.relative_to(ROOT))] for file in files)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({key: report[key] for key in [
        "false_rejects", "false_accepts", "source_stable",
    ]}))


if __name__ == "__main__":
    asyncio.run(run())
