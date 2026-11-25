"""Transport normalization preserves speech duration/waveform without amplifying noise."""

import io
import wave
from time import perf_counter

import numpy as np
import pytest

from backend.services.speech_activity import _decode_audio
from backend.services.speech_upload import prepare_stt_upload


def wav(samples, rate):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(samples.shape[1] if samples.ndim == 2 else 1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.astype("<i2").tobytes())
    return output.getvalue()


@pytest.mark.parametrize("rate,channels", [(16000, 1), (44100, 2), (48000, 1)])
def test_large_pcm_is_smaller_without_trimming_or_changing_the_normalized_waveform(rate, channels):
    t = np.arange(rate * 18) / rate
    signal = (np.sin(t * 2 * np.pi * 220) * 9000).astype(np.int16)
    samples = np.repeat(signal[:, None], channels, axis=1) if channels > 1 else signal
    content = wav(samples, rate)
    encoded = prepare_stt_upload(content)
    assert encoded and encoded.startswith(b"fLaC") and len(encoded) < len(content) * 0.9
    before = _decode_audio(content, perf_counter() + 2)
    after = _decode_audio(encoded, perf_counter() + 2)
    assert before.shape == after.shape and np.max(np.abs(before - after)) < 0.0001


@pytest.mark.parametrize("content", [b"small", b"ID3" + bytes(600000)], ids=["small", "mp3"])
def test_small_or_already_compressed_upload_is_not_reencoded(content):
    assert prepare_stt_upload(content) is None


def test_unavailable_flac_encoder_leaves_the_original_upload_usable(monkeypatch):
    import av

    native_open = av.open

    def open_audio(file, **kwargs):
        if kwargs.get("mode") == "w":
            raise av.error.EncoderNotFoundError(1, "encoder unavailable")
        return native_open(file, **kwargs)

    monkeypatch.setattr("backend.services.speech_upload.av.open", open_audio)
    assert prepare_stt_upload(wav(np.ones(300000, dtype=np.int16), 16000)) is None


def test_peak_beyond_pcm16_range_is_not_clipped_or_rescaled(monkeypatch):
    monkeypatch.setattr("backend.services.speech_upload._decode_audio",
                        lambda *args: np.array([0.1, -1.06, 0.9], dtype=np.float32))
    content = wav(np.ones(300000, dtype=np.int16), 16000)
    assert prepare_stt_upload(content) is None
