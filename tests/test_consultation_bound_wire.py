"""Bound schemas and local refusal remain complementary; no provider calls."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.products.catalog import ProductSnapshot
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReview,
    GroundedPick,
    GroundedSelection,
    _render_selection,
    _review_issue,
    _structured,
)
from backend.services.groq import GroqChatResult


class NativeCapture:
    supports_local_schema_recovery = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def structured_chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        return GroqChatResult(json.dumps(self.payload), "offline-capture")


class LegacyCapture:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        return GroqChatResult(
            "",
            "offline-capture",
            tool_calls=[
                {
                    "function": {
                        "name": kwargs["tool_choice"]["function"]["name"],
                        "arguments": json.dumps(self.payload),
                    }
                }
            ],
        )


def captured_schema(model):
    call = model.calls[-1]
    return call["schema"] if "schema" in call else call["tools"][0]["function"]["parameters"]


def product(index):
    return ProductSnapshot(
        product_id=f"notebook-{index}",
        name=f"Sổ A5 mẫu {index}",
        category="Sổ",
        category_hierarchy="Nhà cửa > Văn phòng phẩm > Sổ",
        price_vnd=20_000 + index,
        stock=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [NativeCapture, LegacyCapture])
@pytest.mark.parametrize(
    "mode,max_picks,max_quantity",
    [
        ("alternatives", 2, 1),
        ("basket", 6, 20),
    ],
)
async def test_selection_wire_binds_actual_integer_ids_and_purchase_mode(
    provider,
    mode,
    max_picks,
    max_quantity,
):
    model = provider(GroundedSelection(text="Mình ưu tiên khổ A5 để dễ mang theo.").model_dump())
    data = {
        "customer": "Chọn sổ giúp mình",
        "selection_contract": {"mode": mode},
        "candidates": [{"candidate": 2}, {"candidate": 7}, {"candidate": True}, {"candidate": "9"}],
    }
    original = deepcopy(data)
    result, _ = await _structured(model, GroundedSelection, "policy", data)
    picks = captured_schema(model)["properties"]["picks"]
    assert picks["items"]["properties"]["candidate"]["enum"] == [2, 7]
    assert picks["maxItems"] == max_picks
    assert picks["items"]["properties"]["quantity"]["maximum"] == max_quantity
    assert "basis" not in picks["items"]["properties"]
    assert "label_quote" not in picks["items"]["properties"]
    assert result.picks == []
    assert data == original


@pytest.mark.asyncio
async def test_explicit_empty_candidate_list_forbids_picks_but_absent_context_does_not():
    for data, expected in [({"candidates": []}, 0), ({}, 6)]:
        model = NativeCapture(
            GroundedSelection(
                text="Mình ưu tiên khổ A5 để dễ mang theo.",
            ).model_dump()
        )
        await _structured(model, GroundedSelection, "policy", {"customer": "Chọn giúp", **data})
        assert captured_schema(model)["properties"]["picks"]["maxItems"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [[], [{"candidate": 4}], [{"candidate": 1}, {"candidate": 4}]])
async def test_review_wire_requires_exact_selected_count_and_selected_ids(selected):
    model = NativeCapture(ConsultationReview(acceptable=True).model_dump())
    await _structured(
        model,
        ConsultationReview,
        "review",
        {
            "customer": "Tư vấn giúp mình",
            "selected_products": selected,
        },
    )
    checks = captured_schema(model)["properties"]["product_checks"]
    assert checks["minItems"] == checks["maxItems"] == len(selected)
    if selected:
        assert checks["items"]["properties"]["candidate"]["enum"] == [
            item["candidate"] for item in selected
        ]
    else:
        assert "enum" not in checks["items"]["properties"]["candidate"]


@pytest.mark.asyncio
async def test_minimal_empty_review_remains_valid_and_native_objects_remain_closed():
    model = NativeCapture({"acceptable": True})
    review, _ = await _structured(
        model,
        ConsultationReview,
        "review",
        {
            "customer": "Tư vấn hướng chọn",
            "selected_products": [],
        },
    )
    assert review.product_checks == [] and review.issue == "" and review.revised_text == ""
    assert _review_issue(review, GroundedSelection(), []) == ""

    def assert_closed(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                assert_closed(child)
        elif isinstance(node, list):
            for child in node:
                assert_closed(child)

    assert_closed(captured_schema(model))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "picks,reason",
    [
        ([{"candidate": 3}], "Unknown candidate"),
        ([{"candidate": 0, "quantity": 2}], "single units"),
        ([{"candidate": 0}, {"candidate": 1}, {"candidate": 2}], "limited to two"),
    ],
)
async def test_untrusted_provider_ignoring_wire_bounds_is_still_refused_locally(picks, reason):
    candidates = [product(index) for index in range(3)]
    model = NativeCapture({"picks": picks, "text": ""})
    selection, _ = await _structured(
        model,
        GroundedSelection,
        "policy",
        {
            "customer": "Chọn một cuốn",
            "selection_contract": {"mode": "alternatives"},
            "candidates": [{"candidate": index} for index in range(3)],
        },
    )
    with pytest.raises(ValueError, match=reason):
        _render_selection(selection, candidates, ConsultationDecision(action="recommend"), {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pick",
    [
        {"candidate": -1},
        {"candidate": 0, "quantity": 21},
        {"candidate": 0, "quantity": 0},
        {"candidate": 0, "invented": "field"},
    ],
)
async def test_provider_output_cannot_bypass_static_pydantic_bounds_or_closed_objects(pick):
    model = NativeCapture({"picks": [pick], "text": ""})
    with pytest.raises(ValidationError):
        await _structured(model, GroundedSelection, "policy", {"customer": "Chọn giúp"})


def test_review_count_and_enum_do_not_replace_local_exactly_once_semantic_check():
    candidates = [product(index) for index in range(3)]
    selection = GroundedSelection(picks=[GroundedPick(candidate=0), GroundedPick(candidate=2)])
    checks = [
        {
            "candidate": index,
            "name_quote": candidates[index].name,
            "actual_type": "Sổ",
            "fits_need": True,
            "claims_supported": True,
        }
        for index in [0, 2]
    ]
    assert (
        _review_issue(
            ConsultationReview(acceptable=True, product_checks=checks), selection, candidates
        )
        == ""
    )
    for invalid in [checks[:1], [checks[0], checks[0]], [checks[0], {**checks[1], "candidate": 1}]]:
        review = ConsultationReview(acceptable=True, product_checks=invalid)
        assert "exactly once" in _review_issue(review, selection, candidates)


@pytest.mark.asyncio
async def test_default_recovery_callback_preserves_strict_numeric_types_and_required_fields():
    model = NativeCapture({"picks": [], "text": ""})
    await _structured(model, GroundedSelection, "policy", {"customer": "Chọn giúp"})
    accepts = model.calls[-1]["local_json_validator"]
    assert accepts('{"text":"","picks":[{"candidate":0}]}')
    assert not accepts('{"text":"","picks":[{"candidate":"0"}]}')
    assert not accepts('{"text":"","picks":[{"quantity":1}]}')
    assert not accepts('{"text":"","picks":[{"candidate":0,"extra":true}]}')
