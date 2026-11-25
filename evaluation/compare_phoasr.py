"""Pinned, local PhoASR diagnostic against the previously captured same audio.

Research only: downloads data/config/safetensors from the publisher, never remote
Python. No backend setting, provider credential or reference prompt is modified.
The CPU int8 conversion is a separate configuration, not the paper's exact setup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.services.speech_activity import (
    _decode_audio,
    analyze_speech_activity,
)
from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Qualcomm-AI-Research/PhoASR-whisper-small"
REVISION = "da2d9ae0ac0a6a6faba20add61c2e980ccd85037"


def run(args):
    if args.output.exists():
        raise ValueError("Choose a new output to preserve prior evidence")
    from ctranslate2.converters import TransformersConverter
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    cache = ROOT / ".tmp/phoasr_20261106"
    cache.mkdir(parents=True, exist_ok=True)
    print("Preparing pinned publisher weights and CPU int8 conversion", flush=True)
    snapshot = snapshot_download(
        MODEL_ID, revision=REVISION, cache_dir=str(cache / "publisher"), token=False,
        allow_patterns=["*.json", "*.txt", "*.safetensors", "README.md"],
    )
    converted = cache / "ct2-int8"
    if not (converted / "model.bin").is_file():
        TransformersConverter(
            snapshot, copy_files=["tokenizer.json", "preprocessor_config.json"],
        ).convert(str(converted), quantization="int8")
    model = WhisperModel(str(converted), device="cpu", compute_type="int8",
                         cpu_threads=4, num_workers=1, local_files_only=True)
    source = ROOT / "docs/reports/artifacts/vietnamese_asr_paired_20261105.json"
    baseline = json.loads(source.read_text(encoding="utf-8"))
    report = {
        "created_at": datetime.now(UTC).isoformat(), "complete": False,
        "source": source.relative_to(ROOT).as_posix(),
        "model": MODEL_ID, "revision": REVISION,
        "license": "BSD-3-Clause-Clear + Qualcomm Responsible AI; research/education",
        "conversion": "CTranslate2 int8, CPU, four threads, beam5, temperature0, no timestamps",
        "selection": "All33 previously scheduled files, in the original order.",
        "limitations": [
            "Exposed same-file diagnostic; not held out or a physical microphone test.",
            "Public corpora may overlap training; only18 human files are within60s.",
            "Hosted Whisper internal decoding differs; no equal beam or hardware claim.",
            "Whitespace WER normalization is imperfect, especially spoken numbers/brands.",
            "CPU timings include decode and acoustic gate; contention may affect timings.",
        ],
        "weights_sha256": hashlib.sha256((converted / "model.bin").read_bytes()).hexdigest(),
        "reference_used_as_prompt": False, "rows": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")

    save()
    for sample in baseline["rows"]:
        content = (ROOT / sample["path"]).read_bytes()
        row = {key: sample[key] for key in ("id", "reference", "path", "sha256")}
        row["kind"] = sample.get("kind", sample["id"].split(":", 1)[0])
        row["baseline_variants"] = {
            name: {k: v for k, v in value.items() if k in (
                "text", "model", "wer", "cer", "word_errors", "reference_word_count",
                "error_type", "speech_detected", "seconds",
            )}
            for name, value in sample["variants"].items() if name != "compact"
        }
        started = perf_counter()
        try:
            if row["sha256"] != hashlib.sha256(content).hexdigest():
                raise ValueError("Input changed since the paired baseline")
            activity = analyze_speech_activity(content)
            if activity.speech_detected:
                audio = _decode_audio(content, perf_counter() + 5)
                segments, _ = model.transcribe(
                    audio, language="vi", task="transcribe", beam_size=5,
                    temperature=0, vad_filter=False, condition_on_previous_text=False,
                    initial_prompt=None, no_speech_threshold=0.5,
                    log_prob_threshold=-0.75, compression_ratio_threshold=2.4,
                    without_timestamps=True,
                )
                text = " ".join(segment.text.strip() for segment in segments).strip()
            else:
                text = ""
            row["phoasr"] = {"text": text, "speech_detected": bool(text)}
            if sample["reference"]:
                row["phoasr"].update(accuracy(sample["reference"], text))
        except Exception as exc:
            row["phoasr"] = {"error_type": type(exc).__name__}
        row["phoasr"]["seconds"] = round(perf_counter() - started, 3)
        report["rows"].append(row)
        save()
        print(json.dumps({"id": row["id"], **row["phoasr"]}, ensure_ascii=True), flush=True)
    report["complete"] = True
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
