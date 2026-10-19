from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product as cart_product
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.products.catalog import PriceOperator, ProductSnapshot
from backend.products.repository import ProductRepository, get_product_repository
from backend.services.retail_information import (
    RetailInformationKind,
    lookup_retail_information,
)
from backend.services.vietnamese_intent import (
    explicit_recommendation_category as _explicit_category,
)
from backend.services.vietnamese_intent import (
    normalize_vietnamese as _normalize_vietnamese,
)
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10
MAX_RECOMMENDATION_CATEGORIES = 4
FactField = Literal[
    "price_vnd",
    "stock",
    "shelf",
    "promotion",
    "expiry_at",
    "ingredients",
    "spicy",
    "data_timestamp",
]
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
                "For a whole-cart request spanning multiple categories, pass categories and "
                "total_budget_vnd; the backend computes combinations that never exceed that cap. "
                "For 'under' use LT; for 'at most/up to' use LTE."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "category": {"type": "string"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": MAX_RECOMMENDATION_CATEGORIES,
                        "uniqueItems": True,
                        "description": (
                            "Use for a combo that must include every requested category."
                        ),
                    },
                    "total_budget_vnd": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100000000,
                        "description": "Whole-cart cap; the backend computes each combo total.",
                    },
                    "quantity_per_category": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Units of each category per proposed combo.",
                    },
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
                "promotion, expiry_at, ingredients, spicy, data_timestamp. For shelf, preserve "
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
                                "data_timestamp",
                            ],
                        },
                        "minItems": 1,
                        "maxItems": 8,
                        "uniqueItems": True,
                    },
                },
                "required": ["product_id", "fields"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_product_promotions",
            "description": (
                "List product-level promotion text recorded in the latest catalog snapshot. "
                "Use for broad questions about recorded offers when no single product is named. "
                "This is not a campaign calendar: the source has no verified start/end dates, "
                "eligibility rules, or guarantee that an offer is still active."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Optional product category from the user's question.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_new_catalog_products",
            "description": (
                "List products newly present in the latest complete catalog compared with "
                "the previous imported catalog. This proves only that the product is new to "
                "the catalog, not when it launched in the market. If fewer than two complete "
                "catalog versions exist, the tool returns DATA_UNAVAILABLE."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Optional product category from the user's question.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
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
    categories: list[str] | None = Field(
        default=None, min_length=2, max_length=MAX_RECOMMENDATION_CATEGORIES
    )
    total_budget_vnd: int | None = Field(default=None, ge=1, le=100_000_000)
    quantity_per_category: int = Field(default=1, ge=1, le=10)
    brand: str | None = Field(default=None, max_length=120)
    price_constraint: PriceConstraint | None = None
    spicy: bool | None = None
    limit: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def require_budget_for_multi_category(self) -> RecommendProductsArguments:
        if self.categories is not None and self.total_budget_vnd is None:
            raise ValueError("categories requires a whole-cart budget")
        if self.total_budget_vnd is not None and self.categories is None:
            raise ValueError("a whole-cart budget requires multiple categories")
        if self.categories is not None:
            normalized = [category.strip().casefold() for category in self.categories]
            if any(not category for category in normalized) or len(set(normalized)) != len(
                normalized
            ):
                raise ValueError("categories must be unique and non-empty")
        return self


class GetProductSnapshotArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: str = Field(min_length=1, max_length=64)
    fields: list[FactField] = Field(min_length=1, max_length=8)


class ListProductPromotionsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: str | None = Field(default=None, max_length=160)
    limit: int = Field(default=5, ge=1, le=5)


class ListNewCatalogProductsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: str | None = Field(default=None, max_length=160)
    limit: int = Field(default=5, ge=1, le=5)


class RetailInformationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: str | None = Field(default=None, max_length=160)


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


def _whole_cart_recommendation(
    repo: ProductRepository,
    parsed: RecommendProductsArguments,
) -> ToolObservation:
    categories = parsed.categories or []
    labels = {
        "drink": "đồ uống",
        "snack": "đồ ăn vặt",
        "dairy": "sữa",
        "meal": "đồ ăn chính",
    }
    candidate_groups: list[list[ProductSnapshot]] = []
    missing_categories: list[str] = []
    for category in categories:
        repository_category = _category_for_repository(category, repo)
        candidates = [
            item
            for item in repo.search(
                category=repository_category,
                in_stock=True,
                limit=MAX_PRODUCT_RESULTS,
            )
            if item.sellable_stock >= parsed.quantity_per_category
        ]
        candidates.sort(key=lambda item: (item.price_vnd, item.product_id))
        if not candidates:
            missing_categories.append(category)
        candidate_groups.append(candidates)

    options: list[dict[str, Any]] = []
    if not missing_categories:
        valid_combos = []
        for combo in cart_product(*candidate_groups):
            total = sum(
                item.price_vnd * parsed.quantity_per_category for item in combo
            )
            if total <= parsed.total_budget_vnd:
                valid_combos.append((total, combo))
        valid_combos.sort(
            key=lambda row: (row[0], tuple(item.product_id for item in row[1]))
        )
        chosen: list[tuple[int, tuple[ProductSnapshot, ...]]] = []
        if valid_combos:
            chosen.append(valid_combos[0])
            if valid_combos[-1] != valid_combos[0]:
                chosen.append(valid_combos[-1])
            if len(chosen) < parsed.limit:
                middle = sorted(
                    valid_combos[1:-1],
                    key=lambda row: (
                        abs(row[0] - parsed.total_budget_vnd * 0.75),
                        tuple(item.product_id for item in row[1]),
                    ),
                )
                chosen.extend(middle[: parsed.limit - len(chosen)])
        for index, (total, combo) in enumerate(chosen):
            options.append(
                {
                    "label": "Tiết kiệm" if index == 0 else "Phương án khác",
                    "items": [
                        {
                            "product_id": item.product_id,
                            "name": item.name,
                            "category": item.category,
                            "category_label": labels.get(category, category),
                            "quantity": parsed.quantity_per_category,
                            "unit_price_vnd": item.price_vnd,
                            "line_total_vnd": item.price_vnd * parsed.quantity_per_category,
                        }
                        for category, item in zip(categories, combo, strict=True)
                    ],
                    "total_vnd": total,
                    "remaining_budget_vnd": parsed.total_budget_vnd - total,
                }
            )

    selected_products: dict[str, ProductSnapshot] = {}
    for option in options:
        for item in option["items"]:
            product_id = item["product_id"]
            for group in candidate_groups:
                match = next(
                    (candidate for candidate in group if candidate.product_id == product_id),
                    None,
                )
                if match is not None:
                    selected_products[product_id] = match
                    break
    timestamp = max(
        (item.data_timestamp for item in selected_products.values()),
        default=datetime.now(UTC),
    )
    content = {
        "recommendation_type": "whole_cart",
        "categories": categories,
        "category_labels": [labels.get(category, category) for category in categories],
        "missing_category_labels": [
            labels.get(category, category) for category in missing_categories
        ],
        "total_budget_vnd": parsed.total_budget_vnd,
        "quantity_per_category": parsed.quantity_per_category,
        "options": options,
        "missing_categories": missing_categories,
        "count": len(options),
        "data_timestamp": timestamp.isoformat(),
        "cart_mutated": False,
    }
    return ToolObservation(
        content=content,
        products=tuple(selected_products.values()),
        tool_name="recommend_products",
        source=repo.source_name,
        response_mode="DETERMINISTIC_RECOMMENDATION",
        terminal=True,
    )


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
    if name not in {
        "search_products",
        "recommend_products",
        "get_product_snapshot",
        "list_product_promotions",
        "list_new_catalog_products",
    }:
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
        if name == "list_product_promotions":
            parsed = ListProductPromotionsArguments.model_validate(arguments)
            list_promotions = getattr(repo, "list_promotions", None)
            if list_promotions is None:
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The active catalog does not provide a promotion listing.",
                    source=repo.source_name,
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            category = _category_for_repository(parsed.category, repo)
            products = tuple(
                product
                for product in list_promotions(category=category, limit=parsed.limit)
                if product.sellable_stock > 0
            )
            latest_timestamp = max(
                (product.data_timestamp for product in products),
                default=None,
            )
            return ToolObservation(
                content={
                    "items": [
                        {
                            "product_id": product.product_id,
                            "name": product.name,
                            "promotion": product.promotion,
                            "data_timestamp": product.data_timestamp.isoformat(),
                        }
                        for product in products
                    ],
                    "count": len(products),
                    "latest_snapshot_at": (
                        latest_timestamp.isoformat() if latest_timestamp else None
                    ),
                    "snapshot_only": True,
                    "campaign_validity_verified": False,
                },
                products=products,
                tool_name=name,
                source=repo.source_name,
                requested_fields=("promotion",),
                response_mode="DETERMINISTIC_FACT" if products else "DETERMINISTIC_ERROR",
                terminal=True,
            )
        if name == "list_new_catalog_products":
            parsed = ListNewCatalogProductsArguments.model_validate(arguments)
            list_additions = getattr(repo, "list_catalog_additions", None)
            if list_additions is None:
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The active catalog cannot compare complete import versions.",
                    source=repo.source_name,
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            category = _category_for_repository(parsed.category, repo)
            comparison = list_additions(category=category, limit=parsed.limit)
            if not comparison.comparable:
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "At least two complete imported catalog versions are required.",
                    source=repo.source_name,
                    extra={
                        "current_imported_at": (
                            comparison.current_imported_at.isoformat()
                            if comparison.current_imported_at
                            else None
                        ),
                        "previous_imported_at": None,
                    },
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            products = tuple(comparison.products)
            return ToolObservation(
                content={
                    "items": [
                        {
                            "product_id": product.product_id,
                            "name": product.name,
                            "category": product.category,
                            "brand": product.brand,
                        }
                        for product in products
                    ],
                    "count": len(products),
                    "current_imported_at": comparison.current_imported_at.isoformat(),
                    "previous_imported_at": comparison.previous_imported_at.isoformat(),
                    "meaning": "newly present in catalog; market launch date is unknown",
                },
                products=products,
                tool_name=name,
                source=repo.source_name,
                response_mode="DETERMINISTIC_FACT",
                terminal=True,
            )
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
            if parsed.categories is not None:
                return _whole_cart_recommendation(repo, parsed)
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
            content = _candidate_content(products, matched)
            content["constraints"] = {
                "category": category,
                "brand": parsed.brand,
                "spicy": parsed.spicy,
                "price_operator": price.operator if price else None,
                "price_value_vnd": price.value_vnd if price else None,
            }
            return ToolObservation(
                content=content,
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
        elif field_name == "data_timestamp":
            data[field_name] = product.data_timestamp.isoformat()
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


def execute_retail_information_tool(
    name: str,
    raw_arguments: str,
    *,
    kiosk_id: str,
) -> ToolObservation:
    """Execute a branch-scoped read-only tool; kiosk identity is server supplied."""
    kinds: dict[str, RetailInformationKind] = {
        "get_store_information": "store",
        "list_active_store_promotions": "promotions",
        "list_recent_stock_receipts": "receipts",
        "list_nearby_places_for_store": "nearby",
    }
    kind = kinds.get(name)
    if kind is None:
        return _tool_error(
            name,
            "DENIED",
            "This tool is not available to the customer agent.",
            source="policy_gateway",
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )
    try:
        raw = json.loads(raw_arguments)
        if not isinstance(raw, dict):
            raise ValueError("Tool arguments must be an object")
        parsed = RetailInformationArguments.model_validate(raw)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return _tool_error(
            name,
            "INVALID_ARGUMENT",
            "Invalid arguments. Follow the provided tool schema.",
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )

    result = lookup_retail_information(kind, kiosk_id, parsed.category)

    def json_safe(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [json_safe(item) for item in value]
        return value

    content = {
        "store": json_safe(result.store),
        "items": json_safe(result.items),
        "count": len(result.items),
        "reason": result.reason,
        "source_updated_at": json_safe(result.source_updated_at),
    }
    return ToolObservation(
        content=content,
        tool_name=name,
        status=result.status,
        source=result.source,
        response_mode=(
            "DETERMINISTIC_FACT"
            if result.status == "SUCCESS" and result.items
            else "DETERMINISTIC_ERROR"
        ),
        terminal=True,
    )
