"""Catalog context should describe the requested objects across departments."""

from pathlib import Path

import pytest

from backend.products.catalog import ProductSnapshot
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.customer_consultation import _category_observations, _facet_context


@pytest.fixture(scope="module")
def catalog():
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json",
    )


@pytest.mark.parametrize("query", ["Gối ngủ dưới 200 nghìn", "goi ngu duoi 200 nghin"])
def test_real_pillow_is_visible_in_bounded_observations(catalog, query):
    observations = _category_observations(catalog, catalog.list_category_hierarchies(), query)
    assert observations[0]["category"] == "Gối"
    assert any("Ruột gối" in name for name in observations[0]["example_names"])


def test_explicit_accents_prevent_false_product_observations(catalog):
    observations = _category_observations(
        catalog, catalog.list_category_hierarchies(), "Gối ngủ dưới 200 nghìn",
    )
    labels = {item["category"] for item in observations}
    assert labels == {"Gối", "Thảm phòng ngủ"}


def test_unmatched_purpose_does_not_gain_categories_from_request_glue(catalog):
    query = "Muốn làm bữa tối nhẹ bụng, chọn giúp tôi"
    assert _category_observations(catalog, catalog.list_category_hierarchies(), query) == []


@pytest.mark.parametrize("query,rejected,requested", [
    ("Mua nước mang đi làm, không nước có ga và không cà phê.",
     ["Cà phê nước", "Cà phê hạt", "Cà phê hòa tan", "Nước ngọt có ga"], None),
    ("Tìm sữa tắm, không dầu gội.", ["Dầu gội nữ", "Dầu gội nam"], "Sữa tắm nữ"),
    ("Tìm bút, không sổ và không vở.", ["Sổ", "Vở"], "Bút"),
])
def test_rejected_categories_do_not_gain_positive_discovery_priority(
    catalog, query, rejected, requested,
):
    facets = _facet_context(catalog.list_category_hierarchies(), query).split(" | ")
    assert not set(facets).intersection(rejected)
    if requested:
        assert requested in facets


@pytest.mark.parametrize(
    "category,query,unrelated",
    [
        ("Ổ cắm điện", "Tôi cần ổ cắm điện dưới 200 nghìn", "Ốc đóng hộp"),
        ("Đất trồng cây", "Bạn chọn giúp đất trồng cây", "Đặt bàn"),
        ("Bàn làm việc", "Mình muốn mua bàn làm việc", "Bán cây giống"),
        ("Đèn đọc sách", "Tu van den doc sach", "Đèn sân vườn"),
        ("Vỏ máy tính", "Chọn vỏ máy tính giúp tôi", "Vở học sinh"),
    ],
)
def test_unseen_category_phrases_rank_ahead_of_incidental_matches(category, query, unrelated):
    paths = (
        ("Cửa hàng", "Ngành mới", unrelated),
        ("Cửa hàng", "Ngành mới", category),
        ("Cửa hàng", "Ngành mới", "Túi quà tặng"),
    )
    assert _facet_context(paths, query).split(" | ")[0] == category


def test_ancestor_phrase_still_exposes_its_descendant_categories():
    paths = (
        ("Cửa hàng", "Thiết bị mạng", "Bộ định tuyến"),
        ("Cửa hàng", "Thiết bị mạng", "Bộ chuyển mạch"),
        ("Cửa hàng", "Cây cảnh", "Lan hồ điệp"),
    )
    assert set(_facet_context(paths, "Tư vấn thiết bị mạng").split(" | ")) == {
        "Bộ định tuyến", "Bộ chuyển mạch",
    }


def test_synthetic_product_examples_follow_ranked_identity():
    class Repository:
        products = [
            ProductSnapshot(
                product_id=str(index), name=name, category=category,
                category_hierarchy=f"Cửa hàng > Ngành mới > {category}", price_vnd=10000, stock=1,
            )
            for index, (name, category) in enumerate([
                ("Đất hữu cơ trồng cây túi 2kg", "Đất trồng cây"),
                ("Khăn thấm dầu hộp 20 tờ", "Khăn giấy gói"),
            ])
        ]

    paths = tuple(tuple(product.category_hierarchy.split(" > ")) for product in Repository.products)
    observations = _category_observations(Repository(), paths, "Mình cần đất trồng cây")
    assert observations == [{
        "category": "Đất trồng cây", "example_names": ["Đất hữu cơ trồng cây túi 2kg"],
    }]
