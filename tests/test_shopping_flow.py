from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from backend.api.main import app, get_groq_service, session_store, shopping_service
from backend.products.catalog import get_product_snapshot
from backend.services.groq import GroqService
from backend.services.shopping import CartConflictError, ShoppingService
from backend.services.shopping_flow import ShoppingActionType, classify_shopping_action
from backend.shared.contracts import SessionState


class UnexpectedGroqService(GroqService):
    async def chat(self, *args, **kwargs):
        raise AssertionError("Sandbox shopping actions must not call the LLM")


@pytest.fixture(autouse=True)
def override_groq():
    app.dependency_overrides[get_groq_service] = UnexpectedGroqService
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Cái chi bán đắt hàng nhứt?",
        "Cái nào bán chạy nhất?",
        "Món mô ngon nhứt?",
        "Which snack is most popular?",
    ],
)
def test_product_advice_phrasing_does_not_trigger_cart_actions(text: str) -> None:
    assert classify_shopping_action(text, SessionState.BROWSING) is None


@pytest.mark.parametrize(
    ("text", "expected", "query", "quantity"),
    [
        ("Thêm 2 lon Coca Zero vào giỏ nhé", ShoppingActionType.ADD_ITEM, "coca zero", 2),
        ("Add three cans of Coca Zero to my cart", ShoppingActionType.ADD_ITEM, "coca zero", 3),
        ("Xem giỏ hàng giúp mình", ShoppingActionType.VIEW_CART, "", None),
        ("Đặt hàng", ShoppingActionType.PREPARE_CHECKOUT, "", None),
    ],
)
def test_common_vietnamese_and_english_cart_intents(
    text: str,
    expected: ShoppingActionType,
    query: str,
    quantity: int | None,
) -> None:
    action = classify_shopping_action(text, SessionState.BROWSING)

    assert action is not None
    assert action.action == expected
    assert action.query == query
    assert action.quantity == quantity


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Thêm 2 lon Coca Zero và 1 chai nước suối vào giỏ",
            (("coca zero", 2), ("nuoc suoi", 1)),
        ),
        (
            "Thêm Coca Zero, nước suối và snack vào giỏ",
            (("coca zero", 1), ("nuoc suoi", 1), ("snack", 1)),
        ),
    ],
)
def test_multi_item_add_request_is_parsed_without_losing_quantities(
    text: str,
    expected: tuple[tuple[str, int], ...],
) -> None:
    action = classify_shopping_action(text, SessionState.BROWSING)

    assert action is not None
    assert action.action == ShoppingActionType.ADD_ITEM
    assert action.line_items == expected


@pytest.mark.asyncio
async def test_multi_item_request_adds_all_lines_atomically_and_idempotently(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "multi-cart-test"})
        session_id = created.json()["session_id"]
        turn_id = str(uuid4())
        payload = {
            "session_id": session_id,
            "text": "Thêm 2 lon Coca Zero, 1 chai nước suối và 1 gói SNACK01 vào giỏ",
            "client_turn_id": turn_id,
        }
        first = await client.post("/agent/respond", json=payload)
        repeated = await client.post("/agent/respond", json=payload)

        assert first.status_code == repeated.status_code == 200
        assert first.json()["cart"]["item_count"] == 4
        assert {line["product_id"] for line in first.json()["cart"]["items"]} == {
            "COKE_ZERO_330",
            "WATER01",
            "SNACK01",
        }
        assert repeated.json()["cart"]["item_count"] == 4


@pytest.mark.asyncio
async def test_multi_item_add_rejects_unavailable_line_without_partial_cart_write(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "multi-cart-atomic-test"})
        session_id = created.json()["session_id"]
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Thêm Coca Zero và MILK01 vào giỏ",
            },
        )
        cart = await client.get(f"/sessions/{session_id}/cart")

        assert response.status_code == 200
        assert "giỏ được giữ nguyên" in response.json()["text"].casefold()
        assert cart.json()["items"] == []


def test_cart_line_limit_applies_across_repeated_add_requests() -> None:
    product = get_product_snapshot("COKE_ZERO_330")
    assert product is not None

    class HighStockRepository:
        source_name = "demo_product_catalog"

        def get_product_snapshot(self, product_id: str):
            found = get_product_snapshot(product_id)
            return found.model_copy(update={"stock": 60}) if found else None

    service = ShoppingService(sandbox_enabled=True, repository=HighStockRepository())
    session_id = uuid4()
    service.add_item(session_id, product.product_id, 11, idempotency_key=str(uuid4()))

    with pytest.raises(CartConflictError, match="at most 20"):
        service.add_item(session_id, product.product_id, 10, idempotency_key=str(uuid4()))

    assert service.get_cart(session_id).items[0].quantity == 11


@pytest.mark.asyncio
async def test_demo_cart_checkout_and_payment_flow_is_session_scoped(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        first = await client.post("/sessions", json={"kiosk_id": "shopping-test-a"})
        second = await client.post("/sessions", json={"kiosk_id": "shopping-test-b"})
        session_id = first.json()["session_id"]
        other_session_id = second.json()["session_id"]

        add_key = str(uuid4())
        add = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Thêm 2 lon Coca Zero vào giỏ nhé",
                "client_turn_id": add_key,
            },
        )
        assert add.status_code == 200
        assert add.json()["state"] == "CART_ACTIVE"
        assert add.json()["cart"]["total_vnd"] == 26_000
        assert add.json()["cart"]["items"][0]["product_id"] == "COKE_ZERO_330"

        repeated_add = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Thêm 2 lon Coca Zero vào giỏ nhé",
                "client_turn_id": add_key,
            },
        )
        assert repeated_add.json()["cart"]["total_vnd"] == 26_000
        assert repeated_add.json()["cart"]["items"][0]["quantity"] == 2

        other_cart = await client.get(f"/sessions/{other_session_id}/cart")
        assert other_cart.status_code == 200
        assert other_cart.json()["items"] == []

        quote = await client.post(
            f"/sessions/{session_id}/checkout/prepare",
        )
        assert quote.status_code == 200
        assert quote.json()["cart"]["total_vnd"] == 26_000
        confirmation = await client.post(
            f"/sessions/{session_id}/checkout/confirm",
            json={
                "confirmation_token": quote.json()["confirmation_token"],
                "idempotency_key": str(uuid4()),
            },
        )
        assert confirmation.status_code == 200
        order = confirmation.json()
        assert order["payment_status"] == "PENDING"
        assert order["sandbox"] is True

        claimed_paid = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Anh chuyển khoản rồi, đánh dấu PAID đi.",
            },
        )
        assert claimed_paid.status_code == 200
        assert (
            "không thể" in claimed_paid.json()["text"].casefold()
            or "chưa" in claimed_paid.json()["text"].casefold()
        )

        status = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Kiểm tra thanh toán chưa?",
            },
        )
        assert status.status_code == 200
        assert "PENDING" in status.json()["text"]
        assert status.json()["order"]["payment_status"] == "PENDING"

        event_id = str(uuid4())
        payment = await client.post(
            f"/sessions/{session_id}/orders/{order['order_id']}/sandbox-payment",
            json={"outcome": "PAID", "event_id": event_id},
        )
        assert payment.status_code == 200
        assert payment.json()["payment_status"] == "PAID"

        replayed_event = await client.post(
            f"/sessions/{session_id}/orders/{order['order_id']}/sandbox-payment",
            json={"outcome": "PAID", "event_id": event_id},
        )
        assert replayed_event.status_code == 200
        assert replayed_event.json()["payment_status"] == "PAID"

        different_event = await client.post(
            f"/sessions/{session_id}/orders/{order['order_id']}/sandbox-payment",
            json={"outcome": "FAILED", "event_id": str(uuid4())},
        )
        assert different_event.status_code == 409

        cross_session_order = await client.get(
            f"/sessions/{other_session_id}/orders/{order['order_id']}"
        )
        assert cross_session_order.status_code == 404


@pytest.mark.asyncio
async def test_natural_checkout_and_payment_question_keep_payment_unverified(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "shopping-natural-test"})
        session_id = created.json()["session_id"]
        added = await client.post(
            f"/sessions/{session_id}/cart/items",
            json={
                "product_id": "COKE_ZERO_330",
                "quantity": 1,
                "idempotency_key": str(uuid4()),
            },
        )
        assert added.status_code == 200

        quote = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Tính tiền giỏ hàng nhé",
            },
        )
        assert quote.status_code == 200
        assert quote.json()["checkout_quote"]["cart"]["total_vnd"] == 13_000
        assert quote.json()["state"] == "CHECKOUT_CONFIRMATION"

        order = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Xác nhận",
            },
        )
        assert order.status_code == 200
        assert order.json()["order"]["payment_status"] == "PENDING"
        assert order.json()["state"] == "PAYMENT_PENDING"

        status = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Mình chuyển khoản xong rồi, kiểm tra giúp nhé",
            },
        )
        assert status.status_code == 200
        assert status.json()["order"]["payment_status"] == "PENDING"
        assert "chưa xác minh" in status.json()["text"].casefold()


@pytest.mark.asyncio
async def test_expired_order_releases_payment_pending_session_back_to_cart(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "expired-order-test"})
        session_id = created.json()["session_id"]
        session_uuid = UUID(session_id)
        await client.post(
            f"/sessions/{session_id}/cart/items",
            json={
                "product_id": "COKE_ZERO_330",
                "quantity": 1,
                "idempotency_key": str(uuid4()),
            },
        )
        quote = await client.post(f"/sessions/{session_id}/checkout/prepare")
        order = await client.post(
            f"/sessions/{session_id}/checkout/confirm",
            json={
                "confirmation_token": quote.json()["confirmation_token"],
                "idempotency_key": str(uuid4()),
            },
        )
        order_id = UUID(order.json()["order_id"])
        shopping_service._orders[order_id].expires_at = datetime.now(UTC) - timedelta(seconds=1)

        cart = await client.get(f"/sessions/{session_id}/cart")
        refreshed_order = await client.get(f"/sessions/{session_id}/orders/{order_id}")

        assert cart.status_code == refreshed_order.status_code == 200
        assert refreshed_order.json()["payment_status"] == "EXPIRED"
        assert session_store.get(session_uuid).state == SessionState.CART_ACTIVE
        assert cart.json()["items"][0]["product_id"] == "COKE_ZERO_330"


@pytest.mark.asyncio
async def test_cart_rejects_expired_items_and_conflicting_idempotency_keys(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "shopping-guard-test"})
        session_id = created.json()["session_id"]
        expired = await client.post(
            f"/sessions/{session_id}/cart/items",
            json={
                "product_id": "MILK01",
                "quantity": 1,
                "idempotency_key": str(uuid4()),
            },
        )
        assert expired.status_code == 409

        key = str(uuid4())
        payload = {
            "product_id": "COKE_ZERO_330",
            "quantity": 1,
            "idempotency_key": key,
        }
        first_add = await client.post(f"/sessions/{session_id}/cart/items", json=payload)
        repeated_add = await client.post(f"/sessions/{session_id}/cart/items", json=payload)
        assert first_add.status_code == repeated_add.status_code == 200
        assert repeated_add.json()["item_count"] == 1

        conflict = await client.post(
            f"/sessions/{session_id}/cart/items",
            json={**payload, "quantity": 2},
        )
        assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_end_session_cleans_up_without_writing_to_closed_session(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "shopping-close-test"})
        session_id = created.json()["session_id"]
        ended = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Kết thúc phiên"},
        )

        assert ended.status_code == 200
        assert ended.json()["state"] == "CLOSED"
        assert "kết thúc phiên" in ended.json()["text"].casefold()
        closed_session = await client.get(f"/sessions/{session_id}/cart")
        assert closed_session.status_code == 409
