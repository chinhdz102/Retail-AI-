from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from backend.api import main as api_main
from backend.api import support_routes
from backend.config import Settings
from backend.services import manager_notifier
from backend.services.manager_notifier import (
    ManagerAlertPayload,
    ManagerNotifierService,
    format_manager_alert_text,
)
from backend.services.support import SupportTicketStore, classify_support_intent
from backend.shared.contracts import IssueType, SessionState


@pytest.mark.parametrize(
    "text",
    [
        "Mai ơi gọi quản lý giúp tôi",
        "Nhắn cho quản lí ra đây giúp mình nhé",
        "Tôi đang cần tìm quản lý",
        "Báo quản lý ra quầy giúp em",
        "Gọi cửa hàng trưởng giúp mình",
        "Call the manager please",
        "Cảm ơn Mai nhiều nhé, gọi quản lý giúp tôi luôn nha",
    ],
)
def test_classify_support_intent_detects_manager_requests(text: str) -> None:
    intent = classify_support_intent(text)
    assert intent is not None
    assert intent.target_role == "manager"


@pytest.mark.asyncio
async def test_manager_notifier_prioritizes_zalo_bot_first() -> None:
    called_urls: list[str] = []
    captured_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        captured_body.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": "zalo-msg-101"}},
        )

    settings = Settings(
        _env_file=None,
        zalo_bot_token=SecretStr("12345:secret-bot-token"),
        zalo_bot_chat_id="mgr-chat-88",
        messenger_page_access_token=SecretStr("fb-token"),
        messenger_manager_psid="psid-99",
        manager_gmail_sender="kiosk@gmail.com",
        manager_gmail_app_password=SecretStr("abcd efgh ijkl mnop"),
    )
    notifier = ManagerNotifierService(
        settings=settings,
        http_transport=httpx.MockTransport(handler),
    )
    payload = ManagerAlertPayload(
        ticket_id=uuid4(),
        session_id=uuid4(),
        kiosk_id="kiosk-01",
        store_name="Thanh Lam Mart",
        target_role="manager",
        issue_type=IssueType.OTHER,
        customer_message="Gọi quản lý giúp tôi",
        created_at=datetime.now(UTC),
        recent_context="Sản phẩm vừa xem: Sữa tươi TH True Milk 1L",
    )

    result = await notifier.notify(payload)

    assert result.configured is True
    assert result.delivered is True
    assert result.channel == "zalo"
    assert result.provider == "zalo_bot"
    assert result.provider_message_id == "zalo-msg-101"
    assert len(called_urls) == 1
    assert (
        called_urls[0]
        == "https://bot-api.zaloplatforms.com/bot12345:secret-bot-token/sendMessage"
    )
    assert captured_body["chat_id"] == "mgr-chat-88"
    assert "KHÁCH ĐANG CẦN GẶP QUẢN LÝ" in captured_body["text"]
    assert "Sữa tươi TH True Milk 1L" in captured_body["text"]


@pytest.mark.asyncio
async def test_zalo_oa_auto_refreshes_expired_token_on_minus_216() -> None:
    calls: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("access_token")))
        if "oauth.zaloapp.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "fresh-oa-token"})
        if request.headers.get("access_token") == "expired-oa-token":
            return httpx.Response(200, json={"error": -216, "message": "Access token expired"})
        return httpx.Response(200, json={"error": 0, "data": {"message_id": "oa-msg-55"}})

    settings = Settings(
        _env_file=None,
        zalo_oa_access_token=SecretStr("expired-oa-token"),
        zalo_oa_refresh_token=SecretStr("refresh-tok"),
        zalo_oa_app_id="app-123",
        zalo_oa_secret_key=SecretStr("secret-123"),
        zalo_oa_manager_user_id="zalo-uid-77",
    )
    notifier = ManagerNotifierService(
        settings=settings,
        http_transport=httpx.MockTransport(handler),
    )
    payload = ManagerAlertPayload(
        ticket_id=uuid4(),
        session_id=uuid4(),
        kiosk_id="kiosk-02",
        store_name="Thanh Lam Mart",
        target_role="manager",
        issue_type=IssueType.OTHER,
        customer_message="Cần gặp quản lý",
        created_at=datetime.now(UTC),
    )

    result = await notifier.notify(payload)

    assert result.delivered is True
    assert result.channel == "zalo"
    assert result.provider == "zalo_oa"
    assert result.provider_message_id == "oa-msg-55"
    assert len(calls) == 3
    assert calls[2][1] == "fresh-oa-token"


@pytest.mark.asyncio
async def test_fallback_chain_zalo_to_messenger_to_gmail(monkeypatch) -> None:
    sent_emails: list[dict] = []

    def fake_smtp_deliver(**kwargs) -> None:
        sent_emails.append(kwargs)

    monkeypatch.setattr(manager_notifier, "_smtp_deliver", fake_smtp_deliver)

    def failing_http(request: httpx.Request) -> httpx.Response:
        if "bot-api.zaloplatforms.com" in str(request.url):
            return httpx.Response(503, json={"ok": False, "description": "Zalo temporarily down"})
        return httpx.Response(
            400,
            json={"error": {"code": 190, "message": "Messenger token expired"}},
        )

    settings = Settings(
        _env_file=None,
        zalo_bot_token=SecretStr("bot-tok"),
        zalo_bot_chat_id="chat-1",
        messenger_page_access_token=SecretStr("fb-tok"),
        messenger_manager_psid="psid-1",
        manager_gmail_sender="kiosk.thanhlammart@gmail.com",
        manager_gmail_app_password=SecretStr("aaaa bbbb cccc dddd"),
        manager_gmail_to="manager@thanhlammart.vn",
    )
    notifier = ManagerNotifierService(
        settings=settings,
        http_transport=httpx.MockTransport(failing_http),
    )
    payload = ManagerAlertPayload(
        ticket_id=uuid4(),
        session_id=uuid4(),
        kiosk_id="kiosk-01",
        store_name="Thanh Lam Mart",
        target_role="manager",
        issue_type=IssueType.PAYMENT_PROBLEM,
        customer_message="Gọi quản lý giúp tôi, tôi bị trừ tiền 2 lần",
        created_at=datetime.now(UTC),
    )

    result = await notifier.notify(payload)

    assert result.delivered is True
    assert result.channel == "gmail"
    assert result.provider == "gmail"
    assert len(result.attempts) == 3
    assert [a.provider for a in result.attempts] == ["zalo_bot", "messenger", "gmail"]
    assert sent_emails[0]["password"] == "aaaabbbbccccdddd"
    assert sent_emails[0]["recipients"] == ["manager@thanhlammart.vn"]


@pytest.mark.asyncio
async def test_agent_respond_sends_zalo_once_and_confirms_truthfully(monkeypatch) -> None:
    sent_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_calls.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": "z-777"}})

    test_settings = Settings(
        _env_file=None,
        zalo_bot_token=SecretStr("z-token"),
        zalo_bot_chat_id="mgr-zalo-01",
    )
    test_notifier = ManagerNotifierService(
        settings=test_settings,
        http_transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(api_main, "support_ticket_store", SupportTicketStore())
    monkeypatch.setattr(
        api_main,
        "get_manager_notifier_service",
        lambda _settings=None: test_notifier,
    )
    api_main.app.dependency_overrides[support_routes.get_manager_notifier_service] = (
        lambda: test_notifier
    )

    try:
        transport = httpx.ASGITransport(app=api_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://manager-test") as client:
            status_resp = await client.get("/support/manager-notifier/status")
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["enabled"] is True
            assert status_data["primary_channel"] == "zalo"
            assert "z-token" not in status_resp.text

            session = await client.post("/sessions", json={"kiosk_id": "kiosk-mobile-01"})
            session_id = session.json()["session_id"]
            turn_id = str(uuid4())

            res = await client.post(
                "/agent/respond",
                json={
                    "session_id": session_id,
                    "text": "Mai ơi gọi quản lý giúp tôi nhé",
                    "client_turn_id": turn_id,
                },
            )
            assert res.status_code == 200
            body = res.json()
            assert body["state"] == SessionState.HANDOFF
            assert body["model"] == "manager-notifier"
            assert "notify_manager_zalo" in body["tools_used"]
            assert "Zalo" in body["text"]
            assert "mã yêu cầu" not in body["text"].casefold()
            assert body["support_ticket"]["ticket_id"][:8].upper() not in body["text"]
            assert body["support_ticket"]["demo"] is False
            assert body["support_ticket"]["status"] == "NOTIFIED_ZALO"
            assert body["support_ticket"]["notified_channel"] == "zalo"
            assert body["support_ticket"]["notification_status"] == "DELIVERED"
            assert body["support_ticket"]["target_role"] == "manager"
            assert len(sent_calls) == 1

            # Idempotent retry must NOT double-message the manager on Zalo.
            replay = await client.post(
                "/agent/respond",
                json={
                    "session_id": session_id,
                    "text": "Mai ơi gọi quản lý giúp tôi nhé",
                    "client_turn_id": turn_id,
                },
            )
            assert replay.status_code == 200
            assert len(sent_calls) == 1
            assert (
                replay.json()["support_ticket"]["ticket_id"]
                == body["support_ticket"]["ticket_id"]
            )

            # Follow-up verification confirms real delivery truthfully without raw IDs/enums.
            followup = await client.post(
                "/agent/respond",
                json={"session_id": session_id, "text": "Đã nhắn cho quản lý chưa?"},
            )
            assert followup.status_code == 200
            assert "Zalo" in followup.json()["text"]
            assert "NOTIFIED_ZALO" not in followup.json()["text"]
            assert body["support_ticket"]["ticket_id"] not in followup.json()["text"]
            assert followup.json()["support_ticket"]["status"] == "NOTIFIED_ZALO"

            # Test endpoint dispatches cleanly too.
            ping = await client.post(
                "/support/manager-notifier/test",
                json={"message": "Kiểm tra Zalo"},
            )
            assert ping.status_code == 200
            assert ping.json()["delivered"] is True
            assert ping.json()["channel"] == "zalo"
            assert len(sent_calls) == 2
    finally:
        api_main.app.dependency_overrides.pop(support_routes.get_manager_notifier_service, None)


def test_format_manager_alert_text_is_compact_and_clear() -> None:
    payload = ManagerAlertPayload(
        ticket_id=uuid4(),
        session_id=uuid4(),
        kiosk_id="kiosk-01",
        store_name="Thanh Lam Mart",
        target_role="manager",
        issue_type=IssueType.OTHER,
        customer_message="Tôi muốn gặp quản lý",
        created_at=datetime(2026, 11, 25, 2, 30, 0, tzinfo=UTC),
    )
    text = format_manager_alert_text(payload)
    assert "[MAI KIOSK - Thanh Lam Mart] KHÁCH ĐANG CẦN GẶP QUẢN LÝ" in text
    assert "09:30:00 · 25/11/2026" in text
    assert "kiosk-01" in text
