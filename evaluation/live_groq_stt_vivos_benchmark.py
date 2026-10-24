"""Evaluate the kiosk's live transcription endpoint with held-out VIVOS speech.

The script fetches one labeled utterance per test speaker from Hugging Face's
dataset viewer, sends it through the real `/audio/transcribe` route, and saves
metrics without persisting the audio or signed download URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import httpx

from evaluation.live_groq_stt_benchmark import accuracy

DATASET = "lengocquangLAB/vivos"
DATASET_ROWS_URL = "https://datasets-server.huggingface.co/rows"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_OUTPUT = Path("evaluation/results/live_groq_stt_vivos_fullpath_20261021.json")
PAGE_SIZE = 100
EXPECTED_TEST_ROWS = 760
SELECTION_SEED = 20261021


def _speaker_from_path(path: str) -> str | None:
    match = re.search(r"/(VIVOSDEV\d{2})/", path, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def select_one_per_speaker(
    rows: list[dict[str, Any]], seed: int = SELECTION_SEED
) -> list[dict[str, Any]]:
    """Choose one longer, distinct test utterance per VIVOS speaker."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        speaker = _speaker_from_path(str(row.get("path", "")))
        text = str(row.get("text", "")).strip()
        audio = row.get("audio")
        if isinstance(audio, list):
            audio = audio[0] if audio else None
        if speaker and len(text) >= 30 and isinstance(audio, dict) and audio.get("src"):
            groups[speaker].append({**row, "_speaker": speaker, "_audio": audio})

    rng = random.Random(seed)
    selected = [rng.choice(groups[speaker]) for speaker in sorted(groups)]
    return selected


async def _fetch_rows(client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], int]:
    async def fetch_page(offset: int) -> dict[str, Any]:
        response = await client.get(
            DATASET_ROWS_URL,
            params={
                "dataset": DATASET,
                "config": "default",
                "split": "test",
                "offset": offset,
                "length": PAGE_SIZE,
            },
        )
        response.raise_for_status()
        return response.json()

    pages = await asyncio.gather(
        *(fetch_page(offset) for offset in range(0, EXPECTED_TEST_ROWS, PAGE_SIZE))
    )
    rows = [item["row"] for page in pages for item in page.get("rows", [])]
    return rows, len(rows)


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return ordered[index]


async def run(api_base_url: str, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        rows, fetched_rows = await _fetch_rows(client)
        samples = select_one_per_speaker(rows)
        if not samples:
            raise RuntimeError("The VIVOS test split returned no selectable speaker samples")

        session_response = await client.post(
            f"{api_base_url.rstrip('/')}/sessions",
            json={"kiosk_id": "stt-benchmark-vivos"},
        )
        session_response.raise_for_status()
        session_id = session_response.json()["session_id"]
        report: dict[str, Any] = {
            "kind": "live_groq_stt_vivos_fullpath_benchmark",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "endpoint": f"POST {api_base_url.rstrip('/')}/audio/transcribe",
            "dataset": DATASET,
            "dataset_source": "https://huggingface.co/datasets/lengocquangLAB/vivos",
            "dataset_original_source": "https://zenodo.org/records/7068130",
            "split": "test",
            "license_note": (
                "VIVOS mirror metadata lists CC BY-NC-SA 4.0; evaluation-only, "
                "audio not redistributed."
            ),
            "expected_test_rows": EXPECTED_TEST_ROWS,
            "fetched_test_rows": fetched_rows,
            "selection_seed": SELECTION_SEED,
            "selection": (
                "one randomly selected reference of at least 30 characters per distinct "
                "VIVOSDEV speaker; stable speaker order"
            ),
            "sample_count": len(samples),
            "limitations": [
                "VIVOS is read speech recorded in a quiet setting, not spontaneous retail "
                "conversation or microphone-room noise.",
                "One utterance per speaker is a diagnostic sample, not a production WER estimate.",
                "The kiosk endpoint includes its catalog-aware transcript correction stage.",
            ],
            "results": [],
            "complete": False,
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            for index, sample in enumerate(samples, start=1):
                audio = sample["_audio"]
                download = await client.get(audio["src"])
                download.raise_for_status()
                audio_bytes = download.content
                started = perf_counter()
                try:
                    response = await client.post(
                        f"{api_base_url.rstrip('/')}/audio/transcribe",
                        data={"session_id": session_id, "language": "vi"},
                        files={
                            "file": (
                                Path(str(sample.get("path", "vivos.wav"))).name,
                                audio_bytes,
                                "audio/wav",
                            )
                        },
                    )
                    latency_ms = round((perf_counter() - started) * 1000)
                    response.raise_for_status()
                    body = response.json()
                    text = str(body.get("text", ""))
                    status = (
                        "success"
                        if body.get("speech_detected", bool(text.strip()))
                        else "rejected"
                    )
                    scores = accuracy(str(sample["text"]), text)
                    result: dict[str, Any] = {
                        "speaker": sample["_speaker"],
                        "file": Path(str(sample.get("path", "vivos.wav"))).name,
                        "reference": str(sample["text"]),
                        "transcription": text,
                        "status": status,
                        "latency_ms": latency_ms,
                        "model": body.get("model"),
                        "quality": body.get("quality", {}),
                        **scores,
                    }
                except Exception as error:
                    result = {
                        "speaker": sample["_speaker"],
                        "file": Path(str(sample.get("path", "vivos.wav"))).name,
                        "reference": str(sample["text"]),
                        "status": "error",
                        "latency_ms": round((perf_counter() - started) * 1000),
                        "error_type": type(error).__name__,
                    }
                result["audio_bytes"] = len(audio_bytes)
                report["results"].append(result)
                report["complete"] = len(report["results"]) == len(samples)
                output_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{index}/{len(samples)} speaker={sample['_speaker']} "
                    f"status={result['status']} "
                    f"latency_ms={result['latency_ms']} wer={result.get('wer')}",
                    flush=True,
                )
        finally:
            await client.post(f"{api_base_url.rstrip('/')}/sessions/{session_id}/close")

    scored = [item for item in report["results"] if item["status"] in {"success", "rejected"}]
    latencies = [item["latency_ms"] for item in scored]
    total_word_errors = sum(int(item["word_errors"]) for item in scored)
    total_reference_words = sum(int(item["reference_word_count"]) for item in scored)
    total_char_errors = sum(int(item["character_errors"]) for item in scored)
    total_reference_chars = sum(int(item["reference_character_count"]) for item in scored)
    report["summary"] = {
        "sample_count": len(report["results"]),
        "accepted": sum(item["status"] == "success" for item in report["results"]),
        "rejected": sum(item["status"] == "rejected" for item in report["results"]),
        "errors": sum(item["status"] == "error" for item in report["results"]),
        "exact_matches": sum(item.get("wer") == 0 for item in scored),
        "aggregate_wer": total_word_errors / max(1, total_reference_words),
        "aggregate_cer": total_char_errors / max(1, total_reference_chars),
        "mean_utterance_wer": sum(float(item["wer"]) for item in scored) / max(1, len(scored)),
        "mean_utterance_cer": sum(float(item["cer"]) for item in scored) / max(1, len(scored)),
        "median_latency_ms": round(median(latencies)) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 95),
        "models_used": sorted({str(item["model"]) for item in scored if item.get("model")}),
        "speaker_count": len({item["speaker"] for item in report["results"]}),
    }
    report["complete"] = (
        len(report["results"]) == len(samples)
        and report["summary"]["errors"] == 0
        and report["summary"]["speaker_count"] == len(samples)
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "summary": report["summary"],
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(run(args.api_base_url, args.output))


if __name__ == "__main__":
    main()
