"""Check that silence and the kiosk's reported repeated phrase stay out of the agent."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urljoin

import httpx

DEFAULT_API_BASE_URL = "http://127.0.0.1:8002"
DEFAULT_OUTPUT = Path("evaluation/results/live_stt_hallucination_guard_demo_recheck_20261021.json")
REPORTED_REPEAT = (
    "Cảm ơn các bạn đã theo dõi và ủng thông, và hạn thấy nhiều thông, "
    "và hạn thấy nhiều thông."
)


def _silence_wav(duration_seconds: int = 2, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * duration_seconds * sample_rate)
    return buffer.getvalue()


async def run(api_base_url: str, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(90.0, connect=15.0)
    report: dict[str, Any] = {
        "kind": "live_stt_hallucination_guard_recheck",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "endpoint": urljoin(api_base_url.rstrip("/") + "/", "audio/transcribe"),
        "reported_repeat_text": REPORTED_REPEAT,
        "cases": [],
        "complete": False,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        health = await client.get(urljoin(api_base_url.rstrip("/") + "/", "health"))
        health.raise_for_status()
        session_response = await client.post(
            urljoin(api_base_url.rstrip("/") + "/", "sessions"),
            json={"kiosk_id": "stt-hallucination-guard-benchmark"},
        )
        session_response.raise_for_status()
        session_id = session_response.json()["session_id"]
        try:
            silence = _silence_wav()
            report["cases"].append(
                await _transcribe(
                    client,
                    api_base_url,
                    session_id,
                    name="two_second_digital_silence",
                    audio=silence,
                    filename="silence.wav",
                    content_type="audio/wav",
                )
            )

            synthesis = await client.post(
                urljoin(api_base_url.rstrip("/") + "/", "audio/synthesize"),
                json={"session_id": session_id, "text": REPORTED_REPEAT, "language": "vi"},
            )
            synthesis.raise_for_status()
            report["repeat_audio"] = {
                "provider": synthesis.headers.get("x-tts-provider"),
                "voice_id": synthesis.headers.get("x-voice-id"),
                "audio_bytes": len(synthesis.content),
            }
            report["cases"].append(
                await _transcribe(
                    client,
                    api_base_url,
                    session_id,
                    name="reported_repeated_phrase_synthesized",
                    audio=synthesis.content,
                    filename="reported-repeat.mp3",
                    content_type=synthesis.headers.get("content-type", "audio/mpeg"),
                )
            )
        finally:
            await client.post(
                urljoin(api_base_url.rstrip("/") + "/", f"sessions/{session_id}/close")
            )

    report["complete"] = len(report["cases"]) == 2 and all(
        row["http_status"] == 200
        and row["speech_detected"] is False
        and not row["text"]
        for row in report["cases"]
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def _transcribe(
    client: httpx.AsyncClient,
    api_base_url: str,
    session_id: str,
    *,
    name: str,
    audio: bytes,
    filename: str,
    content_type: str,
) -> dict[str, Any]:
    started = perf_counter()
    response = await client.post(
        urljoin(api_base_url.rstrip("/") + "/", "audio/transcribe"),
        data={"session_id": session_id, "language": "vi"},
        files={"file": (filename, audio, content_type)},
    )
    body = response.json()
    return {
        "case": name,
        "http_status": response.status_code,
        "latency_ms": round((perf_counter() - started) * 1000),
        "audio_bytes": len(audio),
        "speech_detected": body.get("speech_detected"),
        "text": body.get("text", ""),
        "model": body.get("model"),
        "quality": body.get("quality", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = asyncio.run(run(args.api_base_url, args.output))
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "cases": [
                    {
                        key: row.get(key)
                        for key in ("case", "http_status", "latency_ms", "speech_detected", "text")
                    }
                    for row in report["cases"]
                ],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
