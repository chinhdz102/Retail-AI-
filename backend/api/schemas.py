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
    target_role: Literal["manager", "staff"] = "staff"


class SupportTicketResponse(BaseModel):
    ticket_id: UUID
    session_id: UUID
    kiosk_id: str
    issue_type: IssueType
    description: str
    status: str
    created_at: datetime
    demo: bool = True
    target_role: Literal["manager", "staff"] = "staff"
    notified_channel: Literal["zalo", "messenger", "gmail"] | None = None
    notified_provider: str | None = None
    notification_status: Literal["DELIVERED", "FAILED", "NOT_CONFIGURED"] = "NOT_CONFIGURED"
    notification_detail: str | None = None
    notified_at: datetime | None = None


class ManagerNotifierStatusResponse(BaseModel):
    enabled: bool
    store_name: str
    primary_channel: Literal["zalo", "messenger", "gmail"] | None = None
    primary_provider: str | None = None
    priority: list[str] = Field(default_factory=list)
    configured_providers: list[str] = Field(default_factory=list)
    channels: dict[str, bool] = Field(default_factory=dict)


class ManagerNotifierAttemptResponse(BaseModel):
    provider: str
    channel_group: Literal["zalo", "messenger", "gmail"]
    ok: bool
    detail: str
    provider_message_id: str | None = None


class ManagerNotifierTestRequest(BaseModel):
    message: str = Field(
        default="Tin nhắn kiểm tra kết nối từ trợ lý siêu thị Mai.",
        min_length=1,
        max_length=500,
    )
    kiosk_id: str = Field(default="kiosk-01", min_length=1, max_length=64)
    provider: Literal["zalo_bot", "zalo_oa", "zalo_webhook", "messenger", "gmail"] | None = None


class ManagerNotifierTestResponse(BaseModel):
    configured: bool
    delivered: bool
    channel: Literal["zalo", "messenger", "gmail"] | None = None
    provider: str | None = None
    provider_message_id: str | None = None
    detail: str
    attempts: list[ManagerNotifierAttemptResponse] = Field(default_factory=list)
    sent_at: datetime | None = None


class ZaloBotChatHint(BaseModel):
    chat_id: str
    display_name: str = ""
    last_text: str = ""


class ZaloBotUpdatesResponse(BaseModel):
    ok: bool
    detail: str
    chats: list[ZaloBotChatHint] = Field(default_factory=list)


class ProductCardResponse(BaseModel):
    product_id: str
    name: str
    category: str
    brand: str | None = None
    price_vnd: int
    list_price_vnd: int | None = None
    promotion: str | None = None
    shelf: str | None = None
    image_url: str | None = None
    stock: int
    stock_is_current: bool = True
    uom: str | None = None
    data_timestamp: datetime
    data_source: str = "unknown"
    demo_units_sold_30d: int | None = Field(default=None, ge=0, le=999)
    sales_data_source: Literal["SIMULATED"] | None = None


class ShoppingPlanQuantityEstimate(BaseModel):
    party_size: int
    target_per_person: int
    measurement: Literal["ml", "g"]
    package_size: int


class ShoppingPlanServingTarget(BaseModel):
    target_per_person: int
    measurement: Literal["ml", "g"]


class ShoppingPlanItemResponse(BaseModel):
    need_id: str
    need_label: str
    category: str
    rationale: str = ""
    source: Literal["EXPLICIT", "INFERRED"]
    required: bool
    product_id: str
    name: str
    brand: str | None = None
    quantity: int
    quantity_basis: Literal[
        "customer_specified",
        "party_size_estimate",
        "catalog_size_unknown",
        "need_default",
    ] = "need_default"
    quantity_estimate: ShoppingPlanQuantityEstimate | None = None
    quantity_needs_review: bool = False
    unit_price_vnd: int
    line_total_vnd: int
    uom: str | None = None
    stock_is_current: bool
    data_timestamp: datetime
    data_source: str
    budget_share_vnd: int | None = None


class ShoppingPlanAlternativeProductResponse(BaseModel):
    product_id: str
    name: str
    brand: str | None = None
    price_vnd: int
    stock_is_current: bool
    data_timestamp: datetime
    data_source: str


class ShoppingPlanAlternativeCategoryResponse(BaseModel):
    category: str
    products: list[ShoppingPlanAlternativeProductResponse] = Field(default_factory=list)


class ShoppingPlanNeedResponse(BaseModel):
    need_id: str
    label: str
    rationale: str = ""
    requested_category: str
    resolved_category: str | None = None
    source: Literal["EXPLICIT", "INFERRED"]
    required: bool
    quantity: int
    candidate_count: int = 0
    candidate_product_ids: list[str] = Field(default_factory=list)
    alternative_categories: list[ShoppingPlanAlternativeCategoryResponse] = Field(
        default_factory=list
    )
    blocked_reasons: list[str] = Field(default_factory=list)
    satisfied_by_existing: bool = False


class ShoppingPlanOptionResponse(BaseModel):
    option_id: str
    label: str
    items: list[ShoppingPlanItemResponse]
    covered_need_ids: list[str]
    total_vnd: int
    remaining_budget_vnd: int | None = None
    budget_fit: bool


class ShoppingPlanResponse(BaseModel):
    mission_id: str = ""
    revision: int = 0
    goal: str
    use_case: str = ""
    occasion: str = ""
    party_size: int | None = None
    budget_vnd: int | None = None
    budget_exclusive: bool = False
    confirmed_needs: list[str] = Field(default_factory=list)
    inferred_needs: list[str] = Field(default_factory=list)
    satisfied_needs: list[str] = Field(default_factory=list)
    needs: list[ShoppingPlanNeedResponse] = Field(default_factory=list)
    options: list[ShoppingPlanOptionResponse] = Field(default_factory=list)
    missing_required_needs: list[str] = Field(default_factory=list)
    missing_optional_needs: list[str] = Field(default_factory=list)
    optional_needs_not_selected: list[str] = Field(default_factory=list)
    budget_shortfall_vnd: int | None = None
    pending_decision: str | None = None
    inventory_verified: bool
    quantity_estimation_measurements: list[Literal["ml", "g"]] = Field(default_factory=list)
    quantity_estimation_targets: list[ShoppingPlanServingTarget] = Field(default_factory=list)
    quantity_size_unknown: bool = False
    data_source: str
    data_timestamp: datetime | None = None
    cart_mutated: bool = False
    unresolved_details: list[str] = Field(default_factory=list)
    unknown_details: list[str] = Field(default_factory=list)


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
    related_products: list[ProductCardResponse] = Field(default_factory=list)
    shopping_plan: ShoppingPlanResponse | None = None
    cart: CartResponse | None = None
    checkout_quote: CheckoutQuoteResponse | None = None
    order: SandboxOrderResponse | None = None
    support_ticket: SupportTicketResponse | None = None


class ProductSearchResponse(BaseModel):
    items: list[ProductSnapshot]
    data_timestamp: datetime


class TranscriptionCorrection(BaseModel):
    """A bounded, opt-in explanation of catalog post-processing."""

    source: str
    replacement: str


class TranscriptionDiagnostics(BaseModel):
    """Raw decoder output and deterministic corrections for local debugging."""

    raw_text: str
    corrections: list[TranscriptionCorrection] = Field(default_factory=list)


class TranscriptionResponse(BaseModel):
    session_id: UUID
    text: str
    language: LanguageCode = "vi"
    model: str
    speech_detected: bool = True
    needs_confirmation: bool | None = None
    quality: dict[str, float] = Field(default_factory=dict)
    diagnostics: TranscriptionDiagnostics | None = None


class SynthesizeRequest(BaseModel):
    session_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    language: LanguageCode = "vi"


class SpeechPrewarmRequest(BaseModel):
    session_id: UUID
    language: LanguageCode = "vi"


class SpeechPrewarmResponse(BaseModel):
    warmed: bool
    provider: str


class SynthesizeTicketResponse(BaseModel):
    audio_url: str
