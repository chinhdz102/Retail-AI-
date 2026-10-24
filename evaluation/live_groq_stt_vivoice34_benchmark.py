"""Run Vietnamese Whisper endpoint checks on province-labeled ViVoice-34 audio.

The dataset viewer is queried for metadata and signed audio URLs. URLs are kept
in memory only; selected public WAV samples and a compact result report are
saved locally for reproducible, licensed evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import wave
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import httpx

from evaluation.live_groq_stt_benchmark import accuracy

DATASET = "anonymous-vivoice34/ViVoice34"
DATASET_URL = "https://huggingface.co/datasets/anonymous-vivoice34/ViVoice34"
DATASET_ROWS_URL = "https://datasets-server.huggingface.co/rows"
LICENSE = "CC BY 4.0"
PAGE_SIZE = 100
TARGET_PROVINCES = (
    "Hải Phòng",
    "Quảng Ninh",
    "Nghệ An",
    "Thừa Thiên Huế",
    "Quảng Trị",
    "Gia Lai",
    "Tiền Giang",
)
DEFAULT_API_BASE_URL = "http://127.0.0.1:8002"
DEFAULT_OUTPUT = Path("evaluation/results/live_groq_stt_vivoice34_20261021.json")
DEFAULT_DOWNLOAD_DIR = Path(".tmp/vivoice34_samples")


def _as_audio(row: dict[str, Any]) -> dict[str, Any] | None:
    audio = row.get("audio")
    if isinstance(audio, list):
        audio = audio[0] if audio else None
    return audio if isinstance(audio, dict) and audio.get("src") else None


def select_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select one stable, labeled sample per target province and unique speaker."""
    selected: list[dict[str, Any]] = []
    used_speakers: set[str] = set()
    for province in TARGET_PROVINCES:
        candidates = sorted(
            (
                row
                for row in rows
                if str(row.get("province", "")).strip() == province
                and len(str(row.get("transcript", "")).strip()) >= 40
                and _as_audio(row) is not None
                and 15 <= float(row.get("duration_s", 0)) <= 120
            ),
            key=lambda row: int(row.get("row_idx", 0)),
        )
        candidate = next(
            (row for row in candidates if str(row.get("speaker_id", "")) not in used_speakers),
            None,
        )
        if candidate is None:
            continue
        speaker = str(candidate.get("speaker_id", ""))
        if speaker:
            used_speakers.add(speaker)
        selected.append({**candidate, "_audio": _as_audio(candidate)})
    return selected


async def _fetch_rows(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    async def fetch_page(offset: int, length: int) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.get(
                    DATASET_ROWS_URL,
                    params={
                        "dataset": DATASET,
                        "config": "default",
                        "split": "train",
                        "offset": offset,
                        "length": length,
                    },
                )
                if response.status_code >= 500 or response.status_code == 429:
                    last_error = httpx.HTTPStatusError(
                        f"Temporary dataset-server response {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    if attempt < 2:
                        await asyncio.sleep(1 + attempt * 2)
                        continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(1 + attempt * 2)
                    continue
        if last_error:
            raise last_error
        raise RuntimeError(f"Dataset-server returned no response for offset={offset}")

    first_page = await fetch_page(0, PAGE_SIZE)
    total = int(first_page.get("num_rows_total", 0))
    offsets = range(PAGE_SIZE, total, PAGE_SIZE)
    pages = await asyncio.gather(*(fetch_page(offset, PAGE_SIZE) for offset in offsets))
    rows = [
        {**item["row"], "row_idx": item.get("row_idx")}
        for item in first_page.get("rows", [])
    ]
    for page in pages:
        rows.extend(
            {**item["row"], "row_idx": item.get("row_idx")}
            for item in page.get("rows", [])
        )
    return rows


def _safe_slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return normalized or "sample"


def _wav_duration(audio: bytes) -> float | None:
    try:
        import io

        with wave.open(io.BytesIO(audio), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return ordered[index]


async def run(
    api_base_url: str,
    output_path: Path,
    download_dir: Path,
    reuse_report: Path | None = None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(90.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        source_report: dict[str, Any] | None = None
        if reuse_report is None:
            rows = await _fetch_rows(client)
            samples = select_samples(rows)
            rows_available = len(rows)
        else:
            source_report = json.loads(reuse_report.read_text(encoding="utf-8"))
            samples = source_report.get("results") or []
            rows_available = int(source_report.get("rows_available", len(samples)))
        if len(samples) < 4:
            raise RuntimeError(f"Expected at least 4 province samples; found {len(samples)}")

        base_url = api_base_url.rstrip("/")
        session_response = await client.post(
            f"{base_url}/sessions", json={"kiosk_id": "stt-benchmark-vivoice34"}
        )
        session_response.raise_for_status()
        session_id = session_response.json()["session_id"]
        report: dict[str, Any] = {
            "kind": "live_groq_stt_vivoice34_province_benchmark",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "endpoint": f"POST {base_url}/audio/transcribe",
            "dataset": DATASET,
            "dataset_source": DATASET_URL,
            "split": "train preview subset",
            "license": LICENSE,
            "rows_available": rows_available,
            "selection": (
                "Reused the labeled, province-balanced audio selection from the source report."
                if source_report is not None
                else "First row by stable row_idx for each target province with a transcript of "
                "at least 40 characters, duration 15–120 seconds, and a distinct speaker ID."
            ),
            "provinces_requested": list(TARGET_PROVINCES),
            "sample_count": len(samples),
            "audio_saved_locally": True,
            "limitations": [
                "Samples are long read passages, not spontaneous retail conversations.",
                "Province metadata is not a verified accent label; use the results as a regional "
                "diagnostic only.",
                "The endpoint includes catalog-aware transcript correction; a single phrase "
                "mismatch "
                "is not a measure of semantic understanding.",
            ],
            "results": [],
            "complete": False,
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        audio_paths: list[Path] = []
        try:
            for index, sample in enumerate(samples, start=1):
                row_idx = int(sample.get("row_idx", index - 1))
                province = str(sample.get("province", "Unknown"))
                filename = str(
                    sample.get("file")
                    or f"ViVoice34_{row_idx:03d}_{_safe_slug(province)}.wav"
                )
                audio_path = download_dir / filename
                if reuse_report is None:
                    audio_info = sample["_audio"]
                    download_started = perf_counter()
                    downloaded = await client.get(audio_info["src"])
                    downloaded.raise_for_status()
                    audio_bytes = downloaded.content
                    download_ms = round((perf_counter() - download_started) * 1000)
                    audio_path.write_bytes(audio_bytes)
                else:
                    audio_bytes = audio_path.read_bytes()
                    download_ms = 0
                audio_paths.append(audio_path)

                started = perf_counter()
                try:
                    response = await client.post(
                        f"{base_url}/audio/transcribe",
                        data={"session_id": session_id, "language": "vi"},
                        files={"file": (filename, audio_bytes, "audio/wav")},
                    )
                    latency_ms = round((perf_counter() - started) * 1000)
                    response.raise_for_status()
                    body = response.json()
                    text = str(body.get("text", ""))
                    accepted = bool(body.get("speech_detected", bool(text.strip())))
                    result: dict[str, Any] = {
                        "row_idx": row_idx,
                        "province": province,
                        "province_code": sample.get("province_code"),
                        "speaker_id": sample.get("speaker_id"),
                        "source_file_name": sample.get("file_name"),
                        "file": filename,
                        "audio_bytes": len(audio_bytes),
                        "audio_duration_seconds": _wav_duration(audio_bytes)
                        or float(sample.get("duration_s", 0)),
                        "download_ms": download_ms,
                        "reference": str(sample.get("transcript") or sample["reference"]),
                        "transcription": text,
                        "speech_detected": accepted,
                        "latency_ms": latency_ms,
                        "model": body.get("model"),
                        "quality": body.get("quality", {}),
                        "status": "success" if accepted else "rejected",
                        **accuracy(str(sample.get("transcript") or sample["reference"]), text),
                    }
                except Exception as error:
                    result = {
                        "row_idx": row_idx,
                        "province": province,
                        "source_file_name": sample.get("file_name"),
                        "file": filename,
                        "audio_bytes": len(audio_bytes),
                        "download_ms": download_ms,
                        "reference": str(sample.get("transcript") or sample["reference"]),
                        "status": "error",
                        "latency_ms": round((perf_counter() - started) * 1000),
                        "error_type": type(error).__name__,
                    }
                report["results"].append(result)
                report["complete"] = len(report["results"]) == len(samples)
                output_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{index}/{len(samples)} sample={row_idx} status={result['status']} "
                    f"latency_ms={result['latency_ms']} wer={result.get('wer')}",
                    flush=True,
                )
        finally:
            await client.post(f"{base_url}/sessions/{session_id}/close")

    scored = [item for item in report["results"] if item["status"] in {"success", "rejected"}]
    latencies = [int(item["latency_ms"]) for item in scored]
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
        "median_latency_ms": round(median(latencies)) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 95),
        "models_used": sorted({str(item["model"]) for item in scored if item.get("model")}),
        "province_count": len({str(item["province"]) for item in report["results"]}),
    }
    report["complete"] = (
        len(report["results"]) == len(samples)
        and report["summary"]["errors"] == 0
        and report["summary"]["province_count"] >= 4
    )
    archive = download_dir / "vivoice34_province_samples.zip"
    attribution = (
        "Audio samples and transcripts: ViVoice-34: Vietnamese Speech Dataset from 34 Provinces, "
        "https://huggingface.co/datasets/anonymous-vivoice34/ViVoice34, CC BY 4.0. "
        "Samples selected for STT evaluation; no endorsement implied."
    )
    (download_dir / "ATTRIBUTION.txt").write_text(attribution + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(download_dir / "ATTRIBUTION.txt", "ATTRIBUTION.txt")
        for audio_path in audio_paths:
            bundle.write(audio_path, audio_path.name)
    report["audio_bundle"] = str(archive)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--reuse-report", type=Path)
    args = parser.parse_args()
    report = asyncio.run(
        run(args.api_base_url, args.output, args.download_dir, reuse_report=args.reuse_report)
    )
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "summary": report.get("summary"),
                "audio_bundle": report.get("audio_bundle"),
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
