from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import Settings, get_settings
from backend.services.speech_stream import SpeechAudioStream
from backend.services.speech_text import retail_ssml_text

_MAX_TTS_CACHE_ENTRIES = 64
_MAX_TTS_CACHE_ENTRY_BYTES = 256 * 1024
_TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})


class AzureSpeechConfigurationError(RuntimeError):
    """Raised when Azure Speech credentials or region are missing or invalid."""


class AzureSpeechProviderError(RuntimeError):
    """Raised when Azure Speech cannot synthesize audio."""


@dataclass(frozen=True)
class AzureSpeechAudio:
    content: bytes
    content_type: str
    voice_id: str


class _MemoryAudioResponse:
    """Serves cached MP3 bytes through the same streaming interface as httpx."""

    def __init__(self, content: bytes, chunk_size: int = 8192) -> None:
        self._content = content
        self._chunk_size = chunk_size

    async def aiter_bytes(self):
        for offset in range(0, len(self._content), self._chunk_size):
            yield self._content[offset : offset + self._chunk_size]

    async def aclose(self) -> None:
        return None


class _CachingAudioResponse:
    """Proxies an upstream httpx stream and caches completed small responses."""

    def __init__(
        self,
        response: Any,
        *,
        on_complete: Any,
    ) -> None:
        self._response = response
        self._on_complete = on_complete

    async def aiter_bytes(self):
        collected = bytearray()
        oversized = False
        async for chunk in self._response.aiter_bytes(chunk_size=4096):
            if chunk:
                if not oversized:
                    if len(collected) + len(chunk) <= _MAX_TTS_CACHE_ENTRY_BYTES:
                        collected.extend(chunk)
                    else:
                        oversized = True
                        collected.clear()
                yield chunk
        if collected and not oversized:
            self._on_complete(bytes(collected))

    async def aclose(self) -> None:
        await self._response.aclose()


class AzureSpeechService:
    VOICES: dict[str, tuple[str, str]] = {
        "vi": ("vi-VN", "vi-VN-HoaiMyNeural"),
        "en": ("en-US", "en-US-JennyNeural"),
        "zh": ("zh-CN", "zh-CN-XiaoxiaoNeural"),
    }
    OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        # Injected mock clients in unit tests expect every call to hit transport.
        self._cache_enabled = client is None
        self._audio_cache: OrderedDict[tuple[str, str], AzureSpeechAudio] = OrderedDict()

    def _cache_get(self, key: tuple[str, str]) -> AzureSpeechAudio | None:
        if not self._cache_enabled:
            return None
        cached = self._audio_cache.get(key)
        if cached is not None:
            self._audio_cache.move_to_end(key)
        return cached

    def _cache_put(self, key: tuple[str, str], audio: AzureSpeechAudio) -> None:
        if not self._cache_enabled or len(audio.content) > _MAX_TTS_CACHE_ENTRY_BYTES:
            return
        self._audio_cache[key] = audio
        self._audio_cache.move_to_end(key)
        while len(self._audio_cache) > _MAX_TTS_CACHE_ENTRIES:
            self._audio_cache.popitem(last=False)

    def _client_or_raise(self) -> tuple[Any, str]:
        key = self.settings.azure_speech_key.get_secret_value().strip()
        region = self.settings.azure_speech_region.strip().lower()
        if not key or not region:
            raise AzureSpeechConfigurationError(
                "Azure Speech key and region are not configured"
            )
        if not re.fullmatch(r"[a-z0-9-]+", region):
            raise AzureSpeechConfigurationError("Azure Speech region is invalid")

        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=f"https://{region}.tts.speech.microsoft.com",
                timeout=self.settings.azure_speech_timeout_seconds,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=60.0,
                ),
            )
        return self.client, key

    def _build_ssml_and_headers(
        self, text: str, language: str, key: str
    ) -> tuple[str, str, bytes, dict[str, str]]:
        voice = self.VOICES.get(language)
        if voice is None:
            raise AzureSpeechProviderError("Azure Speech voice is unavailable")
        if not text.strip():
            raise AzureSpeechProviderError("Azure Speech text is empty")
        locale, voice_id = voice
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{locale}"><voice name="{voice_id}">'
            f'{retail_ssml_text(text, language)}</voice></speak>'
        )
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.OUTPUT_FORMAT,
            "User-Agent": "retail-ai-kiosk",
        }
        return voice_id, ssml, ssml.encode("utf-8"), headers

    async def preconnect(self, language: str = "vi") -> None:
        """Warm the reusable Azure connection while the customer is speaking."""
        voice = self.VOICES.get(language)
        if voice is None:
            raise AzureSpeechProviderError("Azure Speech voice is unavailable")

        client, key = self._client_or_raise()
        locale, _ = voice
        try:
            response = await client.get(
                "/cognitiveservices/voices/list",
                params={"language": locale},
                headers={"Ocp-Apim-Subscription-Key": key},
                timeout=min(self.settings.azure_speech_timeout_seconds, 5.0),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AzureSpeechProviderError(
                f"Azure Speech preconnect failed with HTTP {exc.response.status_code}"
            ) from exc
        except AzureSpeechProviderError:
            raise
        except Exception as exc:
            raise AzureSpeechProviderError("Azure Speech preconnect failed") from exc

    async def synthesize(self, text: str, language: str = "vi") -> AzureSpeechAudio:
        voice = self.VOICES.get(language)
        if voice is None:
            raise AzureSpeechProviderError("Azure Speech voice is unavailable")
        if not text.strip():
            raise AzureSpeechProviderError("Azure Speech text is empty")

        client, key = self._client_or_raise()
        voice_id, ssml, ssml_bytes, headers = self._build_ssml_and_headers(text, language, key)
        cache_key = (voice_id, ssml)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        max_attempts = 2 if self._cache_enabled else 1
        for attempt in range(max_attempts):
            try:
                response = await client.post(
                    "/cognitiveservices/v1",
                    headers=headers,
                    content=ssml_bytes,
                )
                response.raise_for_status()
                if not response.content:
                    raise ValueError("Azure Speech returned empty audio")
                content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
                if not content_type.startswith("audio/"):
                    raise ValueError("Azure Speech returned a non-audio response")
                audio = AzureSpeechAudio(
                    content=response.content,
                    content_type=content_type,
                    voice_id=voice_id,
                )
                self._cache_put(cache_key, audio)
                return audio
            except httpx.HTTPStatusError as exc:
                if (
                    attempt + 1 < max_attempts
                    and exc.response.status_code in _TRANSIENT_HTTP_STATUSES
                ):
                    continue
                raise AzureSpeechProviderError(
                    f"Azure Speech request failed with HTTP {exc.response.status_code}"
                ) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt + 1 < max_attempts:
                    continue
                raise AzureSpeechProviderError("Azure Speech synthesis failed") from exc
            except AzureSpeechProviderError:
                raise
            except Exception as exc:
                raise AzureSpeechProviderError("Azure Speech synthesis failed") from exc
        raise AzureSpeechProviderError("Azure Speech synthesis failed")

    async def open_stream(self, text: str, language: str = "vi") -> SpeechAudioStream:
        voice = self.VOICES.get(language)
        if voice is None:
            raise AzureSpeechProviderError("Azure Speech voice is unavailable")
        if not text.strip():
            raise AzureSpeechProviderError("Azure Speech text is empty")

        client, key = self._client_or_raise()
        voice_id, ssml, ssml_bytes, headers = self._build_ssml_and_headers(text, language, key)
        cache_key = (voice_id, ssml)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return SpeechAudioStream(
                _MemoryAudioResponse(cached.content),
                cached.content_type,
                cached.voice_id,
            )

        max_attempts = 2 if self._cache_enabled else 1
        for attempt in range(max_attempts):
            response = None
            try:
                request = client.build_request(
                    "POST", "/cognitiveservices/v1", headers=headers, content=ssml_bytes
                )
                response = await client.send(request, stream=True)
                if response.status_code >= 400:
                    status_code = response.status_code
                    await response.aclose()
                    response = None
                    if attempt + 1 < max_attempts and status_code in _TRANSIENT_HTTP_STATUSES:
                        continue
                    raise AzureSpeechProviderError(
                        f"Azure Speech request failed with HTTP {status_code}"
                    )
                content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
                if not content_type.startswith("audio/"):
                    await response.aclose()
                    response = None
                    raise AzureSpeechProviderError("Azure Speech returned a non-audio response")
                wrapped_response = (
                    _CachingAudioResponse(
                        response,
                        on_complete=lambda data: self._cache_put(
                            cache_key,
                            AzureSpeechAudio(
                                content=data,
                                content_type=content_type,
                                voice_id=voice_id,
                            ),
                        ),
                    )
                    if self._cache_enabled
                    else response
                )
                return SpeechAudioStream(wrapped_response, content_type, voice_id)
            except AzureSpeechProviderError:
                if response is not None:
                    await response.aclose()
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if response is not None:
                    await response.aclose()
                if attempt + 1 < max_attempts:
                    continue
                raise AzureSpeechProviderError("Azure Speech synthesis failed") from exc
            except httpx.HTTPStatusError as exc:
                if response is not None:
                    await response.aclose()
                raise AzureSpeechProviderError(
                    f"Azure Speech request failed with HTTP {exc.response.status_code}"
                ) from exc
            except Exception as exc:
                if response is not None:
                    await response.aclose()
                raise AzureSpeechProviderError("Azure Speech synthesis failed") from exc
        raise AzureSpeechProviderError("Azure Speech synthesis failed")
