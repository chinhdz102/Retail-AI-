import json

import pytest

from backend.services.consultation import Composition, Plan, _render_structured, _review_advice, consult
from backend.services.groq import GroqChatResult, GroqProviderError
from tests.test_consultation import Repository


class RecordedReplies:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []

    async def chat(self, messages, **options):
        self.calls.append((messages, options))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return GroqChatResult(json.dumps(reply, ensure_ascii=False), "actual-model")


PLAN = {"need": "Chọn thịt ăn lẩu, không lấy bánh bao.",
        "searches": [{"query": "thịt bò"}]}
BAD_DRAFT = {"choices": [{"product_id": "P1", "reason": "Dễ cắt thành lát mỏng.",
                           "evidence": ["Thịt bò"]}]}
GOOD_DRAFT = {"choices": [{"product_id": "P1", "reason": "Vo viên nhỏ rồi nấu chín trong nước dùng.",
                            "evidence": ["Thịt bò"]}]}


@pytest.mark.asyncio
async def test_semantic_feedback_repairs_impossible_usage_before_sending_customer_answer():
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Thịt bò xay 200g"})
    model = RecordedReplies(PLAN, BAD_DRAFT,
        {"acceptable": False, "issues": ["Thịt xay không thể cắt thành lát nguyên miếng."]},
        GOOD_DRAFT, {"acceptable": True, "issues": []})
    result = await consult(model, repo, "Chọn giúp t", [
        {"role": "user", "content": "Ăn lẩu, không lấy bánh bao."}], [])
    assert "Vo viên nhỏ" in result.text and "Dễ cắt" not in result.text
    review = json.loads(model.calls[2][0][-1]["content"])
    assert review["products"][0]["name"] == "Thịt bò xay 200g"
    assert "không lấy bánh bao" in review["history"][0]["content"]
    assert "không thể cắt" in model.calls[3][0][-1]["content"]
    assert len(model.calls) == 5


@pytest.mark.asyncio
async def test_repeated_semantic_failure_never_escapes_as_a_successful_answer():
    model = RecordedReplies(PLAN, BAD_DRAFT,
        {"acceptable": False, "issues": ["Cách dùng không hợp dạng hàng."]},
        BAD_DRAFT, {"acceptable": False, "issues": ["Vẫn sai cách dùng."]})
    with pytest.raises(GroqProviderError, match="Vẫn sai") as raised:
        await consult(model, Repository(), "Chọn giúp", [], [])
    assert raised.value.consultation_plan["need"] == PLAN["need"]


@pytest.mark.asyncio
async def test_useful_general_usage_does_not_require_a_label_instruction():
    model = RecordedReplies(PLAN, GOOD_DRAFT, {"acceptable": True, "issues": []})
    result = await consult(model, Repository(), "Chọn giúp", [], [])
    assert "nấu chín trong nước dùng" in result.text


@pytest.mark.asyncio
async def test_format_repair_does_not_spend_semantic_repair_budget():
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Thịt bò xay 200g"})
    model = RecordedReplies(PLAN,
        {"choices": [{"product_id": "INVENTED", "evidence": ["Thịt"]}]},
        BAD_DRAFT, {"acceptable": False, "issues": ["Thịt xay không cắt lát được."]},
        GOOD_DRAFT, {"acceptable": True, "issues": []})
    result = await consult(model, repo, "Chọn giúp", [], [])
    assert "Vo viên nhỏ" in result.text
    assert len(model.calls) == 6


@pytest.mark.asyncio
async def test_unavailable_reviewer_does_not_release_an_unchecked_draft():
    model = RecordedReplies(PLAN, BAD_DRAFT, GroqProviderError("Provider unavailable"))
    with pytest.raises(GroqProviderError, match="unavailable"):
        await consult(model, Repository(), "Chọn giúp", [], [])
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_two_distinct_format_errors_can_be_repaired_inside_three_draft_limit():
    model = RecordedReplies(PLAN,
        {"choices": [{"product_id": "UNKNOWN", "evidence": ["Thịt bò"]}]},
        {"choices": [{"product_id": "P1", "reason": "Không gây kích ứng.", "evidence": ["Thịt bò"]}]},
        GOOD_DRAFT, {"acceptable": True, "issues": []})
    answer = await consult(model, Repository(), "Chọn giúp", [], [])
    assert "Vo viên nhỏ" in answer.text
    assert len(model.calls) == 5
    assert "Chỉ dùng ID" in model.calls[2][0][-1]["content"]
    assert "chưa có nhãn" in model.calls[3][0][-1]["content"]


@pytest.mark.asyncio
async def test_identical_render_failure_stops_without_spending_a_third_draft():
    invalid = {"choices": [{"product_id": "UNKNOWN", "evidence": ["Thịt bò"]}]}
    model = RecordedReplies(PLAN, invalid, invalid)
    with pytest.raises(GroqProviderError):
        await consult(model, Repository(), "Chọn giúp", [], [])
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_reviewer_receives_verified_purchase_weight_and_cost_for_per_kg_goods():
    product = Repository().product.model_copy(update={
        'name': 'Thịt ba rọi heo (S)', 'uom': 'Kg', 'price_vnd': 199900,
    })
    composition = Composition(choices=[{
        'product_id': 'P1', 'weight_g': 300,
        'reason': 'Thái mỏng tại nhà rồi nấu chín trong nước dùng.',
        'evidence': ['Thịt ba rọi heo'],
    }])
    answer = _render_structured(composition, {'P1': product}, Plan(need='Thịt ăn lẩu'), 'model')
    assert '300g (59.970đ)' in answer.text
    model = RecordedReplies({'acceptable': True, 'issues': []})
    await _review_advice(model, answer, 'Chọn thịt heo ăn lẩu', [])
    payload = json.loads(model.calls[0][0][-1]['content'])
    assert payload['verified_purchase_lines'] == [{
        'product_id': 'P1', 'unit': 'Kg', 'unit_price_vnd': 199900,
        'quantity': 1, 'weight_g': 300, 'line_total_vnd': 59970,
    }]


@pytest.mark.asyncio
async def test_unnecessary_followup_clarification_is_reviewed_and_recovers_known_topic():
    model = RecordedReplies({"need": "thịt bò ăn lẩu", "action": "ask",
        "clarification": "Bạn muốn mua thịt nào?"},
        {"acceptable": False, "issues": ["Khách đã chọn thịt bò, không hỏi lại loại thịt."]},
        GOOD_DRAFT, {"acceptable": True, "issues": []})
    answer = await consult(model, Repository(), "Nhắc lại phương án giúp mình", [
        {"role": "user", "content": "Chọn thịt bò ăn lẩu."}], ["P1"],
        {"need": "thịt bò ăn lẩu", "searches": [{"query": "thịt bò"}]})
    assert answer.products and "Bạn muốn mua thịt nào?" not in answer.text
