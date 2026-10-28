from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import backend.api.main as api
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.catalog_browse import explicit_catalog_category_pair


@pytest.fixture(scope="module")
def snapshot():
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/winmart_products.json"
    )


@pytest.fixture(scope="module")
def hierarchies(snapshot):
    return snapshot.list_category_hierarchies()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Nước tinh khiết đóng chai và nước khoáng có bán không?",
            ("Nước tinh khiết", "Nước khoáng"),
        ),
        (
            "Cá nước ngọt tươi và cá biển đang bán gồm những loại nào?",
            ("Cá nước ngọt tươi", "Cá nước mặn tươi"),
        ),
        (
            "Rau cải và thịt bò có bán không?",
            ("Rau cải", "Thịt Bò, Bê"),
        ),
        (
            "Mì ăn liền và nước khoáng đang bán gồm những loại nào?",
            ("Mì ăn liền", "Nước khoáng"),
        ),
    ],
)
def test_category_enumeration_resolves_distinct_taxonomy_groups(hierarchies, text, expected):
    assert explicit_catalog_category_pair(text, hierarchies) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Mì ăn liền vị bò với vị gà đang có những nhãn nào?",
        "Trứng gà ta với trứng gà công nghiệp có loại nào?",
        "Dầu gội với sữa tắm có những loại nào?",
        "Mì ăn liền với nước khoáng khác nhau gì?",
        "Mì ăn liền với nước khoáng loại nào rẻ nhất?",
        "Bim bim và thịt bò có bán ở kệ nào?",
        "Tôi thích mì bò và gà nướng.",
    ],
)
def test_category_enumeration_does_not_capture_variants_or_other_intents(hierarchies, text):
    assert explicit_catalog_category_pair(text, hierarchies) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_categories"),
    [
        (
            "Nước tinh khiết đóng chai và nước khoáng có bán không?",
            ("Nước tinh khiết", "Nước khoáng"),
        ),
        (
            "Cá nước ngọt tươi và cá biển đang bán gồm những loại nào?",
            ("Cá nước ngọt tươi", "Cá nước mặn tươi"),
        ),
        (
            "Rau cải và thịt bò có bán không?",
            ("Rau cải", "Thịt Bò, Bê"),
        ),
    ],
)
async def test_multi_category_browse_returns_only_grounded_catalog_items(
    snapshot,
    monkeypatch,
    text,
    expected_categories,
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "category-browse"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": text},
        )

    payload = response.json()
    assert response.status_code == 200, response.text
    assert payload["model"] == "catalog-category-browse"
    assert payload["tools_used"] == ["search_products"]
    assert len(payload["grounded_product_ids"]) >= 2
    assert "snapshot" in payload["text"].lower()
    assert "chưa được xác minh" in payload["text"]
    saved = api.session_store.get(api.UUID(payload["session_id"])).shopping
    assert set(saved.selected_product_ids) == set(payload["grounded_product_ids"])
    assert saved.active_category is None

    products = [
        snapshot.get_product_snapshot(item_id) for item_id in payload["grounded_product_ids"]
    ]
    assert all(product is not None for product in products)
    for category in expected_categories:
        assert category in payload["text"]
        assert any(
            product is not None and category in product.category_hierarchy
            for product in products
        )


@pytest.mark.asyncio
async def test_same_category_flavors_continue_through_single_category_search(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "variant-browse"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session,
                "text": "Mì ăn liền vị bò với vị gà đang có những nhãn nào?",
            },
        )

    payload = response.json()
    assert response.status_code == 200, response.text
    assert payload["model"] != "catalog-category-browse"
    assert all(
        product is not None and "Mì ăn liền" in product.category_hierarchy
        for product in (
            snapshot.get_product_snapshot(product_id)
            for product_id in payload["grounded_product_ids"]
        )
    )
