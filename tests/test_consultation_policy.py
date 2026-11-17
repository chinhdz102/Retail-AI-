"""Behavioural regressions for dialogue decisions and stale product reuse."""

import json

import pytest

from backend.services.consultation import (
    Composition, Plan, Search, _infer_catalog_query, _render_structured, consult,
    use_consultation,
)
from backend.services.groq import GroqProviderError
from backend.services.vietnamese_intent import normalize_vietnamese
from tests.test_consultation import Repository
from tests.test_consultation_review import RecordedReplies


@pytest.mark.asyncio
async def test_rejected_followup_question_is_repaired_without_recommending_old_products():
    model = RecordedReplies(
        {"need": "Quà cho mẹ chưa rõ sở thích", "action": "ask",
         "clarification": "Bạn mua cho bao nhiêu người?"},
        {"acceptable": False, "repair_action": "clarify",
         "issues": ["Cần hỏi sở thích người nhận, số người không giúp chọn quà."]},
        {"question": "Mẹ thích đồ ăn hay đồ dùng trong nhà hơn?"},
        {"acceptable": True, "issues": []},
    )
    repo = Repository()
    result = await consult(model, repo, "Chọn quà cho mẹ giúp mình", [], ["P1"], {
        "need": "Chọn quà", "budget_vnd": 300000, "budget_scope": "item",
        "searches": [{"query": "thịt bò"}],
    })
    assert result.plan.action == "ask"
    assert result.products == ()
    assert result.text == "Mẹ thích đồ ăn hay đồ dùng trong nhà hơn?"
    assert repo.calls == []
    assert len(model.calls) == 4


@pytest.mark.asyncio
async def test_topic_change_with_reuse_flag_cannot_leak_old_product_into_empty_new_search():
    model = RecordedReplies(
        {"need": "Cần nước uống", "topic_changed": True, "reuse_previous": True},
        {"advice": "Bạn thích nước lọc hay trà hơn?"},
        {"acceptable": True, "issues": []},
    )
    await consult(model, Repository(), "Giờ mua nước uống", [], ["P1"], {
        "need": "Mua thịt", "searches": [{"query": "thịt bò"}],
    })
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert context["candidates"] == []
    assert context["previous_products"] == []


@pytest.mark.asyncio
async def test_missing_clarification_text_still_keeps_question_policy_and_no_candidates():
    model = RecordedReplies(
        {"need": "Chưa xác định loại hàng", "action": "ask", "reuse_previous": True},
        {"question": "Bạn đang cần mua loại đồ dùng nào?"},
        {"acceptable": True, "issues": []},
    )
    repo = Repository()
    result = await consult(model, repo, "Cần cái khác cơ", [], ["P1"], {
        "need": "Mua thịt", "searches": [{"query": "thịt bò"}],
    })
    assert result.products == ()
    assert repo.calls == []
    assert "ONE short question" in " ".join(model.calls[1][0][0]["content"].split())


@pytest.mark.asyncio
async def test_missing_repair_action_does_not_authorize_reusing_a_previous_choice():
    model = RecordedReplies(
        {"need": "Chọn quà", "action": "ask", "clarification": "Mua cho mấy người?"},
        {"acceptable": False, "issues": ["Cần hỏi sở thích trước."]},
        {"question": "Người nhận thích đồ ăn hay đồ dùng hơn?"},
        {"acceptable": True, "issues": []},
    )
    repo = Repository()
    result = await consult(model, repo, "Tư vấn quà giúp mình", [], ["P1"], {
        "need": "Chọn quà", "searches": [{"query": "thịt bò"}],
    })
    assert result.plan.action == "ask"
    assert result.products == ()
    assert repo.calls == []


@pytest.mark.asyncio
async def test_old_topic_size_quote_cannot_authorize_new_topic_package_filter():
    model = RecordedReplies(
        {"need": "Mua thịt", "topic_changed": True,
         "searches": [{"query": "thịt bò", "package_constraints": [
             {"dimension": "volume_ml", "value": 1000, "source_quote": "chai 1 lít"}]}]},
        {"choices": [{"product_id": "P1", "reason": "Thái mỏng rồi nấu chín.",
                      "evidence_fields": ["name"]}]},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Giờ mua thịt", [
        {"role": "user", "content": "Mua chai 1 lít"}], [], {
        "need": "Mua nước", "searches": [{"query": "nước"}],
    })
    assert result.plan.searches[0].package_constraints == []
    assert result.products


@pytest.mark.asyncio
async def test_recommendation_cardinality_is_in_generation_schema_before_first_draft():
    model = RecordedReplies(
        {"need": "Chọn một loại thịt", "decision_mode": "recommend",
         "searches": [{"query": "thịt bò"}]},
        {"choices": [{"product_id": "P1", "reason": "Thái mỏng rồi nấu chín.",
                      "evidence_fields": ["name"]}]},
        {"acceptable": True, "issues": []},
    )
    await consult(model, Repository(), "Chọn một loại thịt giúp mình", [], [])
    system = model.calls[1][0][0]["content"]
    schema = json.loads(system.split("Return only a JSON object following this schema: ", 1)[1])
    assert schema["properties"]["choices"]["maxItems"] == 1
    assert schema["$defs"]["Choice"]["properties"]["product_id"]["enum"] == ["P1"]


@pytest.mark.asyncio
async def test_no_match_render_failure_recovers_to_useful_guidance_instead_of_502():
    model = RecordedReplies(
        {"need": "Trà không đường", "searches": [{"query": "trà", "required_phrases": ["không đường"]}]},
        {"advice": "Mình không thấy trong catalog, hãy đổi tiêu chí."},
        {"advice": "Bạn nên xem nhãn để xác nhận không đường; nếu cần uống ngay, chọn chai nhỏ dễ mang theo.",
         "choices": []},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Tìm trà không đường", [], [])
    assert result.products == ()
    assert "xem nhãn" in result.text
    assert "catalog" not in result.text.casefold()


@pytest.mark.asyncio
async def test_open_gift_request_forces_preference_question_when_small_model_skips_search():
    model = RecordedReplies(
        {"need": "Quà cho mẹ", "decision_mode": "recommend", "budget_vnd": 300000},
        {"question": "Mẹ thích đồ ăn, đồ uống hay đồ dùng trong nhà hơn?"},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Mình muốn mua quà cho mẹ khoảng 300 nghìn", [], [])
    assert result.plan.action == "ask"
    assert result.products == ()
    assert "thích" in result.text
    assert len(model.calls) == 3


def test_pantry_followup_stays_in_consultation_context():
    assert use_consultation(
        "Nhà có dầu ăn và gia vị, không tính các món đó.",
        ("price_vnd",),
    )


def test_fast_query_uses_catalog_taxonomy_and_refuses_broad_tokens():
    class CatalogRepo(Repository):
        def search(self, **kwargs):
            query = normalize_vietnamese(kwargs.get("query") or "")
            return [self.product] if "sua rua mat" in query else []

    repo = CatalogRepo()
    repo.product = repo.product.model_copy(update={
        "name": "Sữa rửa mặt 100g",
        "category_hierarchy": "Chăm sóc da > Sữa rửa mặt nữ",
    })
    assert _infer_catalog_query(repo, "Tư vấn sữa rửa mặt giúp mình") == "Sữa rửa mặt"
    assert _infer_catalog_query(repo, "Các loại thịt ăn lẩu") is None


def test_broad_drink_question_cannot_ask_quantity_before_identifying_choice():
    with pytest.raises(GroqProviderError, match="xác định loại hàng"):
        _render_structured(
            Composition(question="Bạn muốn mua bao nhiêu lượng nước cho 2 người?"), {},
            Plan(need="Đồ uống", action="ask", searches=[Search(query="nước")],
                 source_user_text="Chuyển sang mua đồ uống"), "test",
        )


def test_no_advertising_followup_cannot_turn_into_an_unneeded_ingredient_question():
    with pytest.raises(GroqProviderError, match="một điểm cân nhắc"):
        _render_structured(
            Composition(question="Bạn có ưu tiên tránh silicone không?"), {},
            Plan(need="Dầu gội tóc dầu", action="ask", searches=[Search(query="dầu gội")],
                 source_user_text="Không cần quảng cáo công dụng, nói một điểm cần cân nhắc thôi."), "test",
        )


@pytest.mark.parametrize("currency", ["VNĐ", "vnđ", "VND", "đ", "₫"])
def test_repeated_price_in_reason_is_rejected_for_vietnamese_currency_forms(currency):
    product = Repository().product
    with pytest.raises(GroqProviderError, match="Bỏ câu đọc lại giá"):
        _render_structured(Composition(choices=[{
            "product_id": "P1", "reason": f"Dễ thái mỏng, giá 50.000 {currency} vừa túi tiền.",
            "evidence_fields": ["name"],
        }]), {"P1": product}, Plan(need="Chọn thịt"), "fixture")

