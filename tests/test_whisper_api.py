import asyncio
import io

import httpx
import numpy as np
import pytest

from backend.api.main import app, get_groq_service, get_whisper_service, settings
from backend.services.speech_activity import SpeechActivityInputError, SpeechActivityUnavailable
from backend.services.whisper_stt import (
    WhisperConfigurationError,
    WhisperProviderError,
    WhisperResult,
    WhisperTimeoutError,
)
from tests.test_whisper_stt import wav


@pytest.fixture
def api(monkeypatch):
    # Route tests exercise the Groq-shaped fake below, independent of a local
    # demo's Azure provider and optional capture of development diagnostics.
    monkeypatch.setattr(settings, "stt_provider", "groq")
    monkeypatch.setattr(settings, "stt_debug_capture", False)
    def forbidden_chat():
        pytest.fail("STT must not depend on the chat service")

    app.dependency_overrides[get_groq_service] = forbidden_chat
    yield httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


async def upload(api, content=b"audio", language="vi"):
    session = (await api.post("/sessions", json={})).json()["session_id"]
    return await api.post(
        "/audio/transcribe",
        data={"session_id": session, "language": language},
        files={"file": ("voice.wav", io.BytesIO(content), "audio/wav")},
    )


@pytest.mark.asyncio
async def test_real_silence_route_returns_no_text_and_no_chat_dependency(api):
    response = await upload(api, wav(np.zeros(16000)))
    assert response.status_code == 200
    assert response.json()["text"] == ""
    assert response.json()["speech_detected"] is False
    assert response.json()["rejection_reason"] == "no_speech"


@pytest.mark.asyncio
async def test_route_returns_whisper_result_and_diagnostics(api):
    class FakeSTT:
        async def transcribe(self, content):
            assert content == b"audio"
            return WhisperResult(text="Đăng ký kênh.", speech_detected=True, quality={"test": 1.0})

    app.dependency_overrides[get_whisper_service] = FakeSTT
    response = await upload(api)
    assert response.status_code == 200
    assert response.json()["model"] == "whisper-large-v3"
    assert response.json()["text"] == "Đăng ký kênh."
    assert response.json()["language"] == "vi"
    assert response.json()["quality"] == {"test": 1.0}
    assert response.json()["request_id"]
    assert response.json()["provider"] == "groq"
    config = (await api.get("/audio/config")).json()
    assert response.json()["pipeline_version"] == config["pipeline_version"]
    assert config["prompt_enabled"] is False


@pytest.mark.asyncio
async def test_cancelled_speculation_cancels_cloud_work_and_releases_capacity():
    from fastapi import HTTPException

    from backend.api.main import _recognize_while_connected

    released = asyncio.Event()

    class Disconnected:
        async def is_disconnected(self):
            return True

    async def recognition():
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    with pytest.raises(HTTPException) as exc:
        await _recognize_while_connected(Disconnected(), recognition())
    assert exc.value.status_code == 499 and released.is_set()


@pytest.mark.asyncio
async def test_prepare_uses_body_session_without_running_asr_or_exposing_tts_failure(api):
    from backend.api.main import get_azure_speech_service
    from backend.services.azure_speech import AzureSpeechProviderError

    class STT:
        async def transcribe(self, *args, **kwargs):
            pytest.fail("Preparing audio must not decode any customer audio")

    class TTS:
        async def preconnect(self, language):
            assert language == "vi"
            raise AzureSpeechProviderError("secret-provider-body")

    app.dependency_overrides[get_whisper_service] = STT
    app.dependency_overrides[get_azure_speech_service] = TTS
    session = (await api.post("/sessions", json={})).json()["session_id"]
    response = await api.post("/audio/prepare", json={"session_id": session})
    assert response.status_code == 200 and response.json() == {"ready": False}
    assert "secret-provider-body" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "zh", "fr"])
async def test_other_stt_languages_rejected(api, language):
    response = await upload(api, language=language)
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (SpeechActivityInputError, 422),
        (SpeechActivityUnavailable, 503),
        (WhisperConfigurationError, 503),
        (WhisperProviderError, 502),
        (WhisperTimeoutError, 504),
    ],
)
async def test_route_maps_errors_without_secret_leak(api, error, status):
    class FakeSTT:
        async def transcribe(self, content):
            raise error("secret-provider-details")

    app.dependency_overrides[get_whisper_service] = FakeSTT
    response = await upload(api)
    assert response.status_code == status
    assert "secret-provider-details" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,status",
    [
        (b"", 422),
        (b"x" * (10 * 1024 * 1024 + 1), 413),
    ],
    ids=["empty", "oversized"],
)
async def test_route_bounds_upload(api, content, status):
    assert (await upload(api, content)).status_code == status
