from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from backend.products.catalog import ProductSnapshot
from backend.products.repository import ProductRepository, get_product_repository

MAX_CART_QUANTITY = 20
MAX_CART_LINES = 12
QUOTE_TTL = timedelta(minutes=2)
ORDER_TTL = timedelta(minutes=15)
SandboxPaymentOutcome = Literal["PAID", "FAILED", "CANCELLED"]


class ShoppingError(RuntimeError):
    """Base domain error for cart and sandbox checkout operations."""


class ShoppingUnavailableError(ShoppingError):
    """The active source is not approved for transaction simulation."""


class CartNotFoundError(ShoppingError):
    """The requested product is not in this session's cart."""


class CartConflictError(ShoppingError):
    """The cart changed and must be reviewed again."""


class StockConflictError(CartConflictError):
    """The requested quantity is no longer available."""


class CheckoutConfirmationError(ShoppingError):
    """Checkout confirmation is missing, expired, or belongs to another session."""


class OrderNotFoundError(ShoppingError):
    """The order is unknown or does not belong to the given session."""


class IdempotencyConflictError(ShoppingError):
    """An idempotency key was reused for a different action."""


@dataclass(frozen=True)
class CartLine:
    product_id: str
    name: str
    category: str
    brand: str | None
    unit_price_vnd: int
    quantity: int
    line_total_vnd: int
    available_stock: int
    uom: str | None
    data_timestamp: datetime


@dataclass(frozen=True)
class CartSnapshot:
    cart_id: UUID
    session_id: UUID
    items: tuple[CartLine, ...]
    item_count: int
    subtotal_vnd: int
    total_vnd: int
    version: int
    sandbox: bool = True
    data_source: str = "DEMO_CATALOG"


@dataclass(frozen=True)
class CheckoutQuote:
    confirmation_token: str
    cart: CartSnapshot
    expires_at: datetime
    sandbox: bool = True


@dataclass
class SandboxOrder:
    order_id: UUID
    session_id: UUID
    cart_id: UUID
    items: tuple[CartLine, ...]
    amount_vnd: int
    order_status: str
    payment_status: str
    idempotency_key: str
    created_at: datetime
    expires_at: datetime
    sandbox: bool = True
    provider_transaction_id: str | None = None
    payment_events: dict[str, str] = field(default_factory=dict)


@dataclass
class _Cart:
    cart_id: UUID
    session_id: UUID
    quantities: dict[str, int] = field(default_factory=dict)
    unit_prices: dict[str, int] = field(default_factory=dict)
    version: int = 1


@dataclass
class _QuoteReservation:
    quote: CheckoutQuote
    product_quantities: dict[str, int]
    status: str = "PREPARED"
    order_id: UUID | None = None


class ShoppingService:
    """In-memory retail cart and explicitly marked sandbox payment simulator.

    Commerce writes are disabled for live/PostgreSQL sources because the current catalog
    repository reads a web snapshot, not a branch-authoritative inventory/price service.
    """

    def __init__(
        self,
        *,
        sandbox_enabled: bool,
        repository: ProductRepository | None = None,
    ) -> None:
        self.sandbox_enabled = sandbox_enabled
        self._repository = repository
        self._carts: dict[UUID, _Cart] = {}
        self._quotes: dict[str, _QuoteReservation] = {}
        self._active_quote_by_session: dict[UUID, str] = {}
        self._orders: dict[UUID, SandboxOrder] = {}
        self._idempotency: dict[tuple[UUID, str], tuple[str, UUID]] = {}
        self._reserved: dict[str, int] = {}
        self._sold: dict[str, int] = {}
        self._payment_events: dict[str, tuple[UUID, str]] = {}
        self._lock = threading.RLock()

    def _repo(self) -> ProductRepository:
        self._ensure_sandbox()
        repo = self._repository or get_product_repository()
        if repo.source_name != "demo_product_catalog":
            raise ShoppingUnavailableError(
                "The active catalog is not a branch-verified transaction source"
            )
        return repo

    def _ensure_sandbox(self) -> None:
        if not self.sandbox_enabled:
            raise ShoppingUnavailableError(
                "Shopping checkout is disabled outside demo sandbox mode"
            )

    def _product(self, product_id: str) -> ProductSnapshot:
        repo = self._repo()
        product = repo.get_product_snapshot(product_id)
        if product is None:
            raise CartNotFoundError("Product not found in the active catalog")
        return product

    @staticmethod
    def _available(product: ProductSnapshot, reserved: int, sold: int) -> int:
        return max(0, product.sellable_stock - reserved - sold)

    def _cart(self, session_id: UUID) -> _Cart:
        cart = self._carts.get(session_id)
        if cart is None:
            cart = _Cart(cart_id=uuid4(), session_id=session_id)
            self._carts[session_id] = cart
        return cart

    def _release_reservation(self, reservation: _QuoteReservation) -> None:
        if reservation.status != "PREPARED":
            return
        for product_id, quantity in reservation.product_quantities.items():
            self._reserved[product_id] = max(0, self._reserved.get(product_id, 0) - quantity)
        reservation.status = "RELEASED"
        if (
            self._active_quote_by_session.get(reservation.quote.cart.session_id)
            == reservation.quote.confirmation_token
        ):
            self._active_quote_by_session.pop(reservation.quote.cart.session_id, None)

    def _expire_quotes(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for reservation in self._quotes.values():
            if reservation.status == "PREPARED" and reservation.quote.expires_at <= current:
                self._release_reservation(reservation)

    def _expire_orders(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for order in self._orders.values():
            if order.payment_status == "PENDING" and order.expires_at <= current:
                order.order_status = "EXPIRED"
                order.payment_status = "EXPIRED"
                self._release_order_reservation(order)

    def _release_order_reservation(self, order: SandboxOrder) -> None:
        for line in order.items:
            self._reserved[line.product_id] = max(
                0, self._reserved.get(line.product_id, 0) - line.quantity
            )

    def _snapshot(self, session_id: UUID) -> CartSnapshot:
        cart = self._cart(session_id)
        lines = []
        for product_id, quantity in cart.quantities.items():
            product = self._product(product_id)
            available = self._available(
                product,
                self._reserved.get(product_id, 0),
                self._sold.get(product_id, 0),
            )
            unit_price = cart.unit_prices[product_id]
            lines.append(
                CartLine(
                    product_id=product.product_id,
                    name=product.name,
                    category=product.category,
                    brand=product.brand,
                    unit_price_vnd=unit_price,
                    quantity=quantity,
                    line_total_vnd=unit_price * quantity,
                    available_stock=available,
                    uom=product.uom,
                    data_timestamp=product.data_timestamp,
                )
            )
        subtotal = sum(line.line_total_vnd for line in lines)
        return CartSnapshot(
            cart_id=cart.cart_id,
            session_id=session_id,
            items=tuple(lines),
            item_count=sum(line.quantity for line in lines),
            subtotal_vnd=subtotal,
            total_vnd=subtotal,
            version=cart.version,
        )

    def _invalidate_quote(self, session_id: UUID) -> None:
        token = self._active_quote_by_session.pop(session_id, None)
        if token:
            reservation = self._quotes.get(token)
            if reservation:
                self._release_reservation(reservation)

    def get_cart(self, session_id: UUID) -> CartSnapshot:
        with self._lock:
            self._repo()
            self._expire_quotes()
            self._expire_orders()
            return self._snapshot(session_id)

    def add_item(
        self,
        session_id: UUID,
        product_id: str,
        quantity: int,
        *,
        idempotency_key: str,
    ) -> CartSnapshot:
        return self.add_items(
            session_id,
            [(product_id, quantity)],
            idempotency_key=idempotency_key,
        )

    def add_items(
        self,
        session_id: UUID,
        items: list[tuple[str, int]] | tuple[tuple[str, int], ...],
        *,
        idempotency_key: str,
    ) -> CartSnapshot:
        if not items:
            raise ValueError("At least one product is required")
        requested: dict[str, int] = {}
        for product_id, quantity in items:
            normalized_id = product_id.strip()
            if not normalized_id:
                raise ValueError("Product ID must not be empty")
            if not 1 <= quantity <= MAX_CART_QUANTITY:
                raise ValueError(f"Quantity must be between 1 and {MAX_CART_QUANTITY}")
            requested[normalized_id] = requested.get(normalized_id, 0) + quantity
            if requested[normalized_id] > MAX_CART_QUANTITY:
                raise CartConflictError(
                    f"A cart line can contain at most {MAX_CART_QUANTITY} products"
                )
        with self._lock:
            self._repo()
            self._expire_quotes()
            self._expire_orders()
            cart = self._cart(session_id)
            digest_payload = json.dumps(
                sorted(
                    (product_id.casefold(), quantity)
                    for product_id, quantity in requested.items()
                ),
                separators=(",", ":"),
            )
            digest = hashlib.sha256(f"add|{digest_payload}".encode()).hexdigest()
            cached = self._idempotency.get((session_id, idempotency_key))
            if cached:
                if cached[0] != digest:
                    raise IdempotencyConflictError(
                        "Idempotency key reused for a different cart update"
                    )
                return self._snapshot(session_id)
            resolved: dict[str, tuple[ProductSnapshot, int]] = {}
            for requested_id, quantity in requested.items():
                product = self._product(requested_id)
                current_quantity = cart.quantities.get(product.product_id, 0)
                new_quantity = current_quantity + quantity
                if new_quantity > MAX_CART_QUANTITY:
                    raise CartConflictError(
                        f"A cart line can contain at most {MAX_CART_QUANTITY} products"
                    )
                if new_quantity > product.sellable_stock - self._sold.get(product.product_id, 0):
                    raise StockConflictError("Requested quantity is above the sellable demo stock")
                resolved[product.product_id] = (product, quantity)
            new_line_count = len(set(cart.quantities) | set(resolved))
            if new_line_count > MAX_CART_LINES:
                raise CartConflictError(f"A cart can contain up to {MAX_CART_LINES} products")
            self._invalidate_quote(session_id)
            for product_id, (product, quantity) in resolved.items():
                cart.quantities[product_id] = cart.quantities.get(product_id, 0) + quantity
                cart.unit_prices[product_id] = product.price_vnd
            cart.version += 1
            self._idempotency[(session_id, idempotency_key)] = (digest, cart.cart_id)
            return self._snapshot(session_id)

    def set_quantity(
        self,
        session_id: UUID,
        product_id: str,
        quantity: int,
        *,
        idempotency_key: str,
    ) -> CartSnapshot:
        if not 0 <= quantity <= MAX_CART_QUANTITY:
            raise ValueError(f"Quantity must be between 0 and {MAX_CART_QUANTITY}")
        with self._lock:
            self._repo()
            self._expire_quotes()
            self._expire_orders()
            cart = self._cart(session_id)
            product = self._product(product_id)
            digest = hashlib.sha256(f"set|{product.product_id}|{quantity}".encode()).hexdigest()
            cached = self._idempotency.get((session_id, idempotency_key))
            if cached:
                if cached[0] != digest:
                    raise IdempotencyConflictError(
                        "Idempotency key reused for a different cart update"
                    )
                return self._snapshot(session_id)
            if product.product_id not in cart.quantities:
                raise CartNotFoundError("Product is not in this session's cart")
            if quantity > product.sellable_stock - self._sold.get(product.product_id, 0):
                raise StockConflictError("Requested quantity is above the sellable demo stock")
            self._invalidate_quote(session_id)
            if quantity == 0:
                cart.quantities.pop(product.product_id, None)
                cart.unit_prices.pop(product.product_id, None)
            else:
                cart.quantities[product.product_id] = quantity
                cart.unit_prices[product.product_id] = product.price_vnd
            cart.version += 1
            self._idempotency[(session_id, idempotency_key)] = (digest, cart.cart_id)
            return self._snapshot(session_id)

    def prepare_checkout(self, session_id: UUID) -> CheckoutQuote:
        with self._lock:
            repo = self._repo()
            self._expire_quotes()
            self._expire_orders()
            cart = self._cart(session_id)
            if not cart.quantities:
                raise CartConflictError("Cart is empty")
            self._invalidate_quote(session_id)
            refreshed_prices: dict[str, int] = {}
            products: dict[str, ProductSnapshot] = {}
            for product_id, quantity in cart.quantities.items():
                product = repo.get_product_snapshot(product_id)
                if product is None or product.sellable_stock <= 0:
                    raise StockConflictError("A cart product is no longer sellable")
                if cart.unit_prices[product_id] != product.price_vnd:
                    cart.unit_prices[product_id] = product.price_vnd
                    refreshed_prices[product_id] = product.price_vnd
                available = self._available(
                    product,
                    self._reserved.get(product_id, 0),
                    self._sold.get(product_id, 0),
                )
                if quantity > available:
                    raise StockConflictError("Cart quantity exceeds current available stock")
                products[product_id] = product
            if refreshed_prices:
                cart.version += 1
                raise CartConflictError(
                    "A price changed; review the updated cart and prepare checkout again"
                )
            now = datetime.now(UTC)
            lines = tuple(
                CartLine(
                    product_id=product_id,
                    name=products[product_id].name,
                    category=products[product_id].category,
                    brand=products[product_id].brand,
                    unit_price_vnd=cart.unit_prices[product_id],
                    quantity=quantity,
                    line_total_vnd=cart.unit_prices[product_id] * quantity,
                    available_stock=self._available(
                        products[product_id],
                        self._reserved.get(product_id, 0),
                        self._sold.get(product_id, 0),
                    ),
                    uom=products[product_id].uom,
                    data_timestamp=products[product_id].data_timestamp,
                )
                for product_id, quantity in cart.quantities.items()
            )
            subtotal = sum(line.line_total_vnd for line in lines)
            snapshot = CartSnapshot(
                cart_id=cart.cart_id,
                session_id=session_id,
                items=lines,
                item_count=sum(line.quantity for line in lines),
                subtotal_vnd=subtotal,
                total_vnd=subtotal,
                version=cart.version,
            )
            quote = CheckoutQuote(
                confirmation_token=secrets.token_urlsafe(32),
                cart=snapshot,
                expires_at=now + QUOTE_TTL,
            )
            reservation = _QuoteReservation(
                quote=quote,
                product_quantities=dict(cart.quantities),
            )
            self._quotes[quote.confirmation_token] = reservation
            self._active_quote_by_session[session_id] = quote.confirmation_token
            for product_id, quantity in reservation.product_quantities.items():
                self._reserved[product_id] = self._reserved.get(product_id, 0) + quantity
            return quote

    def confirm_checkout(
        self,
        session_id: UUID,
        confirmation_token: str,
        idempotency_key: str,
    ) -> SandboxOrder:
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Idempotency key must be between 8 and 128 characters")
        with self._lock:
            self._repo()
            self._expire_quotes()
            self._expire_orders()
            reservation = self._quotes.get(confirmation_token)
            if reservation is None or reservation.quote.cart.session_id != session_id:
                raise CheckoutConfirmationError(
                    "Checkout confirmation is unknown or belongs to another session"
                )
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "items": [
                            (line.product_id, line.quantity, line.unit_price_vnd)
                            for line in reservation.quote.cart.items
                        ],
                        "amount": reservation.quote.cart.total_vnd,
                    },
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            key = (session_id, idempotency_key)
            previous = self._idempotency.get(key)
            if previous:
                if previous[0] != digest:
                    raise IdempotencyConflictError(
                        "Checkout idempotency key reused for a different order"
                    )
                order = self._orders.get(previous[1])
                if order:
                    return order
            if reservation.status != "PREPARED" or reservation.quote.expires_at <= datetime.now(
                UTC
            ):
                self._release_reservation(reservation)
                raise CheckoutConfirmationError(
                    "Checkout confirmation expired; prepare a new summary"
                )
            order = SandboxOrder(
                order_id=uuid4(),
                session_id=session_id,
                cart_id=reservation.quote.cart.cart_id,
                items=reservation.quote.cart.items,
                amount_vnd=reservation.quote.cart.total_vnd,
                order_status="AWAITING_PAYMENT",
                payment_status="PENDING",
                idempotency_key=idempotency_key,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + ORDER_TTL,
            )
            reservation.status = "CONFIRMED"
            reservation.order_id = order.order_id
            self._active_quote_by_session.pop(session_id, None)
            self._orders[order.order_id] = order
            self._idempotency[key] = (digest, order.order_id)
            return order

    def cancel_checkout(self, session_id: UUID) -> CartSnapshot:
        with self._lock:
            self._repo()
            self._invalidate_quote(session_id)
            return self._snapshot(session_id)

    def get_order(self, session_id: UUID, order_id: UUID) -> SandboxOrder:
        with self._lock:
            self._ensure_sandbox()
            self._expire_quotes()
            self._expire_orders()
            order = self._orders.get(order_id)
            if order is None or order.session_id != session_id:
                raise OrderNotFoundError("Order not found")
            return order

    def simulate_payment(
        self,
        session_id: UUID,
        order_id: UUID,
        outcome: SandboxPaymentOutcome,
        event_id: str,
    ) -> SandboxOrder:
        if not 8 <= len(event_id) <= 128:
            raise ValueError("Payment event ID must be between 8 and 128 characters")
        if outcome not in {"PAID", "FAILED", "CANCELLED"}:
            raise ValueError("Unsupported sandbox payment outcome")
        with self._lock:
            self._ensure_sandbox()
            self._expire_quotes()
            self._expire_orders()
            order = self.get_order(session_id, order_id)
            prior_event = self._payment_events.get(event_id)
            if prior_event:
                if prior_event != (order_id, outcome):
                    raise IdempotencyConflictError("Payment event ID reused with different data")
                return order
            if order.payment_status != "PENDING":
                # Keep retries idempotent while refusing a distinct event after terminal state.
                raise CartConflictError("Order payment is already terminal")
            self._payment_events[event_id] = (order_id, outcome)
            self._release_order_reservation(order)
            order.payment_status = outcome
            order.order_status = {
                "PAID": "PAID",
                "FAILED": "FAILED",
                "CANCELLED": "CANCELLED",
            }[outcome]
            if outcome == "PAID":
                order.provider_transaction_id = f"SANDBOX-{uuid4().hex[:12].upper()}"
                for line in order.items:
                    self._sold[line.product_id] = self._sold.get(line.product_id, 0) + line.quantity
                self._carts.pop(session_id, None)
            return order

    def close_session(self, session_id: UUID) -> None:
        with self._lock:
            self._invalidate_quote(session_id)
            self._carts.pop(session_id, None)
            for key in [key for key in self._idempotency if key[0] == session_id]:
                self._idempotency.pop(key, None)
            # Pending orders stay in the independent order table until terminal or expired.
