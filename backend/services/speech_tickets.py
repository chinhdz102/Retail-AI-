from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from secrets import token_urlsafe
from time import monotonic


class SpeechTicketStore:
    """Short-lived handles for streaming TTS text across single or serverless instances."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 90.0,
        max_entries: int = 512,
        clock: Callable[[], float] = monotonic,
        token_factory: Callable[[], str] | None = None,
        signing_key_provider: Callable[[], str] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self.token_factory = token_factory or (lambda: token_urlsafe(24))
        self.signing_key_provider = signing_key_provider
        self._tickets: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._consumed_signed: OrderedDict[str, float] = OrderedDict()

    def _expire(self) -> None:
        now = self.clock()
        for token in list(self._tickets):
            expires_at, _ = self._tickets[token]
            if expires_at <= now:
                self._tickets.pop(token, None)
        wall = time.time()
        for sig in list(self._consumed_signed):
            if self._consumed_signed[sig] <= wall:
                self._consumed_signed.pop(sig, None)

    def _signing_key(self) -> str:
        if not self.signing_key_provider:
            return ""
        return (self.signing_key_provider() or "").strip()

    def create(self, payload: dict) -> str:
        self._expire()
        while len(self._tickets) >= self.max_entries:
            self._tickets.popitem(last=False)
        key = self._signing_key()
        if key:
            exp = int(time.time()) + max(30, int(self.ttl_seconds))
            envelope = {
                "p": dict(payload),
                "e": exp,
                "n": token_urlsafe(8),
            }
            raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
            sig = hmac.new(key.encode("utf-8"), b64.encode("ascii"), hashlib.sha256).hexdigest()
            token = f"s1.{b64}.{sig[:32]}"
            self._tickets[token] = (self.clock() + self.ttl_seconds, dict(payload))
            return token
        token = self.token_factory()
        self._tickets[token] = (self.clock() + self.ttl_seconds, dict(payload))
        return token

    def consume(self, token: str) -> dict | None:
        self._expire()
        entry = self._tickets.pop(token, None)
        if entry is not None:
            if token.startswith("s1."):
                self._mark_signed_consumed(token)
            return dict(entry[1])
        if not token.startswith("s1."):
            return None
        key = self._signing_key()
        if not key:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        _prefix, b64, sig = parts
        if sig in self._consumed_signed:
            return None
        expected = hmac.new(
            key.encode("utf-8"), b64.encode("ascii"), hashlib.sha256
        ).hexdigest()[:32]
        if not hmac.compare_digest(expected, sig):
            return None
        try:
            padded = b64 + ("=" * (-len(b64) % 4))
            envelope = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except Exception:
            return None
        if not isinstance(envelope, dict):
            return None
        exp = int(envelope.get("e") or 0)
        if exp <= int(time.time()):
            return None
        payload = envelope.get("p")
        if not isinstance(payload, dict):
            return None
        self._mark_signed_consumed(token, exp=float(exp))
        return dict(payload)

    def _mark_signed_consumed(self, token: str, exp: float | None = None) -> None:
        parts = token.split(".")
        if len(parts) != 3:
            return
        sig = parts[2]
        while len(self._consumed_signed) >= self.max_entries:
            self._consumed_signed.popitem(last=False)
        self._consumed_signed[sig] = exp or (time.time() + self.ttl_seconds)
