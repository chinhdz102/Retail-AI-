from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import backend.api.main as api
from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.catalog_preferences import preparation_terms
from backend.services.retail_tools import ToolObservation
from backend.services.shopping_flow import ShoppingActionType, classify_shopping_action
from backend.shared.contracts import SessionState


@pytest.fixture(scope="module")
def snapshot():
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/winmart_products.json"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["Gợi ý dầu gội", "Giới thiệu khăn mặt", "Gợi ý nước giặt"])
async def test_price_and_rank_followups_preserve_actual_candidates(snapshot, monkeypatch, first):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "ranking-context"})).json()[
            "session_id"
        ]

        async def ask(text):
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": text}
            )
            assert response.status_code == 200, response.text
            return response.json()

        original = await ask(first)
        assert len(original["products"]) >= 2
        expected = min(item["price_vnd"] for item in original["products"])
        cheapest = await ask("loại nào rẻ nhất")
        assert cheapest["products"]
        assert all(item["price_vnd"] == expected for item in cheapest["products"])
        assert all(
            item["product_id"] in original["grounded_product_ids"] for item in cheapest["products"]
        )
        assert "vừa hiển thị" in cheapest["text"]
        lowest = await ask("giá nào thấp nhất")
        assert lowest["products"]
        assert all(item["price_vnd"] == expected for item in lowest["products"])
        regional = await ask("cái mô rẻ nhứt")
        assert regional["products"]
        assert all(item["price_vnd"] == expected for item in regional["products"])
        bestseller = await ask("loại nào là best seller")
        assert "bán chạy nhất" in bestseller["text"]
        assert "nhóm hàng nào" not in bestseller["text"]
        assert "đánh giá vị" not in bestseller["text"]
        assert bestseller["grounded_product_ids"] == original["grounded_product_ids"]
        taste = await ask("loại nào ngon nhất")
        assert "đánh giá" in taste["text"]
        assert "vị nào" not in taste["text"]
        assert "bán chạy" not in taste["text"]
        assert "nhóm hàng nào" not in taste["text"]
        assert taste["grounded_product_ids"] == original["grounded_product_ids"]
        best = await ask("loại nào tốt nhứt?")
        assert "đánh giá" in best["text"] and "đã xác minh" in best["text"]
        assert best["grounded_product_ids"] == original["grounded_product_ids"]
        for ranking_reply in (bestseller, taste, best):
            assert all(
                product_id in original["grounded_product_ids"]
                for product_id in ranking_reply["grounded_product_ids"]
            )
            assert "chưa có" in ranking_reply["text"]


@pytest.mark.asyncio
async def test_new_category_cheapest_question_uses_new_group_and_global_minimum(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "change-price-scope"})).json()[
            "session_id"
        ]
        first = await client.post(
            "/agent/respond", json={"session_id": session, "text": "Gợi ý dầu gội"}
        )
        assert first.json()["products"]
        second = await client.post(
            "/agent/respond",
            json={
                "session_id": session,
                "text": "Khăn mặt nào rẻ nhất?",
            },
        )
        assert second.status_code == 200
        payload = second.json()
        towels = []
        for offset in range(0, 100_000, 20):
            page = snapshot.search(
                query="",
                category="Khăn mặt",
                limit=20,
                offset=offset,
            )
            towels.extend(page)
            if len(page) < 20:
                break
        minimum = min(product.price_vnd for product in towels)
        expected_ids = {
            product.product_id for product in towels if product.price_vnd == minimum
        }
        assert set(payload["grounded_product_ids"]) == expected_ids
        assert {product["price_vnd"] for product in payload["products"]} == {minimum}
        assert "giá tham khảo thấp nhất" in payload["text"]
        assert "Dầu gội" not in payload["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "category"),
    [
        ("Trong các loại dầu gội, loại nào có giá thấp nhất?", "Dầu gội"),
        ("Khăn mặt nào rẻ nhất?", "Khăn mặt"),
        ("Nước giặt nào có giá thấp nhất?", "Nước giặt"),
    ],
)
async def test_explicit_category_cheapest_returns_catalog_global_minimum(
    snapshot, monkeypatch, query, category
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    items = []
    for offset in range(0, 100_000, 20):
        page = snapshot.search(
            query="",
            category=category,
            limit=20,
            offset=offset,
        )
        items.extend(page)
        if len(page) < 20:
            break
    assert items
    minimum = min(product.price_vnd for product in items)
    expected_ids = {
        product.product_id for product in items if product.price_vnd == minimum
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (
            await client.post("/sessions", json={"kiosk_id": f"cheapest-{category}"})
        ).json()["session_id"]
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": query},
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["grounded_product_ids"]) == expected_ids
    assert {product["price_vnd"] for product in payload["products"]} == {minimum}
    assert "giá tham khảo thấp nhất" in payload["text"]
    assert "snapshot ngày" in payload["text"]


@pytest.mark.asyncio
async def test_specific_meat_cut_is_not_broadened_to_cheapest_meat(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    expected = snapshot.search(query="Thịt Thăn bò", category="Thịt", limit=10)
    assert expected

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (
            await client.post("/sessions", json={"kiosk_id": "specific-beef-cut"})
        ).json()["session_id"]
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "thịt thăn bò loại nào giá thấp nhất?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["grounded_product_ids"]) == {
        product.product_id for product in expected
    }
    assert all("thăn bò" in product["name"].casefold() for product in payload["products"])
    assert all("gà viên" not in product["name"].casefold() for product in payload["products"])


@pytest.mark.asyncio
async def test_unscoped_cheapest_request_asks_for_category_instead_of_offering_yes_no(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "unscoped-cheapest"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "tìm sản phẩm giá thấp nhất giúp tôi"},
        )
        assert response.status_code == 200
        assert "nhóm hàng nào" in response.json()["text"]
        assert not response.json()["products"]


@pytest.mark.asyncio
async def test_natural_shelf_lookup_keeps_vietnamese_product_terms(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "thăn-kệ"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond", json={"session_id": session, "text": "thịt thăn bò ở đâu"}
        )
        payload = response.json()
        item = snapshot.get_product_snapshot("WIN_10053675")
        assert response.status_code == 200
        assert payload["grounded_product_ids"] == [item.product_id]
        assert item.shelf in payload["text"]
        assert "đề xuất" in payload["text"]


@pytest.mark.asyncio
async def test_missing_product_variant_is_explained_without_claiming_a_comparison(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "egg-variant-gap"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session,
                "text": "Trứng gà ta với trứng gà công nghiệp có loại nào?",
            },
        )

    payload = response.json()
    assert response.status_code == 200, response.text
    assert payload["tools_used"] == ["recommend_products"]
    assert len(payload["products"]) == 1
    assert "Trứng gà ta" in payload["products"][0]["name"]
    assert "Trứng gà công nghiệp" not in payload["products"][0]["name"]
    assert "các biến thể hoặc chi tiết còn lại" in payload["text"]
    assert "chưa thể kết luận hay so sánh đầy đủ" in payload["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "positive_category", "positive_title", "excluded_title"),
    [
        (
            "Mình muốn nước khoáng, không phải nước ngọt có ga nha.",
            "nước khoáng",
            "nước khoáng",
            "nước ngọt",
        ),
        (
            "Mình muốn khăn giấy, không phải khăn ướt nhé.",
            "khăn giấy gói",
            "khăn giấy",
            "khăn ướt",
        ),
        (
            "Mình muốn sữa tươi, không phải sữa hạt nhé.",
            "sữa tươi",
            "sữa tươi",
            "sữa hạt",
        ),
        (
            "Mình muốn mì ăn liền, không phải mì ly nhé.",
            "mì ăn liền",
            "mì",
            "mì ly",
        ),
    ],
)
async def test_customer_correction_keeps_positive_scope_through_agent_response(
    snapshot,
    monkeypatch,
    query: str,
    positive_category: str,
    positive_title: str,
    excluded_title: str,
) -> None:
    class NoModelNeeded:
        async def chat(self, *_args, **_kwargs):
            raise AssertionError("Catalog-backed category correction must stay deterministic")

    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    api.app.dependency_overrides[api.get_groq_service] = lambda: NoModelNeeded()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
        ) as client:
            session = (
                await client.post(
                    "/sessions",
                    json={
                        "kiosk_id": (
                            f"negative-scope-{normalize_category_text(positive_category)}"
                        )
                    },
                )
            ).json()["session_id"]
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": query}
            )

        payload = response.json()
        assert response.status_code == 200, response.text
        assert payload["tools_used"] == ["search_products"]
        assert payload["grounded_product_ids"]
        assert payload["products"]
        products = [
            snapshot.get_product_snapshot(product_id)
            for product_id in payload["grounded_product_ids"]
        ]
        assert all(product is not None for product in products)
        assert all(
            normalize_category_text(positive_category)
            in normalize_category_text(product.category_hierarchy)
            for product in products
        )
        assert all(
            normalize_category_text(excluded_title)
            not in normalize_category_text(product.name)
            for product in products
        )
        assert any(
            normalize_category_text(positive_title) in normalize_category_text(product.name)
            for product in products
        )
    finally:
        api.app.dependency_overrides.pop(api.get_groq_service, None)


@pytest.mark.asyncio
async def test_natural_category_comparison_routes_to_grounded_product_clarification(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "natural-comparison"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session,
                "text": "Nước tinh khiết đóng chai với nước khoáng có gì khác theo nhãn?",
            },
        )

        payload = response.json()
        assert response.status_code == 200, response.text
        assert payload["model"] == "comparison-clarification"
        assert payload["tools_used"] == ["search_products"]
        assert payload["grounded_product_ids"]
        products = [
            snapshot.get_product_snapshot(product_id)
            for product_id in payload["grounded_product_ids"]
        ]
        assert all(product is not None for product in products)
        categories = {
            product.category_hierarchy.split(" > ")[-1]
            for product in products
            if product is not None
        }
        assert "Nước tinh khiết" in categories
        assert "Nước khoáng" in categories
        assert "Aquafina" in payload["text"] or "Dasani" in payload["text"]
        assert "LaVie" in payload["text"] or "Faith" in payload["text"]
        assert "Bạn chọn tên hoặc quy cách một món ở mỗi bên" in payload["text"]
        assert "rẻ hơn" not in payload["text"]
        assert "thành phần khoáng" not in payload["text"]


@pytest.mark.asyncio
async def test_pocket_tissue_shelf_query_preserves_compound_product_phrase(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "pocket-tissue-shelf"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "Khăn giấy bỏ túi ở kệ nào?"},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["grounded_product_ids"]
        items = [
            snapshot.get_product_snapshot(product_id)
            for product_id in payload["grounded_product_ids"]
        ]
        assert all(item is not None for item in items)
        assert all(
            "Khăn giấy gói" in item.category_hierarchy for item in items if item is not None
        )
        assert any(item.shelf and item.shelf in payload["text"] for item in items if item)


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["Gợi ý dầu gội", "Gợi ý nước giặt", "Giới thiệu khăn mặt"])
async def test_unverified_bestseller_still_shows_grounded_category_options(
    snapshot, monkeypatch, first
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "ranking-followup"})).json()[
            "session_id"
        ]
        initial = (
            await client.post("/agent/respond", json={"session_id": session, "text": first})
        ).json()
        response = (
            await client.post(
                "/agent/respond", json={"session_id": session, "text": "loại nào bán chạy nhất?"}
            )
        ).json()
        assert initial["grounded_product_ids"]
        assert response["grounded_product_ids"] == initial["grounded_product_ids"]
        assert "bán chạy nhất" in response["text"]
        assert all(product["name"] in response["text"] for product in initial["products"][:3])
        assert "chưa có số liệu bán đã xác minh" in response["text"]


@pytest.mark.asyncio
async def test_price_comparison_does_not_guess_if_one_snapshot_is_unavailable(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "price-data-missing"})).json()[
            "session_id"
        ]
        first = await client.post(
            "/agent/respond", json={"session_id": session, "text": "Gợi ý dầu gội"}
        )
        assert len(first.json()["products"]) > 1

        async def unavailable(*args, **kwargs):
            return ToolObservation(
                content={}, tool_name="get_product_snapshot", status="TEMPORARY_FAILURE"
            )

        monkeypatch.setattr(api, "lookup_followup_snapshot", unavailable)
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "loại nào rẻ nhất"},
        )
        assert response.status_code == 200
        assert "chưa xác minh đủ giá" in response.json()["text"]
        assert not response.json()["products"]


@pytest.mark.asyncio
async def test_one_shown_product_is_not_presented_as_a_meaningful_price_ranking():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "only-one-option"})).json()[
            "session_id"
        ]
        first = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "Tìm snack không cay"},
        )
        assert len(first.json()["products"]) == 1
        second = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "loại nào rẻ nhất"},
        )
        assert "một lựa chọn" in second.json()["text"]
        assert "thấp nhất" not in second.json()["text"]


@pytest.mark.parametrize(
    "text", ["cho t thanh toán", "cho mình tính tiền nhé", "thanh toán", "tính tiền"]
)
def test_direct_checkout_language_is_an_action_even_with_empty_cart(text):
    action = classify_shopping_action(text, SessionState.BROWSING)
    assert action is not None and action.action == ShoppingActionType.PREPARE_CHECKOUT


@pytest.mark.parametrize("text", ["thanh toán bằng tiền mặt được không", "tôi đã thanh toán xong"])
def test_payment_method_or_past_payment_is_not_a_checkout_request(text):
    action = classify_shopping_action(text, SessionState.BROWSING)
    assert action is None or action.action != ShoppingActionType.PREPARE_CHECKOUT


@pytest.mark.asyncio
async def test_checkout_request_on_empty_cart_gives_actionable_response_and_no_order(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "checkout-language"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond", json={"session_id": session, "text": "cho t thanh toán"}
        )
        payload = response.json()
        assert response.status_code == 200
        assert "giỏ hàng đang trống" in payload["text"].casefold()
        assert "quầy" in payload["text"].casefold()
        assert payload["order"] is None and payload["checkout_quote"] is None


@pytest.mark.asyncio
async def test_direct_checkout_of_demo_cart_requires_explicit_confirmation():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "checkout-demo-cart"})).json()[
            "session_id"
        ]
        added = await client.post(
            f"/sessions/{session}/cart/items",
            json={"product_id": "COKE_ZERO_330", "quantity": 1},
        )
        assert added.status_code == 200, added.text
        checkout = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "cho t thanh toán"},
        )
        assert checkout.status_code == 200, checkout.text
        payload = checkout.json()
        assert payload["checkout_quote"] is not None
        assert payload["order"] is None
        assert payload["state"] == "CHECKOUT_CONFIRMATION"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("thịt nhúng lẩu", {"lau"}),
        ("rau để xào", {"xao"}),
        ("cá để nướng", {"nuong"}),
        ("gà dùng để hấp", {"hap"}),
        ("khăn lau nhà", set()),
        ("Tôi muốn khăn mặt để lau mặt, giới thiệu vài cái", set()),
        ("thit de nau lau", {"lau"}),
        ("chọn thịt nhưng lâu rồi", set()),
    ],
)
def test_preparation_recognition_is_not_tied_to_a_product(text, expected):
    assert preparation_terms(text) == expected


def test_cross_branch_preparation_search_preserves_price_and_excluded_brand(snapshot):
    def search(text, **extra):
        arguments = {"category": "Thịt", "preference_text": text, **extra}
        return retail_tools.execute_catalog_tool(
            "recommend_products", json.dumps(arguments, ensure_ascii=False), repository=snapshot
        ).products

    baseline = search("Tìm thịt để ăn lẩu")
    assert baseline
    price = min(product.price_vnd for product in baseline)
    limited = search("Tìm thịt để ăn lẩu", price_constraint={"operator": "LT", "value_vnd": price})
    assert all(product.price_vnd < price for product in limited)
    brand = baseline[0].brand
    excluding = search(f"Tìm thịt để ăn lẩu, không lấy hãng {brand}")
    assert all(product.brand.casefold() != brand.casefold() for product in excluding)


@pytest.mark.asyncio
async def test_cooking_equipment_category_is_not_a_food_preparation_constraint(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "cookware"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={"session_id": session, "text": "Giới thiệu giúp tôi đồ dùng nấu nướng"},
        )
        assert response.status_code == 200
        assert response.json()["products"]
        for product in response.json()["products"]:
            item = snapshot.get_product_snapshot(product["product_id"])
            assert "Đồ dùng nấu nướng" in item.category_hierarchy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "thế t mua thịt nhúng lẩu m tư vấn cho t thịt nào ngon",
        "Tư vấn thịt để nướng cho mình",
        "Mình cần rau để xào, cho vài gợi ý",
    ],
)
async def test_cooking_use_is_never_inferred_from_an_unrelated_title(
    snapshot, monkeypatch, question
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "preparation-test"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond", json={"session_id": session, "text": question}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["products"]:
            for product in payload["products"]:
                assert preparation_terms(question) & set(
                    api._normalize_request_text(product["name"]).split()
                )
        else:
            assert "xác nhận" in payload["text"] or "chưa tìm" in payload["text"]


@pytest.mark.asyncio
async def test_cooking_goal_survives_an_ingredient_reply_and_resets_on_new_department(
    snapshot,
    monkeypatch,
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (
            await client.post("/sessions", json={"kiosk_id": "preparation-followup"})
        ).json()["session_id"]

        async def ask(text):
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": text}
            )
            assert response.status_code == 200, response.text
            return response.json()

        first = await ask("Tư vấn thịt nhúng lẩu giúp mình")
        assert first["products"]
        assert all("lẩu" in p["name"].casefold() for p in first["products"])
        assert "tên hàng" in first["text"].casefold()
        repeated = await ask("ăn lẩu")
        assert repeated["products"]
        assert all("lẩu" in p["name"].casefold() for p in repeated["products"])
        second = await ask("mình thích thịt bò")
        if second["products"]:
            assert all("lẩu" in p["name"].casefold() for p in second["products"])
            assert all("bò" in p["name"].casefold() for p in second["products"])
            assert "tên hàng" in second["text"]
        else:
            assert "ghi nhận" in second["text"] and "cách chế biến" in second["text"]
        switched = await ask("giới thiệu khăn mặt cho mình")
        assert switched["products"]
        assert all("khăn mặt" in p["name"].casefold() for p in switched["products"])


@pytest.mark.asyncio
async def test_personal_care_condition_refines_current_category_without_inventing_fit(
    snapshot,
    monkeypatch,
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://test",
        timeout=60,
    ) as client:
        session = (
            await client.post("/sessions", json={"kiosk_id": "personal-care-context"})
        ).json()["session_id"]

        async def ask(text):
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": text}
            )
            assert response.status_code == 200, response.text
            return response.json()

        initial = await ask("Tư vấn kem dưỡng da mặt giúp mình")
        condition = await ask("Da mặt mình hay khô căng sau khi rửa")
        refinement = await ask("Đừng lấy Pond’s, ưu tiên da khô")

    assert initial["products"]
    assert condition["products"]
    assert refinement["products"]
    for payload in (condition, refinement):
        for item in payload["products"]:
            product = snapshot.get_product_snapshot(item["product_id"])
            assert product is not None
            assert product.category_hierarchy.endswith("Kem dưỡng da mặt")
    assert all("pond's" not in item["name"].casefold() for item in refinement["products"])
    assert "Catalog chưa có đủ thông tin xác minh" in condition["text"]
    assert "Catalog chưa có đủ thông tin xác minh" in refinement["text"]
    assert "một sản phẩm trong nhóm này" in condition["text"]
    assert "Mình chưa có dữ liệu đã xác minh" not in condition["text"]
    assert "Mình chưa có dữ liệu đã xác minh" not in refinement["text"]
    assert "Mình gợi ý" not in condition["text"]
    assert "Mình gợi ý" not in refinement["text"]


@pytest.mark.asyncio
async def test_hotpot_other_types_are_real_new_products_with_uncertainty(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "alternative"})).json()[
            "session_id"
        ]
        first = (
            await client.post(
                "/agent/respond",
                json={"session_id": session, "text": "tôi muốn tìm thịt để ăn lẩu"},
            )
        ).json()
        other = (
            await client.post(
                "/agent/respond", json={"session_id": session, "text": "có các loại thịt khác ko"}
            )
        ).json()
        assert first["products"] and other["products"]
        assert not set(first["grounded_product_ids"]) & set(other["grounded_product_ids"])
        assert "chưa xác nhận" in other["text"]
        for card in other["products"]:
            product = snapshot.get_product_snapshot(card["product_id"])
            assert product and product.price_vnd == card["price_vnd"]
            assert "thịt" in product.category_hierarchy.casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first,followup",
    [
        ("Gợi ý dầu gội", "có loại dầu gội khác không"),
        ("Giới thiệu nước giặt", "có loại nước giặt khác ko"),
        ("Giới thiệu khăn mặt", "các loại khăn mặt khác không"),
    ],
)
async def test_other_products_do_not_repeat_previous_candidates(
    snapshot, monkeypatch, first, followup
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "different-products"})).json()[
            "session_id"
        ]
        initial = (
            await client.post("/agent/respond", json={"session_id": session, "text": first})
        ).json()
        other = (
            await client.post("/agent/respond", json={"session_id": session, "text": followup})
        ).json()
        assert initial["products"] and other["products"]
        assert not set(initial["grounded_product_ids"]) & set(other["grounded_product_ids"])
        for card in other["products"]:
            product = snapshot.get_product_snapshot(card["product_id"])
            assert (
                product and card["name"] == product.name and card["price_vnd"] == product.price_vnd
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first,followup,category",
    [
        ("Gợi ý dầu gội", "có loại dầu gội khác không", "dầu gội"),
        ("Giới thiệu nước giặt", "có loại nước giặt khác ko", "nước giặt"),
    ],
)
async def test_repeated_alternatives_exclude_every_previously_shown_product(
    snapshot, monkeypatch, first, followup, category
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    available = snapshot.search(
        query="", category=category, limit=100, diversify_by_subcategory=False
    )
    assert len(available) >= 10

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session_response = await client.post(
            "/sessions", json={"kiosk_id": "alternative-pagination"}
        )
        session = session_response.json()["session_id"]

        async def ask(text):
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": text}
            )
            assert response.status_code == 200, response.text
            return response.json()

        pages = [await ask(first)]
        for _ in range(3):
            pages.append(await ask(followup))

        # The pagination state is scoped to the current category/query and
        # must not turn into a cross-category deny-list.
        switched = await ask("Gợi ý khăn mặt")
        assert switched["products"]
        state = api.session_store.get(api.UUID(session)).shopping
        assert state.seen_product_scope
        assert set(state.seen_product_ids) == set(switched["grounded_product_ids"])
        cart = await client.get(f"/sessions/{session}/cart")
        assert cart.status_code == 200
        assert cart.json()["item_count"] == 0
        assert api.session_store.get(api.UUID(session)).shopping.cart_reference is None

    page_ids = [set(page["grounded_product_ids"]) for page in pages]
    assert all(page_ids)
    for index, current_ids in enumerate(page_ids[1:], start=1):
        previously_shown = set().union(*page_ids[:index])
        assert not previously_shown & current_ids
    assert len(set().union(*page_ids)) == sum(map(len, page_ids))


@pytest.mark.asyncio
async def test_return_to_original_candidate_resolves_product_id_and_price(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "return-original"})).json()[
            "session_id"
        ]

        async def ask(text):
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": text}
            )
            assert response.status_code == 200, response.text
            return response.json()

        first_page = await ask("Gợi ý dầu gội")
        assert len(first_page["grounded_product_ids"]) >= 2
        original_product_id = first_page["grounded_product_ids"][0]
        original_product = snapshot.get_product_snapshot(original_product_id)
        assert original_product is not None

        second_page = await ask("có loại dầu gội khác không")
        assert second_page["grounded_product_ids"]
        assert not set(first_page["grounded_product_ids"]) & set(
            second_page["grounded_product_ids"]
        )
        browse_memory = api.session_store.get(api.UUID(session)).shopping
        assert browse_memory.displayed_product_pages[0] == first_page["grounded_product_ids"]
        assert browse_memory.original_displayed_product_ids == first_page["grounded_product_ids"]

        restored = await ask("Quay lại món ban đầu, giá bao nhiêu?")
        cart = await client.get(f"/sessions/{session}/cart")

    assert restored["grounded_product_ids"] == [original_product_id]
    assert len(restored["products"]) == 1
    assert f"{original_product.price_vnd:,}".replace(",", ".") in restored["text"]
    assert cart.status_code == 200
    assert cart.json()["item_count"] == 0


@pytest.mark.asyncio
async def test_exhausted_alternatives_are_explained_without_claiming_catalog_failure(
    snapshot, monkeypatch
):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    towel_count = len(
        snapshot.search(query="", category="khăn mặt", limit=100, diversify_by_subcategory=False)
    )
    assert 3 < towel_count < 100

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session_response = await client.post(
            "/sessions", json={"kiosk_id": "alternative-exhaustion"}
        )
        session = session_response.json()["session_id"]

        async def ask(text):
            response = await client.post(
                "/agent/respond", json={"session_id": session, "text": text}
            )
            assert response.status_code == 200, response.text
            return response.json()

        pages = [await ask("Gợi ý khăn mặt")]
        while (
            len(set().union(*(set(page["grounded_product_ids"]) for page in pages)))
            < towel_count
        ):
            page = await ask("các loại khăn mặt khác không")
            if not page["grounded_product_ids"]:
                break
            pages.append(page)
        exhausted = await ask("các loại khăn mặt khác không")

    assert not exhausted["grounded_product_ids"]
    assert "chưa tìm thấy thêm sản phẩm mới" in exhausted["text"].casefold()


@pytest.mark.asyncio
async def test_ordinal_product_reference_is_isolated_between_sessions(snapshot, monkeypatch):
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60
    ) as client:
        session_a = (
            await client.post("/sessions", json={"kiosk_id": "ordinal-isolation-a"})
        ).json()["session_id"]
        session_b = (
            await client.post("/sessions", json={"kiosk_id": "ordinal-isolation-b"})
        ).json()["session_id"]
        listed = await client.post(
            "/agent/respond",
            json={
                "session_id": session_a,
                "text": "Mình đang cần Bánh bao, cho mình xem vài loại.",
            },
        )
        assert listed.status_code == 200, listed.text
        first_id = listed.json()["grounded_product_ids"][0]
        product = snapshot.get_product_snapshot(first_id)
        assert product is not None

        foreign_reference = await client.post(
            "/agent/respond",
            json={"session_id": session_b, "text": "Giá của loại đầu tiên là bao nhiêu?"},
        )
        own_reference = await client.post(
            "/agent/respond",
            json={"session_id": session_a, "text": "Giá của loại đầu tiên là bao nhiêu?"},
        )

    assert foreign_reference.status_code == 200, foreign_reference.text
    assert foreign_reference.json()["grounded_product_ids"] == []
    assert own_reference.status_code == 200, own_reference.text
    assert own_reference.json()["grounded_product_ids"] == [first_id]
    assert f"{product.price_vnd:,}".replace(",", ".") in own_reference.json()["text"]
