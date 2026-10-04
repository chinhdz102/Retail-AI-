import json

from backend.services.retail_tools import execute_catalog_tool


def test_snapshot_requires_fields_and_returns_provenance_envelope() -> None:
    invalid = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "COKE_ZERO_330"}),
    )
    verified = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "COKE_ZERO_330", "fields": ["price_vnd"]}),
    )

    assert invalid.status == "INVALID_ARGUMENT"
    assert verified.status == "SUCCESS"
    assert verified.requested_fields == ("price_vnd",)
    assert verified.content["product"] == {
        "product_id": "COKE_ZERO_330",
        "name": "Coca-Cola Zero 330ml",
        "price_vnd": 13000,
    }
    envelope = verified.to_envelope()
    assert envelope["tool"] == "get_product_snapshot"
    assert envelope["status"] == "SUCCESS"
    assert envelope["source"] == "demo_product_catalog"
    assert envelope["provenance_id"]
    assert envelope["response_mode"] == "DETERMINISTIC_FACT"
    assert envelope["terminal"] is True
    assert envelope["data"] == verified.content


def test_search_tool_returns_candidate_metadata_without_product_facts() -> None:
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "Coca Zero", "in_stock": False}),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["count"] == 1
    assert observation.content["items"] == [
        {
            "product_id": "COKE_ZERO_330",
            "name": "Coca-Cola Zero 330ml",
            "category": "drink",
            "brand": None,
            "matched_constraints": ["in_stock"],
        }
    ]
    assert "price_vnd" not in observation.content["items"][0]
    assert "stock" not in observation.content["items"][0]
    assert observation.response_mode == "CANDIDATE_LIST"
    assert observation.terminal is False


def test_empty_search_result_is_terminal_deterministic_error() -> None:
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "not-a-real-retail-product-9382"}),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["count"] == 0
    assert observation.response_mode == "DETERMINISTIC_ERROR"
    assert observation.terminal is True


def test_recommendation_tool_enforces_sellable_stock() -> None:
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps({"query": "MILK01"}),
    )

    assert observation.content["count"] == 0
    assert observation.content["items"] == []
    assert observation.content["data_timestamp"]
    assert observation.response_mode == "DETERMINISTIC_RECOMMENDATION"
    assert observation.terminal is True


def test_search_can_still_resolve_an_expired_product() -> None:
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "MILK01", "in_stock": False}),
    )

    assert observation.content["count"] == 1
    assert observation.products[0].product_id == "MILK01"


def test_recommendation_uses_explicit_price_operator_boundaries() -> None:
    below = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "category": "drink",
                "price_constraint": {"operator": "LT", "value_vnd": 7000},
            }
        ),
    )
    at_most = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "category": "drink",
                "price_constraint": {"operator": "LTE", "value_vnd": 7000},
            }
        ),
    )

    assert below.content["count"] == 0
    assert [product.product_id for product in at_most.products] == ["WATER01"]


def test_recommendation_resolves_vietnamese_demo_category() -> None:
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "category": "đồ uống",
                "price_constraint": {"operator": "LTE", "value_vnd": 10000},
            }
        ),
    )

    assert [product.product_id for product in observation.products] == ["WATER01"]


def test_catalog_tool_registry_separates_recommendations_from_lookup() -> None:
    from backend.services.retail_tools import CATALOG_TOOL_DEFINITIONS

    names = [entry["function"]["name"] for entry in CATALOG_TOOL_DEFINITIONS]

    assert names == ["search_products", "recommend_products", "get_product_snapshot"]


def test_expired_product_snapshot_reports_zero_sellable_stock() -> None:
    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "MILK01", "fields": ["stock"]}),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["product"]["stock"] == 0
    assert observation.content["product"]["availability_status"] == "EXPIRED"
    assert observation.content["product"]["expiry_at"] == "2026-09-15"
