"""Adjacent recommendation routes must not append operational diagnostics."""

import pytest

from backend.api.main import (
    _grounded_reply,
    _localized_unranked_candidates,
    _localized_whole_cart_recommendation,
)
from backend.products.catalog import ProductSnapshot
from backend.services.retail_agent import RetailAgentResult
from backend.services.retail_tools import ToolObservation


@pytest.mark.parametrize("language", ["vi", "en", "zh"])
@pytest.mark.parametrize("route", ["combo", "unranked_category", "lowest_price"])
def test_recommendation_routes_keep_prices_and_relevant_limits_without_log_tails(
    language, route,
) -> None:
    product = ProductSnapshot(
        product_id="WORDING-ITEM", name="Sản phẩm để tham khảo", category="Đồ uống",
        price_vnd=25_000, stock=4, stock_is_current=False,
    )
    content = {
        "combo": {
            "recommendation_type": "whole_cart", "total_budget_vnd": 60_000,
            "options": [{
                "label": "A", "total_vnd": 50_000,
                "items": [{"name": product.name, "quantity": 2, "line_total_vnd": 50_000}],
            }],
        },
        "unranked_category": {"browse_mode": "CATEGORY_OVERVIEW", "category_label": "Đồ uống"},
        "lowest_price": {"price_ranked_across_scope": True},
    }[route]
    observation = ToolObservation(
        content=content, products=(product,), tool_name="recommend_products",
    )
    if route == "combo":
        answer = _localized_whole_cart_recommendation(observation, language)
        expected_amount = "50.000" if language == "vi" else "50,000"
    elif route == "unranked_category":
        answer = _localized_unranked_candidates(observation, language, "Loại nào ngon nhất?")
        expected_amount = "25.000" if language == "vi" else "25,000"
        # Unavailable taste ratings answer the actual question and must remain.
        assert {"vi": "ngon nhất", "en": "taste", "zh": "口味"}[language] in answer
    else:
        answer = _grounded_reply(
            RetailAgentResult(
                text="", model="catalog", tools_used=("recommend_products",),
                products=(product,), observations=(observation,),
            ),
            language,
        )
        expected_amount = "25.000" if language == "vi" else "25,000"
    assert product.name in answer
    assert expected_amount in answer
    assert {"vi": "tham khảo", "en": "reference", "zh": "参考"}[language] in answer
    for phrase in (
        "Theo giá catalog", "chưa xác minh tại chi nhánh", "Tồn hiện tại", "tồn thực tế",
        "catalog prices", "verified for this branch", "availability", "current stock",
        "snapshot", "目录价格", "此门店核实", "当前库存", "快照",
    ):
        assert phrase not in answer
