import io
import wave

import httpx
import numpy as np
import pytest

from backend.config import Settings
from backend.services.azure_stt import AzureVietnameseSTT, evaluate_azure_transcript
from backend.services.speech_activity import AnalyzedSpeechAudio, SpeechActivityResult
from backend.services.whisper_stt import WhisperConfigurationError, WhisperProviderError, _wav


def recognized(text):
    return {"RecognitionStatus": "Success", "NBest": [{"Display": text, "Confidence": .45}]}


@pytest.mark.asyncio
async def test_azure_silence_never_reaches_decoder():
    def forbidden(request):
        pytest.fail("Non-speech must not reach generative recognition")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        result = await AzureVietnameseSTT(Settings(), client).transcribe(_wav(np.zeros(16000)))
    assert not result.speech_detected and result.rejection_reason == "no_speech"


@pytest.mark.asyncio
async def test_azure_keeps_quiet_pcm_whole_and_locks_vietnamese_without_prompt(monkeypatch):
    samples = np.concatenate([np.zeros(800), np.full(16000, .002), np.zeros(1600)])
    monkeypatch.setattr("backend.services.azure_stt.analyze_speech_audio", lambda _:
                        AnalyzedSpeechAudio(samples, SpeechActivityResult(True, {"speech": 1.0})))
    seen = []
    def provider(request):
        seen.append(request)
        assert dict(request.url.params) == {"language": "vi-VN", "format": "detailed", "profanity": "raw"}
        assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
        with wave.open(io.BytesIO(request.content)) as uploaded:
            assert uploaded.getframerate() == 16000 and uploaded.getnchannels() == 1
            assert uploaded.getnframes() == len(samples)
        return httpx.Response(200, json=recognized("Không lấy sữa có đường. Một lít."))
    settings = Settings(azure_speech_key="test-key", azure_speech_region="southeastasia")
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await AzureVietnameseSTT(settings, client).transcribe(b"audio")
    assert result.text == "Không lấy sữa có đường. Một lít."
    assert result.speech_detected and result.quality["asr_input_gain"] > 1
    assert len(seen) == 1


@pytest.mark.parametrize("text", ["Không.", "Dạ.", "Lẩu.", "Huỷ món trước.", "Hủy món trước."])
def test_low_confidence_short_speech_and_diacritics_are_not_rewritten(text):
    result = evaluate_azure_transcript(recognized(text), {})
    assert result.text == text and result.speech_detected


@pytest.mark.parametrize("payload", [
    {}, {"RecognitionStatus": "Success"},
    {"RecognitionStatus": "Success", "NBest": [{"Display": "Lẩu.", "Confidence": float("nan")}]},
])
def test_azure_rejects_malformed_recognition_evidence(payload):
    with pytest.raises(WhisperProviderError):
        evaluate_azure_transcript(payload, {})


def test_no_match_does_not_create_a_customer_turn():
    result = evaluate_azure_transcript({"RecognitionStatus": "NoMatch"}, {})
    assert not result.text and result.rejection_reason == "no_match"


@pytest.mark.asyncio
async def test_azure_reuses_owned_http_client_and_releases_it_at_shutdown():
    service = AzureVietnameseSTT(Settings())
    first = service._recognition_client()
    assert service._recognition_client() is first
    assert not first.is_closed
    await service.aclose()
    assert first.is_closed and service.client is None
    await service.aclose()


@pytest.mark.asyncio
async def test_azure_does_not_close_an_injected_client():
    async with httpx.AsyncClient() as client:
        service = AzureVietnameseSTT(Settings(), client)
        assert service._recognition_client() is client
        await service.aclose()
        assert not client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [(401, WhisperConfigurationError), (500, WhisperProviderError)])
async def test_azure_errors_never_surface_provider_body(status, error):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, text="private-provider-content"),
    )) as client:
        with pytest.raises(error) as exc:
            await AzureVietnameseSTT(Settings(), client)._recognize(client, b"wav", "key", "eastus")
    assert "private-provider-content" not in str(exc.value)
