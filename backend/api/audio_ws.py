"""Real-time WebSocket streaming STT router backed by Azure AI Speech SDK.

Streams 16 kHz mono PCM16 frames directly from the browser while the customer is
still speaking, emitting live partial transcripts and finalizing in ~150-350 ms
after VAD end-of-speech (vs ~2.0-2.5 s for post-utterance WAV upload).

If heavy ambient noise prevents live streaming recognition, transparently runs
the local DPDFNet2 ONNX denoiser + Azure STT rescue on the in-memory PCM buffer
without requiring an extra client upload.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from time import perf_counter
from typing import Any
from uuid import UUID

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.azure_stt import (
    _LOCALE_BY_LANGUAGE,
    _build_phrase_list,
)
from backend.services.catalog_transcription import correct_catalog_words
from backend.services.groq import (
    _has_insufficient_acoustic_support,
    _has_repeated_phrase,
    _is_impulsive_non_speech,
    _is_low_level_non_speech,
    _is_near_silence,
    _is_steady_tone,
    _is_steady_unvoiced_noise,
    _wav_signal_quality,
)
from backend.services.sessions import SessionClosedError, SessionExpiredError, SessionNotFoundError
from backend.services.smart_turn import predict_turn_completion
from backend.services.speech_activity import SAMPLE_RATE, analyze_speech_audio
from backend.services.speech_denoiser import estimate_clip_snr_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audio-stream"])

_MAX_STREAM_PCM_BYTES = SAMPLE_RATE * 2 * 13  # 13 seconds of 16 kHz mono PCM16
_FINALIZE_TIMEOUT_SECONDS = 2.4


def _pcm16_to_wav(pcm_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def _pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm_bytes[:usable], dtype=np.int16).astype(np.float32) / 32768.0


@router.websocket("/audio/transcribe/stream")
async def transcribe_audio_stream(
    websocket: WebSocket,
    session_id: str,
    language: str = "vi",
) -> None:
    """Stream live 16 kHz mono PCM16 audio frames to Azure Speech SDK."""
    await websocket.accept()

    # Late import keeps modular router cleanly decoupled from main.py startup order.
    import backend.api.main as api_main

    settings = api_main.get_settings()
    if language not in {"vi", "en", "zh"}:
        await websocket.send_json({"type": "error", "detail": "Unsupported language"})
        await websocket.close(code=4422)
        return

    try:
        parsed_session_id = UUID(session_id)
        record = api_main.session_store.get(parsed_session_id)
    except (ValueError, SessionNotFoundError):
        await websocket.send_json({"type": "error", "detail": "Session not found"})
        await websocket.close(code=4404)
        return
    except (SessionClosedError, SessionExpiredError):
        await websocket.send_json({"type": "error", "detail": "Session is no longer active"})
        await websocket.close(code=4409)
        return

    if settings.stt_provider != "azure" or not settings.has_azure_speech:
        await websocket.send_json({"type": "fallback", "reason": "azure_stt_disabled"})
        await websocket.close(code=1000)
        return

    key = settings.azure_speech_key.get_secret_value().strip()
    region = settings.azure_speech_region.strip().lower()
    locale = _LOCALE_BY_LANGUAGE.get(language, "vi-VN")

    catalog = api_main.get_transcription_catalog(settings)
    vocabulary: tuple[str, ...] = ()
    list_speech_terms = getattr(catalog, "list_speech_terms", None)
    if callable(list_speech_terms):
        try:
            from backend.services.transcription_context import session_speech_terms

            vocabulary = await api_main.run_catalog_work(
                session_speech_terms,
                catalog,
                record,
                api_main.GROQ_STT_CATALOG_CONTEXT_TERMS,
                timeout_seconds=0.6,
            )
        except Exception:
            vocabulary = ()
    phrases = _build_phrase_list(language, vocabulary)

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=64)
    done_event = asyncio.Event()
    final_segments: list[str] = []
    last_partial = ""
    canceled_error = ""

    def _enqueue(msg: dict[str, Any]) -> None:
        try:
            out_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    try:
        import azure.cognitiveservices.speech as speechsdk

        def _start_recognizer() -> tuple[Any, Any]:
            cfg = speechsdk.SpeechConfig(subscription=key, region=region)
            cfg.speech_recognition_language = locale
            # Frontend Silero VAD + Smart Turn v3 control end-of-turn via `push.close()`.
            # Using 1200 ms stops Azure from prematurely chopping natural mid-sentence pauses
            # without adding any post-end latency once EOF arrives.
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
            if phrases:
                grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
                for phrase in phrases:
                    grammar.addPhrase(phrase)

            def _on_recognizing(evt: Any) -> None:
                nonlocal last_partial
                txt = (getattr(evt.result, "text", "") or "").strip()
                if txt:
                    last_partial = txt
                    combined = " ".join([*final_segments, txt]).strip()
                    loop.call_soon_threadsafe(
                        _enqueue, {"type": "partial", "text": combined}
                    )

            def _on_recognized(evt: Any) -> None:
                nonlocal last_partial
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    txt = (getattr(evt.result, "text", "") or "").strip()
                    if txt:
                        final_segments.append(txt)
                        last_partial = ""
                        combined = " ".join(final_segments).strip()
                        loop.call_soon_threadsafe(
                            _enqueue, {"type": "partial", "text": combined}
                        )

            def _on_canceled(evt: Any) -> None:
                nonlocal canceled_error
                details = getattr(evt, "cancellation_details", None)
                if details and details.reason == speechsdk.CancellationReason.Error:
                    canceled_error = str(details.error_details or details.reason)
                loop.call_soon_threadsafe(done_event.set)

            def _on_stopped(_evt: Any) -> None:
                loop.call_soon_threadsafe(done_event.set)

            recognizer.recognizing.connect(_on_recognizing)
            recognizer.recognized.connect(_on_recognized)
            recognizer.canceled.connect(_on_canceled)
            recognizer.session_stopped.connect(_on_stopped)
            recognizer.start_continuous_recognition_async().get()
            return recognizer, push

        recognizer, push = await asyncio.to_thread(_start_recognizer)
    except Exception as exc:
        logger.warning("Azure WS streaming STT init failed: %s", exc)
        await websocket.send_json({"type": "fallback", "reason": "azure_ws_init_failed"})
        await websocket.close(code=1000)
        return

    async def _sender_loop() -> None:
        while True:
            item = await out_queue.get()
            if item is None:
                return
            try:
                await websocket.send_json(item)
            except Exception:
                return

    sender_task = asyncio.create_task(_sender_loop())
    pcm_buffer = bytearray()
    stream_ended = False
    client_canceled = False
    end_received_at = 0.0

    try:
        await websocket.send_json({"type": "ready", "locale": locale})
        while True:
            message = await websocket.receive()
            msg_type = message.get("type")
            if msg_type == "websocket.disconnect":
                client_canceled = True
                break
            chunk = message.get("bytes")
            if chunk:
                remaining = _MAX_STREAM_PCM_BYTES - len(pcm_buffer)
                if remaining > 0:
                    piece = chunk[:remaining]
                    pcm_buffer.extend(piece)
                    push.write(piece)
                continue
            text_frame = message.get("text")
            if text_frame:
                try:
                    cmd = json.loads(text_frame)
                except ValueError:
                    cmd = {"type": text_frame.strip()}
                action = str(cmd.get("type") or "").lower()
                if action == "check_turn":
                    current_text = (
                        " ".join([*final_segments, last_partial]).strip()
                        or str(cmd.get("partial") or cmd.get("text") or "").strip()
                    )
                    turn_samples = _pcm16_to_float32(bytes(pcm_buffer))
                    turn_res = await asyncio.to_thread(
                        predict_turn_completion,
                        turn_samples,
                        partial_text=current_text,
                        language=language,
                    )
                    _enqueue(
                        {
                            "type": "turn_status",
                            "complete": turn_res.complete,
                            "probability": turn_res.probability,
                            "reason": turn_res.reason,
                            "model": turn_res.model_used,
                            "hold_ms": 0 if turn_res.complete else 1050,
                        }
                    )
                    continue
                if action == "end":
                    stream_ended = True
                    end_received_at = perf_counter()
                    push.close()
                    break
                if action in {"cancel", "abort"}:
                    client_canceled = True
                    break

        if client_canceled or not stream_ended:
            return

        # Wait for Azure to flush the final segment (typically ~150-350 ms).
        try:
            await asyncio.wait_for(done_event.wait(), timeout=_FINALIZE_TIMEOUT_SECONDS)
        except TimeoutError:
            pass

        def _stop_rec() -> None:
            try:
                recognizer.stop_continuous_recognition_async().get()
            except Exception:
                pass

        try:
            await asyncio.wait_for(asyncio.to_thread(_stop_rec), timeout=1.0)
        except TimeoutError:
            pass

        final_text = " ".join(final_segments).strip() or last_partial.strip()
        used_model = f"azure-ws-stream:{locale}"
        rescued = False

        # Heavy-noise DPDFNet2 rescue + acoustic non-speech verification using
        # the already-buffered in-RAM PCM (no extra client upload).
        if len(pcm_buffer) >= SAMPLE_RATE * 2 * 0.25:
            samples = _pcm16_to_float32(bytes(pcm_buffer))
            est_snr = estimate_clip_snr_db(samples) if samples.size else 25.0
            # Guard against fan/HVAC airflow, desk clicks, or quiet room noise
            # producing phantom Azure transcripts over WebSocket.
            ws_has_real_speech = True
            vad_quality: dict[str, float] = {}
            word_count = len(final_text.split())
            if samples.size and (est_snr < 15.0 or word_count <= 3 or not final_text):
                wav_bytes_check = _pcm16_to_wav(bytes(pcm_buffer))
                try:
                    analyzed = await asyncio.to_thread(
                        analyze_speech_audio,
                        wav_bytes_check,
                    )
                    vad_quality = dict(analyzed.activity.quality)
                    ws_has_real_speech = bool(analyzed.activity.speech_detected)
                except Exception:
                    ws_has_real_speech = True
                if ws_has_real_speech:
                    strong_vad = (
                        vad_quality.get("server_vad_positive_frame_ratio", 0.0) >= 0.35
                        and vad_quality.get("server_vad_max_probability", 0.0) >= 0.85
                        and vad_quality.get("server_vad_max_speech_frames", 0.0) >= 12.0
                    )
                    if not strong_vad:
                        sig_q = await asyncio.to_thread(
                            _wav_signal_quality,
                            wav_bytes_check,
                            vad_quality.get("server_vad_analysis_gain", 1.0),
                        )
                        if sig_q is not None and any(
                            pred(sig_q)
                            for pred in (
                                _is_near_silence,
                                _is_low_level_non_speech,
                                _is_steady_unvoiced_noise,
                                _is_steady_tone,
                                _is_impulsive_non_speech,
                            )
                        ):
                            ws_has_real_speech = False
            if not ws_has_real_speech:
                final_text = ""
            else:
                if final_text and vad_quality and _has_insufficient_acoustic_support(
                    final_text, vad_quality, language
                ):
                    final_text = ""
                needs_rescue = (
                    not final_text
                    or bool(canceled_error)
                    or (est_snr < 14.0 and len(final_text.split()) <= 2)
                )
                if needs_rescue:
                    try:
                        azure_stt = api_main.get_azure_stt_service(settings)
                        wav_bytes = _pcm16_to_wav(bytes(pcm_buffer))
                        rescue_res = await asyncio.wait_for(
                            azure_stt.transcribe(
                                "ws-rescue.wav",
                                wav_bytes,
                                "audio/wav",
                                language=language,
                                vocabulary=vocabulary,
                            ),
                            timeout=8.0,
                        )
                        if rescue_res.speech_detected and str(rescue_res).strip():
                            final_text = str(rescue_res).strip()
                            used_model = f"{rescue_res.model}:ws-rescue"
                            rescued = True
                    except Exception as exc:
                        logger.warning("WS DPDFNet2 rescue skipped: %s", exc)

        if final_text and _has_repeated_phrase(final_text):
            final_text = ""

        if final_text and language == "vi":
            try:
                final_text, _ = await asyncio.to_thread(
                    correct_catalog_words,
                    final_text,
                    catalog,
                    language=language,
                )
            except Exception:
                pass

        elapsed_after_end_ms = round((perf_counter() - end_received_at) * 1000.0, 1)
        logger.info(
            "Azure WS STT finished model=%s rescued=%s pcm_sec=%.2f post_end_ms=%.1f",
            used_model,
            rescued,
            len(pcm_buffer) / (SAMPLE_RATE * 2),
            elapsed_after_end_ms,
        )
        await out_queue.put(None)
        await sender_task
        await websocket.send_json(
            {
                "type": "final",
                "session_id": str(parsed_session_id),
                "text": final_text,
                "model": used_model,
                "speech_detected": bool(final_text),
                "elapsed_after_end_ms": elapsed_after_end_ms,
                "rescued_with_denoiser": rescued,
            }
        )
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            push.close()
        except Exception:
            pass
        if not sender_task.done():
            try:
                out_queue.put_nowait(None)
            except asyncio.QueueFull:
                sender_task.cancel()
            try:
                await asyncio.wait_for(sender_task, timeout=0.5)
            except Exception:
                sender_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: recognizer.stop_continuous_recognition_async().get()
                ),
                timeout=0.8,
            )
        except Exception:
            pass
