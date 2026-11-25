"""Repair feedback must expose arithmetic without silently editing a purchase."""

import copy
import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import consult_customer
from backend.services.purchase_budget import purchase_budget_context
from backend.services.sessions import InMemorySessionStore


def test_actual_over_budget_draft_gets_line_costs_and_per_role_bounds():
    rows = [
        {"candidate": 0, "price_vnd": 53500, "search_indexes": [0]},
        {"candidate": 1, "price_vnd": 11000, "search_indexes": [1]},
        {"candidate": 2, "price_vnd": 4000, "search_indexes": [1]},
    ]
    picks = [{"candidate": 1, "quantity": 2}, {"candidate": 0, "quantity": 1}]
    original = copy.deepcopy((rows, picks))
    observed = purchase_budget_context(60000, rows, [0, 1], picks)
    assert observed["previous_total_vnd"] == 75500
    assert observed["exceeds_by_vnd"] == 15500
    assert observed["previous_pick_costs"][0]["line_total_vnd"] == 22000
    assert observed["one_unit_per_required_role_lower_bound_vnd"] == 57500
    assert observed["candidate_quantity_bounds"][1]["roles"] == [
        {"search_index": 1, "max_quantity": 0},
    ]
    assert observed["candidate_quantity_bounds"][2]["roles"] == [
        {"search_index": 1, "max_quantity": 1},
    ]
    assert (rows, picks) == original


@pytest.mark.parametrize("budget", [0, 45000, 60000])
def test_quantity_bounds_are_correct_for_roles_optional_extras_and_zero_price(budget):
    rows = [
        {"candidate": 0, "price_vnd": 20000, "search_indexes": [0]},
        {"candidate": 1, "price_vnd": 25000, "search_indexes": [1]},
        {"candidate": 2, "price_vnd": 5000, "search_indexes": [2]},
        {"candidate": 3, "price_vnd": 0, "search_indexes": [0]},
    ]
    observed = purchase_budget_context(budget, rows, [0, 1])
    assert observed["one_unit_per_required_role_lower_bound_vnd"] == 25000
    for row, bounds in zip(rows, observed["candidate_quantity_bounds"], strict=True):
        for bound in bounds["roles"]:
            minimum_others = 25000 if bound["search_index"] == 0 else 0
            if bound["search_index"] == 2:
                minimum_others = 25000
            if bound["max_quantity"]:
                assert row["price_vnd"] * bound["max_quantity"] + minimum_others <= budget
            assert 0 <= bound["max_quantity"] <= 20


def test_absent_roles_and_unknown_picks_never_claim_a_complete_feasible_purchase():
    rows = [{"candidate": 0, "price_vnd": 10000, "search_indexes": [0]}]
    observed = purchase_budget_context(30000, rows, [0, 1],
                                       [{"candidate": 5, "quantity": 1}])
    assert observed["required_roles_without_candidates"] == [1]
    assert observed["one_unit_per_required_role_lower_bound_vnd"] is None
    assert "previous_total_vnd" not in observed
    assert observed["candidate_quantity_bounds"][0]["roles"] == []
    assert purchase_budget_context(None, rows, [0]) == {}


@pytest.mark.asyncio
async def test_repair_can_change_quantities_for_multiple_non_food_purchase_roles():
    rows = [product("pen", "Bút Nova", 20000, "Cửa hàng > Học tập > Bút"),
            product("book", "Vở Nova", 15000, "Cửa hàng > Học tập > Vở")]
    model = Model(
        {"action": "recommend", "basket": True, "total_budget_vnd": 60000,
         "searches": [{"category": "Bút"}, {"category": "Vở"}]},
        {"picks": [{"candidate": 0, "quantity": 2, "search_index": 0},
                   {"candidate": 1, "quantity": 2, "search_index": 1}],
         "text": "Mình chọn {name:0} và {name:1}, tổng {total}."},
        {"picks": [{"candidate": 0, "quantity": 1, "search_index": 0},
                   {"candidate": 1, "quantity": 1, "search_index": 1}],
         "text": "Mình chọn {name:0} ({price:0}) để viết và {name:1} ({price:1}) để ghi. "
                 "Tổng {total}."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Chọn bút với vở cho mình, tổng dưới 60 nghìn.",
        repository=Repository(rows), groq=model, record=InMemorySessionStore().create("budget"),
        review_selection=True,
    )
    assert result.products == rows and "35.000đ" in result.text
    context = json.loads(model.calls[2][0][1]["content"].split("\n", 1)[1])
    assert context["purchase_budget"]["previous_total_vnd"] == 70000
    assert context["purchase_budget"]["exceeds_by_vnd"] == 10000
    correction = model.calls[2][0][-1]["content"]
    assert "phải sửa lựa chọn" in correction
    assert "Giữ lựa chọn nếu đúng chức năng" not in correction
