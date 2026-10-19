from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import redis

from backend.services.sessions import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionClosedError,
    SessionExpiredError,
    SessionUnavailableError,
    build_session_store,
)
from backend.shared.contracts import IntentType, SessionState


def test_structured_shopping_memory_applies_set_replace_remove_and_keep() -> None:
    store = InMemorySessionStore()
    session = store.create("memory-test")

    store.update_constraints(
        session.session_id,
        [
            {"action": "SET", "key": "budget_vnd", "value": 50_000},
            {"action": "SET", "key": "spicy", "value": False},
            {"action": "KEEP"},
        ],
    )
    store.update_constraints(
        session.session_id,
        [
            {"action": "REMOVE", "key": "budget_vnd"},
            {"action": "REPLACE", "value": {"category": "snack"}},
        ],
    )
    store.update_shopping(
        session.session_id,
        current_intent=IntentType.CART_UPDATE,
        active_product_id="SNACK01",
        selected_product_ids=["SNACK01"],
    )
    saved = store.get(session.session_id)

    assert saved.shopping.constraints == {"category": "snack"}
    assert saved.shopping.current_intent == IntentType.CART_UPDATE
    assert saved.shopping.active_product_id == "SNACK01"
    assert saved.shopping.selected_product_ids == ["SNACK01"]


def test_closed_and_expired_sessions_clear_customer_context() -> None:
    store = InMemorySessionStore(ttl_seconds=60)
    session = store.create("private-context-test")
    store.add_message(session.session_id, "user", "My last product was Coca Zero")
    store.set_last_grounded_product(session.session_id, "COKE_ZERO_330", "Coca-Cola Zero")
    store.claim_snapshot_disclosures(session.session_id, ("demo|COKE_ZERO_330|price",))
    store.update_shopping(session.session_id, active_product_id="COKE_ZERO_330")

    closed = store.close(session.session_id)
    assert closed.state == SessionState.CLOSED
    assert closed.messages == []
    assert closed.last_grounded_product_id is None
    assert closed.shopping.active_product_id is None
    assert closed.snapshot_disclosures == []
    with pytest.raises(SessionClosedError):
        store.get(session.session_id)

    now = datetime(2026, 10, 19, tzinfo=UTC)
    expiring = InMemorySessionStore(ttl_seconds=60)
    monkeypatch_now = now
    expiring._now = lambda: monkeypatch_now
    expired_session = expiring.create("expiry-test")
    expiring.add_message(expired_session.session_id, "user", "private text")
    monkeypatch_now += timedelta(seconds=61)

    with pytest.raises(SessionExpiredError):
        expiring.get(expired_session.session_id)
    expired_record = expiring._sessions[expired_session.session_id]
    assert expired_record.messages == []
    assert expired_record.shopping.active_product_id is None
    assert expired_record.snapshot_disclosures == []


def test_snapshot_disclosure_claim_is_once_per_session_and_redis_round_trips() -> None:
    store = InMemorySessionStore()
    session = store.create("snapshot-disclosure-test")
    keys = ("demo|COKE_ZERO_330|price", "demo|COKE_ZERO_330|stock")

    assert store.claim_snapshot_disclosures(session.session_id, keys) == keys
    assert store.claim_snapshot_disclosures(session.session_id, keys) == ()

    saved = store.get(session.session_id)
    decoded = RedisSessionStore._decode(RedisSessionStore._encode(saved))
    assert decoded.snapshot_disclosures == list(keys)

    other = store.create("snapshot-disclosure-isolation")
    assert store.claim_snapshot_disclosures(other.session_id, keys) == keys


def test_redis_snapshot_disclosure_claim_persists_atomically() -> None:
    class MemoryRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def pipeline(self):
            return MemoryPipeline(self)

    class MemoryPipeline:
        def __init__(self, client: MemoryRedis) -> None:
            self.client = client
            self.pending: list[tuple[str, str, int | None]] = []
            self.in_transaction = False

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def watch(self, _key: str) -> None:
            return None

        def get(self, key: str) -> str | None:
            return self.client.values.get(key)

        def unwatch(self) -> None:
            return None

        def multi(self) -> None:
            self.in_transaction = True

        def set(self, key: str, value: str, ex: int | None = None):
            self.pending.append((key, value, ex))
            return self

        def execute(self) -> list[bool]:
            for key, value, _expiry in self.pending:
                self.client.values[key] = value
            return [True for _ in self.pending]

    client = MemoryRedis()
    store = RedisSessionStore("redis://unused", client=client)
    session = InMemorySessionStore().create("redis-disclosure-test")
    client.values[store._key(session.session_id)] = store._encode(session)
    client.values[store._status_key(session.session_id)] = "ACTIVE"
    keys = ("demo|COKE_ZERO_330|price_vnd",)

    assert store.claim_snapshot_disclosures(session.session_id, keys) == keys
    assert store.claim_snapshot_disclosures(session.session_id, keys) == ()
    assert store.get(session.session_id).snapshot_disclosures == list(keys)


def test_session_ttl_is_refreshed_by_activity_and_expires_after_idle_period() -> None:
    now = datetime(2026, 10, 19, tzinfo=UTC)
    current = now
    store = InMemorySessionStore(ttl_seconds=60)
    store._now = lambda: current
    session = store.create("idle-timeout-test")
    first_expiry = session.expires_at

    current += timedelta(seconds=50)
    refreshed = store.get(session.session_id)
    assert refreshed.expires_at > first_expiry
    assert refreshed.expires_at == current + timedelta(seconds=60)

    current += timedelta(seconds=61)
    with pytest.raises(SessionExpiredError):
        store.get(session.session_id)


def test_production_uses_redis_and_fails_closed_when_store_is_unavailable() -> None:
    class OfflineRedis:
        def pipeline(self):
            raise redis.ConnectionError("offline")

    store = build_session_store("production", "redis://localhost:6379/0", 1800)
    assert isinstance(store, RedisSessionStore)

    unavailable = RedisSessionStore(
        "redis://localhost:6379/0",
        client=OfflineRedis(),
    )
    with pytest.raises(SessionUnavailableError):
        unavailable.get(uuid4())
