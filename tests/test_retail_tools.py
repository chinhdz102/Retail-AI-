import json
from datetime import UTC, datetime

import pytest

from backend.products.catalog import ProductSnapshot, search_products
from backend.products.repository import DemoProductRepository
from backend.services.retail_tools import execute_catalog_tool
from backend.services.vietnamese_intent import normalize_vietnamese


def test_demo_catalog_allows_one_conservative_typo_with_an_exact_product_anchor() -> None:
    assert [product.product_id for product in search_products(query="Coca Zaro")] == [
        "COKE_ZERO_330"
    ]
    assert [product.product_id for product in search_products(query="Coca Zreo")] == [
        "COKE_ZERO_330"
    ]
    assert search_products(query="Zaro") == []


def test_demo_catalog_matches_packaging_unit_and_normalizes_central_vietnamese() -> None:
    assert [product.product_id for product in search_products(query="nuoc suoi chai 500 ml")] == [
        "WATER01"
    ]
    assert normalize_vietnamese("Ở mô rứa?").strip() == "o dau vay"


def test_recommendation_observation_preserves_applied_hard_constraints() -> None:
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps({"category": "snack", "spicy": False}),
    )

    assert observation.content["constraints"] == {
        "category": "snack",
        "brand": None,
        "spicy": False,
        "price_operator": None,
        "price_value_vnd": None,
    }
    assert observation.products
    assert all(product.spicy is False for product in observation.products)


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


def test_snapshot_can_verify_spice_attribute() -> None:
    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "NOODLE01", "fields": ["spicy"]}),
    )

    assert observation.status == "SUCCESS"
    assert observation.requested_fields == ("spicy",)
    assert observation.content["product"]["spicy"] is True


def test_snapshot_timestamp_is_json_serializable_for_agent_tool_messages() -> None:
    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "COKE_ZERO_330", "fields": ["data_timestamp"]}),
    )

    encoded = json.dumps(observation.to_envelope(), ensure_ascii=False)
    decoded = json.loads(encoded)

    assert decoded["data"]["product"]["data_timestamp"] == "2026-08-31T00:00:00+00:00"


def test_promotion_list_returns_only_catalog_records_with_snapshot_provenance() -> None:
    class ActivePromotionRepository:
        source_name = "active_promotion_test_catalog"

        def list_promotions(self, *, category: str | None, limit: int):
            assert category is None
            return (
                ProductSnapshot(
                    product_id="PROMO01",
                    name="Coca-Cola Zero 330ml",
                    category="drink",
                    price_vnd=13000,
                    stock=16,
                    promotion="Giảm 2.000đ",
                ),
            )[:limit]

    observation = execute_catalog_tool(
        "list_product_promotions",
        json.dumps({"limit": 5}),
        repository=ActivePromotionRepository(),
    )

    assert observation.status == "SUCCESS"
    assert observation.tool_name == "list_product_promotions"
    assert observation.requested_fields == ("promotion",)
    assert observation.content["snapshot_only"] is True
    assert observation.content["campaign_validity_verified"] is False
    assert observation.content["count"] == 1
    assert observation.content["items"][0] == {
        "product_id": "PROMO01",
        "name": "Coca-Cola Zero 330ml",
        "promotion": "Giảm 2.000đ",
        "data_timestamp": observation.products[0].data_timestamp.isoformat(),
    }


def test_promotion_list_excludes_expired_catalog_products() -> None:
    observation = execute_catalog_tool(
        "list_product_promotions",
        json.dumps({"limit": 5}),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["count"] == 0
    assert observation.content["items"] == []


def test_promotion_list_rejects_unsupported_repository_instead_of_guessing() -> None:
    class SnapshotOnlyRepository:
        source_name = "snapshot_only_test_catalog"

        def get_product_snapshot(self, product_id: str):
            return None

    observation = execute_catalog_tool(
        "list_product_promotions",
        json.dumps({"limit": 5}),
        repository=SnapshotOnlyRepository(),
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert observation.content["error"]


def test_new_catalog_products_requires_two_imports() -> None:
    observation = execute_catalog_tool(
        "list_new_catalog_products",
        json.dumps({"limit": 5}),
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert "two complete" in observation.content["error"]
    assert observation.content["previous_imported_at"] is None


def test_snapshot_returns_verified_fields_when_another_requested_field_is_missing() -> None:
    class PartialRepository:
        source_name = "partial_test_catalog"

        def get_product_snapshot(self, product_id: str) -> ProductSnapshot:
            return ProductSnapshot(
                product_id=product_id,
                name="Nước cam Teppy 1L",
                category="drink",
                price_vnd=24_700,
                stock=4,
                ingredients=None,
                data_timestamp=datetime(2026, 9, 15, tzinfo=UTC),
            )

    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps(
            {
                "product_id": "TEPPY_1L",
                "fields": ["price_vnd", "ingredients"],
            }
        ),
        repository=PartialRepository(),
    )

    assert observation.status == "SUCCESS"
    assert observation.requested_fields == ("price_vnd",)
    assert observation.content["product"]["price_vnd"] == 24_700
    assert observation.content["unavailable_fields"] == ["ingredients"]


def test_snapshot_abstains_when_only_the_requested_field_is_missing() -> None:
    class PartialRepository:
        source_name = "partial_test_catalog"

        def get_product_snapshot(self, product_id: str) -> ProductSnapshot:
            return ProductSnapshot(
                product_id=product_id,
                name="Nước cam Teppy 1L",
                category="drink",
                price_vnd=24_700,
                stock=4,
                ingredients=None,
            )

    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "TEPPY_1L", "fields": ["ingredients"]}),
        repository=PartialRepository(),
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert observation.requested_fields == ()
    assert observation.content["unavailable_fields"] == ["ingredients"]


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


def test_whole_cart_recommendation_calculates_sellable_combos_under_total_budget() -> None:
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "categories": ["drink", "snack"],
                "total_budget_vnd": 70_000,
                "quantity_per_category": 2,
                "limit": 3,
            }
        ),
        repository=DemoProductRepository(),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["recommendation_type"] == "whole_cart"
    assert observation.content["cart_mutated"] is False
    assert observation.content["missing_categories"] == []
    assert [option["total_vnd"] for option in observation.content["options"]] == [
        50_000,
        68_000,
        62_000,
    ]
    for option in observation.content["options"]:
        assert option["total_vnd"] <= observation.content["total_budget_vnd"]
        assert {item["category_label"] for item in option["items"]} == {
            "đồ uống",
            "đồ ăn vặt",
        }
        assert all(item["quantity"] == 2 for item in option["items"])
        assert sum(item["line_total_vnd"] for item in option["items"]) == option["total_vnd"]

    strict_budget = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "categories": ["drink", "snack"],
                "total_budget_vnd": 50_000,
                "total_budget_exclusive": True,
                "quantity_per_category": 2,
                "limit": 3,
            }
        ),
        repository=DemoProductRepository(),
    )
    assert strict_budget.status == "SUCCESS"
    assert strict_budget.content["options"] == []
    assert strict_budget.content["total_budget_exclusive"] is True


def test_whole_cart_recommendation_fails_closed_when_requested_group_has_no_sellable_item() -> None:
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "categories": ["dairy", "snack"],
                "total_budget_vnd": 100_000,
            }
        ),
        repository=DemoProductRepository(),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["missing_categories"] == ["dairy"]
    assert observation.content["options"] == []
    assert observation.products == ()


def test_whole_cart_recommendation_requires_multiple_categories_and_total_budget() -> None:
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps({"categories": ["drink", "snack"]}),
        repository=DemoProductRepository(),
    )

    assert observation.status == "INVALID_ARGUMENT"


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

    assert names == [
        "search_products",
        "recommend_products",
        "get_product_snapshot",
        "list_product_promotions",
        "list_new_catalog_products",
    ]


def test_expired_product_snapshot_reports_zero_sellable_stock() -> None:
    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": "MILK01", "fields": ["stock"]}),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["product"]["stock"] == 0
    assert observation.content["product"]["availability_status"] == "EXPIRED"
    assert observation.content["product"]["expiry_at"] == "2026-09-15"


@pytest.mark.parametrize(
    ("category", "expected_query"),
    [
        ("drink", "nước uống"),
        ("snack", "bánh snack"),
        ("dairy", "sữa"),
        ("meal", "thực phẩm ăn liền"),
    ],
)
def test_canonical_categories_are_translated_for_winmart_taxonomy(
    category: str, expected_query: str
) -> None:
    class WinMartRepository:
        source_name = "winmart_postgres_catalog"

        def __init__(self) -> None:
            self.criteria = None

        def search(self, **criteria):
            self.criteria = criteria
            return []

        def get_product_snapshot(self, product_id: str):
            return None

    repository = WinMartRepository()
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps({"category": category}),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert repository.criteria["category"] == expected_query


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_query"),
    [
        ("search_products", {"query": "bim bim"}, ""),
        (
            "search_products",
            {"query": "Bim bim Poppedcorn 280g", "category": "Tã - Bỉm"},
            "Bim bim Poppedcorn 280g",
        ),
        ("recommend_products", {"query": "bim bim"}, ""),
    ],
)
def test_bim_bim_queries_are_constrained_to_snacks(
    tool_name: str,
    arguments: dict[str, str],
    expected_query: str,
) -> None:
    class WinMartRepository:
        source_name = "winmart_postgres_catalog"

        def __init__(self) -> None:
            self.criteria = None

        def search(self, **criteria):
            self.criteria = criteria
            return []

        def get_product_snapshot(self, product_id: str):
            return None

    repository = WinMartRepository()
    observation = execute_catalog_tool(
        tool_name,
        json.dumps(arguments, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert repository.criteria["category"] == "bánh snack"
    assert repository.criteria["query"] == expected_query


def test_bim_bim_lookup_cannot_return_diapers_from_a_confusable_category() -> None:
    products = (
        ProductSnapshot(
            product_id="SNACK_BIM",
            name="Bim bim Poppedcorn quẩy soắn 280g",
            category="Bánh Snack",
            price_vnd=57_500,
            stock=8,
        ),
        ProductSnapshot(
            product_id="DIAPER_BIM",
            name="Tã quần Huggies dry pants big jumbo L38",
            category="Tã - Bỉm",
            price_vnd=259_000,
            stock=10,
        ),
    )

    class ConfusableCatalogRepository:
        source_name = "winmart_postgres_catalog"

        def search(self, **criteria):
            query_tokens = normalize_vietnamese(criteria["query"]).split()
            category = normalize_vietnamese(criteria["category"])
            return [
                product
                for product in products
                if all(
                    token in normalize_vietnamese(f"{product.name} {product.category}")
                    for token in query_tokens
                )
                and category in normalize_vietnamese(product.category)
                and (not criteria["in_stock"] or product.sellable_stock > 0)
            ]

        def get_product_snapshot(self, product_id: str):
            return next((item for item in products if item.product_id == product_id), None)

    for tool_name in ("search_products", "recommend_products"):
        observation = execute_catalog_tool(
            tool_name,
            json.dumps(
                {"query": "bim bim", "category": "Tã - Bỉm"},
                ensure_ascii=False,
            ),
            repository=ConfusableCatalogRepository(),
        )

        assert [product.product_id for product in observation.products] == ["SNACK_BIM"]


def test_bim_without_repetition_is_not_misread_as_snack_category() -> None:
    class WinMartRepository:
        source_name = "winmart_postgres_catalog"

        def __init__(self) -> None:
            self.criteria = None

        def search(self, **criteria):
            self.criteria = criteria
            return []

        def get_product_snapshot(self, product_id: str):
            return None

    repository = WinMartRepository()
    execute_catalog_tool(
        "search_products",
        json.dumps({"query": "bỉm"}, ensure_ascii=False),
        repository=repository,
    )

    assert repository.criteria["category"] is None
    assert repository.criteria["query"] == "bỉm"
