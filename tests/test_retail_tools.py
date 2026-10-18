import json
from datetime import UTC, datetime

import pytest

from backend.products.catalog import ProductSnapshot
from backend.services.retail_tools import execute_catalog_tool
from backend.services.vietnamese_intent import normalize_vietnamese


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
                    token
                    in normalize_vietnamese(f"{product.name} {product.category}")
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
