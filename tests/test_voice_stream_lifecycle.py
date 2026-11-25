"""Exercise API stream lifetime directly, including cancellation before first audio."""

import asyncio
from types import SimpleNamespace

import anyio
import pytest

from backend.api import main as api
from backend.config import Settings
from backend.services.sessions import InMemorySessionStore
from backend.services.speech_tickets import SpeechTicketStore


class Stream:
    content_type = "audio/mpeg"
    voice_id = "test-voice"

    def __init__(self, mode):
        self.mode = mode
        self.closed = False

    async def iter_bytes(self):
        if self.mode == "cancel-before-first":
            raise asyncio.CancelledError()
        yield b"first"
        if self.mode == "provider-failure":
            raise RuntimeError("broken provider stream")
        yield b"second"

    async def aclose(self):
        await anyio.sleep(0)
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["consumer-close", "provider-failure", "cancel-before-first"])
async def test_stream_always_closes_upstream(monkeypatch, mode):
    store = InMemorySessionStore()
    record = store.create("voice-lifecycle")
    tickets = SpeechTicketStore()
    token = tickets.create({"session_id": str(record.session_id), "text": "Test", "language": "vi"})
    monkeypatch.setattr(api, "session_store", store)
    monkeypatch.setattr(api, "speech_ticket_store", tickets)
    stream = Stream(mode)

    async def open_stream(*args):
        return stream

    call = api.stream_synthesis_ticket(
        token,
        azure_speech=SimpleNamespace(open_stream=open_stream),
        elevenlabs=None,
        settings=Settings(_env_file=None, azure_speech_key="test", azure_speech_region="test"),
    )
    if mode == "cancel-before-first":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        response = await call
        assert await anext(response.body_iterator) == b"first"
        if mode == "provider-failure":
            with pytest.raises(RuntimeError):
                await anext(response.body_iterator)
        else:
            await response.body_iterator.aclose()
    assert stream.closed
    assert tickets.consume(token) is None


@pytest.mark.asyncio
async def test_cleanup_survives_cancelled_anyio_scope(monkeypatch):
    store = InMemorySessionStore()
    record = store.create("disconnect")
    tickets = SpeechTicketStore()
    token = tickets.create({"session_id": str(record.session_id), "text": "Test", "language": "vi"})
    monkeypatch.setattr(api, "session_store", store)
    monkeypatch.setattr(api, "speech_ticket_store", tickets)
    stream = Stream("consumer-close")

    async def open_stream(*args):
        return stream

    response = await api.stream_synthesis_ticket(
        token,
        azure_speech=SimpleNamespace(open_stream=open_stream),
        elevenlabs=None,
        settings=Settings(_env_file=None, azure_speech_key="test", azure_speech_region="test"),
    )
    assert await anext(response.body_iterator) == b"first"
    with anyio.CancelScope() as scope:
        scope.cancel()
        await response.body_iterator.aclose()
    assert stream.closed
