import asyncio
import io
from pathlib import Path
from threading import BoundedSemaphore, Event
from time import perf_counter
from types import SimpleNamespace

import av
import numpy as np
import pytest

from backend.config import Settings
from backend.services import speech_activity as vad
from backend.services.groq import GroqAudioInputError, GroqProviderError, GroqService

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC = ROOT / ".tmp/voice_diagnostics_20261103"


def encoded_audio(samples, *, rate=16000, layout="mono", container_format="wav", codec="pcm_s16le"):
    output = io.BytesIO()
    with av.open(output, "w", format=container_format) as container:
        stream = container.add_stream(codec, rate=rate)
        stream.layout = layout
        frame = av.AudioFrame.from_ndarray(
            np.asarray(samples, dtype=np.float32).reshape(-1, len(samples[0])),
            format="fltp", layout=layout,
        )
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


class FrameScores:
    def __init__(self, scores):
        self.scores = iter(scores)
        self.inputs = []

    def run(self, _names, inputs):
        self.inputs.append({name: value.copy() for name, value in inputs.items()})
        return np.array([[next(self.scores)]], dtype=np.float32), inputs["state"] + 1


@pytest.mark.parametrize(
    ("scores", "detected"),
    [
        ([0.9] * 6, False),
        ([0.9] * 7, True),
        ([0.9] * 4 + [0.1] * 10 + [0.9] * 4, True),
        ([0.9] * 4 + [0.1] * 29 + [0.9] * 4, False),
    ],
)
def test_neural_gate_requires_enough_speech_in_one_episode(scores, detected):
    result = vad._analyze_samples(
        np.zeros(len(scores) * 512, dtype=np.float32), FrameScores(scores), perf_counter() + 5,
    )
    assert result.speech_detected is detected


def test_neural_state_and_context_belong_to_each_upload():
    session = FrameScores([0.9] * 4)
    samples = np.linspace(-0.1, 0.1, 1024, dtype=np.float32)
    for _ in range(2):
        vad._analyze_samples(samples, session, perf_counter() + 5)
    for first in (session.inputs[0], session.inputs[2]):
        assert not first["state"].any()
        assert not first["input"][0, :64].any()
        assert first["sr"].shape == ()
    np.testing.assert_array_equal(session.inputs[1]["input"][0, :64], samples[448:512])
    np.testing.assert_array_equal(session.inputs[1]["state"], np.ones((2, 1, 128)))


@pytest.mark.parametrize("probability", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_model_output_never_counts_as_speech(probability):
    with pytest.raises(vad.SpeechActivityUnavailable, match="invalid output"):
        vad._analyze_samples(np.zeros(512), FrameScores([probability]), perf_counter() + 5)


@pytest.mark.parametrize(
    ("rate", "layout", "fmt", "codec"),
    [(8000, "mono", "wav", "pcm_s16le"), (44100, "stereo", "wav", "pcm_s16le"),
     (16000, "mono", "mp3", "libmp3lame"), (16000, "mono", "webm", "libopus"),
     (16000, "mono", "mp4", "aac")],
)
def test_decode_resamples_browser_and_compressed_formats(rate, layout, fmt, codec):
    channels = 2 if layout == "stereo" else 1
    content = encoded_audio(
        np.zeros((channels, rate)), rate=rate, layout=layout, container_format=fmt, codec=codec,
    )
    decoded = vad._decode_audio(content, perf_counter() + 5)
    assert decoded.dtype == np.float32
    assert 15_000 <= len(decoded) <= 18_000  # Lossy codecs can retain a padded tail.
    assert np.isfinite(decoded).all()


@pytest.mark.parametrize("content", [b"", b"not a recording", b"RIFF\x00\x00\x00\x00WAVE"])
def test_malformed_audio_is_an_input_error(content):
    with pytest.raises(vad.SpeechActivityInputError):
        vad.analyze_speech_activity(content)


def test_decoding_has_a_duration_limit(monkeypatch):
    monkeypatch.setattr(vad, "MAX_AUDIO_SECONDS", 0.05)
    with pytest.raises(vad.SpeechActivityInputError, match="duration limit"):
        vad._decode_audio(encoded_audio(np.zeros((1, 1600))), perf_counter() + 5)


def test_verification_deadline_does_not_become_no_speech():
    with pytest.raises(vad.SpeechActivityUnavailable, match="time budget"):
        vad._analyze_samples(np.zeros(512), FrameScores([0.9]), perf_counter() - 1)


def test_quiet_audio_can_be_verified_after_bounded_gain_without_mutating_upload(monkeypatch):
    samples = np.full(16000, 0.0001, dtype=np.float32)
    original = samples.copy()
    analyzed = []
    monkeypatch.setattr(vad, "_decode_audio", lambda *_: samples)
    monkeypatch.setattr(vad, "_model_session", lambda: object())

    def analyze(candidate, *_):
        analyzed.append(candidate.copy())
        return vad.SpeechActivityResult(len(analyzed) == 2, {
            "server_vad_max_probability": 0.01 if len(analyzed) == 1 else 0.99,
        })

    monkeypatch.setattr(vad, "_analyze_samples", analyze)
    result = vad.analyze_speech_activity(b"unchanged-original-upload")
    assert result.speech_detected
    assert result.quality["server_vad_analysis_gain"] == 100
    np.testing.assert_array_equal(samples, original)
    np.testing.assert_allclose(analyzed[1], original * 100)


def test_digital_silence_is_never_amplified_into_speech(monkeypatch):
    calls = []
    monkeypatch.setattr(vad, "_decode_audio", lambda *_: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(vad, "_model_session", lambda: object())

    def analyze(*_):
        calls.append(True)
        return vad.SpeechActivityResult(False, {"server_vad_max_probability": 0.0})

    monkeypatch.setattr(vad, "_analyze_samples", analyze)
    assert not vad.analyze_speech_activity(b"silence").speech_detected
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancelled_awaiter_does_not_free_active_analysis_slot(monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(vad, "_WORK_SLOTS", BoundedSemaphore(1))

    def blocked_decode(_content, _deadline):
        entered.set()
        try:
            release.wait(timeout=5)
            raise vad.SpeechActivityInputError("Test worker finished")
        finally:
            finished.set()

    monkeypatch.setattr(vad, "_decode_audio", blocked_decode)
    task = asyncio.create_task(asyncio.to_thread(vad.analyze_speech_activity, b"audio"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(vad.SpeechActivityUnavailable, match="capacity"):
            vad.analyze_speech_activity(b"second")
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        # Synchronize on the slot release in analyze_speech_activity's finally.
        assert await asyncio.to_thread(vad._WORK_SLOTS.acquire, True, 2)
        vad._WORK_SLOTS.release()


@pytest.mark.asyncio
async def test_periodicity_alone_cannot_override_neural_non_speech(monkeypatch):
    # Alarms/instruments can have a voice-range fundamental. A periodic waveform
    # must not bypass the independently negative speech classification.
    seconds = np.arange(16000) / 16000
    samples = 0.01 * (
        np.sin(2 * np.pi * 155 * seconds)
        + 0.4 * np.sin(2 * np.pi * 310 * seconds)
        + 0.2 * np.sin(2 * np.pi * 465 * seconds)
    ) * (0.7 + 0.3 * np.sin(2 * np.pi * 3 * seconds))
    content = encoded_audio([samples])
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise AssertionError("Periodic non-speech must not reach Whisper")

    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity",
        lambda _content: vad.SpeechActivityResult(False, {"server_vad_max_speech_frames": 2.0}),
    )
    service = GroqService(
        Settings(_env_file=None, groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))),
    )
    result = await service.transcribe("periodic.wav", content, "audio/wav")
    assert not result.speech_detected
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["no_speech", "invalid", "unavailable"])
async def test_acoustic_rejection_never_calls_any_whisper_decoder(monkeypatch, outcome):
    calls = []

    def analyze(_content):
        if outcome == "invalid":
            raise vad.SpeechActivityInputError("Invalid media")
        if outcome == "unavailable":
            raise vad.SpeechActivityUnavailable("Missing model")
        return vad.SpeechActivityResult(False, {"server_vad_max_speech_frames": 2.0})

    async def create(**kwargs):
        calls.append(kwargs)
        raise AssertionError("No acoustic speech must not reach ASR")

    monkeypatch.setattr("backend.services.groq.analyze_speech_activity", analyze)
    service = GroqService(
        Settings(_env_file=None, groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))),
    )
    if outcome == "no_speech":
        result = await service.transcribe("voice.webm", b"audio", "audio/webm")
        assert result == ""
        assert result.speech_detected is False
        assert result.fallback_trace[0]["reason"] == "no_acoustic_speech"
        assert result.quality["server_vad_max_speech_frames"] == 2
    else:
        with pytest.raises(GroqAudioInputError if outcome == "invalid" else GroqProviderError):
            await service.transcribe("voice.webm", b"audio", "audio/webm")
    assert calls == []


@pytest.mark.parametrize(
    "filename",
    ["vimd_11_0309.wav", "vimd_12_0284.wav", "vimd_12_0286.wav"],
)
def test_server_silero_accepts_diagnostic_vietnamese_speech(filename: str) -> None:
    if not (DIAGNOSTIC / filename).is_file():
        pytest.skip("Optional licensed diagnostic corpus is not cached")
    result = vad.analyze_speech_activity((DIAGNOSTIC / filename).read_bytes())
    assert result.speech_detected is True
    assert result.quality["server_vad_max_speech_frames"] >= vad.MIN_SPEECH_FRAMES


@pytest.mark.parametrize(
    "filename",
    [
        "5-203128-A-0.wav",  # dog
        "5-210612-A-37.wav",  # clock alarm
        "5-182007-A-36.wav",  # vacuum
        "5-205090-A-32.wav",  # keyboard
        "5-218980-A-30.wav",  # door knock
    ],
)
def test_server_silero_rejects_diagnostic_environmental_audio(filename: str) -> None:
    if not (DIAGNOSTIC / filename).is_file():
        pytest.skip("Optional licensed diagnostic corpus is not cached")
    result = vad.analyze_speech_activity((DIAGNOSTIC / filename).read_bytes())
    assert result.speech_detected is False
    assert result.quality["server_vad_max_speech_frames"] < vad.MIN_SPEECH_FRAMES
