"""Bounded execution for synchronous retail lookups called from async handlers.

The admission slot belongs to the concurrent future, not the request coroutine:
an HTTP timeout or disconnect cannot stop a Python thread that has already
started. Slots are therefore released only when the underlying future finishes.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import BoundedSemaphore
from typing import Any, TypeVar

from backend.config import get_settings

_settings = get_settings()
_CATALOG_TOOL_EXECUTOR = ThreadPoolExecutor(
    max_workers=_settings.catalog_tool_workers,
    thread_name_prefix="retail-catalog",
)
_CATALOG_TOOL_SLOTS = BoundedSemaphore(_settings.catalog_tool_max_in_flight)


class CatalogWorkCapacityExceeded(RuntimeError):
    """Raised when active and queued retail lookups have reached the process limit."""


class CatalogWorkTimeoutError(TimeoutError):
    """A request deadline expired; retry only if its queued job was cancelled."""

    def __init__(self, *, retryable: bool) -> None:
        super().__init__("Retail catalog work exceeded its request deadline")
        self.retryable = retryable


_T = TypeVar("_T")


def _submit_bounded(function: Callable[..., _T], *args: Any, **kwargs: Any) -> Future[_T]:  # noqa: UP047
    if not _CATALOG_TOOL_SLOTS.acquire(blocking=False):
        raise CatalogWorkCapacityExceeded("Retail lookup capacity is temporarily full")

    context = contextvars.copy_context()
    call = partial(context.run, function, *args, **kwargs)
    try:
        future = _CATALOG_TOOL_EXECUTOR.submit(call)
    except BaseException:
        _CATALOG_TOOL_SLOTS.release()
        raise

    # This callback runs only when the actual worker finishes or a queued future
    # is cancelled. Cancelling the awaiting request never frees a running slot.
    future.add_done_callback(lambda _completed: _CATALOG_TOOL_SLOTS.release())
    return future


async def run_catalog_work(  # noqa: UP047
    function: Callable[..., _T],
    /,
    *args: Any,
    timeout_seconds: float,
    **kwargs: Any,
) -> _T:
    """Run blocking retail work with bounded admission and a caller deadline."""
    future = _submit_bounded(function, *args, **kwargs)
    wrapped = asyncio.wrap_future(future)
    try:
        done, _pending = await asyncio.wait({wrapped}, timeout=timeout_seconds)
        if not done:
            # If the future is already complete at the deadline boundary, return
            # its result. Otherwise cancel it only if the executor has not started it.
            if future.done():
                return future.result()
            retryable = future.cancel()
            wrapped.cancel()
            raise CatalogWorkTimeoutError(retryable=retryable)
        return await wrapped
    except asyncio.CancelledError:
        # On disconnect, remove queued work where possible. Running thread work
        # remains bounded by its slot until its own database/function timeout.
        future.cancel()
        wrapped.cancel()
        raise
