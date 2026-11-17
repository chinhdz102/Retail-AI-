from __future__ import annotations

import inspect
import logging
import re
import time
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
# A connection failure is usually a temporarily unhealthy route/key.  Keep it
# out of the next few requests so a consultation does not pay the same network
# timeout repeatedly before reaching a healthy key/model.  Rate-limit headers
# still take precedence and use the provider's reset value below.
TRANSIENT_CONNECTION_COOLDOWN_SECONDS = 8.0


def _retry_delay_seconds(headers: dict[str, str]) -> float:
    """Parse Groq retry/reset headers without exposing provider response bodies."""
    raw = headers.get("retry-after") or headers.get("x-ratelimit-reset-tokens") or "60"
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|d|h|m|s)", str(raw).strip().lower())
        if parts and "".join(number + unit for number, unit in parts) == str(raw).strip().lower():
            scales = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
            return max(1.0, sum(float(number) * scales[unit] for number, unit in parts))
        return 60.0


def _rate_limit_headers(headers: Any) -> dict[str, str]:
    if headers is None or not hasattr(headers, "get"):
        return {}
    return {
        name: str(value)
        for name in RATE_LIMIT_HEADER_NAMES
        if (value := headers.get(name)) is not None
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


class GroqService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._injected_client = client is not None
        self._fallback_clients: list[Any] | None = None
        self._cooldowns: dict[tuple[str, int], float] = {}
        # Groq limits are organization-scoped. A real client that receives a
        # 429 should not retry the same credential through every model; tests
        # with injected clients retain the exhaustive per-model order.
        self._credential_cooldowns: dict[int, float] = {}
        self._next_slots: dict[str, int] = {}

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
                    "groq_api_key_fallback_5",
                ) if getattr(self.settings, field).get_secret_value().strip()
            ))
            # Keys use the same provider URL. SDK copies keep distinct auth
            # headers but share the primary HTTP/TLS connection pool, avoiding
            # several expensive SSL/client initializations on the first turn.
            self._fallback_clients = [primary.with_options(api_key=key) for key in keys[1:]]
        return [primary, *self._fallback_clients]

    def has_available_credentials(self) -> bool:
        """Return whether a real key is outside its provider cooldown.

        Consultation uses this as a fast outage guard.  It is intentionally a
        coarse health signal: a request still performs the normal fallback
        loop, while a known all-key 429 avoids paying another timeout before a
        deterministic catalog response.
        """
        if getattr(self, "_injected_client", True) or getattr(self, "_fallback_clients", None) is None:
            return True
        now = time.monotonic()
        slots = len(self._fallback_clients) + 1
        return any(self._credential_cooldowns.get(slot, 0.0) <= now
                   for slot in range(slots))

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
        defer_encoding_errors: bool = False,
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
        attempts = ((selected_model, slot, clients[slot])
                    for selected_model in models
                    for start in [self._next_slots.get(selected_model, 0)]
                    for slot in [(start + offset) % len(clients) for offset in range(len(clients))])
        for selected_model, credential_slot, client in attempts:
            if credential_slot in blocked_slots or selected_model in failed_models:
                continue
            if not self._injected_client:
                credential_cooldown = self._credential_cooldowns.get(credential_slot, 0) - time.monotonic()
                if credential_cooldown > 0:
                    fallback_trace.append({
                        "model": selected_model,
                        "credential_slot": credential_slot,
                        "status": "cooldown",
                        "retry_after_seconds": round(credential_cooldown, 1),
                    })
                    continue
            cooldown = self._cooldowns.get((selected_model, credential_slot), 0) - time.monotonic()
            if cooldown > 0:
                fallback_trace.append({"model": selected_model, "credential_slot": credential_slot,
                                       "status": "cooldown", "retry_after_seconds": round(cooldown, 1)})
                continue
            try:
                attempt_started = time.monotonic()
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
                elapsed_ms = (time.monotonic() - attempt_started) * 1000
                if response_headers:
                    successful_attempt["rate_limit_headers"] = response_headers
                fallback_trace.append(successful_attempt)
                self._next_slots[selected_model] = (credential_slot + 1) % len(clients)
                logger.info(
                    "Groq chat completed model=%s credential_slot=%d attempts=%d elapsed_ms=%.1f",
                    selected_model,
                    credential_slot,
                    len(fallback_trace),
                    elapsed_ms,
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
                if defer_encoding_errors and provider_code == "json_validate_failed":
                    # The structured caller can retry a simpler encoding on
                    # this model. Trying other keys/models with the same schema
                    # first added whole reasoning passes to simple text turns.
                    raise GroqProviderError(
                        "Provider could not encode the structured response",
                        fallback_trace=[*fallback_trace, failed_attempt],
                    ) from exc
                status = getattr(exc, "status_code", None)
                if status == 401:
                    blocked_slots.add(credential_slot)
                elif status in {400, 413, 422}:
                    # Key changes cannot repair invalid request parameters.
                    # 403/404 can be model permissions on one key, so another
                    # key may still succeed on that same model.
                    failed_models.add(selected_model)
                error_response = getattr(exc, "response", None)
                response_headers = _rate_limit_headers(getattr(error_response, "headers", None))
                if response_headers:
                    failed_attempt["rate_limit_headers"] = response_headers
                if status == 429:
                    delay = _retry_delay_seconds(response_headers)
                    deadline = time.monotonic() + max(1.0, min(delay, 86400.0))
                    self._cooldowns[(selected_model, credential_slot)] = deadline
                    if not self._injected_client:
                        self._credential_cooldowns[credential_slot] = max(
                            self._credential_cooldowns.get(credential_slot, 0), deadline,
                        )
                elif type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
                    # Do not hammer a route that just failed to connect. Keep
                    # this short so a transient network blip can recover, but
                    # long enough to let the current request move to another
                    # key/model instead of waiting through another timeout.
                    self._cooldowns[(selected_model, credential_slot)] = (
                        time.monotonic() + TRANSIENT_CONNECTION_COOLDOWN_SECONDS
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
