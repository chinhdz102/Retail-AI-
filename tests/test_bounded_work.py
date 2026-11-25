from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.services import bounded_work, retail_agent


@pytest.mark.asyncio
async def test_cancelled_request_keeps_running_worker_admitted_until_it_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_EXECUTOR", executor)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_SLOTS", slots)
    started = threading.Event()
    release_worker = threading.Event()

    def lookup() -> None:
        started.set()
        release_worker.wait(timeout=2)

    request = asyncio.create_task(
        bounded_work.run_catalog_work(lookup, timeout_seconds=1)
    )
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        with pytest.raises(bounded_work.CatalogWorkCapacityExceeded):
            await bounded_work.run_catalog_work(lambda: None, timeout_seconds=0.5)
    finally:
        release_worker.set()
        executor.shutdown(wait=True)
        if not request.done():
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.asyncio
async def test_worker_preserves_request_context_and_recovers_after_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_EXECUTOR", executor)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_SLOTS", threading.BoundedSemaphore(1))
    request_context = contextvars.ContextVar("test_request", default="outside")
    token = request_context.set("customer-1")

    def failing_lookup() -> None:
        assert request_context.get() == "customer-1"
        request_context.set("worker-only")
        raise ValueError("lookup failed")

    try:
        with pytest.raises(ValueError, match="lookup failed"):
            await bounded_work.run_catalog_work(failing_lookup, timeout_seconds=0.5)
        assert request_context.get() == "customer-1"
        request_context.reset(token)
        assert await bounded_work.run_catalog_work(
            request_context.get, timeout_seconds=0.5
        ) == "outside"
    finally:
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_timed_out_running_work_keeps_slot_until_worker_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_EXECUTOR", executor)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_SLOTS", slots)

    started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()
    calls: list[str] = []

    def slow_lookup() -> str:
        calls.append("slow")
        started.set()
        release_worker.wait(timeout=2)
        worker_finished.set()
        return "finished"

    try:
        with pytest.raises(bounded_work.CatalogWorkTimeoutError) as timeout:
            await bounded_work.run_catalog_work(slow_lookup, timeout_seconds=0.02)

        assert started.wait(timeout=0.5)
        assert timeout.value.retryable is False
        with pytest.raises(bounded_work.CatalogWorkCapacityExceeded):
            await bounded_work.run_catalog_work(
                lambda: calls.append("must-not-run"), timeout_seconds=0.02
            )
        assert calls == ["slow"]

        release_worker.set()
        assert await asyncio.to_thread(worker_finished.wait, 0.5)
        deadline = time.monotonic() + 0.5
        acquired = False
        while time.monotonic() < deadline:
            acquired = slots.acquire(blocking=False)
            if acquired:
                break
            await asyncio.sleep(0.005)
        assert acquired
        slots.release()
        assert await bounded_work.run_catalog_work(lambda: "recovered", timeout_seconds=0.5) == (
            "recovered"
        )
    finally:
        release_worker.set()
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_queued_work_is_cancelled_at_deadline_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_EXECUTOR", executor)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_SLOTS", slots)

    started = threading.Event()
    release_worker = threading.Event()
    queued_calls: list[str] = []

    def slow_lookup() -> str:
        started.set()
        release_worker.wait(timeout=2)
        return "finished"

    def queued_lookup() -> str:
        queued_calls.append("started")
        return "queued"

    first = asyncio.create_task(
        bounded_work.run_catalog_work(slow_lookup, timeout_seconds=1.0)
    )
    try:
        assert await asyncio.to_thread(started.wait, 0.5)
        with pytest.raises(bounded_work.CatalogWorkTimeoutError) as timeout:
            await bounded_work.run_catalog_work(queued_lookup, timeout_seconds=0.02)

        assert timeout.value.retryable is True
        assert queued_calls == []
        release_worker.set()
        assert await first == "finished"
        assert await bounded_work.run_catalog_work(queued_lookup, timeout_seconds=0.5) == "queued"
        assert queued_calls == ["started"]
    finally:
        release_worker.set()
        if not first.done():
            await first
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_catalog_tool_capacity_failure_is_retryable_and_does_not_queue_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(bounded_work, "_CATALOG_TOOL_SLOTS", slots)
    calls: list[str] = []
    monkeypatch.setattr(
        retail_agent,
        "execute_catalog_tool",
        lambda *args, **kwargs: calls.append("executed"),
    )
    call = {
        "function": {
            "name": "search_products",
            "arguments": '{"query":"sữa"}',
        }
    }

    try:
        name, observation = await retail_agent._execute_tool_call(call, calls_executed=0)
    finally:
        slots.release()

    assert name == "search_products"
    assert observation.status == "TEMPORARY_FAILURE"
    assert observation.content["failure_type"] == "CatalogWorkCapacityExceeded"
    assert calls == []
