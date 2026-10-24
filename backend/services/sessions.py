from __future__ import annotations

import copy
import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import redis

from backend.shared.contracts import IntentType, SessionState


class SessionError(RuntimeError):
    """Base session error."""


class SessionNotFoundError(SessionError):
    """Raised when a session ID is unknown."""


class SessionExpiredError(SessionError):
    """Raised when a session TTL has elapsed."""


class SessionClosedError(SessionError):
    """Raised when a customer tries to reuse a closed shopping session."""


class SessionUnavailableError(SessionError):
    """Raised when the configured shared session store cannot be reached."""


class SessionMissionConflictError(SessionError):
    """Raised when a shopping mission changed while a new plan was being built."""


@dataclass
class ShoppingSessionState:
    """Minimal structured shopping memory; anonymous sessions never outlive their TTL."""

    current_intent: IntentType = IntentType.UNKNOWN
    active_product_id: str | None = None
    active_category: str | None = None
    unresolved_product_category: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    selected_product_ids: list[str] = field(default_factory=list)
    comparison_product_ids: list[str] = field(default_factory=list)
    shopping_mission: dict[str, Any] = field(default_factory=dict)
    cart_reference: str | None = None
    pending_clarification: str | None = None
    pending_action: str | None = None
    pending_quantity: int | None = None
    pending_confirmation_id: str | None = None
    pending_order_id: str | None = None
    handoff_status: str | None = None
    handoff_ticket_id: str | None = None
    handoff_previous_state: str | None = None
    payment_status: str | None = None

    def apply_constraint_updates(self, updates: list[dict[str, Any]]) -> None:
        for update in updates:
            action = str(update.get("action", "")).upper()
            if action == "KEEP":
                continue
            if action == "REPLACE":
                replacement = update.get("value")
                if not isinstance(replacement, dict):
                    raise ValueError("REPLACE requires an object value")
                self.constraints = copy.deepcopy(replacement)
                continue
            key = update.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{action} requires a non-empty key")
            key = key.strip()
            if action == "SET":
                self.constraints[key] = copy.deepcopy(update.get("value"))
            elif action == "REMOVE":
                self.constraints.pop(key, None)
            else:
                raise ValueError(f"Unsupported constraint operation: {action}")


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
    shopping: ShoppingSessionState = field(default_factory=ShoppingSessionState)
    state_version: int = 1
    snapshot_disclosures: list[str] = field(default_factory=list)


ALLOWED_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.NEW: {SessionState.BROWSING, SessionState.CLOSED, SessionState.EXPIRED},
    SessionState.BROWSING: {
        SessionState.BROWSING,
        SessionState.CART_ACTIVE,
        SessionState.HANDOFF,
        SessionState.CLOSED,
        SessionState.EXPIRED,
    },
    SessionState.CART_ACTIVE: {
        SessionState.BROWSING,
        SessionState.CART_ACTIVE,
        SessionState.CHECKOUT_CONFIRMATION,
        SessionState.HANDOFF,
        SessionState.CLOSED,
        SessionState.EXPIRED,
    },
    SessionState.CHECKOUT_CONFIRMATION: {
        SessionState.CART_ACTIVE,
        SessionState.PAYMENT_PENDING,
        SessionState.HANDOFF,
        SessionState.CLOSED,
        SessionState.EXPIRED,
    },
    SessionState.PAYMENT_PENDING: {
        SessionState.PAYMENT_PENDING,
        SessionState.CART_ACTIVE,
        SessionState.HANDOFF,
        SessionState.CLOSED,
        SessionState.EXPIRED,
    },
    SessionState.HANDOFF: {
        SessionState.BROWSING,
        SessionState.CART_ACTIVE,
        SessionState.CHECKOUT_CONFIRMATION,
        SessionState.PAYMENT_PENDING,
        SessionState.CLOSED,
        SessionState.EXPIRED,
    },
    SessionState.CLOSED: set(),
    SessionState.EXPIRED: set(),
}


class InMemorySessionStore:
    """Isolated single-process adapter for local demo/test use only."""

    def __init__(self, ttl_seconds: int = 1800, max_messages: int = 8) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_messages = max_messages
        self._sessions: dict[UUID, SessionRecord] = {}
        self._lock = threading.RLock()

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
        with self._lock:
            self._sessions[record.session_id] = record
        return copy.deepcopy(record)

    def _active_record(self, session_id: UUID) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionNotFoundError("Session not found")
        if record.state == SessionState.CLOSED:
            raise SessionClosedError("Session is closed")
        if record.state == SessionState.EXPIRED or record.expires_at <= self._now():
            record.state = SessionState.EXPIRED
            self._clear_private_state(record)
            record.state_version += 1
            raise SessionExpiredError("Session expired")
        # Session lifetime is idle-based: every active API access starts a new TTL window.
        record.expires_at = self._now() + self.ttl
        return record

    @staticmethod
    def _clear_private_state(record: SessionRecord) -> None:
        record.messages.clear()
        record.last_grounded_product_id = None
        record.last_grounded_product_name = None
        record.shopping = ShoppingSessionState()
        record.snapshot_disclosures.clear()

    def get(self, session_id: UUID) -> SessionRecord:
        with self._lock:
            return copy.deepcopy(self._active_record(session_id))

    def close(self, session_id: UUID) -> SessionRecord:
        with self._lock:
            record = self._active_record(session_id)
            record.state = SessionState.CLOSED
            record.expires_at = self._now()
            self._clear_private_state(record)
            record.state_version += 1
            return copy.deepcopy(record)

    def transition(self, session_id: UUID, state: SessionState) -> SessionRecord:
        with self._lock:
            record = self._active_record(session_id)
            if state not in ALLOWED_TRANSITIONS[record.state]:
                raise SessionError(f"Invalid session transition: {record.state} -> {state}")
            record.state = state
            record.state_version += 1
            return copy.deepcopy(record)

    def update_shopping(self, session_id: UUID, **updates: Any) -> SessionRecord:
        allowed = set(ShoppingSessionState.__dataclass_fields__)
        if set(updates) - allowed:
            raise ValueError("Unsupported shopping session field")
        with self._lock:
            record = self._active_record(session_id)
            for key, value in updates.items():
                setattr(record.shopping, key, copy.deepcopy(value))
            record.state_version += 1
            return copy.deepcopy(record)

    def update_shopping_mission_if_revision(
        self,
        session_id: UUID,
        expected_revision: int,
        mission: dict[str, Any],
    ) -> SessionRecord:
        with self._lock:
            record = self._active_record(session_id)
            current_revision = int(record.shopping.shopping_mission.get("revision", 0))
            if current_revision != expected_revision:
                raise SessionMissionConflictError("Shopping mission changed during this turn")
            record.shopping.shopping_mission = copy.deepcopy(mission)
            record.state_version += 1
            return copy.deepcopy(record)

    def update_constraints(self, session_id: UUID, updates: list[dict[str, Any]]) -> SessionRecord:
        with self._lock:
            record = self._active_record(session_id)
            record.shopping.apply_constraint_updates(updates)
            record.state_version += 1
            return copy.deepcopy(record)

    def add_message(self, session_id: UUID, role: str, content: str) -> None:
        with self._lock:
            record = self._active_record(session_id)
            record.messages.append({"role": role, "content": content})
            if len(record.messages) > self.max_messages:
                del record.messages[: len(record.messages) - self.max_messages]
            record.state_version += 1

    def messages_for_model(self, session_id: UUID) -> list[dict[str, str]]:
        with self._lock:
            return copy.deepcopy(self._active_record(session_id).messages)

    def set_last_grounded_product(
        self,
        session_id: UUID,
        product_id: str | None,
        product_name: str | None = None,
    ) -> None:
        with self._lock:
            record = self._active_record(session_id)
            record.last_grounded_product_id = product_id
            record.last_grounded_product_name = product_name if product_id else None
            record.state_version += 1

    def claim_snapshot_disclosures(
        self,
        session_id: UUID,
        disclosure_keys: tuple[str, ...],
    ) -> tuple[str, ...]:
        requested = tuple(dict.fromkeys(key for key in disclosure_keys if key))
        if not requested:
            return ()
        with self._lock:
            record = self._active_record(session_id)
            known = set(record.snapshot_disclosures)
            newly_disclosed = tuple(key for key in requested if key not in known)
            if newly_disclosed:
                record.snapshot_disclosures = (record.snapshot_disclosures + list(newly_disclosed))[
                    -128:
                ]
                record.state_version += 1
            return newly_disclosed


class RedisSessionStore:
    """Redis adapter with optimistic writes; it never falls back to local memory."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int = 1800,
        max_messages: int = 8,
        client: Any | None = None,
        key_prefix: str = "retail:session:",
    ) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_messages = max_messages
        self.key_prefix = key_prefix
        self.client = client or redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _key(self, session_id: UUID) -> str:
        return f"{self.key_prefix}{session_id}"

    def _status_key(self, session_id: UUID) -> str:
        return f"{self._key(session_id)}:status"

    @staticmethod
    def _encode(record: SessionRecord) -> str:
        return json.dumps(
            {
                "session_id": str(record.session_id),
                "kiosk_id": record.kiosk_id,
                "state": record.state.value,
                "created_at": record.created_at.isoformat(),
                "expires_at": record.expires_at.isoformat(),
                "messages": record.messages,
                "last_grounded_product_id": record.last_grounded_product_id,
                "last_grounded_product_name": record.last_grounded_product_name,
                "snapshot_disclosures": record.snapshot_disclosures,
                "shopping": {
                    **record.shopping.__dict__,
                    "current_intent": record.shopping.current_intent.value,
                },
                "state_version": record.state_version,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _decode(raw: str) -> SessionRecord:
        value = json.loads(raw)
        shopping = value.get("shopping", {})
        shopping["current_intent"] = IntentType(shopping.get("current_intent", "UNKNOWN"))
        return SessionRecord(
            session_id=UUID(value["session_id"]),
            kiosk_id=value["kiosk_id"],
            state=SessionState(value["state"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            expires_at=datetime.fromisoformat(value["expires_at"]),
            messages=value.get("messages", []),
            last_grounded_product_id=value.get("last_grounded_product_id"),
            last_grounded_product_name=value.get("last_grounded_product_name"),
            shopping=ShoppingSessionState(**shopping),
            state_version=value.get("state_version", 1),
            snapshot_disclosures=value.get("snapshot_disclosures", []),
        )

    def create(self, kiosk_id: str) -> SessionRecord:
        now = self._now()
        record = SessionRecord(
            session_id=uuid4(),
            kiosk_id=kiosk_id,
            state=SessionState.BROWSING,
            created_at=now,
            expires_at=now + self.ttl,
        )
        try:
            with self.client.pipeline() as pipe:
                pipe.multi()
                pipe.set(
                    self._key(record.session_id),
                    self._encode(record),
                    ex=math.ceil(self.ttl.total_seconds()),
                )
                pipe.set(
                    self._status_key(record.session_id),
                    "ACTIVE",
                    ex=math.ceil(self.ttl.total_seconds()) + 60,
                )
                pipe.execute()
        except redis.RedisError as exc:
            raise SessionUnavailableError("Shared session store is unavailable") from exc
        return copy.deepcopy(record)

    def _missing(self, session_id: UUID) -> None:
        try:
            status = self.client.get(self._status_key(session_id))
        except redis.RedisError as exc:
            raise SessionUnavailableError("Shared session store is unavailable") from exc
        if status == "EXPIRED":
            raise SessionExpiredError("Session expired")
        if status == "CLOSED":
            raise SessionClosedError("Session is closed")
        raise SessionNotFoundError("Session not found")

    def get(self, session_id: UUID) -> SessionRecord:
        key = self._key(session_id)
        status_key = self._status_key(session_id)
        for _ in range(5):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if raw is None:
                        pipe.unwatch()
                        self._missing(session_id)
                    try:
                        record = self._decode(raw)
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        pipe.unwatch()
                        raise SessionUnavailableError("Stored session data is invalid") from exc
                    if record.state == SessionState.CLOSED:
                        raise SessionClosedError("Session is closed")
                    now = self._now()
                    if record.state == SessionState.EXPIRED or record.expires_at <= now:
                        pipe.unwatch()
                        self._expire(record)
                        raise SessionExpiredError("Session expired")
                    record.expires_at = now + self.ttl
                    remaining = max(1, math.ceil(self.ttl.total_seconds()))
                    pipe.multi()
                    pipe.set(key, self._encode(record), ex=remaining)
                    pipe.set(status_key, "ACTIVE", ex=remaining + 60)
                    pipe.execute()
                    return copy.deepcopy(record)
            except redis.WatchError:
                continue
            except redis.RedisError as exc:
                raise SessionUnavailableError("Shared session store is unavailable") from exc
        raise SessionUnavailableError("Session changed concurrently; retry the request")

    def _expire(self, record: SessionRecord) -> None:
        record.state = SessionState.EXPIRED
        InMemorySessionStore._clear_private_state(record)
        try:
            with self.client.pipeline() as pipe:
                pipe.multi()
                pipe.delete(self._key(record.session_id))
                pipe.set(self._status_key(record.session_id), "EXPIRED", ex=60)
                pipe.execute()
        except redis.RedisError as exc:
            raise SessionUnavailableError("Shared session store is unavailable") from exc

    def _mutate(
        self,
        session_id: UUID,
        mutator: Callable[[SessionRecord], Any],
        *,
        close: bool = False,
    ) -> SessionRecord:
        key = self._key(session_id)
        status_key = self._status_key(session_id)
        for _ in range(5):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if raw is None:
                        pipe.unwatch()
                        self._missing(session_id)
                    record = self._decode(raw)
                    if record.state == SessionState.CLOSED:
                        raise SessionClosedError("Session is closed")
                    if record.expires_at <= self._now():
                        pipe.unwatch()
                        self._expire(record)
                        raise SessionExpiredError("Session expired")
                    mutator(record)
                    record.state_version += 1
                    record.expires_at = self._now() + self.ttl
                    remaining = max(1, math.ceil(self.ttl.total_seconds()))
                    pipe.multi()
                    if close:
                        record.state = SessionState.CLOSED
                        record.expires_at = self._now()
                        InMemorySessionStore._clear_private_state(record)
                        pipe.delete(key)
                        pipe.set(status_key, "CLOSED", ex=60)
                    else:
                        pipe.set(key, self._encode(record), ex=remaining)
                        pipe.set(status_key, "ACTIVE", ex=remaining + 60)
                    pipe.execute()
                    return copy.deepcopy(record)
            except redis.WatchError:
                continue
            except redis.RedisError as exc:
                raise SessionUnavailableError("Shared session store is unavailable") from exc
        raise SessionUnavailableError("Session changed concurrently; retry the request")

    def close(self, session_id: UUID) -> SessionRecord:
        return self._mutate(session_id, lambda _record: None, close=True)

    def transition(self, session_id: UUID, state: SessionState) -> SessionRecord:
        def apply(record: SessionRecord) -> None:
            if state not in ALLOWED_TRANSITIONS[record.state]:
                raise SessionError(f"Invalid session transition: {record.state} -> {state}")
            record.state = state

        return self._mutate(session_id, apply)

    def update_shopping(self, session_id: UUID, **updates: Any) -> SessionRecord:
        allowed = set(ShoppingSessionState.__dataclass_fields__)
        if set(updates) - allowed:
            raise ValueError("Unsupported shopping session field")

        def apply(record: SessionRecord) -> None:
            for key, value in updates.items():
                setattr(record.shopping, key, copy.deepcopy(value))

        return self._mutate(session_id, apply)

    def update_shopping_mission_if_revision(
        self,
        session_id: UUID,
        expected_revision: int,
        mission: dict[str, Any],
    ) -> SessionRecord:
        def apply(record: SessionRecord) -> None:
            current_revision = int(record.shopping.shopping_mission.get("revision", 0))
            if current_revision != expected_revision:
                raise SessionMissionConflictError("Shopping mission changed during this turn")
            record.shopping.shopping_mission = copy.deepcopy(mission)

        return self._mutate(session_id, apply)

    def update_constraints(self, session_id: UUID, updates: list[dict[str, Any]]) -> SessionRecord:
        return self._mutate(
            session_id,
            lambda record: record.shopping.apply_constraint_updates(updates),
        )

    def add_message(self, session_id: UUID, role: str, content: str) -> None:
        def append(record: SessionRecord) -> None:
            record.messages.append({"role": role, "content": content})
            if len(record.messages) > self.max_messages:
                del record.messages[: len(record.messages) - self.max_messages]

        self._mutate(session_id, append)

    def messages_for_model(self, session_id: UUID) -> list[dict[str, str]]:
        return self.get(session_id).messages

    def set_last_grounded_product(
        self,
        session_id: UUID,
        product_id: str | None,
        product_name: str | None = None,
    ) -> None:
        self._mutate(
            session_id,
            lambda record: (
                setattr(record, "last_grounded_product_id", product_id),
                setattr(record, "last_grounded_product_name", product_name if product_id else None),
            ),
        )

    def claim_snapshot_disclosures(
        self,
        session_id: UUID,
        disclosure_keys: tuple[str, ...],
    ) -> tuple[str, ...]:
        requested = tuple(dict.fromkeys(key for key in disclosure_keys if key))
        if not requested:
            return ()
        newly_disclosed: list[str] = []

        def apply(record: SessionRecord) -> None:
            known = set(record.snapshot_disclosures)
            newly_disclosed[:] = [key for key in requested if key not in known]
            if newly_disclosed:
                record.snapshot_disclosures = (record.snapshot_disclosures + newly_disclosed)[-128:]

        self._mutate(session_id, apply)
        return tuple(newly_disclosed)


def build_session_store(app_env: str, redis_url: str, ttl_seconds: int):
    """Production always uses Redis; development/test use isolated process memory."""
    if app_env.casefold() in {"production", "prod"}:
        return RedisSessionStore(redis_url, ttl_seconds=ttl_seconds)
    return InMemorySessionStore(ttl_seconds=ttl_seconds)
