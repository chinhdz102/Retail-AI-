from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

import httpx

from backend.config import Settings, get_settings
from backend.services.speech_stream import SpeechAudioStream


class AzureSpeechConfigurationError(RuntimeError):
    """Raised when Azure Speech credentials or region are missing or invalid."""


class AzureSpeechProviderError(RuntimeError):
    """Raised when Azure Speech cannot synthesize audio."""


@dataclass(frozen=True)
class AzureSpeechAudio:
    content: bytes
    content_type: str
    voice_id: str


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
        locale, voice_id = voice
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{locale}"><voice name="{voice_id}">{escape(text)}</voice></speak>'
        )
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.OUTPUT_FORMAT,
            "User-Agent": "retail-ai-kiosk",
        }
        try:
            response = await client.post(
                "/cognitiveservices/v1",
                headers=headers,
                content=ssml.encode("utf-8"),
            )
            response.raise_for_status()
            if not response.content:
                raise ValueError("Azure Speech returned empty audio")
            content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
            if not content_type.startswith("audio/"):
                raise ValueError("Azure Speech returned a non-audio response")
            return AzureSpeechAudio(
                content=response.content,
                content_type=content_type,
                voice_id=voice_id,
            )
        except httpx.HTTPStatusError as exc:
            raise AzureSpeechProviderError(
                f"Azure Speech request failed with HTTP {exc.response.status_code}"
            ) from exc
        except AzureSpeechProviderError:
            raise
        except Exception as exc:
            raise AzureSpeechProviderError("Azure Speech synthesis failed") from exc

    async def open_stream(self, text: str, language: str = "vi") -> SpeechAudioStream:
        voice = self.VOICES.get(language)
        if voice is None:
            raise AzureSpeechProviderError("Azure Speech voice is unavailable")
        if not text.strip():
            raise AzureSpeechProviderError("Azure Speech text is empty")

        client, key = self._client_or_raise()
        locale, voice_id = voice
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{locale}"><voice name="{voice_id}">{escape(text)}</voice></speak>'
        )
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.OUTPUT_FORMAT,
            "User-Agent": "retail-ai-kiosk",
        }
        response = None
        try:
            request = client.build_request(
                "POST", "/cognitiveservices/v1", headers=headers, content=ssml.encode("utf-8")
            )
            response = await client.send(request, stream=True)
            if response.status_code >= 400:
                raise AzureSpeechProviderError(
                    f"Azure Speech request failed with HTTP {response.status_code}"
                )
            content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
            if not content_type.startswith("audio/"):
                raise AzureSpeechProviderError("Azure Speech returned a non-audio response")
            return SpeechAudioStream(response, content_type, voice_id)
        except AzureSpeechProviderError:
            if response is not None:
                await response.aclose()
            raise
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
