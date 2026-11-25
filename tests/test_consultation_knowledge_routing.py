"""Real evidence lookups must work with the demo's consultation layer enabled."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.api import main as api
from backend.config import Settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.customer_consultation import ConsultationResult
from backend.services.sessions import InMemorySessionStore


@pytest.fixture(scope="module")
def snapshot():
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json",
    )


@pytest.fixture
def live_consultation_client(monkeypatch, snapshot):
    class NoModel:
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            pytest.fail("Named product evidence must not depend on a shopping planner")

    model = NoModel()
    monkeypatch.setattr(api, "session_store", InMemorySessionStore())
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    previous = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = lambda: Settings(
        _env_file=None, app_env="development", groq_api_key="offline-test",
        customer_consultation_enabled=True,
    )
    api.app.dependency_overrides[api.get_groq_service] = lambda: model
    try:
        with TestClient(api.app) as client:
            yield client, model
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(previous)


def completed_payload(response, endpoint):
    assert response.status_code == 200
    if endpoint == "/agent/respond":
        return response.json()
    event = ""
    chunks = []
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            if event == "delta":
                chunks.append(payload["text"])
            elif event == "complete":
                return {**payload["response"], "text": "".join(chunks)}
    pytest.fail("Streaming request did not complete")


@pytest.mark.parametrize("endpoint", ["/agent/respond", "/agent/respond/stream"])
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("text,product_id,phrases,expected_model", [
    (
        "Nước giải khát Coca-Cola Zero chai nhựa 1,5lít có bao nhiêu calo?",
        "TLM_10307381", ("0 kcal", "100 ml"), "catalog-manufacturer-declaration",
    ),
    (
        "Kem đánh răng P/S bảo vệ 123 baking soda và hương thảo hộp 230g dùng thế nào?",
        "TLM_10160995", ("ít nhất mỗi ngày 2 lần", "hạt đậu"),
        "catalog-manufacturer-declaration",
    ),
    (
        "Thùng 30 gói mì tôm chua cay Hảo Hảo Vina Acecook 75g pha thế nào?",
        "TLM_10007893", ("400 ml", "3 phút"), "catalog-manufacturer-declaration",
    ),
    (
        "Dầu gội Pantene Pro-V dưỡng tóc suôn mượt óng ả 650g có công dụng gì?",
        "TLM_10016738", ("Pantene Việt Nam", "tuyên bố của nhà sản xuất"),
        "catalog-manufacturer-claim",
    ),
])
def test_product_facts_do_not_enter_consultation(
    live_consultation_client, endpoint, deferred, text, product_id, phrases, expected_model,
):
    client, model = live_consultation_client
    sid = client.post("/sessions", json={"kiosk_id": "knowledge-routing"}).json()["session_id"]
    original_memory = {"unprocessed_request": {
        "text": "Tư vấn đồ dùng cho nhà mới", "source_turn_id": "previous",
    }} if deferred else {}
    api.session_store.update_shopping(UUID(sid), consultation=original_memory)
    payload = completed_payload(
        client.post(endpoint, json={"session_id": sid, "text": text}), endpoint,
    )
    assert payload["model"] == expected_model, payload["text"]
    assert payload["grounded_product_ids"] == [product_id]
    assert payload["tools_used"] == ["search_products", "get_product_knowledge"]
    assert all(phrase in payload["text"] for phrase in phrases)
    assert "Mục nguồn:" not in payload["text"]
    assert "source_field" not in payload["text"]
    assert ".;" not in payload["text"]
    assert "catalog" not in payload["text"]
    assert api.session_store.get(UUID(sid)).shopping.consultation == original_memory
    assert model.calls == 0


@pytest.mark.parametrize("text", [
    "Em muốn mua đồ uống ít đường để đi làm, dưới 20 nghìn.",
    "Da em nhạy cảm, tư vấn sữa tắm dịu nhẹ giúp em.",
    "Có loại dầu gội nào tầm dưới 150 nghìn dùng ổn không?",
    "Mẹ em không thích quá ngọt, có loại bánh nào phù hợp không?",
    "Tư vấn giúp mình vì sao nên chọn loại này.",
])
def test_choice_requests_still_use_consultation(live_consultation_client, monkeypatch, text):
    client, _model = live_consultation_client
    calls = []

    async def consult(**kwargs):
        calls.append(kwargs["text"])
        return ConsultationResult("Đã vào luồng tư vấn chọn mua.", [], {}, "routing-check")

    monkeypatch.setattr(api, "safe_consult_customer", consult)
    sid = client.post("/sessions", json={"kiosk_id": "choice-routing"}).json()["session_id"]
    payload = client.post("/agent/respond", json={"session_id": sid, "text": text}).json()
    assert payload["model"] == "consultation:routing-check"
    assert calls == [text]


def test_grounded_instruction_followup_uses_last_product(live_consultation_client, snapshot):
    client, model = live_consultation_client
    sid = client.post("/sessions", json={"kiosk_id": "instruction-followup"}).json()["session_id"]
    row = snapshot.get_product_snapshot("TLM_10007893")
    api.session_store.set_last_grounded_product(UUID(sid), row.product_id, row.name)
    api.session_store.update_shopping(UUID(sid), selected_product_ids=[row.product_id])
    payload = client.post("/agent/respond", json={
        "session_id": sid, "text": "Loại này pha thế nào?",
    }).json()
    assert payload["model"] == "catalog-manufacturer-declaration"
    assert payload["grounded_product_ids"] == [row.product_id]
    assert "400 ml" in payload["text"] and "3 phút" in payload["text"]
    assert model.calls == 0


@pytest.mark.parametrize("question", [
    "cách pha ra sao?", "cho mình hướng dẫn sử dụng.", "cách nấu như thế nào?",
])
def test_named_instructions_accept_common_wording(live_consultation_client, question):
    client, model = live_consultation_client
    sid = client.post("/sessions", json={"kiosk_id": "usage-paraphrase"}).json()["session_id"]
    payload = client.post("/agent/respond", json={
        "session_id": sid,
        "text": f"Thùng 30 gói mì tôm chua cay Hảo Hảo Vina Acecook 75g {question}",
    }).json()
    assert payload["model"] == "catalog-manufacturer-declaration"
    assert payload["grounded_product_ids"] == ["TLM_10007893"]
    assert "400 ml" in payload["text"] and "3 phút" in payload["text"]
    assert model.calls == 0
