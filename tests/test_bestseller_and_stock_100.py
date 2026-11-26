"""Regression tests for store-wide/category best-sellers and snapshot stock queries."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import backend.api.main as api
from backend.config import get_settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.demo_sales_store import DemoSalesStore
from backend.services.stock_inventory import (
    parse_sales_or_stock_comparison,
    parse_stock_ranking_request,
    rank_snapshot_stock,
)


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json"
    )


def test_stock_inventory_parsers_and_ranking(snapshot: SnapshotProductRepository) -> None:
    req_most = parse_stock_ranking_request("Sản phẩm nào tồn kho nhiều nhất?")
    assert req_most is not None and req_most.mode == "most"
    top_items, zero_cnt, low_cnt = rank_snapshot_stock(
        snapshot, mode="most", category=None, top_n=5
    )
    assert len(top_items) == 5
    assert top_items[0].stock >= top_items[1].stock >= top_items[-1].stock
    assert zero_cnt >= 1 and low_cnt >= 10

    req_low = parse_stock_ranking_request("Sữa tươi nào sắp hết hàng?")
    assert req_low is not None and req_low.mode == "low"
    low_milk, _, _ = rank_snapshot_stock(snapshot, mode="low", category="Sữa tươi", top_n=3)
    assert low_milk
    assert all("sữa tươi" in (p.category_hierarchy or "").casefold() for p in low_milk)

    cmp_sales = parse_sales_or_stock_comparison("So sánh lượt bán của Coca-Cola và Pepsi?")
    assert cmp_sales is not None and cmp_sales.metric == "sales"
    cmp_stock = parse_sales_or_stock_comparison("So sánh tồn kho của mì Hảo Hảo và Omachi?")
    assert cmp_stock is not None and cmp_stock.metric == "stock"
    assert cmp_stock.target_b == "mi omachi"


@pytest.mark.asyncio
async def test_bestseller_and_stock_end_to_end_contracts(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    store = DemoSalesStore(tmp_path / "sales.sqlite3")
    store.seed(p.product_id for p in snapshot.products)
    settings = get_settings().model_copy(
        update={
            "app_env": "development",
            "demo_sales_enabled": True,
            "demo_sales_database_path": str(store.path),
            "customer_consultation_enabled": False,
            "consultation_cache_enabled": False,
        }
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=30
        ) as client:
            async def ask(text: str, *, turn1: str | None = None, lang: str = "vi") -> dict:
                sid = (await client.post("/sessions", json={"kiosk_id": "bs-test"})).json()[
                    "session_id"
                ]
                if turn1:
                    await client.post(
                        "/agent/respond",
                        json={"session_id": sid, "text": turn1, "language": lang},
                    )
                resp = await client.post(
                    "/agent/respond",
                    json={"session_id": sid, "text": text, "language": lang},
                )
                assert resp.status_code == 200
                return resp.json()

            # 1. Store-wide best-sellers (including Top-N & budget & colloquial phrasing)
            for q in (
                "Sản phẩm nào bán chạy nhất cửa hàng?",
                "Top 5 sản phẩm bán chạy nhất ở đây là gì?",
                "Ở đây món nào nhiều người mua nhất?",
                "Best seller của siêu thị là gì?",
                "Sản phẩm bán chạy nhất dưới 50.000đ?",
            ):
                res = await ask(q)
                assert res["model"] == "demo-sales", f"{q}: {res}"
                assert res["products"] and res["products"][0]["demo_units_sold_30d"] > 0

            # 2. Synonym / budget / sub-brand exclusion / English best-sellers
            gum = await ask("Kẹo cao su nào bán chạy nhất?")
            assert gum["model"] == "demo-sales" and gum["products"]
            milk40 = await ask("Sữa tươi nào bán chạy nhất dưới 40.000đ?")
            assert milk40["model"] == "demo-sales" and milk40["products"][0]["price_vnd"] < 40000
            no_hh = await ask("Mì nào bán chạy nhất không phải Hảo Hảo?")
            assert no_hh["model"] == "demo-sales"
            assert "hảo hảo" not in no_hh["products"][0]["name"].casefold()
            en_bs = await ask("Which instant noodle is the best seller?", lang="en")
            assert en_bs["model"] == "demo-sales" and en_bs["products"]

            # 3. Stock ranking (most / least / low / out / category) with snapshot disclaimer
            most = await ask("Sản phẩm nào tồn kho nhiều nhất?")
            assert most["model"] == "catalog-stock" and most["products"]
            assert "snapshot" in most["text"].casefold()
            low_milk = await ask("Sữa tươi nào sắp hết hàng?")
            assert low_milk["model"] == "catalog-stock" and low_milk["products"]

            # 4. Multi-variant stock discloses snapshot counts
            hh_stock = await ask("Mì Hảo Hảo tồn kho bao nhiêu gói?")
            assert hh_stock["products"] and "snapshot tồn" in hh_stock["text"].casefold()

            # 5. Sales & stock comparisons + affordable best-seller
            cheap_bs = await ask("Món nào vừa bán chạy vừa rẻ?")
            assert cheap_bs["model"] == "demo-sales" and cheap_bs["products"]
            assert cheap_bs["products"][0]["price_vnd"] <= 25000
            cmp_s = await ask("So sánh lượt bán của Coca-Cola và Pepsi?")
            assert cmp_s["model"] == "demo-sales" and len(cmp_s["products"]) == 2
            assert "ngang nhau" in cmp_s["text"].casefold() or "cao hơn" in cmp_s["text"].casefold()
            cmp_inv = await ask("So sánh tồn kho của mì Hảo Hảo và Omachi?")
            assert cmp_inv["model"] == "catalog-stock" and len(cmp_inv["products"]) == 2
            assert any(p["stock"] >= 500 for p in cmp_inv["products"])

            # 6. Multi-turn bare stock & budget best-seller follow-ups stay locked to Turn 1
            mt_srm = await ask(
                "Còn bao nhiêu tuýp trong kho?", turn1="Hỏi giá sữa rửa mặt"
            )
            assert mt_srm["products"]
            assert "sữa rửa mặt" in mt_srm["products"][0]["name"].casefold()
            assert "colgate" not in mt_srm["products"][0]["name"].casefold()
            mt_noodle_10k = await ask(
                "Cái nào bán chạy nhất dưới 10k?", turn1="Cho tôi xem mì ăn liền"
            )
            assert mt_noodle_10k["model"] == "demo-sales" and mt_noodle_10k["products"]
            assert mt_noodle_10k["products"][0]["price_vnd"] < 10000
            mt_gum_stock = await ask("Còn hàng không?", turn1="Có những loại kẹo gum nào?")
            assert mt_gum_stock["model"] == "catalog-stock" and mt_gum_stock["products"]
            assert "snapshot" in mt_gum_stock["text"].casefold()
            mt_milk_stock = await ask("Còn hàng không?", turn1="Có những loại sữa tươi nào?")
            assert mt_milk_stock["model"] == "catalog-stock" and mt_milk_stock["products"]
            assert "snapshot" in mt_milk_stock["text"].casefold()

            # 7. Out-of-catalog stock & best-seller never leak unrelated products
            for ooc in (
                "Đồ khui bia tồn kho bao nhiêu?",
                "Áo mưa còn hàng không?",
                "Bóng đèn nào bán chạy nhất?",
            ):
                res = await ask(ooc)
                assert not res["products"], f"OOC leaked: {ooc} -> {res['products']}"
    finally:
        api.app.dependency_overrides.pop(api.get_settings, None)
