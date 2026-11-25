"""Shrink large WAV uploads using the provider's 16 kHz mono speech format.

No denoising, gain, time trimming or transcript rewriting is applied here.
FLAC is lossless relative to the normalized PCM16; resampling/quantization
are explicit, and the original audio is kept for acoustic checks/verifier.
"""

from __future__ import annotations

import io
from fractions import Fraction
from time import perf_counter

import av
import numpy as np

from backend.services.speech_activity import SAMPLE_RATE, _decode_audio

MIN_PREPARATION_BYTES = 512 * 1024


def prepare_stt_upload(
    content: bytes,
    *,
    samples: np.ndarray | None = None,
) -> bytes | None:
    """Return a smaller FLAC, or leave a small/already-compressed upload alone.

    Input validation and no-speech rejection run before this optional step.
    A size threshold avoids making MP3/browser-Opus payloads larger.
    """
    if len(content) < MIN_PREPARATION_BYTES or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        return None
    deadline = perf_counter() + 2
    if samples is None:
        samples = _decode_audio(content, deadline)
    # A downmix can have peaks beyond PCM16's range. Keep the original upload
    # instead of introducing clipping or changing its gain to make it encodable.
    if not samples.size or np.max(np.abs(samples)) > 1:
        return None
    pcm = np.rint(samples * 32767).astype(np.int16)
    output = io.BytesIO()
    try:
        with av.open(output, mode="w", format="flac") as container:
            stream = container.add_stream("flac", rate=SAMPLE_RATE)
            stream.layout = "mono"
            for offset in range(0, pcm.size, 4096):
                if perf_counter() > deadline:
                    return None
                frame = av.AudioFrame.from_ndarray(pcm[None, offset:offset + 4096],
                                                   format="s16", layout="mono")
                frame.sample_rate = SAMPLE_RATE
                frame.time_base = Fraction(1, SAMPLE_RATE)
                frame.pts = offset
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
    except av.error.FFmpegError:
        # An unavailable encoder must not make an otherwise valid upload fail.
        return None
    encoded = output.getvalue()
    return encoded if len(encoded) < len(content) * 0.9 else None
