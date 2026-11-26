"""Assisted shopping cart order service with idempotent staff/manager pick-list handoff.

Allows shoppers on kiosk or mobile to assemble a real-catalog basket across all catalog
sources (including dated snapshots) and submit an assisted-shopping request that sends a
structured pick-and-handover message to the store manager / staff via Zalo, Messenger, or Gmail.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from backend.products.planogram import recommended_shelf
from backend.products.repository import ProductRepository
from backend.services.support import NotificationChannel, NotificationStatus

MAX_ASSISTED_LINES = 20
MAX_ASSISTED_QTY_PER_LINE = 20

AssistedOrderStatus = Literal["SENT_TO_STAFF", "RECORDED_PENDING_STAFF"]


class AssistedOrderError(RuntimeError):
    """Base error for assisted cart order operations."""


class AssistedOrderProductNotFoundError(AssistedOrderError):
    """One or more requested products do not exist in the active catalog."""


class AssistedOrderIdempotencyConflictError(AssistedOrderError):
    """The same idempotency key was reused with different items or customer details."""


@dataclass(frozen=True)
class AssistedOrderItem:
    product_id: str
    name: str
    brand: str | None
    category: str
    uom: str | None
    image_url: str | None
    unit_price_vnd: int
    quantity: int
    line_total_vnd: int
    shelf_location: str | None
    snapshot_stock: int
    stock_is_current: bool


@dataclass(frozen=True)
class AssistedOrderRecord:
    order_id: UUID
    order_code: str
    session_id: UUID
    kiosk_id: str
    store_name: str
    items: tuple[AssistedOrderItem, ...]
    item_count: int
    subtotal_vnd: int
    total_vnd: int
    customer_name: str | None
    customer_phone: str | None
    pickup_location: str | None
    customer_note: str | None
    status: AssistedOrderStatus
    ticket_id: UUID
    created_at: datetime
    data_source: str
    stock_is_current: bool
    notified_channel: NotificationChannel | None = None
    notified_provider: str | None = None
    notification_status: NotificationStatus = "NOT_CONFIGURED"
    notification_detail: str | None = None
    notified_at: datetime | None = None


def _clean_optional_field(value: str | None, *, max_len: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _build_order_code(order_id: UUID) -> str:
    return f"TLM-{order_id.hex[:6].upper()}"


class AssistedOrderStore:
    """Thread-safe in-memory store for assisted pick-and-handover orders."""

    def __init__(self) -> None:
        self._orders: dict[UUID, AssistedOrderRecord] = {}
        self._by_session: dict[UUID, list[UUID]] = {}
        self._idempotency: dict[tuple[UUID, str], tuple[str, UUID]] = {}
        self._lock = threading.RLock()

    def create_or_get(
        self,
        *,
        session_id: UUID,
        kiosk_id: str,
        store_name: str,
        requested_items: list[tuple[str, int]],
        repository: ProductRepository,
        ticket_id: UUID,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        pickup_location: str | None = None,
        customer_note: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[AssistedOrderRecord, bool]:
        if not requested_items:
            raise ValueError("Giỏ hàng đang trống; vui lòng chọn ít nhất một sản phẩm.")

        merged: dict[str, int] = {}
        for raw_pid, qty in requested_items:
            pid = raw_pid.strip()
            if not pid:
                raise ValueError("Mã sản phẩm không hợp lệ.")
            if not 1 <= qty <= MAX_ASSISTED_QTY_PER_LINE:
                raise ValueError(
                    f"Số lượng mỗi sản phẩm phải từ 1 đến {MAX_ASSISTED_QTY_PER_LINE}."
                )
            merged[pid] = merged.get(pid, 0) + qty
            if merged[pid] > MAX_ASSISTED_QTY_PER_LINE:
                raise ValueError(
                    f"Mỗi sản phẩm tối đa {MAX_ASSISTED_QTY_PER_LINE} đơn vị mỗi đơn."
                )

        if len(merged) > MAX_ASSISTED_LINES:
            raise ValueError(f"Mỗi đơn hỗ trợ chọn tối đa {MAX_ASSISTED_LINES} loại sản phẩm.")

        clean_name = _clean_optional_field(customer_name, max_len=80)
        clean_phone = _clean_optional_field(customer_phone, max_len=32)
        clean_pickup = _clean_optional_field(pickup_location, max_len=120)
        clean_note = _clean_optional_field(customer_note, max_len=300)

        digest_body = {
            "items": sorted((pid, qty) for pid, qty in merged.items()),
            "name": clean_name or "",
            "phone": clean_phone or "",
            "pickup": clean_pickup or "",
            "note": clean_note or "",
        }
        digest = hashlib.sha256(
            json.dumps(digest_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self._lock:
            if idempotency_key:
                key = (session_id, idempotency_key.strip())
                cached = self._idempotency.get(key)
                if cached is not None:
                    prev_digest, prev_order_id = cached
                    if prev_digest != digest:
                        raise AssistedOrderIdempotencyConflictError(
                            "Idempotency key đã được dùng cho đơn hỗ trợ khác."
                        )
                    existing = self._orders.get(prev_order_id)
                    if existing is not None:
                        return existing, False

            resolved_items: list[AssistedOrderItem] = []
            for pid, qty in merged.items():
                product = repository.get_product_snapshot(pid)
                if product is None:
                    raise AssistedOrderProductNotFoundError(
                        f"Không tìm thấy sản phẩm mã {pid} trong danh mục hiện tại."
                    )
                shelf_loc = (
                    product.shelf
                    if (product.shelf and not product.shelf.startswith("shelf_"))
                    else recommended_shelf(
                        product.product_id,
                        getattr(product, "catalog_version", None),
                    )
                )
                resolved_items.append(
                    AssistedOrderItem(
                        product_id=product.product_id,
                        name=product.name,
                        brand=product.brand,
                        category=product.category,
                        uom=product.uom,
                        image_url=product.image_url,
                        unit_price_vnd=product.price_vnd,
                        quantity=qty,
                        line_total_vnd=product.price_vnd * qty,
                        shelf_location=shelf_loc,
                        snapshot_stock=product.stock,
                        stock_is_current=product.stock_is_current,
                    )
                )

            subtotal = sum(item.line_total_vnd for item in resolved_items)
            order_id = uuid4()
            record = AssistedOrderRecord(
                order_id=order_id,
                order_code=_build_order_code(order_id),
                session_id=session_id,
                kiosk_id=kiosk_id,
                store_name=store_name,
                items=tuple(resolved_items),
                item_count=sum(item.quantity for item in resolved_items),
                subtotal_vnd=subtotal,
                total_vnd=subtotal,
                customer_name=clean_name,
                customer_phone=clean_phone,
                pickup_location=clean_pickup,
                customer_note=clean_note,
                status="RECORDED_PENDING_STAFF",
                ticket_id=ticket_id,
                created_at=datetime.now(UTC),
                data_source=repository.source_name,
                stock_is_current=all(item.stock_is_current for item in resolved_items),
            )
            self._orders[order_id] = record
            self._by_session.setdefault(session_id, []).append(order_id)
            if idempotency_key:
                self._idempotency[(session_id, idempotency_key.strip())] = (digest, order_id)
            return record, True

    def record_notification(
        self,
        order_id: UUID,
        *,
        configured: bool,
        delivered: bool,
        channel: NotificationChannel | None,
        provider: str | None,
        detail: str | None,
        sent_at: datetime | None,
    ) -> AssistedOrderRecord:
        with self._lock:
            existing = self._orders[order_id]
            notif_status: NotificationStatus = (
                "DELIVERED" if delivered else ("FAILED" if configured else "NOT_CONFIGURED")
            )
            order_status: AssistedOrderStatus = (
                "SENT_TO_STAFF" if delivered else "RECORDED_PENDING_STAFF"
            )
            updated = replace(
                existing,
                status=order_status,
                notified_channel=channel,
                notified_provider=provider,
                notification_status=notif_status,
                notification_detail=detail,
                notified_at=sent_at,
            )
            self._orders[order_id] = updated
            return updated

    def list_for_session(self, session_id: UUID) -> tuple[AssistedOrderRecord, ...]:
        with self._lock:
            ids = self._by_session.get(session_id, ())
            return tuple(self._orders[oid] for oid in reversed(ids) if oid in self._orders)

    def get(self, session_id: UUID, order_id: UUID) -> AssistedOrderRecord | None:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None or order.session_id != session_id:
                return None
            return order

    def clear_session(self, session_id: UUID) -> None:
        with self._lock:
            for oid in self._by_session.pop(session_id, ()):
                self._orders.pop(oid, None)
            for key in [k for k in self._idempotency if k[0] == session_id]:
                self._idempotency.pop(key, None)
