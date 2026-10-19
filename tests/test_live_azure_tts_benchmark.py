import json

import pytest

from backend.config import Settings
from backend.services.azure_speech import AzureSpeechAudio
from evaluation.live_azure_tts_benchmark import _median, _percentile, run


class FakeStream:
    voice_id = "vi-VN-TestNeural"
    content_type = "audio/mpeg"

    async def iter_bytes(self):
        yield b"first"
        yield b"second"

    async def aclose(self):
        return None


class FakeSpeechService:
    async def synthesize(self, text, language):
        assert language == "vi"
        return AzureSpeechAudio(b"audio", "audio/mpeg", "vi-VN-TestNeural")

    async def open_stream(self, text, language):
        assert language == "vi"
        return FakeStream()


def test_percentile_uses_nearest_rank():
    assert _percentile([9, 1, 4], 0.5) == 4
    assert _percentile([9, 1, 4], 0.95) == 9
    assert _percentile([], 0.95) is None
    assert _median([9, 1, 4]) == 4
    assert _median([9, 1]) == 5
    assert _median([]) is None


@pytest.mark.asyncio
async def test_tts_benchmark_saves_metrics_without_saving_spoken_text(tmp_path):
    output = tmp_path / "tts.json"
    phrase = "Câu thử không được lưu vào kết quả."

    report = await run(Settings(), output, phrase, "vi", 1, service=FakeSpeechService())

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert report["complete"] is True
    assert saved["summary"]["synthesize_successes"] == 1
    assert saved["summary"]["stream_successes"] == 1
    assert saved["results"][0]["stream"]["audio_bytes"] == len(b"firstsecond")
    assert saved["text_saved"] is False
    assert saved["text_characters"] == len(phrase)
    assert phrase not in output.read_text(encoding="utf-8")
