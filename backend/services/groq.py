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
    """Chat text carrying the exact Groq model that produced it."""

    model: str

    def __new__(cls, text: str, model: str) -> GroqChatResult:
        result = super().__new__(cls, text)
        result.model = model
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
        messages: Sequence[dict[str, str]],
        model: str | None = None,
    ) -> GroqChatResult:
        client = self._client_or_raise()
        models = [model or self.settings.groq_model]
        if self.settings.groq_fallback_model not in models:
            models.append(self.settings.groq_fallback_model)

        last_error: Exception | None = None
        for selected_model in models:
            try:
                response = await client.chat.completions.create(
                    model=selected_model,
                    messages=list(messages),
                    temperature=0.2,
                    max_tokens=80,
                    **({"reasoning_effort": "none"} if selected_model.startswith("qwen/") else {}),
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise ValueError("Groq returned an empty response")
                return GroqChatResult(content.strip(), selected_model)
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
        try:
            response = await client.audio.transcriptions.create(
                file=(filename, content, content_type),
                model=self.settings.groq_stt_model,
                language=language,
                prompt=RETAIL_STT_PROMPTS.get(language, RETAIL_STT_PROMPT),
                response_format="json",
            )
            text = getattr(response, "text", "")
            if not text or not text.strip():
                raise ValueError("Groq returned an empty transcription")
            return text.strip()
        except GroqConfigurationError:
            raise
        except Exception as exc:
            raise GroqProviderError("Groq transcription request failed") from exc
