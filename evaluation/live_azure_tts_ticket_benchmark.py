"""Measure the actual kiosk ticket-to-stream TTS path without saving response text."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any
from urllib.parse import urljoin

import httpx

DEFAULT_API_BASE_URL = "http://127.0.0.1:8002"
DEFAULT_OUTPUT = Path("evaluation/results/live_azure_tts_ticket_demo_recheck_20261021.json")
SAMPLE_TEXT = (
    "Chào bạn, cửa hàng có Coca Zero không? Mình sẽ kiểm tra giá và vị trí trên kệ giúp bạn nhé."
)


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return ordered[index]


async def run(
    api_base_url: str,
    output_path: Path,
    trials: int = 3,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(90.0, connect=15.0)
    report: dict[str, Any] = {
        "kind": "live_api_tts_ticket_stream_benchmark",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "endpoint": urljoin(api_base_url.rstrip("/") + "/", "audio/synthesize/tickets"),
        "text_saved": False,
        "text_characters": len(SAMPLE_TEXT),
        "trial_count": trials,
        "results": [],
        "prewarm": None,
        "complete": False,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        health = await client.get(urljoin(api_base_url.rstrip("/") + "/", "health"))
        health.raise_for_status()
        session_response = await client.post(
            urljoin(api_base_url.rstrip("/") + "/", "sessions"),
            json={"kiosk_id": "tts-ticket-benchmark"},
        )
        session_response.raise_for_status()
        session_id = session_response.json()["session_id"]
        try:
            for trial in range(1, trials + 1):
                result: dict[str, Any] = {"trial": trial}
                if trial == 2:
                    prewarm_started = perf_counter()
                    try:
                        prewarm = await client.post(
                            urljoin(api_base_url.rstrip("/") + "/", "audio/synthesize/prewarm"),
                            json={"session_id": session_id, "language": "vi"},
                        )
                        prewarm.raise_for_status()
                        report["prewarm"] = {
                            **prewarm.json(),
                            "latency_ms": round((perf_counter() - prewarm_started) * 1000),
                            "status": prewarm.status_code,
                        }
                    except Exception as error:
                        report["prewarm"] = {
                            "status": "error",
                            "error_type": type(error).__name__,
                        }

                ticket_started = perf_counter()
                try:
                    ticket = await client.post(
                        urljoin(api_base_url.rstrip("/") + "/", "audio/synthesize/tickets"),
                        json={
                            "session_id": session_id,
                            "text": SAMPLE_TEXT,
                            "language": "vi",
                        },
                    )
                    ticket.raise_for_status()
                    result["ticket_ms"] = round((perf_counter() - ticket_started) * 1000)
                    stream_url = urljoin(api_base_url.rstrip("/") + "/", ticket.json()["audio_url"])
                    stream_started = perf_counter()
                    async with client.stream("GET", stream_url) as response:
                        response.raise_for_status()
                        first_chunk_ms: int | None = None
                        audio_bytes = 0
                        chunk_count = 0
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            if first_chunk_ms is None:
                                first_chunk_ms = round((perf_counter() - stream_started) * 1000)
                            audio_bytes += len(chunk)
                            chunk_count += 1
                        result.update(
                            {
                                "status": "success",
                                "http_status": response.status_code,
                                "first_audio_byte_ms": first_chunk_ms,
                                "stream_complete_ms": round(
                                    (perf_counter() - stream_started) * 1000
                                ),
                                "audio_bytes": audio_bytes,
                                "chunk_count": chunk_count,
                                "content_type": response.headers.get("content-type"),
                                "provider": response.headers.get("x-tts-provider"),
                                "voice_id": response.headers.get("x-voice-id"),
                            }
                        )
                except Exception as error:
                    result.update(
                        {
                            "status": "error",
                            "error_type": type(error).__name__,
                            "latency_ms": round((perf_counter() - ticket_started) * 1000),
                        }
                    )
                report["results"].append(result)
                output_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        finally:
            await client.post(
                urljoin(api_base_url.rstrip("/") + "/", f"sessions/{session_id}/close")
            )

    successful = [row for row in report["results"] if row.get("status") == "success"]
    first_chunks = [
        int(row["first_audio_byte_ms"])
        for row in successful
        if row.get("first_audio_byte_ms") is not None
    ]
    totals = [int(row["stream_complete_ms"]) for row in successful]
    report["summary"] = {
        "successes": len(successful),
        "errors": trials - len(successful),
        "first_audio_byte_median_ms": round(median(first_chunks)) if first_chunks else None,
        "first_audio_byte_p95_ms": _percentile(first_chunks, 95),
        "stream_complete_median_ms": round(median(totals)) if totals else None,
        "stream_complete_p95_ms": _percentile(totals, 95),
        "providers": sorted({str(row["provider"]) for row in successful if row.get("provider")}),
        "voices": sorted({str(row["voice_id"]) for row in successful if row.get("voice_id")}),
    }
    report["complete"] = len(successful) == trials and all(
        row.get("audio_bytes", 0) > 0 and row.get("chunk_count", 0) > 0 for row in successful
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    report = asyncio.run(run(args.api_base_url, args.output, args.trials))
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "summary": report["summary"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
