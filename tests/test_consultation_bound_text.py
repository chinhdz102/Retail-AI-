import json

import pytest
from test_customer_consultation import product

from backend.services.consultation_text import decode_bound_text
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReview,
    GroundedSelection,
    _decision_context,
    _render_selection,
    _structured,
)
from backend.services.groq import GroqChatResult


class Native:
    supports_bound_consultation = True
    supports_local_schema_recovery = True

    def __init__(self, result):
        self.result = result
        self.kwargs = None

    async def structured_chat(self, messages, **kwargs):
        self.kwargs = kwargs
        return GroqChatResult(json.dumps(self.result), "native-mock")


@pytest.mark.asyncio
async def test_native_fragments_bind_identity_price_and_keep_grounding_checks():
    model = Native(
        {"picks": [{"candidate": 0}], "text": [
            {"candidate": 0, "explanation": "Gói nhỏ dễ mang theo."},
        ]}
    )
    selection, _ = await _structured(
        model,
        GroundedSelection,
        "select",
        {
            "customer": "Chọn một gói nhỏ",
            "candidates": [{"candidate": 0}],
            "selection_contract": {"mode": "alternatives"},
        },
    )
    wire = model.kwargs["schema"]["properties"]["text"]
    assert wire["type"] == "array"
    assert wire["items"]["properties"]["candidate"]["anyOf"][1]["enum"] == [0]
    row = product(name="Bánh Nova gói 50g")
    answer, selected = _render_selection(
        selection,
        [row],
        ConsultationDecision(action="recommend"),
        {},
    )
    assert row.name in answer and "80.000đ" in answer and selected == [row]
    model.result["text"] = [{"candidate": 0, "explanation": "Mở ra ăn ngay."}]
    unsafe, _ = await _structured(
        model,
        GroundedSelection,
        "select",
        {
            "customer": "Chọn giúp",
            "candidates": [{"candidate": 0}],
        },
    )
    with pytest.raises(ValueError, match="immediate-use"):
        _render_selection(unsafe, [row], ConsultationDecision(action="recommend"), {})


@pytest.mark.parametrize("part", [True, 0.0, -1, 9, None, {"candidate": 0}, "{price:999}"])
def test_fragment_decoder_never_coerces_or_fuzzily_repairs_reference(part):
    with pytest.raises(ValueError):
        decode_bound_text(json.dumps({"text": [part]}), "text", [0], basket=False)


def test_integer_reference_cannot_mention_unselected_product():
    decoded = decode_bound_text(
        json.dumps(
            {
                "picks": [{"candidate": 0}],
                "text": [{"candidate": 1, "explanation": "Gói nhỏ."}],
            }
        ),
        "text",
        [0, 1],
        basket=False,
    )
    value = GroundedSelection.model_validate_json(decoded)
    with pytest.raises(ValueError, match="selected product|not selected"):
        _render_selection(
            value, [product(), product()], ConsultationDecision(action="recommend"), {}
        )


def test_product_identity_and_spacing_are_generated_once_outside_prose():
    decoded = json.loads(decode_bound_text(json.dumps({
        "picks": [{"candidate": 0}],
        "text": [{"candidate": 0, "explanation": "Gói nhỏ để mang theo."}],
    }), "text", [0], basket=False))
    assert decoded["text"] == "Mình ưu tiên {name:0} ({price:0}). Gói nhỏ để mang theo."
    with pytest.raises(ValueError, match="reference"):
        decode_bound_text(json.dumps({"text": [
            {"candidate": 0, "explanation": "Gói nhỏ."},
            {"candidate": 0, "explanation": "Giá."},
        ]}), "text", [0], basket=False)


@pytest.mark.parametrize("title,task", [
    ("Sổ A5 80 trang", "ghi chép lúc đi học"),
    ("Hộp chữ nhật 500ml", "đựng đồ mang theo"),
    ("Túi rác cuộn 20 chiếc", "gom rác trong phòng"),
])
def test_model_controls_the_sentence_while_identity_and_price_remain_application_bound(title, task):
    row = product(name=title)
    wire = {
        "picks": [{"candidate": 0}],
        "text": [{"candidate": 0, "explanation": f"Với việc {task}, mình lấy {{product}} trước."}],
    }
    selection = GroundedSelection.model_validate_json(
        decode_bound_text(json.dumps(wire), "text", [0], basket=False)
    )
    answer, products = _render_selection(
        selection, [row], ConsultationDecision(action="recommend"), {}
    )
    assert answer == f"Với việc {task}, mình lấy {title} (80.000đ) trước."
    assert products == [row] and "{product}" not in answer
    assert "Mình ưu tiên" not in answer and answer.count(row.name) == 1


def test_conversational_basket_keeps_app_owned_quantity_and_total():
    payload = {
        "picks": [{"candidate": 1, "quantity": 3}],
        "text": [
            {"candidate": None, "explanation": "Bạn có đồ dùng còn lại rồi."},
            {"candidate": 1, "explanation": "Mình chỉ lấy thêm {product} để ghi chép."},
        ],
    }
    decoded = json.loads(decode_bound_text(json.dumps(payload), "text", [1], basket=True))
    assert decoded["text"] == (
        "Bạn có đồ dùng còn lại rồi. Mình chỉ lấy thêm 3 × {name:1} ({price:1}) "
        "để ghi chép. Tổng là {total}."
    )


@pytest.mark.parametrize("quantity,typed,valid", [(1, 1, True), (3, 3, True), (3, 1, False)])
def test_repeated_wire_quantity_is_deduplicated_only_when_it_matches_the_pick(
    quantity, typed, valid,
):
    raw = json.dumps({"picks": [{"candidate": 0, "quantity": quantity}], "text": [
        {"candidate": 0, "explanation": f"Mình lấy {typed}\u202f×\u202f{{product}} trước."},
    ]})
    if not valid:
        with pytest.raises(ValueError, match="quantity"):
            decode_bound_text(raw, "text", [0], basket=True)
        return
    text = json.loads(decode_bound_text(raw, "text", [0], basket=True))["text"]
    assert text.count("×") == 1 and f"{quantity} × {{name:0}}" in text


@pytest.mark.parametrize("price", ["31\u202fkđ", "31 k VND", "31000 VNĐ", "31k₫", "31kđồng"])
def test_compound_currency_cannot_bypass_the_application_price_binding(price):
    row = product(price=30_800)
    native_text = f"Mình chọn {{name:0}} ({{price:0}}), giá {price}, để bạn mang theo."
    from backend.services.customer_consultation import ConsultationReply, Pick, _render

    with pytest.raises(ValueError, match="unbound price"):
        _render(ConsultationReply(text=native_text, picks=[Pick(candidate=0)]),
                [row], ConsultationDecision(action="recommend"))


@pytest.mark.asyncio
@pytest.mark.parametrize("literal_price", ["28.800\u202fđ", "28800đ", "28,800 VND", "28 800 đồng"])
async def test_exact_echoed_name_and_price_are_rebound_without_a_second_sales_lead(literal_price):
    row = product(name="Hộp Nova 500ml", price=28_800)
    native = Native({"picks": [{"candidate": 0}], "text": [
        {"candidate": 0, "explanation": (
            f"Mình chọn Hộp  Nova 500ml, giá {literal_price}, để bạn đựng đồ mang theo."
        )},
    ]})
    selection, _ = await _structured(native, GroundedSelection, "select", {
        "customer": "Chọn hộp mang đi làm", "selection_contract": {"mode": "alternatives"},
        "candidates": [{"candidate": 0, "name": row.name, "price_vnd": row.price_vnd}],
    })
    answer, rows = _render_selection(
        selection, [row], ConsultationDecision(action="recommend"), {},
    )
    assert rows == [row] and answer.count(row.name) == 1 and answer.count("28.800đ") == 1
    assert answer.startswith("Mình chọn Hộp Nova") and "Mình ưu tiên" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("literal_name,literal_price", [
    ("Hộp Nova 500ml", "29.800đ"),
    ("Hộp Nova 750ml", "28.800đ"),
])
async def test_fact_rebinding_cannot_correct_a_guessed_pack_or_price(literal_name, literal_price):
    row = product(name="Hộp Nova 500ml", price=28_800)
    native = Native({"picks": [{"candidate": 0}], "text": [
        {"candidate": 0, "explanation": f"Mình chọn {literal_name}, giá {literal_price}."},
    ]})
    selection, _ = await _structured(native, GroundedSelection, "select", {
        "customer": "Chọn hộp", "candidates": [
            {"candidate": 0, "name": row.name, "price_vnd": row.price_vnd},
        ],
    })
    with pytest.raises(ValueError, match="unbound price"):
        _render_selection(selection, [row], ConsultationDecision(action="recommend"), {})


@pytest.mark.parametrize("candidate,explanation", [
    (None, "Mình chọn {product}."),
    (0, "Mình chọn {product} thay cho {product}."),
    (0, "Mình chọn {name:0} thay cho {product}."),
])
def test_conversational_marker_cannot_bind_without_identity_or_duplicate_a_reference(
    candidate, explanation,
):
    with pytest.raises(ValueError):
        decode_bound_text(json.dumps({
            "picks": [{"candidate": 0}],
            "text": [{"candidate": candidate, "explanation": explanation}],
        }), "text", [0], basket=False)


@pytest.mark.asyncio
async def test_editor_uses_only_selected_integers_and_default_recovery_checks_decoded_text():
    native = Native({"acceptable": True, "revised_text": []})
    result, _ = await _structured(
        native,
        ConsultationReview,
        "review",
        {
            "customer": "Chọn giúp",
            "selected_products": [{"candidate": 4}],
        },
    )
    assert result.revised_text == ""
    wire = native.kwargs["schema"]["properties"]["revised_text"]["items"]
    assert wire["properties"]["candidate"]["anyOf"][1]["enum"] == [4]
    validator = native.kwargs["local_json_validator"]
    assert validator(json.dumps({"acceptable": True, "revised_text": [
        {"candidate": 4, "explanation": "Gói nhỏ."},
    ]}))
    assert not validator(json.dumps({"acceptable": True, "revised_text": [
        {"candidate": 8, "explanation": "Gói nhỏ."},
    ]}))


@pytest.mark.asyncio
async def test_editor_keeps_application_owned_basket_quantities_in_formatted_blocks():
    native = Native({"acceptable": True, "revised_text": [
        {"candidate": 4, "explanation": "Gói nhỏ."},
    ]})
    review, _ = await _structured(native, ConsultationReview, "review", {
        "customer": "Mua ba gói", "selection_contract": {"mode": "basket"},
        "selected_products": [{"candidate": 4, "quantity": 3}],
    })
    assert "3 × {name:4}" in review.revised_text
    assert "1 ×" not in review.revised_text and "{total}" in review.revised_text


@pytest.mark.asyncio
async def test_planner_binds_actual_leaf_labels_without_department_or_sku_map():
    native = Native({"action": "recommend", "searches": [{"category": 1}]})
    result, _ = await _structured(
        native,
        ConsultationDecision,
        "plan",
        {
            "customer": "Cần đồ cho chuyến đi",
            "catalog_groups": {"Đồ dùng": ["Sổ", "Pin", "Sổ"], "Thực phẩm": ["Nước"]},
        },
    )
    category = native.kwargs["schema"]["properties"]["searches"]["items"]["properties"]["category"]
    assert category["type"] == "integer" and category["maximum"] == 2
    assert result.searches[0].category == "Pin"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], None, {"searches": None}, {"searches": [None]},
                                    {"searches": [{"category": True}]},
                                    {"searches": [{"category": -1}]},
                                    {"searches": [{"category": 1}]}])
async def test_malformed_bound_plans_are_rejected_cleanly_without_coercion(payload):
    native = Native(payload)
    with pytest.raises(ValueError):
        await _structured(native, ConsultationDecision, "plan", {
            "customer": "Chọn giúp", "catalog_groups": {"Đồ dùng": ["Pin"]},
        })
    assert not native.kwargs["local_json_validator"](json.dumps(payload))


def test_functional_roles_survive_context_compaction_without_mutating_memory():
    plan = ConsultationDecision(action="recommend", searches=[{
        "category": "Đèn đọc sách", "role": "chiếu sáng bàn học",
    }])
    assert _decision_context(plan)["searches"][0]["role"] == "chiếu sáng bàn học"
    assert plan.searches[0].role == "chiếu sáng bàn học"
    plan.basket = True
    assert _decision_context(plan)["searches"][0]["role"] == "chiếu sáng bàn học"
