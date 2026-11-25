"""Score identical noisy utterances before/after installed worklet replay via HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import av
import httpx
import numpy as np

from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reports/artifacts/noise_stt_ablation_20261103.json"


def wav(samples: np.ndarray) -> bytes:
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
    frame.sample_rate = 48000
    r = av.AudioResampler(format="s16", layout="mono", rate=16000)
    frames = r.resample(frame) + r.resample(None)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(f.to_ndarray().tobytes() for f in frames))
    return buf.getvalue()


async def run():
    manifest = json.loads((ROOT / ".tmp/noise_ablation_20261103/manifest.json").read_text("utf-8"))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "selection": manifest["selection"],
        "limitations": manifest["limitations"],
        "rows": [],
    }

    def save():
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=35) as client:
        res = await client.post("/sessions", json={"kiosk_id": "noise-ablation"})
        res.raise_for_status()
        sid = res.json()["session_id"]
        try:
            for item in manifest["rows"]:
                for method in ["baseline", "rnnoise", "gtcrn"]:
                    filename = (
                        item["pcm_path"]
                        if method == "baseline"
                        else item["pcm_path"].replace(".f32", "." + method + ".f32")
                    )
                    samples = np.fromfile(ROOT / filename, dtype="<f4")
                    content = wav(samples)
                    # Noise attenuation after warm-up; no speech-preservation inference.
                    middle = samples[48000:-4800] if samples.size > 52800 else samples
                    rms = float(np.sqrt(np.mean(middle**2)))
                    row = {
                        **item,
                        "method": method,
                        "rms": rms,
                        "audio_sha256": hashlib.sha256(content).hexdigest(),
                    }
                    started = perf_counter()
                    try:
                        response = await client.post(
                            "/audio/transcribe",
                            data={"session_id": sid, "language": item["language"]},
                            files={"file": ("noise-diagnostic.wav", content, "audio/wav")},
                        )
                        row["http_status"] = response.status_code
                        if response.is_success:
                            row["response"] = response.json()
                            if item["reference"]:
                                row.update(
                                    accuracy(item["reference"], row["response"].get("text", ""))
                                )
                            else:
                                row["false_accept"] = bool(row["response"].get("text", "").strip())
                        else:
                            row["error"] = "http_error"
                    except Exception as exc:
                        row["error"] = type(exc).__name__
                    row["latency_ms"] = round((perf_counter() - started) * 1000)
                    report["rows"].append(row)
                    save()
                    print(
                        json.dumps(
                            {
                                "index": len(report["rows"]),
                                "id": item["id"],
                                "method": method,
                                "status": row.get("http_status"),
                                "wer": row.get("wer"),
                                "false_accept": row.get("false_accept"),
                            }
                        ),
                        flush=True,
                    )
        finally:
            await client.delete(f"/sessions/{sid}")
    report["complete"] = True
    save()


if __name__ == "__main__":
    asyncio.run(run())
