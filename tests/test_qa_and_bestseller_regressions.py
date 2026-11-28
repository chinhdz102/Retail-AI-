"""Regression tests for product knowledge QA, storage/OOC routing, and best-seller queries."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import backend.api.main as api
from backend.config import get_settings
from backend.products.knowledge import _load_retailer_details
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.demo_sales_store import DemoSalesStore


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    _load_retailer_details.cache_clear()
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json"
    )


@pytest.mark.asyncio
async def test_product_knowledge_qa_regressions(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    settings = get_settings().model_copy(
        update={
            "app_env": "development",
            "customer_consultation_enabled": False,
            "consultation_cache_enabled": False,
        }
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=30
        ) as client:
            async def new_session() -> tuple[str, dict[str, str]]:
                resp = await client.post("/sessions", json={"kiosk_id": "qa-reg", "language": "vi"})
                assert resp.status_code == 201
                sid = resp.json()["session_id"]
                tok = resp.headers.get("X-Kiosk-Session-Token") or resp.headers.get(
                    "x-kiosk-session-token"
                )
                return sid, ({"X-Kiosk-Session-Token": tok} if tok else {})

            async def ask(
                text: str,
                *,
                session: tuple[str, dict[str, str]] | None = None,
            ) -> dict:
                sid, hdrs = session if session is not None else await new_session()
                resp = await client.post(
                    "/agent/respond",
                    json={"session_id": sid, "text": text, "language": "vi"},
                    headers=hdrs,
                )
                assert resp.status_code == 200
                return resp.json()

            # 1. Origin queries without "ở đâu" + multi-SKU shared origin
            r_ritter = await ask("Socola Ritter Sport sữa hạnh nhân xuất xứ nước nào?")
            assert "Đức" in r_ritter["text"]

            r_pepsi_src = await ask("Nguồn gốc của Nước giải khát Pepsi 1.5 lít là gì?")
            assert "Việt Nam" in r_pepsi_src["text"]

            r_pepsi_country = await ask("Pepsi 1.5 lít của nước nào?")
            assert "Việt Nam" in r_pepsi_country["text"]

            r_chinsu = await ask("Tương ớt Chin Su sản xuất ở đâu?")
            assert "Việt Nam" in r_chinsu["text"]

            # 2. Food storage/cooking queries bypass appliance OOC gate;
            #    appliance shopping stays blocked
            r_mm = await ask("Kẹo socola M&M's gói 100g có cần để tủ lạnh không?")
            assert r_mm["model"] != "local-ooc-gate"
            assert "khô ráo" in r_mm["text"].casefold()

            r_milk = await ask("Sữa tươi Vinamilk có cần để tủ lạnh không?")
            assert r_milk["model"] != "local-ooc-gate"
            assert "bảo quản" in r_milk["text"].casefold()

            r_ooc = await ask(
                "Ở đây có bán máy lọc không khí Xiaomi hoặc nồi chiên không dầu không?"
            )
            assert r_ooc["model"] == "local-ooc-gate"

            assert not api._non_supermarket_out_of_catalog_requested(
                "Chả giò chiên bằng nồi chiên không dầu được không?"
            )

            # 3. Multi-attribute ingredients + storage returns both verified facts
            r_romano = await ask(
                "Dầu tắm gội Romano Force 650g có thành phần gì và bảo quản thế nào?"
            )
            assert "Water" in r_romano["text"] and "Bảo quản" in r_romano["text"]

            # 4. Material ("chất liệu") query maps to verified ingredients/material
            r_hap = await ask("Bình nước PET Lock&Lock HAP731 làm từ chất liệu gì?")
            assert "Nhựa PET" in r_hap["text"]

            # 5. Calorie basis "100g" renders cleanly as "trên 100 g"
            r_carrot = await ask("Cà rốt có bao nhiêu calo?")
            assert "41.3 kcal trên 100 g" in r_carrot["text"]

            # 6. Catalog typo alias Jumbo <-> Jumpo Vape finds TLM_10020111
            r_vape = await ask("Xịt côn trùng Jumbo Vape hương lài có thành phần gì?")
            assert "Jumpo Vape" in r_vape["text"]
            assert "Transfluthrin" in r_vape["text"]

            # 7. Multi-turn follow-up "thành phần chính là gì?" stays grounded on Pepsi
            #    (never Mì chính)
            sess = await new_session()
            t1 = await ask("Pepsi 1.5 lít giá bao nhiêu?", session=sess)
            assert "Pepsico" in t1["text"]
            t2 = await ask("thành phần chính là gì?", session=sess)
            assert "Pepsico" in t2["text"] and "Mì chính" not in t2["text"]
            t3 = await ask("bảo quản thế nào?", session=sess)
            assert "Pepsico" in t3["text"] and "Bảo quản" in t3["text"]
            t4 = await ask("xuất xứ nước nào?", session=sess)
            assert "Pepsico" in t4["text"] and "Việt Nam" in t4["text"]
    finally:
        api.app.dependency_overrides.pop(api.get_settings, None)


@pytest.mark.asyncio
async def test_bestseller_and_sales_comparison_regressions(
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
            async def new_session() -> tuple[str, dict[str, str]]:
                resp = await client.post("/sessions", json={"kiosk_id": "bs-reg", "language": "vi"})
                assert resp.status_code == 201
                sid = resp.json()["session_id"]
                tok = resp.headers.get("X-Kiosk-Session-Token") or resp.headers.get(
                    "x-kiosk-session-token"
                )
                return sid, ({"X-Kiosk-Session-Token": tok} if tok else {})

            async def ask(
                text: str,
                *,
                session: tuple[str, dict[str, str]] | None = None,
            ) -> dict:
                sid, hdrs = session if session is not None else await new_session()
                resp = await client.post(
                    "/agent/respond",
                    json={"session_id": sid, "text": text, "language": "vi"},
                    headers=hdrs,
                )
                assert resp.status_code == 200
                return resp.json()

            # 1. Title-verified descriptor best-sellers
            milk0 = await ask("sữa tươi không đường nào bán chạy nhất?")
            assert milk0["model"] == "demo-sales" and milk0["products"]
            assert "không đường" in milk0["products"][0]["name"].casefold()

            drink0 = await ask("nước ngọt không đường nào bán chạy nhất?")
            assert drink0["model"] == "demo-sales" and drink0["products"]
            assert any(
                k in drink0["products"][0]["name"].casefold()
                for k in ("không đường", "không calo", "zero")
            )

            drink0cal = await ask("nước ngọt không calo nào bán chạy nhất?")
            assert drink0cal["model"] == "demo-sales" and drink0cal["products"]

            noodle_ns = await ask("mì không cay nào bán chạy nhất?")
            assert noodle_ns["model"] == "demo-sales" and noodle_ns["products"]
            assert "cay" not in noodle_ns["products"][0]["name"].casefold()

            # 2. Synonyms, use-cases, colloquial prompts & truthful unmatched-modifier fallback
            snack_lf = await ask("snack ít béo nào bán chạy nhất?")
            assert snack_lf["model"] == "demo-sales" and snack_lf["products"]
            assert "chưa có số liệu bán" not in snack_lf["text"].casefold()

            pastry = await ask("bánh ăn sáng nào bán chạy nhất?")
            assert pastry["model"] == "demo-sales" and pastry["products"]

            baby_milk = await ask("sữa cho bé nào bán chạy nhất?")
            assert baby_milk["model"] == "demo-sales" and baby_milk["products"]
            assert "sữa tắm" not in baby_milk["products"][0]["name"].casefold()

            water = await ask("nước suối nào bán chạy nhất?")
            assert water["model"] == "demo-sales" and water["products"]

            mau = await ask("mẫu nào chạy nhất?")
            assert mau["model"] == "demo-sales" and mau["products"]

            sp = await ask("sp nào bán chạy nhất?")
            assert sp["model"] == "demo-sales" and sp["products"]

            # 3. Single-item lookup -> category best-seller (never Cá nước ngọt) & storewide escape
            sess_pepsi = await new_session()
            await ask("Pepsi 1.5 lít giá bao nhiêu?", session=sess_pepsi)
            cat_bs = await ask("cái nào bán chạy nhất?", session=sess_pepsi)
            assert cat_bs["model"] == "demo-sales" and cat_bs["products"]
            assert "cá " not in cat_bs["products"][0]["name"].casefold()

            store_bs = await ask("sản phẩm nào bán chạy nhất?", session=sess_pepsi)
            assert store_bs["model"] == "demo-sales" and store_bs["products"]

            # 4. 2-turn comparison -> "Cái nào bán chạy hơn?" returns 30-day sales comparison
            sess_cmp = await new_session()
            cmp_t1 = await ask("So sánh Coca-Cola và Pepsi", session=sess_cmp)
            # Ensure session holds 2 compared products even if disambiguated
            if cmp_t1["model"] == "comparison-clarification":
                cmp_t1 = await ask(
                    "So sánh Nước giải khát Coca Cola chai 1.5lít "
                    "và Nước giải khát có gas Pepsico chai 1.5 lít",
                    session=sess_cmp,
                )
            cmp_t2 = await ask("Cái nào bán chạy hơn?", session=sess_cmp)
            assert cmp_t2["model"] == "demo-sales"
            assert "số liệu bán" in cmp_t2["text"].casefold()
            assert "không có số liệu bán" not in cmp_t2["text"].casefold()

            # 5. OOC product still returns local-ooc-gate
            ooc_bs = await ask("bóng đèn nào bán chạy nhất?")
            assert ooc_bs["model"] == "local-ooc-gate" and not ooc_bs["products"]
    finally:
        api.app.dependency_overrides.pop(api.get_settings, None)
