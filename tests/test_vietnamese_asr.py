import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.config import Settings
from backend.services.groq import GroqProviderError, GroqService
from backend.services.speech_activity import SpeechActivityResult
from backend.services.vietnamese_asr import (
    MODEL_NAME,
    VietnameseASRUnavailable,
    _recognizer,
    transcribe_vietnamese,
)


@pytest.mark.asyncio
async def test_local_mode_keeps_acoustic_gate_and_does_not_require_cloud_secret(monkeypatch):
    service = GroqService(Settings(vietnamese_asr_backend="zipformer", groq_api_key=""))
    decode = Mock(return_value="KHÔNG LẤY LOẠI CÓ ĐƯỜNG")
    monkeypatch.setattr("backend.services.vietnamese_asr.transcribe_vietnamese", decode)
    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity",
        lambda _: SpeechActivityResult(False, {"test_gate": 1.0}),
    )
    result = await service.transcribe("test.wav", b"audio", "audio/wav", "vi")
    assert not result.speech_detected and result.model == MODEL_NAME and str(result) == ""
    decode.assert_not_called()
    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity", lambda _: SpeechActivityResult(True, {})
    )
    result = await service.transcribe("test.wav", b"audio", "audio/wav", "vi")
    assert str(result) == "KHÔNG LẤY LOẠI CÓ ĐƯỜNG" and result.speech_detected
    assert result.model == MODEL_NAME
    decode.assert_called_once()


@pytest.mark.asyncio
async def test_missing_local_model_is_unavailability_not_speech_or_silent_cloud_switch(monkeypatch):
    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity", lambda _: SpeechActivityResult(True, {})
    )
    monkeypatch.setattr(
        "backend.services.vietnamese_asr.transcribe_vietnamese",
        Mock(side_effect=VietnameseASRUnavailable("missing")),
    )
    service = GroqService(Settings(vietnamese_asr_backend="zipformer"))
    with pytest.raises(GroqProviderError, match="Local Vietnamese ASR unavailable"):
        await service.transcribe("test.wav", b"audio", "audio/wav", "vi")


def test_model_path_fails_before_loading_native_runtime_or_downloading(tmp_path):
    _recognizer.cache_clear()
    with pytest.raises(VietnameseASRUnavailable, match="files are missing"):
        _recognizer(str(tmp_path))


def test_decoder_slot_stays_owned_by_native_worker(monkeypatch):
    import backend.services.vietnamese_asr as local

    monkeypatch.setattr(local, "_decode_audio", Mock(return_value=[]))

    class Recognizer:
        def create_stream(self):
            from types import SimpleNamespace

            return SimpleNamespace(
                accept_waveform=lambda *_: None, result=SimpleNamespace(text="MỘT LÍT KHÔNG ĐƯỜNG")
            )

        def decode_stream(self, _):
            with pytest.raises(VietnameseASRUnavailable, match="busy"):
                transcribe_vietnamese(b"audio", "model")

    monkeypatch.setattr(local, "_recognizer", lambda *_: Recognizer())
    assert transcribe_vietnamese(b"audio", "model") == "MỘT LÍT KHÔNG ĐƯỜNG"
    assert transcribe_vietnamese(b"audio", "model") == "MỘT LÍT KHÔNG ĐƯỜNG"


def cloud_with_verifier(monkeypatch, *, total_budget=20):
    monkeypatch.setattr(
        "backend.services.groq.analyze_speech_activity", lambda _: SpeechActivityResult(True, {})
    )
    primary = AsyncMock(return_value=SimpleNamespace(text="Lấy sữa không đường.", segments=[
        SimpleNamespace(start=0, end=1.6, no_speech_prob=0.001,
                        avg_logprob=-0.12, compression_ratio=0.8),
    ]))
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=primary)))
    service = GroqService(Settings(
        _env_file=None, groq_api_key="test", vietnamese_asr_verify_uncertain=True,
        groq_stt_total_timeout_seconds=total_budget,
    ), client=client)
    return service, primary


@pytest.mark.asyncio
async def test_optional_timeout_preserves_primary_and_native_worker_slot(monkeypatch):
    import backend.services.vietnamese_asr as local

    entered, release, finished = Event(), Event(), Event()
    monkeypatch.setattr("backend.services.groq.STT_OPTIONAL_VERIFIER_MAX_SECONDS", 0.04)
    monkeypatch.setattr(local, "_decode_audio", Mock(return_value=[]))

    class Recognizer:
        def create_stream(self):
            return SimpleNamespace(
                accept_waveform=lambda *_: None, result=SimpleNamespace(text="LẤY SỮA KHÔNG ĐƯỜNG")
            )

        def decode_stream(self, _):
            entered.set()
            release.wait(timeout=2)

    monkeypatch.setattr(local, "_recognizer", lambda *_: Recognizer())
    original_decode = local.transcribe_vietnamese

    def decode(*args):
        try:
            return original_decode(*args)
        finally:
            finished.set()

    monkeypatch.setattr(local, "transcribe_vietnamese", decode)
    service, primary = cloud_with_verifier(monkeypatch)
    try:
        task = asyncio.create_task(service.transcribe("speech.wav", b"audio", "audio/wav", "vi"))
        assert await asyncio.to_thread(entered.wait, 1)
        result = await asyncio.wait_for(task, timeout=0.5)
        assert str(result) == "Lấy sữa không đường."
        assert result.speech_detected and result.needs_confirmation
        assert result.quality["asr_verifier_available"] == 0
        primary.assert_awaited_once()  # No second hosted decode of accepted speech.
        with pytest.raises(VietnameseASRUnavailable, match="busy"):
            original_decode(b"audio", "model")
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
    assert original_decode(b"audio", "model") == "LẤY SỮA KHÔNG ĐƯỜNG"


@pytest.mark.asyncio
async def test_no_remaining_verification_budget_does_not_start_worker(monkeypatch):
    verifier = Mock(return_value="LẤY SỮA KHÔNG ĐƯỜNG")
    monkeypatch.setattr("backend.services.vietnamese_asr.transcribe_vietnamese", verifier)
    service, primary = cloud_with_verifier(monkeypatch, total_budget=0.1)
    result = await service.transcribe("speech.wav", b"audio", "audio/wav", "vi")
    assert str(result) == "Lấy sữa không đường." and result.needs_confirmation
    verifier.assert_not_called()
    primary.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("other,available,confirmation", [
    ("LẤY SỮA KHÔNG ĐƯỜNG", 1, False),
    ("LẤY SỮA CÓ ĐƯỜNG", 1, True),
    (VietnameseASRUnavailable("busy"), 0, True),
])
async def test_enabled_verifier_distinguishes_agreement_disagreement_and_unavailability(
    monkeypatch, other, available, confirmation,
):
    verifier = Mock(side_effect=other) if isinstance(other, Exception) else Mock(return_value=other)
    monkeypatch.setattr("backend.services.vietnamese_asr.transcribe_vietnamese", verifier)
    service, primary = cloud_with_verifier(monkeypatch)
    result = await service.transcribe("speech.wav", b"audio", "audio/wav", "vi")
    assert str(result) == "Lấy sữa không đường."
    assert result.needs_confirmation is confirmation
    assert result.quality["asr_verifier_available"] == available
    primary.assert_awaited_once()
