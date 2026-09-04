from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.services.groq import GroqConfigurationError, GroqService


class FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Xin chào từ Qwen."))]
        )


class FakeTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="  Cho tôi biết giá Coca Zero.  ")


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions())
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())


class FallbackChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("primary model unavailable")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Xin chào từ model dự phòng.")
                )
            ]
        )


class FallbackClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FallbackChatCompletions())
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())


@pytest.mark.asyncio
async def test_chat_uses_configured_qwen_model() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_model="qwen/qwen3.8-27b")
    service = GroqService(settings=settings, client=client)

    result = await service.chat([{"role": "user", "content": "Xin chào"}])

    assert result == "Xin chào từ Qwen."
    assert result.model == "qwen/qwen3.8-27b"
    assert client.chat.completions.calls[0]["model"] == "qwen/qwen3.8-27b"
    assert client.chat.completions.calls[0]["reasoning_effort"] == "none"
    assert client.chat.completions.calls[0]["max_tokens"] == 80


@pytest.mark.asyncio
async def test_chat_reports_the_qwen_fallback_model_that_replied() -> None:
    client = FallbackClient()
    settings = Settings(
        groq_api_key="test-key",
        groq_model="qwen/primary-model",
        groq_fallback_model="qwen/fallback-model",
    )
    service = GroqService(settings=settings, client=client)

    result = await service.chat([{"role": "user", "content": "Xin chào"}])

    assert result == "Xin chào từ model dự phòng."
    assert result.model == "qwen/fallback-model"
    assert [call["model"] for call in client.chat.completions.calls] == [
        "qwen/primary-model",
        "qwen/fallback-model",
    ]


@pytest.mark.asyncio
async def test_transcribe_forces_vietnamese_whisper_prompt() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3-turbo")
    service = GroqService(settings=settings, client=client)

    result = await service.transcribe("voice.webm", b"audio", "audio/webm")

    assert result == "Cho tôi biết giá Coca Zero."
    call = client.audio.transcriptions.calls[0]
    assert call["model"] == "whisper-large-v3-turbo"
    assert call["language"] == "vi"
    assert "Coca" in call["prompt"]


@pytest.mark.asyncio
async def test_transcribe_uses_chinese_prompt_when_selected() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3-turbo")
    service = GroqService(settings=settings, client=client)

    await service.transcribe("voice.webm", b"audio", "audio/webm", language="zh")

    call = client.audio.transcriptions.calls[0]
    assert call["language"] == "zh"
    assert "可口可乐" in call["prompt"]


@pytest.mark.asyncio
async def test_missing_key_fails_without_exposing_secret() -> None:
    service = GroqService(settings=Settings(groq_api_key=""), client=None)

    with pytest.raises(GroqConfigurationError) as error:
        await service.chat([])

    assert "test-key" not in str(error.value)
    assert "gsk_" not in str(error.value)
