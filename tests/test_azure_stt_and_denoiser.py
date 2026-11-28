"""Tests for Azure AI Speech STT, DPDFNet2 neural speech denoiser, and STT false-edit guards."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from pydantic import SecretStr

import backend.api.main as api
from backend.config import get_settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.azure_stt import AzureSTTService, _build_phrase_list
from backend.services.catalog_transcription import correct_catalog_words
from backend.services.groq import GroqTranscriptionResult
from backend.services.sessions import InMemorySessionStore
from backend.services.speech_activity import (
    AnalyzedSpeechAudio,
    SpeechActivityResult,
)
from backend.services.speech_denoiser import (
    DEFAULT_DENOISER_MODEL,
    enhance_speech_samples,
    estimate_clip_snr_db,
    highpass_85hz,
    trim_to_speech_span,
)


def _wav16(samples: np.ndarray) -> bytes:
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def test_highpass_85hz_attenuates_50hz_hum_and_preserves_speech_band() -> None:
    t = np.arange(16_000, dtype=np.float64) / 16_000.0
    hum_50hz = (0.5 * np.sin(2 * np.pi * 50.0 * t)).astype(np.float32)
    tone_1khz = (0.5 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)
    hum_out = highpass_85hz(hum_50hz)
    tone_out = highpass_85hz(tone_1khz)
    assert float(np.sqrt(np.mean(hum_out**2))) < float(np.sqrt(np.mean(hum_50hz**2))) * 0.35
    assert float(np.sqrt(np.mean(tone_out**2))) > float(np.sqrt(np.mean(tone_1khz**2))) * 0.95


def test_trim_to_speech_span_removes_excess_padding_safely() -> None:
    samples = np.zeros(16_000 * 4, dtype=np.float32)
    samples[16_000 : 16_000 * 2] = 0.2
    trimmed, trimmed_sec = trim_to_speech_span(
        samples, speech_start_sample=16_000, speech_end_sample=16_000 * 2
    )
    assert len(trimmed) < len(samples)
    assert trimmed_sec > 2.0


def test_snr_estimator_and_dpdfnet2_denoiser_runs_cleanly() -> None:
    rng = np.random.default_rng(42)
    t = np.arange(16_000 * 2, dtype=np.float64) / 16_000.0
    speech = np.zeros_like(t)
    speech[8000:24000] = 0.35 * np.sin(2 * np.pi * 440.0 * t[8000:24000])
    clean_sig = (speech + 0.002 * rng.standard_normal(len(t))).astype(np.float32)
    clean_snr = estimate_clip_snr_db(clean_sig)
    noisy_sig = (speech + 0.08 * rng.standard_normal(len(t))).astype(np.float32)
    noisy_snr = estimate_clip_snr_db(noisy_sig)
    assert clean_snr > noisy_snr + 10.0

    if DEFAULT_DENOISER_MODEL.is_file():
        res = enhance_speech_samples(noisy_sig, enabled=True, model_path=DEFAULT_DENOISER_MODEL)
        assert res.denoised is True
        assert res.model_name == "dpdfnet2"
        assert res.samples.dtype == np.float32
        assert np.isfinite(res.samples).all()
        assert res.quality["denoiser_applied"] == 1.0


def test_phrase_list_includes_session_vocab_and_thanh_lam_brands() -> None:
    phrases = _build_phrase_list("vi", ("Mì Hảo Hảo", "Thanh Lam Home"))
    assert "Mì Hảo Hảo" in phrases
    assert "Thanh Lam Home" in phrases
    assert "TH True Milk" in phrases
    assert len(phrases) <= 48


def test_catalog_transcription_no_longer_corrupts_everyday_vietnamese_words() -> None:
    snapshot = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json"
    )
    cases = [
        "Bây giờ mình nói từ thẻ xanh 2 năm tới thẻ xanh vĩnh viễn.",
        "Chúng tôi cũng nhận được sự góp ý của anh em Linh Mục.",
        "Chúng tôi nghĩ rằng là cần đảm bảo an toàn cho người dân.",
        "Tướng Campuchia còn yêu cầu Thủ tướng Singapore điều chỉnh phát biểu.",
    ]
    for raw in cases:
        corrected, _ = correct_catalog_words(raw, snapshot, language="vi")
        assert "MINH" not in corrected
        assert "Bánh" not in corrected
        assert "BÁNH" not in corrected
        assert "Capuchiano" not in corrected

    fixed_towel, _ = correct_catalog_words(
        "Cho mình xem khăn mặt proton Thanh Lam hôn dùng hằng ngày.", snapshot, language="vi"
    )
    assert "khăn mặt cotton" in fixed_towel
    assert "Thanh Lam Home" in fixed_towel

    fixed_beef, _ = correct_catalog_words(
        "ba chỉ bò Mỹ ACFOGS loại nào bán chạy nhất?", snapshot, language="vi"
    )
    assert "ACEFOODS" in fixed_beef


@pytest.mark.asyncio
async def test_azure_stt_service_rest_path_and_non_speech_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(
        update={
            "azure_speech_key": SecretStr("test-azure-key"),
            "azure_speech_region": "japaneast",
            "stt_provider": "azure",
            "azure_stt_use_sdk": False,
            "speech_denoiser_enabled": False,
        }
    )

    class _FakeRestClient:
        def __init__(self) -> None:
            self.calls = 0

        async def post(self, url: str, **_kwargs):
            self.calls += 1
            assert "cognitiveservices/v1" in url
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "RecognitionStatus": "Success",
                    "NBest": [
                        {
                            "Display": "Cho mình xem khăn mặt cotton Thanh Lam Home",
                            "Confidence": 0.94,
                        }
                    ],
                },
            )

    t = np.arange(16_000, dtype=np.float64) / 16_000.0
    speech_like = (0.25 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    wav_bytes = _wav16(speech_like)

    monkeypatch.setattr(
        "backend.services.azure_stt.analyze_speech_audio",
        lambda _b: AnalyzedSpeechAudio(
            samples=speech_like,
            activity=SpeechActivityResult(
                speech_detected=True,
                quality={"server_vad_frame_count": 32.0, "server_vad_positive_frame_ratio": 0.8},
            ),
            speech_start_sample=0,
            speech_end_sample=16_000,
        ),
    )
    monkeypatch.setattr("backend.services.azure_stt._wav_signal_quality", lambda *_a, **_k: None)

    fake_client = _FakeRestClient()
    svc = AzureSTTService(settings=settings, client=fake_client)
    res = await svc.transcribe("clip.wav", wav_bytes, "audio/wav", language="vi")
    assert res.speech_detected is True
    assert str(res) == "Cho mình xem khăn mặt cotton Thanh Lam Home"
    assert res.quality["azure_confidence"] == pytest.approx(0.94)
    assert fake_client.calls == 1


@pytest.mark.asyncio
async def test_azure_stt_keeps_sdk_primary_even_after_rest_pool_warmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(
        update={
            "azure_speech_key": SecretStr("test-azure-key"),
            "azure_speech_region": "japaneast",
            "stt_provider": "azure",
            "azure_stt_use_sdk": True,
            "speech_denoiser_enabled": False,
        }
    )
    t = np.arange(16_000, dtype=np.float64) / 16_000.0
    speech_like = (0.25 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    wav_bytes = _wav16(speech_like)

    monkeypatch.setattr(
        "backend.services.azure_stt.analyze_speech_audio",
        lambda _b: AnalyzedSpeechAudio(
            samples=speech_like,
            activity=SpeechActivityResult(
                speech_detected=True,
                quality={"server_vad_frame_count": 32.0, "server_vad_positive_frame_ratio": 0.8},
            ),
            speech_start_sample=0,
            speech_end_sample=16_000,
        ),
    )
    monkeypatch.setattr("backend.services.azure_stt._wav_signal_quality", lambda *_a, **_k: None)

    svc = AzureSTTService(settings=settings)
    # Simulate lifespan preconnect() populating the fallback REST client lazily.
    svc._rest_client = object()
    sdk_called = False

    def _fake_sdk(_pcm: bytes, locale: str, phrases: list[str], _k: str, _r: str):
        nonlocal sdk_called
        sdk_called = True
        assert locale == "vi-VN"
        assert "Thanh Lam Mart" in phrases
        return "Cho mình xem khăn mặt Thanh Lam Home", {"azure_stt_ms": 310.0}

    monkeypatch.setattr(svc, "_recognize_sdk_sync", _fake_sdk)
    res = await svc.transcribe("clip.wav", wav_bytes, "audio/wav", language="vi")
    assert sdk_called is True
    assert res.model == "azure-sdk:vi-VN"
    assert str(res) == "Cho mình xem khăn mặt Thanh Lam Home"


@pytest.mark.asyncio
async def test_transcribe_endpoint_routes_to_azure_without_touching_groq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_store = api.session_store
    api.session_store = InMemorySessionStore()
    settings = get_settings().model_copy(
        update={
            "stt_provider": "azure",
            "azure_speech_key": SecretStr("test-azure-key"),
            "azure_speech_region": "japaneast",
        }
    )

    class _FakeAzureSTT:
        def __init__(self) -> None:
            self.called = False

        async def transcribe(self, *_args, **_kwargs):
            self.called = True
            return GroqTranscriptionResult(
                "Cho mình xem khăn mặt",
                "azure-sdk:vi-VN+dpdfnet2",
                [{"model": "azure-speech-sdk", "status": "success"}],
                speech_detected=True,
                quality={"denoiser_applied": 1.0, "azure_stt_ms": 420.0},
            )

    class _ForbiddenGroq:
        async def transcribe(self, *_args, **_kwargs):
            raise AssertionError("Groq STT must never be called when stt_provider='azure'")

    fake_azure = _FakeAzureSTT()
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    api.app.dependency_overrides[api.get_azure_stt_service] = lambda: fake_azure
    api.app.dependency_overrides[api.get_groq_service] = lambda: _ForbiddenGroq()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://test",
        ) as client:
            sid = (await client.post("/sessions", json={"kiosk_id": "az-stt"})).json()[
                "session_id"
            ]
            wav = _wav16(np.zeros(3200, dtype=np.float32))
            resp = await client.post(
                "/audio/transcribe",
                data={"session_id": sid, "language": "vi"},
                files={"file": ("clip.wav", wav, "audio/wav")},
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert fake_azure.called is True
            assert payload["model"] == "azure-sdk:vi-VN+dpdfnet2"
            assert payload["text"] == "Cho mình xem khăn mặt"
            assert payload["quality"]["denoiser_applied"] == 1.0
    finally:
        api.session_store = original_store
        api.app.dependency_overrides.pop(api.get_settings, None)
        api.app.dependency_overrides.pop(api.get_azure_stt_service, None)
        api.app.dependency_overrides.pop(api.get_groq_service, None)


def test_streaming_stt_flag_is_false_without_azure_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without the optional Azure Speech SDK, find_spec on the dotted name raises
    # ModuleNotFoundError; POST /sessions must still work and just report False.
    def missing_parent(name: str, *args: object) -> None:
        raise ModuleNotFoundError("No module named 'azure'")

    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(api.importlib.util, "find_spec", missing_parent)
    assert api._is_streaming_stt_available() is False
