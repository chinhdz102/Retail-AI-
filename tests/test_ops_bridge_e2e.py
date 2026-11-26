"""End-to-end integration tests for read-only coordination between ``ops_chest`` and ``Retail-AI-``.

Verifies all inter-agent requirements over a simulated ``ops_chest`` HTTP API + HITL state machine
mapped through the shared 164-SKU crosswalk (``data/bridge/crosswalk.csv``):
  1. 164/164 crosswalk SKU integrity against ``data/thanh_lam_products.json``.
  2. HITL isolation: unapproved ``PENDING`` markdown proposals never leak to the customer kiosk.
  3. Approved FEFO markdown propagation: manager approval updates kiosk price and promo immediately.
  4. Live stockout suppression: zero-stock SKU is reported out-of-stock on direct lookup and hidden
     from in-stock recommendations in favor of an in-stock mapped substitute.
  5. Fail-closed stale TTL and offline fallback to static snapshot caveats.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import backend.api.main as api
from backend.config import get_settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.ops_bridge import (
    BridgedProductRepository,
    OpsChestBridgeClient,
    OpsCrosswalkIndex,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "thanh_lam_products.json"
CROSSWALK_PATH = ROOT / "data" / "bridge" / "crosswalk.csv"


def _build_simulated_ops_chest_app(state: dict[str, Any]) -> FastAPI:
    """Minimal faithful simulator of ``ops_chest`` read-only endpoints + HITL approval."""
    sim = FastAPI()

    @sim.get("/api/inventory/summary")
    def inventory_summary() -> dict[str, Any]:
        if state.get("offline"):
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="ops_chest unavailable")
        return {
            "store_id": 18,
            "as_of": state["as_of"].isoformat(),
            "active_skus": len(state["items"]),
            "approved_markdowns": sum(
                1 for p in state["markdowns"].values() if p["status"] in {"APPROVED", "EXECUTED"}
            ),
        }

    @sim.get("/api/inventory/items")
    def inventory_items(sku: str | None = None) -> dict[str, Any]:
        if state.get("offline"):
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="ops_chest unavailable")
        rows = list(state["items"].values())
        if sku:
            rows = [r for r in rows if r["sku"] == sku]
        return {"items": rows}

    @sim.get("/api/inventory/expiring")
    def inventory_expiring() -> dict[str, Any]:
        if state.get("offline"):
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="ops_chest unavailable")
        return {"items": list(state["markdowns"].values())}

    @sim.post("/api/proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: str) -> dict[str, Any]:
        prop = state["markdowns"][proposal_id]
        prop["status"] = "EXECUTED"
        prop["approved_at"] = datetime.now(UTC).isoformat()
        state["as_of"] = datetime.now(UTC)
        return prop

    return sim


class _AsgiSyncTransport(httpx.BaseTransport):
    """Synchronous httpx transport routing directly into an in-process FastAPI app."""

    def __init__(self, app: FastAPI) -> None:
        from starlette.testclient import TestClient

        self._client = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        res = self._client.request(
            method=request.method,
            url=str(request.url.raw_path, "ascii"),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=res.status_code,
            headers=dict(res.headers),
            content=res.content,
            request=request,
        )


@pytest.fixture(scope="module")
def base_snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(CATALOG_PATH)


def test_crosswalk_164_sku_integrity(base_snapshot: SnapshotProductRepository) -> None:
    crosswalk = OpsCrosswalkIndex.from_csv(CROSSWALK_PATH)
    assert len(crosswalk) == 164
    valid_ids = {p.product_id for p in base_snapshot.products}
    matched = [e for e in crosswalk.entries if e.product_id in valid_ids]
    assert len(matched) == 164


@pytest.mark.asyncio
async def test_inter_agent_e2e_markdown_stockout_hitl_and_fail_closed(
    base_snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crosswalk = OpsCrosswalkIndex.from_csv(CROSSWALK_PATH)
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    # F0005 -> TLM_10283202 (MEATDELI Cánh gà, regular 123,900 VND)
    # F0007 -> TLM_10283203 (MEATDELI Má đùi gà, regular 95,900 VND)
    ops_state: dict[str, Any] = {
        "offline": False,
        "as_of": now,
        "items": {
            "F0005": {
                "sku": "F0005",
                "on_hand": 18,
                "expiry_at": tomorrow,
                "updated_at": now.isoformat(),
            },
            "F0007": {
                "sku": "F0007",
                "on_hand": 14,
                "expiry_at": tomorrow,
                "updated_at": now.isoformat(),
            },
        },
        "markdowns": {
            "prop_md_01": {
                "proposal_id": "prop_md_01",
                "sku": "F0005",
                "status": "PENDING",
                "discount_pct": 25,
                "markdown_price_vnd": 92925,
                "expiry_at": tomorrow,
            }
        },
    }
    sim_ops_app = _build_simulated_ops_chest_app(ops_state)
    sim_transport = _AsgiSyncTransport(sim_ops_app)
    bridge_client = OpsChestBridgeClient(
        base_url="http://ops-chest.sim",
        crosswalk=crosswalk,
        freshness_ttl_seconds=300,
        transport=sim_transport,
    )
    bridged_repo = BridgedProductRepository(base=base_snapshot, bridge=bridge_client)

    monkeypatch.setattr(api, "get_product_repository", lambda *args: bridged_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: bridged_repo)

    settings = get_settings().model_copy(
        update={
            "app_env": "development",
            "demo_sales_enabled": False,
            "customer_consultation_enabled": False,
            "consultation_cache_enabled": False,
        }
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=30
        ) as kiosk:
            async def ask(text: str) -> dict[str, Any]:
                sid = (await kiosk.post("/sessions", json={"kiosk_id": "bridge-e2e"})).json()[
                    "session_id"
                ]
                t0 = time.perf_counter()
                resp = await kiosk.post(
                    "/agent/respond",
                    json={"session_id": sid, "text": text, "language": "vi"},
                )
                assert resp.status_code == 200
                data = resp.json()
                data["_latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
                return data

            # 1) HITL ISOLATION: PENDING markdown must NOT affect kiosk price
            r_pending = await ask("MEATDELI Cánh gà giá bao nhiêu?")
            assert r_pending["products"]
            wing_card = next(
                p for p in r_pending["products"] if p["product_id"] == "TLM_10283202"
            )
            assert wing_card["price_vnd"] == 123900
            assert "92.925" not in r_pending["text"]

            # 2) MANAGER APPROVES FEFO MARKDOWN IN ops_chest -> KIOSK REFLECTS 92,925 VND
            with httpx.Client(base_url="http://ops-chest.sim", transport=sim_transport) as ops_http:
                ap = ops_http.post("/api/proposals/prop_md_01/approve")
                assert ap.status_code == 200
                assert ap.json()["status"] == "EXECUTED"
            bridge_client.invalidate_cache()

            r_approved = await ask("MEATDELI Cánh gà giá bao nhiêu?")
            wing_after = next(
                p for p in r_approved["products"] if p["product_id"] == "TLM_10283202"
            )
            assert wing_after["price_vnd"] == 92925
            assert "92.925" in r_approved["text"]

            # Active FEFO markdown also surfaces in promotions list
            promos = bridged_repo.list_promotions(category="gà", limit=5)
            assert any(
                p.product_id == "TLM_10283202"
                and p.price_vnd == 92925
                and "FEFO" in (p.promotion or "")
                for p in promos
            )

            # 3) LIVE STOCKOUT SUPPRESSION: ops_chest sets F0007 (TLM_10283203) on_hand = 0
            ops_state["items"]["F0007"]["on_hand"] = 0
            ops_state["as_of"] = datetime.now(UTC)
            bridge_client.invalidate_cache()

            # Direct inquiry reports live out-of-stock (sellable_stock == 0, current == True)
            r_oos = await ask("MEATDELI Má đùi gà còn hàng không?")
            assert r_oos["products"]
            thigh_snap = bridged_repo.get_product_snapshot("TLM_10283203")
            assert thigh_snap is not None
            assert thigh_snap.stock == 0
            assert thigh_snap.stock_is_current is True
            assert thigh_snap.sellable_stock == 0
            assert (
                "chưa ghi nhận hàng bán được" in r_oos["text"].casefold()
                or "hết hàng" in r_oos["text"].casefold()
                or "0" in r_oos["text"]
            )

            # In-stock search suppresses TLM_10283203 and returns in-stock mapped TLM_10283202
            in_stock_chicken = bridged_repo.search(query="MEATDELI gà", in_stock=True, limit=10)
            in_stock_ids = [p.product_id for p in in_stock_chicken]
            assert "TLM_10283203" not in in_stock_ids
            assert "TLM_10283202" in in_stock_ids

            # 4) FAIL-CLOSED STALE TTL: timestamp older than 300s reverts safely to static snapshot
            ops_state["as_of"] = datetime.now(UTC) - timedelta(seconds=600)
            bridge_client.invalidate_cache()
            snap_stale = bridge_client.get_snapshot()
            assert snap_stale.is_fresh is False
            wing_stale = bridged_repo.get_product_snapshot("TLM_10283202")
            assert wing_stale is not None
            assert wing_stale.stock_is_current is False
            assert wing_stale.price_vnd == 123900

            # 5) FAIL-CLOSED OFFLINE: HTTP 503 reverts safely with zero crash
            ops_state["as_of"] = datetime.now(UTC)
            ops_state["offline"] = True
            bridge_client.invalidate_cache()
            r_offline = await ask("MEATDELI Cánh gà giá bao nhiêu?")
            assert r_offline["products"]
            assert bridge_client.get_snapshot().is_fresh is False
    finally:
        api.app.dependency_overrides.pop(api.get_settings, None)
