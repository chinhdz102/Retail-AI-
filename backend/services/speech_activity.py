"""Independent acoustic evidence before accepting a generative ASR transcript.

Silero v6 uses the same weights, 64-sample context and 512-sample frames as
vad-web. Recurrent state belongs to each upload, never to the cached session.
This checks for speech; it does not identify the speaker or validate the words.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter

import av
import numpy as np
import onnxruntime as ort

from backend.services.audio_events import verify_speech_events, warm_audio_events

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
CONTEXT_SAMPLES = 64
POSITIVE_THRESHOLD = 0.5
NEGATIVE_THRESHOLD = 0.35
# vad-web 0.0.31 uses floor when quantizing milliseconds to native 32 ms frames.
MIN_SPEECH_FRAMES = math.floor(160 / 32)
REDEMPTION_FRAMES = math.floor(900 / 32)
MAX_AUDIO_SECONDS = 60
MAX_PROCESSING_SECONDS = 5.0
MODEL_PATH = Path(__file__).resolve().parents[1] / "assets/silero/silero_vad_v6.onnx"
_WORK_SLOTS = BoundedSemaphore(2)
_MODEL_LOCK = Lock()


def warm_speech_models() -> None:
    """Load both local acoustic models before the first customer turn.

    The first request otherwise pays ONNX graph/session creation while the
    customer is already waiting for recognition. This is intentionally a
    synchronous helper so the API can run it in a worker thread at startup.
    """
    with _MODEL_LOCK:
        _model_session()
    # YAMNet is the second, non-verbal-event veto in the same bounded path.
    warm_audio_events()


class SpeechActivityInputError(ValueError):
    """Audio exceeds the bounded server analysis duration or has invalid samples."""


class SpeechActivityUnavailable(RuntimeError):
    """Acoustic verification is temporarily unavailable; do not silently bypass it."""


@dataclass(frozen=True)
class SpeechActivityResult:
    speech_detected: bool
    quality: dict[str, float]


@dataclass(frozen=True)
class AnalyzedSpeechAudio:
    samples: np.ndarray
    activity: SpeechActivityResult


@lru_cache(maxsize=1)
def _model_session() -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(
        str(MODEL_PATH),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _decode_audio(content: bytes, deadline: float) -> np.ndarray:
    """Decode locally, with bounded output, including browser MP4/WebM fallback.

    An undecodable/unsupported upload is an input error, never evidence of speech.
    """
    chunks: list[np.ndarray] = []
    sample_count = 0
    try:
        with av.open(io.BytesIO(content), mode="r") as container:
            if not container.streams.audio:
                raise SpeechActivityInputError("Upload contains no audio stream")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                if perf_counter() > deadline:
                    raise SpeechActivityUnavailable("Audio decoding exceeded its time budget")
                for converted in resampler.resample(frame):
                    sample_count += converted.samples
                    if sample_count > SAMPLE_RATE * MAX_AUDIO_SECONDS:
                        raise SpeechActivityInputError("Audio exceeds server duration limit")
                    chunks.append(converted.to_ndarray().reshape(-1))
            for converted in resampler.resample(None):
                sample_count += converted.samples
                if sample_count > SAMPLE_RATE * MAX_AUDIO_SECONDS:
                    raise SpeechActivityInputError("Audio exceeds server duration limit")
                chunks.append(converted.to_ndarray().reshape(-1))
    except (av.error.FFmpegError, ValueError) as exc:
        if isinstance(exc, SpeechActivityInputError):
            raise
        raise SpeechActivityInputError("Audio could not be decoded") from exc
    if not chunks:
        return np.empty(0, dtype=np.float32)
    samples = np.concatenate(chunks)
    if not np.isfinite(samples).all():
        raise SpeechActivityInputError("Audio contains non-finite samples")
    return samples


def _analyze_samples(
    samples: np.ndarray,
    session: ort.InferenceSession,
    deadline: float,
    *,
    positive_threshold: float | None = None,
    min_speech_frames: int | None = None,
) -> SpeechActivityResult:
    positive_threshold = POSITIVE_THRESHOLD if positive_threshold is None else positive_threshold
    min_speech_frames = MIN_SPEECH_FRAMES if min_speech_frames is None else min_speech_frames
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
    sample_rate = np.array(SAMPLE_RATE, dtype=np.int64)
    positive_frames = 0
    episode_frames = 0
    longest_episode = 0
    redemption = 0
    peak_probability = 0.0
    probability_sum = 0.0
    frame_count = 0
    for offset in range(0, len(samples), FRAME_SAMPLES):
        if perf_counter() > deadline:
            raise SpeechActivityUnavailable("Acoustic verification exceeded its time budget")
        frame = samples[offset : offset + FRAME_SAMPLES]
        if len(frame) < FRAME_SAMPLES:
            frame = np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
        model_input = np.concatenate((context, frame)).reshape(1, -1)
        output, state = session.run(
            ["output", "stateN"],
            {"input": model_input, "state": state, "sr": sample_rate},
        )
        context = frame[-CONTEXT_SAMPLES:].copy()
        probability = float(output.reshape(-1)[0])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise SpeechActivityUnavailable("Acoustic model returned invalid output")
        frame_count += 1
        probability_sum += probability
        peak_probability = max(peak_probability, probability)
        if probability >= positive_threshold:
            positive_frames += 1
            episode_frames += 1
            redemption = 0
            longest_episode = max(longest_episode, episode_frames)
        elif probability < NEGATIVE_THRESHOLD and episode_frames:
            redemption += 1
            if redemption >= REDEMPTION_FRAMES:
                episode_frames = 0
                redemption = 0
    return SpeechActivityResult(
        speech_detected=longest_episode >= min_speech_frames,
        quality={
            "server_vad_duration_seconds": len(samples) / SAMPLE_RATE,
            "server_vad_frame_count": float(frame_count),
            "server_vad_positive_frame_ratio": positive_frames / max(1, frame_count),
            "server_vad_positive_frame_count": float(positive_frames),
            "server_vad_max_probability": peak_probability,
            "server_vad_mean_probability": probability_sum / max(1, frame_count),
            "server_vad_max_speech_frames": float(longest_episode),
        },
    )


def analyze_speech_activity(content: bytes) -> SpeechActivityResult:
    return analyze_speech_audio(content).activity


def analyze_speech_audio(
    content: bytes,
    *,
    positive_threshold: float | None = None,
    min_speech_frames: int | None = None,
) -> AnalyzedSpeechAudio:
    """Run in a worker thread. Cancellation never frees a still-running slot."""
    if not _WORK_SLOTS.acquire(blocking=False):
        raise SpeechActivityUnavailable("Acoustic verification capacity is temporarily full")
    try:
        deadline = perf_counter() + MAX_PROCESSING_SECONDS
        samples = _decode_audio(content, deadline)
        # lru_cache alone may initialize twice on concurrent cold requests.
        with _MODEL_LOCK:
            session = _model_session()
        profile = {}
        if positive_threshold is not None:
            profile["positive_threshold"] = positive_threshold
        if min_speech_frames is not None:
            profile["min_speech_frames"] = min_speech_frames
        result = _analyze_samples(samples, session, deadline, **profile)
        gain = 1.0
        raw_peak_probability = result.quality["server_vad_max_probability"]
        # A second acoustic pass compensates for very quiet recordings. Never
        # retry the generative decoder or substitute periodicity for speech.
        # Preserve the original upload sent to ASR, and avoid clipping/silence.
        if not result.speech_detected and samples.size:
            rms = float(np.sqrt(np.mean(samples * samples)))
            peak = float(np.max(np.abs(samples)))
            if 0 < rms < 0.01 and peak >= 2 / 32768:
                gain = min(100.0, 0.05 / rms, 0.95 / peak)
                if gain > 1:
                    result = _analyze_samples(samples * gain, session, deadline, **profile)
        event_quality = {}
        speech_detected = result.speech_detected
        if speech_detected:
            speech_detected, event_quality = verify_speech_events(samples)
            if perf_counter() > deadline:
                raise SpeechActivityUnavailable("Acoustic verification exceeded its time budget")
        activity = SpeechActivityResult(
            speech_detected,
            {
                **result.quality,
                **event_quality,
                "server_vad_analysis_gain": gain,
                "server_vad_raw_max_probability": raw_peak_probability,
                "server_vad_elapsed_ms": (perf_counter() - deadline + MAX_PROCESSING_SECONDS)
                * 1000,
            },
        )
        return AnalyzedSpeechAudio(samples, activity)
    except (SpeechActivityInputError, SpeechActivityUnavailable):
        raise
    except Exception as exc:
        raise SpeechActivityUnavailable("Acoustic verification failed") from exc
    finally:
        _WORK_SLOTS.release()
