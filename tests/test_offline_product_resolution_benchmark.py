from __future__ import annotations

from pathlib import Path

import pytest

from backend.data.catalog_import import analyze_dataset
from evaluation.offline_product_resolution_benchmark import _prepare_catalog, _rank

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def product_search_index():
    products, _quarantined, report = analyze_dataset(ROOT / "data/winmart_products.json")
    assert report["valid"] is True
    candidates, token_index, identifier_index, title_index = _prepare_catalog(products)
    known_words = frozenset(word for candidate in candidates for word in candidate.words)
    return candidates, token_index, identifier_index, title_index, known_words


def _search(product_search_index, query: str, top_k: int = 5):
    candidates, token_index, identifier_index, title_index, known_words = product_search_index
    return _rank(
        query,
        candidates,
        token_index,
        identifier_index,
        title_index,
        top_k,
        known_words,
    )


def test_comparison_query_retrieves_both_requested_coca_zero_variants(product_search_index):
    results = _search(product_search_index, "Coca Zero 330 với chai 600 khác gì nhau?")

    assert {item["product_id"] for item in results} == {"WIN_10011651", "WIN_10194711"}


def test_small_bottle_query_uses_package_and_capacity(product_search_index):
    results = _search(product_search_index, "Cho loại Coca không đường chai nhỏ.")

    assert results[0]["product_id"] == "WIN_10194711"
    assert all(item["product_id"] != "WIN_10011651" for item in results)


def test_huggies_size_l_returns_both_pack_variants_without_other_brands(product_search_index):
    results = _search(product_search_index, "Cho bỉm Huggies size L.")

    assert [item["product_id"] for item in results] == ["WIN_10020234", "WIN_10161030"]


def test_front_load_omo_query_rejects_products_missing_the_use_case(product_search_index):
    results = _search(product_search_index, "Tìm Omo cho máy giặt cửa trước.")

    assert [item["product_id"] for item in results[:2]] == ["WIN_10161777", "WIN_10161177"]
    assert all("cửa trước" in item["name"].casefold() for item in results)


def test_cheese_snack_query_returns_snacks_that_match_the_flavor(product_search_index):
    results = _search(product_search_index, "Bim bim vị phô mai có không?")

    expected = {
        "WIN_10015708",
        "WIN_10015713",
        "WIN_10015828",
        "WIN_10195895",
        "WIN_10197481",
        "WIN_10320541",
        "WIN_10623327",
        "WIN_10639588",
    }
    assert len(results) == 5
    assert {item["product_id"] for item in results} <= expected
