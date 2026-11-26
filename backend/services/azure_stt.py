"""Azure AI Speech STT service with DPDFNet2 neural noise suppression & PhraseListGrammar.

Replaces Groq Whisper in the live kiosk runtime:
  1. Verifies acoustic speech presence with local Silero VAD v6 + waveform gates.
  2. Trims excess leading/trailing silence/noise using Silero VAD boundaries.
  3. Enhances noisy audio locally via DPDFNet2 ONNX (DeepFilterNet family).
  4. Transcribes with Azure AI Speech (SDK + Thanh Lam Mart PhraseListGrammar primary,
     keep-alive REST v1 secondary, local ONNX Gipformer1.5 offline fallback).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import threading
import wave
from collections.abc import Sequence
from time import perf_counter
from typing import Any

import httpx
import numpy as np

from backend.config import Settings, get_settings
from backend.services.groq import (
    GroqAudioInputError,
    GroqConfigurationError,
    GroqProviderError,
    GroqTranscriptionResult,
    _has_insufficient_acoustic_support,
    _has_repeated_phrase,
    _is_impulsive_non_speech,
    _is_low_level_non_speech,
    _is_near_silence,
    _is_steady_tone,
    _is_steady_unvoiced_noise,
    _wav_signal_quality,
)
from backend.services.speech_activity import (
    SAMPLE_RATE,
    SpeechActivityInputError,
    SpeechActivityUnavailable,
    analyze_speech_audio,
)
from backend.services.speech_denoiser import enhance_speech_samples

logger = logging.getLogger(__name__)

_LOCALE_BY_LANGUAGE: dict[str, str] = {
    "vi": "vi-VN",
    "en": "en-US",
    "zh": "zh-CN",
}

# High-value Thanh Lam Mart retail/supermarket terms that benefit from Azure PhraseListGrammar.
_BASE_RETAIL_PHRASES_VI: tuple[str, ...] = (
    "Thanh Lam Mart",
    "Thanh Lam Home",
    "Thanh Lam Good",
    "PayOS",
    "giỏ hàng",
    "tồn kho",
    "ACEFOODS",
    "TH True Milk",
    "Vinamilk",
    "OMO Matic",
    "Coca-Cola Zero",
    "Head & Shoulders",
    "Hảo Hảo",
    "Omachi",
    "Nam Ngư",
    "Chin-su",
    "Chinsu",
    "tương ớt Chin-su",
    "Cholimex",
    "Nam Dương",
    "Selsun",
    "dầu gội Selsun",
    "Sensodyne",
    "Lifebuoy",
    "Comfort",
    "Huggies",
    "Yakult",
    "Simply",
    "Mollis",
    "khăn mặt cotton",
    "ba chỉ bò Mỹ",
    "sữa tươi ít đường",
    "sữa tươi không đường",
    "nước giặt",
    "dầu gội trị gàu",
    "bán được nhiều nhất",
    "bán chạy nhất",
)


def _encode_pcm16_bytes(samples: np.ndarray) -> bytes:
    return np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def _encode_wav16(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(_encode_pcm16_bytes(samples))
    return buf.getvalue()


def _build_phrase_list(
    language: str, vocabulary: Sequence[str] | None = None
) -> list[str]:
    if language != "vi":
        return []
    phrases: list[str] = []
    seen: set[str] = set()
    for term in (*(vocabulary or ()), *_BASE_RETAIL_PHRASES_VI):
        cleaned = " ".join(str(term or "").split())[:64]
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            phrases.append(cleaned)
        if len(phrases) >= 48:
            break
    return phrases


_WARM_SDK_MAX_AGE_SECONDS = 35.0
_STS_TOKEN_TTL_SECONDS = 480.0
_BASE_PHRASES_VI_KEYS = frozenset(term.casefold() for term in _BASE_RETAIL_PHRASES_VI)


class AzureSTTService:
    """Primary Vietnamese/EN/ZH Speech-to-Text backed by Azure AI Speech + DPDFNet2."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: Any | None = None,
        sts_client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._rest_client: Any | None = client
        self._sts_client: Any | None = sts_client
        self._injected_rest_client: bool = client is not None
        self._sdk_lock = threading.Lock()
        self._warm_sdk: dict[str, tuple[Any, Any, Any, float]] = {}
        self._sts_token_cache: tuple[str, str, float] | None = None
        self._sts_lock = asyncio.Lock()

    async def issue_auth_token(self) -> tuple[str, str, int]:
        """Issue (or return cached) 10-minute Azure Speech STS token for direct browser WSS."""
        key, region = self._credentials_or_raise()
        now = perf_counter()
        cached = self._sts_token_cache
        if cached is not None and cached[1] == region:
            age = now - cached[2]
            if age < _STS_TOKEN_TTL_SECONDS:
                remaining = max(30, int(600 - age))
                return cached[0], region, remaining

        async with self._sts_lock:
            now = perf_counter()
            cached = self._sts_token_cache
            if cached is not None and cached[1] == region:
                age = now - cached[2]
                if age < _STS_TOKEN_TTL_SECONDS:
                    remaining = max(30, int(600 - age))
                    return cached[0], region, remaining

            sts_url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
            headers = {
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "0",
            }
            if self._sts_client is not None:
                resp = await self._sts_client.post(sts_url, headers=headers, timeout=6.0)
            else:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    resp = await client.post(sts_url, headers=headers)
            resp.raise_for_status()
            token = resp.text.strip()
            if not token:
                raise GroqProviderError("Azure Speech STS returned an empty token")
            self._sts_token_cache = (token, region, perf_counter())
            return token, region, 540

    def _credentials_or_raise(self) -> tuple[str, str]:
        key = self.settings.azure_speech_key.get_secret_value().strip()
        region = self.settings.azure_speech_region.strip().lower()
        if not key or not region:
            raise GroqConfigurationError("Azure Speech key and region are not configured")
        if not re.fullmatch(r"[a-z0-9-]+", region):
            raise GroqConfigurationError("Azure Speech region is invalid")
        return key, region

    def _rest_client_or_create(self, region: str) -> Any:
        if self._rest_client is None:
            self._rest_client = httpx.AsyncClient(
                base_url=f"https://{region}.stt.speech.microsoft.com",
                timeout=min(self.settings.azure_speech_timeout_seconds, 15.0),
                limits=httpx.Limits(
                    max_connections=16,
                    max_keepalive_connections=8,
                    keepalive_expiry=60.0,
                ),
            )
        return self._rest_client

    def _create_sdk_slot(
        self,
        locale: str,
        key: str,
        region: str,
        *,
        preopen: bool,
    ) -> tuple[Any, Any, Any, float]:
        import azure.cognitiveservices.speech as speechsdk

        cfg = speechsdk.SpeechConfig(subscription=key, region=region)
        cfg.speech_recognition_language = locale
        # PushAudioInputStream calls push.close() right after writing the full WAV,
        # so EOF triggers immediate finalization without waiting on silence timers.
        # Keeping 1200 ms prevents recognize_once_async() from chopping off the
        # second half of a sentence whenever the shopper pauses briefly mid-clause.
        cfg.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "1200"
        )
        cfg.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs, "1200"
        )
        fmt = speechsdk.audio.AudioStreamFormat(
            samples_per_second=SAMPLE_RATE, bits_per_sample=16, channels=1
        )
        push = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
        audio_cfg = speechsdk.audio.AudioConfig(stream=push)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=cfg, audio_config=audio_cfg
        )
        if locale == "vi-VN":
            grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
            for phrase in _BASE_RETAIL_PHRASES_VI:
                grammar.addPhrase(phrase)
        conn = speechsdk.Connection.from_recognizer(recognizer)
        if preopen:
            conn.open(False)
        return recognizer, push, conn, perf_counter()

    def _replenish_warm_sdk(self, locale: str, key: str, region: str) -> None:
        try:
            with self._sdk_lock:
                existing = self._warm_sdk.get(locale)
                if (
                    existing is not None
                    and (perf_counter() - existing[3]) < _WARM_SDK_MAX_AGE_SECONDS
                ):
                    return
            slot = self._create_sdk_slot(locale, key, region, preopen=True)
            with self._sdk_lock:
                old = self._warm_sdk.get(locale)
                self._warm_sdk[locale] = slot
            if old is not None:
                try:
                    old[2].close()
                except Exception:
                    pass
        except Exception:
            pass

    async def preconnect(self, language: str = "vi") -> None:
        """Warm both the Azure REST keep-alive pool and a pre-opened SDK WebSocket."""
        try:
            key, region = self._credentials_or_raise()
            client = self._rest_client_or_create(region)
            await client.get(
                "/",
                headers={"Ocp-Apim-Subscription-Key": key},
                timeout=3.0,
            )
            if self.settings.azure_stt_use_sdk and not self._injected_rest_client:
                locale = _LOCALE_BY_LANGUAGE.get(language, "vi-VN")
                await asyncio.to_thread(self._replenish_warm_sdk, locale, key, region)
        except Exception:
            pass

    def _recognize_sdk_sync(
        self,
        pcm_bytes: bytes,
        locale: str,
        phrases: list[str],
        key: str,
        region: str,
    ) -> tuple[str, dict[str, float]]:
        import azure.cognitiveservices.speech as speechsdk

        started = perf_counter()
        warm_slot: tuple[Any, Any, Any, float] | None = None
        with self._sdk_lock:
            candidate = self._warm_sdk.pop(locale, None)
            if candidate is not None:
                if (perf_counter() - candidate[3]) < _WARM_SDK_MAX_AGE_SECONDS:
                    warm_slot = candidate
                else:
                    try:
                        candidate[2].close()
                    except Exception:
                        pass

        # Pre-open the next turn's WebSocket in parallel so back-to-back turns pay 0ms handshake.
        threading.Thread(
            target=self._replenish_warm_sdk,
            args=(locale, key, region),
            daemon=True,
        ).start()

        def _run_once(
            slot: tuple[Any, Any, Any, float], *, base_loaded: bool
        ) -> Any:
            recognizer, push, _conn, _ts = slot
            extra_phrases = [
                p for p in phrases if not base_loaded or p.casefold() not in _BASE_PHRASES_VI_KEYS
            ]
            if extra_phrases:
                grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
                for phrase in extra_phrases:
                    grammar.addPhrase(phrase)
            push.write(pcm_bytes)
            push.close()
            return recognizer.recognize_once_async().get()

        reused = warm_slot is not None
        if warm_slot is not None:
            result = _run_once(warm_slot, base_loaded=(locale == "vi-VN"))
            if result.reason == speechsdk.ResultReason.Canceled:
                # Stale pre-opened socket; transparently retry once with a fresh socket.
                reused = False
                fresh = self._create_sdk_slot(locale, key, region, preopen=False)
                result = _run_once(fresh, base_loaded=(locale == "vi-VN"))
        else:
            fresh = self._create_sdk_slot(locale, key, region, preopen=False)
            result = _run_once(fresh, base_loaded=(locale == "vi-VN"))

        elapsed_ms = (perf_counter() - started) * 1000.0
        metrics = {
            "azure_stt_ms": round(elapsed_ms, 1),
            "azure_sdk_preopened": 1.0 if reused else 0.0,
        }
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            return result.text.strip(), metrics
        if result.reason == speechsdk.ResultReason.NoMatch:
            return "", metrics
        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            raise RuntimeError(
                f"Azure Speech SDK canceled: {details.reason} ({details.error_details})"
            )
        raise RuntimeError(f"Azure Speech SDK unexpected reason: {result.reason}")

    async def _recognize_rest(
        self,
        wav_bytes: bytes,
        locale: str,
        key: str,
        region: str,
    ) -> tuple[str, dict[str, float]]:
        started = perf_counter()
        client = self._rest_client_or_create(region)
        response = await client.post(
            "/speech/recognition/conversation/cognitiveservices/v1",
            params={"language": locale, "format": "detailed"},
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                "Accept": "application/json",
            },
            content=wav_bytes,
            timeout=min(self.settings.azure_speech_timeout_seconds, 12.0),
        )
        response.raise_for_status()
        payload = response.json()
        elapsed_ms = (perf_counter() - started) * 1000.0
        status = str(payload.get("RecognitionStatus") or "")
        if status == "Success":
            nbest = payload.get("NBest") or []
            top = nbest[0] if nbest else {}
            text = str(top.get("Display") or payload.get("DisplayText") or "").strip()
            quality: dict[str, float] = {"azure_stt_ms": round(elapsed_ms, 1)}
            conf = top.get("Confidence")
            if isinstance(conf, (int, float)):
                quality["azure_confidence"] = round(float(conf), 4)
            return text, quality
        if status in {"NoMatch", "InitialSilenceTimeout", "BabbleTimeout"}:
            return "", {"azure_stt_ms": round(elapsed_ms, 1)}
        raise RuntimeError(f"Azure Speech REST status={status}")

    async def transcribe(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        language: str = "vi",
        vocabulary: Sequence[str] | None = None,
        *,
        skip_denoiser: bool = False,
    ) -> GroqTranscriptionResult:
        """Transcribe audio using Silero VAD + DPDFNet2 AI denoiser + Azure Speech STT."""
        del filename, content_type
        key, region = self._credentials_or_raise()
        locale = _LOCALE_BY_LANGUAGE.get(language, "vi-VN")

        # 1. Local Silero VAD v6 independent speech verification
        try:
            analyzed = await asyncio.to_thread(analyze_speech_audio, content)
        except SpeechActivityInputError as exc:
            raise GroqAudioInputError(
                "Audio could not be decoded within analysis limits"
            ) from exc
        except SpeechActivityUnavailable as exc:
            raise GroqProviderError("Acoustic verification unavailable") from exc

        acoustic_quality = dict(analyzed.activity.quality)
        if not analyzed.activity.speech_detected:
            logger.info("Azure STT rejected clip reason=no_acoustic_speech")
            rejected_trace = [
                {
                    "model": "azure-speech-stt",
                    "status": "rejected",
                    "reason": "no_acoustic_speech",
                }
            ]
            return GroqTranscriptionResult(
                "",
                "azure-speech-stt",
                rejected_trace,
                speech_detected=False,
                quality=acoustic_quality,
            )

        # 2. Waveform non-speech gate (rejects pure hum, steady tones, clicks).
        # Skip the pure-Python WAV scan (~115 ms) when Silero VAD v6 already
        # strongly confirms clear human speech; still run it on borderline clips.
        strong_vad_speech = (
            acoustic_quality.get("server_vad_positive_frame_ratio", 0.0) >= 0.35
            and acoustic_quality.get("server_vad_max_probability", 0.0) >= 0.85
            and acoustic_quality.get("server_vad_speech_seconds", 0.0) >= 0.35
        )
        signal_quality = (
            None
            if strong_vad_speech
            else await asyncio.to_thread(
                _wav_signal_quality,
                content,
                acoustic_quality.get("server_vad_analysis_gain", 1.0),
            )
        )
        if signal_quality is not None:
            for predicate, reason in (
                (_is_near_silence, "near_silence"),
                (_is_low_level_non_speech, "low_level_non_speech"),
                (_is_steady_unvoiced_noise, "steady_unvoiced_noise"),
                (_is_steady_tone, "steady_tone"),
                (_is_impulsive_non_speech, "impulsive_non_speech"),
            ):
                if predicate(signal_quality):
                    logger.info("Azure STT rejected clip reason=%s", reason)
                    return GroqTranscriptionResult(
                        "",
                        "azure-speech-stt",
                        [{"model": "azure-speech-stt", "status": "rejected", "reason": reason}],
                        speech_detected=False,
                        quality={**signal_quality, **acoustic_quality},
                    )

        # 3. Dedicated AI neural speech enhancement (DPDFNet2 ONNX + VAD trim + 85 Hz HPF).
        # Skip heavy server neural denoising when the user's browser already ran GTCRN WASM
        # or on Vercel serverless where denoising runs client-side to save server CPU/RAM.
        run_server_denoiser = (
            self.settings.speech_denoiser_enabled
            and not skip_denoiser
            and not os.getenv("VERCEL")
        )
        enhanced = await asyncio.to_thread(
            enhance_speech_samples,
            analyzed.samples,
            enabled=run_server_denoiser,
            model_path=self.settings.speech_denoiser_model_path,
            speech_start_sample=analyzed.speech_start_sample,
            speech_end_sample=analyzed.speech_end_sample,
        )
        quality = {**acoustic_quality, **enhanced.quality}
        phrases = _build_phrase_list(language, vocabulary)
        pcm_bytes = _encode_pcm16_bytes(enhanced.samples)
        wav_bytes = _encode_wav16(enhanced.samples)

        # 4. Primary: Azure Speech REST v1 (fast ~0.8-1.0s keep-alive) OR SDK+PhraseList
        #    When SDK + PhraseList is enabled (default), use SDK for superior brand/SKU accuracy
        #    with automatic fallback to Azure REST v1 and local Gipformer1.5 (0 Groq calls!).
        fallback_trace: list[dict[str, Any]] = []
        text = ""
        used_model = f"azure-stt:{locale}"
        if enhanced.denoised:
            used_model += f"+{enhanced.model_name}"

        use_sdk = bool(self.settings.azure_stt_use_sdk) and not self._injected_rest_client
        succeeded = False
        if use_sdk:
            try:
                text, stt_q = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._recognize_sdk_sync, pcm_bytes, locale, phrases, key, region
                    ),
                    timeout=min(self.settings.azure_speech_timeout_seconds, 12.0),
                )
                quality.update(stt_q)
                fallback_trace.append({"model": "azure-speech-sdk", "status": "success"})
                used_model = f"azure-sdk:{locale}" + (
                    f"+{enhanced.model_name}" if enhanced.denoised else ""
                )
                succeeded = True
            except Exception as exc:
                logger.warning("Azure Speech SDK STT failed, falling back to Azure REST: %s", exc)
                fallback_trace.append(
                    {
                        "model": "azure-speech-sdk",
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                )

        if not succeeded:
            try:
                text, stt_q = await self._recognize_rest(wav_bytes, locale, key, region)
                quality.update(stt_q)
                fallback_trace.append({"model": "azure-speech-rest", "status": "success"})
                used_model = f"azure-rest:{locale}" + (
                    f"+{enhanced.model_name}" if enhanced.denoised else ""
                )
                succeeded = True
            except Exception as exc:
                logger.warning("Azure Speech REST STT failed: %s", exc)
                fallback_trace.append(
                    {
                        "model": "azure-speech-rest",
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                )

        if not succeeded and language == "vi":
            # Offline-resilient local Vietnamese ONNX fallback (zero Groq dependency!)
            try:
                from backend.services.vietnamese_asr import (
                    MODEL_NAME as LOCAL_VI_MODEL,
                )
                from backend.services.vietnamese_asr import (
                    transcribe_vietnamese_samples,
                )

                local_dir = self.settings.vietnamese_stt_model_dir
                text = await asyncio.to_thread(
                    transcribe_vietnamese_samples,
                    enhanced.samples,
                    local_dir,
                    "greedy_search",
                )
                used_model = f"local:{LOCAL_VI_MODEL}"
                fallback_trace.append({"model": used_model, "status": "success"})
                succeeded = True
            except Exception as exc:
                fallback_trace.append(
                    {
                        "model": "local-vietnamese-asr",
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                )

        if not succeeded:
            raise GroqProviderError(
                "Azure Speech recognition failed",
                fallback_trace=fallback_trace,
            )

        if not text.strip():
            return GroqTranscriptionResult(
                "",
                used_model,
                [*fallback_trace, {"model": used_model, "status": "rejected", "reason": "empty"}],
                speech_detected=False,
                quality=quality,
            )

        if _has_repeated_phrase(text) or _has_insufficient_acoustic_support(
            text, quality, language
        ):
            return GroqTranscriptionResult(
                "",
                used_model,
                [
                    *fallback_trace,
                    {"model": used_model, "status": "rejected", "reason": "quality_gate"},
                ],
                speech_detected=False,
                quality=quality,
            )

        logger.info(
            "Azure STT completed model=%s denoise=%s snr_db=%s stt_ms=%s",
            used_model,
            enhanced.model_name,
            quality.get("denoiser_estimated_snr_db"),
            quality.get("azure_stt_ms"),
        )
        return GroqTranscriptionResult(
            text.strip(),
            used_model,
            fallback_trace,
            speech_detected=True,
            quality=quality,
        )
