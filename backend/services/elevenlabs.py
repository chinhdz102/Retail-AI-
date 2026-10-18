from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import Settings, get_settings
from backend.services.speech_stream import SpeechAudioStream


class ElevenLabsConfigurationError(RuntimeError):
    """Raised when the server has no ElevenLabs credential configured."""


class ElevenLabsProviderError(RuntimeError):
    """Raised when ElevenLabs cannot complete a request."""


class ElevenLabsPaymentRequiredError(ElevenLabsProviderError):
    """Raised when a voice requires a paid ElevenLabs plan."""


@dataclass(frozen=True)
class ElevenLabsAudio:
    content: bytes
    content_type: str
    voice_id: str


class ElevenLabsService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._voice_cache: dict[str, str] = {}

    def _client_or_raise(self) -> Any:
        if not self.settings.has_elevenlabs_key:
            raise ElevenLabsConfigurationError("ElevenLabs API key is not configured")
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=self.settings.elevenlabs_base_url.rstrip("/"),
                timeout=self.settings.elevenlabs_timeout_seconds,
            )
        return self.client

    def _headers(self, *, audio: bool = False) -> dict[str, str]:
        headers = {"xi-api-key": self.settings.elevenlabs_api_key.get_secret_value()}
        if audio:
            headers["Accept"] = "audio/mpeg"
        return headers

    async def _list_voices(self) -> list[dict[str, Any]]:
        client = self._client_or_raise()
        try:
            response = await client.get("/v1/voices", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except ElevenLabsConfigurationError:
            raise
        except Exception as exc:
            raise ElevenLabsProviderError("ElevenLabs voice list request failed") from exc
        voices = payload.get("voices", []) if isinstance(payload, dict) else []
        return [voice for voice in voices if isinstance(voice, dict)]

    async def _resolve_voice_id(self, language: str) -> str:
        if language in self._voice_cache:
            return self._voice_cache[language]

        id_setting = getattr(self.settings, f"elevenlabs_{language}_voice_id", "").strip()
        if id_setting:
            self._voice_cache[language] = id_setting
            return id_setting

        name_setting = getattr(self.settings, f"elevenlabs_{language}_voice_name", "").strip()
        voices = await self._list_voices()
        exact_match = next(
            (
                voice
                for voice in voices
                if str(voice.get("name", "")).strip().casefold() == name_setting.casefold()
            ),
            None,
        )
        if exact_match and exact_match.get("voice_id"):
            voice_id = str(exact_match["voice_id"])
            self._voice_cache[language] = voice_id
            return voice_id

        if language != "vi":
            language_match = next(
                (
                    voice
                    for voice in voices
                    if any(
                        str(item.get("language", "")).lower() == language
                        for item in voice.get("verified_languages", [])
                        if isinstance(item, dict)
                    )
                    and voice.get("voice_id")
                ),
                None,
            )
            if language_match:
                voice_id = str(language_match["voice_id"])
                self._voice_cache[language] = voice_id
                return voice_id

        raise ElevenLabsProviderError(
            f"ElevenLabs voice is not available for language {language}"
        )

    async def _request_audio(self, client: Any, text: str, voice_id: str) -> ElevenLabsAudio:
        encoded_voice_id = quote(voice_id, safe="")
        output_format = quote(self.settings.elevenlabs_output_format, safe="")
        url = f"/v1/text-to-speech/{encoded_voice_id}?output_format={output_format}"
        payload = {
            "text": text,
            "model_id": self.settings.elevenlabs_model,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.15,
                "use_speaker_boost": True,
                "speed": self.settings.elevenlabs_speed,
            },
        }
        try:
            response = await client.post(url, headers=self._headers(audio=True), json=payload)
            if getattr(response, "status_code", 200) == 402:
                raise ElevenLabsPaymentRequiredError(
                    "ElevenLabs voice requires a paid plan"
                )
            response.raise_for_status()
            if not response.content:
                raise ValueError("ElevenLabs returned an empty audio response")
            content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
            return ElevenLabsAudio(
                content=response.content,
                content_type=content_type or "audio/mpeg",
                voice_id=voice_id,
            )
        except ElevenLabsPaymentRequiredError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                raise ElevenLabsPaymentRequiredError(
                    "ElevenLabs voice requires a paid plan"
                ) from exc
            raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed") from exc
        except ElevenLabsProviderError:
            raise
        except Exception as exc:
            raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed") from exc

    async def synthesize(self, text: str, language: str = "vi") -> ElevenLabsAudio:
        client = self._client_or_raise()
        voice_id = await self._resolve_voice_id(language)
        try:
            return await self._request_audio(client, text, voice_id)
        except ElevenLabsPaymentRequiredError:
            fallback_voice_id = self.settings.elevenlabs_vi_fallback_voice_id.strip()
            if language != "vi" or not fallback_voice_id or fallback_voice_id == voice_id:
                raise
            fallback_audio = await self._request_audio(client, text, fallback_voice_id)
            self._voice_cache[language] = fallback_voice_id
            return fallback_audio
        except ElevenLabsConfigurationError:
            raise
        except ElevenLabsProviderError:
            raise
        except Exception as exc:
            raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed") from exc

    async def _open_stream_for_voice(
        self, client: Any, text: str, voice_id: str
    ) -> SpeechAudioStream:
        encoded_voice_id = quote(voice_id, safe="")
        output_format = quote(self.settings.elevenlabs_output_format, safe="")
        url = f"/v1/text-to-speech/{encoded_voice_id}?output_format={output_format}"
        payload = {
            "text": text,
            "model_id": self.settings.elevenlabs_model,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.15,
                "use_speaker_boost": True,
                "speed": self.settings.elevenlabs_speed,
            },
        }
        response = None
        try:
            request = client.build_request(
                "POST", url, headers=self._headers(audio=True), json=payload
            )
            response = await client.send(request, stream=True)
            if response.status_code == 402:
                raise ElevenLabsPaymentRequiredError(
                    "ElevenLabs voice requires a paid plan"
                )
            if response.status_code >= 400:
                raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed")
            content_type = response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]
            if not content_type.startswith("audio/"):
                raise ElevenLabsProviderError("ElevenLabs returned a non-audio response")
            return SpeechAudioStream(response, content_type, voice_id)
        except (ElevenLabsPaymentRequiredError, ElevenLabsProviderError):
            if response is not None:
                await response.aclose()
            raise
        except httpx.HTTPStatusError as exc:
            if response is not None:
                await response.aclose()
            if exc.response.status_code == 402:
                raise ElevenLabsPaymentRequiredError(
                    "ElevenLabs voice requires a paid plan"
                ) from exc
            raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed") from exc
        except Exception as exc:
            if response is not None:
                await response.aclose()
            raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed") from exc

    async def open_stream(self, text: str, language: str = "vi") -> SpeechAudioStream:
        if not text.strip():
            raise ElevenLabsProviderError("ElevenLabs text is empty")
        client = self._client_or_raise()
        voice_id = await self._resolve_voice_id(language)
        try:
            return await self._open_stream_for_voice(client, text, voice_id)
        except ElevenLabsPaymentRequiredError:
            fallback_voice_id = self.settings.elevenlabs_vi_fallback_voice_id.strip()
            if language != "vi" or not fallback_voice_id or fallback_voice_id == voice_id:
                raise
            stream = await self._open_stream_for_voice(client, text, fallback_voice_id)
            self._voice_cache[language] = fallback_voice_id
            return stream
        except (ElevenLabsConfigurationError, ElevenLabsProviderError):
            raise
        except Exception as exc:
            raise ElevenLabsProviderError("ElevenLabs text-to-speech request failed") from exc

    async def usage(self) -> dict[str, int | str]:
        client = self._client_or_raise()
        try:
            response = await client.get("/v1/user/subscription", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except ElevenLabsConfigurationError:
            raise
        except Exception as exc:
            raise ElevenLabsProviderError("ElevenLabs usage request failed") from exc

        character_count = int(payload.get("character_count", 0))
        character_limit = int(payload.get("character_limit", 0))
        return {
            "tier": str(payload.get("tier", "unknown")),
            "character_count": character_count,
            "character_limit": character_limit,
            "remaining_characters": max(0, character_limit - character_count),
            "next_character_count_reset_unix": int(
                payload.get("next_character_count_reset_unix", 0)
            ),
        }
