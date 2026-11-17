"""Research-only Qwen3-ASR comparison; pinned weights, vi, no context/rewrite.

Run from an isolated research environment. This never changes the demo provider.
Synthetic speech is a functional probe, not proof of microphone/room accuracy.
"""
import argparse
import hashlib
import io
import json
import time
from pathlib import Path

import av
import numpy as np
import torch
import transformers
from huggingface_hub import snapshot_download
from transformers import AutoModelForMultimodalLM, AutoProcessor

from evaluation.vietnamese_tone_metrics import score

MODEL = "Qwen/Qwen3-ASR-0.6B-hf"
REVISION = "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c"


def load_audio(path):
    chunks = []
    with av.open(io.BytesIO(path.read_bytes()), mode="r") as container:
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in container.decode(container.streams.audio[0]):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def persist(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf8")


def run(args):
    torch.set_num_threads(4)
    report = {"model": MODEL, "revision": REVISION, "license": "apache-2.0",
              "torch": torch.__version__, "transformers": transformers.__version__,
              "device": "cpu", "language": "vi", "context": None,
              "physical_microphone": False, "production_switch": False,
              "complete": False, "rows": []}
    persist(args.output, report)
    folder = snapshot_download(MODEL, revision=REVISION, local_dir=args.model_dir,
                               allow_patterns=["*.json", "*.jinja", "*.safetensors", "README.md"])
    print(json.dumps({"downloaded": MODEL, "revision": REVISION}), flush=True)
    processor = AutoProcessor.from_pretrained(folder, trust_remote_code=False)
    model = AutoModelForMultimodalLM.from_pretrained(
        folder, trust_remote_code=False, dtype=torch.float32, attn_implementation="sdpa",
    ).eval()
    for manifest_path in args.manifests:
        for item in json.loads(manifest_path.read_text(encoding="utf8"))["cases"]:
            path = Path(item["file"])
            path = path if path.is_absolute() else manifest_path.parent / path
            content = path.read_bytes()
            row = {**item, "sha256": hashlib.sha256(content).hexdigest()}
            start = time.perf_counter()
            try:
                if item.get("sha256") and row["sha256"] != item["sha256"]:
                    raise ValueError("Input hash mismatch")
                audio = load_audio(path)
                if not np.isfinite(audio).all() or len(audio) > 60 * 16000:
                    raise ValueError("Invalid research audio")
                inputs = processor.apply_transcription_request(
                    audio=audio, language="vi", processor_kwargs={"sampling_rate": 16000},
                ).to(model.device)
                with torch.inference_mode():
                    result = model.generate(**inputs, do_sample=False, max_new_tokens=384)
                generated = result[:, inputs["input_ids"].shape[1]:]
                text = processor.decode(generated, return_format="transcription_only")[0].strip()
                row.update(text=text, scores=score(item["reference"], text),
                           output_tokens=generated.shape[1],
                           token_limit_reached=generated.shape[1] >= 384)
            except Exception as error:
                row["error_type"] = type(error).__name__
            row["seconds"] = round(time.perf_counter() - start, 3)
            report["rows"].append(row)
            persist(args.output, report)
            print(json.dumps({k: row.get(k) for k in ["id", "text", "seconds", "error_type"]},
                             ensure_ascii=False), flush=True)
    report["complete"] = True
    persist(args.output, report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifests", type=Path, nargs="+", required=True)
    parser.add_argument("--model-dir", type=Path, default=Path(".tmp/qwen3-asr-research-20261111"))
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
