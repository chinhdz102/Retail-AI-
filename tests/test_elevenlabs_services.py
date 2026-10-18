import json

import httpx
import pytest

from backend.config import Settings
from backend.services.elevenlabs import (
    ElevenLabsConfigurationError,
    ElevenLabsService,
)


class FakeResponse:
    def __init__(self, *, content=b"", payload=None, status_code=200, content_type="audio/mpeg"):
        self.content = content
        self.payload = payload
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeElevenClient:
    def __init__(self, *, voices=None, subscription=None):
        self.voices = voices or []
        self.subscription = subscription or {}
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url == "/v1/voices":
            return FakeResponse(payload={"voices": self.voices})
        if url == "/v1/user/subscription":
            return FakeResponse(payload=self.subscription)
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(content=b"mp3-audio")


class FreePlanElevenClient(FakeElevenClient):
    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if "giang-voice-id" in url:
            return FakeResponse(status_code=402)
        return FakeResponse(content=b"fallback-mp3")


@pytest.mark.asyncio
async def test_synthesize_uses_giang_voice_and_multilingual_model() -> None:
    client = FakeElevenClient()
    settings = Settings(
        elevenlabs_api_key="test-key",
        elevenlabs_vi_voice_id="giang-voice-id",
        elevenlabs_model="eleven_multilingual_v2",
    )
    service = ElevenLabsService(settings=settings, client=client)

    result = await service.synthesize("Xin chào", language="vi")

    assert result.content == b"mp3-audio"
    assert result.voice_id == "giang-voice-id"
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "/v1/text-to-speech/giang-voice-id?output_format=mp3_44100_64"
    assert kwargs["headers"]["xi-api-key"] == "test-key"
    assert kwargs["json"]["model_id"] == "eleven_multilingual_v2"
    assert kwargs["json"]["text"] == "Xin chào"
    assert kwargs["json"]["voice_settings"]["speed"] == 0.88


@pytest.mark.asyncio
async def test_synthesize_resolves_giang_by_exact_voice_name() -> None:
    client = FakeElevenClient(
        voices=[
            {"voice_id": "other", "name": "Other voice"},
            {"voice_id": "giang-voice-id", "name": "Giang - Northern female Narrator"},
        ]
    )
    settings = Settings(elevenlabs_api_key="test-key", elevenlabs_vi_voice_id="")
    service = ElevenLabsService(settings=settings, client=client)

    result = await service.synthesize("Xin chào", language="vi")

    assert result.voice_id == "giang-voice-id"
    assert client.calls[0][0:2] == ("GET", "/v1/voices")


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_free_premade_voice_when_giang_requires_paid_plan() -> None:
    client = FreePlanElevenClient()
    settings = Settings(
        elevenlabs_api_key="test-key",
        elevenlabs_vi_voice_id="giang-voice-id",
        elevenlabs_vi_fallback_voice_id="free-voice-id",
        elevenlabs_model="eleven_turbo_v2_5",
    )
    service = ElevenLabsService(settings=settings, client=client)

    result = await service.synthesize("Xin chào", language="vi")

    assert result.content == b"fallback-mp3"
    assert result.voice_id == "free-voice-id"
    assert [call[1] for call in client.calls] == [
        "/v1/text-to-speech/giang-voice-id?output_format=mp3_44100_64",
        "/v1/text-to-speech/free-voice-id?output_format=mp3_44100_64",
    ]
    assert client.calls[1][2]["json"]["model_id"] == "eleven_turbo_v2_5"


@pytest.mark.asyncio
async def test_synthesize_caches_paid_voice_failure_for_follow_up_turns() -> None:
    client = FreePlanElevenClient()
    settings = Settings(
        elevenlabs_api_key="test-key",
        elevenlabs_vi_voice_id="giang-voice-id",
        elevenlabs_vi_fallback_voice_id="free-voice-id",
    )
    service = ElevenLabsService(settings=settings, client=client)

    await service.synthesize("Xin chào", language="vi")
    await service.synthesize("Bạn khỏe không?", language="vi")

    assert [call[1] for call in client.calls] == [
        "/v1/text-to-speech/giang-voice-id?output_format=mp3_44100_64",
        "/v1/text-to-speech/free-voice-id?output_format=mp3_44100_64",
        "/v1/text-to-speech/free-voice-id?output_format=mp3_44100_64",
    ]


@pytest.mark.asyncio
async def test_usage_returns_safe_remaining_character_count() -> None:
    client = FakeElevenClient(
        subscription={
            "tier": "starter",
            "character_count": 1200,
            "character_limit": 10000,
            "next_character_count_reset_unix": 1770000000,
        }
    )
    settings = Settings(elevenlabs_api_key="test-key")
    service = ElevenLabsService(settings=settings, client=client)

    usage = await service.usage()

    assert usage == {
        "tier": "starter",
        "character_count": 1200,
        "character_limit": 10000,
        "remaining_characters": 8800,
        "next_character_count_reset_unix": 1770000000,
    }


@pytest.mark.asyncio
async def test_missing_elevenlabs_key_fails_without_exposing_secret() -> None:
    service = ElevenLabsService(settings=Settings(elevenlabs_api_key=""), client=None)

    with pytest.raises(ElevenLabsConfigurationError) as error:
        await service.synthesize("Hello", language="en")

    assert "test-key" not in str(error.value)
    assert "xi-api-key" not in str(error.value)


@pytest.mark.asyncio
async def test_open_stream_returns_audio_stream_without_buffering_synthesis() -> None:
    seen = {}

    async def handler(request):
        seen["request"] = request
        return httpx.Response(
            200,
            headers={"content-type": "audio/mpeg"},
            content=b"first-second",
        )

    async with httpx.AsyncClient(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        service = ElevenLabsService(
            settings=Settings(
                elevenlabs_api_key="test-key",
                elevenlabs_vi_voice_id="giang-voice-id",
            ),
            client=client,
        )
        stream = await service.open_stream("Xin chào")
        chunks = [chunk async for chunk in stream.iter_bytes()]
        await stream.aclose()

    request = seen["request"]
    assert b"".join(chunks) == b"first-second"
    assert request.headers["xi-api-key"] == "test-key"
    assert request.url.path.endswith("/giang-voice-id")
    assert json.loads(request.content)["text"] == "Xin chào"
    assert stream.voice_id == "giang-voice-id"
