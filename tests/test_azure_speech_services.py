import httpx
import pytest

from backend.config import Settings
from backend.services.azure_speech import (
    AzureSpeechConfigurationError,
    AzureSpeechProviderError,
    AzureSpeechService,
)


class FakeResponse:
    def __init__(self, *, content=b"mp3-audio", status_code=200, content_type="audio/mpeg"):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://southeastasia.tts.speech.microsoft.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


class FakeAzureSpeechClient:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []
        self.get_calls = []

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.response

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.parametrize(
    ("language", "locale", "voice"),
    [
        ("vi", "vi-VN", "vi-VN-HoaiMyNeural"),
        ("en", "en-US", "en-US-JennyNeural"),
        ("zh", "zh-CN", "zh-CN-XiaoxiaoNeural"),
    ],
)
@pytest.mark.asyncio
async def test_synthesize_uses_configured_region_and_language_voice(
    language: str, locale: str, voice: str
) -> None:
    client = FakeAzureSpeechClient()
    settings = Settings(azure_speech_key="azure-secret", azure_speech_region="SoutheastAsia")
    service = AzureSpeechService(settings=settings, client=client)

    result = await service.synthesize("Giá < 20.000đ & mới", language=language)

    assert result.content == b"mp3-audio"
    assert result.content_type == "audio/mpeg"
    assert result.voice_id == voice
    url, kwargs = client.calls[0]
    assert url == "/cognitiveservices/v1"
    assert kwargs["headers"] == {
        "Ocp-Apim-Subscription-Key": "azure-secret",
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "retail-ai-kiosk",
    }
    ssml = kwargs["content"].decode("utf-8")
    assert f'xml:lang="{locale}"' in ssml
    assert f'<voice name="{voice}">' in ssml
    assert "Giá &lt; 20.000đ &amp; mới" in ssml


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "locale"),
    [("vi", "vi-VN"), ("en", "en-US"), ("zh", "zh-CN")],
)
async def test_preconnect_uses_the_language_specific_voices_endpoint(
    language: str, locale: str
) -> None:
    client = FakeAzureSpeechClient()
    service = AzureSpeechService(
        settings=Settings(azure_speech_key="azure-secret", azure_speech_region="southeastasia"),
        client=client,
    )

    await service.preconnect(language)

    assert client.get_calls == [(
        "/cognitiveservices/voices/list",
        {
            "params": {"language": locale},
            "headers": {"Ocp-Apim-Subscription-Key": "azure-secret"},
            "timeout": 5.0,
        },
    )]


@pytest.mark.asyncio
async def test_preconnect_rejects_invalid_voice_without_network_call() -> None:
    client = FakeAzureSpeechClient()
    service = AzureSpeechService(
        settings=Settings(azure_speech_key="azure-secret", azure_speech_region="southeastasia"),
        client=client,
    )

    with pytest.raises(AzureSpeechProviderError, match="voice is unavailable"):
        await service.preconnect("xx")

    assert client.get_calls == []


@pytest.mark.asyncio
async def test_missing_azure_key_or_region_fails_without_disclosing_secret() -> None:
    service = AzureSpeechService(
        settings=Settings(azure_speech_key="azure-secret", azure_speech_region=""),
        client=FakeAzureSpeechClient(),
    )

    with pytest.raises(AzureSpeechConfigurationError) as error:
        await service.synthesize("Xin chào")

    assert "azure-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_invalid_region_is_rejected_before_request() -> None:
    client = FakeAzureSpeechClient()
    service = AzureSpeechService(
        settings=Settings(azure_speech_key="azure-secret", azure_speech_region="region.example"),
        client=client,
    )

    with pytest.raises(AzureSpeechConfigurationError, match="region is invalid"):
        await service.synthesize("Xin chào")

    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_http_errors_are_safe_and_empty_audio_is_rejected() -> None:
    settings = Settings(azure_speech_key="azure-secret", azure_speech_region="southeastasia")
    throttled = AzureSpeechService(
        settings=settings,
        client=FakeAzureSpeechClient(FakeResponse(status_code=429)),
    )

    with pytest.raises(AzureSpeechProviderError, match="HTTP 429") as error:
        await throttled.synthesize("Xin chào")

    assert "azure-secret" not in str(error.value)

    empty = AzureSpeechService(
        settings=settings,
        client=FakeAzureSpeechClient(FakeResponse(content=b"")),
    )
    with pytest.raises(AzureSpeechProviderError, match="synthesis failed"):
        await empty.synthesize("Xin chào")


@pytest.mark.asyncio
async def test_non_audio_success_response_is_rejected() -> None:
    service = AzureSpeechService(
        settings=Settings(azure_speech_key="azure-secret", azure_speech_region="southeastasia"),
        client=FakeAzureSpeechClient(FakeResponse(content_type="application/json")),
    )

    with pytest.raises(AzureSpeechProviderError, match="synthesis failed"):
        await service.synthesize("Xin chào")


@pytest.mark.asyncio
async def test_open_stream_keeps_audio_incremental_and_closes_upstream() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"first-second")

    async with httpx.AsyncClient(
        base_url="https://southeastasia.tts.speech.microsoft.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        service = AzureSpeechService(
            settings=Settings(azure_speech_key="azure-secret", azure_speech_region="southeastasia"),
            client=client,
        )
        stream = await service.open_stream("Xin chào")
        chunks = [chunk async for chunk in stream.iter_bytes()]
        await stream.aclose()

    assert b"".join(chunks) == b"first-second"
    assert chunks
    assert seen["request"].headers["Ocp-Apim-Subscription-Key"] == "azure-secret"
    assert stream.voice_id == "vi-VN-HoaiMyNeural"
