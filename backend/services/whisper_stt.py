"""Vietnamese Whisper: acoustic verification -> one decode -> evidence checks.

No vocabulary prompt, language guessing, model fallback or transcript rewriting.
Rejected audio returns no text; it never gets another generative decoding attempt.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
import wave
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from backend.config import Settings, get_settings
from backend.services.speech_activity import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    SpeechActivityUnavailable,
    analyze_speech_audio,
)

MODEL = "whisper-large-v3"
PIPELINE_VERSION = "vi-whisper-20261110-4"


class WhisperConfigurationError(RuntimeError):
    pass


class WhisperProviderError(RuntimeError):
    pass


class WhisperTimeoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class WhisperResult:
    text: str = ""
    model: str = MODEL
    speech_detected: bool = False
    rejection_reason: str | None = None
    quality: dict[str, float] = field(default_factory=dict)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)


def _repetition(tokens: list[str]) -> bool:
    if len(tokens) >= 6 and len(set(tokens)) == 1:
        return True
    for size in range(2, min(16, len(tokens) // 3) + 1):
        for start in range(len(tokens) - size * 3 + 1):
            phrase = tokens[start : start + size]
            repeats = 1
            while tokens[start + repeats * size : start + (repeats + 1) * size] == phrase:
                repeats += 1
            if repeats >= 3 and repeats * size >= len(tokens) * 0.6:
                return True
    return False


def _number(segment: dict, name: str) -> float:
    value = segment.get(name)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise WhisperProviderError("Whisper returned incomplete segment evidence")
    if not math.isfinite(value):
        raise WhisperProviderError("Whisper returned invalid segment evidence")
    return float(value)


def evaluate_transcript(payload: Any, acoustic: dict[str, float]) -> WhisperResult:
    """Validate provider evidence without matching or censoring specific phrases."""
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise WhisperProviderError("Whisper returned an invalid response")
    quality = dict(acoustic)

    def reject(reason: str) -> WhisperResult:
        return WhisperResult(rejection_reason=reason, quality=quality)

    if not payload["text"].strip():
        return reject("empty_transcript")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise WhisperProviderError("Whisper did not return segment evidence")
    texts: list[str] = []
    probabilities: list[float] = []
    logprobs: list[float] = []
    compressions: list[float] = []
    uncertain = False
    duration = acoustic["server_vad_duration_seconds"]
    previous_end = 0.0
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise WhisperProviderError("Whisper returned invalid segments")
        start, end = _number(segment, "start"), _number(segment, "end")
        probability = _number(segment, "no_speech_prob")
        logprob = _number(segment, "avg_logprob")
        compression = _number(segment, "compression_ratio")
        if (
            start < 0
            or end <= start
            or start < previous_end - 0.1
            or not 0 <= probability <= 1
            or logprob > 0
            or compression < 0
        ):
            raise WhisperProviderError("Whisper returned invalid segment evidence")
        if end > duration + 0.5:
            return reject("unaligned_timestamps")
        previous_end = end
        probabilities.append(probability)
        logprobs.append(logprob)
        compressions.append(compression)
        if segment["text"].strip():
            # Whisper's no-voice decision combines these signals. Either signal
            # alone also occurs in real speech, especially brief replies.
            uncertain |= probability > 0.6 and logprob <= -1.0
            texts.append(segment["text"].strip())
    quality.update(
        no_speech_prob=max(probabilities),
        avg_logprob=min(logprobs),
        compression_ratio=max(compressions),
        low_confidence=float(min(logprobs) < -1.0),
    )
    text = " ".join(texts).strip()
    tokens = _tokens(text)
    if not tokens:
        return reject("empty_transcript")
    if _tokens(payload["text"]) != tokens:
        raise WhisperProviderError("Whisper text and segment evidence disagree")
    if uncertain:
        return reject("uncertain_segments")
    if _repetition(tokens):
        return reject("repetition")
    speech_seconds = acoustic["server_vad_positive_frame_count"] * FRAME_SAMPLES / SAMPLE_RATE
    quality["tokens_per_speech_second"] = len(tokens) / max(speech_seconds, 0.032)
    if speech_seconds < 0.6 and (min(logprobs) < -0.8 or max(probabilities) >= 0.5):
        return reject("uncertain_sparse_speech")
    # Vietnamese orthographic words include syllables. This deliberately generous
    # bound only blocks long text supported by a tiny amount of acoustic speech.
    # Regression: 15 words of a subscription outro from 416 ms of cow audio.
    if len(tokens) >= 8 and len(tokens) > speech_seconds * 30:
        return reject("insufficient_acoustic_support")
    return WhisperResult(text=text, speech_detected=True, quality=quality)


def _wav(samples: np.ndarray) -> bytes:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


def _prepare_decode_audio(samples: np.ndarray) -> tuple[bytes, float]:
    """Recover quiet microphone level after acoustic verification, without clipping.

    Previously only VAD saw a louder signal; Whisper still received the quiet
    original and could lose words which had just passed the speech detector.
    """
    gain = 1.0
    if samples.size:
        rms = float(np.sqrt(np.mean(samples * samples)))
        peak = float(np.max(np.abs(samples)))
        if 0 < rms < 0.01 and peak > 0:
            gain = max(1.0, min(32.0, 0.05 / rms, 0.95 / peak))
    return _wav(samples * gain), gain


class WhisperSTT:
    def __init__(
        self, settings: Settings | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._inflight = 0

    def _keys(self) -> list[str]:
        keys = [
            getattr(self.settings, name).get_secret_value().strip()
            for name in (
                "groq_api_key",
                "groq_api_key_fallback_1",
                "groq_api_key_fallback_2",
                "groq_api_key_fallback_3",
                "groq_api_key_fallback_4",
                "groq_api_key_fallback_5",
            )
        ]
        return list(dict.fromkeys(key for key in keys if key))

    async def transcribe(
        self, content: bytes, *, diagnostic_id: str | None = None,
    ) -> WhisperResult:
        # Check/increment without an await; bound whole turns, including cloud IO.
        if self._inflight >= 2:
            raise SpeechActivityUnavailable("Speech recognition capacity is temporarily full")
        self._inflight += 1
        try:
            async with asyncio.timeout(self.settings.whisper_timeout_seconds):
                # Slots are owned inside the native worker, including after a
                # disconnected caller or timeout cancels this await.
                analyzed = await asyncio.to_thread(analyze_speech_audio, content)
                activity = analyzed.activity
                if not activity.speech_detected:
                    result = WhisperResult(
                        rejection_reason="no_speech",
                        quality=activity.quality,
                    )
                    await self._trace(diagnostic_id, content, None, None, result)
                    return result
                keys = self._keys()
                if not keys:
                    raise WhisperConfigurationError("Whisper credential is not configured")
                wav, gain = await asyncio.to_thread(_prepare_decode_audio, analyzed.samples)
                if self.client is not None:
                    payload = await self._decode(self.client, wav, keys)
                else:
                    async with httpx.AsyncClient(
                        timeout=self.settings.whisper_timeout_seconds,
                    ) as client:
                        payload = await self._decode(client, wav, keys)
                result = evaluate_transcript(
                    payload, {**activity.quality, "whisper_input_gain": gain},
                )
                await self._trace(diagnostic_id, content, wav, payload, result)
                return result
        except TimeoutError as exc:
            raise WhisperTimeoutError("Whisper exceeded the turn time budget") from exc
        finally:
            self._inflight -= 1

    async def _trace(self, identifier, content, wav, payload, result):
        if (identifier and self.settings.stt_debug_capture
            and self.settings.app_env == "development"):
            from backend.services.stt_diagnostics import save_trace
            await asyncio.to_thread(save_trace, identifier, content, wav, payload, result)

    async def _decode(self, client: httpx.AsyncClient, wav: bytes, keys: list[str]) -> Any:
        for key in keys:
            try:
                response = await client.post(
                    self.settings.groq_base_url.rstrip("/") + "/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": ("voice.wav", wav, "audio/wav")},
                    data={
                        "model": MODEL,
                        "language": "vi",
                        "temperature": "0",
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                    },
                )
            except httpx.TimeoutException as exc:
                raise WhisperTimeoutError("Whisper provider timed out") from exc
            except httpx.HTTPError as exc:
                raise WhisperProviderError("Whisper provider connection failed") from exc
            # Rotate credentials only on auth/quota errors, never after decoding
            # silence, rejected text, or an empty result. Same Whisper model.
            if response.status_code in {401, 403, 429}:
                continue
            if not response.is_success:
                raise WhisperProviderError("Whisper provider request failed")
            try:
                return response.json()
            except ValueError as exc:
                raise WhisperProviderError("Whisper returned invalid JSON") from exc
        raise WhisperProviderError("Whisper credentials unavailable or rate limited")
