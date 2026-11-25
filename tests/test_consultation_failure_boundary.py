"""Provider failure must not lose a validated goal or publish an unchecked draft."""

from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_customer_consultation import Model, Repository, product

from backend.api import main as api
from backend.config import Settings
from backend.services import retail_tools
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReply,
    _render,
    safe_consult_customer,
)
from backend.services.groq import GroqProviderError
from backend.services.sessions import InMemorySessionStore


class FailsAfterPlan(Model):
    async def chat(self, messages, **kwargs):
        if self.calls:
            raise GroqProviderError("unavailable", [{"status_code": 429}])
        return await super().chat(messages, **kwargs)


@pytest.mark.parametrize("text", [
    "Mình ưu tiên khổ A5 để dễ mang theo và so số trang trước khi chọn.",
    "Bạn nên đo chỗ đặt, khoảng 25 đến 30 cm, trước khi chọn quy cách.",
    "Bạn nên dùng hộp 500ml để mang theo và xem kích thước túi trước khi mua.",
])
def test_ordinary_numeric_labels_do_not_become_generated_currency(text):
    answer, rows = _render(ConsultationReply(text=text), [], ConsultationDecision(action="advise"))
    assert answer == text and rows == []


@pytest.mark.parametrize("currency", ["đ", "đồng", "₫", "VND", "k", "nghìn", "%"])
def test_unbound_currency_is_still_rejected(currency):
    with pytest.raises(ValueError, match="unbound price"):
        _render(ConsultationReply(text=f"Món này có giá 57 {currency}, bạn tham khảo nhé."),
                [], ConsultationDecision(action="advise"))


@pytest.mark.asyncio
@pytest.mark.parametrize("category,need,guidance", [
    ("Đèn đọc sách", "đèn cho bàn học nhỏ",
     "Với bàn nhỏ, mình ưu tiên chân đế gọn để bớt chiếm chỗ."),
    ("Sổ", "sổ để mang đi học", "Mình ưu tiên khổ A5 để dễ mang theo, rồi mới so số trang."),
    ("Nồi", "nồi dùng cho bếp từ", "Bạn nên xem ký hiệu dùng cho bếp từ trước khi so dung tích."),
])
async def test_partial_turn_preserves_new_plan_without_publishing_candidates(
    category, need, guidance,
):
    row = product(path=f"Nhà cửa > Dụng cụ > {category}")
    record = InMemorySessionStore().create("failure-checkpoint")
    original = deepcopy(record.shopping.consultation)
    result = await safe_consult_customer(
        text=f"Tư vấn {need}, dưới 100 nghìn",
        turn_id="original-turn",
        record=record,
        repository=Repository([row]),
        groq=FailsAfterPlan({"action": "recommend", "need": need, "guidance": guidance,
                             "searches": [{"category": category}]}),
    )
    assert result.model == "consultation-fallback"
    assert result.products == []
    assert result.memory["need"] == need
    assert result.memory["constraints"]["max_price_vnd"] == 100_000
    assert result.memory["unprocessed_request"]["source_turn_id"] == "original-turn"
    assert guidance not in result.text  # Planner prose has not passed semantic review.
    assert record.shopping.consultation == original
    assert "catalog" not in result.text and "snapshot" not in result.text


@pytest.mark.asyncio
async def test_rejected_draft_is_not_published_when_repair_provider_fails():
    class FailsAfterReview(Model):
        async def chat(self, messages, **kwargs):
            if len(self.calls) == 3:
                raise GroqProviderError("unavailable")
            return await super().chat(messages, **kwargs)

    guidance = "Mình ưu tiên chân đế gọn cho bàn học nhỏ."
    model = FailsAfterReview(
        {"action": "recommend", "need": "đèn bàn học", "guidance": guidance,
         "searches": [{"category": "Đèn đọc sách"}]},
        {"picks": [{"candidate": 0}], "text": "Bạn có thể xem {name:0} ({price:0}) để đọc sách."},
        {"acceptable": False, "issue": "Lý do trong bản nháp chưa có cơ sở."},
    )
    result = await safe_consult_customer(
        text="Tư vấn đèn cho bàn học nhỏ", record=InMemorySessionStore().create("test"),
        repository=Repository([product()]), groq=model,
    )
    assert result.products == [] and result.model == "consultation-fallback"
    assert guidance not in result.text
    assert "Nova" not in result.text


@pytest.mark.asyncio
async def test_planner_fallback_advice_requires_independent_review_approval():
    guidance = "Mình ưu tiên chân đế gọn cho bàn học nhỏ."
    model = Model(
        {"action": "recommend", "need": "đèn cho bàn học nhỏ", "guidance": guidance,
         "searches": [{"category": "Đèn đọc sách"}]},
        {"picks": [{"candidate": 99}], "text": "Bạn xem {name:99} cho bàn học nhỏ."},
        {"picks": [{"candidate": 99}], "text": "Bạn xem {name:99} cho bàn học nhỏ."},
        {"acceptable": False, "issue": "Lời khuyên chưa được chứng minh phù hợp."},
    )
    result = await safe_consult_customer(
        text="Tư vấn đèn cho bàn học nhỏ", record=InMemorySessionStore().create("test"),
        repository=Repository([product()]), groq=model,
    )

    assert len(model.calls) == 4
    assert model.calls[-1][1]["tool_choice"]["function"]["name"] == "ConsultationReview"
    assert result.model == "consultation-fallback" and result.products == []
    assert guidance not in result.text and "Nova" not in result.text


def test_http_failure_does_not_send_active_consultation_to_catalog_controller(monkeypatch):
    class Broken(Model):
        async def chat(self, messages, **kwargs):
            raise GroqProviderError("unavailable")

    repo = Repository([product()])
    monkeypatch.setattr(api, "session_store", InMemorySessionStore())
    monkeypatch.setattr(api, "get_product_repository", lambda *args: repo)
    old = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = lambda: Settings(
        _env_file=None, groq_api_key="offline-test", customer_consultation_enabled=True,
    )
    api.app.dependency_overrides[api.get_groq_service] = lambda: Broken()
    try:
        with TestClient(api.app) as client:
            sid = client.post("/sessions", json={"kiosk_id": "test"}).json()["session_id"]
            api.session_store.update_shopping(UUID(sid), consultation={
                "need": "đèn cho góc đọc sách nhỏ", "constraints": {
                    "searches": [{"category": "Đèn đọc sách"}], "max_price_vnd": 100_000,
                },
            })
            response = client.post("/agent/respond/stream", json={
                "session_id": sid, "text": "Đèn đọc sách loại thường cũng được",
            })
        assert response.status_code == 200
        assert "consultation:consultation-fallback" in response.text
        assert not repo.queries
        assert "Nova" not in response.text
        state = api.session_store.get(UUID(sid)).shopping.consultation
        assert state["constraints"]["max_price_vnd"] == 100_000
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(old)


@pytest.mark.parametrize("turns", [
    [
        "Em muốn mua đồ để dọn nhà cuối tuần, cần những gì nhỉ?",
        "Dọn bếp với lau nhà thôi, tầm 150k",
    ],
    [
        "Nhà mình giặt máy cửa trước, tư vấn nước giặt tầm 150 nghìn thôi",
        "loại nào đáng tiền hơn, chọn giúp mình đi",
        "chuyển sang nước rửa chén dưới 50 nghìn nhé",
    ],
    [
        "tối nay t muốn ăn lẩu m tư vấn cho t với",
        "loại thịt nào để ăn lẩu ý",
        "thịt bò bình thường hoặc thịt ba chỉ cũng oke, tầm 200k thôi",
    ],
])
def test_outage_before_planning_keeps_followups_out_of_unconstrained_catalog(
    monkeypatch, turns,
):
    """Replay observed SSE failures against the real catalog with an offline provider."""
    from backend.products.repository import get_product_repository

    class Broken(Model):
        async def chat(self, messages, **kwargs):
            raise GroqProviderError("unavailable", [{"status_code": 429}])

    repo = get_product_repository(Settings(_env_file=None, catalog_repository="snapshot"))
    monkeypatch.setattr(api, "session_store", InMemorySessionStore())
    monkeypatch.setattr(api, "get_product_repository", lambda *args: repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: repo)
    old = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = lambda: Settings(
        _env_file=None, groq_api_key="offline-test", customer_consultation_enabled=True,
    )
    api.app.dependency_overrides[api.get_groq_service] = lambda: Broken()
    try:
        with TestClient(api.app) as client:
            sid = client.post("/sessions", json={"kiosk_id": "outage-replay"}).json()["session_id"]
            for text in turns:
                response = client.post("/agent/respond/stream", json={
                    "session_id": sid, "text": text,
                })
                assert response.status_code == 200
                assert "event: complete" in response.text
                assert '"model":"consultation:consultation-fallback"' in response.text
                assert '"products":[]' in response.text
                assert "TLM_" not in response.text
                memory = api.session_store.get(UUID(sid)).shopping.consultation
                assert not memory.get("need")  # The planner failed before producing a goal.
                assert memory["unprocessed_request"]["text"] == text
            history = api.session_store.get(UUID(sid)).messages
            assert [m["content"] for m in history if m["role"] == "user"] == turns
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(old)


@pytest.mark.parametrize("text", [
    "Nước rửa chén Sunlight giá bao nhiêu?",
    "Khăn giấy nằm ở đâu?",
])
def test_deferred_consultation_still_delegates_explicit_catalog_facts(monkeypatch, text):
    from backend.products.repository import get_product_repository

    class NoModel(Model):
        async def chat(self, messages, **kwargs):
            pytest.fail("A factual catalog request must not need a provider")

    repo = get_product_repository(Settings(_env_file=None, catalog_repository="snapshot"))
    monkeypatch.setattr(api, "session_store", InMemorySessionStore())
    monkeypatch.setattr(api, "get_product_repository", lambda *args: repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: repo)
    old = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[api.get_settings] = lambda: Settings(
        _env_file=None, groq_api_key="offline-test", customer_consultation_enabled=True,
    )
    api.app.dependency_overrides[api.get_groq_service] = lambda: NoModel()
    try:
        with TestClient(api.app) as client:
            sid = client.post("/sessions", json={"kiosk_id": "facts-after-outage"}).json()[
                "session_id"
            ]
            original = {"unprocessed_request": {
                "text": "Tư vấn đồ cho nhà mới", "source_turn_id": "previous-turn",
            }}
            api.session_store.update_shopping(UUID(sid), consultation=original)
            response = client.post("/agent/respond", json={"session_id": sid, "text": text})
        assert response.status_code == 200
        payload = response.json()
        assert payload["model"] == "catalog" and payload["products"]
        assert api.session_store.get(UUID(sid)).shopping.consultation == original
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(old)


@pytest.mark.asyncio
async def test_advice_with_a_question_passes_realization_and_review_before_publication():
    plan_text = "Chọn sản phẩm phù hợp với mục đích sử dụng sẽ giúp bạn tìm nhanh hơn."
    useful_text = "Với bàn nhỏ, mình ưu tiên đèn có chân đế gọn để bớt chiếm chỗ."
    model = Model(
        {"action": "clarify", "need": "đèn cho bàn học nhỏ", "guidance": plan_text,
         "question": "Bạn cần loại hàng nào?"},
        {"action": "advise", "need": "đèn cho bàn học nhỏ", "guidance": plan_text},
        {"acceptable": False, "issue": "Câu chỉ nói tìm nhanh, chưa giúp chọn cho bàn nhỏ."},
        {"text": useful_text, "picks": [], "question": ""},
        {"acceptable": True},
    )
    result = await safe_consult_customer(
        text="Tư vấn đèn cho bàn nhỏ", record=InMemorySessionStore().create("test"),
        repository=Repository([product()]), groq=model,
    )
    assert result.text == useful_text and plan_text not in result.text
    assert len(model.calls) == 5 and result.memory["pending_question"] is None


@pytest.mark.asyncio
async def test_failure_does_not_bind_customer_turn_to_an_unpublished_question():
    from backend.services.customer_consultation import _deferred_answer

    record = InMemorySessionStore().create("test")
    text = "Tư vấn đèn cho bàn học nhỏ"
    model = FailsAfterPlan({
        "action": "clarify", "need": "đèn cho bàn học nhỏ",
        "guidance": "Mình ưu tiên chân đế gọn để bớt chiếm chỗ trên bàn.",
        "question": "Bạn có thể chi khoảng bao nhiêu?",
        "pending_question": {"slot_key": "budget", "scope": "đèn bàn"},
    })
    result = await safe_consult_customer(
        text=text, turn_id="original", record=record,
        repository=Repository([product()]), groq=model,
    )
    assert result.memory["pending_question"] is None
    assert "unprocessed_answer" not in result.memory
    assert _deferred_answer("thử lại", result.memory)["text"] == text
    assert _deferred_answer("thử lại", result.memory)["source_turn_id"] == "original"
