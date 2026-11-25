"""HTTP integration regressions for typed clarification state."""

import json
from uuid import UUID

from fastapi.testclient import TestClient
from test_customer_consultation import Model, Repository, product

from backend.api import main as api
from backend.config import Settings
from backend.services.customer_consultation import _question_id
from backend.services.sessions import InMemorySessionStore


def _settings():
    return Settings(
        _env_file=None,
        groq_api_key="offline-test",
        customer_consultation_enabled=True,
        catalog_repository="demo",
    )


def test_http_skin_type_answer_is_consumed_and_not_repeated(monkeypatch):
    row = product(pid="acne", name="Sữa rửa mặt da mụn Nova").model_copy(
        update={
            "category": "Sữa rửa mặt",
            "category_hierarchy": "Chăm sóc cá nhân > Chăm sóc da > Sữa rửa mặt",
        }
    )
    repo = Repository([row])
    pending_question = "Bạn thuộc loại da nào?"
    model = Model(
        {
            "action": "clarify",
            "need": "tư vấn da mụn",
            "question": pending_question,
            "pending_question": {
                "slot_key": "skin_type",
                "scope": "tư vấn da mụn",
                "question": pending_question,
            },
        },
        {
            "question": pending_question,
            "pending_question": {
                "slot_key": "skin_type", "scope": "tư vấn da mụn",
                "question": pending_question,
            },
        },
        {
            "action": "clarify",
            "need": "tư vấn da mụn",
            "question": pending_question,
            "clarification_answer": {
                "status": "answered",
                "question_id": _question_id(pending_question, "skin_type", "tư vấn da mụn"),
                "slot_key": "skin_type",
                "value": "da dầu",
                "source_quote": "da dầu nha",
            },
            "pending_question": {
                "slot_key": "skin_type",
                "scope": "tư vấn da mụn",
                "question": pending_question,
            },
            "searches": [{"category": "Sữa rửa mặt"}],
        },
        {"action": "recommend", "searches": [{"category": "Sữa rửa mặt"}]},
        {"picks": [{"candidate": 0}]},
        {"acceptable": True},
    )
    monkeypatch.setattr(api, "session_store", InMemorySessionStore())
    monkeypatch.setattr(api, "get_product_repository", lambda *args: repo)
    old = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = _settings
    api.app.dependency_overrides[api.get_groq_service] = lambda: model
    try:
        with TestClient(api.app) as client:
            sid = client.post("/sessions", json={"kiosk_id": "test"}).json()["session_id"]
            first = client.post("/agent/respond", json={"session_id": sid, "text": "Tư vấn da mụn"})
            second = client.post(
                "/agent/respond/stream", json={"session_id": sid, "text": "da dầu nha"}
            )
        assert first.status_code == second.status_code == 200
        assert pending_question in first.json()["text"]
        events = [
            json.loads(line[6:]) for line in second.text.splitlines() if line.startswith("data: ")
        ]
        spoken = "".join(event.get("text", "") for event in events)
        assert pending_question not in spoken
        assert "Nova" in spoken
        memory = api.session_store.get(UUID(sid)).shopping.consultation
        assert memory["answered_slots"][0]["slot_key"] == "skin_type"
        assert memory.get("pending_question") is None
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(old)


def test_http_provider_failure_preserves_pending_answer(monkeypatch):
    repo = Repository([product()])

    class Broken(Model):
        async def chat(self, messages, **kwargs):
            raise RuntimeError("provider down")

    model = Broken()
    monkeypatch.setattr(api, "session_store", InMemorySessionStore())
    monkeypatch.setattr(api, "get_product_repository", lambda *args: repo)
    old = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = _settings
    api.app.dependency_overrides[api.get_groq_service] = lambda: model
    try:
        with TestClient(api.app) as client:
            sid = client.post("/sessions", json={"kiosk_id": "test"}).json()["session_id"]
            api.session_store.update_shopping(
                UUID(sid),
                consultation={"need": "tư vấn da mụn", "last_question": "Bạn thuộc loại da nào?"},
            )
            response = client.post("/agent/respond", json={"session_id": sid, "text": "da dầu nha"})
        assert response.status_code == 200
        memory = api.session_store.get(UUID(sid)).shopping.consultation
        assert memory["unprocessed_answer"]["text"] == "da dầu nha"
        assert memory["unprocessed_answer"]["source_turn_id"]
        assert memory["pending_question"]["question"] == "Bạn thuộc loại da nào?"
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(old)
