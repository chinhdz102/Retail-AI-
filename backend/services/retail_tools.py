from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.products.catalog import (
    DATA_TIMESTAMP,
    ProductSnapshot,
    get_product_snapshot,
    search_products,
)
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10

CATALOG_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Search the verified demo catalog. Use this for product discovery, "
                "recommendations, and product facts when the name is uncertain. "
                "Only the declared filters are supported."
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
                    "in_stock": {"type": "boolean"},
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
                "Get verified price, stock, shelf, promotion, ingredients, and expiry "
                "for one product ID returned by search_products."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                    }
                },
                "required": ["product_id"],
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
    in_stock: bool = True
    limit: int = Field(default=5, ge=1, le=MAX_PRODUCT_RESULTS)


class GetProductSnapshotArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: str = Field(min_length=1, max_length=64)


@dataclass(frozen=True)
class ToolObservation:
    content: dict[str, Any]
    products: tuple[ProductSnapshot, ...] = ()


def tools_for_state(state: SessionState) -> list[dict[str, Any]]:
    if state in {SessionState.BROWSING, SessionState.CART_ACTIVE}:
        return CATALOG_TOOL_DEFINITIONS
    return []


def execute_catalog_tool(name: str, raw_arguments: str) -> ToolObservation:
    if name not in {"search_products", "get_product_snapshot"}:
        return ToolObservation({"error": "This tool is not available to the customer agent."})

    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")

        if name == "search_products":
            parsed = SearchProductsArguments.model_validate(arguments)
            products = tuple(
                search_products(
                    query=parsed.query,
                    category=parsed.category,
                    max_price_vnd=parsed.max_price_vnd,
                    spicy=parsed.spicy,
                    in_stock=parsed.in_stock,
                    limit=parsed.limit,
                )
            )
            return ToolObservation(
                {
                    "items": [product.model_dump(mode="json") for product in products],
                    "count": len(products),
                    "data_timestamp": DATA_TIMESTAMP.isoformat(),
                },
                products,
            )

        parsed = GetProductSnapshotArguments.model_validate(arguments)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return ToolObservation(
            {"error": "Invalid arguments. Follow the provided tool schema."}
        )

    product = get_product_snapshot(parsed.product_id)
    if product is None:
        return ToolObservation(
            {"error": "Product not found in the catalog.", "product_id": parsed.product_id}
        )
    return ToolObservation(
        {
            "product": product.model_dump(mode="json"),
            "data_timestamp": DATA_TIMESTAMP.isoformat(),
        },
        (product,),
    )
