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
from backend.services.shopping_mission_routing import should_route_to_shopping_mission
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
        "Tôi mua phải sữa hết hạn, báo giúp nhân viên.",
    ],
)
def test_product_advice_phrasing_does_not_trigger_cart_actions(text: str) -> None:
    assert classify_shopping_action(text, SessionState.BROWSING) is None


def test_shopping_plan_edits_are_not_cart_writes() -> None:
    plan_edit = classify_shopping_action(
        "Thêm vào kế hoạch đồ ăn nhẹ và đồ uống giúp mình.",
        SessionState.BROWSING,
    )
    direct_cart_write = classify_shopping_action(
        "Thêm 2 lon Coca Zero vào giỏ nhé",
        SessionState.BROWSING,
    )

    assert plan_edit is None
    assert direct_cart_write is not None
    assert direct_cart_write.action == ShoppingActionType.ADD_ITEM


@pytest.mark.parametrize(
    "text",
    [
        "Thêm cả nhu cầu trái cây vào phương án và nâng tổng lên 210.000đ; các nhóm cũ vẫn giữ.",
        "Thêm đồ uống vào kế hoạch mua sắm giúp mình.",
    ],
)
def test_add_need_to_shopping_plan_is_not_a_real_cart_write(text: str) -> None:
    assert classify_shopping_action(text, SessionState.BROWSING) is None


@pytest.mark.parametrize(
    "text",
    [
        "Đừng cho vào giỏ vội.",
        "Không thêm Coca Zero vào giỏ nhé.",
        "Chưa cần cho Coca Zero vào giỏ.",
        "Don't add Coca Zero to my cart.",
        (
            "Giúp mình lên phương án mời đồng nghiệp ghé chơi sau giờ làm, "
            "nhóm 5 người, khoảng 180.000đ; đừng cho vào giỏ vội."
        ),
    ],
)
def test_negated_cart_addition_is_not_treated_as_authorization(text: str) -> None:
    assert classify_shopping_action(text, SessionState.BROWSING) is None


def test_explicit_positive_add_after_negated_cart_clause_still_works() -> None:
    action = classify_shopping_action(
        "Đừng cho snack vào giỏ, nhưng thêm 1 chai nước suối vào giỏ nhé.",
        SessionState.BROWSING,
    )

    assert action is not None
    assert action.action == ShoppingActionType.ADD_ITEM
    assert action.query == "nuoc suoi"
    assert action.quantity == 1


def test_gathering_goal_with_explicit_no_cart_write_routes_to_mission_planner() -> None:
    text = (
        "Giúp mình lên phương án cho việc mời đồng nghiệp ghé chơi sau giờ làm. "
        "Nhóm có 5 người, chi khoảng 180.000đ; đừng cho vào giỏ vội."
    )

    assert classify_shopping_action(text, SessionState.BROWSING) is None
    assert should_route_to_shopping_mission(
        text,
        active_mission=False,
        has_factual_lookup=True,
        has_specific_product_reference=False,
        recommendation_requested=False,
        recommendation_tool_ready=False,
        ranking_requested=False,
    )


@pytest.mark.parametrize(
    ("text", "expected", "query", "quantity"),
    [
        ("Thêm 2 lon Coca Zero vào giỏ nhé", ShoppingActionType.ADD_ITEM, "coca zero", 2),
        ("Lấy giúp tui 2 chai bỏ vô giỏ", ShoppingActionType.ADD_ITEM, "", 2),
        ("Add three cans of Coca Zero to my cart", ShoppingActionType.ADD_ITEM, "coca zero", 3),
        ("Xem giỏ hàng giúp mình", ShoppingActionType.VIEW_CART, "", None),
        ("Mở giỏ hàng cho mình xem", ShoppingActionType.VIEW_CART, "", None),
        ("Xem lại giỏ hàng giúp mình", ShoppingActionType.VIEW_CART, "", None),
        ("Đặt hàng", ShoppingActionType.PREPARE_CHECKOUT, "", None),
        ("Tạo đơn Coca Zero rồi gửi mã QR", ShoppingActionType.PREPARE_CHECKOUT, "", None),
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
        (
            "Cho 1 snack khoai tây với 1 chai nước suối vào giỏ.",
            (("snack khoai tay", 1), ("nuoc suoi", 1)),
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


@pytest.mark.parametrize(
    ("text", "expected_query", "expected_quantity"),
    [
        ("Lấy giúp mình 2 chai nước suối 500ml.", "nuoc suoi 500ml", 2),
        ("Mình lấy 1 gói Snack khoai tây vị tự nhiên.", "snack khoai tay vi tu nhien", 1),
        ("Thêm 1 chai cà phê sữa pha sẵn.", "ca phe sua pha san", 1),
        ("Cho mình 1 chai cà phê sữa pha sẵn.", "ca phe sua pha san", 1),
        ("Lấy giúp mình chai nước suối đó.", "chai nuoc suoi do", 1),
        ("Mua Coca Zero giá bao nhiêu?", None, None),
    ],
)
def test_implicit_purchase_requires_a_clear_non_question_purchase_request(
    text: str,
    expected_query: str | None,
    expected_quantity: int | None,
) -> None:
    action = classify_shopping_action(text, SessionState.BROWSING)
    if expected_query is None:
        assert action is None
        return

    assert action is not None
    assert action.action == ShoppingActionType.ADD_ITEM
    assert action.query == expected_query
    assert action.quantity == expected_quantity


def test_implicit_multi_item_purchase_keeps_each_quantity() -> None:
    action = classify_shopping_action(
        "Mình lấy 1 Coca Zero và 1 cà phê sữa pha sẵn.",
        SessionState.BROWSING,
    )

    assert action is not None
    assert action.action == ShoppingActionType.ADD_ITEM
    assert action.line_items == (("coca zero", 1), ("ca phe sua pha san", 1))


def test_cart_removal_quantity_and_payment_status_intents_are_readable() -> None:
    removed = classify_shopping_action("Bỏ một lon Coca ra.", SessionState.CART_ACTIVE)
    quantity = classify_shopping_action(
        "Tăng nước suối thành hai chai.",
        SessionState.CART_ACTIVE,
    )
    payment = classify_shopping_action(
        "Mình chuyển khoản xong rồi, đơn đã thanh toán chưa?",
        SessionState.BROWSING,
    )
    total = classify_shopping_action("Xem tổng tiền giúp mình.", SessionState.CART_ACTIVE)

    assert removed is not None and removed.action == ShoppingActionType.REMOVE_ITEM
    assert removed.query == "coca"
    assert removed.quantity == 1
    assert quantity is not None and quantity.action == ShoppingActionType.SET_QUANTITY
    assert quantity.quantity == 2
    assert payment is not None and payment.action == ShoppingActionType.CHECK_PAYMENT_STATUS
    assert total is not None and total.action == ShoppingActionType.VIEW_CART

    reduced = classify_shopping_action("Giảm còn 1 chai thôi.", SessionState.CART_ACTIVE)
    assert reduced is not None and reduced.action == ShoppingActionType.SET_QUANTITY
    assert reduced.query == "cai nay"
    assert reduced.quantity == 1
    assert (
        classify_shopping_action(
            "Mình chỉ xem trước thôi, chưa thêm vào giỏ đâu.", SessionState.CART_ACTIVE
        )
        is None
    )


@pytest.mark.asyncio
async def test_colloquial_add_reduce_and_total_flow_uses_active_product(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post("/sessions", json={"kiosk_id": "colloquial-cart-followup"})
    session_id = created.json()["session_id"]
    lookup = await client.post(
        "/agent/respond",
        json={"session_id": session_id, "text": "Nước suối 500ml giá bao nhiêu?"},
    )
    added = await client.post(
        "/agent/respond",
        json={"session_id": session_id, "text": "Lấy giúp tui 2 chai bỏ vô giỏ."},
    )
    reduced = await client.post(
        "/agent/respond",
        json={"session_id": session_id, "text": "Giảm còn 1 chai thôi."},
    )
    total = await client.post(
        "/agent/respond",
        json={"session_id": session_id, "text": "Tổng giỏ giờ bao nhiêu?"},
    )

    assert all(response.status_code == 200 for response in (lookup, added, reduced, total))
    assert added.json()["cart"]["items"][0]["product_id"] == "WATER01"
    assert added.json()["cart"]["items"][0]["quantity"] == 2
    assert reduced.json()["cart"]["items"][0]["quantity"] == 1
    assert total.json()["cart"]["total_vnd"] == 7_000


@pytest.mark.asyncio
async def test_natural_cart_and_payment_flow_uses_verified_deterministic_actions(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "natural-cart-v4"})
        session_id = created.json()["session_id"]
        add = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Lấy giúp mình 2 chai nước suối 500ml.",
            },
        )
        assert add.status_code == 200
        assert add.json()["state"] == "CART_ACTIVE"
        assert add.json()["cart"]["items"][0]["product_id"] == "WATER01"
        assert add.json()["cart"]["items"][0]["quantity"] == 2

        view = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Mở giỏ hàng cho mình xem."},
        )
        assert view.json()["cart"]["total_vnd"] == 14_000

        quote = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Chuẩn bị thanh toán giỏ này."},
        )
        assert quote.json()["state"] == "CHECKOUT_CONFIRMATION"
        order = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Xác nhận đặt hàng."},
        )
        assert order.json()["state"] == "PAYMENT_PENDING"
        assert order.json()["order"]["payment_status"] == "PENDING"

        status = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Mình chuyển khoản xong rồi, đơn đã thanh toán chưa?",
            },
        )
        assert status.json()["order"]["payment_status"] == "PENDING"
        assert "chưa xác minh" in status.json()["text"].casefold()
        assert "thanh toán thành công" not in status.json()["text"].casefold()


@pytest.mark.asyncio
async def test_removing_one_item_reduces_quantity_instead_of_clearing_the_line(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "remove-one-v4"})
        session_id = created.json()["session_id"]
        await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Lấy 2 chai nước suối 500ml."},
        )

        removed = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Bỏ một chai nước suối ra."},
        )

        assert removed.status_code == 200
        assert removed.json()["cart"]["items"][0]["quantity"] == 1
        assert removed.json()["cart"]["total_vnd"] == 7_000


@pytest.mark.asyncio
async def test_purchase_and_product_reference_followups_do_not_need_the_llm(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "natural-product-v4"})
        session_id = created.json()["session_id"]
        price = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Mì bò hầm cay giá nhiu vậy ta?"},
        )
        assert price.status_code == 200
        assert "17.000đ" in price.json()["text"]
        assert price.json()["grounded_product_ids"] == ["NOODLE01"]

        shelf = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "À, món đó ở kệ nào vậy?"},
        )
        assert shelf.status_code == 200
        assert "C1-L3" in shelf.json()["text"]
        assert shelf.json()["grounded_product_ids"] == ["NOODLE01"]


@pytest.mark.asyncio
async def test_recommendation_followups_keep_product_and_cart_context(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "recommend-followup-v4"})
        session_id = created.json()["session_id"]
        recommendation = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Mình cần món ăn vặt không cay, dưới 25 nghìn.",
            },
        )
        assert recommendation.status_code == 200
        assert "Snack khoai tây vị tự nhiên" in recommendation.json()["text"]
        assert recommendation.json()["grounded_product_ids"] == ["SNACK01"]

        price = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Giá tham khảo món vừa gợi ý bao nhiêu?",
            },
        )
        assert "18.000đ" in price.json()["text"]
        assert price.json()["grounded_product_ids"] == ["SNACK01"]

        add = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Cho mình thêm gói snack đó vào giỏ.",
            },
        )
        assert add.json()["state"] == "CART_ACTIVE"
        assert add.json()["cart"]["items"][0]["product_id"] == "SNACK01"

        view = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Xem lại giỏ hàng giúp mình."},
        )
        assert view.json()["cart"]["items"][0]["product_id"] == "SNACK01"


@pytest.mark.asyncio
async def test_deictic_water_add_uses_the_grounded_recommendation(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "recommend-water-deictic-v4"})
        session_id = created.json()["session_id"]
        recommendation = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Có chai nước suối nào dưới chục ngàn hông?",
            },
        )
        assert recommendation.status_code == 200
        assert recommendation.json()["grounded_product_ids"] == ["WATER01"]

        add = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Lấy giúp mình chai nước suối đó."},
        )

        assert add.status_code == 200
        assert add.json()["state"] == "CART_ACTIVE"
        assert add.json()["cart"]["items"][0]["product_id"] == "WATER01"
        assert add.json()["cart"]["items"][0]["quantity"] == 1


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


@pytest.mark.asyncio
async def test_colloquial_two_item_cart_can_complete_checkout_without_false_payment(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "colloquial-multi-cart"})
        session_id = created.json()["session_id"]
        added = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Cho 1 snack khoai tây với 1 chai nước suối vào giỏ.",
            },
        )
        view = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Mở giỏ hàng cho mình xem."},
        )
        quote = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Chuẩn bị thanh toán giỏ này."},
        )
        confirmed = await client.post(
            "/agent/respond",
            json={"session_id": session_id, "text": "Xác nhận đặt hàng."},
        )
        claimed_paid = await client.post(
            "/agent/respond",
            json={
                "session_id": session_id,
                "text": "Mình chuyển khoản xong rồi, đơn đã thanh toán chưa?",
            },
        )

    assert all(
        response.status_code == 200 for response in (added, view, quote, confirmed, claimed_paid)
    )
    assert {line["product_id"]: line["quantity"] for line in added.json()["cart"]["items"]} == {
        "SNACK01": 1,
        "WATER01": 1,
    }
    assert view.json()["state"] == "CART_ACTIVE"
    assert quote.json()["state"] == "CHECKOUT_CONFIRMATION"
    assert confirmed.json()["state"] == "PAYMENT_PENDING"
    assert confirmed.json()["order"]["payment_status"] == "PENDING"
    assert claimed_paid.json()["state"] == "PAYMENT_PENDING"
    assert claimed_paid.json()["order"]["payment_status"] == "PENDING"
    assert "chưa xác minh" in claimed_paid.json()["text"].casefold()


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


@pytest.mark.asyncio
async def test_closing_a_kiosk_session_clears_cart_but_keeps_pending_order_private(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post("/sessions", json={"kiosk_id": "pending-order-close-test"})
        session_id = created.json()["session_id"]
        session_uuid = UUID(session_id)
        added = await client.post(
            f"/sessions/{session_id}/cart/items",
            json={
                "product_id": "COKE_ZERO_330",
                "quantity": 1,
                "idempotency_key": str(uuid4()),
            },
        )
        assert added.status_code == 200
        quote = await client.post(f"/sessions/{session_id}/checkout/prepare")
        order_response = await client.post(
            f"/sessions/{session_id}/checkout/confirm",
            json={
                "confirmation_token": quote.json()["confirmation_token"],
                "idempotency_key": str(uuid4()),
            },
        )
        assert order_response.status_code == 200
        order_id = UUID(order_response.json()["order_id"])
        assert order_response.json()["payment_status"] == "PENDING"

        closed = await client.post(f"/sessions/{session_id}/close")

        assert closed.status_code == 200
        assert closed.json()["state"] == "CLOSED"
        assert session_uuid not in shopping_service._carts
        retained_order = shopping_service.get_order(session_uuid, order_id)
        assert retained_order.payment_status == "PENDING"

        next_session = await client.post("/sessions", json={"kiosk_id": "next-customer-test"})
        next_session_id = next_session.json()["session_id"]
        cross_session_order = await client.get(f"/sessions/{next_session_id}/orders/{order_id}")
        closed_session_order = await client.get(f"/sessions/{session_id}/orders/{order_id}")
        assert cross_session_order.status_code == 404
        assert closed_session_order.status_code == 409
