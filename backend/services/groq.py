from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
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
    fallback_trace: list[dict[str, Any]]

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
        self._clients_by_credential: dict[str, Any] = {}

    def _credentials_or_raise(self) -> tuple[tuple[str, str], ...]:
        credentials = self.settings.groq_api_credentials
        if not credentials:
            raise GroqConfigurationError("Groq API key is not configured")
        return credentials

    def _client_for_credential(self, slot: str, api_key: str) -> Any:
        # Injected clients keep unit tests and app integrations deterministic.
        if self.client is not None:
            return self.client
        if slot not in self._clients_by_credential:
            self._clients_by_credential[slot] = AsyncOpenAI(
                api_key=api_key,
                base_url=self.settings.groq_base_url,
                timeout=self.settings.groq_timeout_seconds,
            )
        return self._clients_by_credential[slot]

    @staticmethod
    def _can_rotate_credential(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(exc, "status_code", None) or getattr(
            response, "status_code", None
        )
        if status_code in {401, 403, 429}:
            return True
        return type(exc).__name__.lower() in {
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
        }

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> GroqChatResult:
        credentials = self._credentials_or_raise()
        models = list(
            dict.fromkeys(
                selected_model
                for selected_model in (
                    model or self.settings.groq_model,
                    self.settings.groq_fallback_model,
                    self.settings.groq_fallback_model_2,
                )
                if selected_model
            )
        )

        last_error: Exception | None = None
        fallback_trace: list[dict[str, Any]] = []
        for selected_model in models:
            for credential_slot, api_key in credentials:
                client = self._client_for_credential(credential_slot, api_key)
                try:
                    model_options: dict[str, Any] = {}
                    if selected_model.startswith("qwen/"):
                        model_options["reasoning_effort"] = "none"
                    elif selected_model.startswith("openai/gpt-oss-"):
                        model_options["reasoning_effort"] = "low"
                    if tools:
                        model_options["tools"] = list(tools)
                        model_options["tool_choice"] = tool_choice or "auto"

                    request_options = {
                        "model": selected_model,
                        "messages": list(messages),
                        "temperature": 0.2,
                        "max_tokens": 80,
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
                        "status": "success",
                    }
                    if response_headers:
                        successful_attempt["rate_limit_headers"] = response_headers
                    if len(credentials) > 1:
                        successful_attempt["credential_slot"] = credential_slot
                    fallback_trace.append(successful_attempt)
                    logger.info(
                        "Groq chat completed model=%s attempts=%d",
                        selected_model,
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
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                    error_response = getattr(exc, "response", None)
                    response_headers = _rate_limit_headers(
                        getattr(error_response, "headers", None)
                    )
                    if response_headers:
                        failed_attempt["rate_limit_headers"] = response_headers
                    if len(credentials) > 1:
                        failed_attempt["credential_slot"] = credential_slot
                    fallback_trace.append(failed_attempt)
                    logger.warning(
                        "Groq chat model attempt failed model=%s credential_slot=%s error_type=%s",
                        selected_model,
                        credential_slot if len(credentials) > 1 else "primary",
                        type(exc).__name__,
                    )
                    # Only retry a different key for credential/quota failures.
                    # Other errors move directly to the next model.
                    if not self._can_rotate_credential(exc):
                        break

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
        credentials = self._credentials_or_raise()
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
        fallback_trace: list[dict[str, Any]] = []
        for selected_model in models:
            for credential_slot, api_key in credentials:
                client = self._client_for_credential(credential_slot, api_key)
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
                    successful_attempt = {"model": selected_model, "status": "success"}
                    if len(credentials) > 1:
                        successful_attempt["credential_slot"] = credential_slot
                    fallback_trace.append(successful_attempt)
                    return GroqTranscriptionResult(text.strip(), selected_model, fallback_trace)
                except Exception as exc:
                    last_error = exc
                    failed_attempt = {
                        "model": selected_model,
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                    if len(credentials) > 1:
                        failed_attempt["credential_slot"] = credential_slot
                    fallback_trace.append(failed_attempt)
                    logger.warning(
                        "Groq transcription failed model=%s credential_slot=%s error_type=%s",
                        selected_model,
                        credential_slot if len(credentials) > 1 else "primary",
                        type(exc).__name__,
                    )
                    if not self._can_rotate_credential(exc):
                        break

        logger.error("Groq transcription exhausted configured models attempts=%d", len(models))
        raise GroqProviderError(
            "Groq transcription request failed for configured models"
        ) from last_error
