"""Fixed-order speech/noise diagnostic; source audio and derivatives stay in .tmp."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".tmp/noise_ablation_20261103"


def decode(path: Path, rate: int = 48000) -> np.ndarray:
    chunks = []
    with av.open(io.BytesIO(path.read_bytes())) as c:
        r = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        for frame in c.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in r.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in r.resample(None))
    return np.concatenate(chunks)


def run():
    CACHE.mkdir(exist_ok=True)
    fleurs = json.loads((ROOT / ".tmp/fleurs_vi_test_samples/manifest.json").read_text("utf-8"))
    en = json.loads((ROOT / ".tmp/librispeech_stt_diagnostic/manifest.json").read_text("utf-8"))
    speech = [
        dict(
            id="fleurs:" + s["filename"],
            language="vi",
            reference=s["reference"],
            path=".tmp/fleurs_vi_test_samples/" + s["filename"],
            source="https://huggingface.co/datasets/google/fleurs",
            license="CC-BY-4.0",
        )
        for s in fleurs["samples"][:4]
    ] + en["samples"]
    # Keep every full utterance; the endpoint can score >12s uploads, while kiosk
    # capture has a separate 12s cap. This is not a segmentation acceptance test.
    diagnostics = json.loads(
        (ROOT / ".tmp/voice_diagnostics_20261103/manifest.json").read_text("utf-8")
    )
    noise = [s for s in diagnostics["samples"] if s["kind"] == "environmental-control"]
    rows = []

    def save(item, samples):
        filename = f"{len(rows):03d}.f32"
        samples.astype("<f4").tofile(CACHE / filename)
        rows.append(
            {
                **item,
                "pcm_path": str((CACHE / filename).relative_to(ROOT)),
                "pcm_sha256": hashlib.sha256((CACHE / filename).read_bytes()).hexdigest(),
                "duration_seconds": len(samples) / 48000,
            }
        )

    for s in speech:
        audio = decode(ROOT / s["path"])
        audio *= 0.06 / max(float(np.sqrt(np.mean(audio * audio))), 1e-10)
        padded = np.pad(audio, (48000, 48000))
        # Clean + real vacuum at 0/10dB + keyboard at 0dB, selected before ASR.
        save({**s, "condition": "clean", "id": s["id"] + ":clean"}, padded)
        for category, snr in [
            ("vacuum_cleaner", 0),
            ("vacuum_cleaner", 10),
            ("keyboard_typing", 0),
        ]:
            n = next(n for n in noise if n["category"] == category)
            background = np.resize(decode(ROOT / n["path"]), padded.size)
            background *= (
                0.06 / 10 ** (snr / 20) / max(float(np.sqrt(np.mean(background**2))), 1e-10)
            )
            mixed = padded + background
            if np.max(np.abs(mixed)) >= 1:
                raise ValueError("Mixture would clip")
            save(
                {
                    **s,
                    "condition": f"{category}:{snr}dB",
                    "noise": n,
                    "id": s["id"] + f":{category}:{snr}",
                },
                mixed,
            )
    for n in noise:
        audio = decode(ROOT / n["path"])
        audio *= 0.06 / max(float(np.sqrt(np.mean(audio**2))), 1e-10)
        save(
            {**n, "condition": "noise_only", "language": "vi", "reference": ""},
            np.pad(audio, (48000, 48000)),
        )
    save(
        {"id": "digital-silence", "condition": "noise_only", "language": "vi", "reference": ""},
        np.zeros(48000 * 4, dtype=np.float32),
    )
    (CACHE / "manifest.json").write_text(
        json.dumps(
            {
                "selection": "First four cached FLEURS and four cached LibriSpeech before ASR; "
                "all five cached ESC-50 environmental categories; no output-based selection.",
                "sampling_rate": 48000,
                "seed": None,
                "speech_rms": 0.06,
                "limitations": [
                    "Programmatic additive noise mixtures, not kiosk mic.",
                    "EN clips are a single speaker. All sources exposed diagnostics.",
                    "No browser AEC/NS/AGC. No independent non-speech annotation.",
                ],
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(len(rows), "fixed conditions prepared")


if __name__ == "__main__":
    run()
