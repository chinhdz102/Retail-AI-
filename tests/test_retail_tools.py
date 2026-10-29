import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.products.catalog import ProductSnapshot, search_products
from backend.products.category_taxonomy import normalize_category_text
from backend.products.knowledge import product_knowledge_facts
from backend.products.repository import DemoProductRepository
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.catalog_preferences import excluded_catalog_brands
from backend.services.retail_tools import (
    CATALOG_TOOL_DEFINITIONS,
    _product_is_in_excluded_category,
    _search_alternative_candidates,
    execute_catalog_tool,
)
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


def test_demo_repository_offset_returns_the_next_matching_products() -> None:
    repository = DemoProductRepository()
    first_page = repository.search(query="", category="drink", limit=2, in_stock=False)
    second_page = repository.search(
        query="", category="drink", limit=2, offset=2, in_stock=False
    )

    assert first_page
    assert second_page
    assert not {product.product_id for product in first_page} & {
        product.product_id for product in second_page
    }


def test_alternative_search_pages_past_the_first_twenty_seen_products() -> None:
    class PagedRepository:
        source_name = "paged_test_catalog"

        def __init__(self) -> None:
            self.products = tuple(
                ProductSnapshot(
                    product_id=f"SKU-{index:03}",
                    name=f"Snack item {index:03}",
                    category="snack",
                    price_vnd=1000 + index,
                    stock=1,
                )
                for index in range(45)
            )
            self.offsets: list[int] = []

        def search(self, *, limit: int = 20, offset: int = 0, **_criteria):
            self.offsets.append(offset)
            return list(self.products[offset : offset + min(limit, 20)])

    repository = PagedRepository()
    seen_ids = {product.product_id for product in repository.products[:40]}
    alternatives, exhausted = _search_alternative_candidates(
        repository,
        {"query": "", "category": "snack"},
        repository.products[:5],
        excluded_ids=seen_ids,
        limit=5,
        retain=lambda _product: True,
    )

    assert [product.product_id for product in alternatives] == [
        "SKU-040",
        "SKU-041",
        "SKU-042",
        "SKU-043",
        "SKU-044",
    ]
    assert repository.offsets == [0, 20, 40]
    assert exhausted is False


def test_alternative_search_reports_exhaustion_only_after_the_last_page() -> None:
    class PagedRepository:
        source_name = "paged_test_catalog"

        def __init__(self) -> None:
            self.products = tuple(
                ProductSnapshot(
                    product_id=f"SKU-{index:03}",
                    name=f"Snack item {index:03}",
                    category="snack",
                    price_vnd=1000 + index,
                    stock=1,
                )
                for index in range(45)
            )
            self.offsets: list[int] = []

        def search(self, *, limit: int = 20, offset: int = 0, **_criteria):
            self.offsets.append(offset)
            return list(self.products[offset : offset + min(limit, 20)])

    repository = PagedRepository()
    alternatives, exhausted = _search_alternative_candidates(
        repository,
        {"query": "", "category": "snack"},
        (),
        excluded_ids={product.product_id for product in repository.products},
        limit=5,
        retain=lambda _product: True,
    )

    assert alternatives == ()
    assert repository.offsets == [0, 20, 40]
    assert exhausted is True


def test_recommendation_returns_unseen_products_after_the_first_snapshot_page() -> None:
    class RecordingRepository:
        source_name = "paged_test_catalog"
        supports_current_stock = False

        def __init__(self) -> None:
            self.products = tuple(
                ProductSnapshot(
                    product_id=f"SKU-{index:03}",
                    name=f"Snack item {index:03}",
                    category="snack",
                    category_hierarchy="Food > Snack",
                    price_vnd=1000 + index,
                    stock=1,
                )
                for index in range(45)
            )
            self.offsets: list[int] = []

        def list_category_hierarchies(self):
            return (("Food", "Snack"),)

        def list_brand_names(self, limit: int = 300):
            return ()

        def search(self, **criteria):
            offset = int(criteria.get("offset", 0))
            self.offsets.append(offset)
            limit = min(int(criteria.get("limit", 10)), 20)
            return list(self.products[offset : offset + limit])

    repository = RecordingRepository()
    first_page = repository.products[:20]
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "query": "",
                "category": "snack",
                "alternative_requested": True,
                "exclude_product_ids": [product.product_id for product in first_page],
                "limit": 5,
            },
            ensure_ascii=False,
        ),
        repository=repository,
        current_user_text="Cho mình xem vài loại bim bim khác.",
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert not {
        product.product_id for product in first_page
    } & {product.product_id for product in observation.products}
    assert 20 in repository.offsets


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
        "budget_vnd": None,
        "budget_scope": None,
        "budget_operator": None,
        "quantity": None,
        "max_unit_price_vnd": None,
    }
    assert observation.products
    assert all(product.spicy is False for product in observation.products)


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
@pytest.mark.parametrize(
    ("query", "positive_category", "positive_title_phrase", "excluded_title_phrase"),
    [
        (
            "Mình muốn nước khoáng, không phải nước ngọt có ga nha.",
            "nước khoáng",
            "nước khoáng",
            "nước ngọt",
        ),
        (
            "Mình muốn khăn giấy, không phải khăn ướt nhé.",
            "khăn giấy gói",
            "khăn giấy",
            "khăn ướt",
        ),
        (
            "Mình muốn sữa tươi, không phải sữa hạt nhé.",
            "sữa tươi",
            "sữa tươi",
            "sữa hạt",
        ),
        (
            "Mình muốn mì ăn liền, không phải mì ly nhé.",
            "mì ăn liền",
            "mì",
            "mì ly",
        ),
    ],
)
def test_product_correction_filters_excluded_scope_without_losing_positive_scope(
    tool_name: str,
    query: str,
    positive_category: str,
    positive_title_phrase: str,
    excluded_title_phrase: str,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )

    arguments = {"query": query, "limit": 5 if tool_name == "recommend_products" else 10}
    if tool_name == "recommend_products":
        arguments["preference_text"] = query
    observation = execute_catalog_tool(
        tool_name,
        json.dumps(arguments, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        normalize_category_text(positive_category)
        in normalize_category_text(product.category_hierarchy)
        for product in observation.products
    )
    assert all(
        normalize_category_text(excluded_title_phrase)
        not in normalize_category_text(product.name)
        for product in observation.products
    )
    assert any(
        normalize_category_text(positive_title_phrase)
        in normalize_category_text(product.name)
        for product in observation.products
    )


def test_title_only_exclusion_works_without_a_taxonomy_node() -> None:
    excluded = ProductSnapshot(
        product_id="NOODLE-CUP",
        name="Mì ly hương vị tôm chua cay 65g",
        category="Mì ăn liền",
        category_hierarchy="Thực phẩm > Mì ăn liền",
        price_vnd=5000,
        stock=5,
    )
    allowed = ProductSnapshot(
        product_id="NOODLE-BAG",
        name="Mì gói hương vị tôm chua cay 75g",
        category="Mì ăn liền",
        category_hierarchy="Thực phẩm > Mì ăn liền",
        price_vnd=5000,
        stock=5,
    )

    assert _product_is_in_excluded_category(excluded, (), ("mì ly",))
    assert not _product_is_in_excluded_category(allowed, (), ("mì ly",))


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


def test_full_request_keeps_excluded_brand_and_price_hard_filters() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    groups: dict[str, dict[str, list[ProductSnapshot]]] = {}
    for product in repository.products:
        category = (product.category_hierarchy or product.category or "").split(">")[-1].strip()
        if category and product.brand:
            groups.setdefault(category, {}).setdefault(product.brand, []).append(product)
    selected = next(
        (
            (category, excluded_brand, alternatives)
            for category, brands in sorted(groups.items())
            if len(brands) > 1
            for excluded_brand in sorted(brands)
            if (
                alternatives := [
                    product
                    for brand, products in brands.items()
                    if brand != excluded_brand
                    for product in products
                    if product.price_vnd > 0
                ]
            )
        ),
        None,
    )
    assert selected is not None
    category, excluded_brand, alternatives = selected
    budget = min(product.price_vnd for product in alternatives)
    user_text = f"Cho mình xem {category} dưới {budget} đồng, không lấy hãng {excluded_brand}."
    observation = execute_catalog_tool(
        "search_products",
        json.dumps(
            {
                "query": category,
                "category": category,
                # Simulate a structured extractor incorrectly copying the
                # excluded brand into the positive brand slot.
                "brand": excluded_brand,
                "max_price_vnd": budget,
                "limit": 10,
            },
            ensure_ascii=False,
        ),
        repository=repository,
        current_user_text=user_text,
    )

    assert observation.products
    assert all(product.price_vnd <= budget for product in observation.products)
    assert all(product.brand != excluded_brand for product in observation.products)
    assert all(
        category in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )


def test_coca_cola_zero_with_price_request_keeps_the_named_product_query() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    query = "Cửa hàng có Coca-Cola Zero không? Cho mình biết giá với."
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all("coca" in product.name.casefold() for product in observation.products)
    assert all("zero" in product.name.casefold() for product in observation.products)


def test_catalog_brand_exclusions_work_without_parser_negative_spans() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    groups: dict[str, dict[str, list[ProductSnapshot]]] = {}
    for product in repository.products:
        category = (product.category_hierarchy or product.category or "").split(">")[-1].strip()
        if category and product.brand:
            groups.setdefault(category, {}).setdefault(product.brand, []).append(product)
    selected = next(
        (
            (category, excluded_brand, alternatives)
            for category, brands in sorted(groups.items())
            if len(brands) > 1
            for excluded_brand in sorted(brands)
            if (
                alternatives := [
                    product
                    for brand, products in brands.items()
                    if brand != excluded_brand
                    for product in products
                    if product.price_vnd > 0
                ]
            )
        ),
        None,
    )
    assert selected is not None
    category, excluded_brand, alternatives = selected
    budget = min(product.price_vnd for product in alternatives)
    brands = [product.brand for product in repository.products if product.brand]
    formatted_budget = f"{budget:,}".replace(",", ".")
    exclusion_queries = (
        f"Có {category} nào không phải hãng {excluded_brand}, "
        f"giá không quá {formatted_budget} đồng không?",
        f"Mình muốn mua {category}; loại {excluded_brand} thì bỏ qua, "
        f"ngân sách tối đa {formatted_budget} đồng.",
    )

    for query in exclusion_queries:
        assert normalize_category_text(excluded_brand) in excluded_catalog_brands(query, brands)
        observation = execute_catalog_tool(
            "search_products",
            json.dumps(
                {
                    "query": category,
                    "category": category,
                    "max_price_vnd": budget,
                    "limit": 10,
                },
                ensure_ascii=False,
            ),
            repository=repository,
            current_user_text=query,
        )

        assert observation.status == "SUCCESS"
        assert observation.products
        assert all(product.price_vnd <= budget for product in observation.products)
        assert all(product.brand != excluded_brand for product in observation.products)
        assert all(
            category
            in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
            for product in observation.products
        )


@pytest.mark.parametrize(
    ("query", "category", "excluded_brand", "budget", "exclusive_budget"),
    [
        (
            "Mình cần Nước trà, giá tối đa 22.300 đồng và đừng lấy hãng C2.",
            "Nước trà",
            "C2",
            22_300,
            False,
        ),
        (
            "Cho mình xem Mì ăn liền dưới 84.000đ nhé, tránh thương hiệu KORENO.",
            "Mì ăn liền",
            "KORENO",
            84_000,
            True,
        ),
        (
            "Mình muốn mua Ngũ cốc ăn sáng; loại KELLOGG'S thì bỏ qua, "
            "ngân sách tối đa 103.900 đồng.",
            "Ngũ cốc ăn sáng",
            "KELLOGG'S",
            103_900,
            False,
        ),
    ],
)
def test_post_filter_refill_returns_full_window_without_relaxing_constraints(
    query: str,
    category: str,
    excluded_brand: str,
    budget: int,
    exclusive_budget: bool,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    expected_category = normalize_category_text(category)
    expected_brand = normalize_category_text(excluded_brand)
    eligible = [
        product
        for product in repository.products
        if expected_category
        in {
            normalize_category_text(product.category),
            *(
                normalize_category_text(part)
                for part in (product.category_hierarchy or "").split(">")
            ),
        }
        and normalize_category_text(product.brand) != expected_brand
        and (product.price_vnd < budget if exclusive_budget else product.price_vnd <= budget)
    ]
    assert len(eligible) >= 10

    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 10}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    returned_ids = {product.product_id for product in observation.products}
    eligible_ids = {product.product_id for product in eligible}
    assert observation.status == "SUCCESS"
    assert len(observation.products) == 10
    assert returned_ids <= eligible_ids
    assert all(
        normalize_category_text(item.brand) != expected_brand
        for item in observation.products
    )
    assert all(
        item.price_vnd < budget if exclusive_budget else item.price_vnd <= budget
        for item in observation.products
    )


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


def test_product_knowledge_returns_status_and_provenance_per_attribute() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    product = repository.products[0]
    observation = execute_catalog_tool(
        "get_product_knowledge",
        json.dumps(
            {
                "product_id": product.product_id,
                "attributes": ["price_vnd", "availability", "benefits", "shelf"],
            }
        ),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    facts = {fact["attribute"]: fact for fact in observation.content["facts"]}
    assert facts["price_vnd"]["status"] == "VERIFIED"
    assert facts["price_vnd"]["evidence_scope"] == "SOURCE_RECORD"
    assert facts["price_vnd"]["source"] == repository.source_name
    assert facts["price_vnd"]["source_url"] == product.seo_url
    assert facts["price_vnd"]["observed_at"] is not None
    assert facts["price_vnd"]["verified_at"] is None
    assert "does not establish current store conditions" in facts["price_vnd"]["note"]
    assert facts["price_vnd"]["catalog_version"] == repository.dataset_sha256
    assert facts["availability"]["status"] == "UNKNOWN"
    assert facts["availability"]["evidence_scope"] == "UNKNOWN"
    assert facts["benefits"]["status"] == "UNKNOWN"
    assert facts["shelf"]["status"] == "INFERRED"
    assert facts["shelf"]["evidence_scope"] == "RECOMMENDED_PLANOGRAM"
    assert observation.products == (product,)


@pytest.mark.parametrize(
    "source_url",
    (
        "javascript:alert(1)",
        "https://user:password@catalog.example/item",
        "https://catalog.example/item\nX-Injected: value",
    ),
)
def test_product_knowledge_rejects_unsafe_source_urls(source_url: str) -> None:
    product = ProductSnapshot(
        product_id="URL-TEST",
        name="URL test product",
        category="food",
        price_vnd=10000,
        stock=1,
        seo_url=source_url,
    )

    fact = product_knowledge_facts(product, ["price_vnd"], source="test_catalog")[0]

    assert fact.source_url is None


def test_product_knowledge_scopes_all_direct_fields_as_catalog_record_evidence() -> None:
    timestamp = datetime(2026, 10, 29, 12, tzinfo=UTC)
    product = ProductSnapshot(
        product_id="TEST-123",
        name="Test item",
        brand="Test brand",
        category="food",
        category_hierarchy="Food > Packaged food",
        price_vnd=42000,
        stock=7,
        stock_is_current=False,
        promotion="Recorded promotion",
        expiry_at="2027-01-15",
        ingredients="Recorded ingredients",
        spicy=False,
        shelf="A02-L03",
        shelf_source="RECOMMENDED_PLANOGRAM",
        description="Retailer listing copy",
        data_timestamp=timestamp,
    )
    facts = {
        fact.attribute: fact
        for fact in product_knowledge_facts(
            product,
            [
                "name",
                "brand",
                "category",
                "price_vnd",
                "stock",
                "promotion",
                "expiry_at",
                "ingredients",
                "spicy",
                "availability",
                "shelf",
                "description",
                "benefits",
            ],
            source="snapshot_test",
        )
    }

    for attribute in (
        "name",
        "brand",
        "category",
        "price_vnd",
        "stock",
        "promotion",
        "expiry_at",
        "ingredients",
        "spicy",
    ):
        fact = facts[attribute]
        assert fact.status.value == "VERIFIED"
        assert fact.evidence_scope.value == "SOURCE_RECORD"
        assert fact.observed_at == timestamp
        assert fact.verified_at is None
        assert "source record" in fact.note

    assert facts["availability"].status.value == "UNKNOWN"
    assert facts["shelf"].status.value == "INFERRED"
    assert facts["shelf"].evidence_scope.value == "RECOMMENDED_PLANOGRAM"
    assert facts["description"].status.value == "INFERRED"
    assert facts["description"].evidence_scope.value == "RETAILER_LISTING"
    assert facts["benefits"].status.value == "UNKNOWN"
    assert facts["benefits"].evidence_scope.value == "UNKNOWN"


def test_product_knowledge_current_availability_is_scoped_to_operational_catalog() -> None:
    product = ProductSnapshot(
        product_id="LIVE-123",
        name="Operational test item",
        category="food",
        price_vnd=10000,
        stock=3,
        stock_is_current=True,
    )
    fact = product_knowledge_facts(
        product,
        ["availability"],
        source="operational_test",
    )[0]

    assert fact.status.value == "VERIFIED"
    assert fact.evidence_scope.value == "CURRENT_OPERATIONAL"
    assert fact.value is True
    assert fact.observed_at == product.data_timestamp
    assert fact.verified_at is None


def test_product_knowledge_does_not_upgrade_retailer_copy_to_verified_benefits() -> None:
    observation = execute_catalog_tool(
        "get_product_knowledge",
        json.dumps(
            {
                "product_id": "COKE_ZERO_330",
                "attributes": ["description", "benefits", "ingredients"],
            }
        ),
        repository=DemoProductRepository(),
    )

    assert observation.status == "SUCCESS"
    facts = {fact["attribute"]: fact for fact in observation.content["facts"]}
    assert facts["description"]["status"] != "VERIFIED"
    if facts["description"]["status"] == "INFERRED":
        assert facts["description"]["evidence_scope"] == "RETAILER_LISTING"
    else:
        assert facts["description"]["evidence_scope"] == "UNKNOWN"
    assert facts["benefits"]["status"] == "UNKNOWN"
    assert facts["ingredients"]["status"] == "VERIFIED"
    assert facts["ingredients"]["evidence_scope"] == "SOURCE_RECORD"
    assert facts["ingredients"]["verified_at"] is None


def test_product_knowledge_requires_explicit_physical_verification_for_shelf() -> None:
    repository = DemoProductRepository()
    demo_product = repository.get_product_snapshot("COKE_ZERO_330")
    assert demo_product is not None and demo_product.shelf
    demo_observation = execute_catalog_tool(
        "get_product_knowledge",
        json.dumps({"product_id": demo_product.product_id, "attributes": ["shelf"]}),
        repository=repository,
    )

    demo_fact = demo_observation.content["facts"][0]
    assert demo_fact["status"] == "INFERRED"
    assert demo_fact["evidence_scope"] == "RECOMMENDED_PLANOGRAM"
    assert demo_fact["verified_at"] is None

    physically_verified = demo_product.model_copy(
        update={"shelf_source": "PHYSICALLY_VERIFIED"}
    )
    verified_fact = product_knowledge_facts(
        physically_verified,
        ["shelf"],
        source="test_catalog",
    )[0]
    assert verified_fact.status.value == "VERIFIED"
    assert verified_fact.evidence_scope.value == "PHYSICAL_STORE"
    assert verified_fact.verified_at is None


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
            "subcategory": "drink",
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
        "get_product_knowledge",
        "plan_shopping_mission",
        "list_product_promotions",
        "list_new_catalog_products",
    ]


def test_shopping_mission_schema_separates_customer_quantity_from_estimate() -> None:
    tool = next(
        entry["function"]
        for entry in CATALOG_TOOL_DEFINITIONS
        if entry["function"]["name"] == "plan_shopping_mission"
    )
    need_properties = tool["parameters"]["properties"]["needs"]["items"]["properties"]
    assert need_properties["quantity"]["maximum"] == 200
    assert "quantity_is_explicit" in need_properties


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


def test_snack_flavor_modifier_does_not_route_cheese_snacks_to_dairy() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )

    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "bim bim vi pho mai", "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert len(observation.products) == 5
    assert all("snack" in product.category_hierarchy.casefold() for product in observation.products)
    assert all("pho mai" in normalize_vietnamese(product.name) for product in observation.products)


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
@pytest.mark.parametrize(
    ("query", "expected_category"),
    [
        ("Cho mình xem nhóm Các loại bột khác", "Các loại bột khác"),
        (
            "Mình cần nhóm Các loại bột khác để phục vụ nhu cầu hằng ngày",
            "Các loại bột khác",
        ),
        ("Cho mình xem nhóm Đồ ăn vặt từ thịt", "Đồ ăn vặt từ thịt"),
    ],
)
def test_explicit_catalog_leaf_browse_returns_category_products(
    tool_name: str, query: str, expected_category: str
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )

    observation = execute_catalog_tool(
        tool_name,
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        expected_category
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )


def test_unaccented_category_browse_does_not_match_short_catalog_brand() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )

    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {"query": "khan mat cac", "category": "Khăn mặt", "limit": 5},
            ensure_ascii=False,
        ),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        "Khăn mặt" in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
@pytest.mark.parametrize(
    ("query", "budget_vnd", "quantity", "exclusive"),
    [
        (
            "Mình cần hai chai nước rửa tay, tổng cộng không quá một trăm hai chục nghìn.",
            120_000,
            2,
            False,
        ),
        (
            "Mình cần hai chai nước rửa tay, tổng cộng dưới một trăm hai chục nghìn.",
            120_000,
            2,
            True,
        ),
        (
            "Mình cần hai chai nước rửa tay, dưới sáu chục nghìn mỗi chai.",
            60_000,
            1,
            True,
        ),
    ],
)
def test_natural_language_budget_is_enforced_even_when_tool_args_omit_it(
    tool_name: str,
    query: str,
    budget_vnd: int,
    quantity: int,
    exclusive: bool,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    arguments = {"query": "nước rửa tay", "limit": 10}
    if tool_name == "recommend_products":
        arguments["limit"] = 5

    observation = execute_catalog_tool(
        tool_name,
        json.dumps(arguments, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        product.price_vnd * quantity < budget_vnd
        if exclusive and quantity > 1
        else product.price_vnd < budget_vnd
        if exclusive
        else product.price_vnd * quantity <= budget_vnd
        for product in observation.products
    )
    assert observation.content[
        "constraints" if tool_name == "recommend_products" else "budget_constraint"
    ]


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
@pytest.mark.parametrize(
    "query",
    [
        "Mình cần hai chai nước rửa tay, dưới sáu chục nghìn.",
        "Mình cần nước rửa tay, tổng cộng dưới sáu chục nghìn.",
    ],
)
def test_ambiguous_budget_scope_stops_before_returning_candidates(
    tool_name: str,
    query: str,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    observation = execute_catalog_tool(
        tool_name,
        json.dumps({"query": "nước rửa tay", "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert observation.terminal is True
    assert observation.content["clarification_code"] == "budget_scope"
    assert observation.content["clarification_needed"] is True
    assert observation.products == ()


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
@pytest.mark.parametrize(
    ("query", "expected_category"),
    [
        ("Có kem đánh răng cho bé loại nhỏ không?", "Kem đánh răng trẻ em"),
        ("Rau cải nằm khu rau củ phía nào?", "Rau cải"),
        ("Có quả cà đỏ chín để làm sốt không?", "Cà chua"),
        ("Loại giấy ăn mini để trong túi xách có không?", "Khăn giấy gói"),
        ("Cho mình xem Dầu hướng dương", "Dầu hướng dương"),
    ],
)
def test_natural_product_type_routes_to_exact_catalog_segment(
    tool_name: str,
    query: str,
    expected_category: str,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    observation = execute_catalog_tool(
        tool_name,
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        expected_category
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )


@pytest.mark.parametrize(
    ("opening", "expected_category"),
    [
        ("Mình đang cần Bút, cho mình xem vài loại.", "Bút"),
        ("Mình đang cần Muối, cho mình xem vài loại.", "Muối"),
        ("Mình đang cần Dầu hướng dương, cho mình xem vài loại.", "Dầu hướng dương"),
        ("Mình đang cần Lăn khử mùi Nam, cho mình xem vài loại.", "Lăn khử mùi Nam"),
    ],
)
def test_elliptical_price_followup_keeps_exact_catalog_leaf(
    opening: str, expected_category: str
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    opening_observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": opening, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=opening,
    )
    follow_up = "Trong mấy loại đó, loại nào rẻ nhất?"
    follow_up_observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": follow_up, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=follow_up,
        previous_query=opening,
        previous_queries=(opening,),
    )

    for observation in (opening_observation, follow_up_observation):
        assert observation.status == "SUCCESS"
        assert observation.products
        assert all(
            expected_category
            in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
            for product in observation.products
        )
    assert [product.price_vnd for product in follow_up_observation.products] == sorted(
        product.price_vnd for product in follow_up_observation.products
    )


def test_ambiguous_product_family_stays_with_title_backed_products() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "query": "Tóc mình nhanh bết, dầu gội nào dùng được?",
                "limit": 5,
            },
            ensure_ascii=False,
        ),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all("dầu gội" in product.name.casefold() for product in observation.products)
    assert observation.content["family_details_unverified"] is True


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
def test_package_disjunction_does_not_require_both_package_forms(tool_name: str) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    observation = execute_catalog_tool(
        tool_name,
        json.dumps(
            {"query": "Nước ngọt có ga chai hoặc lon", "limit": 5},
            ensure_ascii=False,
        ),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        "Nước ngọt có ga"
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )


@pytest.mark.parametrize("tool_name", ["search_products", "recommend_products"])
def test_explicitly_excluded_sibling_category_is_enforced_from_taxonomy(
    tool_name: str,
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    query = "Mình cần tã dán chứ không lấy tã quần."
    observation = execute_catalog_tool(
        tool_name,
        json.dumps(
            {"query": query, "limit": 10 if tool_name == "search_products" else 5},
            ensure_ascii=False,
        ),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        "Tã dán trẻ em"
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )
    assert all(
        "Tã quần trẻ em" not in (product.category_hierarchy or "")
        for product in observation.products
    )


@pytest.mark.parametrize(
    ("query", "expected_category"),
    [
        (
            "Nước giặt với bột giặt khác nhau gì? Trước mắt cho mình xem nước giặt.",
            "Nước giặt",
        ),
        (
            "Gạo tẻ với gạo nếp khác nhau thế nào? Mình đang cần gạo tẻ.",
            "Gạo tẻ",
        ),
        (
            "Sữa thanh trùng với tiệt trùng khác gì? Giới thiệu nhóm tiệt trùng trước.",
            "Sữa tươi tiệt trùng",
        ),
    ],
)
def test_search_returns_only_the_explicitly_selected_comparison_category(
    query: str, expected_category: str
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        expected_category
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )


@pytest.mark.parametrize(
    ("query", "expected_category"),
    [
        ("Mình có 30 nghìn, chọn giúp mấy gói mì ăn liền trong tầm này.", "Mì ăn liền"),
        ("Có bịch giấy cuộn nào to, dùng được lâu không shop?", "Giấy vệ sinh"),
        ("Có lon nước ngọt loại không đường không?", "Nước ngọt có ga"),
        ("Mình cần trứng gà, không tìm trứng vịt.", "Trứng gà"),
    ],
)
def test_retrieval_honors_budget_vocative_variant_and_leaf_scope(
    query: str, expected_category: str
) -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        expected_category
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )
    if "30 nghìn" in query:
        assert all(product.price_vnd <= 30_000 for product in observation.products)
    if "không đường" in query:
        assert all(
            "zero" in normalize_vietnamese(product.name)
            or "khong duong" in normalize_vietnamese(product.name)
            for product in observation.products
        )
    if "trứng vịt" in query:
        assert all("trứng gà" in product.name.casefold() for product in observation.products)


def test_household_child_mention_keeps_generic_products_but_marks_suitability_unverified() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    query = "Tư vấn cho mình nước rửa chén dễ tráng, nhà có em bé."
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert any(
        "child suitability" in value
        for value in observation.content["query_understanding"]["unverified_constraints"]
    )


def test_child_product_request_shows_unverified_candidates_with_a_label_caveat() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    query = "Sữa chua ăn loại hộp nhỏ cho bé"
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert any(
        "child suitability" in value
        for value in observation.content["query_understanding"]["unverified_constraints"]
    )


def test_adult_product_request_allows_generic_items_but_marks_age_unverified() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    query = "Đi biển cuối tuần, tìm kem chống nắng cho người lớn giúp mình."
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all("trẻ em" not in product.name.casefold() for product in observation.products)
    assert any(
        "child suitability" in value
        for value in observation.content["query_understanding"]["unverified_constraints"]
    )


def test_household_child_context_does_not_hide_yogurt_but_does_not_claim_age_fit() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    query = "Nhà có trẻ nhỏ thích ăn sữa chua, tư vấn loại phù hợp theo thông tin sản phẩm."
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        "Sữa chua ăn"
        in [segment.strip() for segment in (product.category_hierarchy or "").split(">")]
        for product in observation.products
    )
    assert any(
        "child suitability" in value
        for value in observation.content["query_understanding"]["unverified_constraints"]
    )
