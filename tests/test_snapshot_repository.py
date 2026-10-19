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
from backend.services import retail_tools
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
    results = snapshot_repo.search("Cho loại Coca không đường chai nhỏ.", limit=3)

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
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)
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


@pytest.mark.asyncio
async def test_snapshot_api_keeps_price_freshness_shelf_and_stock_claims_grounded(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/sessions", json={"kiosk_id": "snapshot-grounding"})
        session_id = created.json()["session_id"]
        price = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Coca Zero chai 600ml giá bao nhiêu?"},
        )
        price_confirmation = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Vậy giá ở cửa hàng hôm nay đã được xác nhận chưa?",
            },
        )
        shelf = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Nó ở kệ nào?"},
        )
        stock = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Hiện còn hàng không?"},
        )
        freshness = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Dữ liệu được cập nhật ngày nào?"},
        )

    price_body = price.json()
    assert (
        price.status_code
        == shelf.status_code
        == stock.status_code
        == freshness.status_code
        == price_confirmation.status_code
        == 200
    )
    assert price_body["grounded_product_ids"] == ["WIN_10194711"]
    assert "9.900đ" in price_body["text"]
    assert "giá tham khảo" in price_body["text"].casefold()
    assert "/Chai" in price_body["text"]
    assert "15/09/2026" not in price_body["text"]
    assert "giá tham khảo" in price_confirmation.json()["text"].casefold()
    assert (
        "chưa thể xác nhận đây là giá tại cửa hàng hôm nay"
        in price_confirmation.json()["text"].casefold()
    )
    assert price_confirmation.json()["grounded_product_ids"] == ["WIN_10194711"]
    assert price_body["data_source"] == "winmart_json_snapshot"
    assert price_body["products"][0]["stock_is_current"] is False
    assert "sơ đồ kệ đề xuất" in shelf.json()["text"].casefold()
    assert "chưa xác minh" in shelf.json()["text"].casefold()
    assert "tồn hiện tại chưa được xác minh" in stock.json()["text"].casefold()
    assert "15/09/2026" in freshness.json()["text"]


@pytest.mark.asyncio
async def test_unavailable_product_alternatives_use_catalog_and_retain_category(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)

    async def unexpected_llm_call(*_args, **_kwargs):
        raise AssertionError("Grounded unavailable-product recovery should not call the LLM")

    monkeypatch.setattr(api_main, "run_retail_agent", unexpected_llm_call)
    transport = httpx.ASGITransport(app=app)
    expected_ids = {"WIN_10011695", "WIN_10150497", "WIN_10625446"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for follow_up in (
            "Nếu chưa có thì có nước nào khác không?",
            "Có loại nào tương tự không?",
        ):
            created = await client.post("/sessions", json={"kiosk_id": "snapshot-alternatives"})
            session_id = created.json()["session_id"]
            missing = await client.post(
                "/agent/respond",
                json={"session_id": session_id, "text": "Có nước yến vị đào không?"},
            )
            alternatives = await client.post(
                "/agent/respond",
                json={"session_id": session_id, "text": follow_up},
            )

            assert missing.status_code == alternatives.status_code == 200
            assert missing.json()["tools_used"] == ["search_products"]
            assert missing.json()["grounded_product_ids"] == []
            alternative_body = alternatives.json()
            assert "recommend_products" in alternative_body["tools_used"]
            assert expected_ids.issubset(set(alternative_body["grounded_product_ids"]))
            assert "giá tham khảo" in alternative_body["text"].casefold()
            assert "tồn hiện tại chưa được xác minh" in alternative_body["text"].casefold()
            assert alternative_body.get("cart") is None

        created = await client.post("/sessions", json={"kiosk_id": "snapshot-whole-cart"})
        combo = await client.post(
            "/agent/respond",
            json={
                "session_id": created.json()["session_id"],
                "text": "Mình có 30k, muốn mua nước với đồ ăn vặt.",
            },
        )

    combo_body = combo.json()
    assert combo.status_code == 200
    assert "recommend_products" in combo_body["tools_used"]
    assert "tồn hiện tại chưa được xác minh" in combo_body["text"].casefold()
    assert combo_body.get("cart") is None


@pytest.mark.asyncio
async def test_snapshot_api_refuses_to_add_unverified_inventory_to_demo_cart(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import shopping as shopping_module

    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(shopping_module, "get_product_repository", lambda: snapshot_repo)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/sessions", json={"kiosk_id": "snapshot-cart-guard"})
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": created.json()["session_id"],
                "text": "Thêm một chai Coca Zero 600ml vào giỏ giúp mình.",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["state"] == "BROWSING"
    assert body.get("cart") is None
    assert "chưa xác minh" in body["text"].casefold()


@pytest.mark.asyncio
async def test_snapshot_unavailable_attribute_keeps_selected_product_for_followups(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/sessions", json={"kiosk_id": "snapshot-context"})
        session_id = created.json()["session_id"]
        price = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Cho mình Lay's classic 150g, giá nhiêu?",
            },
        )
        spice = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Nó có cay không?"},
        )
        shelf = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Kệ của gói này ở đâu?"},
        )

    product_id = "WIN_10194182"
    assert price.status_code == spice.status_code == shelf.status_code == 200
    assert price.json()["grounded_product_ids"] == [product_id]
    assert spice.json()["grounded_product_ids"] == [product_id]
    assert "chưa có dữ liệu về độ cay" in spice.json()["text"].casefold()
    assert shelf.json()["grounded_product_ids"] == [product_id]
    assert "A06-B01-L05-P07" in shelf.json()["text"]


@pytest.mark.asyncio
async def test_snapshot_product_search_resolves_variants_and_refinements_without_llm(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/sessions", json={"kiosk_id": "snapshot-refinement"})
        session_id = created.json()["session_id"]
        found = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Tìm Omo cho máy giặt cửa trước với."},
        )
        price = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Loại túi 2,9kg giá bao nhiêu?",
            },
        )
        dosage = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Dùng liều lượng bao nhiêu cho một mẻ giặt?",
            },
        )
        promotion = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Có chương trình giảm giá nào không?"},
        )
        stock = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Còn hàng hiện nay không?"},
        )

    assert all(
        response.status_code == 200 for response in (found, price, dosage, promotion, stock)
    )
    assert "Nước giặt Omo" in found.json()["text"]
    assert "Ariel" not in found.json()["text"]
    assert price.json()["grounded_product_ids"] == ["WIN_10161177"]
    assert "185.000đ" in price.json()["text"]
    assert dosage.json()["grounded_product_ids"] == ["WIN_10161177"]
    assert "chưa có dữ liệu đã xác minh về liều lượng" in dosage.json()["text"].casefold()
    assert "185.000đ" not in dosage.json()["text"]
    assert promotion.json()["grounded_product_ids"] == ["WIN_10161177"]
    assert "chưa có thông tin về khuyến mãi" in promotion.json()["text"].casefold()
    assert "Fami" not in promotion.json()["text"]
    assert stock.json()["grounded_product_ids"] == ["WIN_10161177"]
    assert "tồn hiện tại chưa được xác minh" in stock.json()["text"].casefold()


@pytest.mark.asyncio
async def test_snapshot_candidate_code_selects_the_exact_huggies_pack(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)

    class UnexpectedGroqService:
        async def chat(self, *args, **kwargs):
            raise AssertionError("Exact catalog selection must not call the LLM")

    monkeypatch.setitem(
        app.dependency_overrides,
        api_main.get_groq_service,
        lambda: UnexpectedGroqService(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/sessions", json={"kiosk_id": "snapshot-huggies-code"})
        session_id = created.json()["session_id"]
        candidates = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Cho mình bỉm Huggies size L."},
        )
        selected = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Gói L38 ấy."},
        )
        unit_followup = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Giá này theo gói hay theo miếng?"},
        )

    assert candidates.status_code == selected.status_code == unit_followup.status_code == 200
    assert "L38" in candidates.json()["text"] and "L68" in candidates.json()["text"]
    assert selected.json()["grounded_product_ids"] == ["WIN_10161030"]
    assert "263.000đ/Gói" in selected.json()["text"]
    assert unit_followup.json()["grounded_product_ids"] == ["WIN_10161030"]
    assert "263.000đ/Gói" in unit_followup.json()["text"]


@pytest.mark.asyncio
async def test_snapshot_comparison_keeps_product_context_across_size_and_promotion_followups(
    snapshot_repo: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "get_product_repository", lambda: snapshot_repo)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: snapshot_repo)

    class UnexpectedGroqService:
        async def chat(self, *args, **kwargs):
            raise AssertionError("Grounded catalog follow-ups must not call the LLM")

    monkeypatch.setitem(
        app.dependency_overrides,
        api_main.get_groq_service,
        lambda: UnexpectedGroqService(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/sessions", json={"kiosk_id": "snapshot-coca-followups"})
        session_id = created.json()["session_id"]
        comparison = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Coca Zero 330 với chai 600 khác gì nhau?",
            },
        )
        bottle_price = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Chai 600ml giá bao nhiêu?"},
        )
        larger_variant = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Thế loại chai 1,5 lít thì sao?"},
        )
        promotion_validity = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Ưu đãi kia còn áp dụng không?"},
        )
        stock_confirmation = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Còn 1,5 lít chắc chắn chứ?"},
        )

    assert all(
        response.status_code == 200
        for response in (
            comparison,
            bottle_price,
            larger_variant,
            promotion_validity,
            stock_confirmation,
        )
    )
    assert set(comparison.json()["grounded_product_ids"]) == {"WIN_10011651", "WIN_10194711"}
    assert bottle_price.json()["grounded_product_ids"] == ["WIN_10194711"]
    assert "9.900đ" in bottle_price.json()["text"]
    assert larger_variant.json()["grounded_product_ids"] == ["WIN_10307381"]
    assert "15.900đ" in larger_variant.json()["text"]
    assert promotion_validity.json()["grounded_product_ids"] == ["WIN_10307381"]
    assert "chưa xác minh" in promotion_validity.json()["text"].casefold()
    assert "đang áp dụng chắc chắn" not in promotion_validity.json()["text"].casefold()
    assert stock_confirmation.json()["grounded_product_ids"] == ["WIN_10307381"]
    assert "tồn hiện tại chưa được xác minh" in stock_confirmation.json()["text"].casefold()


def test_unspecified_spice_is_unknown_not_non_spicy() -> None:
    product = ProductSnapshot(
        product_id="UNKNOWN_SPICE",
        name="Product",
        category="snack",
        price_vnd=1000,
        stock=1,
    )

    assert product.spicy is None
