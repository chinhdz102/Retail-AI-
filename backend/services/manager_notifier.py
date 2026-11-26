from __future__ import annotations

import asyncio
import html
import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Literal
from uuid import UUID

import httpx

from backend.config import Settings
from backend.shared.contracts import IssueType

logger = logging.getLogger(__name__)

ChannelGroup = Literal["zalo", "messenger", "gmail"]
TargetRole = Literal["manager", "staff"]

_VIETNAM_TZ = timezone(timedelta(hours=7))

_ISSUE_LABELS_VI: dict[IssueType, str] = {
    IssueType.OTHER: "Khách yêu cầu gặp trực tiếp",
    IssueType.EXPIRED_PRODUCT: "Báo cáo sản phẩm hết hạn",
    IssueType.DAMAGED_PACKAGING: "Báo cáo sản phẩm lỗi / hư hỏng",
    IssueType.WRONG_SHELF: "Báo cáo sai giá / sai vị trí kệ",
    IssueType.WRONG_INFORMATION: "Phản ánh thông tin sản phẩm chưa đúng",
    IssueType.PAYMENT_PROBLEM: "Hỗ trợ sự cố thanh toán",
}

CHANNEL_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "zalo": {"vi": "Zalo", "en": "Zalo", "zh": "Zalo"},
    "messenger": {"vi": "Messenger", "en": "Messenger", "zh": "Messenger"},
    "gmail": {"vi": "Gmail", "en": "Gmail", "zh": "Gmail"},
}


def provider_channel_group(provider: str) -> ChannelGroup:
    if provider.startswith("zalo"):
        return "zalo"
    if provider == "messenger":
        return "messenger"
    return "gmail"


@dataclass(frozen=True)
class ManagerAlertPayload:
    ticket_id: UUID
    session_id: UUID
    kiosk_id: str
    store_name: str
    target_role: TargetRole
    issue_type: IssueType
    customer_message: str
    created_at: datetime
    recent_context: str | None = None


@dataclass(frozen=True)
class AssistedOrderAlertItem:
    product_id: str
    name: str
    quantity: int
    unit_price_vnd: int
    line_total_vnd: int
    uom: str | None = None
    shelf_location: str | None = None


@dataclass(frozen=True)
class AssistedOrderAlertPayload:
    order_id: UUID
    order_code: str
    ticket_id: UUID
    session_id: UUID
    kiosk_id: str
    store_name: str
    items: tuple[AssistedOrderAlertItem, ...]
    item_count: int
    total_vnd: int
    created_at: datetime
    customer_name: str | None = None
    customer_phone: str | None = None
    pickup_location: str | None = None
    customer_note: str | None = None
    stock_is_current: bool = False
    payment_method: Literal["counter", "payos"] = "counter"
    payment_status: str = "UNPAID"
    payos_order_code: int | None = None
    payos_paid_amount_vnd: int | None = None
    payos_transaction_ref: str | None = None


@dataclass(frozen=True)
class ChannelAttempt:
    provider: str
    channel_group: ChannelGroup
    ok: bool
    detail: str
    provider_message_id: str | None = None


@dataclass(frozen=True)
class ManagerNotifyResult:
    configured: bool
    delivered: bool
    channel: ChannelGroup | None
    provider: str | None
    provider_message_id: str | None
    detail: str
    attempts: tuple[ChannelAttempt, ...] = ()
    sent_at: datetime | None = None


def _format_vietnam_time(dt: datetime) -> str:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(_VIETNAM_TZ).strftime("%H:%M:%S · %d/%m/%Y")


def format_manager_alert_text(payload: ManagerAlertPayload) -> str:
    short_id = str(payload.ticket_id)[:8].upper()
    role_banner = (
        "KHÁCH ĐANG CẦN GẶP QUẢN LÝ"
        if payload.target_role == "manager"
        else "KHÁCH CẦN NHÂN VIÊN HỖ TRỢ"
    )
    issue_label = _ISSUE_LABELS_VI.get(payload.issue_type, payload.issue_type.value)
    time_vn = _format_vietnam_time(payload.created_at)
    lines = [
        f"[MAI KIOSK - {payload.store_name}] {role_banner}",
        f"- Điểm hỗ trợ / Kiosk: {payload.kiosk_id}",
        f"- Thời gian: {time_vn}",
        f"- Mã yêu cầu: #{short_id}",
        f"- Phân loại: {issue_label}",
        f'- Khách nói với Mai: "{payload.customer_message}"',
    ]
    if payload.recent_context:
        lines.append(f"- Ngữ cảnh vừa hỏi: {payload.recent_context}")
    lines.append("-> Vui lòng ra hỗ trợ khách tại khu vực kiosk sớm nhất.")
    return "\n".join(lines)[:1950]


def format_manager_alert_email_html(payload: ManagerAlertPayload) -> str:
    short_id = str(payload.ticket_id)[:8].upper()
    role_banner = (
        "KHÁCH ĐANG CẦN GẶP QUẢN LÝ"
        if payload.target_role == "manager"
        else "KHÁCH CẦN NHÂN VIÊN HỖ TRỢ"
    )
    issue_label = _ISSUE_LABELS_VI.get(payload.issue_type, payload.issue_type.value)
    time_vn = _format_vietnam_time(payload.created_at)
    context_row = ""
    if payload.recent_context:
        safe_ctx = html.escape(payload.recent_context)
        context_row = (
            "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Ngữ cảnh trước đó</td>"
            f"<td style='padding:8px 12px;color:#0f172a;'>{safe_ctx}</td></tr>"
        )
    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;background:#f8fafc;"
        "padding:20px;color:#0f172a;'>"
        "<div style='max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;"
        "border-radius:14px;overflow:hidden;'>"
        "<div style='background:#0e7490;color:#ffffff;padding:16px 20px;'>"
        f"<div style='font-size:12px;letter-spacing:0.08em;opacity:0.9;'>MAI RETAIL ASSISTANT · "
        f"{html.escape(payload.store_name)}</div>"
        f"<h2 style='margin:6px 0 0;font-size:18px;'>{html.escape(role_banner)}</h2>"
        "</div>"
        "<table style='width:100%;border-collapse:collapse;font-size:14px;'>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;width:36%;'>Kiosk</td>"
        f"<td style='padding:8px 12px;color:#0f172a;'>{html.escape(payload.kiosk_id)}</td></tr>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Thời gian</td>"
        f"<td style='padding:8px 12px;color:#0f172a;'>{html.escape(time_vn)}</td></tr>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Mã yêu cầu</td>"
        f"<td style='padding:8px 12px;color:#0f172a;'><strong>#{short_id}</strong> "
        f"({payload.ticket_id})</td></tr>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Phân loại</td>"
        f"<td style='padding:8px 12px;color:#0f172a;'>{html.escape(issue_label)}</td></tr>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Nội dung khách nói</td>"
        f"<td style='padding:8px 12px;color:#0f172a;font-weight:600;'>"
        f"&ldquo;{html.escape(payload.customer_message)}&rdquo;</td></tr>"
        f"{context_row}"
        "</table>"
        "<div style='padding:14px 20px;background:#f1f5f9;font-size:13px;color:#334155;'>"
        "Tin nhắn được gửi tự động từ trợ lý bán hàng Mai khi khách yêu cầu hỗ trợ."
        "</div></div></body></html>"
    )


def _format_vnd(amount: int) -> str:
    return f"{int(amount):,}đ".replace(",", ".")


def format_assisted_order_text(payload: AssistedOrderAlertPayload) -> str:
    time_vn = _format_vietnam_time(payload.created_at)
    is_paid = payload.payment_status == "PAID"
    banner = (
        f"[MAI KIOSK - {payload.store_name}] ĐÃ THANH TOÁN PAYOS · SOẠN HÀNG & TRAO TRẢ NGAY"
        if is_paid
        else f"[MAI KIOSK - {payload.store_name}] KHÁCH ĐẶT GIỎ HÀNG · HỖ TRỢ CHỌN & TRAO TRẢ"
    )
    lines = [
        banner,
        f"- Mã đơn hỗ trợ: #{payload.order_code}",
        f"- Điểm hỗ trợ / Kiosk: {payload.kiosk_id}",
        f"- Thời gian: {time_vn}",
    ]
    if is_paid:
        paid_amt = payload.payos_paid_amount_vnd or payload.total_vnd
        ref_part = (
            f" · Mã GD: {payload.payos_transaction_ref}" if payload.payos_transaction_ref else ""
        )
        code_part = f" · PayOS #{payload.payos_order_code}" if payload.payos_order_code else ""
        lines.append(
            f"- Thanh toán: ĐÃ THANH TOÁN PAYOS ({_format_vnd(paid_amt)}{code_part}{ref_part})"
        )
    if payload.customer_name or payload.customer_phone:
        parts = [p for p in (payload.customer_name, payload.customer_phone) if p]
        lines.append(f"- Khách hàng: {' · '.join(parts)}")
    if payload.pickup_location:
        lines.append(f"- Vị trí khách / Hình thức nhận: {payload.pickup_location}")
    if payload.customer_note:
        lines.append(f'- Ghi chú của khách: "{payload.customer_note}"')
    lines.append(
        f"- Danh sách sản phẩm ({len(payload.items)} loại · {payload.item_count} sản phẩm):"
    )
    max_listed = 12
    for idx, item in enumerate(payload.items[:max_listed], 1):
        uom_suffix = f" ({item.uom})" if item.uom else ""
        shelf_suffix = f" [Kệ gợi ý: {item.shelf_location}]" if item.shelf_location else ""
        lines.append(
            f"  {idx}. {item.name}{uom_suffix} x {item.quantity} — "
            f"{_format_vnd(item.line_total_vnd)}{shelf_suffix}"
        )
    if len(payload.items) > max_listed:
        lines.append(f"  ... và {len(payload.items) - max_listed} mặt hàng khác.")
    price_label = (
        "Tổng đã thanh toán"
        if is_paid
        else ("Tổng tạm tính (giá tham khảo)" if not payload.stock_is_current else "Tổng tạm tính")
    )
    lines.append(f"- {price_label}: {_format_vnd(payload.total_vnd)}")
    if is_paid:
        lines.append(
            "-> Khách ĐÃ THANH TOÁN THÀNH CÔNG qua PayOS. Vui lòng phân công nhân viên soạn đúng "
            "danh sách hàng trên và trao trả ngay cho khách!"
        )
    else:
        lines.append(
            "-> Vui lòng phân công nhân viên hỗ trợ khách chọn đúng hàng, kiểm tra tồn thực tế "
            "và hướng dẫn trao trả / thanh toán tại quầy."
        )
    return "\n".join(lines)[:1950]


def format_assisted_order_email_html(payload: AssistedOrderAlertPayload) -> str:
    time_vn = _format_vietnam_time(payload.created_at)
    is_paid = payload.payment_status == "PAID"
    header_bg = "#15803d" if is_paid else "#0e7490"
    header_title = (
        f"ĐÃ THANH TOÁN PAYOS · SOẠN HÀNG &amp; TRAO TRẢ · #{html.escape(payload.order_code)}"
        if is_paid
        else f"ĐƠN HỖ TRỢ CHỌN HÀNG &amp; TRAO TRẢ · #{html.escape(payload.order_code)}"
    )
    customer_rows = ""
    if is_paid:
        paid_amt = payload.payos_paid_amount_vnd or payload.total_vnd
        ref_txt = (
            f" (Mã GD: {payload.payos_transaction_ref})" if payload.payos_transaction_ref else ""
        )
        customer_rows += (
            "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Thanh toán</td>"
            f"<td style='padding:8px 12px;color:#15803d;font-weight:700;'>"
            f"ĐÃ THANH TOÁN PAYOS · {html.escape(_format_vnd(paid_amt))}{html.escape(ref_txt)}"
            "</td></tr>"
        )
    if payload.customer_name or payload.customer_phone:
        parts = [p for p in (payload.customer_name, payload.customer_phone) if p]
        customer_rows += (
            "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Khách hàng</td>"
            f"<td style='padding:8px 12px;color:#0f172a;font-weight:600;'>"
            f"{html.escape(' · '.join(parts))}</td></tr>"
        )
    if payload.pickup_location:
        customer_rows += (
            "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>"
            "Vị trí / Nhận hàng</td>"
            f"<td style='padding:8px 12px;color:#0f172a;'>"
            f"{html.escape(payload.pickup_location)}</td></tr>"
        )
    if payload.customer_note:
        customer_rows += (
            "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Ghi chú</td>"
            f"<td style='padding:8px 12px;color:#0f172a;'>"
            f"&ldquo;{html.escape(payload.customer_note)}&rdquo;</td></tr>"
        )
    item_rows = "".join(
        "<tr>"
        f"<td style='padding:8px 12px;border-top:1px solid #e2e8f0;color:#0f172a;'>"
        f"<strong>{html.escape(item.name)}</strong>"
        + (
            f"<div style='font-size:12px;color:#0e7490;'>Kệ gợi ý: "
            f"{html.escape(item.shelf_location)}</div>"
            if item.shelf_location
            else ""
        )
        + "</td>"
        f"<td style='padding:8px 10px;border-top:1px solid #e2e8f0;text-align:center;'>"
        f"× {item.quantity}</td>"
        f"<td style='padding:8px 12px;border-top:1px solid #e2e8f0;text-align:right;"
        f"font-weight:600;white-space:nowrap;'>{html.escape(_format_vnd(item.line_total_vnd))}</td>"
        "</tr>"
        for item in payload.items
    )
    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;background:#f8fafc;"
        "padding:20px;color:#0f172a;'>"
        "<div style='max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;"
        "border-radius:14px;overflow:hidden;'>"
        f"<div style='background:{header_bg};color:#ffffff;padding:16px 20px;'>"
        f"<div style='font-size:12px;letter-spacing:0.08em;opacity:0.9;'>MAI RETAIL ASSISTANT · "
        f"{html.escape(payload.store_name)}</div>"
        f"<h2 style='margin:6px 0 0;font-size:18px;'>{header_title}</h2>"
        "</div>"
        "<table style='width:100%;border-collapse:collapse;font-size:14px;'>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;width:36%;'>Kiosk</td>"
        f"<td style='padding:8px 12px;color:#0f172a;'>{html.escape(payload.kiosk_id)}</td></tr>"
        "<tr><td style='padding:8px 12px;color:#475569;font-weight:600;'>Thời gian</td>"
        f"<td style='padding:8px 12px;color:#0f172a;'>{html.escape(time_vn)}</td></tr>"
        f"{customer_rows}"
        "</table>"
        "<div style='padding:10px 12px 4px;font-size:13px;font-weight:700;color:#0e7490;'>"
        f"Danh sách chọn hàng ({len(payload.items)} loại · {payload.item_count} sản phẩm)</div>"
        "<table style='width:100%;border-collapse:collapse;font-size:13px;'>"
        "<tr style='background:#f1f5f9;color:#475569;'>"
        "<th style='padding:8px 12px;text-align:left;'>Sản phẩm</th>"
        "<th style='padding:8px 10px;text-align:center;'>SL</th>"
        "<th style='padding:8px 12px;text-align:right;'>Thành tiền</th></tr>"
        f"{item_rows}"
        "<tr style='background:#f8fafc;'>"
        "<td colspan='2' style='padding:10px 12px;border-top:2px solid #cbd5e1;font-weight:700;'>"
        "Tổng tạm tính (giá tham khảo)</td>"
        f"<td style='padding:10px 12px;border-top:2px solid #cbd5e1;text-align:right;"
        f"font-weight:800;color:#0e7490;font-size:15px;'>"
        f"{html.escape(_format_vnd(payload.total_vnd))}</td></tr>"
        "</table>"
        "<div style='padding:14px 20px;background:#f1f5f9;font-size:13px;color:#334155;'>"
        "Vui lòng hỗ trợ khách chọn đúng sản phẩm, kiểm tra tồn thực tế tại quầy và trao trả / "
        "thanh toán cho khách."
        "</div></div></body></html>"
    )


def _safe_error_text(exc: Exception) -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    return msg[:220]


class ManagerNotifierService:
    """Multi-channel store manager notifier with priority fallback: Zalo -> Messenger -> Gmail."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._http_transport = http_transport
        self._zalo_oa_runtime_token: str | None = None

    def get_status(self) -> dict[str, Any]:
        configured = list(self.settings.configured_manager_notify_channels)
        primary_provider = configured[0] if configured else None
        primary_channel = provider_channel_group(primary_provider) if primary_provider else None
        return {
            "enabled": bool(configured),
            "store_name": self.settings.store_name,
            "primary_channel": primary_channel,
            "primary_provider": primary_provider,
            "priority": list(self.settings.manager_notify_order),
            "configured_providers": configured,
            "channels": {
                "zalo_bot": self.settings.has_zalo_bot,
                "zalo_oa": self.settings.has_zalo_oa,
                "zalo_webhook": self.settings.has_zalo_webhook,
                "messenger": self.settings.has_messenger_notifier,
                "gmail": self.settings.has_gmail_notifier,
            },
        }

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.settings.manager_notify_timeout_seconds,
            transport=self._http_transport,
        )

    async def notify(
        self,
        payload: ManagerAlertPayload,
        *,
        preferred_provider: str | None = None,
    ) -> ManagerNotifyResult:
        providers = list(self.settings.configured_manager_notify_channels)
        if preferred_provider:
            key = preferred_provider.strip().lower()
            if key not in providers:
                return ManagerNotifyResult(
                    configured=False,
                    delivered=False,
                    channel=None,
                    provider=None,
                    provider_message_id=None,
                    detail=f"Kênh '{key}' chưa được cấu hình trong .env.",
                )
            providers = [key]

        if not providers:
            return ManagerNotifyResult(
                configured=False,
                delivered=False,
                channel=None,
                provider=None,
                provider_message_id=None,
                detail="Chưa cấu hình kênh nhắn tin quản lý (Zalo / Messenger / Gmail).",
            )

        text = format_manager_alert_text(payload)
        attempts: list[ChannelAttempt] = []
        for provider in providers:
            attempt = await self._dispatch_provider(provider, payload, text)
            attempts.append(attempt)
            if attempt.ok:
                logger.info(
                    "Manager alert delivered ticket_id=%s provider=%s channel=%s msg_id=%s",
                    payload.ticket_id,
                    provider,
                    attempt.channel_group,
                    attempt.provider_message_id,
                )
                return ManagerNotifyResult(
                    configured=True,
                    delivered=True,
                    channel=attempt.channel_group,
                    provider=provider,
                    provider_message_id=attempt.provider_message_id,
                    detail=attempt.detail,
                    attempts=tuple(attempts),
                    sent_at=datetime.now(UTC),
                )
            logger.warning(
                "Manager alert failed ticket_id=%s provider=%s detail=%s",
                payload.ticket_id,
                provider,
                attempt.detail,
            )

        summary = "; ".join(f"{a.provider}: {a.detail}" for a in attempts)
        return ManagerNotifyResult(
            configured=True,
            delivered=False,
            channel=None,
            provider=None,
            provider_message_id=None,
            detail=f"Không gửi được qua các kênh đã cấu hình ({summary[:240]}).",
            attempts=tuple(attempts),
        )

    async def notify_assisted_order(
        self,
        payload: AssistedOrderAlertPayload,
        *,
        preferred_provider: str | None = None,
    ) -> ManagerNotifyResult:
        """Dispatch an itemized cart pick-and-handover request to the store manager / staff."""
        providers = list(self.settings.configured_manager_notify_channels)
        if preferred_provider:
            key = preferred_provider.strip().lower()
            if key not in providers:
                return ManagerNotifyResult(
                    configured=False,
                    delivered=False,
                    channel=None,
                    provider=None,
                    provider_message_id=None,
                    detail=f"Kênh '{key}' chưa được cấu hình trong .env.",
                )
            providers = [key]

        if not providers:
            return ManagerNotifyResult(
                configured=False,
                delivered=False,
                channel=None,
                provider=None,
                provider_message_id=None,
                detail="Chưa cấu hình kênh nhắn tin quản lý (Zalo / Messenger / Gmail).",
            )

        text = format_assisted_order_text(payload)
        attempts: list[ChannelAttempt] = []
        for provider in providers:
            attempt = await self._dispatch_assisted_order_provider(provider, payload, text)
            attempts.append(attempt)
            if attempt.ok:
                logger.info(
                    "Assisted order alert delivered order_code=%s provider=%s channel=%s msg_id=%s",
                    payload.order_code,
                    provider,
                    attempt.channel_group,
                    attempt.provider_message_id,
                )
                return ManagerNotifyResult(
                    configured=True,
                    delivered=True,
                    channel=attempt.channel_group,
                    provider=provider,
                    provider_message_id=attempt.provider_message_id,
                    detail=attempt.detail,
                    attempts=tuple(attempts),
                    sent_at=datetime.now(UTC),
                )
            logger.warning(
                "Assisted order alert failed order_code=%s provider=%s detail=%s",
                payload.order_code,
                provider,
                attempt.detail,
            )

        summary = "; ".join(f"{a.provider}: {a.detail}" for a in attempts)
        return ManagerNotifyResult(
            configured=True,
            delivered=False,
            channel=None,
            provider=None,
            provider_message_id=None,
            detail=f"Không gửi được qua các kênh đã cấu hình ({summary[:240]}).",
            attempts=tuple(attempts),
        )

    async def _dispatch_assisted_order_provider(
        self,
        provider: str,
        payload: AssistedOrderAlertPayload,
        text: str,
    ) -> ChannelAttempt:
        group = provider_channel_group(provider)
        try:
            if provider == "zalo_bot":
                return await self._send_zalo_bot(text)
            if provider == "zalo_oa":
                return await self._send_zalo_oa(text)
            if provider == "zalo_webhook":
                return await self._send_assisted_order_zalo_webhook(payload, text)
            if provider == "messenger":
                return await self._send_messenger(text)
            if provider == "gmail":
                return await self._send_assisted_order_gmail(payload, text)
            return ChannelAttempt(provider, group, False, f"Unknown provider: {provider}")
        except Exception as exc:
            return ChannelAttempt(provider, group, False, _safe_error_text(exc))

    async def _send_assisted_order_zalo_webhook(
        self,
        payload: AssistedOrderAlertPayload,
        text: str,
    ) -> ChannelAttempt:
        url = self.settings.zalo_webhook_url.strip()
        secret = self.settings.zalo_webhook_secret.get_secret_value().strip()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
            headers["X-Webhook-Secret"] = secret
        body = {
            "event": "retail_ai.assisted_order",
            "channel": "zalo",
            "order_id": str(payload.order_id),
            "order_code": payload.order_code,
            "ticket_id": str(payload.ticket_id),
            "session_id": str(payload.session_id),
            "kiosk_id": payload.kiosk_id,
            "store_name": payload.store_name,
            "customer_name": payload.customer_name,
            "customer_phone": payload.customer_phone,
            "pickup_location": payload.pickup_location,
            "customer_note": payload.customer_note,
            "item_count": payload.item_count,
            "total_vnd": payload.total_vnd,
            "items": [
                {
                    "product_id": item.product_id,
                    "name": item.name,
                    "quantity": item.quantity,
                    "unit_price_vnd": item.unit_price_vnd,
                    "line_total_vnd": item.line_total_vnd,
                    "uom": item.uom,
                    "shelf_location": item.shelf_location,
                }
                for item in payload.items
            ],
            "created_at_vn": _format_vietnam_time(payload.created_at),
            "text": text,
        }
        async with self._new_client() as client:
            resp = await client.post(url, json=body, headers=headers)
        if resp.is_success:
            data = _try_json(resp)
            msg_id = str(data.get("message_id") or data.get("id") or "") or None
            return ChannelAttempt("zalo_webhook", "zalo", True, "Đã gửi qua Zalo Webhook.", msg_id)
        return ChannelAttempt(
            "zalo_webhook",
            "zalo",
            False,
            f"Zalo Webhook HTTP {resp.status_code}",
        )

    async def _send_assisted_order_gmail(
        self, payload: AssistedOrderAlertPayload, text: str
    ) -> ChannelAttempt:
        sender = self.settings.manager_gmail_sender.strip()
        raw_pw = self.settings.manager_gmail_app_password.get_secret_value().strip()
        app_password = "".join(raw_pw.split())
        recipients_raw = self.settings.manager_gmail_to.strip() or sender
        recipients = [item.strip() for item in recipients_raw.split(",") if item.strip()]
        host = self.settings.manager_gmail_smtp_host.strip() or "smtp.gmail.com"
        port = self.settings.manager_gmail_smtp_port
        timeout = self.settings.manager_notify_timeout_seconds

        subject_prefix = (
            "ĐÃ THANH TOÁN PAYOS · Đơn"
            if payload.payment_status == "PAID"
            else "Đơn hỗ trợ chọn hàng"
        )
        msg = EmailMessage()
        msg["Subject"] = (
            f"[Mai Kiosk · {payload.kiosk_id}] {subject_prefix} "
            f"#{payload.order_code} ({payload.item_count} SP · {_format_vnd(payload.total_vnd)})"
        )
        msg["From"] = f"Mai Retail Assistant <{sender}>"
        msg["To"] = ", ".join(recipients)
        msg.set_content(text)
        msg.add_alternative(format_assisted_order_email_html(payload), subtype="html")

        await asyncio.to_thread(
            _smtp_deliver,
            host=host,
            port=port,
            sender=sender,
            password=app_password,
            recipients=recipients,
            message=msg,
            timeout=timeout,
        )
        return ChannelAttempt(
            "gmail",
            "gmail",
            True,
            f"Đã gửi email Gmail tới {', '.join(recipients)}.",
            f"gmail-{payload.order_code}",
        )

    async def _dispatch_provider(
        self,
        provider: str,
        payload: ManagerAlertPayload,
        text: str,
    ) -> ChannelAttempt:
        group = provider_channel_group(provider)
        try:
            if provider == "zalo_bot":
                return await self._send_zalo_bot(text)
            if provider == "zalo_oa":
                return await self._send_zalo_oa(text)
            if provider == "zalo_webhook":
                return await self._send_zalo_webhook(payload, text)
            if provider == "messenger":
                return await self._send_messenger(text)
            if provider == "gmail":
                return await self._send_gmail(payload, text)
            return ChannelAttempt(provider, group, False, f"Unknown provider: {provider}")
        except Exception as exc:
            return ChannelAttempt(provider, group, False, _safe_error_text(exc))

    async def _send_zalo_bot(self, text: str) -> ChannelAttempt:
        token = self.settings.zalo_bot_token.get_secret_value().strip()
        chat_id = self.settings.zalo_bot_chat_id.strip()
        base = (
            self.settings.zalo_bot_api_base.strip().rstrip("/")
            or "https://bot-api.zaloplatforms.com"
        )
        url = f"{base}/bot{token}/sendMessage"
        async with self._new_client() as client:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": text},
                headers={"Content-Type": "application/json"},
            )
        data = _try_json(resp)
        ok_flag = data.get("ok")
        error_code = data.get("error")
        if resp.is_success and (ok_flag is True or (ok_flag is None and error_code in (None, 0))):
            result_obj = data.get("result") or data.get("data") or {}
            msg_id = (
                str(result_obj.get("message_id"))
                if isinstance(result_obj, dict) and result_obj.get("message_id") is not None
                else None
            )
            return ChannelAttempt("zalo_bot", "zalo", True, "Đã gửi qua Zalo Bot.", msg_id)
        desc = data.get("description") or data.get("message") or f"HTTP {resp.status_code}"
        return ChannelAttempt("zalo_bot", "zalo", False, f"Zalo Bot error: {str(desc)[:160]}")

    async def _refresh_zalo_oa_token(self, client: httpx.AsyncClient) -> str | None:
        refresh_token = self.settings.zalo_oa_refresh_token.get_secret_value().strip()
        app_id = self.settings.zalo_oa_app_id.strip()
        secret_key = self.settings.zalo_oa_secret_key.get_secret_value().strip()
        if not (refresh_token and app_id and secret_key):
            return None
        resp = await client.post(
            "https://oauth.zaloapp.com/v4/oa/access_token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "secret_key": secret_key,
            },
            data={
                "refresh_token": refresh_token,
                "app_id": app_id,
                "grant_type": "refresh_token",
            },
        )
        data = _try_json(resp)
        new_token = str(data.get("access_token") or "").strip()
        if resp.is_success and new_token:
            self._zalo_oa_runtime_token = new_token
            return new_token
        return None

    async def _send_zalo_oa(self, text: str) -> ChannelAttempt:
        user_id = self.settings.zalo_oa_manager_user_id.strip()
        token = (
            self._zalo_oa_runtime_token
            or self.settings.zalo_oa_access_token.get_secret_value().strip()
        )
        url = "https://openapi.zalo.me/v3.0/oa/message/cs"
        body = {"recipient": {"user_id": user_id}, "message": {"text": text}}
        async with self._new_client() as client:
            if not token:
                token = await self._refresh_zalo_oa_token(client) or ""
            if not token:
                return ChannelAttempt("zalo_oa", "zalo", False, "Thiếu Zalo OA access token.")
            resp = await client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json", "access_token": token},
            )
            data = _try_json(resp)
            if data.get("error") == -216:
                refreshed = await self._refresh_zalo_oa_token(client)
                if refreshed:
                    resp = await client.post(
                        url,
                        json=body,
                        headers={"Content-Type": "application/json", "access_token": refreshed},
                    )
                    data = _try_json(resp)

        if resp.is_success and data.get("error") == 0:
            nested = data.get("data") or {}
            msg_id = (
                str(nested.get("message_id"))
                if isinstance(nested, dict) and nested.get("message_id") is not None
                else None
            )
            return ChannelAttempt("zalo_oa", "zalo", True, "Đã gửi qua Zalo OA.", msg_id)
        err_msg = data.get("message") or f"HTTP {resp.status_code}"
        err_code = data.get("error")
        return ChannelAttempt(
            "zalo_oa",
            "zalo",
            False,
            f"Zalo OA error ({err_code}): {str(err_msg)[:150]}",
        )

    async def _send_zalo_webhook(
        self,
        payload: ManagerAlertPayload,
        text: str,
    ) -> ChannelAttempt:
        url = self.settings.zalo_webhook_url.strip()
        secret = self.settings.zalo_webhook_secret.get_secret_value().strip()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
            headers["X-Webhook-Secret"] = secret
        body = {
            "event": "retail_ai.manager_alert",
            "channel": "zalo",
            "ticket_id": str(payload.ticket_id),
            "session_id": str(payload.session_id),
            "kiosk_id": payload.kiosk_id,
            "store_name": payload.store_name,
            "target_role": payload.target_role,
            "issue_type": payload.issue_type.value,
            "customer_message": payload.customer_message,
            "recent_context": payload.recent_context,
            "created_at_vn": _format_vietnam_time(payload.created_at),
            "text": text,
        }
        async with self._new_client() as client:
            resp = await client.post(url, json=body, headers=headers)
        if resp.is_success:
            data = _try_json(resp)
            msg_id = str(data.get("message_id") or data.get("id") or "") or None
            return ChannelAttempt("zalo_webhook", "zalo", True, "Đã gửi qua Zalo Webhook.", msg_id)
        return ChannelAttempt(
            "zalo_webhook",
            "zalo",
            False,
            f"Zalo Webhook HTTP {resp.status_code}",
        )

    async def _send_messenger(self, text: str) -> ChannelAttempt:
        token = self.settings.messenger_page_access_token.get_secret_value().strip()
        psid = self.settings.messenger_manager_psid.strip()
        version = self.settings.messenger_api_version.strip() or "v21.0"
        url = f"https://graph.facebook.com/{version}/me/messages"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        tagged_body = {
            "recipient": {"id": psid},
            "messaging_type": "MESSAGE_TAG",
            "tag": "HUMAN_AGENT",
            "message": {"text": text},
        }
        async with self._new_client() as client:
            resp = await client.post(url, json=tagged_body, headers=headers)
            data = _try_json(resp)
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            if not resp.is_success and err.get("code") in {10, 100, 2018278}:
                fallback_body = {
                    "recipient": {"id": psid},
                    "messaging_type": "RESPONSE",
                    "message": {"text": text},
                }
                resp = await client.post(url, json=fallback_body, headers=headers)
                data = _try_json(resp)
        if resp.is_success and data.get("message_id"):
            return ChannelAttempt(
                "messenger",
                "messenger",
                True,
                "Đã gửi qua Facebook Messenger.",
                str(data["message_id"]),
            )
        err_obj = data.get("error") if isinstance(data.get("error"), dict) else {}
        err_msg = err_obj.get("message") or f"HTTP {resp.status_code}"
        return ChannelAttempt(
            "messenger",
            "messenger",
            False,
            f"Messenger error: {str(err_msg)[:160]}",
        )

    async def _send_gmail(self, payload: ManagerAlertPayload, text: str) -> ChannelAttempt:
        sender = self.settings.manager_gmail_sender.strip()
        raw_pw = self.settings.manager_gmail_app_password.get_secret_value().strip()
        app_password = "".join(raw_pw.split())
        recipients_raw = self.settings.manager_gmail_to.strip() or sender
        recipients = [item.strip() for item in recipients_raw.split(",") if item.strip()]
        host = self.settings.manager_gmail_smtp_host.strip() or "smtp.gmail.com"
        port = self.settings.manager_gmail_smtp_port
        timeout = self.settings.manager_notify_timeout_seconds

        short_id = str(payload.ticket_id)[:8].upper()
        role_title = "gặp quản lý" if payload.target_role == "manager" else "nhân viên hỗ trợ"
        msg = EmailMessage()
        msg["Subject"] = (
            f"[Mai Kiosk · {payload.kiosk_id}] Khách cần {role_title} (#{short_id})"
        )
        msg["From"] = f"Mai Retail Assistant <{sender}>"
        msg["To"] = ", ".join(recipients)
        msg.set_content(text)
        msg.add_alternative(format_manager_alert_email_html(payload), subtype="html")

        await asyncio.to_thread(
            _smtp_deliver,
            host=host,
            port=port,
            sender=sender,
            password=app_password,
            recipients=recipients,
            message=msg,
            timeout=timeout,
        )
        return ChannelAttempt(
            "gmail",
            "gmail",
            True,
            f"Đã gửi email Gmail tới {', '.join(recipients)}.",
            f"gmail-{short_id}",
        )

    async def fetch_zalo_bot_updates(self, *, limit: int = 10) -> dict[str, Any]:
        """Helper to discover recent Zalo Bot chat_id values after messaging the bot."""
        token = self.settings.zalo_bot_token.get_secret_value().strip()
        if not token:
            return {
                "ok": False,
                "detail": "Chưa đặt ZALO_BOT_TOKEN trong .env.",
                "chats": [],
            }
        base = (
            self.settings.zalo_bot_api_base.strip().rstrip("/")
            or "https://bot-api.zaloplatforms.com"
        )
        url = f"{base}/bot{token}/getUpdates"
        try:
            async with self._new_client() as client:
                resp = await client.post(url, json={"timeout": 2})
                if resp.status_code in {404, 405}:
                    resp = await client.get(url, params={"limit": limit, "timeout": 1})
            data = _try_json(resp)
            if data.get("error_code") == 408:
                return {
                    "ok": True,
                    "detail": "Chưa thấy tin nhắn mới. Hãy nhắn 'hi' vào Zalo Bot rồi thử lại.",
                    "chats": [],
                }
            if not resp.is_success or data.get("ok") is False:
                return {
                    "ok": False,
                    "detail": str(data.get("description") or f"HTTP {resp.status_code}")[:180],
                    "chats": [],
                }
            raw_updates = data.get("result") or []
            if isinstance(raw_updates, dict):
                raw_updates = [raw_updates]
            seen: dict[str, dict[str, Any]] = {}
            for item in raw_updates:
                if not isinstance(item, dict):
                    continue
                message = item.get("message") or item
                if not isinstance(message, dict):
                    continue
                chat = message.get("chat") or {}
                sender = message.get("from") or {}
                chat_id = str(chat.get("id") or sender.get("id") or "").strip()
                if chat_id and chat_id not in seen:
                    seen[chat_id] = {
                        "chat_id": chat_id,
                        "display_name": (
                            sender.get("display_name")
                            or sender.get("name")
                            or chat.get("title")
                            or ""
                        ),
                        "last_text": str(message.get("text") or "")[:100],
                    }
            return {
                "ok": True,
                "detail": (
                    f"Tìm thấy {len(seen)} cuộc hội thoại gần đây."
                    if seen
                    else "Chưa thấy tin nhắn mới. Hãy nhắn 'hi' vào Zalo Bot rồi thử lại."
                ),
                "chats": list(seen.values()),
            }
        except Exception as exc:
            return {"ok": False, "detail": _safe_error_text(exc), "chats": []}


def _try_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        parsed = resp.json()
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def _smtp_deliver(
    *,
    host: str,
    port: int,
    sender: str,
    password: str,
    recipients: list[str],
    message: EmailMessage,
    timeout: float,
) -> None:
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
            server.login(sender, password)
            server.send_message(message, from_addr=sender, to_addrs=recipients)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(sender, password)
            server.send_message(message, from_addr=sender, to_addrs=recipients)
