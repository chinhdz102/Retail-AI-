from pathlib import Path

import pytest

from backend.config import Settings
from backend.data.catalog_import import analyze_dataset
from backend.products.repository import get_product_repository


def test_github_snapshot_is_used_for_search_and_product_lookup():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert repo.source_name == "thanh_lam_json_snapshot"
    assert len(repo.products) == 3081
    results = repo.search(query="10005885", in_stock=False)
    assert len(results) == 1
    product = results[0]
    assert product.name == "Bánh bao nhân thịt heo trứng cút Thọ Phát gói 250g"
    assert product.price_vnd == 30700
    assert product.shelf_source == "RECOMMENDED_PLANOGRAM"
    assert repo.get_product_snapshot(product.product_id.lower()) == product
    assert all(p.price_vnd <= 20000 for p in repo.search(query="sữa", max_price_vnd=20000))


def test_snapshot_excludes_the_existing_price_quarantine():
    path = Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json"
    _, quarantined, report = analyze_dataset(path)
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert repo.dataset_sha256 == report["dataset_version"]
    assert repo.rows_quarantined == 18
    assert all(repo.get_product_snapshot(row["product_id"]) is None for row in quarantined)


@pytest.mark.parametrize("query", ["OMO", "Hảo Hảo", "Ariel", "Milo", "Lipton"])
def test_snapshot_brand_lookup_does_not_match_inside_unrelated_words(query):
    from backend.products.postgres_repository import normalize_search_text

    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    products = repo.search(query=query, in_stock=False, limit=5)
    assert products
    assert all(
        normalize_search_text(product.brand) == normalize_search_text(query)
        for product in products
    )


@pytest.mark.parametrize(
    "query", [
        "thịt bò", "dầu ăn", "dầu gội", "trà xanh", "bánh bao", "sữa tắm", "nước lau sàn"
    ]
)
def test_snapshot_lookup_preserves_product_words_and_ranks_title_matches(query):
    from backend.products.postgres_repository import normalize_search_text

    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    products = repo.search(query=query, in_stock=False, limit=3)
    assert products
    phrase = normalize_search_text(query)
    assert all(phrase in normalize_search_text(product.name) for product in products)


@pytest.mark.parametrize("category", ["drink", "snack", "dairy", "meal"])
def test_snapshot_understands_the_agent_category_ids(category):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert repo.search(category=category, in_stock=False, limit=3)


def test_snapshot_keeps_brand_price_and_category_filters_together():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    products = repo.search(
        query="nước giặt", brand="OMO", price_operator="LT", price_value_vnd=200_000,
        in_stock=False, limit=20,
    )
    assert products
    assert all(product.brand == "OMO" and product.price_vnd < 200_000 for product in products)
    assert repo.search(query="nước giặt", brand="Ajinomoto", in_stock=False) == []
    assert repo.search(query="!!!", in_stock=False) == []


def test_snapshot_prefers_vietnamese_spelling_when_unaccented_words_collide():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert "Nước lẩu" in repo.search(query="nước lẩu", in_stock=False, limit=1)[0].name
    assert "Nước lau" in repo.search(query="nước lau", in_stock=False, limit=1)[0].name


def test_snapshot_full_product_names_remain_searchable_across_categories():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    for product in repo.products[::100]:
        results = repo.search(query=product.name, in_stock=False, limit=3)
        assert product.product_id in [item.product_id for item in results], product.name


def test_snapshot_index_preserves_reference_search_results_and_tie_order():
    from backend.products.catalog import search_products

    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    queries = {"", "!!!", "giá bao nhiêu", "Hảo Hảo", "Hảo Hảo Hảo", "nước lẩu",
               "nước lau", "nước lau sàn", "nuoc lau san", "Coca-Cola", "价格",
               "sản phẩm không tồn tại abcxyz"}
    queries.update(product.category for product in repo.products)
    for product in repo.products[::100]:
        queries.update(filter(None, (product.name, product.brand, product.product_id,
                                      product.item_no, product.barcode)))
    for query in sorted(queries):
        criteria = {"query": query, "in_stock": False, "limit": 17}
        assert repo.search(**criteria) == search_products(products=repo.products, **criteria), query


@pytest.mark.parametrize("criteria", [
    {"query": "nước giặt", "brand": "OMO", "price_operator": "LT", "price_value_vnd": 200000},
    {"query": "sữa", "max_price_vnd": 20000, "in_stock": True},
    {"query": "trà", "price_operator": "GTE", "price_value_vnd": 20000},
    {"query": "", "category": "drink"},
    {"query": "", "category": "meal"},
    {"query": "dầu gội", "spicy": False},
    {"query": "dầu gội", "brand": "Ajinomoto"},
])
def test_snapshot_index_keeps_all_existing_filters(criteria):
    from backend.products.catalog import search_products

    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    canonical = {**criteria, "limit": 11}
    if canonical.get("category"):
        canonical["category"] = repo.category_aliases.get(canonical["category"], canonical["category"])
    assert repo.search(**criteria, limit=11) == search_products(products=repo.products, **canonical)
