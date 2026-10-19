"""Measure live Azure Speech synthesis and first-byte streaming latency."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

from backend.config import Settings
from backend.services.azure_speech import AzureSpeechService


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    return round(median(values))


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    synth = [
        row["synthesize"]["latency_ms"]
        for row in results
        if row["synthesize"]["status"] == "success"
    ]
    first_chunk = [
        row["stream"]["first_chunk_ms"]
        for row in results
        if row["stream"]["status"] == "success" and row["stream"]["first_chunk_ms"] is not None
    ]
    stream_total = [
        row["stream"]["total_ms"] for row in results if row["stream"]["status"] == "success"
    ]
    return {
        "synthesize_successes": len(synth),
        "synthesize_median_ms": _median(synth),
        "synthesize_p95_ms": _percentile(synth, 0.95),
        "stream_successes": len(stream_total),
        "stream_first_chunk_median_ms": _median(first_chunk),
        "stream_first_chunk_p95_ms": _percentile(first_chunk, 0.95),
        "stream_total_median_ms": _median(stream_total),
        "stream_total_p95_ms": _percentile(stream_total, 0.95),
    }


async def run(
    settings: Settings,
    output: Path,
    text: str,
    language: str,
    trials: int,
    service: Any | None = None,
) -> dict[str, Any]:
    speech = service or AzureSpeechService(settings)
    report: dict[str, Any] = {
        "kind": "live_azure_tts_benchmark",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "language": language,
        "text_saved": False,
        "text_characters": len(text),
        "trial_count": trials,
        "results": [],
        "complete": False,
    }

    for trial in range(1, trials + 1):
        result: dict[str, Any] = {"trial": trial, "synthesize": {}, "stream": {}}
        started = perf_counter()
        try:
            audio = await speech.synthesize(text, language)
            result["voice_id"] = audio.voice_id
            result["synthesize"] = {
                "status": "success",
                "latency_ms": round((perf_counter() - started) * 1000),
                "audio_bytes": len(audio.content),
                "content_type": audio.content_type,
            }
        except Exception as exc:
            result["synthesize"] = {"status": "error", "error_type": type(exc).__name__}

        started = perf_counter()
        stream = None
        try:
            stream = await speech.open_stream(text, language)
            first_chunk_ms: int | None = None
            audio_bytes = 0
            async for chunk in stream.iter_bytes():
                if chunk:
                    if first_chunk_ms is None:
                        first_chunk_ms = round((perf_counter() - started) * 1000)
                    audio_bytes += len(chunk)
            result["voice_id"] = stream.voice_id
            result["stream"] = {
                "status": "success",
                "first_chunk_ms": first_chunk_ms,
                "total_ms": round((perf_counter() - started) * 1000),
                "audio_bytes": audio_bytes,
                "content_type": stream.content_type,
            }
        except Exception as exc:
            result["stream"] = {"status": "error", "error_type": type(exc).__name__}
        finally:
            if stream is not None:
                await stream.aclose()
        report["results"].append(result)
        print(
            f"TTS trial={trial} synth={result['synthesize']['status']} "
            f"stream={result['stream']['status']}",
            flush=True,
        )

    report["summary"] = _summary(report["results"])
    report["complete"] = all(
        row["synthesize"]["status"] == "success" and row["stream"]["status"] == "success"
        for row in report["results"]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    client = getattr(speech, "client", None)
    if client is not None:
        await client.aclose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="Chào bạn, cửa hàng có thể giúp gì cho bạn hôm nay?")
    parser.add_argument("--language", choices=("vi", "en", "zh"), default="vi")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")

    settings = Settings(_env_file=os.environ.get("RETAIL_EVAL_ENV_FILE", ".env"))
    if not settings.has_azure_speech:
        raise SystemExit("AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are not configured")
    output = args.output or Path(
        "evaluation/results/live_azure_tts_benchmark_"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report = asyncio.run(run(settings, output, args.text, args.language, args.trials))
    print(f"Saved {output}; complete={report['complete']}")


if __name__ == "__main__":
    main()
