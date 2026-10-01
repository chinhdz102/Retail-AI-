"""Benchmark live Groq transcription latency and accuracy on a labeled clip.

The audio stays at the caller-provided path. Only the reference and returned
transcriptions, timing, model, and fallback metadata are written to the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import mimetypes
import os
import re
import unicodedata
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from openai import AsyncOpenAI

from backend.config import Settings
from backend.services.groq import GroqService


def normalize_text(text: str) -> str:
    text = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", " ", text)
    normalized = unicodedata.normalize("NFC", text).casefold()
    return " ".join(
        "".join(char if char.isalnum() or char.isspace() else " " for char in normalized).split()
    )


def edit_distance(left: list[str] | str, right: list[str] | str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def accuracy(reference: str, hypothesis: str) -> dict[str, float | int]:
    reference_normalized = normalize_text(reference)
    hypothesis_normalized = normalize_text(hypothesis)
    reference_words = reference_normalized.split()
    hypothesis_words = hypothesis_normalized.split()
    reference_chars = reference_normalized.replace(" ", "")
    hypothesis_chars = hypothesis_normalized.replace(" ", "")
    word_errors = edit_distance(reference_words, hypothesis_words)
    char_errors = edit_distance(reference_chars, hypothesis_chars)
    return {
        "wer": word_errors / max(1, len(reference_words)),
        "cer": char_errors / max(1, len(reference_chars)),
        "word_errors": word_errors,
        "reference_word_count": len(reference_words),
        "character_errors": char_errors,
        "reference_character_count": len(reference_chars),
    }


def audio_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, OSError, ZeroDivisionError):
        return None


def save_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(
    settings: Settings,
    audio_path: Path,
    reference: str | None,
    output: Path,
    trials: int,
    language: str,
    source: str | None,
    record_transcription: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    audio_bytes = audio_path.read_bytes()
    content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    report: dict[str, Any] = {
        "kind": "live_groq_stt_benchmark",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "audio_filename": audio_path.name,
        "audio_bytes": len(audio_bytes),
        "audio_duration_seconds": audio_duration_seconds(audio_path),
        "source": source,
        "language": language,
        "primary_model": settings.groq_stt_model,
        "fallback_model": settings.groq_stt_fallback_model,
        "trial_count": trials,
        "timeout_seconds": timeout_seconds,
        "reference": reference,
        "note": "Live API client latency; WER/CER are omitted when no reference is supplied.",
        "transcription_saved": record_transcription,
        "complete": False,
        "results": [],
    }
    save_report(report, output)

    async with AsyncOpenAI(
        api_key=settings.groq_api_key.get_secret_value(),
        base_url=settings.groq_base_url,
        timeout=timeout_seconds,
        max_retries=0,
    ) as client:
        service = GroqService(settings=settings, client=client)
        for trial in range(1, trials + 1):
            started = perf_counter()
            try:
                transcription = await service.transcribe(
                    audio_path.name,
                    audio_bytes,
                    content_type,
                    language=language,
                )
                text = str(transcription)
                item = {
                    "trial": trial,
                    "status": "success",
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "model": transcription.model,
                    "fallback_trace": transcription.fallback_trace,
                }
                if reference is not None:
                    item.update(accuracy(reference, text))
                if record_transcription:
                    item["transcription"] = text
            except Exception as exc:
                item = {
                    "trial": trial,
                    "status": "error",
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "error_type": type(exc).__name__,
                }
            report["results"].append(item)
            save_report(report, output)
            if item["status"] == "success":
                status = (
                    f" wer={item['wer']:.3f}"
                    if "wer" in item
                    else " accuracy=unscored-no-reference"
                )
                print(
                    f"STT trial={trial} model={item['model']} "
                    f"latency_ms={item['latency_ms']}{status}",
                    flush=True,
                )
            else:
                print(
                    f"STT trial={trial} status=error "
                    f"latency_ms={item['latency_ms']} type={item['error_type']}",
                    flush=True,
                )

    successes = [item for item in report["results"] if item["status"] == "success"]
    latencies = sorted(item["latency_ms"] for item in successes)
    if latencies:
        report["summary"] = {
            "success_count": len(successes),
            "median_latency_ms": latencies[len(latencies) // 2],
            "p95_latency_ms": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)],
            "models_used": sorted({item["model"] for item in successes}),
        }
        if reference is not None:
            report["summary"].update(
                {
                    "mean_wer": sum(item["wer"] for item in successes) / len(successes),
                    "mean_cer": sum(item["cer"] for item in successes) / len(successes),
                }
            )
    report["complete"] = True
    save_report(report, output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    reference_group = parser.add_mutually_exclusive_group()
    reference_group.add_argument("--reference")
    reference_group.add_argument("--reference-file", type=Path)
    parser.add_argument("--source")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--omit-transcription",
        action="store_true",
        help="Do not store the returned transcript in the JSON report.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/results/live_groq_stt_benchmark.json")
    )
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0")
    reference = args.reference
    if args.reference_file is not None:
        reference = args.reference_file.read_text(encoding="utf-8")
    settings = Settings(_env_file=os.environ.get("RETAIL_EVAL_ENV_FILE", ".env"))
    if not settings.has_groq_key:
        raise SystemExit("GROQ_API_KEY is not configured in the project .env")
    report = asyncio.run(
        run(
            settings,
            args.audio,
            reference,
            args.output,
            args.trials,
            args.language,
            args.source,
            not args.omit_transcription,
            args.timeout_seconds,
        )
    )
    print(f"Saved {args.output}; complete={report['complete']}")


if __name__ == "__main__":
    main()
