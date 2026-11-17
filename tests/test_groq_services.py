from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.services.groq import (
    GroqConfigurationError, GroqProviderError, GroqService, _retry_delay_seconds,
)


class FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Xin chào từ Qwen."))]
        )


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


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
    assert result.fallback_trace == [
        {"model": "qwen/primary-model", "credential_slot": 0, "status": "error", "error_type": "RuntimeError"},
        {"model": "qwen/fallback-model", "credential_slot": 0, "status": "success"},
    ]
    assert [call["model"] for call in client.chat.completions.calls] == [
        "qwen/primary-model",
        "qwen/fallback-model",
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
        self.response = SimpleNamespace(headers={"retry-after": "2"})


class RateLimitedRawChatCompletions:
    @property
    def with_raw_response(self):
        return self

    async def create(self, **kwargs):
        raise RateLimitFailure()


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
            "credential_slot": 0,
            "status": "error",
            "error_type": "RateLimitFailure",
            "rate_limit_headers": {"retry-after": "2"},
        }
    ]


@pytest.mark.asyncio
async def test_chat_tries_configured_secondary_credential_before_changing_model(monkeypatch):
    class Limited(FakeChatCompletions):
        async def create(self, **kwargs):
            error = RateLimitFailure()
            error.status_code = 429
            raise error

    primary, secondary = FakeClient(), FakeClient()
    primary.chat.completions = Limited()
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    result = await service.chat([{"role": "user", "content": "Mua hàng"}],
                                max_tokens=1000, reasoning_effort="low",
                                response_format={"type": "json_object"})
    assert result.model == service.settings.groq_model
    assert [a["status"] for a in result.fallback_trace] == ["error", "success"]
    assert secondary.chat.completions.calls[0]["max_tokens"] == 1000
    assert secondary.chat.completions.calls[0]["reasoning_effort"] == "low"


class StatusFailure(RateLimitFailure):
    def __init__(self, status):
        super().__init__()
        self.status_code = status


class ScriptedCompletions:
    def __init__(self, *statuses):
        self.statuses = iter(statuses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        status = next(self.statuses)
        if status != 200:
            raise StatusFailure(status)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok'))])


def scripted_client(*statuses):
    client = FakeClient()
    client.chat.completions = ScriptedCompletions(*statuses)
    return client


@pytest.mark.asyncio
async def test_all_keys_rate_limited_moves_to_fallback_model(monkeypatch):
    primary, secondary = scripted_client(429, 200), scripted_client(429)
    service = GroqService(Settings(groq_api_key='test-key'), client=primary)
    monkeypatch.setattr(service, '_clients', lambda _: [primary, secondary])
    result = await service.chat([{'role': 'user', 'content': 'Chọn giúp mình'}],
                                fallback_models=['openai/gpt-oss-120b'])
    assert result.model == 'openai/gpt-oss-120b'
    assert [r['credential_slot'] for r in result.fallback_trace] == [0, 1, 0]
    assert [r['status'] for r in result.fallback_trace] == ['error', 'error', 'success']
    assert primary.chat.completions.calls[-1]['reasoning_effort'] == 'low'


@pytest.mark.asyncio
async def test_rate_limited_pair_is_skipped_until_retry_after_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('backend.services.groq.time.monotonic', lambda: clock[0])
    primary, secondary = scripted_client(429, 200), scripted_client(200, 200)
    service = GroqService(Settings(groq_api_key='test-key'), client=primary)
    monkeypatch.setattr(service, '_clients', lambda _: [primary, secondary])
    await service.chat([], fallback_models=[])
    result = await service.chat([], fallback_models=[])
    assert len(primary.chat.completions.calls) == 1
    assert result.fallback_trace[0]['status'] == 'cooldown'
    assert result.fallback_trace[0]['retry_after_seconds'] == 2
    clock[0] = 103.0
    await service.chat([], fallback_models=[])
    assert len(primary.chat.completions.calls) == 2


@pytest.mark.asyncio
async def test_invalid_key_is_not_retried_on_each_model_in_same_request(monkeypatch):
    primary, secondary = scripted_client(401), scripted_client(429, 200)
    service = GroqService(Settings(groq_api_key='test-key'), client=primary)
    monkeypatch.setattr(service, '_clients', lambda _: [primary, secondary])
    result = await service.chat([], fallback_models=['openai/gpt-oss-120b'])
    assert result.model == 'openai/gpt-oss-120b'
    assert len(primary.chat.completions.calls) == 1
    assert [r['credential_slot'] for r in result.fallback_trace] == [0, 1, 1]


@pytest.mark.parametrize("headers,expected", [
    ({"retry-after": "2"}, 2.0),
    ({"retry-after": "1m12.5s"}, 72.5),
    ({"x-ratelimit-reset-tokens": "3s"}, 3.0),
    ({"x-ratelimit-reset-tokens": "1ms"}, 1.0),
    ({}, 60.0),
])
def test_retry_delay_accepts_groq_second_and_minute_headers(headers, expected):
    assert _retry_delay_seconds(headers) == expected


@pytest.mark.asyncio
async def test_transient_server_error_tries_another_key_for_same_model(monkeypatch):
    primary, secondary = scripted_client(500), scripted_client(200)
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    result = await service.chat([], fallback_models=[])
    assert result.model == service.settings.groq_model
    assert [row["credential_slot"] for row in result.fallback_trace] == [0, 1]


@pytest.mark.asyncio
async def test_transient_connection_failure_is_cooled_before_the_next_turn(monkeypatch):
    class APIConnectionError(RuntimeError):
        pass

    class Unreachable:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            raise APIConnectionError("temporary route failure")

    primary = FakeClient()
    primary.chat.completions = Unreachable()
    secondary = FakeClient()
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])

    first = await service.chat([], fallback_models=[])
    second = await service.chat([], fallback_models=[])

    assert first.model == second.model == service.settings.groq_model
    assert len(primary.chat.completions.calls) == 1
    assert len(secondary.chat.completions.calls) == 2
    assert second.fallback_trace[0]["status"] == "cooldown"


@pytest.mark.asyncio
async def test_second_fallback_model_is_reached_after_all_keys_rate_limited(monkeypatch):
    primary = scripted_client(429, 429, 200)
    secondary = scripted_client(429, 429, 429)
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    result = await service.chat([], fallback_models=["openai/gpt-oss-120b", "openai/gpt-oss-20b"])
    assert result.model == "openai/gpt-oss-20b"
    assert [row["status"] for row in result.fallback_trace] == [
        "error", "error", "error", "error", "success"
    ]
    assert primary.chat.completions.calls[-1]["model"] == "openai/gpt-oss-20b"


@pytest.mark.asyncio
async def test_healthy_keys_share_requests_instead_of_hotspotting_first_key(monkeypatch):
    primary, secondary = scripted_client(200, 200), scripted_client(200)
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    slots = [(await service.chat([], fallback_models=[])).fallback_trace[-1]["credential_slot"]
             for _ in range(3)]
    assert slots == [0, 1, 0]


@pytest.mark.asyncio
async def test_invalid_model_request_does_not_burn_every_key_on_the_same_payload(monkeypatch):
    primary, secondary = scripted_client(400, 200), scripted_client()
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    result = await service.chat([], fallback_models=["openai/gpt-oss-120b"])
    assert result.model == "openai/gpt-oss-120b"
    assert not secondary.chat.completions.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_model_access_error_on_one_key_does_not_hide_other_keys_access(monkeypatch, status):
    primary, secondary = scripted_client(status), scripted_client(200)
    service = GroqService(Settings(groq_api_key="test-key"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    result = await service.chat([], fallback_models=[])
    assert result.model == service.settings.groq_model
    assert result.fallback_trace[-1]["credential_slot"] == 1


def test_chat_and_whisper_load_six_unique_keys_in_the_same_order(monkeypatch):
    from backend.services.whisper_stt import WhisperSTT
    settings = Settings(_env_file=None, groq_api_key="slot0", **{
        f"groq_api_key_fallback_{i}": f"slot{i}" for i in range(1, 6)})
    constructed = []

    def make_client(**kwargs):
        constructed.append(kwargs["api_key"])
        return FakeClient()

    monkeypatch.setattr("backend.services.groq.AsyncOpenAI", make_client)
    service = GroqService(settings)
    clients = service._clients(service._client_or_raise())
    assert len(clients) == 6
    assert constructed == WhisperSTT(settings)._keys() == [f"slot{i}" for i in range(6)]


@pytest.mark.asyncio
async def test_sixth_key_is_tried_before_switching_models(monkeypatch):
    clients = [scripted_client(429) for _ in range(5)] + [scripted_client(200)]
    service = GroqService(Settings(groq_api_key="test"), client=clients[0])
    monkeypatch.setattr(service, "_clients", lambda _: clients)
    result = await service.chat([], fallback_models=["openai/gpt-oss-120b"])
    assert result.model == service.settings.groq_model
    assert [a["credential_slot"] for a in result.fallback_trace] == list(range(6))


@pytest.mark.asyncio
@pytest.mark.parametrize("defer", [True, False])
async def test_encoding_downgrade_is_opt_in_and_does_not_burn_model_fallback(monkeypatch, defer):
    class EncodingFailure(StatusFailure):
        code = "json_validate_failed"

    class Completions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise EncodingFailure(400)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    primary, secondary = FakeClient(), scripted_client()
    primary.chat.completions = Completions()
    service = GroqService(Settings(groq_api_key="test"), client=primary)
    monkeypatch.setattr(service, "_clients", lambda _: [primary, secondary])
    kwargs = {"fallback_models": ["openai/gpt-oss-120b"], "defer_encoding_errors": defer}
    if defer:
        with pytest.raises(GroqProviderError) as error:
            await service.chat([], **kwargs)
        assert error.value.fallback_trace[0]["provider_code"] == "json_validate_failed"
        assert len(primary.chat.completions.calls) == 1
    else:
        assert (await service.chat([], **kwargs)).model == "openai/gpt-oss-120b"
    assert not secondary.chat.completions.calls
