from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from backend.products.catalog import ProductSnapshot
from backend.shared.contracts import IssueType, SessionState

LanguageCode = Literal["vi", "en", "zh"]


class SessionCreateRequest(BaseModel):
    kiosk_id: str = Field(default="kiosk-01", min_length=1, max_length=64)


class SessionResponse(BaseModel):
    session_id: UUID
    kiosk_id: str
    state: SessionState
    expires_at: datetime


class AgentRespondRequest(BaseModel):
    session_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    language: LanguageCode = "vi"
    client_turn_id: UUID = Field(default_factory=uuid4)


class CartLineResponse(BaseModel):
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


class CartResponse(BaseModel):
    cart_id: UUID
    session_id: UUID
    items: list[CartLineResponse]
    item_count: int
    subtotal_vnd: int
    total_vnd: int
    version: int
    sandbox: bool = True
    data_source: str = "DEMO_CATALOG"


class CheckoutQuoteResponse(BaseModel):
    confirmation_token: str
    cart: CartResponse
    expires_at: datetime
    sandbox: bool = True


class SandboxOrderResponse(BaseModel):
    order_id: UUID
    session_id: UUID
    cart_id: UUID
    items: list[CartLineResponse]
    amount_vnd: int
    order_status: str
    payment_status: str
    created_at: datetime
    expires_at: datetime
    sandbox: bool = True
    provider_transaction_id: str | None = None


class CartAddRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=20)
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=128)


class CartQuantityRequest(BaseModel):
    quantity: int = Field(ge=0, le=20)
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=128)


class CheckoutConfirmRequest(BaseModel):
    confirmation_token: str = Field(min_length=24, max_length=128)
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=128)


class SandboxPaymentRequest(BaseModel):
    outcome: Literal["PAID", "FAILED", "CANCELLED"] = "PAID"
    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=128)


class SupportTicketCreateRequest(BaseModel):
    issue_type: IssueType = IssueType.OTHER
    description: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=128)


class SupportTicketResponse(BaseModel):
    ticket_id: UUID
    session_id: UUID
    kiosk_id: str
    issue_type: IssueType
    description: str
    status: str
    created_at: datetime
    demo: bool = True


class ProductCardResponse(BaseModel):
    product_id: str
    name: str
    category: str
    brand: str | None = None
    price_vnd: int
    stock: int
    uom: str | None = None
    data_timestamp: datetime
    data_source: str = "unknown"


class AgentRespondResponse(BaseModel):
    session_id: UUID
    text: str
    model: str
    grounded_product_ids: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    data_timestamp: datetime | None = None
    data_source: str | None = None
    state: SessionState
    products: list[ProductCardResponse] = Field(default_factory=list)
    cart: CartResponse | None = None
    checkout_quote: CheckoutQuoteResponse | None = None
    order: SandboxOrderResponse | None = None
    support_ticket: SupportTicketResponse | None = None


class ProductSearchResponse(BaseModel):
    items: list[ProductSnapshot]
    data_timestamp: datetime


class TranscriptionResponse(BaseModel):
    session_id: UUID
    text: str
    language: LanguageCode = "vi"
    model: str


class SynthesizeRequest(BaseModel):
    session_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    language: LanguageCode = "vi"


class SynthesizeTicketResponse(BaseModel):
    audio_url: str
