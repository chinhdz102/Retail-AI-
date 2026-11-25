"""Vietnamese-only STT: one decode, independent VAD, one local RNNT decoder.

No catalog context, text repair, network requests, or decoder fallback belong to
this path. TTS is an independent service. Legacy cloud evaluations live outside
the kiosk request path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import BoundedSemaphore
from time import perf_counter

from backend.config import Settings
from backend.services.speech_activity import (
    SAMPLE_RATE,
    SpeechActivityInputError,
    SpeechActivityUnavailable,
    analyze_speech_audio,
)
from backend.services.vietnamese_asr import (
    VietnameseASRUnavailable,
    transcribe_vietnamese_samples,
)

MODEL_ID = "g-group-ai-lab/gipformer1.5-68M-rnnt"
MODEL_REVISION = "dd9227dcd8705c13f33bdbe59728d546ab94480f"
MODEL_NAME = "gipformer1.5-68m-rnnt-int8"
ROOT = Path(__file__).resolve().parents[2]
_WORK_SLOTS = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


class VietnameseSTTInputError(ValueError):
    pass


class VietnameseSTTUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VietnameseTranscription:
    text: str
    quality: dict[str, float] = field(default_factory=dict)
    model: str = MODEL_NAME
    reason: str | None = None

    @property
    def speech_detected(self) -> bool:
        return bool(self.text)


class VietnameseSTTService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def transcribe(self, content: bytes) -> VietnameseTranscription:
        # The worker owns capacity until native inference actually exits, even
        # if this await is cancelled by a timeout or a disconnected visitor.
        return await asyncio.wait_for(
            asyncio.to_thread(self._transcribe, content),
            timeout=self.settings.vietnamese_stt_timeout_seconds,
        )

    def _transcribe(self, content: bytes) -> VietnameseTranscription:
        if not _WORK_SLOTS.acquire(blocking=False):
            raise VietnameseSTTUnavailable("Vietnamese STT is busy")
        started = perf_counter()
        try:
            # A short, conservative onset prevents isolated cough/animal bursts
            # from turning into a customer turn. Browser and server use the same
            # profile. This can miss very short single-syllable utterances.
            audio = analyze_speech_audio(content, positive_threshold=0.75, min_speech_frames=10)
            quality = dict(audio.activity.quality)
            if len(audio.samples) > SAMPLE_RATE * self.settings.vietnamese_stt_max_audio_seconds:
                raise VietnameseSTTInputError("Audio exceeds the Vietnamese STT duration limit")
            if not audio.activity.speech_detected:
                return VietnameseTranscription("", quality, reason="no_acoustic_speech")
            model_dir = Path(self.settings.vietnamese_stt_model_dir).expanduser()
            if not model_dir.is_absolute():
                model_dir = ROOT / model_dir
            text = transcribe_vietnamese_samples(audio.samples, str(model_dir), "greedy_search")
            quality["stt_elapsed_ms"] = (perf_counter() - started) * 1000
            logger.info(
                "Vietnamese STT completed model=%s speech=%s elapsed_ms=%d",
                MODEL_NAME, bool(text), round(quality["stt_elapsed_ms"]),
            )
            return VietnameseTranscription(text, quality, reason=None if text else "empty_decode")
        except SpeechActivityInputError as exc:
            raise VietnameseSTTInputError("Audio could not be decoded") from exc
        except (SpeechActivityUnavailable, VietnameseASRUnavailable) as exc:
            raise VietnameseSTTUnavailable("Vietnamese STT is unavailable") from exc
        finally:
            _WORK_SLOTS.release()
