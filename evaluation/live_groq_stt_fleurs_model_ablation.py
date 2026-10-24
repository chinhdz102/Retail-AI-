"""Compare Groq Whisper models on the same public, human-spoken Vietnamese clips.

This exercises the kiosk Groq transcription service, retail vocabulary prompt,
signal-quality checks, and catalog correction while changing only the STT model.
Audio is read from the local Google FLEURS test split and is never written by
this script. The manifest must have been obtained from the public CC-BY-4.0 set.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.catalog_transcription import correct_catalog_words
from backend.services.groq import GroqService
from evaluation.live_groq_stt_benchmark import accuracy

DEFAULT_MANIFEST = Path(".tmp/fleurs_vi_test_samples/manifest.json")
DEFAULT_OUTPUT = Path("evaluation/results/live_groq_stt_fleurs_model_ablation_20261021.json")
MODELS = ("whisper-large-v3", "whisper-large-v3-turbo")


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return ordered[index]


def _model_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in results if row["status"] in {"success", "rejected"}]
    latencies = [int(row["latency_ms"]) for row in scored]
    word_errors = sum(int(row.get("word_errors", 0)) for row in scored)
    word_count = sum(int(row.get("reference_word_count", 0)) for row in scored)
    char_errors = sum(int(row.get("character_errors", 0)) for row in scored)
    char_count = sum(int(row.get("reference_character_count", 0)) for row in scored)
    raw_word_errors = sum(int(row.get("raw_word_errors", 0)) for row in scored)
    raw_char_errors = sum(int(row.get("raw_character_errors", 0)) for row in scored)
    return {
        "attempt_count": len(results),
        "accepted": sum(row["status"] == "success" for row in results),
        "rejected": sum(row["status"] == "rejected" for row in results),
        "errors": sum(row["status"] == "error" for row in results),
        "exact_matches_after_catalog_correction": sum(row.get("wer") == 0 for row in scored),
        "aggregate_wer_raw": raw_word_errors / max(1, word_count),
        "aggregate_cer_raw": raw_char_errors / max(1, char_count),
        "aggregate_wer_after_catalog_correction": word_errors / max(1, word_count),
        "aggregate_cer_after_catalog_correction": char_errors / max(1, char_count),
        "median_latency_ms": round(median(latencies)) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 95),
        "models_used": sorted({str(row["model"]) for row in results if row.get("model")}),
    }


async def run(manifest_path: Path, output_path: Path, timeout_seconds: float) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset") != "google/fleurs" or manifest.get("config") != "vi_vn":
        raise ValueError("Expected a Google FLEURS Vietnamese vi_vn manifest")
    samples = manifest.get("samples") or []
    if not samples:
        raise ValueError("FLEURS manifest has no labeled samples")

    settings = Settings()
    if not settings.has_groq_key:
        raise RuntimeError("GROQ_API_KEY is not configured in the project .env")
    catalog = get_product_repository(settings)
    vocabulary = catalog.list_speech_terms(32)
    sample_audio: list[tuple[dict[str, Any], bytes]] = []
    for sample in samples:
        filename = str(sample["filename"])
        audio_path = manifest_path.parent / filename
        audio = audio_path.read_bytes()
        if sample.get("bytes") and len(audio) != int(sample["bytes"]):
            raise ValueError(f"Audio size mismatch for {filename}")
        sample_audio.append((sample, audio))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "kind": "live_groq_stt_fleurs_same_audio_model_ablation",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "dataset": manifest["dataset"],
        "config": manifest["config"],
        "split": manifest["split"],
        "source": manifest["source"],
        "license": manifest["license"],
        "sample_count": len(samples),
        "models": list(MODELS),
        "comparison_controls": [
            "The exact same public audio and reference are used for both models.",
            "Kiosk language=vi, temperature=0, retail prompt, signal checks, and catalog "
            "correction are held constant.",
            "Only the primary STT model changes; secondary-model fallback is disabled for "
            "attribution.",
            "Raw Whisper and post-catalog-correction accuracy are both recorded.",
        ],
        "limitations": [
            "FLEURS is read speech in a clean setup, not spontaneous retail conversation, "
            "dialect stress testing, or the user's physical microphone.",
            "This is a paired 12-clip diagnostic, not a production WER estimate.",
            "The retail prompt and catalog correction may bias general-domain FLEURS text; "
            "both models receive identical treatment.",
        ],
        "model_results": {},
        "complete": False,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for model in MODELS:
        model_settings = settings.model_copy(
            update={
                "groq_stt_model": model,
                "groq_stt_fallback_model": "",
                "groq_timeout_seconds": timeout_seconds,
            }
        )
        service = GroqService(settings=model_settings)
        results: list[dict[str, Any]] = []
        for index, (sample, audio) in enumerate(sample_audio, start=1):
            filename = str(sample["filename"])
            started = perf_counter()
            try:
                transcription = await service.transcribe(
                    filename,
                    audio,
                    "audio/wav",
                    language="vi",
                    vocabulary=vocabulary,
                )
                raw_text = str(transcription)
                text, corrections = correct_catalog_words(raw_text, catalog, language="vi")
                status = "success" if transcription.speech_detected else "rejected"
                row: dict[str, Any] = {
                    "filename": filename,
                    "status": status,
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "model": transcription.model,
                    "quality": transcription.quality,
                    "fallback_trace": transcription.fallback_trace,
                    "reference": str(sample["reference"]),
                    "raw_transcription": raw_text,
                    "transcription": text,
                    "catalog_corrections": [
                        {"source": item.source, "replacement": item.replacement}
                        for item in corrections
                    ],
                    "audio_sha256": hashlib.sha256(audio).hexdigest(),
                }
                if transcription.speech_detected:
                    raw_scores = accuracy(str(sample["reference"]), raw_text)
                    corrected_scores = accuracy(str(sample["reference"]), text)
                    row.update(corrected_scores)
                    row["raw_word_errors"] = raw_scores["word_errors"]
                    row["raw_character_errors"] = raw_scores["character_errors"]
                else:
                    row["raw_transcription"] = ""
                    row["transcription"] = ""
                    row.update(accuracy(str(sample["reference"]), ""))
                    row["raw_word_errors"] = row["word_errors"]
                    row["raw_character_errors"] = row["character_errors"]
            except Exception as error:
                row = {
                    "filename": filename,
                    "status": "error",
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "error_type": type(error).__name__,
                    "reference": str(sample["reference"]),
                    "audio_sha256": hashlib.sha256(audio).hexdigest(),
                }
            results.append(row)
            report["model_results"][model] = {
                "results": results,
                "summary": _model_summary(results),
            }
            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"{model} {index}/{len(samples)} {row['status']} "
                f"{row['latency_ms']}ms wer={row.get('wer')}",
                flush=True,
            )
        for client in service._clients_by_credential.values():
            await client.close()

    report["paired_audio_hashes_match"] = all(
        report["model_results"][MODELS[0]]["results"][index]["audio_sha256"]
        == report["model_results"][MODELS[1]]["results"][index]["audio_sha256"]
        for index in range(len(samples))
    )
    report["complete"] = all(
        len(report["model_results"][model]["results"]) == len(samples)
        and report["model_results"][model]["summary"]["errors"] == 0
        for model in MODELS
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    result = asyncio.run(run(args.manifest, args.output, args.timeout_seconds))
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "paired_audio_hashes_match": result["paired_audio_hashes_match"],
                "summaries": {
                    model: result["model_results"][model]["summary"] for model in MODELS
                },
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
