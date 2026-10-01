from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.products.catalog import (
    DATA_TIMESTAMP,
    ProductSnapshot,
    get_product_snapshot,
    search_products,
)
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10
FactField = Literal["price_vnd", "stock", "shelf", "promotion", "expiry_at", "ingredients"]
ToolStatus = Literal[
    "SUCCESS", "NOT_FOUND", "INVALID_ARGUMENT", "DENIED", "TEMPORARY_FAILURE"
]
ResponseMode = Literal[
    "DETERMINISTIC_FACT",
    "DETERMINISTIC_ERROR",
    "CANDIDATE_LIST",
    "NEEDS_LLM_COMPOSITION",
]

CATALOG_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Resolve product names and retrieve candidates that satisfy hard filters. "
                "This is candidate retrieval, not authoritative fact lookup. For a "
                "recommendation, the backend enforces sellable stock regardless of this "
                "argument. Omit in_stock to resolve a known unavailable product when the "
                "customer explicitly asks about it. Use "
                "get_product_snapshot for facts, and specify the exact requested fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Product name, brand, category, or catalog keyword.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["drink", "dairy", "snack", "meal"],
                    },
                    "max_price_vnd": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Maximum unit price in VND.",
                    },
                    "spicy": {"type": "boolean"},
                    "in_stock": {
                        "type": "boolean",
                        "description": "Filter to sellable products only when true.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_PRODUCT_RESULTS,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_snapshot",
            "description": (
                "Read authoritative facts for one product ID returned by search_products. "
                "Always set fields to exactly what the customer asked about: price_vnd, "
                "stock (sellable quantity), shelf, promotion, expiry_at, or ingredients. "
                "Never infer a requested field from a different field."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                    },
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "price_vnd",
                                "stock",
                                "shelf",
                                "promotion",
                                "expiry_at",
                                "ingredients",
                            ],
                        },
                        "minItems": 1,
                        "maxItems": 6,
                        "uniqueItems": True,
                        "description": "The exact product facts requested by the customer.",
                    },
                },
                "required": ["product_id", "fields"],
                "additionalProperties": False,
            },
        },
    },
]


class SearchProductsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(default="", max_length=160)
    category: Literal["drink", "dairy", "snack", "meal"] | None = None
    max_price_vnd: int | None = Field(default=None, ge=0, le=100_000_000)
    spicy: bool | None = None
    in_stock: bool = False
    limit: int = Field(default=5, ge=1, le=MAX_PRODUCT_RESULTS)


class GetProductSnapshotArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: str = Field(min_length=1, max_length=64)
    fields: list[FactField] = Field(min_length=1, max_length=6)


@dataclass(frozen=True)
class ToolObservation:
    content: dict[str, Any]
    products: tuple[ProductSnapshot, ...] = ()
    tool_name: str = ""
    status: ToolStatus = "SUCCESS"
    source: str = "demo_product_catalog"
    requested_fields: tuple[str, ...] = ()
    provenance_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    response_mode: ResponseMode = "NEEDS_LLM_COMPOSITION"
    terminal: bool = False

    def to_envelope(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "status": self.status,
            "source": self.source,
            "timestamp": self.timestamp,
            "provenance_id": self.provenance_id,
            "response_mode": self.response_mode,
            "terminal": self.terminal,
            "data": self.content,
        }


def tools_for_state(state: SessionState) -> list[dict[str, Any]]:
    if state in {SessionState.BROWSING, SessionState.CART_ACTIVE}:
        return CATALOG_TOOL_DEFINITIONS
    return []


def _tool_error(
    name: str,
    status: ToolStatus,
    error: str,
    *,
    source: str = "tool_gateway",
    extra: dict[str, Any] | None = None,
    response_mode: ResponseMode = "NEEDS_LLM_COMPOSITION",
    terminal: bool = False,
) -> ToolObservation:
    return ToolObservation(
        content={"error": error, **(extra or {})},
        tool_name=name,
        status=status,
        source=source,
        response_mode=response_mode,
        terminal=terminal,
    )


def execute_catalog_tool(
    name: str,
    raw_arguments: str,
    *,
    require_sellable: bool = False,
) -> ToolObservation:
    if name not in {"search_products", "get_product_snapshot"}:
        return _tool_error(
            name,
            "DENIED",
            "This tool is not available to the customer agent.",
            source="policy_gateway",
        )

    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")

        if name == "search_products":
            parsed = SearchProductsArguments.model_validate(arguments)
            sellable_only = parsed.in_stock or require_sellable
            products = tuple(
                search_products(
                    query=parsed.query,
                    category=parsed.category,
                    max_price_vnd=parsed.max_price_vnd,
                    spicy=parsed.spicy,
                    in_stock=sellable_only,
                    limit=parsed.limit,
                )
            )
            matched_constraints = [
                key
                for key in ("category", "max_price_vnd", "spicy", "in_stock")
                if key in parsed.model_fields_set
                and (key != "in_stock" or sellable_only)
            ]
            if require_sellable and "in_stock" not in matched_constraints:
                matched_constraints.append("in_stock")
            return ToolObservation(
                content={
                    "items": [
                        {
                            "product_id": product.product_id,
                            "name": product.name,
                            "category": product.category,
                            "matched_constraints": matched_constraints,
                        }
                        for product in products
                    ],
                    "count": len(products),
                    "recommendation_mode": require_sellable,
                    "enforced_constraints": (
                        ["in_stock"] if require_sellable and not parsed.in_stock else []
                    ),
                    "data_timestamp": DATA_TIMESTAMP.isoformat(),
                },
                products=products,
                tool_name=name,
                response_mode="CANDIDATE_LIST" if products else "DETERMINISTIC_ERROR",
                terminal=not products,
            )

        parsed = GetProductSnapshotArguments.model_validate(arguments)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return _tool_error(
            name,
            "INVALID_ARGUMENT",
            "Invalid arguments. Follow the provided tool schema.",
        )

    product = get_product_snapshot(parsed.product_id)
    if product is None:
        return _tool_error(
            name,
            "NOT_FOUND",
            "Product not found in the catalog.",
            source="demo_product_catalog",
            extra={"product_id": parsed.product_id},
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )

    data: dict[str, Any] = {"product_id": product.product_id, "name": product.name}
    for field_name in parsed.fields:
        if field_name == "stock":
            data["stock"] = product.sellable_stock
            if product.sellable_stock == 0:
                try:
                    expiry_date = datetime.fromisoformat(product.expiry_at).date()
                    expired = expiry_date < datetime.now(UTC).date()
                except ValueError:
                    expired = True
                data["availability_status"] = "EXPIRED" if expired else "OUT_OF_STOCK"
                if expired:
                    data["expiry_at"] = product.expiry_at
        else:
            data[field_name] = getattr(product, field_name)

    return ToolObservation(
        content={"product": data, "data_timestamp": DATA_TIMESTAMP.isoformat()},
        products=(product,),
        tool_name=name,
        requested_fields=tuple(parsed.fields),
        response_mode="DETERMINISTIC_FACT",
        terminal=True,
    )
