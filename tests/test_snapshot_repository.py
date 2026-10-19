from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.api import main as api_main
from backend.api.main import app
from backend.config import Settings
from backend.products.catalog import ProductSnapshot
from backend.products.repository import get_product_repository
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.retail_response_style import render_verified_snapshot
from backend.services.retail_tools import execute_catalog_tool
from backend.services.shopping import ShoppingService, ShoppingUnavailableError

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "winmart_products.json"
RESOLUTION_CASES = ROOT / "evaluation" / "cases" / "product_resolution_v3_offline.jsonl"


@pytest.fixture(scope="module")
def snapshot_repo() -> SnapshotProductRepository:
    return SnapshotProductRepository(DATA_PATH)


def test_configured_snapshot_repository_loads_validated_full_catalog() -> None:
    repo = get_product_repository(
        Settings(catalog_repository="snapshot", catalog_snapshot_path=str(DATA_PATH))
    )

    assert repo.source_name == "winmart_json_snapshot"
    assert len(repo.products) == 3081
    assert repo.rows_quarantined == 18


def test_snapshot_prices_and_stock_are_never_presented_as_current(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    product = snapshot_repo.get_product_snapshot("WIN_10194711")

    assert product is not None
    assert product.stock_is_current is False
    assert product.sellable_stock == 0
    assert product.spicy is None

    observation = execute_catalog_tool(
        "get_product_snapshot",
        json.dumps({"product_id": product.product_id, "fields": ["stock", "spicy"]}),
        repository=snapshot_repo,
    )
    details = observation.content["product"]
    assert details["stock"] == product.stock
    assert details["stock_is_current"] is False
    assert details["availability_status"] == "NOT_CURRENTLY_VERIFIED"
    assert observation.content["unavailable_fields"] == ["spicy"]

    rendered = render_verified_snapshot(product, "vi", ("stock", "spicy"))
    assert "tồn hiện tại chưa được xác minh" in rendered
    assert "chưa có dữ liệu về độ cay" in rendered


def test_runtime_catalog_resolves_all_heldout_product_queries_at_top_five(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    cases = [
        json.loads(line)
        for line in RESOLUTION_CASES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    misses = []
    for case in cases:
        returned = {
            product.product_id for product in snapshot_repo.search(case["query"], limit=5)
        }
        if not returned.intersection(case["gold_product_ids"]):
            misses.append(case["case_id"])

    assert len(cases) == 28
    assert misses == []


def test_small_coca_zero_query_does_not_rank_case_pack_above_single_bottle(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    results = snapshot_repo.search("coca sugar-free small container", limit=3)

    assert results
    assert results[0].product_id == "WIN_10194711"
    assert results[0].uom == "Chai"


def test_generic_coca_zero_query_prefers_single_bottle_to_case_pack(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    results = snapshot_repo.search("coca zero", limit=3)

    assert results
    assert results[0].uom == "Chai"


@pytest.mark.parametrize(
    ("query", "category", "expected_id"),
    [
        ("Huggies size L", "diaper", "WIN_10020234"),
        ("Omo", "laundry", "WIN_10406729"),
        ("Colgate kids", "cosmetic", "WIN_10625598"),
    ],
)
def test_taxonomy_aliases_cover_common_household_and_personal_care_requests(
    snapshot_repo: SnapshotProductRepository,
    query: str,
    category: str,
    expected_id: str,
) -> None:
    returned = snapshot_repo.search(query, category=category, limit=5)
    assert expected_id in {product.product_id for product in returned}


def test_snapshot_tools_abstain_on_unknown_spice_and_current_stock(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    spice = execute_catalog_tool(
        "recommend_products",
        json.dumps({"query": "snack", "category": "snack", "spicy": False}),
        repository=snapshot_repo,
    )
    stock = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "Coca Zero", "in_stock": True}),
        repository=snapshot_repo,
    )

    assert spice.status == "DATA_UNAVAILABLE"
    assert spice.content["unavailable_fields"] == ["spicy"]
    assert stock.status == "DATA_UNAVAILABLE"
    assert stock.content["unavailable_fields"] == ["current_stock"]


def test_snapshot_recommendations_and_promotions_disclose_unverified_inventory(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    recommendation = execute_catalog_tool(
        "recommend_products",
        json.dumps({"query": "snack", "category": "snack", "limit": 3}),
        repository=snapshot_repo,
    )
    promotions = execute_catalog_tool(
        "list_product_promotions", json.dumps({"limit": 3}), repository=snapshot_repo
    )

    assert recommendation.status == "SUCCESS"
    assert "current_availability_unverified" in recommendation.content["items"][0][
        "matched_constraints"
    ]
    assert recommendation.content["items"][0]["stock_is_current"] is False
    assert "reference_price_vnd" in recommendation.content["items"][0]
    assert promotions.status == "SUCCESS"
    assert promotions.content["items"]
    assert all(item["stock_is_current"] is False for item in promotions.content["items"])


def test_snapshot_catalog_cannot_be_used_for_sandbox_checkout(
    snapshot_repo: SnapshotProductRepository,
) -> None:
    shopping = ShoppingService(sandbox_enabled=True, repository=snapshot_repo)

    with pytest.raises(ShoppingUnavailableError):
        shopping._repo()


@pytest.mark.asyncio
async def test_product_search_api_returns_snapshot_products_without_current_stock_filter(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/products/search", params={"q": "coca zero 600ml"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert items[0]["product_id"] == "WIN_10194711"
    assert all(item["stock_is_current"] is False for item in items)


@pytest.mark.asyncio
async def test_product_search_api_refuses_explicit_current_stock_filter_for_snapshot(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unavailable = await client.get(
            "/products/search", params={"q": "coca zero 600ml", "in_stock": "true"}
        )
        allowed = await client.get(
            "/products/search", params={"q": "coca zero 600ml", "in_stock": "false"}
        )

    assert unavailable.status_code == 409
    assert allowed.status_code == 200
    assert allowed.json()["items"][0]["product_id"] == "WIN_10194711"


def test_unspecified_spice_is_unknown_not_non_spicy() -> None:
    product = ProductSnapshot(
        product_id="UNKNOWN_SPICE",
        name="Product",
        category="snack",
        price_vnd=1000,
        stock=1,
    )

    assert product.spicy is None
