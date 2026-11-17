import asyncio
import io
import wave

import httpx
import numpy as np
import pytest

from backend.config import Settings
from backend.services import whisper_stt
from backend.services.speech_activity import (
    AnalyzedSpeechAudio,
    SpeechActivityInputError,
    SpeechActivityResult,
    analyze_speech_audio,
)
from backend.services.whisper_stt import (
    WhisperProviderError,
    WhisperSTT,
    WhisperTimeoutError,
    _prepare_decode_audio,
    evaluate_transcript,
)


def acoustic(frames=40, duration=3):
    return {
        "server_vad_duration_seconds": float(duration),
        "server_vad_positive_frame_count": float(frames),
    }


def payload(text="Cho tôi biết giá sữa.", **evidence):
    return {
        "text": text,
        "segments": [
            {
                "text": text,
                "start": 0.0,
                "end": 2.0,
                "no_speech_prob": 0.01,
                "avg_logprob": -0.2,
                "compression_ratio": 1.1,
                **evidence,
            }
        ],
    }


def wav(samples):
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((np.asarray(samples) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def test_quiet_verified_audio_reaches_whisper_at_audible_level_without_clipping():
    samples = np.sin(np.arange(16000) * 2 * np.pi * 220 / 16000).astype(np.float32) * .003
    encoded, gain = _prepare_decode_audio(samples)
    assert 1 < gain <= 32
    with wave.open(io.BytesIO(encoded)) as audio:
        pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2") / 32768
    assert .04 < np.sqrt(np.mean(pcm * pcm)) < .06
    assert np.max(np.abs(pcm)) < .95
    normal = samples * 40
    assert _prepare_decode_audio(normal)[1] == 1


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros(16000),
        np.random.default_rng(37).normal(0, 0.002, 16000),
        np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000) * 0.1,
    ],
)
@pytest.mark.asyncio
async def test_native_silence_and_noise_do_not_call_provider(samples):
    async def forbidden(request):
        pytest.fail("Non-speech must never reach Whisper")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        result = await WhisperSTT(Settings(groq_api_key=""), client).transcribe(wav(samples))
    assert result.text == ""
    assert not result.speech_detected
    assert result.rejection_reason == "no_speech"


def test_native_decode_rejects_invalid_and_long_audio():
    with pytest.raises(SpeechActivityInputError):
        analyze_speech_audio(b"not audio")
    with pytest.raises(SpeechActivityInputError):
        analyze_speech_audio(wav(np.zeros(61 * 16000)))


def test_reproduced_confident_subscription_hallucination_is_rejected():
    data = payload(
        "Các bạn có thể nhớ đăng ký kênh để ủng hộ kênh của mình nhé.",
        no_speech_prob=0.05340576,
        avg_logprob=-0.13572094,
        compression_ratio=1.0625,
    )
    result = evaluate_transcript(data, acoustic(frames=13, duration=5))
    assert result.text == ""
    assert result.rejection_reason == "insufficient_acoustic_support"


def test_reproduced_cough_repetition_is_rejected():
    result = evaluate_transcript(
        payload("Cơm, cơm, cơm, cơm, cơm, cơm, cơm.", no_speech_prob=0.46435547),
        acoustic(frames=18, duration=5),
    )
    assert result.rejection_reason == "repetition"
    assert not result.text


def test_sparse_non_speech_does_not_pass_as_one_uncertain_word():
    result = evaluate_transcript(payload("Ngon", avg_logprob=-0.8539931), acoustic(13, 5))
    assert result.rejection_reason == "uncertain_sparse_speech"


@pytest.mark.parametrize(
    "text,frames,probability,logprob",
    [
        ("Cơm cơm", 9, 0.57470703, -0.7109736),
        ("Chào mừng quý vị đến với kênh youtube của mình.", 17, 0.5205078, -0.515461),
    ],
)
def test_cropped_browser_nonspeech_is_not_accepted_as_a_short_reply(
    text,
    frames,
    probability,
    logprob,
):
    result = evaluate_transcript(
        payload(text, no_speech_prob=probability, avg_logprob=logprob),
        acoustic(frames),
    )
    assert result.rejection_reason == "uncertain_sparse_speech"


def test_local_natural_repetition_does_not_discard_a_whole_human_sentence():
    text = (
        "Nếu mà để thực tế thì máy lọc nước biển thành nước uống. "
        "Thế à? Thế à? Thế à? Đúng rồi mình thích máy này."
    )
    assert evaluate_transcript(payload(text), acoustic(200, 10)).text == text


def test_recorded_short_answer_evidence_is_preserved():
    assert evaluate_transcript(
        payload("Dạ.", avg_logprob=-0.7412197),
        acoustic(10, 1.872),
    ).speech_detected


@pytest.mark.parametrize(
    "text,frames",
    [
        ("Có.", 7),
        ("Không.", 8),
        ("Dạ.", 10),
        ("Một lít.", 15),
        ("Không đường.", 16),
        ("Sữa.", 10),
        ("Đăng ký kênh.", 27),
        ("Sữa tươi không đường giá bao nhiêu một hộp vậy bạn?", 40),
    ],
)
def test_short_answers_and_actual_subscription_phrase_are_not_blacklisted(text, frames):
    result = evaluate_transcript(payload(text), acoustic(frames))
    assert result.text == text
    assert result.speech_detected


@pytest.mark.parametrize(
    "evidence",
    [{"no_speech_prob": 0.9, "avg_logprob": -1.5}],
)
def test_uncertain_segments_return_no_text(evidence):
    result = evaluate_transcript(payload(**evidence), acoustic())
    assert not result.text
    assert result.rejection_reason == "uncertain_segments"


@pytest.mark.parametrize(
    "evidence",
    [
        {"no_speech_prob": 0.9},
        {"avg_logprob": -1.5},
        {"compression_ratio": 3.5},
    ],
)
def test_one_quality_signal_does_not_silently_drop_a_supported_sentence(evidence):
    assert evaluate_transcript(payload(**evidence), acoustic()).text == payload()["text"]


@pytest.mark.parametrize(
    "evidence",
    [
        {"no_speech_prob": float("nan")},
        {"end": -1},
        {"start": -1},
        {"avg_logprob": None},
        {"no_speech_prob": 2},
        {"compression_ratio": -2},
    ],
)
def test_missing_or_invalid_evidence_is_provider_error(evidence):
    with pytest.raises(WhisperProviderError):
        evaluate_transcript(payload(**evidence), acoustic())


def test_missing_segments_and_disagreeing_text_are_provider_errors():
    with pytest.raises(WhisperProviderError):
        evaluate_transcript({"text": "Xin chào"}, acoustic())
    bad = payload()
    bad["text"] = "Câu được thêm vào ngoài bằng chứng."
    with pytest.raises(WhisperProviderError):
        evaluate_transcript(bad, acoustic())


@pytest.fixture
def speech(monkeypatch):
    monkeypatch.setattr(
        whisper_stt,
        "analyze_speech_audio",
        lambda content: AnalyzedSpeechAudio(
            np.ones(48000, dtype=np.float32) * 0.02,
            SpeechActivityResult(True, acoustic()),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_payload",
    [
        payload(),
        {"text": "", "segments": []},
        payload(no_speech_prob=0.9, avg_logprob=-1.5),
        payload("cơm " * 7),
    ],
)
async def test_one_decode_no_prompt_no_fallback_even_after_rejection(speech, provider_payload):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=provider_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await WhisperSTT(Settings(groq_api_key="test"), client).transcribe(b"audio")
    assert len(calls) == 1
    body = calls[0].content
    assert b"whisper-large-v3" in body and b"verbose_json" in body
    assert b'name="prompt"' not in body and b"turbo" not in body
    assert b'name="language"\r\n\r\nvi' in body
    assert b"RIFF" in body
    assert bool(result.text) == result.speech_detected


@pytest.mark.asyncio
async def test_auth_or_quota_can_rotate_keys_but_same_model(speech):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await WhisperSTT(
            Settings(
                groq_api_key="primary",
                groq_api_key_fallback_1="secondary",
            ),
            client,
        ).transcribe(b"audio")
    assert result.speech_detected
    assert len(calls) == 2
    assert calls[0].headers["authorization"] != calls[1].headers["authorization"]


@pytest.mark.asyncio
async def test_deadline_and_cancellation_do_not_retry(speech):
    count = 0

    async def slow(request):
        nonlocal count
        count += 1
        await asyncio.sleep(1)
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        service = WhisperSTT(Settings(groq_api_key="test", whisper_timeout_seconds=0.03), client)
        with pytest.raises(WhisperTimeoutError):
            await service.transcribe(b"audio")
    assert count == 1
