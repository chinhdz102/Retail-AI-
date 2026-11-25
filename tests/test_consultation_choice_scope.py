"""Choice count is dialogue state; comparison evidence must survive review compaction."""

import json
from copy import deepcopy

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.consultation_verification import (
    compact_verification_context,
    verification_context,
)
from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _ambiguous_identity_question,
    _preserve_constraints,
    _render_selection,
    _review_context,
    _selection_contract,
    _structured,
    consult_customer,
)
from backend.services.groq import GroqChatResult
from backend.services.sessions import InMemorySessionStore


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["Sổ", "Nước uống", "Dầu gội"])
async def test_single_choice_is_repaired_before_publication_in_multiple_categories(group):
    rows = [product(str(i), f"{group} Nova {i}", path=f"Cửa hàng > Hàng hóa > {group}")
            for i in range(2)]
    model = Model(
        {"action": "recommend", "need": f"chọn một {group}", "max_options": 1,
         "searches": [{"category": group}]},
        {"picks": [{"candidate": 0}, {"candidate": 1}], "text": ""},
        {"picks": [{"candidate": 0}],
         "text": "Mình chọn {name:0} ({price:0}) theo tiền mua ban đầu."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=f"Chọn giúp mình một {group}", record=InMemorySessionStore().create("choice"),
        groq=model, repository=Repository(rows),
    )
    assert result.products == [rows[0]]
    assert rows[1].name not in result.text
    assert result.memory["constraints"]["max_options"] == 1
    assert len(model.calls) == 4


def test_single_choice_does_not_limit_basket_roles_or_create_purchase_quantities():
    rows = [product(str(i), f"Đồ dùng {i}") for i in range(2)]
    selection = GroundedSelection(picks=[{"candidate": 0}, {"candidate": 1}])
    with pytest.raises(ValueError, match="requested number"):
        _render_selection(
            selection, rows, ConsultationDecision(action="recommend", max_options=1), {},
        )
    basket = ConsultationDecision(action="recommend", basket=True, max_options=1,
                                  searches=[{"category": "Đồ dùng"}])
    for pick in selection.picks:
        pick.search_index = 0
    assert _selection_contract(basket)["max_picks"] == 6
    assert _render_selection(selection, rows, basket, {p.product_id: [0] for p in rows})[1] == rows
    assert all(p.quantity == 1 for p in selection.picks)


def test_followup_retains_one_choice_but_explicit_comparison_can_show_two():
    previous = {"constraints": {"max_options": 1, "searches": [{"category": "Sổ"}]}}
    for action, explicit, expected in [("recommend", None, 1), ("compare", None, None),
                                       ("recommend", 2, 2)]:
        plan = ConsultationDecision(action=action, max_options=explicit,
                                    searches=[{"category": "Sổ"}])
        _preserve_constraints(plan, previous, "loại khác", parsed_budget=None, explicit_sizes=set())
        assert plan.max_options == expected


@pytest.mark.asyncio
async def test_native_schema_binds_choice_limit_before_generation():
    class Capture:
        async def structured_chat(self, messages, **kwargs):
            self.schema = kwargs["schema"]
            return GroqChatResult(json.dumps({"picks": [], "text": "Mình sẽ so quy cách trước."}),
                                  "offline")
    model = Capture()
    await _structured(model, GroundedSelection, "selection", {
        "customer": "Chọn một thôi", "selection_contract": {"mode": "alternatives", "max_picks": 1},
        "candidates": [{"candidate": 0}, {"candidate": 1}],
    })
    assert model.schema["properties"]["picks"]["maxItems"] == 1


def test_comparison_witnesses_survive_review_without_pretending_they_were_shown():
    data = {
        "customer": "Mua chai mang đi", "resolved_need": "nước uống", "decision": {},
        "selection_contract": {"mode": "alternatives", "max_picks": 1}, "memory": {},
        "previous_question": "", "general_guidance_sources": [],
        "candidates": [
            {"candidate": 0, "name": "Nước Nova 1,5 lít", "type": "Nước", "uom": "Chai",
             "price_vnd": 10000, "search_indexes": [0],
             "name_ref": "{name:0}", "price_ref": "{price:0}"},
            {"candidate": 1, "name": "Nước khác 450ml", "type": "Nước", "uom": "Chai",
             "price_vnd": 5500, "search_indexes": [0],
             "name_ref": "{name:1}", "price_ref": "{price:1}"},
        ],
    }
    original = deepcopy(data)
    review = _review_context(data, GroundedSelection(picks=[{"candidate": 0}]),
                             "Chai 1,5 lít gọn nhất và rẻ nhất.")
    context = verification_context({**review, "rendered_text": "Chai 1,5 lít gọn nhất."})
    compact = compact_verification_context(context)
    assert compact["choice_comparisons"][1]["title_measurements"] == [("volume", 450)]
    assert compact["choice_comparisons"][1]["price_vnd"] == 5500
    assert compact["previously_shown_products"] == []
    assert len(compact["selected_products"]) == 1
    assert data == original


def test_compaction_keeps_each_fact_verbatim_once_and_preserves_customer_evidence():
    context = verification_context({
        "customer": "Mình muốn mua dưới 30 nghìn", "recent_customer_turns": ["Không lấy mùi chanh"],
        "rendered_text": "Mình chọn loại này.",
        "selected_products": [{"candidate": 3, "name": "Hàng Nova", "price_vnd": 20000,
                               "type": "Đồ dùng", "uom": "Chai", "quantity": 1}],
        "previously_shown_products": [{"name": "Hàng đã xem", "price_vnd": 25000}],
    })
    original = deepcopy(context)
    compact = compact_verification_context(context)
    assert compact["sources"] == context["sources"]
    assert compact["selected_products"] == [{"candidate": 3, "quantity": 1, "source_prefix": "c3."}]
    assert compact["previously_shown_products"] == [{"source_prefix": "v0."}]
    assert {s["text"] for s in compact["sources"] if s["kind"] == "customer"} == {
        "Mình muốn mua dưới 30 nghìn", "Không lấy mùi chanh",
    }
    assert len(json.dumps(compact)) < len(json.dumps(context))
    assert context == original


@pytest.mark.parametrize("text", [
    "Cần nước rửa chén và túi rác dưới 80 nghìn cả hai. Chọn giúp đồ cần mua thôi.",
    "Mua bút với vở cho hai bé, nhà có đồ dùng rồi.",
    "Mua pin và đèn cho cả hai, đồ để mang đi cắm trại.",
    "Mình mua hai loại đồ để dọn nhà, chọn giúp mình.",
])
def test_two_roles_or_people_and_do_dung_cannot_create_an_imaginary_comparison(text):
    assert _ambiguous_identity_question(text, None) is None


@pytest.mark.parametrize("text", [
    "Hai loại sữa này khác nhau gì?", "Hai cái đó thì sao?",
    "hai loai sua nay khac gi", "2 sản phẩm kia khác nhau thế nào?",
])
def test_real_unknown_pair_references_still_ask_for_product_identity(text):
    assert _ambiguous_identity_question(text, None)


@pytest.mark.parametrize("group", ["Nước", "Bút", "Khăn"])
def test_budget_comparison_for_an_actual_bound_product_is_supported_arithmetic(group):
    row = product(name=f"{group} Nova", price=14000)
    selection = GroundedSelection(picks=[{"candidate": 0}],
                                  text="Mình chọn {name:0} ({price:0}), giá dưới 20 nghìn.")
    text, products = _render_selection(
        selection, [row], ConsultationDecision(action="recommend", max_price_vnd=20000), {},
    )
    assert products == [row] and "14.000đ" in text


@pytest.mark.parametrize("price,claim", [
    (20000, "giá dưới 20 nghìn"), (21000, "giá dưới 20 nghìn"),
    (14000, "giá thị trường trung bình dưới 20 nghìn"), (14000, "giá 20 nghìn"),
])
def test_bound_identity_does_not_approve_false_comparisons_or_invented_market_prices(price, claim):
    row = product(name="Hàng Nova", price=price)
    selection = GroundedSelection(picks=[{"candidate": 0}],
                                  text=f"Mình chọn {{name:0}} ({{price:0}}), {claim}.")
    with pytest.raises(ValueError, match="unbound price"):
        _render_selection(
            selection, [row], ConsultationDecision(action="recommend", max_price_vnd=20000), {},
        )
