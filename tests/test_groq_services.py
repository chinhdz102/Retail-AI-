import io
import json
import math
import random
import wave
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from backend.config import Settings
from backend.services.groq import (
    MAX_GROQ_STT_PROMPT_BYTES,
    GroqAudioInputError,
    GroqConfigurationError,
    GroqProviderError,
    GroqService,
    _is_impulsive_non_speech,
    _transcription_prompt,
    _wav_signal_quality,
)
from backend.services.speech_activity import SpeechActivityResult


@pytest.fixture(autouse=True)
def acoustic_gate_for_provider_unit_tests(monkeypatch):
    # These tests isolate provider retries/metadata and the earlier PCM heuristic.
    # Fake bytes and synthesized harmonic tones are not human-speech fixtures.
    # Real decoding/ONNX and the service's no-speech boundary are tested separately
    # in test_speech_activity.py and the cached-corpus replay diagnostic.
    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity",
        lambda _content: SpeechActivityResult(True, {}),
    )


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
        return SimpleNamespace(
            text="  Cho tôi biết giá Coca Zero.  ",
            segments=[
                SimpleNamespace(
                    start=0.0,
                    end=1.6,
                    no_speech_prob=0.001,
                    avg_logprob=-0.12,
                    compression_ratio=0.8,
                )
            ],
        )


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions())
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())


@pytest.mark.asyncio
async def test_invalid_audio_is_not_retried_as_a_provider_outage():
    calls = []

    class InvalidMedia(Exception):
        status_code = 400
        body = {"code": "invalid_media_file"}

    async def invalid(**kwargs):
        calls.append(kwargs)
        raise InvalidMedia()

    client = FakeClient()
    client.audio.transcriptions.create = invalid
    service = GroqService(Settings(_env_file=None, groq_api_key="test"), client=client)
    with pytest.raises(GroqAudioInputError):
        await service.transcribe("broken.wav", b"invalid data", "audio/wav")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_invalid_audio_message_without_provider_code_is_classified_as_input_error():
    calls = []

    class InvalidMediaMessage(Exception):
        status_code = 422
        body = {"error": "The uploaded audio could not be decoded"}

    async def invalid(**kwargs):
        calls.append(kwargs)
        raise InvalidMediaMessage()

    client = FakeClient()
    client.audio.transcriptions.create = invalid
    service = GroqService(Settings(_env_file=None, groq_api_key="test"), client=client)
    with pytest.raises(GroqAudioInputError):
        await service.transcribe("broken.webm", b"invalid data", "audio/webm")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
async def test_chat_never_publishes_incomplete_provider_output(finish_reason):
    client = FakeClient()

    async def incomplete(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content="A plausible but incomplete answer"),
        )])

    client.chat.completions.create = incomplete
    service = GroqService(Settings(_env_file=None, groq_api_key="test-key",
                                   groq_fallback_model="", groq_fallback_model_2=""),
                          client=client)
    with pytest.raises(GroqProviderError) as error:
        await service.chat([{"role": "user", "content": "test"}])
    assert error.value.fallback_trace[0]["error_type"] == "ValueError"


class FallbackChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("primary model unavailable")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="Xin chào từ model dự phòng."))
            ]
        )


class FallbackClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FallbackChatCompletions())
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())


@pytest.mark.asyncio
async def test_chat_rate_limit_cooldown_skips_retry_until_expiry(monkeypatch):
    from backend.services import groq as groq_module

    now = 100.0
    monkeypatch.setattr(groq_module, "perf_counter", lambda: now)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary":
            error = RuntimeError("quota")
            error.status_code = 429
            error.response = SimpleNamespace(status_code=429, headers={"retry-after": "30"})
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="Complete reply"),
        )])

    client = FakeClient()
    client.chat.completions.create = create
    service = GroqService(Settings(_env_file=None, groq_api_key="test", groq_model="primary",
                                   groq_fallback_model="fallback", groq_fallback_model_2=""),
                          client=client)
    await service.chat([{"role": "user", "content": "one"}])
    result = await service.chat([{"role": "user", "content": "two"}])
    assert calls == ["primary", "fallback", "fallback"]
    assert result.fallback_trace[0]["status"] == "cooldown"
    now = 131.0
    await service.chat([{"role": "user", "content": "three"}])
    assert calls[-2:] == ["primary", "fallback"]


@pytest.mark.asyncio
async def test_chat_rate_limit_cooldown_honors_retry_after_over_five_minutes(monkeypatch):
    """Provider retry-after values above the historical 300s cap remain authoritative."""
    from backend.services import groq as groq_module

    now = 1_000.0
    monkeypatch.setattr(groq_module, "perf_counter", lambda: now)
    calls: list[str] = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary":
            error = RuntimeError("quota")
            error.status_code = 429
            error.response = SimpleNamespace(status_code=429, headers={"retry-after": "754"})
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="Complete reply"),
        )])

    client = FakeClient()
    client.chat.completions.create = create
    service = GroqService(Settings(
        _env_file=None,
        groq_api_key="test",
        groq_model="primary",
        groq_fallback_model="fallback",
        groq_fallback_model_2="",
    ), client=client)

    await service.chat([{"role": "user", "content": "first"}])
    now = 1_301.0
    result = await service.chat([{"role": "user", "content": "still waiting"}])
    assert calls == ["primary", "fallback", "fallback"]
    assert result.fallback_trace[0] == {"model": "primary", "status": "cooldown"}

    now = 1_755.0
    await service.chat([{"role": "user", "content": "retry now"}])
    assert calls[-2:] == ["primary", "fallback"]


@pytest.mark.parametrize(("headers", "expected"), [
    ({"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "1h2m3.5s"}, 3723.5),
    ({"retry-after": "2", "x-ratelimit-remaining-requests": "0",
      "x-ratelimit-reset-requests": "23h59m"}, 86340),
    ({"retry-after": "3", "x-ratelimit-remaining-tokens": "100",
      "x-ratelimit-reset-tokens": "1h"}, 3),
    ({"retry-after": "nan", "x-ratelimit-remaining-tokens": "nan",
      "x-ratelimit-reset-tokens": "1h"}, 5),
    ({"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "invalid"}, 5),
    ({"retry-after": "999999"}, 86400),
    ({"retry-after": "1500ms"}, 1.5),
])
def test_rate_limit_waits_respect_only_exhausted_provider_buckets(headers, expected):
    from backend.services.groq import _rate_limit_retry_seconds
    assert _rate_limit_retry_seconds(headers) == expected


def test_rate_limit_waits_accept_http_date_retry_after(monkeypatch):
    from datetime import UTC, datetime

    from backend.services import groq as groq_module

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(groq_module, "datetime", FixedDatetime)
    assert groq_module._rate_limit_retry_seconds({
        "retry-after": "Tue, 03 Nov 2026 12:02:00 GMT",
    }) == 120


@pytest.mark.asyncio
async def test_token_reset_duration_stops_repeated_doomed_requests(monkeypatch):
    from backend.services import groq as groq_module
    now = 100.0
    monkeypatch.setattr(groq_module, "perf_counter", lambda: now)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary":
            error = RuntimeError("quota")
            error.status_code = 429
            error.response = SimpleNamespace(status_code=429, headers={
                "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "1h",
            })
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="Complete reply"),
        )])

    client = FakeClient()
    client.chat.completions.create = create
    service = GroqService(Settings(_env_file=None, groq_api_key="test", groq_model="primary",
                                   groq_fallback_model="fallback", groq_fallback_model_2=""),
                          client=client)
    await service.chat([{"role": "user", "content": "one"}])
    now = 110.0
    result = await service.chat([{"role": "user", "content": "two"}])
    assert calls == ["primary", "fallback", "fallback"]
    assert result.fallback_trace[0]["status"] == "cooldown"
    now = 3701.0
    await service.chat([{"role": "user", "content": "three"}])
    assert calls[-2:] == ["primary", "fallback"]


class RecoverableDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(max_length=10)
    count: int
    eligible: bool
    category: Literal["food", "other"]
    required_size: str | None = None


def strict_decision_validator(text: str) -> bool:
    RecoverableDecision.model_validate_json(text, strict=True)
    return True


def schema_recovery_service(generation, *, body_shape="top", code="json_validate_failed"):
    calls = []
    body = {"code": code, "failed_generation": generation,
            "message": "untrusted customer text must not be logged"}

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary":
            error = RuntimeError("untrusted provider error")
            error.status_code = 400
            error.body = {"error": body} if body_shape == "nested" else body
            if body_shape == "response":
                error.body = None
                error.response = SimpleNamespace(status_code=400, json=lambda: body, headers={})
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="fallback output"),
        )])

    client = FakeClient()
    client.chat.completions.create = create
    return GroqService(Settings(
        _env_file=None, groq_api_key="test", groq_model="primary",
        groq_fallback_model="fallback", groq_fallback_model_2="",
    ), client=client), calls


@pytest.mark.asyncio
@pytest.mark.parametrize("body_shape", ["top", "nested", "response"])
async def test_strict_schema_recovers_only_locally_valid_default_omission(body_shape, caplog):
    generation = json.dumps({"label": "valid", "count": 1, "eligible": True, "category": "food"})
    service, calls = schema_recovery_service(generation, body_shape=body_shape)
    result = await service.structured_chat(
        [], name="Decision", schema=RecoverableDecision.model_json_schema(),
        local_json_validator=strict_decision_validator,
    )
    assert calls == ["primary"]
    assert str(result) == generation
    assert RecoverableDecision.model_validate_json(result, strict=True).required_size is None
    assert result.model == "primary" and result.usage == {} and result.finish_reason is None
    assert result.fallback_trace[0]["status"] == "success"
    assert result.fallback_trace[0]["recovery"] == "local-default-validation"
    assert "failed_generation" not in str(result.fallback_trace)
    assert "untrusted customer" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [
    '{"label":"too long label","count":1,"eligible":true,"category":"food"}',
    '{"label":"valid","count":1,"eligible":true,"category":"food","extra":true}',
    '{"count":1,"eligible":true,"category":"food"}',
    '{"label":"valid","count":"1","eligible":true,"category":"food"}',
    '{"label":"valid","count":1,"eligible":"true","category":"food"}',
    '{"label":"valid","count":1,"eligible":true,"category":"invented"}',
    '{"label":"valid","count":1,"eligible":true,"category":"food","required_size":12}',
    '{invalid json', '[]', 'null',
])
async def test_failed_generation_invalid_fields_preserve_fallback(generation):
    service, calls = schema_recovery_service(generation)
    result = await service.structured_chat(
        [], name="Decision", schema=RecoverableDecision.model_json_schema(),
        local_json_validator=strict_decision_validator,
    )
    assert calls == ["primary", "fallback"]
    assert result == "fallback output"
    assert result.fallback_trace[0]["status"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "absent", "nonstrict", "false", "raises", "truthy", "wrong_code", "no_validator", "tools",
])
async def test_schema_recovery_requires_explicit_strict_validation(mode):
    generation = '{"label":"valid","count":1,"eligible":true,"category":"food"}'
    service, calls = schema_recovery_service(
        generation, code="tool_use_failed" if mode == "wrong_code" else "json_validate_failed",
    )
    def validator(text):
        if mode == "raises":
            raise ValueError("rejected")
        return False if mode == "false" else 1 if mode == "truthy" else True

    response_format = None if mode in {"absent", "tools"} else {
        "type": "json_schema", "json_schema": {"strict": mode != "nonstrict"},
    }
    result = await service.chat(
        [], response_format=response_format,
        local_json_validator=None if mode == "no_validator" else validator,
        tools=[{"type": "function", "function": {"name": "action"}}] if mode == "tools" else None,
    )
    assert calls == ["primary", "fallback"]
    assert result == "fallback output"


def test_stt_defaults_to_accuracy_first_whisper_model() -> None:
    settings = Settings(_env_file=None, groq_api_key="test-key")

    assert settings.groq_stt_model == "whisper-large-v3"
    assert settings.groq_stt_fallback_model == "whisper-large-v3-turbo"


class FallbackTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("primary transcription model unavailable")
        return SimpleNamespace(
            text="Coca còn mười sáu lon.",
            segments=[
                SimpleNamespace(
                    start=0.0,
                    end=1.5,
                    no_speech_prob=0.01,
                    avg_logprob=-0.2,
                    compression_ratio=0.9,
                )
            ],
        )


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
async def test_shopping_mission_tool_call_has_room_for_structured_plan_arguments() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_model="qwen/qwen3.8-27b")
    service = GroqService(settings=settings, client=client)
    tool_choice = {
        "type": "function",
        "function": {"name": "plan_shopping_mission"},
    }

    await service.chat(
        [{"role": "user", "content": "Chuẩn bị buổi gặp mặt."}],
        tools=[
            {
                "type": "function",
                "function": {"name": "plan_shopping_mission", "parameters": {}},
            }
        ],
        tool_choice=tool_choice,
    )

    assert client.chat.completions.calls[0]["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_other_forced_tool_calls_keep_the_existing_token_limit() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_model="qwen/qwen3.8-27b"),
        client=client,
    )

    await service.chat(
        [{"role": "user", "content": "Coca Zero giá bao nhiêu?"}],
        tools=[{"type": "function", "function": {"name": "get_product_snapshot"}}],
        tool_choice={"type": "function", "function": {"name": "get_product_snapshot"}},
    )

    assert client.chat.completions.calls[0]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_read_only_structured_output_uses_constrained_json_without_action_tools():
    client = FakeClient()
    service = GroqService(Settings(_env_file=None, groq_api_key="test"), client=client)
    schema = {"type": "object", "properties": {"text": {"type": "string"}},
              "required": ["text"], "additionalProperties": False}
    await service.structured_chat(
        [{"role": "user", "content": "Tư vấn giúp mình"}],
        name="Advice", schema=schema, max_tokens=1800,
    )
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "Advice", "strict": True, "schema": schema,
    }}
    assert call["reasoning_effort"] == "low"
    assert "tools" not in call and "tool_choice" not in call
    assert call["max_tokens"] == 1800


@pytest.mark.asyncio
async def test_structured_output_cannot_accidentally_invoke_action_tools():
    client = FakeClient()
    service = GroqService(Settings(_env_file=None, groq_api_key="test"), client=client)
    with pytest.raises(ValueError, match="cannot be combined"):
        await service.chat([], response_format={"type": "json_schema"},
                           tools=[{"type": "function", "function": {"name": "add_to_cart"}}])
    assert not client.chat.completions.calls


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
    assert result.fallback_trace == [
        {"model": "qwen/primary-model", "status": "error", "error_type": "RuntimeError"},
        {"model": "qwen/fallback-model", "status": "success"},
    ]
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
    assert call["response_format"] == "verbose_json"
    assert call["timestamp_granularities"] == ["segment"]
    assert call["temperature"] == 0
    assert "Tiếng Việt giao tiếp tự nhiên tại cửa hàng" in call["prompt"]
    assert "giọng Bắc, Trung, Nam" in call["prompt"]
    assert "đồ ăn vặt" in call["prompt"]
    assert "hông, hem, mô, chi, nhiêu" in call["prompt"]
    assert "chai, lon, hộp, gói, lốc, thùng" in call["prompt"]
    assert "ml, lít" in call["prompt"]
    assert "Coca Zero" not in call["prompt"]
    assert "Vinamilk" not in call["prompt"]
    assert "không tự chèn hoặc đoán tên" in call["prompt"]
    assert "không đoán" in call["prompt"].casefold()
    assert "không tự thêm từ" not in call["prompt"]


@pytest.mark.asyncio
async def test_transcribe_adds_active_catalog_vocabulary_to_prompt() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3"),
        client=client,
    )

    await service.transcribe(
        "voice.wav",
        b"audio",
        "audio/wav",
        vocabulary=("HUGGIES", "SUNLIGHT", "Lock&Lock", "Bánh Snack", "P/S"),
    )

    prompt = client.audio.transcriptions.calls[0]["prompt"]
    assert "HUGGIES" in prompt
    assert "SUNLIGHT" in prompt
    assert "Lock&Lock" in prompt
    assert "Bánh Snack" in prompt
    assert "P/S" in prompt


def test_default_vietnamese_stt_prompt_does_not_preseed_brand_names() -> None:
    prompt = _transcription_prompt("vi")

    assert "Coca-Cola" not in prompt
    assert "Coca Zero" not in prompt
    assert "Vinamilk" not in prompt
    assert "giọng Bắc, Trung, Nam" in prompt
    assert "không tự chèn hoặc đoán tên" in prompt
    assert "P S" not in prompt
    assert "Chép sát lời" in prompt
    assert "Không nghe rõ lời thì để trống" in prompt


@pytest.mark.asyncio
async def test_transcribe_keeps_vietnamese_catalog_prompt_under_groq_byte_limit() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3"),
        client=client,
    )
    vocabulary = tuple(
        f"Sản phẩm chăm sóc da dành cho gia đình số {index} rất tốt"
        for index in range(1, 33)
    )

    await service.transcribe(
        "voice.wav",
        b"audio",
        "audio/wav",
        vocabulary=vocabulary,
    )

    prompt = client.audio.transcriptions.calls[0]["prompt"]
    assert len(prompt.encode("utf-8")) <= MAX_GROQ_STT_PROMPT_BYTES
    assert vocabulary[0][:48] in prompt
    assert "Catalog:" in prompt or "catalog" in prompt.casefold()
    assert vocabulary[-1] not in prompt


def _pcm_wav(samples: bytes, *, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples)
    return buffer.getvalue()


def _tone_pcm(duration_seconds: float = 3.0, amplitude: int = 500) -> bytes:
    samples = bytearray()
    for index in range(round(duration_seconds * 16_000)):
        value = amplitude * math.sin(2 * math.pi * 440 * index / 16_000)
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    return bytes(samples)


def _quiet_harmonic_pcm() -> bytes:
    samples = bytearray()
    for index in range(16_000):
        time_seconds = index / 16_000
        envelope = 0.45 + 0.55 * abs(math.sin(2 * math.pi * 2.2 * time_seconds))
        value = (
            500
            * envelope
            * sum(
                harmonic * math.sin(2 * math.pi * 155 * multiple * time_seconds)
                for multiple, harmonic in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.2))
            )
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    return bytes(samples)


def _vad_padded_quiet_harmonic_pcm(scale: float = 0.2) -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(0.45 * sample_rate))
    for index in range(sample_rate):
        time_seconds = index / sample_rate
        envelope = 0.45 + 0.55 * abs(math.sin(2 * math.pi * 2.2 * time_seconds))
        value = (
            500
            * scale
            * envelope
            * sum(
                harmonic * math.sin(2 * math.pi * 155 * multiple * time_seconds)
                for multiple, harmonic in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.2))
            )
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    samples.extend(b"\x00\x00" * round(1.05 * sample_rate))
    return bytes(samples)


def _room_noise_pcm(
    duration_seconds: float = 3.0,
    *,
    leading_silence_seconds: float = 0.0,
    trailing_silence_seconds: float = 0.0,
    scale: float = 1.0,
) -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(leading_silence_seconds * sample_rate))
    previous = 0.0
    generator = random.Random(2407)
    for index in range(round(duration_seconds * sample_rate)):
        time_seconds = index / sample_rate
        previous = 0.94 * previous + 0.06 * generator.uniform(-1.0, 1.0)
        value = (0.025 * previous + 0.003 * math.sin(2 * math.pi * 120 * time_seconds)) * scale
        samples.extend(round(value * 32767).to_bytes(2, "little", signed=True))
    samples.extend(b"\x00\x00" * round(trailing_silence_seconds * sample_rate))
    return bytes(samples)


def _loud_voiced_pcm(duration_seconds: float = 3.0) -> bytes:
    sample_rate = 16_000
    samples = bytearray()
    for index in range(round(duration_seconds * sample_rate)):
        time_seconds = index / sample_rate
        envelope = 0.94 + 0.06 * math.sin(2 * math.pi * 2.2 * time_seconds)
        value = 6_500 * envelope * sum(
            strength * math.sin(2 * math.pi * 155 * harmonic * time_seconds)
            for harmonic, strength in ((1, 1.0), (2, 0.45), (3, 0.2))
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    return bytes(samples)


@pytest.mark.asyncio
async def test_transcribe_rejects_digital_silence_before_calling_whisper() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe("silence.wav", _pcm_wav(b"\x00\x00" * 16_000), "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "near_silence",
        }
    ]
    assert result.quality["audio_duration_seconds"] == 1.0
    assert client.audio.transcriptions.calls == []


def _click_pcm(duration_seconds: float = 2.0, pulse_seconds: float = 0.01) -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(duration_seconds * sample_rate))
    start = round(0.5 * sample_rate) * 2
    pulse = b"\x00\x78" * round(pulse_seconds * sample_rate)
    samples[start : start + len(pulse)] = pulse
    return bytes(samples)


def _short_voiced_pcm() -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(0.8 * sample_rate))
    for index in range(round(0.25 * sample_rate)):
        seconds = index / sample_rate
        value = 2_500 * sum(
            strength * math.sin(2 * math.pi * 155 * harmonic * seconds)
            for harmonic, strength in ((1, 1.0), (2, 0.4), (3, 0.2))
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    samples.extend(b"\x00\x00" * round(1.4 * sample_rate))
    return bytes(samples)


@pytest.mark.asyncio
@pytest.mark.parametrize("pulse_seconds", [0.0001, 0.002, 0.01, 0.03])
async def test_transcribe_rejects_sparse_click_transients_before_whisper(
    pulse_seconds: float,
) -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe(
        "mic-click.wav",
        _pcm_wav(_click_pcm(pulse_seconds=pulse_seconds)),
        "audio/wav",
    )

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "impulsive_non_speech",
        }
    ]
    assert result.quality["waveform_active_frame_ratio"] <= 0.15
    assert result.quality["waveform_active_frame_count"] < 3
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
async def test_transcribe_keeps_a_short_voiced_word_with_vad_padding() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )
    signal_quality = _wav_signal_quality(_pcm_wav(_short_voiced_pcm()))

    result = await service.transcribe(
        "short-word.wav",
        _pcm_wav(_short_voiced_pcm()),
        "audio/wav",
    )

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert signal_quality is not None
    assert signal_quality["waveform_active_frame_count"] == 3
    assert signal_quality["waveform_periodicity_mean"] >= 0.58
    assert _is_impulsive_non_speech(signal_quality) is False
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_keeps_quiet_pcm_for_whisper_instead_of_rejecting_it() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )
    result = await service.transcribe("quiet.wav", _pcm_wav(_quiet_harmonic_pcm()), "audio/wav")

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_keeps_low_volume_voiced_segment_with_vad_padding() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )

    result = await service.transcribe(
        "quiet-voice.wav",
        _pcm_wav(_vad_padded_quiet_harmonic_pcm()),
        "audio/wav",
    )

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "duration_seconds",
        "leading_silence_seconds",
        "trailing_silence_seconds",
        "scale",
    ),
    [
        (3.0, 0.0, 0.0, 1.0),
        (0.8, 0.45, 1.05, 1.0),
        (1.5, 0.45, 1.05, 1.0),
        (3.0, 0.0, 0.0, 8.0),
    ],
)
async def test_transcribe_rejects_low_level_room_noise_before_whisper(
    duration_seconds: float,
    leading_silence_seconds: float,
    trailing_silence_seconds: float,
    scale: float,
) -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe(
        "room-noise.wav",
        _pcm_wav(
            _room_noise_pcm(
                duration_seconds,
                leading_silence_seconds=leading_silence_seconds,
                trailing_silence_seconds=trailing_silence_seconds,
                scale=scale,
            )
        ),
        "audio/wav",
    )

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "low_level_non_speech",
        }
    ]
    assert result.quality["waveform_voiced_frame_ratio"] < 0.15
    assert result.quality["waveform_periodicity_mean"] < 0.58
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scale", [20.0, 40.0])
async def test_transcribe_rejects_loud_steady_room_noise_before_whisper(scale: float) -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe(
        "loud-room-noise.wav",
        _pcm_wav(_room_noise_pcm(3.0, scale=scale)),
        "audio/wav",
    )

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "steady_unvoiced_noise",
        }
    ]
    assert result.quality["waveform_rms"] > 0.03
    assert result.quality["waveform_envelope_cv"] <= 0.15
    assert result.quality["waveform_voiced_frame_ratio"] < 0.15
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
async def test_transcribe_keeps_loud_voiced_audio_with_a_steady_envelope() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )

    result = await service.transcribe(
        "voiced.wav",
        _pcm_wav(_loud_voiced_pcm()),
        "audio/wav",
    )

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_rejects_sustained_tone_before_calling_whisper() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe("tone.wav", _pcm_wav(_tone_pcm()), "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "steady_tone",
        }
    ]
    assert result.quality["waveform_crest_factor"] == pytest.approx(2**0.5, abs=0.01)
    assert result.quality["waveform_envelope_cv"] < 0.01
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
async def test_transcribe_rejects_whisper_text_from_silence_metadata() -> None:
    class SilenceTranscriptions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=2.0,
                        no_speech_prob=0.65,
                        avg_logprob=-0.54,
                        compression_ratio=0.98,
                    )
                ],
            )

    transcriptions = SilenceTranscriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe("silence.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.model == "whisper-large-v3"
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "no_speech",
        }
    ]
    assert [call["model"] for call in transcriptions.calls] == ["whisper-large-v3"]


@pytest.mark.asyncio
async def test_transcribe_rejects_non_speech_tone_hallucination_with_low_log_probability() -> None:
    class ToneTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=5.0,
                        no_speech_prob=0.0,
                        avg_logprob=-0.2,
                        compression_ratio=2.8,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=ToneTranscriptions())),
    )

    result = await service.transcribe("tone.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace[0]["reason"] == "high_compression"


@pytest.mark.asyncio
async def test_transcribe_rejects_repeated_phrase_loop_even_with_good_whisper_scores() -> None:
    class RepeatingTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text=(
                    "Cảm ơn các bạn đã theo dõi và ủng thông, "
                    "và hạn thấy nhiều thông, và hạn thấy nhiều thông."
                ),
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=4.0,
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                        compression_ratio=1.1,
                    )
                ],
            )

    transcriptions = RepeatingTranscriptions()
    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions)),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace[0]["reason"] == "repeated_phrase"
    assert len(result.quality) == 5


@pytest.mark.asyncio
async def test_transcribe_rejects_missing_verbose_quality_metadata() -> None:
    class MetadataMissingTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(text="Có nước suối không ạ?")

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(
            audio=SimpleNamespace(transcriptions=MetadataMissingTranscriptions())
        ),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace[0]["reason"] == "missing_quality_metadata"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_text", ["", "   "])
async def test_transcribe_does_not_retry_empty_speech_as_a_provider_failure(
    empty_text: str,
) -> None:
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        return SimpleNamespace(text=empty_text, segments=[])

    service = GroqService(
        settings=Settings(
            _env_file=None, groq_api_key="test-key", groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))),
    )
    result = await service.transcribe("noise.webm", b"audio", "audio/webm")
    assert result == ""
    assert result.speech_detected is False
    assert calls == ["whisper-large-v3"]
    assert result.fallback_trace == [
        {"model": "whisper-large-v3", "status": "rejected", "reason": "empty_transcript"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_metadata",
    [
        {"no_speech_prob": None},
        {"avg_logprob": None},
        {"compression_ratio": None},
        {"start": float("nan")},
        {"end": float("inf")},
        {"end": -1.0},
        {"no_speech_prob": float("nan")},
    ],
)
async def test_transcribe_rejects_partially_unscored_or_invalid_segments(bad_metadata) -> None:
    good = dict(start=0.0, end=1.0, no_speech_prob=0.01, avg_logprob=-0.2, compression_ratio=0.9)
    bad = dict(start=1.0, end=2.0, no_speech_prob=0.01, avg_logprob=-0.2, compression_ratio=0.9)
    bad.update(bad_metadata)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        return SimpleNamespace(
            text="Có nước suối. Đây là phần không được chấm điểm.",
            segments=[SimpleNamespace(**good), SimpleNamespace(**bad)],
        )

    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))),
    )
    result = await service.transcribe("voice.webm", b"audio", "audio/webm")
    assert result == ""
    assert result.speech_detected is False
    assert len(calls) == 1
    assert result.fallback_trace[0]["reason"] in {
        "missing_quality_metadata", "invalid_quality_metadata"
    }
    assert all(math.isfinite(value) for value in result.quality.values())


@pytest.mark.asyncio
async def test_transcribe_keeps_natural_repetition_in_customer_request() -> None:
    class NaturalRepetitionTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Mua một chai nước, mua thêm hai chai nước.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=2.0,
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                        compression_ratio=0.9,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(
            audio=SimpleNamespace(transcriptions=NaturalRepetitionTranscriptions())
        ),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == "Mua một chai nước, mua thêm hai chai nước."
    assert result.speech_detected is True


@pytest.mark.asyncio
async def test_transcribe_keeps_clear_short_vietnamese_question() -> None:
    class ClearTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Có Coca Zero không ạ?",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        no_speech_prob=0.0005,
                        avg_logprob=-0.402,
                        compression_ratio=0.74,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=ClearTranscriptions())),
    )

    result = await service.transcribe("speech.wav", b"audio", "audio/wav")

    assert result == "Có Coca Zero không ạ?"
    assert result.speech_detected is True


@pytest.mark.asyncio
async def test_transcribe_does_not_let_turbo_override_primary_low_quality_rejection() -> None:
    class QualityFallbackTranscriptions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model"] == "whisper-large-v3":
                return SimpleNamespace(
                    text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                    segments=[
                        SimpleNamespace(
                            start=0.0,
                            end=2.0,
                            no_speech_prob=0.72,
                            avg_logprob=-0.2,
                            compression_ratio=0.9,
                        )
                    ],
                )
            return SimpleNamespace(
                text="Có Coca Zero không ạ?",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        no_speech_prob=0.001,
                        avg_logprob=-0.2,
                        compression_ratio=0.8,
                    )
                ],
            )

    transcriptions = QualityFallbackTranscriptions()
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions)),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.model == "whisper-large-v3"
    assert [call["model"] for call in transcriptions.calls] == ["whisper-large-v3"]
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "no_speech",
        }
    ]


@pytest.mark.asyncio
async def test_transcribe_returns_no_speech_when_primary_model_is_low_quality() -> None:
    class RejectedTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=2.0,
                        no_speech_prob=0.7,
                        avg_logprob=-0.2,
                        compression_ratio=0.9,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=RejectedTranscriptions())),
    )

    result = await service.transcribe("silence.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.model == "whisper-large-v3"
    assert len(result.fallback_trace) == 1
    assert all(attempt["status"] == "rejected" for attempt in result.fallback_trace)


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
async def test_transcribe_uses_english_prompt_when_selected() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3-turbo")
    service = GroqService(settings=settings, client=client)

    await service.transcribe("voice.webm", b"audio", "audio/webm", language="en")

    call = client.audio.transcriptions.calls[0]
    assert call["language"] == "en"
    assert "retail conversation in English" in call["prompt"]
    assert "Do not translate" in call["prompt"]


@pytest.mark.asyncio
async def test_transcription_fallback_reports_the_model_that_replied() -> None:
    transcriptions = FallbackTranscriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    settings = Settings(
        groq_api_key="test-key",
        groq_stt_model="whisper-large-v3-turbo",
        groq_stt_fallback_model="whisper-large-v3",
    )
    service = GroqService(settings=settings, client=client)

    result = await service.transcribe("voice.webm", b"audio", "audio/webm")

    assert result == "Coca còn mười sáu lon."
    assert result.model == "whisper-large-v3"
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3-turbo",
            "status": "error",
            "error_type": "RuntimeError",
        },
        {"model": "whisper-large-v3", "status": "success"},
    ]


@pytest.mark.asyncio
async def test_missing_key_fails_without_exposing_secret() -> None:
    service = GroqService(settings=Settings(groq_api_key=""), client=None)

    with pytest.raises(GroqConfigurationError) as error:
        await service.chat([])

    assert "test-key" not in str(error.value)
    assert "gsk_" not in str(error.value)


class RawChatResponse:
    headers = {
        "x-ratelimit-remaining-requests": "23",
        "x-ratelimit-remaining-tokens": "7900",
    }

    async def parse(self):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5, total_tokens=16),
        )


class RawChatCompletions:
    @property
    def with_raw_response(self):
        return self

    async def create(self, **kwargs):
        return RawChatResponse()


class RateLimitFailure(RuntimeError):
    def __init__(self) -> None:
        self.status_code = 429
        self.response = SimpleNamespace(headers={"retry-after": "2"})


class RateLimitedRawChatCompletions:
    @property
    def with_raw_response(self):
        return self

    async def create(self, **kwargs):
        raise RateLimitFailure()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code,http_status,body_source",
    [
        ("tool_use_failed", 400, "top_level"),
        ("rate_limit_exceeded", 429, "nested"),
        ("invalid_api_key", 401, "response"),
    ],
)
async def test_chat_provider_trace_projects_allowlisted_code_without_raw_body(
    error_code, http_status, body_source, caplog,
) -> None:
    provider_body = {
        "code": error_code,
        "message": "customer secret and failed_generation must never be copied",
        "failed_generation": "api-key-like-content",
    }

    class ToolUseFailure(RuntimeError):
        status_code = http_status
        body = {"error": provider_body} if body_source == "nested" else provider_body

    failure = ToolUseFailure("provider message contains customer secret")
    if body_source == "response":
        failure.body = None
        failure.status_code = None
        failure.response = SimpleNamespace(
            status_code=http_status, json=lambda: provider_body,
            headers={"authorization": "test-key"},
        )

    class FailingCompletions:
        async def create(self, **kwargs):
            raise failure

    client = FakeClient()
    client.chat = SimpleNamespace(completions=FailingCompletions())
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_fallback_model="",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    with pytest.raises(GroqProviderError) as error:
        await service.chat([{"role": "user", "content": "customer secret"}])

    trace = error.value.fallback_trace
    assert trace[0]["status_code"] == http_status
    assert trace[0]["error_code"] == error_code
    assert "message" not in trace[0]
    assert "failed_generation" not in trace[0]
    assert "customer secret" not in repr(trace)
    assert "api-key-like-content" not in repr(trace)
    assert "test-key" not in repr(trace)
    assert "customer secret" not in caplog.text
    assert "api-key-like-content" not in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.asyncio
async def test_chat_provider_trace_drops_unknown_error_code_but_keeps_status() -> None:
    class UnknownFailure(RuntimeError):
        status_code = 500
        body = {"code": "internal_error", "message": "do not expose this"}

    class FailingCompletions:
        async def create(self, **kwargs):
            raise UnknownFailure()

    client = FakeClient()
    client.chat = SimpleNamespace(completions=FailingCompletions())
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_fallback_model="",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    with pytest.raises(GroqProviderError) as error:
        await service.chat([{"role": "user", "content": "hello"}])

    trace = error.value.fallback_trace[0]
    assert trace["status_code"] == 500
    assert "error_code" not in trace
    assert "internal_error" not in repr(trace)
    assert "do not expose this" not in repr(trace)


@pytest.mark.asyncio
async def test_chat_records_rate_limit_headers_and_token_usage() -> None:
    client = FakeClient()
    client.chat = SimpleNamespace(completions=RawChatCompletions())
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_fallback_model=""),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "total_tokens": 16,
    }
    assert result.fallback_trace[0]["rate_limit_headers"] == {
        "x-ratelimit-remaining-requests": "23",
        "x-ratelimit-remaining-tokens": "7900",
    }


@pytest.mark.asyncio
async def test_chat_provider_error_preserves_rate_limit_headers() -> None:
    client = FakeClient()
    client.chat = SimpleNamespace(completions=RateLimitedRawChatCompletions())
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_fallback_model="",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    with pytest.raises(GroqProviderError) as error:
        await service.chat([{"role": "user", "content": "hello"}])

    assert error.value.fallback_trace == [
        {
            "model": "qwen/qwen3.8-27b",
            "status": "error",
            "error_type": "RateLimitFailure",
            "status_code": 429,
            "rate_limit_headers": {"retry-after": "2"},
        }
    ]


class RotatingKeyCompletions:
    def __init__(self, failures_before_success: int) -> None:
        self.calls: list[dict] = []
        self.failures_before_success = failures_before_success

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures_before_success:
            raise RateLimitFailure()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Đã dùng key dự phòng."))]
        )


@pytest.mark.asyncio
async def test_chat_rotates_api_keys_after_quota_error_before_model_fallback() -> None:
    completions = RotatingKeyCompletions(failures_before_success=1)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_api_key_fallback_2="fallback-test-key-2",
            groq_model="qwen/primary-model",
            groq_fallback_model="openai/fallback-model",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result == "Đã dùng key dự phòng."
    assert result.model == "qwen/primary-model"
    assert [call["model"] for call in completions.calls] == [
        "qwen/primary-model",
        "qwen/primary-model",
    ]
    assert result.fallback_trace == [
        {
            "model": "qwen/primary-model",
            "status": "error",
            "error_type": "RateLimitFailure",
            "status_code": 429,
            "rate_limit_headers": {"retry-after": "2"},
            "credential_slot": "primary",
        },
        {
            "model": "qwen/primary-model",
            "status": "success",
            "credential_slot": "fallback_1",
        },
    ]
    assert "primary-test-key" not in repr(result.fallback_trace)
    assert "fallback-test-key-1" not in repr(result.fallback_trace)


def test_groq_builds_a_separate_client_for_each_configured_key(monkeypatch) -> None:
    from backend.services import groq as groq_module

    created: list[dict] = []

    class RecordingOpenAI:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)

    monkeypatch.setattr(groq_module, "AsyncOpenAI", RecordingOpenAI)
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
        )
    )
    primary_slot, primary_key = service.settings.groq_api_credentials[0]
    fallback_slot, fallback_key = service.settings.groq_api_credentials[1]

    primary_client = service._client_for_credential(primary_slot, primary_key)
    fallback_client = service._client_for_credential(fallback_slot, fallback_key)

    assert primary_client is not fallback_client
    assert [kwargs["api_key"] for kwargs in created] == [
        "primary-test-key",
        "fallback-test-key-1",
    ]
    assert all(kwargs["max_retries"] == 0 for kwargs in created)
    assert service._client_for_credential(primary_slot, primary_key) is primary_client


def test_settings_preserves_all_five_groq_credentials_in_failover_order() -> None:
    settings = Settings(
        groq_api_key="primary-test-key",
        groq_api_key_fallback_1="fallback-test-key-1",
        groq_api_key_fallback_2="fallback-test-key-2",
        groq_api_key_fallback_3="fallback-test-key-3",
        groq_api_key_fallback_4="fallback-test-key-4",
    )

    assert [slot for slot, _ in settings.groq_api_credentials] == [
        "primary",
        "fallback_1",
        "fallback_2",
        "fallback_3",
        "fallback_4",
    ]


@pytest.mark.asyncio
async def test_chat_uses_model_fallback_after_all_configured_keys_are_limited() -> None:
    completions = RotatingKeyCompletions(failures_before_success=3)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_api_key_fallback_2="fallback-test-key-2",
            groq_model="qwen/primary-model",
            groq_fallback_model="openai/fallback-model",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result.model == "openai/fallback-model"
    assert [call["model"] for call in completions.calls] == [
        "qwen/primary-model",
        "qwen/primary-model",
        "qwen/primary-model",
        "openai/fallback-model",
    ]
    assert [attempt.get("credential_slot") for attempt in result.fallback_trace] == [
        "primary",
        "fallback_1",
        "fallback_2",
        "primary",
    ]


@pytest.mark.asyncio
async def test_chat_tries_all_five_keys_before_falling_back_to_another_model() -> None:
    completions = RotatingKeyCompletions(failures_before_success=5)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_api_key_fallback_2="fallback-test-key-2",
            groq_api_key_fallback_3="fallback-test-key-3",
            groq_api_key_fallback_4="fallback-test-key-4",
            groq_model="qwen/primary-model",
            groq_fallback_model="openai/fallback-model",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result.model == "openai/fallback-model"
    assert [attempt.get("credential_slot") for attempt in result.fallback_trace] == [
        "primary",
        "fallback_1",
        "fallback_2",
        "fallback_3",
        "fallback_4",
        "primary",
    ]


class RotatingKeyTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RateLimitFailure()
        return SimpleNamespace(
            text="Nước suối còn hàng.",
            segments=[
                SimpleNamespace(
                    start=0.0,
                    end=1.2,
                    no_speech_prob=0.01,
                    avg_logprob=-0.2,
                    compression_ratio=0.9,
                )
            ],
        )


@pytest.mark.asyncio
async def test_transcribe_rotates_api_key_after_quota_error() -> None:
    transcriptions = RotatingKeyTranscriptions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeChatCompletions()),
        audio=SimpleNamespace(transcriptions=transcriptions),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_stt_model="whisper-large-v3-turbo",
            groq_stt_fallback_model="whisper-large-v3",
        ),
        client=client,
    )

    result = await service.transcribe("voice.webm", b"audio", "audio/webm")

    assert result == "Nước suối còn hàng."
    assert result.model == "whisper-large-v3-turbo"
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3-turbo",
            "status": "error",
            "error_type": "RateLimitFailure",
            "credential_slot": "primary",
        },
        {
            "model": "whisper-large-v3-turbo",
            "status": "success",
            "credential_slot": "fallback_1",
        },
    ]


@pytest.mark.asyncio
async def test_transcribe_round_robins_keys_and_skips_cooled_down_slot() -> None:
    calls = 0

    class PoolTranscriptions:
        async def create(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RateLimitFailure()
            return SimpleNamespace(
                text=f"Lượt {calls}.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=1.1,
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                        compression_ratio=0.9,
                    )
                ],
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeChatCompletions()),
        audio=SimpleNamespace(transcriptions=PoolTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    first = await service.transcribe("a.webm", b"audio", "audio/webm")
    second = await service.transcribe("b.webm", b"audio", "audio/webm")
    third = await service.transcribe("c.webm", b"audio", "audio/webm")
    fourth = await service.transcribe("d.webm", b"audio", "audio/webm")

    assert first.fallback_trace[0]["credential_slot"] == "primary"
    assert second.fallback_trace[0]["credential_slot"] == "fallback_1"
    # Call 3 hits RateLimitFailure on primary and succeeds on fallback_1
    assert third.fallback_trace[0]["status"] == "error"
    assert third.fallback_trace[0]["credential_slot"] == "primary"
    assert third.fallback_trace[1]["status"] == "success"
    assert third.fallback_trace[1]["credential_slot"] == "fallback_1"
    # Call 4 wraps back to primary, skips it via cooldown without an API call,
    # and succeeds on fallback_1
    assert fourth.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "cooldown",
            "credential_slot": "primary",
        },
        {
            "model": "whisper-large-v3",
            "status": "success",
            "credential_slot": "fallback_1",
        },
    ]
