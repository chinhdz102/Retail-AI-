"""Vietnamese-only ASR with the same non-destructive acoustic front end.

One provider decodes a turn. No transcript rewriting, phrase blacklists or
fallback decoder selecting whichever sentence looks more plausible.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import replace
from time import perf_counter

import httpx

from backend.services.speech_activity import SpeechActivityUnavailable, analyze_speech_audio
from backend.services.whisper_stt import (
    WhisperConfigurationError,
    WhisperProviderError,
    WhisperResult,
    WhisperSTT,
    WhisperTimeoutError,
    _prepare_decode_audio,
    _repetition,
    _tokens,
)

MODEL = "azure-conversation-vi-VN"
PIPELINE_VERSION = "vi-azure-20261110-1"


def evaluate_azure_transcript(payload, acoustic) -> WhisperResult:
    result = WhisperResult(model=MODEL, quality=acoustic)
    if not isinstance(payload, dict):
        raise WhisperProviderError("ASR returned an invalid response")
    status = payload.get("RecognitionStatus")
    if status in {"NoMatch", "InitialSilenceTimeout", "BabbleTimeout"}:
        return replace(result, rejection_reason="no_match")
    if status != "Success":
        raise WhisperProviderError("ASR did not complete recognition")
    alternatives = payload.get("NBest")
    if not isinstance(alternatives, list) or not alternatives:
        raise WhisperProviderError("ASR returned no recognition evidence")
    best = alternatives[0]
    text = best.get("Display") if isinstance(best, dict) else None
    confidence = best.get("Confidence") if isinstance(best, dict) else None
    if (not isinstance(text, str) or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float)) or not math.isfinite(confidence)
        or not 0 <= confidence <= 1):
        raise WhisperProviderError("ASR returned incomplete recognition evidence")
    quality = {**acoustic, "azure_confidence": float(confidence)}
    text = text.strip()
    tokens = _tokens(text)
    if not tokens:
        return replace(result, rejection_reason="empty_transcript", quality=quality)
    if _repetition(tokens):
        return replace(result, rejection_reason="repetition", quality=quality)
    # Confidence is diagnostic, not proof of correctness. Do not reject quiet,
    # accented or very short speech using an uncalibrated confidence threshold.
    return replace(result, text=text, speech_detected=True, quality=quality)


class AzureVietnameseSTT(WhisperSTT):
    def __init__(self, settings=None, client=None):
        super().__init__(settings, client)
        self._owns_client = client is None

    def _recognition_client(self):
        # A kiosk makes many short turns. Preserve TCP/TLS between them rather
        # than paying connection setup after every utterance has already ended.
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.settings.whisper_timeout_seconds,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2,
                                    keepalive_expiry=60.0),
            )
        return self.client

    async def aclose(self):
        if self._owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    async def transcribe(self, content: bytes, *, diagnostic_id: str | None = None):
        if self._inflight >= 2:
            raise SpeechActivityUnavailable("Speech recognition capacity is temporarily full")
        self._inflight += 1
        try:
            async with asyncio.timeout(self.settings.whisper_timeout_seconds):
                started = perf_counter()
                analyzed = await asyncio.to_thread(analyze_speech_audio, content)
                acoustic_ms = (perf_counter() - started) * 1000
                activity = analyzed.activity
                if not activity.speech_detected:
                    result = WhisperResult(model=MODEL, rejection_reason="no_speech",
                                           quality={**activity.quality,
                                                    "stt_acoustic_ms": acoustic_ms})
                    await self._trace(diagnostic_id, content, None, None, result)
                    return result
                key = self.settings.azure_speech_key.get_secret_value().strip()
                region = self.settings.azure_speech_region.strip().lower()
                if not key or not re.fullmatch(r"[a-z0-9-]+", region):
                    raise WhisperConfigurationError("Vietnamese ASR is not configured")
                prepared_at = perf_counter()
                wav, gain = await asyncio.to_thread(_prepare_decode_audio, analyzed.samples)
                prepare_ms = (perf_counter() - prepared_at) * 1000
                provider_at = perf_counter()
                payload = await self._recognize(self._recognition_client(), wav, key, region)
                result = evaluate_azure_transcript(payload, {
                    **activity.quality, "asr_input_gain": gain,
                    "stt_acoustic_ms": acoustic_ms,
                    "stt_prepare_ms": prepare_ms,
                    "stt_provider_ms": (perf_counter() - provider_at) * 1000,
                })
                trace_at = perf_counter()
                await self._trace(diagnostic_id, content, wav, payload, result)
                return replace(result, quality={
                    **result.quality, "stt_trace_ms": (perf_counter() - trace_at) * 1000,
                    "stt_total_ms": (perf_counter() - started) * 1000,
                })
        except TimeoutError as exc:
            raise WhisperTimeoutError("ASR exceeded the turn time budget") from exc
        finally:
            self._inflight -= 1

    async def _recognize(self, client, wav, key, region):
        try:
            response = await client.post(
                f"https://{region}.stt.speech.microsoft.com/"
                "speech/recognition/conversation/cognitiveservices/v1",
                params={"language": "vi-VN", "format": "detailed", "profanity": "raw"},
                headers={"Ocp-Apim-Subscription-Key": key,
                         "Accept": "application/json",
                         "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000"},
                content=wav,
            )
            if response.status_code in {401, 403}:
                raise WhisperConfigurationError("Vietnamese ASR credential unavailable")
            response.raise_for_status()
            return response.json()
        except (WhisperConfigurationError, WhisperProviderError):
            raise
        except httpx.TimeoutException as exc:
            raise WhisperTimeoutError("ASR timed out") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WhisperProviderError("Vietnamese ASR is unavailable") from exc
