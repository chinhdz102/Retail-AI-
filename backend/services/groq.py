from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI

from backend.config import Settings, get_settings

RETAIL_STT_PROMPT = (
    "Đây là hội thoại mua hàng tại cửa hàng tiện lợi bằng tiếng Việt. "
    "Ưu tiên nhận đúng các từ: Coca-Cola, Coca Zero, sữa, snack, nước, cà phê, "
    "giá, kệ, tồn kho, hạn dùng, giỏ hàng, thanh toán. Không dịch sang ngôn ngữ khác."
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


class GroqChatResult(str):
    """Chat response carrying its model and any structured tool calls."""

    model: str
    tool_calls: list[dict[str, Any]]

    def __new__(
        cls,
        text: str,
        model: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> GroqChatResult:
        result = super().__new__(cls, text)
        result.model = model
        result.tool_calls = tool_calls or []
        return result


class GroqService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client

    def _client_or_raise(self) -> Any:
        if not self.settings.has_groq_key:
            raise GroqConfigurationError("Groq API key is not configured")
        if self.client is None:
            self.client = AsyncOpenAI(
                api_key=self.settings.groq_api_key.get_secret_value(),
                base_url=self.settings.groq_base_url,
                timeout=self.settings.groq_timeout_seconds,
            )
        return self.client

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> GroqChatResult:
        client = self._client_or_raise()
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
        for selected_model in models:
            try:
                model_options: dict[str, Any] = {}
                if selected_model.startswith("qwen/"):
                    model_options["reasoning_effort"] = "none"
                elif selected_model.startswith("openai/gpt-oss-"):
                    model_options["reasoning_effort"] = "low"
                if tools:
                    model_options["tools"] = list(tools)
                    model_options["tool_choice"] = tool_choice or "auto"

                response = await client.chat.completions.create(
                    model=selected_model,
                    messages=list(messages),
                    temperature=0.2,
                    max_tokens=80,
                    **model_options,
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
                return GroqChatResult(content.strip(), selected_model, tool_calls)
            except Exception as exc:
                last_error = exc

        raise GroqProviderError("Groq chat request failed for configured models") from last_error

    async def transcribe(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        language: str = "vi",
    ) -> str:
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
                return text.strip()
            except Exception as exc:
                last_error = exc

        raise GroqProviderError(
            "Groq transcription request failed for configured models"
        ) from last_error
