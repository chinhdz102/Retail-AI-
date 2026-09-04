from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SessionState(StrEnum):
    NEW = "NEW"
    BROWSING = "BROWSING"
    CART_ACTIVE = "CART_ACTIVE"
    CHECKOUT_CONFIRMATION = "CHECKOUT_CONFIRMATION"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    HANDOFF = "HANDOFF"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class IntentType(StrEnum):
    PRODUCT_LOOKUP = "PRODUCT_LOOKUP"
    PRODUCT_KNOWLEDGE = "PRODUCT_KNOWLEDGE"
    RECOMMENDATION = "RECOMMENDATION"
    CART_VIEW = "CART_VIEW"
    CART_UPDATE = "CART_UPDATE"
    CHECKOUT = "CHECKOUT"
    PAYMENT_STATUS = "PAYMENT_STATUS"
    HANDOFF = "HANDOFF"
    PRODUCT_ISSUE = "PRODUCT_ISSUE"
    UNKNOWN = "UNKNOWN"


class ToolName(StrEnum):
    RESOLVE_PRODUCT = "resolve_product"
    SEARCH_PRODUCTS = "search_products"
    GET_PRODUCT_SNAPSHOT = "get_product_snapshot"
    GET_PRODUCT_KNOWLEDGE = "get_product_knowledge"
    GET_EXPIRY_INFO = "get_expiry_info"
    GET_CART = "get_cart"
    UPDATE_CART = "update_cart"
    CREATE_CHECKOUT = "create_checkout"
    CHECK_PAYMENT_STATUS = "check_payment_status"
    HANDOFF_TO_STAFF = "handoff_to_staff"
    REPORT_PRODUCT_ISSUE = "report_product_issue"


class ToolPermission(StrEnum):
    READ_ONLY = "READ_ONLY"
    CART_WRITE = "CART_WRITE"
    CHECKOUT_CREATE = "CHECKOUT_CREATE"
    PAYMENT_VERIFY = "PAYMENT_VERIFY"
    STAFF_HANDOFF = "STAFF_HANDOFF"
    ISSUE_CREATE = "ISSUE_CREATE"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    CHECKOUT_PENDING = "CHECKOUT_PENDING"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class IssueType(StrEnum):
    EXPIRED_PRODUCT = "EXPIRED_PRODUCT"
    DAMAGED_PACKAGING = "DAMAGED_PACKAGING"
    WRONG_SHELF = "WRONG_SHELF"
    WRONG_INFORMATION = "WRONG_INFORMATION"
    PAYMENT_PROBLEM = "PAYMENT_PROBLEM"
    OTHER = "OTHER"


class ToolCallRequest(BaseModel):
    tool: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
