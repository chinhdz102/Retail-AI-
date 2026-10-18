from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from backend.shared.contracts import SessionState


class SessionError(RuntimeError):
    """Base session error."""


class SessionNotFoundError(SessionError):
    """Raised when a session ID is unknown."""


class SessionExpiredError(SessionError):
    """Raised when a session TTL has elapsed."""


@dataclass
class SessionRecord:
    session_id: UUID
    kiosk_id: str
    state: SessionState
    created_at: datetime
    expires_at: datetime
    messages: list[dict[str, str]] = field(default_factory=list)
    last_grounded_product_id: str | None = None
    last_grounded_product_name: str | None = None


class InMemorySessionStore:
    """Isolated local/demo store; replace with Redis adapter before multi-instance deploy."""

    def __init__(self, ttl_seconds: int = 1800, max_messages: int = 8) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_messages = max_messages
        self._sessions: dict[UUID, SessionRecord] = {}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def create(self, kiosk_id: str) -> SessionRecord:
        now = self._now()
        record = SessionRecord(
            session_id=uuid4(),
            kiosk_id=kiosk_id,
            state=SessionState.BROWSING,
            created_at=now,
            expires_at=now + self.ttl,
        )
        self._sessions[record.session_id] = record
        return record

    def get(self, session_id: UUID) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionNotFoundError("Session not found")
        is_active = record.state not in {SessionState.CLOSED, SessionState.EXPIRED}
        if is_active and record.expires_at <= self._now():
            record.state = SessionState.EXPIRED
            raise SessionExpiredError("Session expired")
        return record

    def close(self, session_id: UUID) -> SessionRecord:
        record = self.get(session_id)
        record.state = SessionState.CLOSED
        record.expires_at = self._now()
        return record

    def add_message(self, session_id: UUID, role: str, content: str) -> None:
        record = self.get(session_id)
        record.messages.append({"role": role, "content": content})
        if len(record.messages) > self.max_messages:
            del record.messages[: len(record.messages) - self.max_messages]

    def messages_for_model(self, session_id: UUID) -> list[dict[str, str]]:
        return list(self.get(session_id).messages)

    def set_last_grounded_product(
        self,
        session_id: UUID,
        product_id: str | None,
        product_name: str | None = None,
    ) -> None:
        record = self.get(session_id)
        record.last_grounded_product_id = product_id
        record.last_grounded_product_name = product_name if product_id else None
