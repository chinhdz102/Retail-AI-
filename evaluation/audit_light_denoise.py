"""Research-only paired ASR test of bounded DeepFilterNet noise suppression.

Uses the official Windows binary with delay compensation and no post-filter.
Never changes demo configuration or edits reference labels/transcripts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import subprocess
import time
import wave
from pathlib import Path

import av
import numpy as np

from backend.services.azure_stt import AzureVietnameseSTT
from backend.services.speech_activity import _decode_audio
from backend.services.whisper_stt import _wav
from evaluation.vietnamese_tone_metrics import score


def resample(samples, source_rate, target_rate):
    frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
    frame.sample_rate = source_rate
    sampler = av.AudioResampler(format="fltp", layout="mono", rate=target_rate)
    frames = sampler.resample(frame) + sampler.resample(None)
    return np.concatenate([f.to_ndarray().reshape(-1) for f in frames])


def wav48(samples):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        writer.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


def prepare(args):
    folder = args.folder.resolve()
    source_dir, output_dir = folder / "input48", folder / "filtered48"
    source_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    cases = []
    for manifest in args.manifests:
        for item in json.loads(manifest.read_text(encoding="utf8"))["cases"]:
            path = Path(item["file"])
            path = path if path.is_absolute() else manifest.parent / path
            content = path.read_bytes()
            actual_hash = hashlib.sha256(content).hexdigest()
            if item.get("sha256") and actual_hash != item["sha256"]:
                raise ValueError("Input hash mismatch")
            samples = _decode_audio(content, time.perf_counter() + 10)
            file48 = source_dir / (item["id"] + ".wav")
            # Native 0.5.6 -D removes the lookahead from the front but does not
            # flush it at EOF. Pad before filtering, then remove ONLY that added
            # silence; otherwise the experiment itself loses the final 30 ms.
            padded = np.pad(samples, (0, 3200))
            file48.write_bytes(wav48(resample(padded, 16000, 48000)))
            cases.append({**item, "file": str(path.resolve()), "sha256": actual_hash,
                          "source_samples": len(samples), "input48": str(file48)})
    start = time.perf_counter()
    for item in cases:
        # Native's file loop reuses recurrent state. Start a separate instance
        # for each independent recording to avoid a previous file's context.
        command = [str(args.binary.resolve()), "-D", "-a", str(args.limit_db),
                   "-o", str(output_dir), item["input48"]]
        process = subprocess.run(command, capture_output=True, timeout=60)
        (folder / (item["id"] + "-native-run.txt")).write_bytes(process.stdout + process.stderr)
        if process.returncode:
            raise RuntimeError("Denoiser failed; inspect local native-run.txt")
        filtered_path = output_dir / Path(item["input48"]).name
        if not filtered_path.is_file():
            raise ValueError("Missing denoiser output")
        # Decode/resample the compensated output to the ASR's 16 kHz.
        filtered = _decode_audio(filtered_path.read_bytes(), time.perf_counter() + 10)
        if len(filtered) < item["source_samples"]:
            raise ValueError(f"Denoiser lost original samples: {item['id']}")
        item["compensated_tail_padding_samples"] = len(filtered) - item["source_samples"]
        filtered = filtered[:item["source_samples"]]
        output = folder / (item["id"] + "-light.wav")
        output.write_bytes(_wav(filtered))
        item["filtered_file"] = str(output)
        item["filtered_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        item["filtered_samples"] = len(filtered)
    report = {"production_switch": False, "physical_microphone": False,
              "method": "DeepFilterNet native 0.5.6, delay compensation, no postfilter",
              "independent_model_state": True, "tail_padding_ms": 200,
              "attenuation_limit_db": args.limit_db,
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "preparation_seconds": round(time.perf_counter() - start, 3), "cases": cases}
    args.prepared.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"prepared": len(cases), "seconds": report["preparation_seconds"]}))


async def run(args):
    report = json.loads(args.prepared.read_text(encoding="utf8"))
    report.update(rows=[], complete=False, kind="paired-real-azure-production-acoustic-checks")
    service = AzureVietnameseSTT()
    for item in report["cases"]:
        for variant in ("raw", "light"):
            path = Path(item["file"] if variant == "raw" else item["filtered_file"])
            content = path.read_bytes()
            expected = item["sha256"] if variant == "raw" else item["filtered_sha256"]
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Prepared input hash mismatch")
            row = {"id": item["id"], "kind": item["kind"], "reference": item["reference"],
                   "variant": variant, "sha256": expected, "samples": item["source_samples"]}
            start = time.perf_counter()
            try:
                result = await service.transcribe(content)
                row.update(text=result.text, accepted=result.speech_detected,
                           rejection_reason=result.rejection_reason, quality=result.quality,
                           scores=score(item["reference"], result.text))
            except Exception as error:
                row["error_type"] = type(error).__name__
            row["seconds"] = round(time.perf_counter() - start, 3)
            report["rows"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
            print(json.dumps({k: row.get(k) for k in ["id", "variant", "text", "error_type"]},
                             ensure_ascii=False), flush=True)
    report["complete"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run"])
    parser.add_argument("--manifests", type=Path, nargs="*")
    parser.add_argument("--binary", type=Path, default=Path(".tmp/deepfilter-research-20261111/deep-filter.exe"))
    parser.add_argument("--folder", type=Path, default=Path(".tmp/deepfilter-research-20261111"))
    parser.add_argument("--limit-db", type=float, default=6)
    parser.add_argument("--prepared", type=Path, default=Path(".tmp/deepfilter-research-20261111/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/reports/artifacts/stt_light_denoise_isolated_20261111.json"))
    args = parser.parse_args()
    prepare(args) if args.mode == "prepare" else asyncio.run(run(args))
