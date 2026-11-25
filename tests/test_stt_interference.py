"""Regressions from live Whisper hallucinations over environmental recordings."""

from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.services.groq import GroqService, _has_insufficient_acoustic_support
from backend.services.speech_activity import SpeechActivityResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "positive_frames", "no_speech", "logprob", "reason"),
    [
        ("Cơm, cơm, cơm, cơm, cơm, cơm, cơm.", 18, 0.464, -0.371, "repeated_phrase"),
        (
            "Các bạn có thể nhớ đăng ký kênh để ủng hộ kênh của mình nhé.",
            13, 0.053, -0.136, "insufficient_acoustic_support",
        ),
    ],
)
async def test_confident_noise_transcripts_never_reach_fallback(
    monkeypatch, text, positive_frames, no_speech, logprob, reason,
):
    # Metadata and text captured from real whisper-large-v3 on 10 November.
    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity",
        lambda _: SpeechActivityResult(True, {
            "server_vad_frame_count": 157.0,
            "server_vad_positive_frame_ratio": positive_frames / 157,
        }),
    )
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=text, segments=[{
            "start": 0, "end": 4, "no_speech_prob": no_speech,
            "avg_logprob": logprob, "compression_ratio": 1.1,
        }])

    service = GroqService(
        Settings(_env_file=None, groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))),
    )
    result = await service.transcribe("environment.wav", b"audio", "audio/wav")
    assert result == ""
    assert not result.speech_detected
    assert result.fallback_trace[0]["reason"] == reason
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("text", "positive_frames"),
    [
        ("Không đường", 7),
        ("Lấy hai hộp sữa một lít không đường cho tôi nhé", 20),
        ("Mua một chai nước, mua thêm hai chai nước.", 20),
    ],
)
def test_acoustic_support_preserves_short_and_fast_vietnamese(text, positive_frames):
    # Includes short answers and rapid speech with only part of its duration
    # above the VAD threshold; no hard minimum length for a customer answer.
    assert not _has_insufficient_acoustic_support(text, {
        "server_vad_frame_count": 200,
        "server_vad_positive_frame_ratio": positive_frames / 200,
    }, "vi")
