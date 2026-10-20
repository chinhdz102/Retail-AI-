from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from secrets import token_urlsafe
from time import monotonic


class SpeechTicketStore:
    """Short-lived, one-use handles for streaming private TTS text outside URLs."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        max_entries: int = 512,
        clock: Callable[[], float] = monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self.token_factory = token_factory or (lambda: token_urlsafe(24))
        self._tickets: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def _expire(self) -> None:
        now = self.clock()
        for token in list(self._tickets):
            expires_at, _ = self._tickets[token]
            if expires_at <= now:
                self._tickets.pop(token, None)

    def create(self, payload: dict) -> str:
        self._expire()
        # A token collision must never overwrite an outstanding customer's
        # speech ticket. Generate a unique handle before evicting any ticket.
        for _ in range(16):
            token = self.token_factory()
            if token and token not in self._tickets:
                break
        else:
            raise RuntimeError("Unable to generate a unique speech ticket")
        while len(self._tickets) >= self.max_entries:
            self._tickets.popitem(last=False)
        self._tickets[token] = (self.clock() + self.ttl_seconds, dict(payload))
        return token

    def consume(self, token: str) -> dict | None:
        self._expire()
        entry = self._tickets.pop(token, None)
        return dict(entry[1]) if entry else None
