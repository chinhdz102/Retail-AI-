import asyncio
import threading

import numpy as np
import pytest

from backend.services import speech_activity as activity
from backend.services.whisper_stt import WhisperSTT
from tests.test_whisper_stt import acoustic, payload


@pytest.mark.asyncio
async def test_cancelling_await_does_not_release_still_running_native_slots(monkeypatch):
    both_started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    count = 0

    def decode(content, deadline):
        nonlocal count
        with lock:
            count += 1
            if count == 2:
                both_started.set()
        release.wait(timeout=2)
        return np.zeros(100, dtype=np.float32)

    monkeypatch.setattr(activity, "_WORK_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(activity, "_decode_audio", decode)
    monkeypatch.setattr(activity, "_model_session", lambda: object())
    monkeypatch.setattr(
        activity,
        "_analyze_samples",
        lambda *args, **kwargs: activity.SpeechActivityResult(
            False, {"server_vad_max_probability": 0.0}
        ),
    )
    tasks = [
        asyncio.create_task(asyncio.to_thread(activity.analyze_speech_audio, b"wav"))
        for _ in range(2)
    ]
    try:
        assert await asyncio.to_thread(both_started.wait, 1)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with pytest.raises(activity.SpeechActivityUnavailable, match="capacity"):
            activity.analyze_speech_audio(b"third")
    finally:
        release.set()


@pytest.mark.asyncio
async def test_whole_turn_capacity_released_after_cancellation(monkeypatch):
    from backend.config import Settings
    from backend.services import whisper_stt

    entered = asyncio.Event()
    hold = asyncio.Event()
    monkeypatch.setattr(
        whisper_stt,
        "analyze_speech_audio",
        lambda content: activity.AnalyzedSpeechAudio(
            np.ones(48000, dtype=np.float32) * 0.02,
            activity.SpeechActivityResult(True, acoustic()),
        ),
    )

    class SlowWhisper(WhisperSTT):
        async def _decode(self, client, wav, keys):
            entered.set()
            await hold.wait()
            return payload()

    service = SlowWhisper(Settings(groq_api_key="test"))
    first = asyncio.create_task(service.transcribe(b"wav"))
    second = asyncio.create_task(service.transcribe(b"wav"))
    await entered.wait()
    with pytest.raises(activity.SpeechActivityUnavailable, match="capacity"):
        await service.transcribe(b"third")
    first.cancel()
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    hold.set()
    assert (await service.transcribe(b"new")).speech_detected
