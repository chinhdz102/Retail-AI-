"""Replay v5/v6 on the same cached raw speech/noise with production server framing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import onnxruntime as ort

from backend.services import speech_activity as vad
from evaluation.audit_noise_ablation import wav

ROOT = Path(__file__).resolve().parents[1]


def run():
    speech = json.loads((ROOT / ".tmp/noise_ablation_20261103/manifest.json").read_text("utf-8"))[
        "rows"
    ]
    controls = json.loads(
        (ROOT / ".tmp/noise_controls_20261103/worklet_manifest.json").read_text("utf-8")
    )["rows"]
    rows = []
    for version in ["v5", "v6"]:
        path = ROOT / f"apps/kiosk-airi/public/vad/silero_vad_{version}.onnx"
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        model = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        model_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(vad, "_model_session", return_value=model):
            for item in speech + controls:
                candidates = [("original", 1)]
                if item.get("condition") == "clean":
                    candidates += [("gain:0.02", 0.02), ("gain:0.05", 0.05)]
                original = np.fromfile(ROOT / item["pcm_path"], dtype="<f4")
                for transform, gain in candidates:
                    content = wav(original * gain)
                    result = vad.analyze_speech_activity(content)
                    rows.append(
                        {
                            "id": item["id"],
                            "transform": transform,
                            "version": version,
                            "expected_speech": bool(item.get("reference")),
                            "category": item.get("category"),
                            "speech_detected": result.speech_detected,
                            "quality": result.quality,
                            "audio_sha256": hashlib.sha256(content).hexdigest(),
                            "model_sha256": model_hash,
                        }
                    )
    output = ROOT / "docs/reports/artifacts/noise_silero_model_ablation_20261103.json"
    output.write_text(
        json.dumps(
            {
                "kind": "actual-onnx-server-pyav-silero",
                "limitations": [
                    "Exposed diagnostic, not a physical mic test.",
                    "ESC-50 category is not an independent speech annotation.",
                ],
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for version in ["v5", "v6"]:
        data = [r for r in rows if r["version"] == version]
        print(
            version,
            "speech misses:",
            [r["id"] for r in data if r["expected_speech"] and not r["speech_detected"]],
        )
        print(
            version,
            "controls activated:",
            [r["id"] for r in data if not r["expected_speech"] and r["speech_detected"]],
        )


if __name__ == "__main__":
    run()
