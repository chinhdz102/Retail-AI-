"""Useful partial baskets must not turn missing products into invented substitutes."""

import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.consultation_text import decode_bound_text
from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _render_selection,
    _structured,
    consult_customer,
)
from backend.services.groq import GroqChatResult
from backend.services.sessions import InMemorySessionStore


@pytest.mark.parametrize("first,missing", [
    ("Bút", "Vở"), ("Đèn pin", "Pin"), ("Nước rửa chén", "Túi rác"),
])
@pytest.mark.asyncio
async def test_no_matching_catalog_family_can_still_help_with_available_part(first, missing):
    row = product(name=f"{first} Nova", price=20000, path=f"Cửa hàng > Đồ dùng > {first}")
    model = Model(
        {"action": "recommend", "basket": True, "total_budget_vnd": 80000,
         "searches": [{"category": first, "role": first}], "unresolved_needs": [missing]},
        {"picks": [{"candidate": 0, "search_index": 0}],
         "text": "Mình chọn {name:0} ({price:0}) để ưu tiên phần cần mua trước. "
                 f"Phần {missing} mình chưa chọn được món phù hợp; tạm tính món này là {{total}}."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=f"Cần {first} với {missing}, cả hai dưới 80 nghìn, chọn giúp mình",
        repository=Repository([row]), groq=model, record=InMemorySessionStore().create("partial"),
    )
    assert result.products == [row]
    assert missing in result.text
    assert "20.000đ" in result.text
    assert result.memory["constraints"]["unresolved_needs"] == [missing]
    review = json.loads(model.calls[-1][0][1]["content"].split("\n", 1)[1])
    assert review["constraints"]["unresolved_needs"] == [missing]


def test_role_with_wrong_candidates_can_be_declared_unfulfilled_without_substituting_them():
    row = product(name="Bút Nova", price=20000)
    plan = ConsultationDecision(action="recommend", basket=True, searches=[
        {"category": "Bút", "role": "viết"}, {"category": "Vở", "role": "ghi chép"},
    ])
    draft = GroundedSelection(
        picks=[{"candidate": 0, "search_index": 0}], unfulfilled_searches=[1],
        text="Mình chọn {name:0} ({price:0}); phần vở chưa chọn được món phù hợp.",
    )
    assert _render_selection(draft, [row], plan, {row.product_id: [0]})[1] == [row]
    draft.unfulfilled_searches = []
    with pytest.raises(ValueError, match="missing a required"):
        _render_selection(draft, [row], plan, {row.product_id: [0]})


@pytest.mark.parametrize("unfulfilled", [[0], [1, 1], [2], [3], [-1]])
def test_partial_declaration_cannot_hide_or_fabricate_roles(unfulfilled):
    row = product()
    plan = ConsultationDecision(action="recommend", basket=True, searches=[
        {"category": "Bút"}, {"category": "Vở"}, {"category": "Tẩy", "optional": True},
    ])
    draft = GroundedSelection(picks=[{"candidate": 0, "search_index": 0}],
                              unfulfilled_searches=unfulfilled)
    with pytest.raises(ValueError, match="task role|unfulfilled roles"):
        _render_selection(draft, [row], plan, {row.product_id: [0]})


@pytest.mark.parametrize("unknown_family,unfulfilled", [(True, []), (False, [1])])
def test_bound_partial_price_is_labelled_as_a_subtotal(unknown_family, unfulfilled):
    wire = {"picks": [{"candidate": 0}], "unfulfilled_searches": unfulfilled,
            "text": [{"candidate": 0, "explanation": "Mình chọn {product} cho phần mua trước."}]}
    result = json.loads(decode_bound_text(json.dumps(wire), "text", [0], basket=True,
                                         unresolved_needs=unknown_family))
    assert "Tạm tính" in result["text"]
    assert "Tổng là" not in result["text"]


@pytest.mark.parametrize("missing,unfulfilled", [
    (["Túi rác"], []), ([], [1]), (["Túi rác"], [1]),
])
def test_typed_partial_basket_never_labels_available_items_as_the_full_purchase(
    missing, unfulfilled,
):
    row = product(name="Nước rửa chén Nova", price=20000)
    plan = ConsultationDecision(
        action="recommend", basket=True, unresolved_needs=missing,
        searches=[{"category": "Nước rửa chén", "role": "rửa chén"}]
        + ([{"category": "Khăn lau", "role": "lau bếp"}] if unfulfilled else []),
    )
    selection = GroundedSelection(
        picks=[{"candidate": 0, "search_index": 0}], unfulfilled_searches=unfulfilled,
    )
    text, products = _render_selection(selection, [row], plan, {row.product_id: [0]})
    assert products == [row]
    assert "Tạm tính các món đã chọn: 20.000đ" in text
    assert "Tổng" not in text
    assert "vẫn cần chọn thêm" in text
    if missing:
        assert "Túi rác" in text
    if unfulfilled:
        assert "lau bếp" in text


@pytest.mark.asyncio
async def test_past_answer_can_be_corrected_after_the_pending_question_was_cleared():
    class Capture:
        async def structured_chat(self, messages, **kwargs):
            answer = kwargs["schema"]["properties"]["clarification_answer"]["properties"]
            assert {"status", "question_id", "slot_key", "source_quote"} <= answer.keys()
            return GroqChatResult(json.dumps({"action": "recommend", "clarification_answer": {
                "status": "answered", "question_id": "q_before", "slot_key": "budget",
                "source_quote": "50 nghìn", "value": "50000",
            }}), "offline")
    decision, _ = await _structured(Capture(), ConsultationDecision, "plan", {
        "customer": "Đổi lại chỉ 50 nghìn thôi", "pending_question": None,
        "memory": {"answered_slots": [{"question_id": "q_before", "slot_key": "budget"}]},
    })
    assert decision.clarification_answer.source_quote == "50 nghìn"


@pytest.mark.asyncio
async def test_native_selection_generates_its_question_once_in_the_pending_slot():
    class Capture:
        supports_bound_consultation = True

        async def structured_chat(self, messages, **kwargs):
            assert "question" not in kwargs["schema"]["properties"]
            assert "question" not in kwargs["schema"]["required"]
            return GroqChatResult(json.dumps({
                "text": [{"candidate": None, "explanation": "Mình ưu tiên so quy cách trước."}],
                "picks": [], "pending_question": {
                    "question": "Bạn đang so với món nào?", "slot_key": "product_reference",
                },
            }), "offline")
    selection, _ = await _structured(Capture(), GroundedSelection, "select", {
        "customer": "Nhỏ hơn thì sao?", "selection_contract": {"mode": "alternatives"},
        "candidates": [],
    })
    assert selection.question == "Bạn đang so với món nào?"
    text, _ = _render_selection(selection, [], ConsultationDecision(action="recommend"), {})
    assert text.count(selection.question) == 1
