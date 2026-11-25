"""Paired, exposed VI/EN STT diagnostics against the running kiosk and service.

Public audio stays in .tmp; reports contain source attribution, hashes and text.
This is not a physical-microphone evaluation or a production accuracy estimate.
"""

from __future__ import annotations

import argparse
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

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.catalog_transcription import correct_catalog_words
from backend.services.groq import GROQ_STT_CATALOG_CONTEXT_TERMS, GroqService
from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]
EN_CACHE = ROOT / ".tmp/librispeech_stt_diagnostic"


def pcm_wav(content: bytes) -> bytes:
    frames = []
    with av.open(io.BytesIO(content)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16_000)
        for frame in container.decode(audio=0):
            frames.extend(converted.to_ndarray().tobytes()
                          for converted in resampler.resample(frame))
        frames.extend(converted.to_ndarray().tobytes()
                      for converted in resampler.resample(None))
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"".join(frames))
    return target.getvalue()


def english_samples() -> list[dict]:
    manifest_path = EN_CACHE / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))["samples"]
    EN_CACHE.mkdir(parents=True, exist_ok=True)
    samples = []
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        for offset in (0,):
            response = client.get(
                "https://datasets-server.huggingface.co/rows",
                params={"dataset": "openslr/librispeech_asr", "config": "clean",
                        "split": "test", "offset": offset, "length": 4},
            )
            response.raise_for_status()
            for entry in response.json()["rows"]:
                row = entry["row"]
                response = client.get(row["audio"][0]["src"])
                response.raise_for_status()
                content = pcm_wav(response.content)
                file = EN_CACHE / f"{row['id']}.wav"
                with wave.open(io.BytesIO(content)) as audio:
                    duration = audio.getnframes() / audio.getframerate()
                file.write_bytes(content)
                samples.append({
                    "id": f"librispeech:{row['id']}", "language": "en",
                    "path": str(file.relative_to(ROOT)), "reference": row["text"],
                    "source": "https://huggingface.co/datasets/openslr/librispeech_asr",
                    "license": "CC-BY-4.0", "split": "test-clean",
                    "duration_seconds": duration,
                    "source_audio_sha256": hashlib.sha256(response.content).hexdigest(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                })
    manifest_path.write_text(json.dumps({
        "selection": "First four rows at fixed offset 0, before ASR.",
        "samples": samples,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return samples


def vietnamese_samples() -> list[dict]:
    cache = ROOT / ".tmp/fleurs_vi_test_samples"
    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    samples = [{
        "id": f"fleurs:{s['filename']}", "language": "vi",
        "path": str((cache / s["filename"]).relative_to(ROOT)),
        "reference": s["reference"], "source": manifest["source"],
        "license": manifest["license"], "split": "test",
    } for s in manifest["samples"]]
    regional = json.loads((ROOT / "evaluation/results/"
                           "live_groq_stt_vivoice34_current_20261026.json").read_text(
                               encoding="utf-8"))
    samples.extend({
        "id": f"vivoice:{s['province']}", "language": "vi",
        "path": str(Path(".tmp/vivoice34_samples") / s["file"]),
        "reference": s["reference"],
        "source": regional["dataset_source"],
        "license": "CC-BY-4.0", "split": "train preview",
    } for s in regional["results"])
    extra = json.loads((ROOT / ".tmp/voice_diagnostics_20261103/manifest.json").read_text(
        encoding="utf-8"))
    samples.extend({**s, "language": "vi"} for s in extra["samples"]
                   if s["kind"] == "human-speech")
    return samples


async def run(output: Path, base_url: str) -> None:
    samples = vietnamese_samples() + await asyncio.to_thread(english_samples)
    settings = Settings()
    catalog = get_product_repository(settings)
    vocabulary = catalog.list_speech_terms(GROQ_STT_CATALOG_CONTEXT_TERMS)
    service = GroqService(settings)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "primary_model": settings.groq_stt_model,
        "fallback_model": settings.groq_stt_fallback_model,
        "selection": "Existing exposed 12 FLEURS + 7 ViVoice34 + 3 ViMD; "
                     "4 LibriSpeech test-clean rows at fixed offset 0 before ASR.",
        "limitations": [
            "Small exposed diagnostic; not representative retail or physical microphone audio.",
            "Repeated decoder requests can differ; compare actual text and hashes.",
            "ViVoice34 province labels do not independently verify accents.",
        ],
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ("backend/services/groq.py", "backend/api/main.py",
                                       "backend/services/catalog_transcription.py")},
        "rows": [], "complete": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(base_url=base_url, timeout=35) as client:
        response = await client.post("/sessions", json={"kiosk_id": "paired-stt-diagnostic"})
        response.raise_for_status()
        sid = response.json()["session_id"]
        try:
            for index, sample in enumerate(samples, 1):
                file = ROOT / sample["path"]
                content = file.read_bytes()
                row = {**sample, "audio_sha256": hashlib.sha256(content).hexdigest()}
                if sample.get("sha256") and row["audio_sha256"] != sample["sha256"]:
                    raise ValueError(f"Audio hash mismatch for {sample['id']}")
                started = perf_counter()
                response = await client.post(
                    "/audio/transcribe", data={"session_id": sid,
                                               "language": sample["language"]},
                    files={"file": (file.name, content, "audio/wav")},
                )
                row["endpoint"] = {
                    "http_status": response.status_code,
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "response": response.json(),
                }
                if response.is_success:
                    row["endpoint"].update(accuracy(sample["reference"],
                                                    response.json().get("text", "")))
                started = perf_counter()
                try:
                    result = await asyncio.wait_for(service.transcribe(
                        file.name, content, "audio/wav", language=sample["language"],
                        vocabulary=vocabulary,
                    ), timeout=25)
                    raw = str(result)
                    corrected, corrections = correct_catalog_words(
                        raw, catalog, language=sample["language"],
                    )
                    row["service"] = {
                        "model": result.model, "speech_detected": result.speech_detected,
                        "raw_text": raw, "corrected_text": corrected,
                        "corrections": [{"source": c.source, "replacement": c.replacement}
                                        for c in corrections],
                        "quality": result.quality, "trace": result.fallback_trace,
                        "raw_scores": accuracy(sample["reference"], raw),
                        "corrected_scores": accuracy(sample["reference"], corrected),
                    }
                except Exception as error:
                    row["service"] = {"error_type": type(error).__name__}
                row["service"]["latency_ms"] = round((perf_counter() - started) * 1000)
                report["rows"].append(row)
                save()
                print(json.dumps({"index": index, "total": len(samples), "case": sample["id"],
                                  "language": sample["language"],
                                  "status": row["endpoint"]["http_status"],
                                  "wer": row["endpoint"].get("wer"),
                                  "corrections": row["service"].get("corrections")}), flush=True)
        finally:
            await client.delete(f"/sessions/{sid}")
    report["complete"] = True
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(
        "docs/reports/artifacts/stt_regression_paired_20261103.json"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    asyncio.run(run(args.output, args.base_url))
