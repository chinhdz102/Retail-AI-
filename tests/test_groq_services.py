import io
import math
import random
import wave
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.services.groq import (
    MAX_GROQ_STT_PROMPT_BYTES,
    GroqConfigurationError,
    GroqProviderError,
    GroqService,
    _is_impulsive_non_speech,
    _transcription_prompt,
    _wav_signal_quality,
)


class FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Xin chào từ Qwen."))]
        )


class FakeTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text="  Cho tôi biết giá Coca Zero.  ",
            segments=[
                SimpleNamespace(
                    start=0.0,
                    end=1.6,
                    no_speech_prob=0.001,
                    avg_logprob=-0.12,
                    compression_ratio=0.8,
                )
            ],
        )


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions())
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())


class FallbackChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("primary model unavailable")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="Xin chào từ model dự phòng."))
            ]
        )


class FallbackClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FallbackChatCompletions())
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions())


def test_stt_defaults_to_accuracy_first_whisper_model() -> None:
    settings = Settings(_env_file=None, groq_api_key="test-key")

    assert settings.groq_stt_model == "whisper-large-v3"
    assert settings.groq_stt_fallback_model == "whisper-large-v3-turbo"


class FallbackTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("primary transcription model unavailable")
        return SimpleNamespace(
            text="Coca còn mười sáu lon.",
            segments=[
                SimpleNamespace(
                    start=0.0,
                    end=1.5,
                    no_speech_prob=0.01,
                    avg_logprob=-0.2,
                    compression_ratio=0.9,
                )
            ],
        )


@pytest.mark.asyncio
async def test_chat_uses_configured_qwen_model() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_model="qwen/qwen3.8-27b")
    service = GroqService(settings=settings, client=client)

    result = await service.chat([{"role": "user", "content": "Xin chào"}])

    assert result == "Xin chào từ Qwen."
    assert result.model == "qwen/qwen3.8-27b"
    assert client.chat.completions.calls[0]["model"] == "qwen/qwen3.8-27b"
    assert client.chat.completions.calls[0]["reasoning_effort"] == "none"
    assert client.chat.completions.calls[0]["max_tokens"] == 80


@pytest.mark.asyncio
async def test_shopping_mission_tool_call_has_room_for_structured_plan_arguments() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_model="qwen/qwen3.8-27b")
    service = GroqService(settings=settings, client=client)
    tool_choice = {
        "type": "function",
        "function": {"name": "plan_shopping_mission"},
    }

    await service.chat(
        [{"role": "user", "content": "Chuẩn bị buổi gặp mặt."}],
        tools=[
            {
                "type": "function",
                "function": {"name": "plan_shopping_mission", "parameters": {}},
            }
        ],
        tool_choice=tool_choice,
    )

    assert client.chat.completions.calls[0]["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_other_forced_tool_calls_keep_the_existing_token_limit() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_model="qwen/qwen3.8-27b"),
        client=client,
    )

    await service.chat(
        [{"role": "user", "content": "Coca Zero giá bao nhiêu?"}],
        tools=[{"type": "function", "function": {"name": "get_product_snapshot"}}],
        tool_choice={"type": "function", "function": {"name": "get_product_snapshot"}},
    )

    assert client.chat.completions.calls[0]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_chat_reports_the_qwen_fallback_model_that_replied() -> None:
    client = FallbackClient()
    settings = Settings(
        groq_api_key="test-key",
        groq_model="qwen/primary-model",
        groq_fallback_model="qwen/fallback-model",
    )
    service = GroqService(settings=settings, client=client)

    result = await service.chat([{"role": "user", "content": "Xin chào"}])

    assert result == "Xin chào từ model dự phòng."
    assert result.model == "qwen/fallback-model"
    assert result.fallback_trace == [
        {"model": "qwen/primary-model", "status": "error", "error_type": "RuntimeError"},
        {"model": "qwen/fallback-model", "status": "success"},
    ]
    assert [call["model"] for call in client.chat.completions.calls] == [
        "qwen/primary-model",
        "qwen/fallback-model",
    ]


@pytest.mark.asyncio
async def test_transcribe_forces_vietnamese_whisper_prompt() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3-turbo")
    service = GroqService(settings=settings, client=client)

    result = await service.transcribe("voice.webm", b"audio", "audio/webm")

    assert result == "Cho tôi biết giá Coca Zero."
    call = client.audio.transcriptions.calls[0]
    assert call["model"] == "whisper-large-v3-turbo"
    assert call["language"] == "vi"
    assert call["response_format"] == "verbose_json"
    assert call["timestamp_granularities"] == ["segment"]
    assert call["temperature"] == 0
    assert "Tiếng Việt giao tiếp tự nhiên tại cửa hàng" in call["prompt"]
    assert "giọng Bắc, Trung, Nam" in call["prompt"]
    assert "đồ ăn vặt" in call["prompt"]
    assert "hông, hem, mô, chi, nhiêu" in call["prompt"]
    assert "chai, lon, hộp, gói, lốc, thùng" in call["prompt"]
    assert "ml, lít" in call["prompt"]
    assert "Coca Zero" not in call["prompt"]
    assert "Vinamilk" not in call["prompt"]
    assert "không tự chèn hoặc đoán tên" in call["prompt"]
    assert "không đoán" in call["prompt"].casefold()
    assert "không tự thêm từ" not in call["prompt"]


@pytest.mark.asyncio
async def test_transcribe_adds_active_catalog_vocabulary_to_prompt() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3"),
        client=client,
    )

    await service.transcribe(
        "voice.wav",
        b"audio",
        "audio/wav",
        vocabulary=("HUGGIES", "SUNLIGHT", "Lock&Lock", "Bánh Snack", "P/S"),
    )

    prompt = client.audio.transcriptions.calls[0]["prompt"]
    assert "HUGGIES" in prompt
    assert "SUNLIGHT" in prompt
    assert "Lock&Lock" in prompt
    assert "Bánh Snack" in prompt
    assert "P/S" in prompt


def test_default_vietnamese_stt_prompt_does_not_preseed_brand_names() -> None:
    prompt = _transcription_prompt("vi")

    assert "Coca-Cola" not in prompt
    assert "Coca Zero" not in prompt
    assert "Vinamilk" not in prompt
    assert "giọng Bắc, Trung, Nam" in prompt
    assert "không tự chèn hoặc đoán tên" in prompt
    assert "P S" not in prompt
    assert "Chép sát lời" in prompt
    assert "Không nghe rõ lời thì để trống" in prompt


@pytest.mark.asyncio
async def test_transcribe_keeps_vietnamese_catalog_prompt_under_groq_byte_limit() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3"),
        client=client,
    )
    vocabulary = tuple(
        f"Sản phẩm chăm sóc da dành cho gia đình số {index} rất tốt"
        for index in range(1, 33)
    )

    await service.transcribe(
        "voice.wav",
        b"audio",
        "audio/wav",
        vocabulary=vocabulary,
    )

    prompt = client.audio.transcriptions.calls[0]["prompt"]
    assert len(prompt.encode("utf-8")) <= MAX_GROQ_STT_PROMPT_BYTES
    assert vocabulary[0][:48] in prompt
    assert "Catalog:" in prompt or "catalog" in prompt.casefold()
    assert vocabulary[-1] not in prompt


def _pcm_wav(samples: bytes, *, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples)
    return buffer.getvalue()


def _tone_pcm(duration_seconds: float = 3.0, amplitude: int = 500) -> bytes:
    samples = bytearray()
    for index in range(round(duration_seconds * 16_000)):
        value = amplitude * math.sin(2 * math.pi * 440 * index / 16_000)
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    return bytes(samples)


def _quiet_harmonic_pcm() -> bytes:
    samples = bytearray()
    for index in range(16_000):
        time_seconds = index / 16_000
        envelope = 0.45 + 0.55 * abs(math.sin(2 * math.pi * 2.2 * time_seconds))
        value = (
            500
            * envelope
            * sum(
                harmonic * math.sin(2 * math.pi * 155 * multiple * time_seconds)
                for multiple, harmonic in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.2))
            )
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    return bytes(samples)


def _vad_padded_quiet_harmonic_pcm(scale: float = 0.2) -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(0.45 * sample_rate))
    for index in range(sample_rate):
        time_seconds = index / sample_rate
        envelope = 0.45 + 0.55 * abs(math.sin(2 * math.pi * 2.2 * time_seconds))
        value = (
            500
            * scale
            * envelope
            * sum(
                harmonic * math.sin(2 * math.pi * 155 * multiple * time_seconds)
                for multiple, harmonic in ((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.2))
            )
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    samples.extend(b"\x00\x00" * round(1.05 * sample_rate))
    return bytes(samples)


def _room_noise_pcm(
    duration_seconds: float = 3.0,
    *,
    leading_silence_seconds: float = 0.0,
    trailing_silence_seconds: float = 0.0,
    scale: float = 1.0,
) -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(leading_silence_seconds * sample_rate))
    previous = 0.0
    generator = random.Random(2407)
    for index in range(round(duration_seconds * sample_rate)):
        time_seconds = index / sample_rate
        previous = 0.94 * previous + 0.06 * generator.uniform(-1.0, 1.0)
        value = (0.025 * previous + 0.003 * math.sin(2 * math.pi * 120 * time_seconds)) * scale
        samples.extend(round(value * 32767).to_bytes(2, "little", signed=True))
    samples.extend(b"\x00\x00" * round(trailing_silence_seconds * sample_rate))
    return bytes(samples)


def _loud_voiced_pcm(duration_seconds: float = 3.0) -> bytes:
    sample_rate = 16_000
    samples = bytearray()
    for index in range(round(duration_seconds * sample_rate)):
        time_seconds = index / sample_rate
        envelope = 0.94 + 0.06 * math.sin(2 * math.pi * 2.2 * time_seconds)
        value = 6_500 * envelope * sum(
            strength * math.sin(2 * math.pi * 155 * harmonic * time_seconds)
            for harmonic, strength in ((1, 1.0), (2, 0.45), (3, 0.2))
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    return bytes(samples)


@pytest.mark.asyncio
async def test_transcribe_rejects_digital_silence_before_calling_whisper() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe("silence.wav", _pcm_wav(b"\x00\x00" * 16_000), "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "near_silence",
        }
    ]
    assert result.quality["audio_duration_seconds"] == 1.0
    assert client.audio.transcriptions.calls == []


def _click_pcm(duration_seconds: float = 2.0, pulse_seconds: float = 0.01) -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(duration_seconds * sample_rate))
    start = round(0.5 * sample_rate) * 2
    pulse = b"\x00\x78" * round(pulse_seconds * sample_rate)
    samples[start : start + len(pulse)] = pulse
    return bytes(samples)


def _short_voiced_pcm() -> bytes:
    sample_rate = 16_000
    samples = bytearray(b"\x00\x00" * round(0.8 * sample_rate))
    for index in range(round(0.25 * sample_rate)):
        seconds = index / sample_rate
        value = 2_500 * sum(
            strength * math.sin(2 * math.pi * 155 * harmonic * seconds)
            for harmonic, strength in ((1, 1.0), (2, 0.4), (3, 0.2))
        )
        samples.extend(round(value).to_bytes(2, "little", signed=True))
    samples.extend(b"\x00\x00" * round(1.4 * sample_rate))
    return bytes(samples)


@pytest.mark.asyncio
@pytest.mark.parametrize("pulse_seconds", [0.0001, 0.002, 0.01, 0.03])
async def test_transcribe_rejects_sparse_click_transients_before_whisper(
    pulse_seconds: float,
) -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe(
        "mic-click.wav",
        _pcm_wav(_click_pcm(pulse_seconds=pulse_seconds)),
        "audio/wav",
    )

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "impulsive_non_speech",
        }
    ]
    assert result.quality["waveform_active_frame_ratio"] <= 0.15
    assert result.quality["waveform_active_frame_count"] < 3
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
async def test_transcribe_keeps_a_short_voiced_word_with_vad_padding() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )
    signal_quality = _wav_signal_quality(_pcm_wav(_short_voiced_pcm()))

    result = await service.transcribe(
        "short-word.wav",
        _pcm_wav(_short_voiced_pcm()),
        "audio/wav",
    )

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert signal_quality is not None
    assert signal_quality["waveform_active_frame_count"] == 3
    assert signal_quality["waveform_periodicity_mean"] >= 0.58
    assert _is_impulsive_non_speech(signal_quality) is False
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_keeps_quiet_pcm_for_whisper_instead_of_rejecting_it() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )
    result = await service.transcribe("quiet.wav", _pcm_wav(_quiet_harmonic_pcm()), "audio/wav")

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_keeps_low_volume_voiced_segment_with_vad_padding() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )

    result = await service.transcribe(
        "quiet-voice.wav",
        _pcm_wav(_vad_padded_quiet_harmonic_pcm()),
        "audio/wav",
    )

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "duration_seconds",
        "leading_silence_seconds",
        "trailing_silence_seconds",
        "scale",
    ),
    [
        (3.0, 0.0, 0.0, 1.0),
        (0.8, 0.45, 1.05, 1.0),
        (1.5, 0.45, 1.05, 1.0),
        (3.0, 0.0, 0.0, 8.0),
    ],
)
async def test_transcribe_rejects_low_level_room_noise_before_whisper(
    duration_seconds: float,
    leading_silence_seconds: float,
    trailing_silence_seconds: float,
    scale: float,
) -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe(
        "room-noise.wav",
        _pcm_wav(
            _room_noise_pcm(
                duration_seconds,
                leading_silence_seconds=leading_silence_seconds,
                trailing_silence_seconds=trailing_silence_seconds,
                scale=scale,
            )
        ),
        "audio/wav",
    )

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "low_level_non_speech",
        }
    ]
    assert result.quality["waveform_voiced_frame_ratio"] < 0.15
    assert result.quality["waveform_periodicity_mean"] < 0.58
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scale", [20.0, 40.0])
async def test_transcribe_rejects_loud_steady_room_noise_before_whisper(scale: float) -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe(
        "loud-room-noise.wav",
        _pcm_wav(_room_noise_pcm(3.0, scale=scale)),
        "audio/wav",
    )

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "steady_unvoiced_noise",
        }
    ]
    assert result.quality["waveform_rms"] > 0.03
    assert result.quality["waveform_envelope_cv"] <= 0.15
    assert result.quality["waveform_voiced_frame_ratio"] < 0.15
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
async def test_transcribe_keeps_loud_voiced_audio_with_a_steady_envelope() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(_env_file=None, groq_api_key="test-key"),
        client=client,
    )

    result = await service.transcribe(
        "voiced.wav",
        _pcm_wav(_loud_voiced_pcm()),
        "audio/wav",
    )

    assert result == "Cho tôi biết giá Coca Zero."
    assert result.speech_detected is True
    assert len(client.audio.transcriptions.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_rejects_sustained_tone_before_calling_whisper() -> None:
    client = FakeClient()
    service = GroqService(
        settings=Settings(
            _env_file=None,
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe("tone.wav", _pcm_wav(_tone_pcm()), "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "steady_tone",
        }
    ]
    assert result.quality["waveform_crest_factor"] == pytest.approx(2**0.5, abs=0.01)
    assert result.quality["waveform_envelope_cv"] < 0.01
    assert client.audio.transcriptions.calls == []


@pytest.mark.asyncio
async def test_transcribe_rejects_whisper_text_from_silence_metadata() -> None:
    class SilenceTranscriptions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=2.0,
                        no_speech_prob=0.65,
                        avg_logprob=-0.54,
                        compression_ratio=0.98,
                    )
                ],
            )

    transcriptions = SilenceTranscriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=client,
    )

    result = await service.transcribe("silence.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.model == "whisper-large-v3"
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "no_speech",
        }
    ]
    assert [call["model"] for call in transcriptions.calls] == ["whisper-large-v3"]


@pytest.mark.asyncio
async def test_transcribe_rejects_non_speech_tone_hallucination_with_low_log_probability() -> None:
    class ToneTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=5.0,
                        no_speech_prob=0.0,
                        avg_logprob=-0.2,
                        compression_ratio=2.8,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=ToneTranscriptions())),
    )

    result = await service.transcribe("tone.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace[0]["reason"] == "high_compression"


@pytest.mark.asyncio
async def test_transcribe_rejects_repeated_phrase_loop_even_with_good_whisper_scores() -> None:
    class RepeatingTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text=(
                    "Cảm ơn các bạn đã theo dõi và ủng thông, "
                    "và hạn thấy nhiều thông, và hạn thấy nhiều thông."
                ),
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=4.0,
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                        compression_ratio=1.1,
                    )
                ],
            )

    transcriptions = RepeatingTranscriptions()
    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions)),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace[0]["reason"] == "repeated_phrase"
    assert len(result.quality) == 5


@pytest.mark.asyncio
async def test_transcribe_rejects_missing_verbose_quality_metadata() -> None:
    class MetadataMissingTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(text="Có nước suối không ạ?")

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(
            audio=SimpleNamespace(transcriptions=MetadataMissingTranscriptions())
        ),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.fallback_trace[0]["reason"] == "missing_quality_metadata"


@pytest.mark.asyncio
async def test_transcribe_keeps_natural_repetition_in_customer_request() -> None:
    class NaturalRepetitionTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Mua một chai nước, mua thêm hai chai nước.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=2.0,
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                        compression_ratio=0.9,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(
            audio=SimpleNamespace(transcriptions=NaturalRepetitionTranscriptions())
        ),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == "Mua một chai nước, mua thêm hai chai nước."
    assert result.speech_detected is True


@pytest.mark.asyncio
async def test_transcribe_keeps_clear_short_vietnamese_question() -> None:
    class ClearTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Có Coca Zero không ạ?",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        no_speech_prob=0.0005,
                        avg_logprob=-0.402,
                        compression_ratio=0.74,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(groq_api_key="test-key"),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=ClearTranscriptions())),
    )

    result = await service.transcribe("speech.wav", b"audio", "audio/wav")

    assert result == "Có Coca Zero không ạ?"
    assert result.speech_detected is True


@pytest.mark.asyncio
async def test_transcribe_does_not_let_turbo_override_primary_low_quality_rejection() -> None:
    class QualityFallbackTranscriptions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model"] == "whisper-large-v3":
                return SimpleNamespace(
                    text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                    segments=[
                        SimpleNamespace(
                            start=0.0,
                            end=2.0,
                            no_speech_prob=0.72,
                            avg_logprob=-0.2,
                            compression_ratio=0.9,
                        )
                    ],
                )
            return SimpleNamespace(
                text="Có Coca Zero không ạ?",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        no_speech_prob=0.001,
                        avg_logprob=-0.2,
                        compression_ratio=0.8,
                    )
                ],
            )

    transcriptions = QualityFallbackTranscriptions()
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions)),
    )

    result = await service.transcribe("voice.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.model == "whisper-large-v3"
    assert [call["model"] for call in transcriptions.calls] == ["whisper-large-v3"]
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3",
            "status": "rejected",
            "reason": "no_speech",
        }
    ]


@pytest.mark.asyncio
async def test_transcribe_returns_no_speech_when_primary_model_is_low_quality() -> None:
    class RejectedTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                text="Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
                segments=[
                    SimpleNamespace(
                        start=0.0,
                        end=2.0,
                        no_speech_prob=0.7,
                        avg_logprob=-0.2,
                        compression_ratio=0.9,
                    )
                ],
            )

    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_stt_model="whisper-large-v3",
            groq_stt_fallback_model="whisper-large-v3-turbo",
        ),
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=RejectedTranscriptions())),
    )

    result = await service.transcribe("silence.wav", b"audio", "audio/wav")

    assert result == ""
    assert result.speech_detected is False
    assert result.model == "whisper-large-v3"
    assert len(result.fallback_trace) == 1
    assert all(attempt["status"] == "rejected" for attempt in result.fallback_trace)


@pytest.mark.asyncio
async def test_transcribe_uses_chinese_prompt_when_selected() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3-turbo")
    service = GroqService(settings=settings, client=client)

    await service.transcribe("voice.webm", b"audio", "audio/webm", language="zh")

    call = client.audio.transcriptions.calls[0]
    assert call["language"] == "zh"
    assert "可口可乐" in call["prompt"]


@pytest.mark.asyncio
async def test_transcribe_uses_english_prompt_when_selected() -> None:
    client = FakeClient()
    settings = Settings(groq_api_key="test-key", groq_stt_model="whisper-large-v3-turbo")
    service = GroqService(settings=settings, client=client)

    await service.transcribe("voice.webm", b"audio", "audio/webm", language="en")

    call = client.audio.transcriptions.calls[0]
    assert call["language"] == "en"
    assert "retail conversation in English" in call["prompt"]
    assert "Do not translate" in call["prompt"]


@pytest.mark.asyncio
async def test_transcription_fallback_reports_the_model_that_replied() -> None:
    transcriptions = FallbackTranscriptions()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    settings = Settings(
        groq_api_key="test-key",
        groq_stt_model="whisper-large-v3-turbo",
        groq_stt_fallback_model="whisper-large-v3",
    )
    service = GroqService(settings=settings, client=client)

    result = await service.transcribe("voice.webm", b"audio", "audio/webm")

    assert result == "Coca còn mười sáu lon."
    assert result.model == "whisper-large-v3"
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3-turbo",
            "status": "error",
            "error_type": "RuntimeError",
        },
        {"model": "whisper-large-v3", "status": "success"},
    ]


@pytest.mark.asyncio
async def test_missing_key_fails_without_exposing_secret() -> None:
    service = GroqService(settings=Settings(groq_api_key=""), client=None)

    with pytest.raises(GroqConfigurationError) as error:
        await service.chat([])

    assert "test-key" not in str(error.value)
    assert "gsk_" not in str(error.value)


class RawChatResponse:
    headers = {
        "x-ratelimit-remaining-requests": "23",
        "x-ratelimit-remaining-tokens": "7900",
    }

    async def parse(self):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5, total_tokens=16),
        )


class RawChatCompletions:
    @property
    def with_raw_response(self):
        return self

    async def create(self, **kwargs):
        return RawChatResponse()


class RateLimitFailure(RuntimeError):
    def __init__(self) -> None:
        self.status_code = 429
        self.response = SimpleNamespace(headers={"retry-after": "2"})


class RateLimitedRawChatCompletions:
    @property
    def with_raw_response(self):
        return self

    async def create(self, **kwargs):
        raise RateLimitFailure()


@pytest.mark.asyncio
async def test_chat_records_rate_limit_headers_and_token_usage() -> None:
    client = FakeClient()
    client.chat = SimpleNamespace(completions=RawChatCompletions())
    service = GroqService(
        settings=Settings(groq_api_key="test-key", groq_fallback_model=""),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "total_tokens": 16,
    }
    assert result.fallback_trace[0]["rate_limit_headers"] == {
        "x-ratelimit-remaining-requests": "23",
        "x-ratelimit-remaining-tokens": "7900",
    }


@pytest.mark.asyncio
async def test_chat_provider_error_preserves_rate_limit_headers() -> None:
    client = FakeClient()
    client.chat = SimpleNamespace(completions=RateLimitedRawChatCompletions())
    service = GroqService(
        settings=Settings(
            groq_api_key="test-key",
            groq_fallback_model="",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    with pytest.raises(GroqProviderError) as error:
        await service.chat([{"role": "user", "content": "hello"}])

    assert error.value.fallback_trace == [
        {
            "model": "qwen/qwen3.8-27b",
            "status": "error",
            "error_type": "RateLimitFailure",
            "rate_limit_headers": {"retry-after": "2"},
        }
    ]


class RotatingKeyCompletions:
    def __init__(self, failures_before_success: int) -> None:
        self.calls: list[dict] = []
        self.failures_before_success = failures_before_success

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures_before_success:
            raise RateLimitFailure()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Đã dùng key dự phòng."))]
        )


@pytest.mark.asyncio
async def test_chat_rotates_api_keys_after_quota_error_before_model_fallback() -> None:
    completions = RotatingKeyCompletions(failures_before_success=1)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_api_key_fallback_2="fallback-test-key-2",
            groq_model="qwen/primary-model",
            groq_fallback_model="openai/fallback-model",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result == "Đã dùng key dự phòng."
    assert result.model == "qwen/primary-model"
    assert [call["model"] for call in completions.calls] == [
        "qwen/primary-model",
        "qwen/primary-model",
    ]
    assert result.fallback_trace == [
        {
            "model": "qwen/primary-model",
            "status": "error",
            "error_type": "RateLimitFailure",
            "rate_limit_headers": {"retry-after": "2"},
            "credential_slot": "primary",
        },
        {
            "model": "qwen/primary-model",
            "status": "success",
            "credential_slot": "fallback_1",
        },
    ]
    assert "primary-test-key" not in repr(result.fallback_trace)
    assert "fallback-test-key-1" not in repr(result.fallback_trace)


def test_groq_builds_a_separate_client_for_each_configured_key(monkeypatch) -> None:
    from backend.services import groq as groq_module

    created: list[dict] = []

    class RecordingOpenAI:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)

    monkeypatch.setattr(groq_module, "AsyncOpenAI", RecordingOpenAI)
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
        )
    )
    primary_slot, primary_key = service.settings.groq_api_credentials[0]
    fallback_slot, fallback_key = service.settings.groq_api_credentials[1]

    primary_client = service._client_for_credential(primary_slot, primary_key)
    fallback_client = service._client_for_credential(fallback_slot, fallback_key)

    assert primary_client is not fallback_client
    assert [kwargs["api_key"] for kwargs in created] == [
        "primary-test-key",
        "fallback-test-key-1",
    ]
    assert all(kwargs["max_retries"] == 0 for kwargs in created)
    assert service._client_for_credential(primary_slot, primary_key) is primary_client


def test_settings_preserves_all_five_groq_credentials_in_failover_order() -> None:
    settings = Settings(
        groq_api_key="primary-test-key",
        groq_api_key_fallback_1="fallback-test-key-1",
        groq_api_key_fallback_2="fallback-test-key-2",
        groq_api_key_fallback_3="fallback-test-key-3",
        groq_api_key_fallback_4="fallback-test-key-4",
    )

    assert [slot for slot, _ in settings.groq_api_credentials] == [
        "primary",
        "fallback_1",
        "fallback_2",
        "fallback_3",
        "fallback_4",
    ]


@pytest.mark.asyncio
async def test_chat_uses_model_fallback_after_all_configured_keys_are_limited() -> None:
    completions = RotatingKeyCompletions(failures_before_success=3)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_api_key_fallback_2="fallback-test-key-2",
            groq_model="qwen/primary-model",
            groq_fallback_model="openai/fallback-model",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result.model == "openai/fallback-model"
    assert [call["model"] for call in completions.calls] == [
        "qwen/primary-model",
        "qwen/primary-model",
        "qwen/primary-model",
        "openai/fallback-model",
    ]
    assert [attempt.get("credential_slot") for attempt in result.fallback_trace] == [
        "primary",
        "fallback_1",
        "fallback_2",
        "primary",
    ]


@pytest.mark.asyncio
async def test_chat_tries_all_five_keys_before_falling_back_to_another_model() -> None:
    completions = RotatingKeyCompletions(failures_before_success=5)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_api_key_fallback_2="fallback-test-key-2",
            groq_api_key_fallback_3="fallback-test-key-3",
            groq_api_key_fallback_4="fallback-test-key-4",
            groq_model="qwen/primary-model",
            groq_fallback_model="openai/fallback-model",
            groq_fallback_model_2="",
        ),
        client=client,
    )

    result = await service.chat([{"role": "user", "content": "hello"}])

    assert result.model == "openai/fallback-model"
    assert [attempt.get("credential_slot") for attempt in result.fallback_trace] == [
        "primary",
        "fallback_1",
        "fallback_2",
        "fallback_3",
        "fallback_4",
        "primary",
    ]


class RotatingKeyTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RateLimitFailure()
        return SimpleNamespace(
            text="Nước suối còn hàng.",
            segments=[
                SimpleNamespace(
                    start=0.0,
                    end=1.2,
                    no_speech_prob=0.01,
                    avg_logprob=-0.2,
                    compression_ratio=0.9,
                )
            ],
        )


@pytest.mark.asyncio
async def test_transcribe_rotates_api_key_after_quota_error() -> None:
    transcriptions = RotatingKeyTranscriptions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeChatCompletions()),
        audio=SimpleNamespace(transcriptions=transcriptions),
    )
    service = GroqService(
        settings=Settings(
            groq_api_key="primary-test-key",
            groq_api_key_fallback_1="fallback-test-key-1",
            groq_stt_model="whisper-large-v3-turbo",
            groq_stt_fallback_model="whisper-large-v3",
        ),
        client=client,
    )

    result = await service.transcribe("voice.webm", b"audio", "audio/webm")

    assert result == "Nước suối còn hàng."
    assert result.model == "whisper-large-v3-turbo"
    assert result.fallback_trace == [
        {
            "model": "whisper-large-v3-turbo",
            "status": "error",
            "error_type": "RateLimitFailure",
            "credential_slot": "primary",
        },
        {
            "model": "whisper-large-v3-turbo",
            "status": "success",
            "credential_slot": "fallback_1",
        },
    ]
