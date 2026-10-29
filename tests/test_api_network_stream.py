from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn

import backend.api.main as api
from backend.services.sessions import InMemorySessionStore


class DelayedGroq:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def chat(self, *_args: Any, **_kwargs: Any) -> str:
        self.started.set()
        try:
            await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return "Mình kể chuyện vui nhé: Vì sao cái bóng đi học muộn? Vì mải đi theo mặt trời!"


class BlockingGroq:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def chat(self, *_args: Any, **_kwargs: Any) -> str:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@asynccontextmanager
async def _network_client(groq: Any) -> AsyncIterator[httpx.AsyncClient]:
    original_store = api.session_store
    previous_override = api.app.dependency_overrides.get(api.get_groq_service)
    had_override = api.get_groq_service in api.app.dependency_overrides
    api.session_store = InMemorySessionStore()
    api.app.dependency_overrides[api.get_groq_service] = lambda: groq

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    server = uvicorn.Server(
        uvicorn.Config(
            api.app,
            host="127.0.0.1",
            port=0,
            log_level="critical",
            lifespan="off",
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if server.started and server.servers:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)
        if not server.started or not server.servers:
            raise RuntimeError("Uvicorn did not start within five seconds")
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=5.0,
        ) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5.0)
        api.session_store = original_store
        if had_override:
            api.app.dependency_overrides[api.get_groq_service] = previous_override
        else:
            api.app.dependency_overrides.pop(api.get_groq_service, None)


async def _create_session(client: httpx.AsyncClient) -> str:
    response = await client.post("/sessions", json={"kiosk_id": "network-sse-test"})
    assert response.status_code == 201, response.text
    return str(response.json()["session_id"])


@pytest.mark.asyncio
async def test_network_sse_first_event_waits_for_grounded_response_and_completes() -> None:
    delay_seconds = 0.25
    groq = DelayedGroq(delay_seconds)
    async with _network_client(groq) as client:
        session_id = await _create_session(client)
        request_started = time.perf_counter()
        events: list[tuple[str, dict[str, Any]]] = []
        first_event_latency_ms: float | None = None
        current_event: str | None = None
        data_lines: list[str] = []

        async with client.stream(
            "POST",
            "/agent/respond/stream",
            json={
                "session_id": session_id,
                "text": "Kể cho mình một chuyện vui",
                "language": "vi",
            },
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data_lines.append(line.removeprefix("data: "))
                elif not line and current_event is not None:
                    event_data = json.loads("\n".join(data_lines))
                    events.append((current_event, event_data))
                    if first_event_latency_ms is None:
                        first_event_latency_ms = (time.perf_counter() - request_started) * 1000
                    current_event = None
                    data_lines = []

        assert groq.started.is_set()
        assert first_event_latency_ms is not None
        assert first_event_latency_ms >= delay_seconds * 1000 * 0.8
        assert events[0][0] == "start"
        assert events[-1][0] == "complete"
        assert all(name == "delta" for name, _payload in events[1:-1])
        streamed_text = "".join(payload["text"] for name, payload in events if name == "delta")
        assert streamed_text.startswith("Mình kể chuyện vui nhé:")
        assert events[0][1]["response"]["session_id"] == session_id
        assert events[-1][1]["response"]["session_id"] == session_id
        assert not groq.cancelled.is_set()


@pytest.mark.asyncio
async def test_network_sse_disconnect_cancels_provider_without_session_or_cart_drift() -> None:
    groq = BlockingGroq()
    async with _network_client(groq) as client:
        session_id = await _create_session(client)
        added = await client.post(
            f"/sessions/{session_id}/cart/items",
            json={"product_id": "WATER01", "quantity": 1},
        )
        assert added.status_code == 200, added.text
        messages_before = list(api.session_store.get(UUID(session_id)).messages)
        cart_before = (await client.get(f"/sessions/{session_id}/cart")).json()

        request_task = asyncio.create_task(
            client.post(
                "/agent/respond/stream",
                json={
                    "session_id": session_id,
                    "text": "Kể cho mình một chuyện vui",
                    "language": "vi",
                    "client_turn_id": str(uuid4()),
                },
            )
        )
        await asyncio.wait_for(groq.started.wait(), timeout=3.0)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
        await asyncio.wait_for(groq.cancelled.wait(), timeout=3.0)

        assert api.session_store.get(UUID(session_id)).messages == messages_before
        assert (await client.get(f"/sessions/{session_id}/cart")).json() == cart_before
