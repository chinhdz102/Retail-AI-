from uuid import UUID

import httpx
import pytest

from backend.api import main
from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services import retail_tools
from backend.services.consultation import use_consultation
from backend.services.groq import GroqProviderError
from backend.services.product_prices import lookup_price_products
from backend.services.vietnamese_intent import requested_snapshot_fields


@pytest.mark.parametrize(
    "text, query",
    [
        ("Ờ ờ các loại thịt giá như thế nào?", "thit"),
        ("Cho mình xem giá các loại thịt.", "thit"),
        ("Thịt heo giá sao?", "thit heo"),
        ("Thịt bò bao tiền?", "thit bo"),
        ("Các loại dầu gội giá sao?", "dau goi"),
        ("Giá các loại nước giặt như thế nào?", "nuoc giat"),
        ("Sữa Sao Mai giá sao?", "sua sao mai"),
    ],
)
def test_conversational_price_request_keeps_query_and_avoids_advice(text, query):
    assert requested_snapshot_fields(text) == ("price_vnd",)
    assert not use_consultation(text, ("price_vnd",))
    assert main._fact_product_query(text) == query


@pytest.mark.parametrize(
    "text",
    [
        "Tư vấn thịt nào ngon?",
        "Mua thịt dưới 200 nghìn",
        "200 nghìn mua đồ ăn tối cho hai người",
        "Loại này giá cao hơn có đáng tiền không?",
    ],
)
def test_advice_and_budget_still_use_consultation(text):
    assert use_consultation(text, requested_snapshot_fields(text))
    assert main._broad_price_lookup_arguments(text, requested_snapshot_fields(text)) is None


@pytest.mark.parametrize(
    "text, is_edit",
    [
        ("Các loại sữa tắm giá như thế nào?", False),
        ("Sữa tươi giá bao nhiêu?", False),
        ("Sửa giá sữa tắm thành 1.000đ", True),
        ("Cập nhật giá sữa tươi", True),
    ],
)
def test_price_edit_detection_distinguishes_milk_from_edit_verb(text, is_edit):
    assert main._administrative_action_requested(text) is is_edit


def test_failure_reason_does_not_confuse_rate_limit_with_other_provider_errors():
    busy = main._chat_provider_http_error(
        GroqProviderError(
            "raw secret",
            fallback_trace=[
                {"http_status": 429, "provider_code": "rate_limit_exceeded"},
                {"status": "cooldown"},
            ],
        )
    )
    assert busy.detail["code"] == "assistant_busy"
    invalid = main._chat_provider_http_error(
        GroqProviderError(
            "raw secret",
            fallback_trace=[
                {"http_status": 400, "provider_code": "json_validate_failed"},
            ],
        )
    )
    assert invalid.detail["code"] == "assistant_unavailable"
    assert "raw secret" not in str(busy.detail) + str(invalid.detail)


@pytest.fixture
def price_api(monkeypatch):
    settings = Settings(catalog_repository="snapshot")
    repository = get_product_repository(settings)

    class NoModel:
        async def chat(self, *args, **kwargs):
            raise AssertionError(
                "Price lookups must work even if all provider keys are unavailable"
            )

    main.app.dependency_overrides[main.get_settings] = lambda: settings
    main.app.dependency_overrides[main.get_groq_service] = lambda: NoModel()
    monkeypatch.setattr(main, "get_product_repository", lambda *args: repository)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: repository)
    yield repository
    main.app.dependency_overrides.pop(main.get_settings, None)
    main.app.dependency_overrides.pop(main.get_groq_service, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, category",
    [
        ("Ờ ờ các loại thịt giá như thế nào?", "Thịt"),
        ("Thịt heo giá sao?", "Thịt"),
        ("Cho mình xem giá các loại dầu gội.", "Dầu gội"),
        ("Các loại sữa tắm giá như thế nào?", "Sữa tắm"),
        ("Các loại nước giặt giá sao?", "Nước Giặt"),
        ("Các loại rau giá sao?", "Rau"),
        ("Các loại sữa giá như thế nào?", "Sữa"),
    ],
)
async def test_real_catalog_price_lists_answer_without_llm(price_api, text, category):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "price-regression"})).json()
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session["session_id"],
                "text": text,
                "language": "vi",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "catalog"
    assert 1 <= len(payload["products"]) <= 5
    assert len(payload["products"]) == len(payload["grounded_product_ids"])
    if category in {"Thịt", "Rau"}:
        assert all(p["category"].startswith(category) for p in payload["products"])
    else:
        assert all(category.casefold() in p["name"].casefold() for p in payload["products"])
        if category == "Sữa":
            assert all("tắm" not in p["name"].casefold() for p in payload["products"])
    for card in payload["products"]:
        product = price_api.get_product_snapshot(card["product_id"])
        assert product.name in payload["text"]
        assert f"{product.price_vnd:,}".replace(",", ".") + "đ" in payload["text"]
        assert f"/{product.uom.lower()}" in payload["text"]
    assert "cân nhắc nguồn gốc" not in payload["text"]
    assert "catalog" not in payload["text"].lower()


@pytest.mark.asyncio
async def test_category_price_list_excludes_products_that_only_contain_meat(price_api):
    observation = await lookup_price_products(price_api, "thit")
    assert all(p.category == "Thịt" for p in observation.products)
    assert any("heo" in p.name.lower() for p in observation.products)
    assert any("gà" in p.name.lower() for p in observation.products)
    assert any("bò" in p.name.lower() for p in observation.products)


@pytest.mark.parametrize(
    "text,order",
    [
        ("Giá thấp nhất của nước suối là bao nhiêu?", "min"),
        ("Giá cao nhất của nước suối là bao nhiêu?", "max"),
        ("Giá thịt thấp nhất và cao nhất khoảng bao nhiêu?", "range"),
        ("Trà có mức giá thấp đến cao nào?", "range"),
    ],
)
def test_extreme_price_parser_removes_ranking_words(text, order):
    fields = requested_snapshot_fields(text)
    arguments = main._broad_price_lookup_arguments(text, fields)
    assert arguments is not None
    assert arguments["price_order"] == order
    assert "nhat" not in arguments["query"]


@pytest.mark.asyncio
async def test_extreme_price_answer_sorts_the_full_matching_catalog_without_llm(price_api):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test",
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "price-extrema"})).json()
        response = await client.post("/agent/respond", json={
            "session_id": session["session_id"],
            "text": "Giá thấp nhất và cao nhất của nước suối là bao nhiêu?",
            "language": "vi",
        })
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "catalog"
    assert len(payload["products"]) == 2
    prices = [card["price_vnd"] for card in payload["products"]]
    assert prices == [min(prices), max(prices)]
    assert "Thấp nhất:" in payload["text"]
    assert "Cao nhất:" in payload["text"]


@pytest.mark.asyncio
async def test_tea_low_to_high_price_answer_uses_real_tea_matches(price_api):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test",
    ) as client:
        session = (await client.post("/sessions", json={"kiosk_id": "price-tea-range"})).json()
        response = await client.post("/agent/respond", json={
            "session_id": session["session_id"],
            "text": "Trà trong cửa hàng có những mức giá thấp đến cao nào?",
            "language": "vi",
        })
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "catalog"
    assert len(payload["products"]) == 2
    assert "Thấp nhất:" in payload["text"]
    assert "Cao nhất:" in payload["text"]
    assert all("trà" in card["name"].casefold() for card in payload["products"])


def test_low_to_high_price_query_keeps_only_the_product_type():
    text = "Trà trong cửa hàng có những mức giá thấp đến cao nào?"
    arguments = main._broad_price_lookup_arguments(text, requested_snapshot_fields(text))
    assert arguments == {"query": "tra", "list_requested": True, "price_order": "range"}


@pytest.mark.asyncio
async def test_broad_price_lookup_clears_old_single_product_reference(price_api):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        session_id = (await client.post("/sessions", json={"kiosk_id": "price-context"})).json()[
            "session_id"
        ]
        old = price_api.get_product_snapshot("WIN_10005885")
        main.session_store.set_last_grounded_product(UUID(session_id), old.product_id, old.name)
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Các loại thịt giá như thế nào?",
            },
        )
    assert response.status_code == 200
    assert main.session_store.get(UUID(session_id)).last_grounded_product_id is None


@pytest.mark.asyncio
async def test_named_brand_without_variant_does_not_quote_one_arbitrary_price(price_api):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        session_id = (await client.post("/sessions", json={"kiosk_id": "brand-price"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Coca Zero giá sao?",
            },
        )
    assert response.status_code == 200
    assert "muốn hỏi đúng vị hoặc khối lượng nào" in response.json()["text"]
    assert "13.000đ" not in response.json()["text"]


@pytest.mark.asyncio
async def test_full_product_name_remembers_exact_item_for_next_price_question(price_api):
    product = price_api.get_product_snapshot("WIN_10005885")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        session_id = (await client.post("/sessions", json={"kiosk_id": "exact-price"})).json()[
            "session_id"
        ]
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": f"{product.name} giá sao?",
            },
        )
        assert response.status_code == 200
        assert response.json()["grounded_product_ids"] == [product.product_id]
        assert "30.700đ" in response.json()["text"]
        followup = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Giá bao nhiêu?",
            },
        )
    assert followup.status_code == 200
    assert followup.json()["grounded_product_ids"] == [product.product_id]
