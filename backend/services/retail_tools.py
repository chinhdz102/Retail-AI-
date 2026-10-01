from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.products.catalog import PriceOperator, ProductSnapshot
from backend.products.repository import ProductRepository, get_product_repository
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10
FactField = Literal["price_vnd", "stock", "shelf", "promotion", "expiry_at", "ingredients"]
ToolStatus = Literal[
    "SUCCESS", "NOT_FOUND", "INVALID_ARGUMENT", "DENIED", "TEMPORARY_FAILURE", "DATA_UNAVAILABLE"
]
ResponseMode = Literal[
    "DETERMINISTIC_FACT",
    "DETERMINISTIC_ERROR",
    "DETERMINISTIC_RECOMMENDATION",
    "CANDIDATE_LIST",
    "NEEDS_LLM_COMPOSITION",
]


def _search_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Product name, brand, category, code, or barcode.",
            },
            "category": {
                "type": "string",
                "description": "A catalog category, including free-form categories.",
            },
            "brand": {"type": "string"},
            "max_price_vnd": {"type": "integer", "minimum": 0},
            "spicy": {"type": "boolean"},
            "in_stock": {
                "type": "boolean",
                "description": "True to filter out products with zero stock.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PRODUCT_RESULTS},
        },
        "additionalProperties": False,
    }


CATALOG_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Find matching catalog products for lookup. This may return out-of-stock products. "
                "Use get_product_snapshot to verify the exact facts the customer asked for."
            ),
            "parameters": _search_parameters(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_products",
            "description": (
                "Return product suggestions for an explicit recommendation request. "
                "The backend always filters to products with positive stock. Apply every "
                "stated category, brand, spice, and structured price constraint as a hard filter. "
                "For 'under' use LT; for 'at most/up to' use LTE."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "category": {"type": "string"},
                    "brand": {"type": "string"},
                    "price_constraint": {
                        "type": "object",
                        "properties": {
                            "operator": {
                                "type": "string",
                                "enum": ["LT", "LTE", "GT", "GTE", "EQ"],
                            },
                            "value_vnd": {"type": "integer", "minimum": 0},
                        },
                        "required": ["operator", "value_vnd"],
                        "additionalProperties": False,
                    },
                    "spicy": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 3},
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
                "Read verified facts for one product ID returned by search_products. "
                "Set fields to exactly those requested: price_vnd, stock, shelf, "
                "promotion, expiry_at, ingredients."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "minLength": 1, "maxLength": 64},
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
    category: str | None = Field(default=None, max_length=160)
    brand: str | None = Field(default=None, max_length=120)
    max_price_vnd: int | None = Field(default=None, ge=0, le=100_000_000)
    spicy: bool | None = None
    in_stock: bool = False
    limit: int = Field(default=5, ge=1, le=MAX_PRODUCT_RESULTS)


class PriceConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operator: PriceOperator
    value_vnd: int = Field(ge=0, le=100_000_000)


class RecommendProductsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(default="", max_length=160)
    category: str | None = Field(default=None, max_length=160)
    brand: str | None = Field(default=None, max_length=120)
    price_constraint: PriceConstraint | None = None
    spicy: bool | None = None
    limit: int = Field(default=3, ge=1, le=3)


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


def _candidate_content(products: tuple[ProductSnapshot, ...], matched: list[str]) -> dict[str, Any]:
    return {
        "items": [
            {
                "product_id": product.product_id,
                "name": product.name,
                "category": product.category,
                "brand": product.brand,
                "matched_constraints": matched,
            }
            for product in products
        ],
        "count": len(products),
        "data_timestamp": max(
            (item.data_timestamp for item in products), default=datetime.now(UTC)
        ).isoformat(),
    }


def execute_catalog_tool(
    name: str,
    raw_arguments: str,
    *,
    repository: ProductRepository | None = None,
) -> ToolObservation:
    if name not in {"search_products", "recommend_products", "get_product_snapshot"}:
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
        repo = repository or get_product_repository()
        if name == "search_products":
            parsed = SearchProductsArguments.model_validate(arguments)
            products = tuple(
                repo.search(
                    query=parsed.query,
                    category=parsed.category,
                    brand=parsed.brand,
                    max_price_vnd=parsed.max_price_vnd,
                    spicy=parsed.spicy,
                    in_stock=parsed.in_stock,
                    limit=parsed.limit,
                )
            )
            matched = [
                key
                for key in ("category", "brand", "max_price_vnd", "spicy", "in_stock")
                if key in parsed.model_fields_set
            ]
            content = _candidate_content(products, matched)
            return ToolObservation(
                content=content,
                products=products,
                tool_name=name,
                source=repo.source_name,
                response_mode="CANDIDATE_LIST" if products else "DETERMINISTIC_ERROR",
                terminal=not products,
            )

        if name == "recommend_products":
            parsed = RecommendProductsArguments.model_validate(arguments)
            price = parsed.price_constraint
            products = tuple(
                repo.search(
                    query=parsed.query,
                    category=parsed.category,
                    brand=parsed.brand,
                    spicy=parsed.spicy,
                    in_stock=True,
                    limit=parsed.limit,
                    price_operator=price.operator if price else None,
                    price_value_vnd=price.value_vnd if price else None,
                )
            )
            matched = ["sellable_only"]
            matched.extend(
                key for key in ("category", "brand", "spicy") if key in parsed.model_fields_set
            )
            if price:
                matched.append(f"price_{price.operator.lower()}_{price.value_vnd}")
            return ToolObservation(
                content=_candidate_content(products, matched),
                products=products,
                tool_name=name,
                source=repo.source_name,
                response_mode="DETERMINISTIC_RECOMMENDATION",
                terminal=True,
            )

        parsed = GetProductSnapshotArguments.model_validate(arguments)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return _tool_error(
            name, "INVALID_ARGUMENT", "Invalid arguments. Follow the provided tool schema."
        )

    product = repo.get_product_snapshot(parsed.product_id)
    if product is None:
        return _tool_error(
            name,
            "NOT_FOUND",
            "Product not found in the catalog.",
            source=repo.source_name,
            extra={"product_id": parsed.product_id},
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )

    unavailable = [
        field_name
        for field_name in parsed.fields
        if field_name in {"shelf", "expiry_at", "ingredients"}
        and getattr(product, field_name) is None
    ]
    if unavailable:
        return ToolObservation(
            content={"product_id": product.product_id, "unavailable_fields": unavailable},
            products=(product,),
            tool_name=name,
            source=repo.source_name,
            requested_fields=tuple(parsed.fields),
            status="DATA_UNAVAILABLE",
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )

    data: dict[str, Any] = {"product_id": product.product_id, "name": product.name}
    for field_name in parsed.fields:
        if field_name == "stock":
            data["stock"] = product.sellable_stock
            if product.sellable_stock == 0:
                expiry = product.expiry_at
                try:
                    expired = (
                        bool(expiry)
                        and datetime.fromisoformat(expiry).date() < datetime.now(UTC).date()
                    )
                except ValueError:
                    expired = True
                data["availability_status"] = "EXPIRED" if expired else "OUT_OF_STOCK"
                if expired:
                    data["expiry_at"] = expiry
        else:
            data[field_name] = getattr(product, field_name)

    return ToolObservation(
        content={"product": data, "data_timestamp": product.data_timestamp.isoformat()},
        products=(product,),
        tool_name=name,
        source=repo.source_name,
        requested_fields=tuple(parsed.fields),
        response_mode="DETERMINISTIC_FACT",
        terminal=True,
    )
