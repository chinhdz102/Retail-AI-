from __future__ import annotations

import inspect
import io
import logging
import math
import re
import sys
import wave
from array import array
from collections.abc import Sequence
from time import perf_counter
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
STT_HIGH_NO_SPEECH_PROBABILITY = 0.5
STT_LOW_LOG_PROBABILITY = -0.75
STT_HIGH_COMPRESSION_RATIO = 2.4
STT_LOW_LEVEL_RMS_THRESHOLD = 0.03
STT_LOW_LEVEL_PEAK_THRESHOLD = 0.15
STT_MIN_VOICED_FRAME_RATIO = 0.15
STT_MIN_PERIODICITY = 0.58
STT_STEADY_NOISE_ENVELOPE_CV = 0.15
STT_TRANSIENT_CREST_FACTOR = 8.0
STT_TRANSIENT_ENVELOPE_CV = 1.0
STT_MAX_TRANSIENT_ACTIVE_FRAME_RATIO = 0.15
MAX_GROQ_STT_PROMPT_BYTES = 880


def _rate_limit_headers(headers: Any) -> dict[str, str]:
    if headers is None or not hasattr(headers, "get"):
        return {}
    return {
        name: str(value)
        for name in RATE_LIMIT_HEADER_NAMES
        if (value := headers.get(name)) is not None
    }


RETAIL_STT_PROMPT = (
    "Tiếng Việt giao tiếp tự nhiên tại cửa hàng; giọng Bắc, Trung, Nam và khẩu ngữ "
    "(hông, hem, mô, chi, nhiêu). "
    "Chép sát lời; giữ dấu, tên riêng và viết tắt; không đoán, dịch, tóm tắt hoặc thêm lời. "
    "Có thể hỏi hàng, giá, khuyến mãi, công dụng, vị trí, tồn kho; hoặc mặt bằng, nhập hàng, "
    "nhu cầu, đồ ăn vặt, cổng trường, mô hình bán lẻ, vốn, dòng tiền. "
    "Chỉ chép tên hàng trong gợi ý catalog khi nghe rõ trong audio; không tự chèn hoặc đoán tên. "
    "Đơn vị thường gặp: chai, lon, hộp, gói, lốc, thùng, ml, lít. "
    "{catalog_terms}"
    "Không nghe rõ lời thì để trống."
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
    speech_detected: bool
    quality: dict[str, float]

    def __new__(
        cls,
        text: str,
        model: str,
        fallback_trace: list[dict[str, Any]] | None = None,
        speech_detected: bool = True,
        quality: dict[str, float] | None = None,
    ) -> GroqTranscriptionResult:
        result = super().__new__(cls, text)
        result.model = model
        result.fallback_trace = fallback_trace or []
        result.speech_detected = speech_detected
        result.quality = quality or {}
        return result


def _field(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _mean_centered_rms(frame: Sequence[int]) -> float:
    if not frame:
        return 0.0
    mean = sum(frame) / len(frame)
    return math.sqrt(sum((sample - mean) ** 2 for sample in frame) / len(frame))


def _wav_signal_quality(content: bytes) -> dict[str, float] | None:
    """Measure PCM WAV signal level without decoding compressed audio formats."""
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            if audio.getcomptype() != "NONE" or audio.getsampwidth() != 2:
                return None
            channels = audio.getnchannels()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            samples = array("h")
            samples.frombytes(audio.readframes(frame_count))
    except (EOFError, OSError, wave.Error):
        return None

    if sys.byteorder != "little":
        samples.byteswap()
    if not samples or channels < 1 or sample_rate < 1:
        return {
            "waveform_rms": 0.0,
            "waveform_peak": 0.0,
            "audio_duration_seconds": 0.0,
        }

    mean = sum(samples) / len(samples)
    peak = max(abs(sample - mean) for sample in samples) / 32768.0
    mean_square = sum((sample - mean) ** 2 for sample in samples) / len(samples)
    rms = math.sqrt(mean_square) / 32768.0
    quality = {
        "waveform_rms": rms,
        "waveform_peak": peak,
        "audio_duration_seconds": frame_count / sample_rate,
    }
    if channels == 1 and quality["audio_duration_seconds"] >= 1.0 and rms > 0:
        frame_size = max(1, sample_rate // 10)
        envelope_cv = 1.0
        transient_candidate = False
        frame_rms = [
            _mean_centered_rms(samples[start : start + frame_size])
            for start in range(0, len(samples) - frame_size + 1, frame_size)
        ]
        if len(frame_rms) >= 8:
            mean_frame_rms = sum(frame_rms) / len(frame_rms)
            envelope_cv = (
                math.sqrt(
                    sum((value - mean_frame_rms) ** 2 for value in frame_rms) / len(frame_rms)
                )
                / mean_frame_rms
                if mean_frame_rms > 0
                else 0.0
            )
            quality["waveform_crest_factor"] = peak / rms
            quality["waveform_envelope_cv"] = envelope_cv
            active_frame_floor = max(12.0, mean_frame_rms * 0.08)
            active_frame_ratio = sum(value >= active_frame_floor for value in frame_rms) / len(
                frame_rms
            )
            quality["waveform_active_frame_ratio"] = active_frame_ratio
            transient_candidate = (
                quality["waveform_crest_factor"] >= STT_TRANSIENT_CREST_FACTOR
                and envelope_cv >= STT_TRANSIENT_ENVELOPE_CV
                and active_frame_ratio <= STT_MAX_TRANSIENT_ACTIVE_FRAME_RATIO
            )
        if (
            (rms <= STT_LOW_LEVEL_RMS_THRESHOLD and peak <= STT_LOW_LEVEL_PEAK_THRESHOLD)
            or envelope_cv <= STT_STEADY_NOISE_ENVELOPE_CV
            or transient_candidate
        ):
            quality.update(_wav_voiced_signal_quality(samples, sample_rate, rms))
    return quality


def _wav_voiced_signal_quality(
    samples: array, sample_rate: int, waveform_rms: float
) -> dict[str, float]:
    """Estimate whether quiet PCM contains voiced, speech-like periodic frames.

    Whisper can assign high confidence to complete sentences generated from steady
    room noise. This low-cost, language-independent check is only run for low-level
    PCM, and is used as a rejection guard rather than as a transcript generator.
    """
    stride = max(1, round(sample_rate / 16_000))
    analysis_rate = sample_rate / stride
    frame_size = max(160, round(analysis_rate * 0.04))
    frame_hop = max(frame_size, round(analysis_rate * 0.1))
    min_lag = max(20, round(analysis_rate / 400))
    max_lag = max(min_lag + 1, round(analysis_rate / 70))
    lag_step = max(2, round(analysis_rate / 4_000))
    active_rms_floor = max(0.00035, waveform_rms * 0.08)

    active_frames = 0
    voiced_frames = 0
    periodicity_sum = 0.0
    analyzed_until = min(len(samples), sample_rate * 30)
    for start in range(0, analyzed_until - frame_size * stride + 1, frame_hop * stride):
        frame = [
            float(samples[index]) for index in range(start, start + frame_size * stride, stride)
        ]
        mean = sum(frame) / len(frame)
        centered = [value - mean for value in frame]
        frame_rms = math.sqrt(sum(value * value for value in centered) / len(centered)) / 32768.0
        if frame_rms < active_rms_floor:
            continue

        active_frames += 1
        best_correlation = 0.0
        for lag in range(min_lag, min(max_lag, frame_size - 2), lag_step):
            left = centered[:-lag]
            right = centered[lag:]
            left_energy = sum(value * value for value in left)
            right_energy = sum(value * value for value in right)
            denominator = math.sqrt(left_energy * right_energy)
            if denominator <= 0:
                continue
            correlation = sum(a * b for a, b in zip(left, right)) / denominator
            best_correlation = max(best_correlation, correlation)

        periodicity_sum += best_correlation
        if best_correlation >= 0.6:
            voiced_frames += 1

    if active_frames == 0:
        return {
            "waveform_active_frame_count": float(active_frames),
            "waveform_periodicity_mean": 0.0,
            "waveform_voiced_frame_ratio": 0.0,
        }
    return {
        "waveform_active_frame_count": float(active_frames),
        "waveform_periodicity_mean": periodicity_sum / active_frames,
        "waveform_voiced_frame_ratio": voiced_frames / active_frames,
    }


def _has_voiced_signal(quality: dict[str, float]) -> bool:
    return (
        quality.get("waveform_active_frame_count", 0.0) >= 3
        and quality.get("waveform_voiced_frame_ratio", 0.0) >= STT_MIN_VOICED_FRAME_RATIO
        and quality.get("waveform_periodicity_mean", 0.0) >= STT_MIN_PERIODICITY
    )


def _is_near_silence(quality: dict[str, float]) -> bool:
    """Reject only PCM that is effectively digital silence, preserving quiet speech."""
    if not (
        quality.get("waveform_rms", 1.0) < 0.0015 and quality.get("waveform_peak", 1.0) < 0.012
    ):
        return False
    return not _has_voiced_signal(quality)


def _is_low_level_non_speech(quality: dict[str, float]) -> bool:
    """Reject quiet room noise while retaining low-volume voiced speech."""
    if (
        quality.get("audio_duration_seconds", 0.0) < 1.0
        or quality.get("waveform_rms", 1.0) > STT_LOW_LEVEL_RMS_THRESHOLD
        or quality.get("waveform_peak", 1.0) > STT_LOW_LEVEL_PEAK_THRESHOLD
        or quality.get("waveform_active_frame_count", 0.0) < 3
    ):
        return False
    return (
        quality.get("waveform_voiced_frame_ratio", 0.0) < STT_MIN_VOICED_FRAME_RATIO
        and quality.get("waveform_periodicity_mean", 0.0) < STT_MIN_PERIODICITY
    )


def _is_steady_unvoiced_noise(quality: dict[str, float]) -> bool:
    """Reject louder, nearly constant room noise without rejecting voiced speech."""
    louder_than_low_level = (
        quality.get("waveform_rms", 0.0) > STT_LOW_LEVEL_RMS_THRESHOLD
        or quality.get("waveform_peak", 0.0) > STT_LOW_LEVEL_PEAK_THRESHOLD
    )
    return (
        quality.get("audio_duration_seconds", 0.0) >= 1.0
        and louder_than_low_level
        and quality.get("waveform_envelope_cv", 1.0) <= STT_STEADY_NOISE_ENVELOPE_CV
        and quality.get("waveform_active_frame_count", 0.0) >= 3
        and quality.get("waveform_voiced_frame_ratio", 0.0) < STT_MIN_VOICED_FRAME_RATIO
        and quality.get("waveform_periodicity_mean", 0.0) < STT_MIN_PERIODICITY
    )


def _is_steady_tone(quality: dict[str, float]) -> bool:
    """Reject a sustained sine-like PCM signal with a flat amplitude envelope."""
    return (
        quality.get("audio_duration_seconds", 0.0) >= 1.0
        and 1.25 <= quality.get("waveform_crest_factor", 0.0) <= 1.65
        and quality.get("waveform_envelope_cv", 1.0) <= 0.01
    )


def _is_impulsive_non_speech(quality: dict[str, float]) -> bool:
    """Reject sparse click/pop transients that can prompt fluent Whisper guesses."""
    if (
        quality.get("audio_duration_seconds", 0.0) < 1.0
        or quality.get("waveform_crest_factor", 0.0) < STT_TRANSIENT_CREST_FACTOR
        or quality.get("waveform_envelope_cv", 0.0) < STT_TRANSIENT_ENVELOPE_CV
        or quality.get("waveform_active_frame_ratio", 1.0)
        > STT_MAX_TRANSIENT_ACTIVE_FRAME_RATIO
    ):
        return False
    return not _has_voiced_signal(quality)


def _transcription_quality(response: Any) -> tuple[bool, dict[str, float], str | None]:
    """Reject likely non-speech/hallucinated completions using Whisper segment metadata."""
    segments = _field(response, "segments") or []
    measured: list[tuple[float, float | None, float | None, float | None]] = []
    for segment in segments:
        start = _field(segment, "start")
        end = _field(segment, "end")
        no_speech = _field(segment, "no_speech_prob")
        avg_logprob = _field(segment, "avg_logprob")
        compression_ratio = _field(segment, "compression_ratio")
        try:
            no_speech_value = float(no_speech) if no_speech is not None else None
            logprob_value = float(avg_logprob) if avg_logprob is not None else None
            compression_value = float(compression_ratio) if compression_ratio is not None else None
            start_value = float(start) if start is not None else 0.0
            end_value = float(end) if end is not None else start_value + 1.0
        except (TypeError, ValueError):
            continue
        if no_speech_value is not None and not math.isfinite(no_speech_value):
            no_speech_value = None
        if logprob_value is not None and not math.isfinite(logprob_value):
            logprob_value = None
        if compression_value is not None and not math.isfinite(compression_value):
            compression_value = None
        duration = max(end_value - start_value, 0.01)
        measured.append((duration, no_speech_value, logprob_value, compression_value))

    has_no_speech_metadata = any(row[1] is not None for row in measured)
    has_logprob_metadata = any(row[2] is not None for row in measured)
    has_compression_metadata = any(row[3] is not None for row in measured)
    if not measured or not (
        has_no_speech_metadata and has_logprob_metadata and has_compression_metadata
    ):
        # Groq verbose_json documents these segment fields. Missing metadata means
        # we cannot tell a real utterance from a fluent completion over silence.
        return False, {}, "missing_quality_metadata"

    total_duration = sum(row[0] for row in measured)
    no_speech_duration = sum(
        row[0]
        for row in measured
        if row[1] is not None and row[1] >= STT_HIGH_NO_SPEECH_PROBABILITY
    )
    speech_segments = [
        row for row in measured if row[1] is not None and row[1] < STT_HIGH_NO_SPEECH_PROBABILITY
    ]
    if not speech_segments:
        speech_segments = [row for row in measured if row[1] is None]
    logprob_segments = [row for row in speech_segments if row[2] is not None]
    compression_segments = [row for row in speech_segments if row[3] is not None]

    quality: dict[str, float] = {}
    if total_duration:
        quality["no_speech_prob"] = sum(
            row[0] * (row[1] or 0.0) for row in measured if row[1] is not None
        ) / max(sum(row[0] for row in measured if row[1] is not None), 0.01)
        quality["no_speech_duration_ratio"] = no_speech_duration / max(total_duration, 0.01)
    if logprob_segments:
        quality["avg_logprob"] = sum(row[0] * (row[2] or 0.0) for row in logprob_segments) / max(
            sum(row[0] for row in logprob_segments), 0.01
        )
    if compression_segments:
        quality["compression_ratio"] = sum(
            row[0] * (row[3] or 0.0) for row in compression_segments
        ) / max(sum(row[0] for row in compression_segments), 0.01)
        high_compression_duration = sum(
            row[0] for row in compression_segments if row[3] >= STT_HIGH_COMPRESSION_RATIO
        )
        quality["high_compression_duration_ratio"] = high_compression_duration / max(
            sum(row[0] for row in compression_segments), 0.01
        )

    if quality.get("no_speech_duration_ratio", 0.0) >= STT_HIGH_NO_SPEECH_PROBABILITY:
        return False, quality, "no_speech"
    if quality.get("high_compression_duration_ratio", 0.0) >= 0.5:
        return False, quality, "high_compression"
    if quality.get("avg_logprob", 0.0) <= STT_LOW_LOG_PROBABILITY:
        return False, quality, "low_confidence"
    return True, quality, None


def _has_repeated_phrase(text: str) -> bool:
    """Catch repeated Whisper loops without blocking ordinary word-level repetition."""
    tokens = re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE)
    if len(tokens) < 8:
        return False

    for ngram_size in range(min(10, len(tokens) // 2), 3, -1):
        occurrences: dict[tuple[str, ...], list[int]] = {}
        for index in range(len(tokens) - ngram_size + 1):
            ngram = tuple(tokens[index : index + ngram_size])
            occurrences.setdefault(ngram, []).append(index)
        for starts in occurrences.values():
            non_overlapping = 0
            next_allowed_start = -1
            for start in starts:
                if start >= next_allowed_start:
                    non_overlapping += 1
                    next_allowed_start = start + ngram_size
            if non_overlapping >= 2 and (non_overlapping * ngram_size) / len(tokens) >= 0.35:
                return True
    return False


def _transcription_prompt(language: str, vocabulary: Sequence[str] | None = None) -> str:
    prompt = RETAIL_STT_PROMPTS.get(language, RETAIL_STT_PROMPT)
    if language != "vi":
        return prompt.format(catalog_terms="") if "{catalog_terms}" in prompt else prompt

    safe_terms = []
    for term in (vocabulary or ())[:32]:
        cleaned = re.sub(r"[^\w\s&'’./-]", " ", str(term), flags=re.UNICODE)
        cleaned = " ".join(cleaned.split())[:48]
        if cleaned and cleaned not in safe_terms:
            candidate_terms = [*safe_terms, cleaned]
            catalog_terms = (
                f"Tên sản phẩm/nhãn hiệu trong catalog: {', '.join(candidate_terms)}. "
            )
            candidate_prompt = prompt.format(catalog_terms=catalog_terms)
            if len(candidate_prompt.encode("utf-8")) <= MAX_GROQ_STT_PROMPT_BYTES:
                safe_terms.append(cleaned)

    catalog_terms = (
        f"Tên sản phẩm/nhãn hiệu trong catalog: {', '.join(safe_terms)}. "
        if safe_terms
        else ""
    )
    result = prompt.format(catalog_terms=catalog_terms)
    # The provider limit is measured on the UTF-8 payload. Keep a small margin
    # below its 896-byte cap so Vietnamese diacritics cannot trigger a 400.
    if len(result.encode("utf-8")) > MAX_GROQ_STT_PROMPT_BYTES:
        return RETAIL_STT_PROMPT.format(catalog_terms="")
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
                max_retries=0,
            )
        return self._clients_by_credential[slot]

    @staticmethod
    def _can_rotate_credential(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
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
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
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

                    forced_tool_name = (
                        str((tool_choice.get("function") or {}).get("name") or "")
                        if isinstance(tool_choice, dict)
                        else ""
                    )
                    default_max_tokens = (
                        1024
                        if forced_tool_name == "plan_shopping_mission"
                        else 512
                        if isinstance(tool_choice, dict)
                        else 80
                    )

                    request_options = {
                        "model": selected_model,
                        "messages": list(messages),
                        "temperature": 0.2,
                        "max_tokens": max_tokens
                        if max_tokens is not None
                        else default_max_tokens,
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
                    response_headers = _rate_limit_headers(getattr(error_response, "headers", None))
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
        vocabulary: Sequence[str] | None = None,
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

        signal_quality = _wav_signal_quality(content)
        if signal_quality is not None and _is_near_silence(signal_quality):
            model = models[0] if models else self.settings.groq_stt_model
            logger.info(
                "Groq transcription rejected candidate model=%s reason=near_silence duration_ms=%d",
                model,
                round(signal_quality["audio_duration_seconds"] * 1000),
            )
            return GroqTranscriptionResult(
                "",
                model,
                [{"model": model, "status": "rejected", "reason": "near_silence"}],
                speech_detected=False,
                quality=signal_quality,
            )
        if signal_quality is not None and _is_low_level_non_speech(signal_quality):
            model = models[0] if models else self.settings.groq_stt_model
            logger.info(
                "Groq transcription rejected candidate model=%s reason=low_level_non_speech "
                "duration_ms=%d voiced_frame_ratio=%s periodicity=%s",
                model,
                round(signal_quality["audio_duration_seconds"] * 1000),
                round(signal_quality["waveform_voiced_frame_ratio"], 3),
                round(signal_quality["waveform_periodicity_mean"], 3),
            )
            return GroqTranscriptionResult(
                "",
                model,
                [{"model": model, "status": "rejected", "reason": "low_level_non_speech"}],
                speech_detected=False,
                quality=signal_quality,
            )
        if signal_quality is not None and _is_steady_unvoiced_noise(signal_quality):
            model = models[0] if models else self.settings.groq_stt_model
            logger.info(
                "Groq transcription rejected candidate model=%s reason=steady_unvoiced_noise "
                "duration_ms=%d voiced_frame_ratio=%s periodicity=%s",
                model,
                round(signal_quality["audio_duration_seconds"] * 1000),
                round(signal_quality["waveform_voiced_frame_ratio"], 3),
                round(signal_quality["waveform_periodicity_mean"], 3),
            )
            return GroqTranscriptionResult(
                "",
                model,
                [{"model": model, "status": "rejected", "reason": "steady_unvoiced_noise"}],
                speech_detected=False,
                quality=signal_quality,
            )
        if signal_quality is not None and _is_steady_tone(signal_quality):
            model = models[0] if models else self.settings.groq_stt_model
            logger.info(
                "Groq transcription rejected candidate model=%s reason=steady_tone duration_ms=%d",
                model,
                round(signal_quality["audio_duration_seconds"] * 1000),
            )
            return GroqTranscriptionResult(
                "",
                model,
                [{"model": model, "status": "rejected", "reason": "steady_tone"}],
                speech_detected=False,
                quality=signal_quality,
            )
        if signal_quality is not None and _is_impulsive_non_speech(signal_quality):
            model = models[0] if models else self.settings.groq_stt_model
            logger.info(
                "Groq transcription rejected candidate model=%s reason=impulsive_non_speech "
                "duration_ms=%d active_frame_ratio=%s",
                model,
                round(signal_quality["audio_duration_seconds"] * 1000),
                round(signal_quality["waveform_active_frame_ratio"], 3),
            )
            return GroqTranscriptionResult(
                "",
                model,
                [{"model": model, "status": "rejected", "reason": "impulsive_non_speech"}],
                speech_detected=False,
                quality=signal_quality,
            )

        last_error: Exception | None = None
        fallback_trace: list[dict[str, Any]] = []
        rejected_model: str | None = None
        rejected_quality: dict[str, float] = {}
        for selected_model in models:
            for credential_slot, api_key in credentials:
                client = self._client_for_credential(credential_slot, api_key)
                started = perf_counter()
                try:
                    response = await client.audio.transcriptions.create(
                        file=(filename, content, content_type),
                        model=selected_model,
                        language=language,
                        prompt=_transcription_prompt(language, vocabulary),
                        response_format="verbose_json",
                        timestamp_granularities=["segment"],
                        temperature=0,
                    )
                    text = getattr(response, "text", "")
                    if not text or not text.strip():
                        raise ValueError("Groq returned an empty transcription")
                    speech_detected, quality, rejection_reason = _transcription_quality(response)
                    if speech_detected and _has_repeated_phrase(text):
                        speech_detected = False
                        rejection_reason = "repeated_phrase"
                    if not speech_detected:
                        logger.info(
                            "Groq transcription rejected candidate model=%s reason=%s "
                            "duration_ms=%d "
                            "no_speech_ratio=%s avg_logprob=%s compression_ratio=%s",
                            selected_model,
                            rejection_reason,
                            round((perf_counter() - started) * 1000),
                            round(quality["no_speech_duration_ratio"], 3)
                            if "no_speech_duration_ratio" in quality
                            else None,
                            round(quality["avg_logprob"], 3) if "avg_logprob" in quality else None,
                            round(quality["compression_ratio"], 3)
                            if "compression_ratio" in quality
                            else None,
                        )
                        successful_attempt = {
                            "model": selected_model,
                            "status": "rejected",
                            "reason": rejection_reason or "low_quality",
                        }
                        if len(credentials) > 1:
                            successful_attempt["credential_slot"] = credential_slot
                        fallback_trace.append(successful_attempt)
                        rejected_model = selected_model
                        rejected_quality = quality
                        # Another Whisper model can invent text after this model identifies
                        # the audio as low-quality or non-speech. Stop model fallback here;
                        # the caller can ask the customer to repeat instead.
                        break
                    logger.info(
                        "Groq transcription completed model=%s duration_ms=%d "
                        "avg_logprob=%s compression_ratio=%s",
                        selected_model,
                        round((perf_counter() - started) * 1000),
                        round(quality["avg_logprob"], 3) if "avg_logprob" in quality else None,
                        round(quality["compression_ratio"], 3)
                        if "compression_ratio" in quality
                        else None,
                    )
                    successful_attempt = {"model": selected_model, "status": "success"}
                    if len(credentials) > 1:
                        successful_attempt["credential_slot"] = credential_slot
                    fallback_trace.append(successful_attempt)
                    return GroqTranscriptionResult(
                        text.strip(),
                        selected_model,
                        fallback_trace,
                        speech_detected=True,
                        quality=quality,
                    )
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
            if rejected_model is not None:
                break

        if rejected_model is not None:
            return GroqTranscriptionResult(
                "",
                rejected_model,
                fallback_trace,
                speech_detected=False,
                quality=rejected_quality,
            )
        logger.error("Groq transcription exhausted configured models attempts=%d", len(models))
        raise GroqProviderError(
            "Groq transcription request failed for configured models",
            fallback_trace=fallback_trace,
        ) from last_error
