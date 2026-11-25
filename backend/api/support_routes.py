from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends

from backend.api.schemas import (
    ManagerNotifierAttemptResponse,
    ManagerNotifierStatusResponse,
    ManagerNotifierTestRequest,
    ManagerNotifierTestResponse,
    ZaloBotChatHint,
    ZaloBotUpdatesResponse,
)
from backend.config import Settings, get_settings
from backend.services.manager_notifier import ManagerAlertPayload, ManagerNotifierService
from backend.shared.contracts import IssueType

router = APIRouter(prefix="/support/manager-notifier", tags=["support"])

_manager_notifier_service: ManagerNotifierService | None = None


def get_manager_notifier_service(
    settings: Settings = Depends(get_settings),
) -> ManagerNotifierService:
    global _manager_notifier_service
    if _manager_notifier_service is None or _manager_notifier_service.settings is not settings:
        _manager_notifier_service = ManagerNotifierService(settings=settings)
    return _manager_notifier_service


@router.get("/status", response_model=ManagerNotifierStatusResponse)
def manager_notifier_status(
    notifier: ManagerNotifierService = Depends(get_manager_notifier_service),
) -> ManagerNotifierStatusResponse:
    return ManagerNotifierStatusResponse(**notifier.get_status())


@router.post("/test", response_model=ManagerNotifierTestResponse)
async def manager_notifier_test(
    request: ManagerNotifierTestRequest,
    notifier: ManagerNotifierService = Depends(get_manager_notifier_service),
) -> ManagerNotifierTestResponse:
    payload = ManagerAlertPayload(
        ticket_id=uuid4(),
        session_id=uuid4(),
        kiosk_id=request.kiosk_id,
        store_name=notifier.settings.store_name,
        target_role="manager",
        issue_type=IssueType.OTHER,
        customer_message=request.message,
        created_at=datetime.now(UTC),
        recent_context="Kiểm tra kết nối gửi cảnh báo quản lý từ giao diện Mai.",
    )
    result = await notifier.notify(payload, preferred_provider=request.provider)
    return ManagerNotifierTestResponse(
        configured=result.configured,
        delivered=result.delivered,
        channel=result.channel,
        provider=result.provider,
        provider_message_id=result.provider_message_id,
        detail=result.detail,
        attempts=[
            ManagerNotifierAttemptResponse(
                provider=item.provider,
                channel_group=item.channel_group,
                ok=item.ok,
                detail=item.detail,
                provider_message_id=item.provider_message_id,
            )
            for item in result.attempts
        ],
        sent_at=result.sent_at,
    )


@router.get("/zalo-updates", response_model=ZaloBotUpdatesResponse)
async def manager_notifier_zalo_updates(
    limit: int = 10,
    notifier: ManagerNotifierService = Depends(get_manager_notifier_service),
) -> ZaloBotUpdatesResponse:
    bounded = max(1, min(limit, 50))
    data = await notifier.fetch_zalo_bot_updates(limit=bounded)
    return ZaloBotUpdatesResponse(
        ok=bool(data.get("ok")),
        detail=str(data.get("detail") or ""),
        chats=[ZaloBotChatHint(**chat) for chat in data.get("chats") or []],
    )
