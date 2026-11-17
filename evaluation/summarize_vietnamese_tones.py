"""Summarize matched ASR comparisons and browser audio integrity without text repair."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import wave
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from evaluation.vietnamese_tone_metrics import score


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(rows):
    metrics = [score(row["reference"], row.get("text", "")) for row in rows]
    words = sum(item["reference_syllables"] for item in metrics)
    chars = sum(item["reference_characters"] for item in metrics)
    confusion = Counter()
    for item in metrics:
        confusion.update(item["tone_confusions"])
    return {
        "count": len(rows),
        "exact": sum(item["exact"] for item in metrics),
        "api_errors": sum(
            row.get("http_status", 200) != 200 or bool(row.get("error_type")) for row in rows
        ),
        "reference_syllables": words,
        "syllable_errors": sum(item["syllable_errors"] for item in metrics),
        "syllable_error_rate": sum(item["syllable_errors"] for item in metrics) / words
        if words
        else None,
        "character_error_rate": sum(item["character_errors"] for item in metrics) / chars
        if chars
        else None,
        "tone_only_substitutions": sum(item["tone_only_substitutions"] for item in metrics),
        "vowel_shape_substitutions": sum(item["vowel_shape_substitutions"] for item in metrics),
        "tone_confusions": dict(confusion),
        "nonempty": sum(bool(row.get("text", "").strip()) for row in rows),
        "latency_median_seconds": (
            float(np.median([row["seconds"] for row in rows if "seconds" in row]))
            if any("seconds" in row for row in rows)
            else None
        ),
        "latency_measurements": sum("seconds" in row for row in rows),
    }


def pcm(content: bytes):
    with wave.open(io.BytesIO(content)) as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
            raise ValueError("Expected 16 kHz mono PCM16")
        return (
            np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float) / 32768
        )


def capture_integrity(browser: dict):
    rows = []
    for row in browser["rows"]:
        input_audio = np.asarray(read(Path(row["framePath"])), dtype=float).reshape(-1)
        for audio in row["capture"]["audio"]:
            content = Path(audio["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != audio["sha256"]:
                raise ValueError("Captured WAV hash mismatch")
            captured = pcm(content)
            if len(captured) > len(input_audio) or not len(captured):
                raise ValueError("Capture length outside available input frames")
            size = 1 << (len(input_audio) + len(captured) - 1).bit_length()
            correlation = np.fft.irfft(
                np.fft.rfft(input_audio, size) * np.conj(np.fft.rfft(captured, size)),
                size,
            )
            offset = int(np.argmax(correlation[: len(input_audio) - len(captured) + 1]))
            expected = input_audio[offset : offset + len(captured)]
            denominator = np.linalg.norm(expected) * np.linalg.norm(captured)
            result = audio.get("result", {})
            rows.append(
                {
                    "id": row["id"],
                    "processing": row["processing"],
                    "context_rates": row["capture"]["contextRates"],
                    "input_settings": row["capture"]["inputSettings"],
                    "input_samples": len(input_audio),
                    "captured_samples": len(captured),
                    "offset_samples": offset,
                    "input_peak": float(np.max(np.abs(input_audio))),
                    "input_clipped_samples": int(np.sum(np.abs(expected) > 1)),
                    "input_clipped_fraction": float(np.mean(np.abs(expected) > 1)),
                    "input_energy_retained": float(
                        np.sum(expected**2) / max(1e-20, np.sum(input_audio**2))
                    ),
                    "cosine_similarity": float(
                        np.dot(expected, captured) / max(1e-20, denominator)
                    ),
                    "max_pcm_error": float(np.max(np.abs(expected - captured))),
                    "reference": row["reference"],
                    "text": result.get("text", ""),
                    "scores": score(row["reference"], result.get("text", "")),
                    "http_status": audio.get("httpStatus"),
                    "all_tracks_ended": audio["allTracksEnded"],
                }
            )
    return {"physical_microphone": False, "complete": browser.get("complete", False), "rows": rows}


def run(args):
    directory = args.directory
    source = read(args.manifest)
    expected = {row["id"]: row for row in source["cases"]}
    retries = read(directory / "vietnamese_tones_remote_recheck_20261110.json")["rows"]
    overrides = {(row["id"], row["variant"]): row for row in retries}
    groups, details, coverage = defaultdict(list), [], {}
    for name in ("remote", "pipeline", "azure", "phoasr_official"):
        document = read(directory / f"vietnamese_tones_{name}_20261110.json")
        variants = defaultdict(list)
        for original in document["rows"]:
            row = (
                overrides.get((original["id"], original.get("variant")), original)
                if name == "remote"
                else original
            )
            variant = row.get("variant", "running-pipeline")
            if row["sha256"] != expected[row["id"]]["sha256"]:
                raise ValueError("Audio hash differs between candidate runs")
            variants[variant].append(row["id"])
            groups[(variant, row["kind"])].append(row)
            metrics = score(row["reference"], row.get("text", ""))
            if not metrics["exact"]:
                details.append(
                    {
                        "variant": variant,
                        "id": row["id"],
                        "kind": row["kind"],
                        "reference": row["reference"],
                        "text": row.get("text", ""),
                        "scores": metrics,
                    }
                )
        for variant, ids in variants.items():
            coverage[variant] = {
                "expected": len(expected),
                "received": len(ids),
                "complete": len(ids) == len(expected) and set(ids) == set(expected),
            }
    if not all(item["complete"] for item in coverage.values()):
        raise ValueError("Incomplete matched comparison")
    summary = {
        "coverage": coverage,
        "groups": [
            {"variant": variant, "kind": kind, **summarize(rows)}
            for (variant, kind), rows in groups.items()
        ],
        "provider_rechecks": len(retries),
        "errors": details,
        "excluded": [
            "vietnamese_tones_phoasr_20261110.json: incomplete incompatible generic CT2 decode"
        ],
        "limitations": source["limitations"]
        + [
            "Fixed contextual vocabulary overlaps synthetic development phrases.",
            "FLEURS references may contain spelling inconsistencies; labels are unchanged.",
            "Azure TTS synthetic speech may favor Azure STT; human scores are separate.",
            "Tone-only alignment is a text diagnostic, not acoustic tone recognition accuracy.",
        ],
    }
    if args.browser:
        summary["browser_integrity"] = capture_integrity(read(args.browser))
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in summary["groups"]:
        print(json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
