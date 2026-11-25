"""Compare a pinned TEN-VAD native binary on cached speech and noise, not a deployment."""

from __future__ import annotations

import ctypes as c
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from backend.services.speech_activity import _decode_audio
from evaluation.audit_noise_ablation import wav

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / ".tmp/noise_research_20261103/ten/ten_vad.dll"


def evaluate(audio: np.ndarray) -> dict:
    lib = c.CDLL(str(LIB))
    lib.ten_vad_create.argtypes = [c.POINTER(c.c_void_p), c.c_size_t, c.c_float]
    lib.ten_vad_destroy.argtypes = [c.POINTER(c.c_void_p)]
    lib.ten_vad_process.argtypes = [
        c.c_void_p,
        c.c_void_p,
        c.c_size_t,
        c.POINTER(c.c_float),
        c.POINTER(c.c_int32),
    ]
    state = c.c_void_p()
    if lib.ten_vad_create(c.byref(state), 256, c.c_float(0.5)):
        raise RuntimeError("TEN initialization failed")
    probability, flag = c.c_float(), c.c_int32()
    probs = []
    try:
        for offset in range(0, audio.size, 256):
            f = audio[offset : offset + 256]
            f = np.pad(f, (0, 256 - len(f)))
            f = (np.clip(f, -1, 1) * 32767).astype(np.int16)
            if lib.ten_vad_process(state, f.ctypes.data, 256, c.byref(probability), c.byref(flag)):
                raise RuntimeError("TEN processing failed")
            probs.append(probability.value)
    finally:
        lib.ten_vad_destroy(c.byref(state))
    count = longest = silence = 0
    for p in probs:
        if p >= 0.5:
            count += 1
            longest = max(count, longest)
            silence = 0
        elif p < 0.35 and count:
            silence += 1
            if silence >= int(900 / 16):
                count = silence = 0
    return {
        "speech_detected": longest >= int(250 / 16),
        "max_positive_frames": longest,
        "max_probability": max(probs, default=0),
        "frame_ms": 16,
    }


def run():
    noise = json.loads(
        (ROOT / ".tmp/noise_controls_20261103/worklet_manifest.json").read_text("utf-8")
    )["rows"]
    speech = json.loads((ROOT / ".tmp/noise_ablation_20261103/manifest.json").read_text("utf-8"))[
        "rows"
    ]
    rows = []
    # Same 16k decoded input as endpoint; no gain retry in this TEN candidate.
    for item in speech + noise:
        samples = np.fromfile(ROOT / item["pcm_path"], dtype="<f4")
        audio = _decode_audio(wav(samples), perf_counter() + 5)
        r = evaluate(audio)
        rows.append({"id": item["id"], "expected_speech": bool(item.get("reference")), **r})
    (ROOT / "docs/reports/artifacts/noise_ten_candidate_20261103.json").write_text(
        json.dumps(
            {
                "repo_commit": "d68ab71c8d5fbd8f98e97ae7c9986af76b5d72b1",
                "binary_sha256": hashlib.sha256(LIB.read_bytes()).hexdigest(),
                "limitations": [
                    "TEN candidate diagnostic; not deployed.",
                    "Exposed mixtures and category controls; no physical mic.",
                ],
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        "Speech misses:",
        [r["id"] for r in rows if r["expected_speech"] and not r["speech_detected"]],
    )
    print(
        "Noise activated:",
        [
            (r["id"], r["max_positive_frames"])
            for r in rows
            if not r["expected_speech"] and r["speech_detected"]
        ],
    )


if __name__ == "__main__":
    run()
