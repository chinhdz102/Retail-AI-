"""Concrete choices, actual eligible coverage and budget evidence stay distinct."""

import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _concrete_purchase_request,
    _render_selection,
    consult_customer,
)
from backend.services.sessions import InMemorySessionStore


@pytest.mark.parametrize("text", [
    "Chọn giúp một cuốn thôi và nói vì sao.",
    "Mua nước mang đi làm khoảng 20 nghìn.",
    "Mình muốn mua đồ để dọn nhà, chọn giúp mình.",
    "Chọn cho mình một chiếc chậu.",
])
def test_explicit_purchase_or_choice_is_a_concrete_decision(text):
    assert _concrete_purchase_request(text)


@pytest.mark.parametrize("text", [
    "Chỉ mình cách chọn sổ.",
    "Nhà có mèo, chọn nước lau sàn thế nào, không cần giới thiệu hãng vội.",
    "Mình muốn biết cách dùng loại này trước khi mua.",
    "Chưa cần chốt hãng, nói cách chọn trước.",
])
def test_how_to_and_deferred_purchases_do_not_force_a_sku(text):
    assert not _concrete_purchase_request(text)


@pytest.mark.parametrize("claim", [
    "Cả hai loại thường nằm trong khoảng 20 nghìn.",
    "Các loại này giá trung bình dưới 20 nghìn.",
    "Loại này giá dưới 20 nghìn nên hợp với bạn.",
])
def test_the_customers_ceiling_does_not_prove_an_unspecified_product_price(claim):
    with pytest.raises(ValueError, match="budget is not evidence"):
        _render_selection(
            GroundedSelection(text=claim), [],
            ConsultationDecision(action="advise", max_price_vnd=20_000), {},
        )


@pytest.mark.parametrize("text", [
    "Bạn ưu tiên quy cách nhỏ trong ngân sách 20 nghìn để mang theo.",
    "Mình sẽ tìm trong giới hạn giá 20 nghìn bạn dành ra.",
    "Bạn nên chọn đúng chức năng rồi so các lựa chọn dưới 20 nghìn.",
])
def test_a_selection_criterion_can_still_restate_the_actual_budget(text):
    answer, _ = _render_selection(
        GroundedSelection(text=text), [],
        ConsultationDecision(action="advise", max_price_vnd=20_000), {},
    )
    assert answer


@pytest.mark.asyncio
async def test_advice_action_cannot_drop_an_explicit_request_to_choose_an_item():
    row = product(name="Chậu Nova 5L", path="Nhà cửa > Đồ dùng > Chậu")
    model = Model(
        {"action": "advise", "need": "Mua chậu để ngâm đồ", "searches": [{"category": "Chậu"}],
         "guidance": "Bạn nên so chỗ đặt trước rồi chọn quy cách vừa chỗ đó."},
        {"picks": [{"candidate": 0}],
         "text": "Mình ưu tiên {name:0} ({price:0}) để ngâm riêng đồ, rồi so với chỗ đặt."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Mua chậu để ngâm đồ, chọn giúp mình", repository=Repository([row]), groq=model,
        record=InMemorySessionStore().create("purchase-intent"),
    )
    assert result.products == [row]
    assert result.memory["last_action"] == "recommend"
    assert len(model.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("category,alternative", [
    ("Nước ngọt có ga", "Nước khoáng"),
    ("Đèn bàn", "Đèn kẹp"),
    ("Sổ lò xo", "Vở"),
])
async def test_an_excluded_category_cannot_hide_other_choices_from_the_planner(
    category, alternative,
):
    rows = [product(pid="excluded", name=category, path=f"Cửa hàng > Nhóm > {category}"),
            product(pid="alternative", name=alternative, path=f"Cửa hàng > Nhóm > {alternative}")]
    rows.extend(product(pid=str(i), name=f"Hàng mới {i}", path=f"Cửa hàng > Ngành khác > Hàng {i}")
                for i in range(20))
    model = Model(
        {"action": "recommend", "searches": [{"category": alternative}]},
        {"picks": [{"candidate": 0}],
         "text": "Mình ưu tiên {name:0} ({price:0}) cho hướng chọn này."},
        {"acceptable": True},
    )
    await consult_customer(
        text=f"Tư vấn cho mình nhưng không {category}", groq=model,
        repository=Repository(rows), record=InMemorySessionStore().create("negative-scope"),
    )
    context = json.loads(model.calls[0][0][1]["content"].split("\n", 1)[1])
    assert context["identified_catalog_category"] is None
    assert context["catalog_scope"]["focused"] is False
    assert alternative in [name for leaves in context["catalog_groups"].values() for name in leaves]


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded,first,second,bad_title,good_title", [
    ("lò xo", "Sổ", "Vở", "Sổ lò co A6", "Vở gáy dán A6"),
    ("túi châm", "Nước rửa tay", "Xà phòng", "Túi refill nước rửa tay", "Xà phòng bánh"),
    ("xay", "Nguyên liệu", "Thực phẩm", "Thịt xay", "Thịt nguyên miếng"),
])
async def test_retry_category_coverage_respects_exclusions_before_replanning(
    excluded, first, second, bad_title, good_title,
):
    bad = product(pid="bad", name=bad_title, path=f"Cửa hàng > Nhóm > {first}")
    good = product(pid="good", name=good_title, path=f"Cửa hàng > Nhóm > {second}")
    model = Model(
        {"action": "recommend", "searches": [{"category": first, "excluded": [excluded]}]},
        {"action": "recommend", "searches": [{"category": second}]},
        {"picks": [{"candidate": 0}],
         "text": "Mình ưu tiên {name:0} ({price:0}) cho hướng chọn này."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Chọn giúp mình một món phù hợp với việc đang cần", groq=model,
        repository=Repository([bad, good]), record=InMemorySessionStore().create("coverage"),
    )
    repair_context = json.loads(model.calls[1][0][1]["content"].split("\n", 1)[1])
    groups = [name for leaves in repair_context["catalog_groups"].values() for name in leaves]
    assert first not in groups and second in groups
    assert result.products == [good]
    assert excluded in result.memory["constraints"]["searches"][0]["excluded"]
