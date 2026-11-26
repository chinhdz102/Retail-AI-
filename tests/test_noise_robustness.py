"""Acoustic regressions: long stationary interference and quiet noisy human speech."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from backend.services.speech_activity import MODEL_PATH, analyze_speech_activity
from evaluation.audit_noise_ablation import wav
from evaluation.prepare_noise_ablation import decode

ROOT = Path(__file__).resolve().parents[1]


def fan_like(seconds: float) -> np.ndarray:
    # Synthetic fixture, not a real fan recording. Low-frequency colored noise,
    # motor harmonics and slow blade modulation, selected independent of VAD.
    rate = 48000
    rng = np.random.default_rng(117)
    size = int(seconds * rate)
    white = rng.normal(0, 1, size)
    spectrum = np.fft.rfft(white)
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    spectrum /= np.sqrt(1 + (frequencies / 600) ** 2)
    noise = np.fft.irfft(spectrum, size)
    time = np.arange(size) / rate
    noise += 0.3 * np.sin(2 * np.pi * 100 * time)
    noise += 0.15 * np.sin(2 * np.pi * 1200 * time)
    noise *= 1 + 0.08 * np.sin(2 * np.pi * 8 * time)
    return (noise / np.sqrt(np.mean(noise**2))).astype(np.float32)


@pytest.mark.parametrize("level", [0.0003, 0.001, 0.005, 0.02, 0.08])
@pytest.mark.parametrize("pumping", [False, True])
def test_long_fan_like_noise_is_not_customer_speech(level, pumping):
    audio = fan_like(20) * level
    if pumping:
        audio *= np.linspace(0.2, 2, audio.size, dtype=np.float32)
    assert not analyze_speech_activity(wav(audio)).speech_detected


@pytest.mark.parametrize("gain", [0.02, 0.05, 1.0])
@pytest.mark.parametrize("filename", ["10057024225045006889.wav", "10072308967409148015.wav"])
def test_quiet_vietnamese_speech_survives_fan_like_noise(filename, gain):
    file = ROOT / ".tmp/fleurs_vi_test_samples" / filename
    if not file.exists():
        pytest.skip("Optional licensed FLEURS diagnostic is not cached")
    audio = decode(file)
    audio *= 0.06 / np.sqrt(np.mean(audio**2))
    noise = fan_like(len(audio) / 48000)[: len(audio)] * 0.03
    # +6dB RMS speech/noise before reducing both to test quiet microphones.
    assert analyze_speech_activity(wav((audio + noise) * gain)).speech_detected


def test_server_and_browser_ship_identical_vad_weights():
    browser = ROOT / "apps/kiosk-airi/public/vad" / MODEL_PATH.name
    if not browser.exists():
        pytest.skip("Browser VAD assets not generated yet (run npm run predev)")
    assert (
        hashlib.sha256(MODEL_PATH.read_bytes()).digest()
        == hashlib.sha256(browser.read_bytes()).digest()
    )
