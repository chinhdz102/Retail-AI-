"""Acoustic veto for confident non-verbal events, independent of ASR text.

Silero also fires on coughs and animals. YAMNet compares sound events before
Whisper is called. It never sees a transcript, prompt, language or product list.
"""

from functools import lru_cache
from pathlib import Path
from threading import Lock

import numpy as np
import onnxruntime as ort

MODEL_PATH = Path(__file__).resolve().parents[1] / "assets/yamnet/yamnet.onnx"
# AudioSet speech descendants: Speech, Child speech, Conversation, Narration,
# Babbling, Speech synthesizer, Whispering. Silence is neutral for short replies.
SPEECH_CLASSES = (0, 1, 2, 3, 4, 5, 12)
OTHER_CLASSES = tuple(i for i in range(521) if i not in (*SPEECH_CLASSES, 494))
_LOCK = Lock()


@lru_cache(maxsize=1)
def _model_session() -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(
        str(MODEL_PATH), sess_options=options, providers=["CPUExecutionProvider"]
    )


def warm_audio_events() -> None:
    with _LOCK:
        _model_session()


def verify_speech_events(samples: np.ndarray) -> tuple[bool, dict[str, float]]:
    """Veto only confident competing events without strong speech support.

    Taking the maximum speech score alone fails on coughs/cows. Their other
    sound classes are even stronger. Ambiguous ordinary audio remains for VAD
    and Whisper to assess, rather than demanding another high speech threshold.
    The owning acoustic worker bounds concurrency and the whole turn deadline.
    """
    gain = 1.0
    if samples.size:
        rms = float(np.sqrt(np.mean(samples * samples)))
        peak = float(np.max(np.abs(samples)))
        if 0 < rms < 0.01 and peak > 0:
            gain = max(1.0, min(32.0, 0.05 / rms, 0.95 / peak))
    with _LOCK:
        session = _model_session()
    scores = session.run(["output_0"], {"waveform": samples * gain})[0]
    if (
        scores.ndim != 2
        or scores.shape[1] != 521
        or not scores.shape[0]
        or not np.isfinite(scores).all()
        or np.any(scores < 0)
        or np.any(scores > 1)
    ):
        raise RuntimeError("Audio event model returned invalid scores")
    peaks = scores.max(axis=0)
    speech = float(peaks[list(SPEECH_CLASSES)].max())
    competing = peaks[list(OTHER_CLASSES)]
    other = float(competing.max())
    # A loud nonverbal sound early in the upload must not veto a later quiet
    # voice. Compare contemporaneous windows, not maxima from unrelated times.
    # Coughs can also score ~0.8 as speech, so require stronger independent
    # speech support before overriding a confident competing event.
    speech_per_window = scores[:, list(SPEECH_CLASSES)].max(axis=1)
    other_per_window = scores[:, list(OTHER_CLASSES)].max(axis=1)
    supported_windows = (speech_per_window >= 0.85) & (speech_per_window > other_per_window)
    veto = other >= 0.9 and speech < 0.95 and not bool(supported_windows.any())
    return not veto, {
        "event_speech_peak": speech,
        "event_non_speech_peak": other,
        "event_non_speech_class": float(OTHER_CLASSES[int(competing.argmax())]),
        "event_veto": float(veto),
        "event_supported_speech_windows": float(supported_windows.sum()),
    }
