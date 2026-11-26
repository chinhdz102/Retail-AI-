"""Tests for assisted shopping cart orders & manager/staff pick-and-handover notifications."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import backend.api.main as api
from backend.config import Settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.manager_notifier import (
    AssistedOrderAlertItem,
    AssistedOrderAlertPayload,
    ManagerNotifierService,
    format_assisted_order_email_html,
    format_assisted_order_text,
)


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json"
    )


def test_format_assisted_order_text_and_html_include_items_shelf_and_total() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    payload = AssistedOrderAlertPayload(
        order_id=uuid4(),
        order_code="TLM-ABC123",
        ticket_id=uuid4(),
        session_id=uuid4(),
        kiosk_id="kiosk-01",
        store_name="Thanh Lam Mart",
        items=(
            AssistedOrderAlertItem(
                product_id="TLM_10194711",
                name="Nước Giải Khát Coca-Cola Zero Pet 600ml",
                quantity=2,
                unit_price_vnd=9900,
                line_total_vnd=19800,
                uom="Chai",
                shelf_location="A04-B02-L03-P01",
            ),
        ),
        item_count=2,
        total_vnd=19800,
        created_at=datetime(2026, 11, 26, 13, 15, tzinfo=UTC),
        customer_name="Anh Minh",
        customer_phone="0901234567",
        pickup_location="Đứng tại Kiosk",
        customer_note="Chọn chai lạnh giúp mình",
        stock_is_current=False,
    )

    text = format_assisted_order_text(payload)
    assert "#TLM-ABC123" in text
    assert "Thanh Lam Mart" in text
    assert "Anh Minh · 0901234567" in text
    assert "Đứng tại Kiosk" in text
    assert "Chọn chai lạnh giúp mình" in text
    assert "Nước Giải Khát Coca-Cola Zero Pet 600ml" in text
    assert "A04-B02-L03-P01" in text
    assert "19.800đ" in text
    assert "trao trả" in text

    html_doc = format_assisted_order_email_html(payload)
    assert "TLM-ABC123" in html_doc
    assert "A04-B02-L03-P01" in html_doc
    assert "19.800đ" in html_doc


@pytest.mark.asyncio
async def test_assisted_order_api_dispatches_zalo_bot_and_is_idempotent(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_requests: list[tuple[str, dict]] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        sent_requests.append((str(request.url), _json.loads(request.content.decode("utf-8"))))
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": "zalo-order-77"}},
        )

    mock_settings = Settings(
        store_name="Thanh Lam Mart",
        zalo_bot_token="test-token-12345",
        zalo_bot_chat_id="manager-chat-01",
    )
    notifier = ManagerNotifierService(
        mock_settings, http_transport=httpx.MockTransport(mock_handler)
    )
    monkeypatch.setattr(api, "get_product_repository", lambda *args, **kwargs: snapshot)
    api.app.dependency_overrides[api.get_manager_notifier_service] = lambda: notifier
    api.app.dependency_overrides[api.get_settings] = lambda: mock_settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
        ) as client:
            sess = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})
            assert sess.status_code == 201
            sid = sess.json()["session_id"]

            p1 = snapshot.products[0]
            p2 = snapshot.products[1]
            payload = {
                "items": [
                    {"product_id": p1.product_id, "quantity": 2},
                    {"product_id": p2.product_id, "quantity": 1},
                ],
                "customer_name": "Chị Lan",
                "customer_phone": "0912345678",
                "pickup_location": "Đứng tại Kiosk",
                "customer_note": "Kiểm tra hạn sử dụng xa",
                "idempotency_key": "assisted-order-key-001",
            }
            first = await client.post(f"/sessions/{sid}/assisted-orders", json=payload)
            assert first.status_code == 201, first.text
            body = first.json()

            expected_total = p1.price_vnd * 2 + p2.price_vnd
            assert body["order_code"].startswith("TLM-")
            assert body["item_count"] == 3
            assert body["total_vnd"] == expected_total
            assert body["status"] == "SENT_TO_STAFF"
            assert body["notification_status"] == "DELIVERED"
            assert body["notified_channel"] == "zalo"
            assert body["notified_provider"] == "zalo_bot"
            assert "Zalo" in body["customer_message"]
            assert body["order_code"] in body["customer_message"]
            assert len(sent_requests) == 1
            zalo_url, zalo_body = sent_requests[0]
            assert "sendMessage" in zalo_url
            assert zalo_body["chat_id"] == "manager-chat-01"
            assert p1.name in zalo_body["text"]
            assert p2.name in zalo_body["text"]
            assert "Chị Lan" in zalo_body["text"]
            assert "Kiểm tra hạn sử dụng xa" in zalo_body["text"]

            # Exact idempotent retry MUST NOT send a duplicate Zalo message
            retry = await client.post(f"/sessions/{sid}/assisted-orders", json=payload)
            assert retry.status_code == 201
            assert retry.json()["order_id"] == body["order_id"]
            assert len(sent_requests) == 1

            # Reusing idempotency key with different items returns 409 Conflict
            conflict = await client.post(
                f"/sessions/{sid}/assisted-orders",
                json={
                    **payload,
                    "items": [{"product_id": p1.product_id, "quantity": 5}],
                },
            )
            assert conflict.status_code == 409

            # Unknown product returns 404
            missing = await client.post(
                f"/sessions/{sid}/assisted-orders",
                json={
                    "items": [{"product_id": "NON_EXISTENT_SKU", "quantity": 1}],
                    "idempotency_key": "assisted-order-key-002",
                },
            )
            assert missing.status_code == 404

            # Listing session assisted orders returns the placed order
            listed = await client.get(f"/sessions/{sid}/assisted-orders")
            assert listed.status_code == 200
            assert len(listed.json()) == 1
            assert listed.json()[0]["order_id"] == body["order_id"]
    finally:
        api.app.dependency_overrides.pop(api.get_manager_notifier_service, None)
        api.app.dependency_overrides.pop(api.get_settings, None)


@pytest.mark.asyncio
async def test_assisted_order_without_notifier_still_creates_honest_pickup_receipt(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_settings = Settings(
        store_name="Thanh Lam Mart",
        zalo_bot_token="",
        zalo_bot_chat_id="",
        zalo_oa_access_token="",
        zalo_oa_manager_user_id="",
        zalo_webhook_url="",
        messenger_page_access_token="",
        messenger_manager_psid="",
        manager_gmail_sender="",
        manager_gmail_app_password="",
    )
    notifier = ManagerNotifierService(empty_settings)
    monkeypatch.setattr(api, "get_product_repository", lambda *args, **kwargs: snapshot)
    api.app.dependency_overrides[api.get_manager_notifier_service] = lambda: notifier
    api.app.dependency_overrides[api.get_settings] = lambda: empty_settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
        ) as client:
            sid = (await client.post("/sessions", json={"kiosk_id": "kiosk-02"})).json()[
                "session_id"
            ]
            p1 = snapshot.products[0]
            resp = await client.post(
                f"/sessions/{sid}/assisted-orders",
                json={
                    "items": [{"product_id": p1.product_id, "quantity": 1}],
                    "idempotency_key": "assisted-order-noconf-1",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "RECORDED_PENDING_STAFF"
            assert data["notification_status"] == "NOT_CONFIGURED"
            assert data["order_code"] in data["customer_message"]
    finally:
        api.app.dependency_overrides.pop(api.get_manager_notifier_service, None)
        api.app.dependency_overrides.pop(api.get_settings, None)
