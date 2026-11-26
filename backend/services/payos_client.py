"""PayOS v2 REST API client (httpx + HMAC-SHA256) with webhook verification and signed tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import Settings

logger = logging.getLogger(__name__)

_PAYOS_CREATE_SIGNATURE_KEYS = ("amount", "cancelUrl", "description", "orderCode", "returnUrl")


class PayOSConfigurationError(RuntimeError):
    """PayOS credentials are missing or incomplete."""


class PayOSProviderError(RuntimeError):
    """PayOS API returned an error or unreachable response."""


@dataclass(frozen=True)
class PayOSPaymentLink:
    order_code: int
    payment_link_id: str
    checkout_url: str
    qr_code: str
    amount: int
    description: str
    status: str
    account_number: str | None = None
    account_name: str | None = None
    bin: str | None = None
    currency: str = "VND"
    expired_at: int | None = None


@dataclass(frozen=True)
class PayOSPaymentStatus:
    order_code: int
    payment_link_id: str
    status: str
    amount: int
    amount_paid: int
    amount_remaining: int
    transaction_reference: str | None = None
    transaction_datetime: str | None = None
    counter_account_name: str | None = None
    counter_account_number: str | None = None


def generate_payos_order_code() -> int:
    """Generate a unique positive integer <= 9007199254740991 for PayOS orderCode."""
    millis = int(time.time() * 1000) % 100_000_000_000  # 11 digits
    rand3 = secrets.randbelow(900) + 100  # 3 digits (100..999)
    return millis * 1000 + rand3  # 14 digits, safe JS & PayOS int


def create_payment_request_signature(
    *,
    amount: int,
    cancel_url: str,
    description: str,
    order_code: int,
    return_url: str,
    checksum_key: str,
) -> str:
    """Compute PayOS v2 create-payment-link HMAC-SHA256 signature over the 5 canonical fields."""
    canonical = (
        f"amount={int(amount)}"
        f"&cancelUrl={cancel_url}"
        f"&description={description}"
        f"&orderCode={int(order_code)}"
        f"&returnUrl={return_url}"
    )
    return hmac.new(
        checksum_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _serialize_webhook_value(val: Any) -> str:
    if val is None or val in {"null", "undefined"}:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, dict):
        sorted_dict = {k: val[k] for k in sorted(val.keys())}
        return json.dumps(sorted_dict, ensure_ascii=False, separators=(",", ":"))
    if isinstance(val, list):
        normalized = [
            {k: item[k] for k in sorted(item.keys())} if isinstance(item, dict) else item
            for item in val
        ]
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return str(val)


def verify_payos_webhook_signature(payload: dict[str, Any], checksum_key: str) -> bool:
    """Verify incoming PayOS webhook HMAC-SHA256 signature in constant time."""
    received = payload.get("signature")
    data = payload.get("data")
    if not isinstance(received, str) or not received.strip() or not isinstance(data, dict):
        return False
    if not checksum_key.strip():
        return False
    parts = [f"{k}={_serialize_webhook_value(data[k])}" for k in sorted(data.keys())]
    canonical = "&".join(parts)
    expected = hmac.new(
        checksum_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected.lower(), received.strip().lower())


def sign_order_snapshot_token(snapshot: dict[str, Any], checksum_key: str) -> str:
    """Sign a compact order snapshot so stateless serverless sync can rebuild pick-lists."""
    raw = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(checksum_key.encode("utf-8"), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_order_snapshot_token(token: str, checksum_key: str) -> dict[str, Any] | None:
    """Validate and decode a signed order snapshot token."""
    if not token or "." not in token or not checksum_key.strip():
        return None
    b64, _, sig = token.partition(".")
    expected = hmac.new(
        checksum_key.encode("utf-8"), b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected.lower(), sig.strip().lower()):
        return None
    try:
        padding = "=" * (-len(b64) % 4)
        decoded = base64.urlsafe_b64decode(b64 + padding)
        parsed = json.loads(decoded.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


class PayOSService:
    """Async PayOS v2 payment-requests client."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._http_transport = http_transport

    @property
    def is_configured(self) -> bool:
        return self.settings.has_payos

    def _headers(self) -> dict[str, str]:
        if not self.is_configured:
            raise PayOSConfigurationError(
                "PayOS chưa được cấu hình (thiếu PAYOS_CLIENT_ID / PAYOS_API_KEY / CHECKSUM_KEY)."
            )
        return {
            "x-client-id": self.settings.payos_client_id.get_secret_value().strip(),
            "x-api-key": self.settings.payos_api_key.get_secret_value().strip(),
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return (
            self.settings.payos_api_base.strip().rstrip("/") or "https://api-merchant.payos.vn"
        )

    def _checksum_key(self) -> str:
        key = self.settings.payos_checksum_key.get_secret_value().strip()
        if not key:
            raise PayOSConfigurationError("Thiếu PAYOS_CHECKSUM_KEY.")
        return key

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.settings.payos_timeout_seconds,
            transport=self._http_transport,
        )

    async def create_payment_link(
        self,
        *,
        order_code: int,
        amount: int,
        description: str,
        items: list[dict[str, Any]],
        return_url: str,
        cancel_url: str,
        buyer_name: str | None = None,
        buyer_phone: str | None = None,
        ttl_seconds: int = 900,
    ) -> PayOSPaymentLink:
        headers = self._headers()
        checksum = self._checksum_key()
        # Keep description compact ASCII (<= 25 chars; <= 9 chars works across all VN banks).
        clean_desc = "".join(
            ch for ch in description if ch.isascii() and (ch.isalnum() or ch == " ")
        )
        clean_desc = " ".join(clean_desc.split())[:25] or f"TLM{str(order_code)[-6:]}"
        expired_at = int(time.time()) + max(180, min(ttl_seconds, 3600))
        sig = create_payment_request_signature(
            amount=int(amount),
            cancel_url=cancel_url,
            description=clean_desc,
            order_code=int(order_code),
            return_url=return_url,
            checksum_key=checksum,
        )
        body: dict[str, Any] = {
            "orderCode": int(order_code),
            "amount": int(amount),
            "description": clean_desc,
            "items": items[:20],
            "returnUrl": return_url,
            "cancelUrl": cancel_url,
            "expiredAt": expired_at,
            "signature": sig,
        }
        if buyer_name:
            body["buyerName"] = buyer_name[:80]
        if buyer_phone:
            body["buyerPhone"] = buyer_phone[:32]

        url = f"{self._base_url()}/v2/payment-requests"
        try:
            async with self._new_client() as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise PayOSProviderError(f"Không kết nối được cổng PayOS: {exc}") from exc

        data = _safe_json(resp)
        if not resp.is_success or str(data.get("code")) != "00":
            desc = data.get("desc") or data.get("message") or f"HTTP {resp.status_code}"
            raise PayOSProviderError(f"PayOS từ chối tạo mã thanh toán: {desc}")

        inner = data.get("data")
        if not isinstance(inner, dict):
            raise PayOSProviderError("PayOS trả về dữ liệu thanh toán không hợp lệ.")

        return PayOSPaymentLink(
            order_code=int(inner.get("orderCode") or order_code),
            payment_link_id=str(inner.get("paymentLinkId") or ""),
            checkout_url=str(inner.get("checkoutUrl") or ""),
            qr_code=str(inner.get("qrCode") or ""),
            amount=int(inner.get("amount") or amount),
            description=str(inner.get("description") or clean_desc),
            status=str(inner.get("status") or "PENDING").upper(),
            account_number=str(inner.get("accountNumber") or "") or None,
            account_name=str(inner.get("accountName") or "") or None,
            bin=str(inner.get("bin") or "") or None,
            currency=str(inner.get("currency") or "VND"),
            expired_at=expired_at,
        )

    async def get_payment_status(self, order_code: int) -> PayOSPaymentStatus:
        headers = self._headers()
        url = f"{self._base_url()}/v2/payment-requests/{int(order_code)}"
        try:
            async with self._new_client() as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise PayOSProviderError(f"Không kiểm tra được trạng thái PayOS: {exc}") from exc

        data = _safe_json(resp)
        if not resp.is_success or str(data.get("code")) != "00":
            desc = data.get("desc") or data.get("message") or f"HTTP {resp.status_code}"
            raise PayOSProviderError(f"PayOS chưa trả được trạng thái giao dịch: {desc}")

        inner = data.get("data")
        if not isinstance(inner, dict):
            raise PayOSProviderError("Dữ liệu trạng thái PayOS không hợp lệ.")

        transactions = inner.get("transactions")
        tx_ref: str | None = None
        tx_dt: str | None = None
        counter_name: str | None = None
        counter_num: str | None = None
        if isinstance(transactions, list) and transactions:
            first_tx = transactions[0] if isinstance(transactions[0], dict) else {}
            tx_ref = str(first_tx.get("reference") or "") or None
            tx_dt = str(first_tx.get("transactionDateTime") or "") or None
            counter_name = str(first_tx.get("counterAccountName") or "") or None
            counter_num = str(first_tx.get("counterAccountNumber") or "") or None

        return PayOSPaymentStatus(
            order_code=int(inner.get("orderCode") or order_code),
            payment_link_id=str(inner.get("id") or inner.get("paymentLinkId") or ""),
            status=str(inner.get("status") or "PENDING").upper(),
            amount=int(inner.get("amount") or 0),
            amount_paid=int(inner.get("amountPaid") or 0),
            amount_remaining=int(inner.get("amountRemaining") or 0),
            transaction_reference=tx_ref,
            transaction_datetime=tx_dt,
            counter_account_name=counter_name,
            counter_account_number=counter_num,
        )

    async def cancel_payment_link(
        self, order_code: int, *, reason: str = "Khách hủy tại kiosk"
    ) -> PayOSPaymentStatus:
        headers = self._headers()
        url = f"{self._base_url()}/v2/payment-requests/{int(order_code)}/cancel"
        try:
            async with self._new_client() as client:
                resp = await client.post(
                    url, json={"cancellationReason": reason[:120]}, headers=headers
                )
        except httpx.HTTPError as exc:
            raise PayOSProviderError(f"Không hủy được mã thanh toán PayOS: {exc}") from exc
        data = _safe_json(resp)
        if not resp.is_success or str(data.get("code")) != "00":
            desc = data.get("desc") or data.get("message") or f"HTTP {resp.status_code}"
            raise PayOSProviderError(f"PayOS không hủy được đơn: {desc}")
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        return PayOSPaymentStatus(
            order_code=int(inner.get("orderCode") or order_code),
            payment_link_id=str(inner.get("id") or ""),
            status=str(inner.get("status") or "CANCELLED").upper(),
            amount=int(inner.get("amount") or 0),
            amount_paid=int(inner.get("amountPaid") or 0),
            amount_remaining=int(inner.get("amountRemaining") or 0),
        )


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        parsed = resp.json()
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}
