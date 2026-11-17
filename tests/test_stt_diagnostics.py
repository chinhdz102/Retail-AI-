import json
import os
import time
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.services.stt_diagnostics import save_trace
from backend.services.whisper_stt import WhisperResult, WhisperSTT


def test_capture_preserves_provider_text_and_sent_audio_without_tone_rewriting(tmp_path):
    request_id = str(uuid4())
    result = WhisperResult(text="Ăn lấu", speech_detected=True)
    save_trace(request_id, b"uploaded", b"prepared", {"text": "Ăn lấu"}, result, tmp_path)
    trace = json.loads((tmp_path / request_id / "trace.json").read_text(encoding="utf-8"))
    assert trace["provider_response"]["text"] == trace["returned_result"]["text"] == "Ăn lấu"
    assert (tmp_path / request_id / "provider-input.wav").read_bytes() == b"prepared"
    assert trace["provider_input_sha256"] != trace["uploaded_sha256"]


def test_capture_caps_turns_and_prunes_all_expired_traces_without_skipping(tmp_path):
    stale = time.time() - 1900
    for _ in range(15):
        folder = tmp_path / str(uuid4())
        folder.mkdir()
        os.utime(folder, (stale, stale))
    unrelated = tmp_path / "keep-me"
    unrelated.mkdir()
    for _ in range(15):
        save_trace(str(uuid4()), b"audio", None, None, WhisperResult(), tmp_path)
    assert unrelated.is_dir()
    traces = [p for p in tmp_path.iterdir() if p.name != "keep-me"]
    assert len(traces) == 12
    assert all(p.stat().st_mtime > stale for p in traces)


def test_invalid_request_cannot_write_outside_trace_folder(tmp_path):
    save_trace("../escaped", b"audio", None, None, WhisperResult(), tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,enabled", [("development", False), ("production", True)])
async def test_capture_disabled_by_default_and_always_disabled_in_production(
    monkeypatch, environment, enabled,
):
    def forbidden(*args):
        pytest.fail("Audio traces must not be saved")

    monkeypatch.setattr("backend.services.stt_diagnostics.save_trace", forbidden)
    stt = WhisperSTT(Settings(app_env=environment, stt_debug_capture=enabled))
    await stt._trace(str(uuid4()), b"audio", None, None, WhisperResult())


@pytest.mark.asyncio
async def test_development_capture_receives_same_audio_payload_and_result(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.services.stt_diagnostics.save_trace",
                        lambda *args: calls.append(args))
    stt = WhisperSTT(Settings(app_env="development", stt_debug_capture=True))
    request_id, result = str(uuid4()), WhisperResult(text="Ăn lẩu", speech_detected=True)
    payload = {"text": " Ăn lẩu"}
    await stt._trace(request_id, b"upload", b"pcm", payload, result)
    assert calls == [(request_id, b"upload", b"pcm", payload, result)]
