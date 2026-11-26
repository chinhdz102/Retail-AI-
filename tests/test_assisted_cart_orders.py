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


def test_payos_signatures_and_snapshot_token_roundtrip() -> None:
    import hashlib
    import hmac

    from backend.services.payos_client import (
        create_payment_request_signature,
        sign_order_snapshot_token,
        verify_order_snapshot_token,
        verify_payos_webhook_signature,
    )

    checksum = "test-payos-checksum-key-123456"
    sig = create_payment_request_signature(
        amount=21000,
        cancel_url="https://example.com/?payos=cancel",
        description="TLMPAY001",
        order_code=987654321,
        return_url="https://example.com/?payos=success",
        checksum_key=checksum,
    )
    expected_canonical = (
        "amount=21000&cancelUrl=https://example.com/?payos=cancel"
        "&description=TLMPAY001&orderCode=987654321&returnUrl=https://example.com/?payos=success"
    )
    assert sig == hmac.new(
        checksum.encode("utf-8"), expected_canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    webhook_data = {
        "orderCode": 987654321,
        "amount": 21000,
        "description": "TLMPAY001",
        "reference": "FT261126001",
        "code": "00",
    }
    canonical_wh = "&".join(f"{k}={webhook_data[k]}" for k in sorted(webhook_data.keys()))
    wh_sig = hmac.new(
        checksum.encode("utf-8"), canonical_wh.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert verify_payos_webhook_signature({"data": webhook_data, "signature": wh_sig}, checksum)
    tampered = {**webhook_data, "amount": 999999}
    assert not verify_payos_webhook_signature({"data": tampered, "signature": wh_sig}, checksum)
    assert not verify_payos_webhook_signature({"data": webhook_data, "signature": "bad"}, checksum)

    token = sign_order_snapshot_token({"order_code": "TLM-ABC123", "total_vnd": 21000}, checksum)
    assert verify_order_snapshot_token(token, checksum) == {
        "order_code": "TLM-ABC123",
        "total_vnd": 21000,
    }
    assert verify_order_snapshot_token(token + "x", checksum) is None


@pytest.mark.asyncio
async def test_payos_end_to_end_deferred_zalo_notify_dedupe_and_stateless_sync(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import hmac
    import json as _json

    from backend.services.assisted_orders import AssistedOrderStore
    from backend.services.payos_client import PayOSService

    checksum_key = "unit-test-payos-checksum-secret"
    payos_state = {"status": "PENDING", "last_create": None}
    sent_zalo: list[tuple[str, dict]] = []

    async def payos_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v2/payment-requests":
            assert request.headers.get("x-client-id") == "payos-client-unit"
            assert request.headers.get("x-api-key") == "payos-api-unit"
            req_body = _json.loads(request.content.decode("utf-8"))
            payos_state["last_create"] = req_body
            assert len(req_body["description"]) <= 9
            return httpx.Response(
                200,
                json={
                    "code": "00",
                    "desc": "success",
                    "data": {
                        "orderCode": req_body["orderCode"],
                        "paymentLinkId": "plink-unit-001",
                        "checkoutUrl": f"https://pay.payos.vn/web/{req_body['orderCode']}",
                        "qrCode": "00020101021238570010A000000727012700069704220113VQRQUNITTEST",
                        "amount": req_body["amount"],
                        "description": req_body["description"],
                        "status": "PENDING",
                        "accountNumber": "19036888999",
                        "accountName": "THANH LAM MART",
                        "bin": "970422",
                    },
                },
            )
        if request.method == "GET" and request.url.path.startswith("/v2/payment-requests/"):
            order_code = int(request.url.path.rsplit("/", 1)[-1])
            amt = int((payos_state["last_create"] or {}).get("amount", 10000))
            is_paid = payos_state["status"] == "PAID"
            return httpx.Response(
                200,
                json={
                    "code": "00",
                    "desc": "success",
                    "data": {
                        "id": "plink-unit-001",
                        "orderCode": order_code,
                        "amount": amt,
                        "amountPaid": amt if is_paid else 0,
                        "amountRemaining": 0 if is_paid else amt,
                        "status": payos_state["status"],
                        "transactions": (
                            [
                                {
                                    "reference": "FT261126PAYOS",
                                    "transactionDateTime": "2026-11-26 20:30:00",
                                    "counterAccountName": "NGUYEN VAN A",
                                    "counterAccountNumber": "0011223344",
                                }
                            ]
                            if is_paid
                            else []
                        ),
                    },
                },
            )
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            order_code = int(request.url.path.split("/")[-2])
            return httpx.Response(
                200,
                json={
                    "code": "00",
                    "desc": "success",
                    "data": {
                        "id": "plink-unit-001",
                        "orderCode": order_code,
                        "amount": 10000,
                        "amountPaid": 0,
                        "amountRemaining": 10000,
                        "status": "CANCELLED",
                    },
                },
            )
        return httpx.Response(404, json={"code": "404", "desc": "not found"})

    async def zalo_handler(request: httpx.Request) -> httpx.Response:
        sent_zalo.append((str(request.url), _json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": "zalo-paid-99"}})

    mock_settings = Settings(
        store_name="Thanh Lam Mart",
        payment_provider="payos",
        payos_client_id="payos-client-unit",
        payos_api_key="payos-api-unit",
        payos_checksum_key=checksum_key,
        payos_return_url="https://retail-ai-kiosk.vercel.app/?payos=success",
        payos_cancel_url="https://retail-ai-kiosk.vercel.app/?payos=cancel",
        zalo_bot_token="test-token-12345",
        zalo_bot_chat_id="manager-chat-01",
    )
    notifier = ManagerNotifierService(
        mock_settings, http_transport=httpx.MockTransport(zalo_handler)
    )
    payos = PayOSService(mock_settings, http_transport=httpx.MockTransport(payos_handler))
    monkeypatch.setattr(api, "get_product_repository", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(api, "assisted_order_store", AssistedOrderStore())
    api.app.dependency_overrides[api.get_manager_notifier_service] = lambda: notifier
    api.app.dependency_overrides[api.get_payos_service] = lambda: payos
    api.app.dependency_overrides[api.get_settings] = lambda: mock_settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
        ) as client:
            status_resp = await client.get("/payments/payos/status")
            assert status_resp.status_code == 200
            assert status_resp.json()["enabled"] is True

            sid = (await client.post("/sessions", json={"kiosk_id": "kiosk-payos"})).json()[
                "session_id"
            ]
            p1 = next(p for p in snapshot.products if p.price_vnd >= 5000)

            # 1. Creating a PayOS order generates QR & signed token, does NOT alert manager yet!
            created = await client.post(
                f"/sessions/{sid}/assisted-orders",
                json={
                    "items": [{"product_id": p1.product_id, "quantity": 2}],
                    "payment_method": "payos",
                    "customer_name": "Anh Huy",
                    "customer_phone": "0988777666",
                    "pickup_location": "Đứng tại Kiosk",
                    "customer_note": "Lấy chai mát giúp mình",
                    "idempotency_key": "payos-order-key-1",
                },
            )
            assert created.status_code == 201, created.text
            order = created.json()
            assert order["payment_method"] == "payos"
            assert order["payment_status"] == "PENDING_PAYOS"
            assert order["payos_order_code"] > 0
            assert order["payos_qr_code"].startswith("000201")
            assert order["payos_checkout_url"].startswith("https://pay.payos.vn/")
            assert order["payment_verify_token"]
            assert len(sent_zalo) == 0

            # 2. Sync while still PENDING must NOT notify manager
            pending_sync = await client.post(
                f"/payments/payos/{order['payos_order_code']}/sync",
                json={"payment_verify_token": order["payment_verify_token"]},
            )
            assert pending_sync.status_code == 200
            assert pending_sync.json()["payment_status"] == "PENDING_PAYOS"
            assert len(sent_zalo) == 0

            # 3. Once PayOS marks PAID, sync notifies manager ONCE via Zalo with paid banner & items
            payos_state["status"] = "PAID"
            paid_sync = await client.post(
                f"/payments/payos/{order['payos_order_code']}/sync",
                json={"payment_verify_token": order["payment_verify_token"]},
            )
            assert paid_sync.status_code == 200, paid_sync.text
            paid_data = paid_sync.json()
            assert paid_data["payment_status"] == "PAID"
            assert paid_data["status"] == "SENT_TO_STAFF"
            assert paid_data["notification_status"] == "DELIVERED"
            assert paid_data["notified_channel"] == "zalo"
            assert paid_data["payos_transaction_ref"] == "FT261126PAYOS"
            assert len(sent_zalo) == 1
            _, zalo_msg = sent_zalo[0]
            assert "ĐÃ THANH TOÁN PAYOS" in zalo_msg["text"]
            assert p1.name in zalo_msg["text"]
            assert "Anh Huy" in zalo_msg["text"]
            assert "FT261126PAYOS" in zalo_msg["text"]

            # 4. Duplicate sync + PayOS webhook MUST NOT send a second Zalo alert
            second_sync = await client.post(
                f"/payments/payos/{order['payos_order_code']}/sync",
                json={"payment_verify_token": order["payment_verify_token"]},
            )
            assert second_sync.status_code == 200
            assert len(sent_zalo) == 1

            wh_data = {
                "orderCode": order["payos_order_code"],
                "amount": order["total_vnd"],
                "description": order["payos_description"],
                "reference": "FT261126PAYOS",
                "code": "00",
            }
            wh_canonical = "&".join(f"{k}={wh_data[k]}" for k in sorted(wh_data.keys()))
            wh_sig = hmac.new(
                checksum_key.encode("utf-8"), wh_canonical.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            wh_resp = await client.post(
                "/payments/payos/webhook",
                json={
                    "code": "00",
                    "desc": "success",
                    "success": True,
                    "data": wh_data,
                    "signature": wh_sig,
                },
            )
            assert wh_resp.status_code == 200
            assert len(sent_zalo) == 1

            # 5. PayOS dashboard verification ping (orderCode=123 / VQRIO123) succeeds with 0 Zalo
            ping_resp = await client.post(
                "/payments/payos/webhook",
                json={
                    "code": "00",
                    "desc": "success",
                    "data": {"orderCode": 123, "description": "VQRIO123", "amount": 3000},
                    "signature": "ignored-on-payos-test-ping",
                },
            )
            assert ping_resp.status_code == 200
            assert len(sent_zalo) == 1

            # 6. Invalid webhook signature is rejected with 400
            bad_wh = await client.post(
                "/payments/payos/webhook",
                json={"code": "00", "data": wh_data, "signature": "tampered-signature"},
            )
            assert bad_wh.status_code == 400

            # 7. Stateless serverless cold restart: clear in-memory store and sync via signed token
            monkeypatch.setattr(api, "assisted_order_store", AssistedOrderStore())
            cold_sync = await client.post(
                f"/payments/payos/{order['payos_order_code']}/sync",
                json={"payment_verify_token": order["payment_verify_token"]},
            )
            assert cold_sync.status_code == 200, cold_sync.text
            cold_data = cold_sync.json()
            assert cold_data["payment_status"] == "PAID"
            assert cold_data["order_code"] == order["order_code"]
            assert cold_data["items"][0]["product_id"] == p1.product_id
            assert len(sent_zalo) == 2
            assert p1.name in sent_zalo[1][1]["text"]
    finally:
        api.app.dependency_overrides.pop(api.get_manager_notifier_service, None)
        api.app.dependency_overrides.pop(api.get_payos_service, None)
        api.app.dependency_overrides.pop(api.get_settings, None)


@pytest.mark.asyncio
async def test_browse_product_catalog_departments_subcategories_search_and_pagination(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "get_product_repository", lambda *args, **kwargs: snapshot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
    ) as client:
        all_resp = await client.get("/products/catalog", params={"limit": 24, "offset": 0})
        assert all_resp.status_code == 200, all_resp.text
        all_data = all_resp.json()
        assert all_data["total_catalog"] == len(snapshot.products)
        assert all_data["total"] == len(snapshot.products)
        assert len(all_data["items"]) == 24
        assert all_data["has_more"] is True
        assert len(all_data["departments"]) >= 15
        dept_names = [d["name"] for d in all_data["departments"]]
        assert "Bánh kẹo" in dept_names
        assert "Đồ uống, thuốc lá" in dept_names

        # Filter by department + subcategory + price sort
        snack_resp = await client.get(
            "/products/catalog",
            params={
                "department": "Bánh kẹo",
                "category": "Bánh Snack",
                "sort": "price_asc",
                "limit": 12,
            },
        )
        assert snack_resp.status_code == 200
        snack_data = snack_resp.json()
        assert snack_data["total"] > 12
        assert snack_data["has_more"] is True
        prices = [item["price_vnd"] for item in snack_data["items"]]
        assert prices == sorted(prices)
        assert all(item["category"] == "Bánh Snack" for item in snack_data["items"])

        # Accent-insensitive keyword search across all products
        q_resp = await client.get("/products/catalog", params={"q": "coca zero", "limit": 10})
        assert q_resp.status_code == 200
        assert "s-maxage=300" in q_resp.headers.get("cache-control", "")
        q_data = q_resp.json()
        assert q_data["total"] >= 1
        assert any("coca" in item["name"].lower() for item in q_data["items"])

        # Compact full-catalog index endpoint for 0ms client-side filtering + Edge CDN caching
        idx_resp = await client.get("/products/catalog/index")
        assert idx_resp.status_code == 200, idx_resp.text
        assert "s-maxage=3600" in idx_resp.headers.get("cache-control", "")
        idx_data = idx_resp.json()
        assert idx_data["total"] == len(snapshot.products)
        assert len(idx_data["rows"]) == len(snapshot.products)
        assert len(idx_data["departments"]) >= 15
        first_row = idx_data["rows"][0]
        assert len(first_row) == 13
        assert isinstance(first_row[0], str) and isinstance(first_row[5], int)


@pytest.mark.asyncio
async def test_serverless_session_token_and_signed_speech_ticket_survive_container_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services.sessions import InMemorySessionStore
    from backend.services.speech_tickets import SpeechTicketStore

    signed_settings = Settings(
        store_name="Thanh Lam Mart",
        payos_checksum_key="serverless-signing-secret-123",
    )
    monkeypatch.setattr(api, "get_settings", lambda: signed_settings)
    api.app.dependency_overrides[api.get_settings] = lambda: signed_settings
    store_a = InMemorySessionStore(ttl_seconds=1800)
    tickets_a = SpeechTicketStore(
        signing_key_provider=lambda: api._serverless_hmac_secret(signed_settings)
    )
    monkeypatch.setattr(api, "session_store", store_a)
    monkeypatch.setattr(api, "speech_ticket_store", tickets_a)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
        ) as client:
            # 1. Container A creates session + returns signed session_token
            sess = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})
            assert sess.status_code == 201
            body = sess.json()
            sid = body["session_id"]
            token = body["session_token"]
            assert token and token.startswith("st1.")

            # 2. Simulate Vercel scale-out to fresh Container B (empty RAM stores)
            store_b = InMemorySessionStore(ttl_seconds=1800)
            tickets_b = SpeechTicketStore(
                signing_key_provider=lambda: api._serverless_hmac_secret(signed_settings)
            )
            monkeypatch.setattr(api, "session_store", store_b)
            monkeypatch.setattr(api, "speech_ticket_store", tickets_b)

            # Without token, Container B still strictly rejects unknown session with 404
            no_tok = await client.post(
                "/audio/synthesize/tickets",
                json={"session_id": sid, "text": "Xin chào", "language": "vi"},
            )
            assert no_tok.status_code == 404

            # With signed X-Kiosk-Session-Token, Container B transparently rehydrates session!
            with_tok = await client.post(
                "/audio/synthesize/tickets",
                headers={"X-Kiosk-Session-Token": token},
                json={"session_id": sid, "text": "Xin chào", "language": "vi"},
            )
            assert with_tok.status_code == 200, with_tok.text
            audio_url = with_tok.json()["audio_url"]
            ticket_token = audio_url.rsplit("/", 1)[-1]
            assert ticket_token.startswith("s1.")

            # Signed speech ticket can also be consumed on yet another fresh Container C!
            tickets_c = SpeechTicketStore(
                signing_key_provider=lambda: api._serverless_hmac_secret(signed_settings)
            )
            consumed = tickets_c.consume(ticket_token)
            assert consumed is not None
            assert consumed["session_id"] == sid
            assert consumed["text"] == "Xin chào"
    finally:
        api.app.dependency_overrides.pop(api.get_settings, None)


@pytest.mark.asyncio
async def test_docker_spa_static_serving_headers_and_path_traversal_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "vad" / "onnx").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id='app'>Mai</div>", encoding="utf-8")
    (dist / "catalog-index.json").write_text('{"total":1,"rows":[]}', encoding="utf-8")
    (dist / "vad" / "onnx" / "ort.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (tmp_path / "secret.txt").write_text("top-secret", encoding="utf-8")

    monkeypatch.setenv("FRONTEND_DIST_DIR", str(dist))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
    ) as client:
        root = await client.get("/")
        assert root.status_code == 200
        assert "Mai" in root.text
        assert "no-cache" in root.headers.get("cache-control", "")

        idx = await client.get("/catalog-index.json")
        assert idx.status_code == 200
        assert idx.json()["total"] == 1

        wasm = await client.get("/vad/onnx/ort.wasm")
        assert wasm.status_code == 200
        assert "application/wasm" in wasm.headers.get("content-type", "")
        assert "immutable" in wasm.headers.get("cache-control", "")

        # Path traversal cannot escape FRONTEND_DIST_DIR
        escape = await client.get("/..%2Fsecret.txt")
        assert "top-secret" not in escape.text

        # Unknown /api route still returns 404 JSON rather than index.html
        missing_api = await client.get("/api/products/nonexistent-route-xyz")
        assert missing_api.status_code == 404
        missing_unlisted_api = await client.get("/api/does-not-exist-xyz")
        assert missing_unlisted_api.status_code == 404


@pytest.mark.asyncio
async def test_azure_speech_ephemeral_token_endpoint_and_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services.azure_stt import AzureSTTService

    sts_calls = 0

    async def sts_handler(request: httpx.Request) -> httpx.Response:
        nonlocal sts_calls
        sts_calls += 1
        assert request.url == httpx.URL(
            "https://southeastasia.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        )
        assert request.headers.get("ocp-apim-subscription-key") == "azure-secret-test-key"
        return httpx.Response(200, text="eyJhbGciOiJub25lIn0.ephemeral-azure-token")

    mock_settings = Settings(
        azure_speech_key="azure-secret-test-key",
        azure_speech_region="southeastasia",
    )
    sts_client = httpx.AsyncClient(transport=httpx.MockTransport(sts_handler))
    azure_stt = AzureSTTService(settings=mock_settings, sts_client=sts_client)
    api.app.dependency_overrides[api.get_settings] = lambda: mock_settings
    api.app.dependency_overrides[api.get_azure_stt_service] = lambda: azure_stt
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=20
        ) as client:
            created = await client.post("/sessions", json={"kiosk_id": "kiosk-azure-tok"})
            assert created.status_code == 201
            payload = created.json()
            assert payload["direct_azure_stt_available"] is True
            sid = payload["session_id"]

            r1 = await client.post("/audio/azure-speech-token", json={"session_id": sid})
            assert r1.status_code == 200
            d1 = r1.json()
            assert d1["enabled"] is True
            assert d1["token"] == "eyJhbGciOiJub25lIn0.ephemeral-azure-token"
            assert d1["region"] == "southeastasia"
            assert d1["expires_in_seconds"] >= 300
            assert "Thanh Lam Mart" in d1["phrases_vi"]
            assert "azure-secret-test-key" not in r1.text

            # Second request reuses cached STS token without hitting Azure STS again
            r2 = await client.post("/audio/azure-speech-token", json={"session_id": sid})
            assert r2.status_code == 200
            assert r2.json()["token"] == d1["token"]
            assert sts_calls == 1
    finally:
        await sts_client.aclose()
        api.app.dependency_overrides.pop(api.get_settings, None)
        api.app.dependency_overrides.pop(api.get_azure_stt_service, None)
