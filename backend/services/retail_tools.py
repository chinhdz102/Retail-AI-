from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.products.catalog import PriceOperator, ProductSnapshot
from backend.products.repository import ProductRepository, get_product_repository
from backend.services.vietnamese_intent import (
    explicit_recommendation_category as _explicit_category,
)
from backend.services.vietnamese_intent import (
    normalize_vietnamese as _normalize_vietnamese,
)
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10
FactField = Literal["price_vnd", "stock", "shelf", "promotion", "expiry_at", "ingredients", "spicy"]
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
                "promotion, expiry_at, ingredients, spicy. For shelf, preserve "
                "shelf_source: RECOMMENDED_PLANOGRAM means this is a proposed locator, "
                "not a verified physical store location."
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
                                "spicy",
                            ],
                        },
                        "minItems": 1,
                        "maxItems": 7,
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
    fields: list[FactField] = Field(min_length=1, max_length=7)


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


def _category_for_repository(category: str | None, repository: ProductRepository) -> str | None:
    if not category or repository.source_name != "winmart_postgres_catalog":
        return category
    canonical_category = category.strip().casefold()
    # The WinMart source stores Vietnamese taxonomy labels rather than the
    # agent's stable category IDs. Translate only known IDs; keep free-form
    # customer/model categories intact.
    return {
        "drink": "nước uống",
        "snack": "bánh snack",
        "dairy": "sữa",
        "meal": "thực phẩm ăn liền",
    }.get(canonical_category, category)


def _query_category_hint(query: str) -> tuple[str | None, bool]:
    """Resolve an explicit category in the query and whether it is category-only."""
    category = _explicit_category(query)
    if category is None:
        return None, False
    normalized_query = " ".join(_normalize_vietnamese(query).split())
    category_only = normalized_query in {
        "an nhe",
        "an vat",
        "bim bim",
        "banh keo",
        "do an nhe",
        "do an vat",
        "mon an nhe",
        "mon an vat",
        "nham nhi",
        "snack",
        "snacks",
        "crisps",
        "chips",
        "drink",
        "drinks",
        "beverage",
        "beverages",
        "water",
        "soft drink",
        "soft drinks",
        "soda",
        "coffee",
        "tea",
        "thirsty",
        "dairy",
        "milk",
        "yogurt",
        "yoghurt",
        "cheese",
        "meal",
        "meals",
        "food",
        "breakfast",
        "lunch",
        "dinner",
        "ready meal",
        "instant noodles",
        "noodles",
        "hot food",
        "hungry",
    }
    return category, category_only


_ENGLISH_CATALOG_TERMS = (
    ("soft drinks", "nuoc ngot"),
    ("soft drink", "nuoc ngot"),
    ("mineral water", "nuoc suoi"),
    ("bottled water", "nuoc suoi"),
    ("drinking water", "nuoc uong"),
    ("milk tea", "tra sua"),
    ("instant noodles", "mi an lien"),
    ("potato chips", "snack khoai tay"),
    ("potato crisps", "snack khoai tay"),
    ("coke zero", "coca zero"),
    ("coke", "coca"),
    ("water", "nuoc"),
    ("beverages", "nuoc uong"),
    ("beverage", "nuoc uong"),
    ("drinks", "nuoc uong"),
    ("drink", "nuoc uong"),
    ("soda", "nuoc ngot"),
    ("coffee", "ca phe"),
    ("tea", "tra"),
    ("milk", "sua"),
    ("dairy", "sua"),
    ("yoghurt", "sua chua"),
    ("yogurt", "sua chua"),
    ("cheese", "pho mai"),
    ("crisps", "snack"),
    ("chips", "snack"),
    ("noodles", "mi"),
    ("breakfast", "bua sang"),
    ("lunch", "bua trua"),
    ("dinner", "bua toi"),
    ("ready meal", "thuc an san"),
    ("hot food", "do an nong"),
    ("meals", "thuc an"),
    ("meal", "thuc an"),
    ("food", "thuc an"),
)
_ENGLISH_CATALOG_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "at", "can", "could", "do", "does", "for",
        "have", "has", "how", "i", "in", "is", "it", "me", "my", "of", "on",
        "please", "the", "this", "that", "to", "we", "what", "where", "which",
        "who", "with", "you", "your", "find", "get", "looking", "recommend",
        "suggest", "under", "below", "up", "less", "than", "no", "more", "most",
        "price", "cost", "shelf", "aisle", "stock", "available", "deal", "deals",
        "promotion", "promotions", "offer", "offers", "discount", "sale", "expiry",
        "expiration", "ingredients", "contains", "inside", "mild", "spicy", "not",
        "non", "budget", "left", "in", "many", "much", "today", "current",
    }
)


def _normalize_catalog_query(query: str) -> str:
    """Translate common English catalog terms and remove question filler before lookup."""
    normalized = " ".join(_normalize_vietnamese(query).split())
    english_signals = (
        "where", "what", "which", "who", "can", "could", "does", "have", "find",
        "please", "recommend", "suggest", "looking for", "under", "below", "less than",
        "up to", "at most", "price", "cost", "in stock", "available", "promotion",
        "deal", "offer", "discount", "expiry", "expiration", "ingredients", "shelf",
        "aisle", "milk", "coffee", "water", "beverage", "drink", "dairy", "yogurt",
        "yoghurt", "cheese", "crisps", "chips", "noodles", "breakfast", "lunch",
        "dinner", "food", "meal", "soda", "tea",
    )
    has_english_signal = any(
        re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", normalized)
        for signal in english_signals
    )
    if not has_english_signal:
        return query.strip()
    normalized = re.sub(
        r"\b(?:under|below|less than|no more than|at most|up to|duoi|toi da|khong qua)\s+"
        r"\d[\d\s]*(?:k|ngan|nghin|thousand|million|trieu|vnd|dong|d)?\b",
        " ",
        normalized,
    )
    for english, vietnamese in _ENGLISH_CATALOG_TERMS:
        normalized = re.sub(
            rf"(?<!\w){re.escape(english)}(?!\w)", vietnamese, normalized
        )
    return " ".join(
        token for token in normalized.split() if token not in _ENGLISH_CATALOG_STOP_WORDS
    )


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
            query_category, category_only_query = _query_category_hint(parsed.query)
            category = _category_for_repository(query_category or parsed.category, repo)
            search_criteria = {
                "query": _normalize_catalog_query(parsed.query),
                "category": category,
                "brand": parsed.brand,
                "max_price_vnd": parsed.max_price_vnd,
                "spicy": parsed.spicy,
                "in_stock": parsed.in_stock,
                "limit": parsed.limit,
            }
            products = tuple(repo.search(**search_criteria))
            if not products and category_only_query:
                # Keep the product-name match when present (e.g. "Bim bim Poppedcorn");
                # only broaden within the already-constrained snack category if absent.
                search_criteria["query"] = ""
                products = tuple(repo.search(**search_criteria))
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
            query_category, category_only_query = _query_category_hint(parsed.query)
            category = _category_for_repository(query_category or parsed.category, repo)
            search_criteria = {
                "query": _normalize_catalog_query(parsed.query),
                "category": category,
                "brand": parsed.brand,
                "spicy": parsed.spicy,
                "in_stock": True,
                "limit": parsed.limit,
                "price_operator": price.operator if price else None,
                "price_value_vnd": price.value_vnd if price else None,
            }
            products = tuple(repo.search(**search_criteria))
            if not products and category_only_query:
                search_criteria["query"] = ""
                products = tuple(repo.search(**search_criteria))
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
        if field_name in {"shelf", "expiry_at", "ingredients", "spicy"}
        and getattr(product, field_name) is None
    ]
    available_fields = tuple(
        field_name for field_name in parsed.fields if field_name not in unavailable
    )
    if unavailable and not available_fields:
        return ToolObservation(
            content={
                "product_id": product.product_id,
                "name": product.name,
                "unavailable_fields": unavailable,
            },
            products=(product,),
            tool_name=name,
            source=repo.source_name,
            requested_fields=(),
            status="DATA_UNAVAILABLE",
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )

    data: dict[str, Any] = {"product_id": product.product_id, "name": product.name}
    for field_name in available_fields:
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
            if field_name == "shelf":
                data["shelf_source"] = product.shelf_source
                if product.shelf_source == "RECOMMENDED_PLANOGRAM":
                    data["shelf_note"] = (
                        "Taxonomy-based proposed locator; physical placement at the store "
                        "has not been verified."
                    )

    return ToolObservation(
        content={
            "product": data,
            "data_timestamp": product.data_timestamp.isoformat(),
            "unavailable_fields": unavailable,
        },
        products=(product,),
        tool_name=name,
        source=repo.source_name,
        requested_fields=available_fields,
        response_mode="DETERMINISTIC_FACT",
        terminal=True,
    )
