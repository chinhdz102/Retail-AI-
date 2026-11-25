from __future__ import annotations

import asyncio
import inspect
import io
import json
import logging
import math
import re
import sys
import wave
from array import array
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any

import httpx
from openai import AsyncOpenAI

from backend.config import Settings, get_settings
from backend.services.speech_activity import (
    SpeechActivityInputError,
    SpeechActivityUnavailable,
    analyze_speech_activity,
    analyze_speech_audio,
)

_DEFAULT_ANALYZE_SPEECH_ACTIVITY = analyze_speech_activity

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
CHAT_ERROR_CODES = frozenset({
    "tool_use_failed", "json_validate_failed", "rate_limit_exceeded", "invalid_api_key",
})
STT_HIGH_NO_SPEECH_PROBABILITY = 0.5
STT_LOW_LOG_PROBABILITY = -0.75
STT_HIGH_COMPRESSION_RATIO = 2.4
# Deliberately permissive: VAD-positive time excludes pauses and some consonants.
# Catch whole sentences generated over a few animal/noise frames, without using
# the recognizer's own (potentially confident) no_speech score as proof of speech.
STT_MAX_VI_TOKENS_PER_SPEECH_SECOND = 30.0
STT_LOW_LEVEL_RMS_THRESHOLD = 0.03
STT_LOW_LEVEL_PEAK_THRESHOLD = 0.15
STT_MIN_VOICED_FRAME_RATIO = 0.15
STT_MIN_PERIODICITY = 0.58
STT_STEADY_NOISE_ENVELOPE_CV = 0.15
STT_TRANSIENT_CREST_FACTOR = 8.0
STT_TRANSIENT_ENVELOPE_CV = 1.0
STT_MAX_TRANSIENT_ACTIVE_FRAME_RATIO = 0.15
MAX_GROQ_STT_PROMPT_BYTES = 880
GROQ_STT_CATALOG_CONTEXT_TERMS = 8
STT_OPTIONAL_VERIFIER_MAX_SECONDS = 4.0
STT_VERIFIER_RETURN_MARGIN_SECONDS = 0.25


def _rate_limit_headers(headers: Any) -> dict[str, str]:
    if headers is None or not hasattr(headers, "get"):
        return {}
    return {
        name: str(value)
        for name in RATE_LIMIT_HEADER_NAMES
        if (value := headers.get(name)) is not None
    }


def _reset_duration_seconds(value: str) -> float | None:
    """Groq reset headers are durations such as ``2m59.56s``, not timestamps."""
    try:
        seconds = float(value)
    except ValueError:
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|d|h|m|s)", value)
        if not parts or "".join(number + unit for number, unit in parts) != value:
            return None
        seconds = sum(float(number) * {"ms": .001, "s": 1, "m": 60,
                                      "h": 3600, "d": 86400}[unit]
                      for number, unit in parts)
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _rate_limit_retry_seconds(headers: dict[str, str]) -> float:
    """Honor provider wait hints without cooling down non-exhausted buckets."""
    waits: list[float] = []
    retry_after = headers.get("retry-after", "")
    if retry_after:
        seconds = _reset_duration_seconds(retry_after)
        if seconds is None:
            try:
                date = parsedate_to_datetime(retry_after)
                seconds = (date - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = None
        if seconds is not None and math.isfinite(seconds):
            waits.append(seconds)
    for bucket in ("requests", "tokens"):
        try:
            remaining = float(headers.get(f"x-ratelimit-remaining-{bucket}", "nan"))
        except ValueError:
            continue
        if remaining <= 0:
            seconds = _reset_duration_seconds(headers.get(f"x-ratelimit-reset-{bucket}", ""))
            if seconds is not None:
                waits.append(seconds)
    return max(1, min(86_400, max(waits) if waits else 5))


_LONG_TERM_QUOTA_RE = re.compile(
    r"(?:free allowance|allowance.*used up|used up|comes back on|quota exceeded|"
    r"monthly.*limit|daily.*limit|insufficient_quota)",
    re.IGNORECASE,
)


def _is_long_term_quota_exhausted(exc: Exception) -> bool:
    """Detect multi-hour/multi-day allowance exhaustion in provider 429 error bodies."""
    candidates: list[str] = [str(exc)]
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, str)):
        candidates.append(str(body))
    response = getattr(exc, "response", None)
    if response is not None:
        resp_text = getattr(response, "text", None)
        if isinstance(resp_text, str):
            candidates.append(resp_text[:400])
    return any(_LONG_TERM_QUOTA_RE.search(part) for part in candidates if part)


def _cooldown_seconds_for_429(headers: dict[str, str], exc: Exception) -> float:
    """Honor explicit rate-limit headers, and cool down exhausted free allowances for 1 hour."""
    base = _rate_limit_retry_seconds(headers)
    if _is_long_term_quota_exhausted(exc):
        return max(base, 3600.0)
    return base


def _chat_error_metadata(exc: Exception) -> dict[str, int | str]:
    """Project only bounded diagnostic fields from an untrusted provider error.

    Error messages and failed generations may contain customer text or secrets;
    never copy them (or an unknown code) into the fallback trace.
    """
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    metadata: dict[str, int | str] = {}
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        metadata["status_code"] = status
    body = getattr(exc, "body", None)
    if body is None and response is not None:
        decode = getattr(response, "json", None)
        if callable(decode):
            try:
                body = decode()
            except Exception:
                body = None
    if isinstance(body, dict):
        nested_error = body.get("error")
        candidates = [body.get("code")]
        if isinstance(nested_error, dict):
            candidates.append(nested_error.get("code"))
        for code in candidates:
            if isinstance(code, str) and code in CHAT_ERROR_CODES:
                metadata["error_code"] = code
                break
    return metadata


def _recover_local_schema_defaults(
    exc: Exception,
    response_format: dict[str, Any] | None,
    validator: Callable[[str], bool] | None,
) -> str | None:
    """Never treat a failed provider generation as valid without local consent."""
    if not callable(validator) or not isinstance(response_format, dict):
        return None
    specification = response_format.get("json_schema")
    if response_format.get("type") != "json_schema" or not isinstance(specification, dict):
        return None
    if specification.get("strict") is not True:
        return None
    metadata = _chat_error_metadata(exc)
    if metadata.get("status_code") != 400 or metadata.get("error_code") != "json_validate_failed":
        return None
    body = getattr(exc, "body", None)
    if body is None:
        decode = getattr(getattr(exc, "response", None), "json", None)
        if callable(decode):
            try:
                body = decode()
            except Exception:
                return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    generation = body.get("failed_generation")
    if generation is None and isinstance(error, dict):
        generation = error.get("failed_generation")
    if not isinstance(generation, str):
        return None
    try:
        if not isinstance(json.loads(generation), dict):
            return None
        if validator(generation) is True:
            return generation
    except Exception:
        # Validation failures preserve the original provider fallback path.
        pass
    return None


def _is_audio_input_provider_error(exc: Exception) -> bool:
    """Recognize provider 4xx media errors even when the SDK omits ``body.code``.

    Groq's SDK has returned both ``{"code": "invalid_media_file"}`` and nested or
    message-only error payloads over time. Treating those as provider outages makes
    the API report 502 for a bad recording and can trigger pointless model/key
    retries. Keep this predicate narrow so unrelated 400s remain provider errors.
    """
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    status = status or getattr(response, "status_code", None)
    if status not in {400, 422}:
        return False
    body = getattr(exc, "body", None)
    if body is None and response is not None:
        body = getattr(response, "json", None)
        if callable(body):
            try:
                body = body()
            except Exception:
                body = None
    if isinstance(body, dict):
        candidates = [body.get("code"), body.get("message"), body.get("error")]
    else:
        candidates = [body, str(exc)]
    text = " ".join(str(value) for value in candidates if value is not None).casefold()
    return any(
        token in text
        for token in (
            "invalid_media_file",
            "invalid_audio_file",
            "unsupported_audio_format",
            "audio could not be decoded",
            "could not decode audio",
            "unsupported audio",
        )
    )


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


class GroqAudioInputError(ValueError):
    """The provider rejected undecodable or unsupported customer audio."""


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
    finish_reason: str | None

    def __new__(
        cls,
        text: str,
        model: str,
        tool_calls: list[dict[str, Any]] | None = None,
        fallback_trace: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        finish_reason: str | None = None,
    ) -> GroqChatResult:
        result = super().__new__(cls, text)
        result.model = model
        result.tool_calls = tool_calls or []
        result.fallback_trace = fallback_trace or []
        result.usage = usage or {}
        result.finish_reason = finish_reason
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
        needs_confirmation: bool = False,
        verification_text: str | None = None,
    ) -> GroqTranscriptionResult:
        result = super().__new__(cls, text)
        result.model = model
        result.fallback_trace = fallback_trace or []
        result.speech_detected = speech_detected
        result.quality = quality or {}
        result.needs_confirmation = needs_confirmation
        # Internal comparison only; API schemas/logs never serialize this text.
        result.verification_text = verification_text
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


def _wav_signal_quality(content: bytes, analysis_gain: float = 1.0) -> dict[str, float] | None:
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
        "waveform_analysis_gain": float(analysis_gain),
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
            quality.update(_wav_voiced_signal_quality(samples, sample_rate, rms, analysis_gain))
    return quality


def _wav_voiced_signal_quality(
    samples: array, sample_rate: int, waveform_rms: float, analysis_gain: float = 1.0,
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
    active_rms_floor = max(0.00035 / max(1.0, analysis_gain), waveform_rms * 0.08)

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
    gain = max(1.0, quality.get("waveform_analysis_gain", 1.0))
    if not (
        quality.get("waveform_rms", 1.0) * gain < 0.0015
        and quality.get("waveform_peak", 1.0) * gain < 0.012
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
    missing_metadata = False
    invalid_metadata = False
    for segment in segments:
        start = _field(segment, "start")
        end = _field(segment, "end")
        no_speech = _field(segment, "no_speech_prob")
        avg_logprob = _field(segment, "avg_logprob")
        compression_ratio = _field(segment, "compression_ratio")
        if any(value is None for value in (
            start, end, no_speech, avg_logprob, compression_ratio,
        )):
            missing_metadata = True
            continue
        try:
            start_value = float(start)
            end_value = float(end)
            no_speech_value = float(no_speech)
            logprob_value = float(avg_logprob)
            compression_value = float(compression_ratio)
        except (TypeError, ValueError):
            invalid_metadata = True
            continue
        if (
            not all(math.isfinite(value) for value in (
                start_value, end_value, no_speech_value, logprob_value, compression_value,
            ))
            or end_value <= start_value
            or not 0.0 <= no_speech_value <= 1.0
            or compression_value < 0.0
        ):
            invalid_metadata = True
            continue
        duration = end_value - start_value
        measured.append((duration, no_speech_value, logprob_value, compression_value))

    if invalid_metadata:
        return False, {}, "invalid_quality_metadata"
    if missing_metadata or not measured:
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
    # Coughs can decode to a single word repeated seven times and still have
    # good compression/log-probability scores. The multiword check misses this.
    if len(tokens) >= 6 and len(set(tokens)) == 1:
        return True
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


def _has_insufficient_acoustic_support(
    text: str, quality: dict[str, float], language: str,
) -> bool:
    """Reject implausible Vietnamese text volume relative to independent speech.

    This is a loose hallucination guard, not an accuracy or speaker-identity
    score. Use total positive frames across all episodes so pauses/long replies
    do not inflate the rate. Short answers are intentionally exempt.
    """
    if language != "vi":
        return False
    frames = quality.get("server_vad_frame_count")
    ratio = quality.get("server_vad_positive_frame_ratio")
    if frames is None or ratio is None:
        return False
    tokens = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    speech_seconds = frames * ratio * 512 / 16_000
    return len(tokens) >= 8 and len(tokens) > speech_seconds * STT_MAX_VI_TOKENS_PER_SPEECH_SECOND


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
    supports_bound_consultation = True
    supports_local_schema_recovery = True

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._clients_by_credential: dict[str, Any] = {}
        self._chat_cooldowns: dict[tuple[str, str], float] = {}
        self._chat_token_budgets: dict[tuple[str, str], tuple[int, float]] = {}
        self._chat_rr_cursor: dict[str, int] = {}
        self._stt_cooldowns: dict[tuple[str, str], float] = {}
        self._stt_rr_cursor: dict[str, int] = {}

    def _record_token_budget(
        self,
        model: str,
        slot: str,
        headers: dict[str, str],
        *,
        exhausted: bool = False,
    ) -> None:
        now = perf_counter()
        reset_seconds = _reset_duration_seconds(headers.get("x-ratelimit-reset-tokens", ""))
        if exhausted:
            wait = _rate_limit_retry_seconds(headers)
            self._chat_token_budgets[(model, slot)] = (0, now + wait)
            return
        raw_remaining = headers.get("x-ratelimit-remaining-tokens")
        if raw_remaining is None:
            return
        try:
            remaining = int(float(raw_remaining))
        except ValueError:
            return
        if remaining < 0:
            return
        ttl = reset_seconds if reset_seconds is not None and reset_seconds > 0 else 60.0
        self._chat_token_budgets[(model, slot)] = (remaining, now + ttl)

    def _ordered_credentials(
        self,
        model: str,
        credentials: tuple[tuple[str, str], ...],
        estimated_tokens: int,
    ) -> list[tuple[int, str, str]]:
        count = len(credentials)
        if count <= 1:
            return [(0, credentials[0][0], credentials[0][1])]
        start = self._chat_rr_cursor.get(model, 0) % count
        rotated = [
            (idx, credentials[idx][0], credentials[idx][1])
            for idx in (*range(start, count), *range(0, start))
        ]
        now = perf_counter()

        def sort_key(entry: tuple[int, str, str]) -> tuple[int, int]:
            _, slot, _ = entry
            budget = self._chat_token_budgets.get((model, slot))
            if budget is None or now >= budget[1]:
                return (0, 0)
            remaining = budget[0]
            if remaining >= estimated_tokens:
                return (0, 0)
            return (1, -remaining)

        return sorted(rotated, key=sort_key)

    def _credentials_or_raise(self) -> tuple[tuple[str, str], ...]:
        credentials = self.settings.groq_api_credentials
        if not credentials:
            raise GroqConfigurationError("Groq API key is not configured")
        return credentials

    def _chat_credentials_or_raise(self) -> tuple[tuple[str, str], ...]:
        credentials = self.settings.chat_api_credentials
        if not credentials:
            raise GroqConfigurationError("Chat LLM / Groq API key is not configured")
        return credentials

    def _build_openai_client(self, *, api_key: str, base_url: str) -> AsyncOpenAI:
        http_client = httpx.AsyncClient(
            timeout=self.settings.groq_timeout_seconds,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=60.0,
            ),
        )
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.settings.groq_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    def _client_for_credential(self, slot: str, api_key: str) -> Any:
        # Injected clients keep unit tests and app integrations deterministic.
        if self.client is not None:
            return self.client
        if slot not in self._clients_by_credential:
            self._clients_by_credential[slot] = self._build_openai_client(
                api_key=api_key,
                base_url=self.settings.groq_base_url,
            )
        return self._clients_by_credential[slot]

    def _chat_client_for_credential(self, slot: str, api_key: str) -> Any:
        if self.client is not None:
            return self.client
        base_url = self.settings.effective_chat_base_url
        if base_url == self.settings.groq_base_url:
            return self._client_for_credential(slot, api_key)
        cache_key = f"{base_url}::{slot}"
        if cache_key not in self._clients_by_credential:
            self._clients_by_credential[cache_key] = self._build_openai_client(
                api_key=api_key,
                base_url=base_url,
            )
        return self._clients_by_credential[cache_key]

    def _evict_client(self, slot: str, *, use_groq_pool: bool) -> None:
        if self.client is not None:
            return
        base_url = (
            self.settings.groq_base_url
            if use_groq_pool
            else self.settings.effective_chat_base_url
        )
        key = slot if base_url == self.settings.groq_base_url else f"{base_url}::{slot}"
        self._clients_by_credential.pop(key, None)

    def _is_on_cooldown(self, selected_model: str, credential_slot: str, now_ts: float) -> bool:
        if (
            self._chat_cooldowns.get((selected_model, credential_slot), 0) > now_ts
            or self._chat_cooldowns.get(("*", credential_slot), 0) > now_ts
        ):
            return True
        if ":free" in selected_model and (
            self._chat_cooldowns.get((":free", credential_slot), 0) > now_ts
        ):
            return True
        return False

    def _record_429_cooldown(
        self,
        selected_model: str,
        credential_slot: str,
        response_headers: dict[str, str],
        exc: Exception,
    ) -> None:
        wait = _cooldown_seconds_for_429(response_headers, exc)
        until = perf_counter() + wait
        self._chat_cooldowns[(selected_model, credential_slot)] = until
        if ":free" in selected_model and _is_long_term_quota_exhausted(exc):
            self._chat_cooldowns[(":free", credential_slot)] = until

    def _can_use_groq_fast_lane(self) -> bool:
        return (
            self.settings.stream_llm_use_groq_lpu
            and self.client is None
            and bool(self.settings.groq_api_credentials)
            and self.settings.effective_chat_base_url != self.settings.groq_base_url
        )

    async def preconnect_chat(self) -> None:
        """Warm HTTPS keep-alive pools for the chat LLM (and Groq LPU fast lane when enabled)."""
        async def _warm(client: Any, base_url: str, api_key: str) -> None:
            try:
                http_client = getattr(client, "_client", None)
                if http_client is not None:
                    await http_client.get(
                        f"{base_url.rstrip('/')}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=3.0,
                    )
            except Exception:
                pass

        tasks: list[Any] = []
        try:
            credentials = self._chat_credentials_or_raise()
            slot, api_key = credentials[0]
            tasks.append(
                _warm(
                    self._chat_client_for_credential(slot, api_key),
                    self.settings.effective_chat_base_url,
                    api_key,
                )
            )
        except Exception:
            pass
        if self._can_use_groq_fast_lane():
            g_slot, g_key = self.settings.groq_api_credentials[0]
            tasks.append(
                _warm(
                    self._client_for_credential(g_slot, g_key),
                    self.settings.groq_base_url,
                    g_key,
                )
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
        response_format: dict[str, Any] | None = None,
        local_json_validator: Callable[[str], bool] | None = None,
        reasoning_effort: str | None = None,
    ) -> GroqChatResult:
        if response_format is not None and (tools or tool_choice):
            raise ValueError("Structured output cannot be combined with tool use")
        primary_credentials = self._chat_credentials_or_raise()
        primary_models = list(
            dict.fromkeys(
                selected_model
                for selected_model in (
                    model or self.settings.effective_chat_model,
                    self.settings.effective_chat_fallback_model,
                    self.settings.effective_chat_fallback_model_2,
                )
                if selected_model
            )
        )
        # Conservative estimate of request token footprint for selecting a key
        # that still has enough remaining TPM budget before attempting a call.
        msg_chars = sum(len(str(m.get("content", ""))) for m in messages)
        estimated_tokens = max(256, msg_chars // 3 + min(max_tokens or 512, 1200))

        # When the Groq LPU fast-lane is explicitly opted in (stream_llm_use_groq_lpu=True)
        # and no explicit model override was requested, run Groq LPU first (~350-800ms) for
        # prompts that fit Groq's free-tier per-request TPM cap, then fail over to the
        # dedicated chat LLM. Large (>22k char) prompts skip instant-413 Groq attempts.
        provider_stages: list[tuple[tuple[tuple[str, str], ...], list[str], bool]] = []
        if model is None and self._can_use_groq_fast_lane() and msg_chars <= 22_000:
            rf_schema_name = (
                str((response_format.get("json_schema") or {}).get("name") or "")
                if isinstance(response_format, dict)
                else ""
            )
            prefer_strong_groq = (
                msg_chars > 15_500
                or rf_schema_name in {"GroundedSelection", "SelectionReview"}
            )
            candidate_groq_models = (
                (self.settings.groq_fallback_model, self.settings.groq_fallback_model_2)
                if prefer_strong_groq
                else (
                    self.settings.groq_model,
                    self.settings.groq_fallback_model,
                    self.settings.groq_fallback_model_2,
                )
            )
            groq_fast_models = list(
                dict.fromkeys(m for m in candidate_groq_models if m)
            )
            if groq_fast_models:
                provider_stages.append(
                    (self.settings.groq_api_credentials, groq_fast_models, True)
                )
        provider_stages.append((primary_credentials, primary_models, False))

        last_error: Exception | None = None
        fallback_trace: list[dict[str, Any]] = []
        for credentials, models, use_groq_pool in provider_stages:
            stage_exhausted = False
            for selected_model in models:
                if stage_exhausted:
                    break
                ordered = self._ordered_credentials(selected_model, credentials, estimated_tokens)
                for slot_index, credential_slot, api_key in ordered:
                    now_ts = perf_counter()
                    if self._is_on_cooldown(selected_model, credential_slot, now_ts):
                        fallback_trace.append({"model": selected_model, "status": "cooldown"})
                        continue
                    client = (
                        self._client_for_credential(credential_slot, api_key)
                        if use_groq_pool
                        else self._chat_client_for_credential(credential_slot, api_key)
                    )
                    try:
                        model_options: dict[str, Any] = {}
                        configured_effort = (
                            reasoning_effort
                            if reasoning_effort is not None
                            else self.settings.groq_structured_reasoning_effort
                        )
                        if selected_model.startswith("qwen/"):
                            model_options["reasoning_effort"] = (
                                "none"
                                if use_groq_pool and reasoning_effort is None
                                else configured_effort
                                if response_format is not None
                                else "none"
                            )
                        elif selected_model.startswith("openai/gpt-oss-"):
                            model_options["reasoning_effort"] = (
                                configured_effort
                                if response_format is not None and configured_effort != "none"
                                else "low"
                            )
                        elif "deepseek" in selected_model.casefold():
                            # Non-fast DeepSeek endpoints can exceed 16s with reasoning enabled;
                            # keep structured reasoning on fast endpoints and "none" on slow
                            # fallbacks.
                            is_fast_deepseek = "fast" in selected_model.casefold()
                            model_options["reasoning_effort"] = (
                                configured_effort
                                if response_format is not None and is_fast_deepseek
                                else "none"
                            )
                        if tools:
                            model_options["tools"] = list(tools)
                            model_options["tool_choice"] = tool_choice or "auto"
                        if response_format is not None:
                            model_options["response_format"] = response_format

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
                        base_attempt_timeout = (
                            min(self.settings.groq_chat_attempt_timeout_seconds, 6.0)
                            if use_groq_pool
                            else self.settings.groq_chat_attempt_timeout_seconds
                        )
                        if (
                            not use_groq_pool
                            and self.settings.effective_chat_base_url != self.settings.groq_base_url
                        ):
                            base_attempt_timeout = max(base_attempt_timeout, 16.0)
                        attempt_timeout = min(
                            self.settings.groq_timeout_seconds,
                            base_attempt_timeout,
                        )
                        if raw_endpoint is None:
                            response = await asyncio.wait_for(
                                completion_endpoint.create(**request_options),
                                timeout=attempt_timeout,
                            )
                        else:
                            raw_response = await asyncio.wait_for(
                                raw_endpoint.create(**request_options), timeout=attempt_timeout,
                            )
                            response_headers = _rate_limit_headers(raw_response.headers)
                            parsed_response = raw_response.parse()
                            response = (
                                await parsed_response
                                if inspect.isawaitable(parsed_response)
                                else parsed_response
                            )
                        finish_reason = getattr(response.choices[0], "finish_reason", None)
                        if finish_reason in {"length", "content_filter"}:
                            raise ValueError("Groq returned an incomplete response")
                        response_message = response.choices[0].message
                        content = response_message.content or ""
                        if response_format is not None and content.strip().startswith("```"):
                            stripped = re.sub(
                                r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I
                            )
                            if stripped:
                                content = stripped
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
                            self._record_token_budget(
                                selected_model, credential_slot, response_headers
                            )
                        if len(credentials) > 1:
                            successful_attempt["credential_slot"] = credential_slot
                            self._chat_rr_cursor[selected_model] = (
                                (slot_index + 1) % len(credentials)
                            )
                        fallback_trace.append(successful_attempt)
                        logger.info(
                            "Chat LLM completed model=%s attempts=%d",
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
                            content.strip(), selected_model, tool_calls, fallback_trace,
                            usage_summary, finish_reason,
                        )
                    except Exception as exc:
                        last_error = exc
                        failed_attempt: dict[str, Any] = {
                            "model": selected_model,
                            "status": "error",
                            "error_type": type(exc).__name__,
                            **_chat_error_metadata(exc),
                        }
                        error_response = getattr(exc, "response", None)
                        response_headers = _rate_limit_headers(
                            getattr(error_response, "headers", None)
                        )
                        status_code = getattr(exc, "status_code", None) or getattr(
                            error_response, "status_code", None
                        )
                        if status_code == 402:
                            # Short cooldown so balance/pass updates take effect promptly
                            # without hammering all fallback models in a single turn.
                            self._chat_cooldowns[("*", credential_slot)] = perf_counter() + 15.0
                            if len(credentials) == 1:
                                stage_exhausted = True
                        elif status_code == 429:
                            self._record_429_cooldown(
                                selected_model, credential_slot, response_headers, exc,
                            )
                            self._record_token_budget(
                                selected_model, credential_slot, response_headers, exhausted=True,
                            )
                            if len(credentials) > 1:
                                self._chat_rr_cursor[selected_model] = (
                                    (slot_index + 1) % len(credentials)
                                )
                        elif type(exc).__name__ == "APIConnectionError":
                            self._evict_client(credential_slot, use_groq_pool=use_groq_pool)
                        if response_headers:
                            failed_attempt["rate_limit_headers"] = response_headers
                        if len(credentials) > 1:
                            failed_attempt["credential_slot"] = credential_slot
                        # Hosted strict decoding can reject an omitted wire-required
                        # field whose local schema explicitly defines a safe default.
                        # Only a caller-owned strict validator can authorize recovery.
                        recovered = _recover_local_schema_defaults(
                            exc, response_format, local_json_validator,
                        )
                        if recovered is not None:
                            failed_attempt.update(
                                status="success", recovery="local-default-validation"
                            )
                            if len(credentials) > 1:
                                self._chat_rr_cursor[selected_model] = (
                                    (slot_index + 1) % len(credentials)
                                )
                            fallback_trace.append(failed_attempt)
                            logger.info(
                                "Chat LLM structured output recovered model=%s", selected_model
                            )
                            return GroqChatResult(
                                recovered, selected_model, fallback_trace=fallback_trace,
                            )
                        fallback_trace.append(failed_attempt)
                        logger.warning(
                            "Chat LLM attempt failed model=%s credential_slot=%s "
                            "error_type=%s",
                            selected_model,
                            credential_slot if len(credentials) > 1 else "primary",
                            type(exc).__name__,
                        )
                        # Only retry a different key for credential/quota failures.
                        # Other errors move directly to the next model.
                        if stage_exhausted or not self._can_rotate_credential(exc):
                            break

        logger.error(
            "Chat LLM exhausted configured models attempts=%d",
            len(fallback_trace),
        )
        raise GroqProviderError(
            "Chat LLM request failed for configured models",
            fallback_trace=fallback_trace,
        ) from last_error

    async def fast_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        max_tokens: int = 140,
        temperature: float = 0.1,
    ) -> GroqChatResult:
        """Ultra-low-latency plain-text/micro-JSON helper for small non-schema prompts.

        When ``stream_llm_use_groq_lpu`` is enabled, runs first on Groq LPU (~300ms,
        free tier) and seamlessly falls back to ``chat()`` on any error/rate-limit.
        """
        if self._can_use_groq_fast_lane():
            groq_credentials = self.settings.groq_api_credentials
            groq_models = list(
                dict.fromkeys(
                    m
                    for m in (
                        self.settings.groq_model,
                        self.settings.groq_fallback_model_2,
                        self.settings.groq_fallback_model,
                    )
                    if m
                )
            )
            msg_chars = sum(len(str(m.get("content", ""))) for m in messages)
            estimated_tokens = max(128, msg_chars // 3 + min(max_tokens, 400))
            for selected_model in groq_models:
                ordered = self._ordered_credentials(
                    selected_model, groq_credentials, estimated_tokens
                )
                for slot_index, credential_slot, api_key in ordered:
                    if self._is_on_cooldown(selected_model, credential_slot, perf_counter()):
                        continue
                    client = self._client_for_credential(credential_slot, api_key)
                    try:
                        request_options: dict[str, Any] = {
                            "model": selected_model,
                            "messages": list(messages),
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                        }
                        if selected_model.startswith("qwen/"):
                            request_options["reasoning_effort"] = "none"
                        elif selected_model.startswith("openai/gpt-oss-"):
                            request_options["reasoning_effort"] = "low"
                        response = await asyncio.wait_for(
                            client.chat.completions.create(**request_options),
                            timeout=min(self.settings.groq_timeout_seconds, 5.0),
                        )
                        content = (response.choices[0].message.content or "").strip()
                        if not content:
                            raise ValueError("Groq fast_chat returned empty response")
                        if len(groq_credentials) > 1:
                            self._chat_rr_cursor[selected_model] = (
                                (slot_index + 1) % len(groq_credentials)
                            )
                        return GroqChatResult(content, selected_model)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error_response = getattr(exc, "response", None)
                        response_headers = _rate_limit_headers(
                            getattr(error_response, "headers", None)
                        )
                        status_code = getattr(exc, "status_code", None) or getattr(
                            error_response, "status_code", None
                        )
                        if status_code == 429:
                            self._record_429_cooldown(
                                selected_model, credential_slot, response_headers, exc,
                            )
                            self._record_token_budget(
                                selected_model, credential_slot, response_headers, exhausted=True,
                            )
                            if len(groq_credentials) > 1:
                                self._chat_rr_cursor[selected_model] = (
                                    (slot_index + 1) % len(groq_credentials)
                                )
                        elif type(exc).__name__ == "APIConnectionError":
                            self._evict_client(credential_slot, use_groq_pool=True)
                        if not self._can_rotate_credential(exc):
                            break
        return await self.chat(messages, max_tokens=max_tokens, reasoning_effort="none")

    async def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 260,
        temperature: float = 0.2,
    ) -> AsyncIterator[tuple[str, str]]:
        """Stream visible text deltas with optional Groq LPU fast-lane + chat LLM failover.

        When ``stream_llm_use_groq_lpu`` is enabled and no explicit model override is
        requested, tries Groq LPU first (~300-500ms TTFT, free tier) and seamlessly
        rotates to the configured dedicated chat LLM if an error occurs before any
        visible token has been yielded to the caller.
        """
        primary_credentials = self._chat_credentials_or_raise()
        primary_models = list(
            dict.fromkeys(
                selected_model
                for selected_model in (
                    model or self.settings.effective_chat_model,
                    self.settings.effective_chat_fallback_model,
                    self.settings.effective_chat_fallback_model_2,
                )
                if selected_model
            )
        )
        provider_stages: list[tuple[tuple[tuple[str, str], ...], list[str], bool]] = []
        if model is None and self._can_use_groq_fast_lane():
            groq_stream_models = list(
                dict.fromkeys(
                    m
                    for m in (
                        self.settings.groq_model,
                        self.settings.groq_fallback_model_2,
                        self.settings.groq_fallback_model,
                    )
                    if m
                )
            )
            if groq_stream_models:
                provider_stages.append(
                    (self.settings.groq_api_credentials, groq_stream_models, True)
                )
        provider_stages.append((primary_credentials, primary_models, False))

        msg_chars = sum(len(str(m.get("content", ""))) for m in messages)
        estimated_tokens = max(256, msg_chars // 3 + min(max_tokens, 800))

        last_error: Exception | None = None
        fallback_trace: list[dict[str, Any]] = []
        for stage_credentials, stage_models, use_groq_pool in provider_stages:
            stage_exhausted = False
            for selected_model in stage_models:
                if stage_exhausted:
                    break
                ordered = self._ordered_credentials(
                    selected_model, stage_credentials, estimated_tokens
                )
                for slot_index, credential_slot, api_key in ordered:
                    now_ts = perf_counter()
                    if self._is_on_cooldown(selected_model, credential_slot, now_ts):
                        fallback_trace.append({"model": selected_model, "status": "cooldown"})
                        continue
                    client = (
                        self._client_for_credential(credential_slot, api_key)
                        if use_groq_pool
                        else self._chat_client_for_credential(credential_slot, api_key)
                    )
                    yielded_any = False
                    stream: Any | None = None
                    try:
                        request_options: dict[str, Any] = {
                            "model": selected_model,
                            "messages": list(messages),
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "stream": True,
                        }
                        if "deepseek" in selected_model.casefold() or selected_model.startswith(
                            "qwen/"
                        ):
                            request_options["reasoning_effort"] = "none"
                        elif selected_model.startswith("openai/gpt-oss-"):
                            request_options["reasoning_effort"] = "low"
                        base_attempt_timeout = (
                            6.0
                            if use_groq_pool
                            else max(self.settings.groq_chat_attempt_timeout_seconds, 14.0)
                        )
                        attempt_timeout = min(
                            self.settings.groq_timeout_seconds, base_attempt_timeout
                        )
                        stream = await asyncio.wait_for(
                            client.chat.completions.create(**request_options),
                            timeout=attempt_timeout,
                        )
                        async for chunk in stream:
                            choices = getattr(chunk, "choices", None) or ()
                            if not choices:
                                continue
                            delta = getattr(choices[0], "delta", None)
                            content = getattr(delta, "content", None) if delta is not None else None
                            if content:
                                yielded_any = True
                                yield content, selected_model
                        if not yielded_any:
                            raise ValueError("Chat LLM stream returned an empty response")
                        if len(stage_credentials) > 1:
                            self._chat_rr_cursor[selected_model] = (
                                (slot_index + 1) % len(stage_credentials)
                            )
                        logger.info("Chat LLM stream completed model=%s", selected_model)
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        last_error = exc
                        error_response = getattr(exc, "response", None)
                        response_headers = _rate_limit_headers(
                            getattr(error_response, "headers", None)
                        )
                        status_code = getattr(exc, "status_code", None) or getattr(
                            error_response, "status_code", None
                        )
                        if status_code == 402:
                            self._chat_cooldowns[("*", credential_slot)] = perf_counter() + 15.0
                            if len(stage_credentials) == 1:
                                stage_exhausted = True
                        elif status_code == 429:
                            self._record_429_cooldown(
                                selected_model, credential_slot, response_headers, exc,
                            )
                            self._record_token_budget(
                                selected_model, credential_slot, response_headers, exhausted=True,
                            )
                            if len(stage_credentials) > 1:
                                self._chat_rr_cursor[selected_model] = (
                                    (slot_index + 1) % len(stage_credentials)
                                )
                        elif type(exc).__name__ == "APIConnectionError":
                            self._evict_client(credential_slot, use_groq_pool=use_groq_pool)
                        fallback_trace.append(
                            {
                                "model": selected_model,
                                "status": "error",
                                "error_type": type(exc).__name__,
                                **_chat_error_metadata(exc),
                            }
                        )
                        if yielded_any:
                            raise GroqProviderError(
                                "Chat LLM stream interrupted after partial output",
                                fallback_trace=fallback_trace,
                            ) from exc
                        if stage_exhausted or not self._can_rotate_credential(exc):
                            break
                    finally:
                        if stream is not None:
                            close_fn = getattr(stream, "close", None) or getattr(
                                stream, "aclose", None
                            )
                            if callable(close_fn):
                                try:
                                    closed = close_fn()
                                    if inspect.isawaitable(closed):
                                        await closed
                                except Exception:
                                    pass

        raise GroqProviderError(
            "Chat LLM stream failed for configured models",
            fallback_trace=fallback_trace,
        ) from last_error

    async def structured_chat(
        self, messages: Sequence[dict[str, Any]], *, name: str,
        schema: dict[str, Any], max_tokens: int = 1600,
        local_json_validator: Callable[[str], bool] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> GroqChatResult:
        """Read-only decisions use constrained JSON, separate from action tools.

        The configured Qwen/GPT-OSS models support Groq strict outputs. Local
        validation still checks business constraints and grounded references.
        """
        return await self.chat(
            messages, model=model, max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": {
                "name": name, "strict": True, "schema": schema,
            }},
            local_json_validator=local_json_validator,
            reasoning_effort=reasoning_effort,
        )

    async def transcribe(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        language: str = "vi",
        vocabulary: Sequence[str] | None = None,
    ) -> GroqTranscriptionResult:
        # Optional local verification shares the endpoint's overall STT budget.
        # Preserve a successful primary result rather than timing it out while
        # waiting for a second decoder, particularly on CPU or a cold model.
        transcription_deadline = perf_counter() + max(
            0.1, self.settings.groq_stt_total_timeout_seconds,
        )
        local_vi = language == "vi" and self.settings.vietnamese_asr_backend == "zipformer"
        credentials = () if local_vi else self._credentials_or_raise()
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

        if local_vi:
            from backend.services.vietnamese_asr import MODEL_NAME
            models = [MODEL_NAME]

        # Whisper may confidently invent whole sentences over animal/mechanical
        # sounds. Verify acoustic speech independently of its decoder metadata.
        decoded_samples = None
        try:
            if analyze_speech_activity is _DEFAULT_ANALYZE_SPEECH_ACTIVITY:
                analyzed = await asyncio.to_thread(analyze_speech_audio, content)
                activity = analyzed.activity
                decoded_samples = analyzed.samples
            else:
                activity = await asyncio.to_thread(analyze_speech_activity, content)
        except SpeechActivityInputError as exc:
            raise GroqAudioInputError("Audio could not be decoded within analysis limits") from exc
        except SpeechActivityUnavailable as exc:
            raise GroqProviderError("Acoustic verification unavailable") from exc
        acoustic_quality = activity.quality
        if not activity.speech_detected:
            model = models[0] if models else self.settings.groq_stt_model
            logger.info("Groq transcription rejected model=%s reason=no_acoustic_speech", model)
            return GroqTranscriptionResult(
                "", model,
                [{"model": model, "status": "rejected", "reason": "no_acoustic_speech"}],
                speech_detected=False,
                quality=acoustic_quality,
            )

        # Correlation analysis also runs off the event loop.
        signal_quality = await asyncio.to_thread(
            _wav_signal_quality, content, acoustic_quality.get("server_vad_analysis_gain", 1.0),
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
                    model = models[0] if models else self.settings.groq_stt_model
                    logger.info("Groq transcription rejected model=%s reason=%s", model, reason)
                    return GroqTranscriptionResult(
                        "", model,
                        [{"model": model, "status": "rejected", "reason": reason}],
                        speech_detected=False,
                        quality={**signal_quality, **acoustic_quality},
                    )

        if local_vi:
            from backend.services.vietnamese_asr import (
                VietnameseASRUnavailable,
                transcribe_vietnamese,
            )
            try:
                text = await asyncio.to_thread(
                    transcribe_vietnamese, content, self.settings.vietnamese_asr_model_dir,
                    self.settings.vietnamese_asr_decoding,
                )
            except VietnameseASRUnavailable as exc:
                raise GroqProviderError("Local Vietnamese ASR unavailable") from exc
            return GroqTranscriptionResult(
                text, models[0], [{"model": models[0], "status": "success"}],
                speech_detected=bool(text), quality=acoustic_quality,
            )

        last_error: Exception | None = None
        fallback_trace: list[dict[str, Any]] = []
        rejected_model: str | None = None
        rejected_quality: dict[str, float] = {}
        upload = (filename, content, content_type)
        # VAD and PCM quality above use the original waveform. Reducing only
        # large uncompressed WAVs leaves small MP3/Opus capture untouched.
        from backend.services.speech_upload import prepare_stt_upload

        try:
            if decoded_samples is not None and "samples" in inspect.signature(
                prepare_stt_upload
            ).parameters:
                prepared = await asyncio.to_thread(
                    prepare_stt_upload, content, samples=decoded_samples,
                )
            else:
                prepared = await asyncio.to_thread(prepare_stt_upload, content)
        except (SpeechActivityInputError, SpeechActivityUnavailable, ValueError, OSError):
            prepared = None  # Optional transport optimization, never skip acoustic checks.
        if prepared is not None:
            upload = ("speech.flac", prepared, "audio/flac")
            acoustic_quality.update(stt_upload_original_bytes=float(len(content)),
                                    stt_upload_bytes=float(len(prepared)))
        for selected_model in models:
            cred_count = len(credentials)
            rr_start = self._stt_rr_cursor.get(selected_model, 0) % cred_count if cred_count else 0
            ordered_stt_creds = [
                (idx, credentials[idx][0], credentials[idx][1])
                for idx in (*range(rr_start, cred_count), *range(0, rr_start))
            ]
            for slot_index, credential_slot, api_key in ordered_stt_creds:
                if self._stt_cooldowns.get((selected_model, credential_slot), 0) > perf_counter():
                    cooldown_attempt = {"model": selected_model, "status": "cooldown"}
                    if len(credentials) > 1:
                        cooldown_attempt["credential_slot"] = credential_slot
                    fallback_trace.append(cooldown_attempt)
                    continue
                client = self._client_for_credential(credential_slot, api_key)
                started = perf_counter()
                try:
                    attempt_budget = min(
                        self.settings.groq_stt_attempt_timeout_seconds,
                        self.settings.groq_timeout_seconds,
                        transcription_deadline - perf_counter()
                        - min(STT_VERIFIER_RETURN_MARGIN_SECONDS,
                              self.settings.groq_stt_total_timeout_seconds / 10),
                    )
                    if attempt_budget <= 0:
                        raise TimeoutError("No transcription attempt budget remains")
                    response = await asyncio.wait_for(
                        client.audio.transcriptions.create(
                            file=upload,
                            model=selected_model,
                            language=language,
                            prompt=_transcription_prompt(language, vocabulary),
                            response_format="verbose_json",
                            timestamp_granularities=["segment"],
                            temperature=0,
                        ), timeout=attempt_budget,
                    )
                    text = getattr(response, "text", "")
                    if not isinstance(text, str):
                        raise ValueError("Groq returned an invalid transcription payload")
                    if not text.strip():
                        # An empty successful ASR result means there are no words to
                        # submit. A second decoder must not invent a customer turn.
                        speech_detected, quality, rejection_reason = False, {}, "empty_transcript"
                    else:
                        speech_detected, quality, rejection_reason = _transcription_quality(
                            response
                        )
                    quality.update(acoustic_quality)
                    if speech_detected and _has_repeated_phrase(text):
                        speech_detected = False
                        rejection_reason = "repeated_phrase"
                    if speech_detected and _has_insufficient_acoustic_support(
                        text, quality, language
                    ):
                        speech_detected = False
                        rejection_reason = "insufficient_acoustic_support"
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
                        self._stt_rr_cursor[selected_model] = (slot_index + 1) % len(credentials)
                    fallback_trace.append(successful_attempt)
                    confirm = False
                    verification_text = None
                    if language == "vi" and self.settings.vietnamese_asr_verify_uncertain:
                        from backend.services.asr_disagreement import needs_confirmation
                        from backend.services.vietnamese_asr import (
                            VietnameseASRUnavailable,
                            transcribe_vietnamese,
                        )
                        try:
                            verifier_budget = min(
                                STT_OPTIONAL_VERIFIER_MAX_SECONDS,
                                transcription_deadline - perf_counter()
                                - STT_VERIFIER_RETURN_MARGIN_SECONDS,
                            )
                            if verifier_budget <= 0:
                                raise TimeoutError("No optional verification budget remains")
                            other = await asyncio.wait_for(
                                asyncio.to_thread(
                                    transcribe_vietnamese, content,
                                    self.settings.vietnamese_asr_model_dir,
                                    self.settings.vietnamese_asr_decoding,
                                ),
                                timeout=verifier_budget,
                            )
                        except (VietnameseASRUnavailable, SpeechActivityUnavailable, TimeoutError):
                            # An unavailable verifier is not agreement. Keep the
                            # transcript editable and never auto-submit it as a
                            # successfully double-checked customer request.
                            quality["asr_verifier_available"] = 0.0
                            confirm = True
                        else:
                            verification_text = other
                            confirm, distance = needs_confirmation(text, other, quality)
                            quality.update(asr_verifier_available=1.0,
                                           asr_disagreement_ratio=distance)
                    return GroqTranscriptionResult(
                        text.strip(),
                        selected_model,
                        fallback_trace,
                        speech_detected=True,
                        quality=quality,
                        needs_confirmation=confirm,
                        verification_text=verification_text,
                    )
                except Exception as exc:
                    last_error = exc
                    failed_attempt = {
                        "model": selected_model,
                        "status": "error",
                        "error_type": type(exc).__name__,
                    }
                    status = getattr(exc, "status_code", None)
                    response = getattr(exc, "response", None)
                    status = status or getattr(response, "status_code", None)
                    body = getattr(exc, "body", None)
                    code = body.get("code") if isinstance(body, dict) else None
                    if _is_audio_input_provider_error(exc) or (status in {400, 422} and code in {
                        "invalid_media_file", "invalid_audio_file", "unsupported_audio_format",
                    }):
                        # Retry cannot repair a malformed upload. Do not expose
                        # provider bodies, which may contain request details.
                        raise GroqAudioInputError("Audio could not be decoded") from exc
                    if status == 429:
                        headers = _rate_limit_headers(getattr(response, "headers", None))
                        self._stt_cooldowns[(selected_model, credential_slot)] = (
                            perf_counter() + _rate_limit_retry_seconds(headers)
                        )
                        if len(credentials) > 1:
                            self._stt_rr_cursor[selected_model] = (
                                (slot_index + 1) % len(credentials)
                            )
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
