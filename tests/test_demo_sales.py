"""30-day sales ranking must respect the same catalog identity and hard filters."""

from pathlib import Path

import httpx
import pytest

import backend.api.main as api
from backend.config import get_settings
from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.demo_sales import rank_demo_sales, simulated_units
from backend.services.demo_sales_store import DemoSalesStore
from backend.services.query_understanding import understand_catalog_query


@pytest.fixture(scope="module")
def snapshot():
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json"
    )


def _rank(snapshot, query, category, selected_ids=()):
    return rank_demo_sales(
        snapshot, query, category, selected_ids,
        sales_counts={p.product_id: simulated_units(p.product_id) for p in snapshot.products},
    )


@pytest.mark.parametrize(
    "category",
    [
        "Khăn mặt",
        "Dầu gội nữ",
        "Nước giặt",
        "Mì ăn liền",
        "Nồi cơm điện",
    ],
)
def test_demo_ranking_scans_whole_category_and_is_repeatable(snapshot, category):
    query = understand_catalog_query(
        f"{category} bán chạy nhất", snapshot.list_category_hierarchies()
    )
    result = _rank(snapshot, query, category)
    expected = sorted(
        (
            p
            for p in snapshot.products
            if normalize_category_text(category)
            in [normalize_category_text(label) for label in p.category_hierarchy.split(" > ")]
        ),
        key=lambda p: (-simulated_units(p.product_id), p.product_id),
    )[:3]
    assert expected
    assert [item.product.product_id for item in result] == [p.product_id for p in expected]
    assert result == _rank(snapshot, query, category)


def test_synthetic_sales_keep_variant_budget_exclusion_and_stock(snapshot):
    text = "Nước giặt bán chạy nhất loại túi dưới 200k không lấy hãng OMO"
    query = understand_catalog_query(text, snapshot.list_category_hierarchies())
    result = _rank(snapshot, query, "Nước giặt")
    assert result
    for item in result:
        product = item.product
        assert "tui" in normalize_category_text(product.name).split()
        assert product.price_vnd < 200000
        assert normalize_category_text(product.brand) != "omo"


def test_demo_total_budget_is_per_quantity_and_exclusive(snapshot):
    query = understand_catalog_query("Khăn mặt bán chạy nhất", snapshot.list_category_hierarchies())
    query = query.model_copy(
        update={
            "budget_vnd": 100000,
            "budget_scope": "total",
            "budget_operator": "LT",
            "quantity": 3,
        }
    )
    result = _rank(snapshot, query, "Khăn mặt")
    assert result and all(item.product.price_vnd * 3 < 100000 for item in result)


def test_demo_followup_modifiers_filter_selected_ids(snapshot):
    ids = tuple(p.product_id for p in snapshot.search(category="Nước giặt", limit=20))
    query = understand_catalog_query("loại túi bán chạy nhất")
    result = _rank(snapshot, query, None, ids)
    assert result
    assert all("tui" in normalize_category_text(item.product.name).split() for item in result)


@pytest.mark.parametrize(
    "text",
    [
        "Khăn mặt bán chạy nhất không gây dị ứng",
        "Thịt bán chạy nhất để ăn lẩu",
        "Khăn mặt bán chạy nhất tổng dưới 100k",
    ],
)
def test_demo_does_not_erase_unverified_or_incomplete_requirements(snapshot, text):
    query = understand_catalog_query(text, snapshot.list_category_hierarchies())
    assert not _rank(snapshot, query, query.product_type)


def test_repeated_page_fails_closed_instead_of_choosing_partial_winner():
    products = [
        ProductSnapshot(
            product_id=f"new-{i}",
            name=f"Đèn học Nova {i}",
            category="Đèn học",
            category_hierarchy="Gia dụng > Đèn học",
            brand="Nova",
            price_vnd=10000,
            stock=2,
        )
        for i in range(20)
    ]

    class RepeatedPage:
        def list_brand_names(self, **kwargs):
            return ["Nova"]

        def search(self, **kwargs):
            return products

    query = understand_catalog_query("Đèn học bán chạy nhất")
    assert not rank_demo_sales(RepeatedPage(), query, "Đèn học")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "environment,enabled,expected",
    [
        ("development", True, True),
        ("development", False, False),
        ("production", True, False),
    ],
)
async def test_demo_only_runs_when_enabled_in_development(
    snapshot,
    monkeypatch,
    environment,
    enabled,
    expected,
    tmp_path,
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    store = DemoSalesStore(tmp_path / "sales.sqlite3")
    store.seed(p.product_id for p in snapshot.products)
    settings = get_settings().model_copy(
        update={
            "app_env": environment,
            "demo_sales_enabled": enabled,
            "demo_sales_database_path": str(store.path),
        }
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://test",
        ) as client:
            session = (await client.post("/sessions", json={"kiosk_id": "sales-demo"})).json()[
                "session_id"
            ]
            response = await client.post(
                "/agent/respond",
                json={
                    "session_id": session,
                    "text": "khăn mặt nào là best seller",
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert (payload["model"] == "demo-sales") == expected
            if expected:
                assert "sản phẩm trong 30 ngày" in payload["text"]
                assert payload["products"][0]["sales_data_source"] == "SIMULATED"
                assert payload["products"][0]["demo_units_sold_30d"] > 0
                followup = (
                    await client.post(
                        "/agent/respond",
                        json={
                            "session_id": session,
                            "text": "loại này giá bao nhiêu",
                        },
                    )
                ).json()
                assert followup["grounded_product_ids"] == payload["grounded_product_ids"]
    finally:
        api.app.dependency_overrides.pop(api.get_settings, None)


@pytest.mark.asyncio
async def test_colloquial_school_supplies_location_keeps_identity(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "school-location"})).json()[
            "session_id"
        ]
        payload = (
            await client.post(
                "/agent/respond",
                json={
                    "session_id": session,
                    "text": "t muốn mua thước kẻ cho con ở đâu v",
                },
            )
        ).json()
    assert payload["products"]
    for card in payload["products"]:
        product = snapshot.get_product_snapshot(card["product_id"])
        assert "Đồ dùng học sinh" in product.category_hierarchy
        assert product.shelf in payload["text"]
    assert "nhãn sản phẩm" not in payload["text"]
