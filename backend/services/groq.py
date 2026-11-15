from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Sequence
from itertools import product
from typing import Any

from openai import AsyncOpenAI

from backend.config import Settings, get_settings

logger = logging.getLogger(__name__)
RATE_LIMIT_HEADER_NAMES = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
)


def _rate_limit_headers(headers: Any) -> dict[str, str]:
    if headers is None or not hasattr(headers, "get"):
        return {}
    return {
        name: str(value)
        for name in RATE_LIMIT_HEADER_NAMES
        if (value := headers.get(name)) is not None
    }


RETAIL_STT_PROMPT = (
    "Đây là hội thoại mua hàng tại cửa hàng tiện lợi bằng tiếng Việt. "
    "Người nói có thể dùng giọng Bắc, Trung hoặc Nam và cách nói địa phương; "
    "các ví dụ gồm mô/chi (hỏi nào/gì), nhứt (nhất), hông/hem (không). "
    "Giữ đúng từ thực sự được nói, nhất là tên hàng; chỉ ghi các từ địa phương "
    "khi nghe thấy, không tự thêm từ hoặc viết lại câu. "
    "Ưu tiên nhận đúng các từ: Coca-Cola, Coca Zero, sữa, snack, quà vặt, đồ ăn vặt, "
    "cửa hàng tiện lợi, ngoài cổng trường, dòng tiền, nước, cà phê, giá, kệ, tồn kho, "
    "hạn dùng, giỏ hàng, thanh toán. Không dịch sang ngôn ngữ khác."
)

RETAIL_STT_PROMPTS = {
    "vi": RETAIL_STT_PROMPT,
    "en": (
        "This is a retail conversation in English. Prefer accurate recognition of: "
        "Coca-Cola, Coca Zero, milk, snack, water, coffee, price, shelf, stock, "
        "cart and checkout. Do not translate into another language."
    ),
    "zh": (
        "这是便利店里的普通话购物对话。请优先准确识别：可口可乐、Coca Zero、牛奶、零食、"
        "水、咖啡、价格、货架、库存、购物车和结账。不要翻译成其他语言。"
    ),
}


class GroqConfigurationError(RuntimeError):
    """Raised when the server has no Groq credential configured."""


class GroqProviderError(RuntimeError):
    """Raised when Groq cannot complete a request."""

    def __init__(
        self,
        message: str,
        fallback_trace: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.fallback_trace = fallback_trace or []


class GroqChatResult(str):
    """Chat response carrying its model and any structured tool calls."""

    model: str
    tool_calls: list[dict[str, Any]]
    fallback_trace: list[dict[str, Any]]
    usage: dict[str, int]

    def __new__(
        cls,
        text: str,
        model: str,
        tool_calls: list[dict[str, Any]] | None = None,
        fallback_trace: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
    ) -> GroqChatResult:
        result = super().__new__(cls, text)
        result.model = model
        result.tool_calls = tool_calls or []
        result.fallback_trace = fallback_trace or []
        result.usage = usage or {}
        return result


class GroqTranscriptionResult(str):
    """Transcription carrying its actual model and fallback trace."""

    model: str
    fallback_trace: list[dict[str, str]]

    def __new__(
        cls,
        text: str,
        model: str,
        fallback_trace: list[dict[str, Any]] | None = None,
    ) -> GroqTranscriptionResult:
        result = super().__new__(cls, text)
        result.model = model
        result.fallback_trace = fallback_trace or []
        return result


class GroqService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._injected_client = client is not None
        self._fallback_clients: list[Any] | None = None
        self._cooldowns: dict[tuple[str, int], float] = {}

    def _client_or_raise(self) -> Any:
        if not self.settings.has_groq_key:
            raise GroqConfigurationError("Groq API key is not configured")
        if self.client is None:
            self.client = AsyncOpenAI(
                api_key=self.settings.groq_api_key.get_secret_value(),
                base_url=self.settings.groq_base_url,
                timeout=self.settings.groq_timeout_seconds,
                max_retries=0,
            )
        return self.client

    def _clients(self, primary: Any) -> list[Any]:
        if self._injected_client:
            return [primary]
        if self._fallback_clients is None:
            keys = list(dict.fromkeys(
                getattr(self.settings, field).get_secret_value().strip()
                for field in (
                    "groq_api_key", "groq_api_key_fallback_1", "groq_api_key_fallback_2",
                    "groq_api_key_fallback_3", "groq_api_key_fallback_4",
                ) if getattr(self.settings, field).get_secret_value().strip()
            ))
            self._fallback_clients = [AsyncOpenAI(
                api_key=key, base_url=self.settings.groq_base_url,
                timeout=self.settings.groq_timeout_seconds, max_retries=0,
            ) for key in keys[1:]]
        return [primary, *self._fallback_clients]

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        *,
        max_tokens: int = 80,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        fallback_models: Sequence[str] | None = None,
    ) -> GroqChatResult:
        client = self._client_or_raise()
        models = list(
            dict.fromkeys(
                selected_model
                for selected_model in (
                    model or self.settings.groq_model,
                    *(fallback_models if fallback_models is not None else (
                        self.settings.groq_fallback_model,
                        self.settings.groq_fallback_model_2,
                    )),
                )
                if selected_model
            )
        )

        last_error: Exception | None = None
        fallback_trace: list[dict[str, Any]] = []
        clients = self._clients(client)
        blocked_slots: set[int] = set()
        failed_models: set[str] = set()
        for selected_model, (credential_slot, client) in product(models, enumerate(clients)):
            if credential_slot in blocked_slots or selected_model in failed_models:
                continue
            cooldown = self._cooldowns.get((selected_model, credential_slot), 0) - time.monotonic()
            if cooldown > 0:
                fallback_trace.append({"model": selected_model, "credential_slot": credential_slot,
                                       "status": "cooldown", "retry_after_seconds": round(cooldown, 1)})
                continue
            try:
                model_options: dict[str, Any] = {}
                if selected_model.startswith("qwen/"):
                    model_options["reasoning_effort"] = "none"
                elif selected_model.startswith("openai/gpt-oss-"):
                    model_options["reasoning_effort"] = "low"
                if reasoning_effort is not None:
                    model_options["reasoning_effort"] = reasoning_effort
                if tools:
                    model_options["tools"] = list(tools)
                    model_options["tool_choice"] = tool_choice or "auto"
                if response_format is not None:
                    model_options["response_format"] = response_format

                request_options = {
                    "model": selected_model,
                    "messages": list(messages),
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                    **model_options,
                }
                completion_endpoint = client.chat.completions
                raw_endpoint = getattr(completion_endpoint, "with_raw_response", None)
                response_headers: dict[str, str] = {}
                if raw_endpoint is None:
                    response = await completion_endpoint.create(**request_options)
                else:
                    raw_response = await raw_endpoint.create(**request_options)
                    response_headers = _rate_limit_headers(raw_response.headers)
                    parsed_response = raw_response.parse()
                    response = (
                        await parsed_response
                        if inspect.isawaitable(parsed_response)
                        else parsed_response
                    )
                response_message = response.choices[0].message
                content = response_message.content or ""
                tool_calls = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in (getattr(response_message, "tool_calls", None) or [])
                ]
                if not content.strip() and not tool_calls:
                    raise ValueError("Groq returned an empty response")
                successful_attempt: dict[str, Any] = {
                    "model": selected_model,
                    "credential_slot": credential_slot,
                    "status": "success",
                }
                if response_headers:
                    successful_attempt["rate_limit_headers"] = response_headers
                fallback_trace.append(successful_attempt)
                logger.info(
                    "Groq chat completed model=%s credential_slot=%d attempts=%d",
                    selected_model,
                    credential_slot,
                    len(fallback_trace),
                )
                usage = getattr(response, "usage", None)
                usage_summary = {
                    field: int(value)
                    for field in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if (value := getattr(usage, field, None)) is not None
                }
                return GroqChatResult(
                    content.strip(), selected_model, tool_calls, fallback_trace, usage_summary
                )
            except Exception as exc:
                last_error = exc
                failed_attempt: dict[str, Any] = {
                    "model": selected_model,
                    "credential_slot": credential_slot,
                    "status": "error",
                    "error_type": type(exc).__name__,
                }
                status = getattr(exc, "status_code", None)
                if isinstance(status, int):
                    failed_attempt["http_status"] = status
                # Keep a bounded diagnostic code, never the provider's raw body
                # (which may include request data or credential information).
                provider_code = getattr(exc, "code", None)
                if provider_code in {
                    "json_validate_failed", "tool_use_failed", "rate_limit_exceeded",
                    "context_length_exceeded", "model_not_found", "invalid_api_key",
                }:
                    failed_attempt["provider_code"] = provider_code
                if getattr(exc, "status_code", None) in {401, 403}:
                    blocked_slots.add(credential_slot)
                elif getattr(exc, "status_code", None) != 429:
                    failed_models.add(selected_model)
                error_response = getattr(exc, "response", None)
                response_headers = _rate_limit_headers(getattr(error_response, "headers", None))
                if response_headers:
                    failed_attempt["rate_limit_headers"] = response_headers
                if getattr(exc, "status_code", None) == 429:
                    try:
                        delay = float(response_headers.get("retry-after", "60"))
                    except ValueError:
                        delay = 60.0
                    self._cooldowns[(selected_model, credential_slot)] = (
                        time.monotonic() + max(1.0, min(delay, 86400.0))
                    )
                fallback_trace.append(failed_attempt)
                logger.warning(
                    "Groq chat model attempt failed model=%s credential_slot=%d error_type=%s code=%s",
                    selected_model,
                    credential_slot,
                    type(exc).__name__,
                    failed_attempt.get("provider_code", "unclassified"),
                )

        logger.error(
            "Groq chat exhausted configured models attempts=%d",
            len(fallback_trace),
        )
        raise GroqProviderError(
            "Groq chat request failed for configured models",
            fallback_trace=fallback_trace,
        ) from last_error

    async def transcribe(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        language: str = "vi",
    ) -> GroqTranscriptionResult:
        client = self._client_or_raise()
        models = list(
            dict.fromkeys(
                model
                for model in (
                    self.settings.groq_stt_model,
                    self.settings.groq_stt_fallback_model,
                )
                if model
            )
        )

        last_error: Exception | None = None
        fallback_trace: list[dict[str, str]] = []
        for selected_model in models:
            try:
                response = await client.audio.transcriptions.create(
                    file=(filename, content, content_type),
                    model=selected_model,
                    language=language,
                    prompt=RETAIL_STT_PROMPTS.get(language, RETAIL_STT_PROMPT),
                    response_format="json",
                )
                text = getattr(response, "text", "")
                if not text or not text.strip():
                    raise ValueError("Groq returned an empty transcription")
                logger.info("Groq transcription completed model=%s", selected_model)
                fallback_trace.append({"model": selected_model, "status": "success"})
                return GroqTranscriptionResult(text.strip(), selected_model, fallback_trace)
            except Exception as exc:
                last_error = exc
                fallback_trace.append(
                    {
                        "model": selected_model,
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                )
                logger.warning(
                    "Groq transcription model attempt failed model=%s error_type=%s",
                    selected_model,
                    type(exc).__name__,
                )

        logger.error("Groq transcription exhausted configured models attempts=%d", len(models))
        raise GroqProviderError(
            "Groq transcription request failed for configured models"
        ) from last_error
