"""Dedicated neural speech enhancement (DPDFNet2 ONNX) + VAD-guided silence trimming.

Uses the Dual-Path Deep Filtering Network (DPDFNet2, DeepFilterNet family) via
sherpa-onnx ONNX Runtime on CPU at 16 kHz mono, combined with zero-phase 85 Hz
high-pass filtering and SNR-adaptive wet/dry blending so:
  - Ambient HVAC hum, fans, chatter, and supermarket clatter are suppressed.
  - Clean high-SNR voices are not over-processed (avoiding double-suppression
    artifacts when browser WebRTC noiseSuppression is active).
  - Vietnamese tone contours and micro-phonemes remain intact for Azure STT.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DENOISER_MODEL = ROOT / "data/models/speech-enhancement/dpdfnet2.onnx"
_DENOISER_SLOTS = BoundedSemaphore(2)
_DENOISER_LOCK = Lock()
_DLL_HANDLES: list[object] = []


@dataclass(frozen=True)
class DenoisedSpeechResult:
    samples: np.ndarray
    quality: dict[str, float]
    model_name: str
    denoised: bool


def _register_windows_onnx_dlls() -> None:
    if os.name != "nt" or _DLL_HANDLES:
        return
    for package, relative in (("onnxruntime", "capi"), ("sherpa_onnx", "lib")):
        spec = importlib.util.find_spec(package)
        if spec and spec.origin:
            directory_path = Path(spec.origin).parent / relative
            if directory_path.is_dir():
                _DLL_HANDLES.append(os.add_dll_directory(str(directory_path)))


@lru_cache(maxsize=2)
def _load_dpdfnet_denoiser(model_path: str):
    path = Path(model_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Speech denoiser model missing: {path}")
    _register_windows_onnx_dlls()
    import onnxruntime  # noqa: F401 -- must load before sherpa_onnx on Windows
    import sherpa_onnx

    is_gtcrn = "gtcrn" in path.name.casefold()
    if is_gtcrn:
        model_cfg = sherpa_onnx.OfflineSpeechDenoiserModelConfig(
            gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(path)),
            num_threads=2,
            provider="cpu",
        )
    else:
        model_cfg = sherpa_onnx.OfflineSpeechDenoiserModelConfig(
            dpdfnet=sherpa_onnx.OfflineSpeechDenoiserDpdfNetModelConfig(model=str(path)),
            num_threads=1,
            provider="cpu",
        )
    denoiser = sherpa_onnx.OfflineSpeechDenoiser(
        sherpa_onnx.OfflineSpeechDenoiserConfig(model=model_cfg)
    )
    # Run a tiny warmup call so the first live turn avoids ONNX graph init lag.
    denoiser(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), SAMPLE_RATE)
    return denoiser


def warm_speech_denoiser(model_path: str | Path | None = None) -> bool:
    """Preload the ONNX speech enhancement graph during FastAPI lifespan."""
    target = str(model_path or DEFAULT_DENOISER_MODEL)
    try:
        with _DENOISER_LOCK:
            _load_dpdfnet_denoiser(target)
        logger.info("Neural speech denoiser pre-warmed model=%s", Path(target).name)
        return True
    except Exception as exc:
        logger.warning("Neural speech denoiser warmup skipped: %s", exc)
        return False


def highpass_85hz(samples: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Zero-phase 85 Hz Butterworth-like FFT high-pass to strip AC hum & DC rumble."""
    n = len(samples)
    if n < 256:
        return samples.astype(np.float32, copy=False)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    spec = np.fft.rfft(samples.astype(np.float64))
    hpf = 1.0 / (1.0 + (85.0 / np.maximum(freqs, 1.0)) ** 4)
    hpf[0] = 0.0
    out = np.fft.irfft(spec * hpf, n=n)
    return np.clip(out, -0.99, 0.99).astype(np.float32)


def estimate_clip_snr_db(samples: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Estimate active-speech-to-background-floor SNR in dB from 20 ms frames."""
    frame_len = max(1, sr // 50)  # 20 ms = 320 samples
    num_frames = len(samples) // frame_len
    if num_frames < 5:
        return 25.0
    framed = samples[: num_frames * frame_len].reshape(num_frames, frame_len).astype(np.float64)
    frame_rms = np.sqrt(np.mean(framed * framed, axis=1)) + 1e-7
    noise_floor = float(np.percentile(frame_rms, 15))
    speech_level = float(np.percentile(frame_rms, 88))
    if speech_level <= noise_floor * 1.05:
        return 0.0
    snr = 20.0 * float(np.log10(speech_level / max(noise_floor, 1e-6)))
    return float(np.clip(snr, -5.0, 45.0))


def trim_to_speech_span(
    samples: np.ndarray,
    speech_start_sample: int | None,
    speech_end_sample: int | None,
    *,
    pre_pad_ms: int = 180,
    post_pad_ms: int = 200,
    sr: int = SAMPLE_RATE,
) -> tuple[np.ndarray, float]:
    """Trim excess leading/trailing silence/noise using Silero VAD boundaries."""
    if (
        speech_start_sample is None
        or speech_end_sample is None
        or speech_end_sample <= speech_start_sample
        or len(samples) == 0
    ):
        return samples, 0.0
    pre = int(sr * pre_pad_ms / 1000)
    post = int(sr * post_pad_ms / 1000)
    start = max(0, speech_start_sample - pre)
    end = min(len(samples), speech_end_sample + post)
    # Never over-trim to an unusable tiny slice (< 350 ms).
    if end - start < int(sr * 0.35):
        return samples, 0.0
    trimmed_seconds = max(0.0, (len(samples) - (end - start)) / sr)
    return samples[start:end].copy(), trimmed_seconds


def enhance_speech_samples(
    samples: np.ndarray,
    *,
    enabled: bool = True,
    model_path: str | Path | None = None,
    speech_start_sample: int | None = None,
    speech_end_sample: int | None = None,
) -> DenoisedSpeechResult:
    """Apply VAD edge trimming, 85 Hz HPF, and SNR-adaptive DPDFNet2 neural denoising."""
    started = perf_counter()
    if samples.size == 0:
        return DenoisedSpeechResult(
            samples=samples,
            quality={"denoiser_applied": 0.0},
            model_name="none",
            denoised=False,
        )

    trimmed, trimmed_sec = trim_to_speech_span(
        samples, speech_start_sample, speech_end_sample
    )
    hp = highpass_85hz(trimmed)
    snr_db = estimate_clip_snr_db(hp)

    quality: dict[str, float] = {
        "denoiser_estimated_snr_db": round(snr_db, 2),
        "denoiser_trimmed_seconds": round(trimmed_sec, 3),
        "denoiser_applied": 0.0,
    }
    if not enabled:
        quality["denoiser_elapsed_ms"] = round((perf_counter() - started) * 1000.0, 1)
        return DenoisedSpeechResult(
            samples=hp,
            quality=quality,
            model_name="hpf-85hz",
            denoised=False,
        )

    # Already studio-clean clip (SNR >= 28 dB): VAD trim + 85 Hz HPF is sufficient,
    # saving ~550 ms of neural inference and avoiding double-suppression artifacts.
    if snr_db >= 28.0:
        quality["denoiser_skipped_clean"] = 1.0
        quality["denoiser_elapsed_ms"] = round((perf_counter() - started) * 1000.0, 1)
        return DenoisedSpeechResult(
            samples=hp,
            quality=quality,
            model_name="hpf-85hz-clean",
            denoised=False,
        )

    target = str(model_path or DEFAULT_DENOISER_MODEL)
    model_label = Path(target).stem
    if not _DENOISER_SLOTS.acquire(blocking=False):
        quality["denoiser_elapsed_ms"] = round((perf_counter() - started) * 1000.0, 1)
        return DenoisedSpeechResult(
            samples=hp,
            quality=quality,
            model_name="hpf-85hz-busy",
            denoised=False,
        )
    try:
        with _DENOISER_LOCK:
            denoiser = _load_dpdfnet_denoiser(target)
        res = denoiser(hp.astype(np.float32, copy=False), SAMPLE_RATE)
        enhanced = np.asarray(res.samples, dtype=np.float32)
        n = min(len(hp), len(enhanced))
        if n == 0:
            raise ValueError("Neural denoiser returned empty audio")
        # SNR-adaptive phase-aligned blend:
        # - High SNR (>=26 dB, already clean mic): gentle 45% neural touch avoids
        #   double-suppression artifacts if browser WebRTC NS already ran.
        # - Moderate ambient noise (10..26 dB): 85% DPDFNet2 kills room/supermarket
        #   noise while keeping clean Vietnamese tone contours.
        # - Extreme noise (<10 dB): 75% DPDFNet2 + 25% HPF preserves micro-phonemes
        #   that Azure's acoustic decoder uses at 5 dB SNR.
        if snr_db >= 26.0:
            wet = 0.45
        elif snr_db >= 10.0:
            wet = 0.85
        else:
            wet = 0.75
        blended = np.clip(wet * enhanced[:n] + (1.0 - wet) * hp[:n], -0.99, 0.99).astype(
            np.float32
        )
        quality.update(
            {
                "denoiser_applied": 1.0,
                "denoiser_wet_ratio": wet,
                "denoiser_elapsed_ms": round((perf_counter() - started) * 1000.0, 1),
            }
        )
        return DenoisedSpeechResult(
            samples=blended,
            quality=quality,
            model_name=model_label,
            denoised=True,
        )
    except Exception as exc:
        logger.warning("Neural denoiser fallback to HPF-85Hz: %s", exc)
        quality["denoiser_elapsed_ms"] = round((perf_counter() - started) * 1000.0, 1)
        return DenoisedSpeechResult(
            samples=hp,
            quality=quality,
            model_name="hpf-85hz-fallback",
            denoised=False,
        )
    finally:
        _DENOISER_SLOTS.release()
