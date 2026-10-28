from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from functools import lru_cache
from itertools import product as cart_product
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.products.catalog import (
    DEMO_PRODUCTS,
    PRODUCT_ALIASES,
    PriceOperator,
    ProductSnapshot,
    explicit_product_audience,
    has_household_child_context,
    matches_explicit_product_audience,
    product_audience_evidence,
)
from backend.products.category_taxonomy import (
    CatalogCategoryMatch,
    catalog_category_alias_source_phrase,
    catalog_leaf_query_phrase,
    category_alias_refines_explicit_audience,
    category_is_parent,
    category_leaf,
    normalize_category_text,
    query_names_catalog_leaf,
    resolve_catalog_category,
    resolve_catalog_category_alias,
)
from backend.products.repository import ProductRepository, get_product_repository
from backend.services.catalog_preferences import (
    excluded_catalog_brands,
    preparation_terms,
    remove_excluded_brand_phrases,
    search_with_preferences,
    title_mentions_preparation,
)
from backend.services.query_understanding import (
    QueryUnderstandingResult,
    _budget_match,
    understand_catalog_query,
)
from backend.services.retail_information import (
    RetailInformationKind,
    lookup_retail_information,
)
from backend.services.shopping_mission import build_shopping_mission_plan
from backend.services.vietnamese_intent import (
    explicit_recommendation_category as _explicit_category,
)
from backend.services.vietnamese_intent import (
    explicit_recommendation_query as _explicit_query,
)
from backend.services.vietnamese_intent import (
    normalize_vietnamese as _normalize_vietnamese,
)
from backend.services.vietnamese_intent import (
    product_type_attribute_modifier as _product_type_attribute_modifier,
)
from backend.services.vietnamese_intent import (
    recommendation_category_cues as _category_cues,
)
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10
MAX_RECOMMENDATION_CATEGORIES = 4
MAX_RECOMMENDATION_RESULTS = 5
_PRODUCT_IDENTITY_FILLER = frozenset(
    "minh toi em anh chi ban muon can tim cho xem giup hang loai nhom san pham "
    "mot vai nao nhe nha gia bao nhieu tien nhiu nhieu bn may dong vay zay ta "
    "ben trong gom nhung gi loi ich cong dung suc khoe thanh phan nguyen lieu "
    "o dau ke nao day nao khu nao quay nao vi tri con bao nhieu con hang ton kho "
    "het han dung den bao gio luc thi hom ngay truoc vua xong price cost how much "
    "inside ingredients benefits "
    "health expiry expiration shelf aisle where find product products category type".split()
)
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
                "For a live-stock source, the backend filters to positive stock. For a dated "
                "snapshot source, availability is unverified and must be disclosed. Apply every "
                "stated category, brand, spice, and structured price constraint as a hard filter. "
                "For a whole-cart request spanning multiple categories, pass categories and "
                "total_budget_vnd; set total_budget_exclusive=true for 'under/less than' and "
                "false for 'at most/up to'. The backend computes each combo total exactly. "
                "For a single-item price filter, use LT for 'under' and LTE for 'at most/up to'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "category": {"type": "string"},
                    "preference_text": {
                        "type": "string",
                        "maxLength": 2000,
                        "description": (
                            "Copy the customer's original need verbatim for ranking catalog "
                            "titles. This never verifies product benefits or suitability."
                        ),
                    },
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
                    "total_budget_exclusive": {
                        "type": "boolean",
                        "description": (
                            "True means the full cart total must be strictly less than "
                            "total_budget_vnd; false means it may equal the cap."
                        ),
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
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_RECOMMENDATION_RESULTS,
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
                "Read verified facts for one product ID returned by search_products. "
                "Set fields to exactly those requested: price_vnd, stock, shelf, "
                "promotion, expiry_at, ingredients, spicy, data_timestamp. Treat stock as "
                "unverified when stock_is_current is false; treat null spicy/ingredients as "
                "unknown. For shelf, preserve "
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
            "name": "plan_shopping_mission",
            "description": (
                "Create or revise a read-only multi-product shopping plan from the customer's "
                "goal, occasion, people, budget, and needs. Use when the customer does not know "
                "product names, describes a problem or activity, wants several categories, or "
                "revises an existing plan. Decompose the goal into explicit and inferred needs; "
                "inferred needs are always optional. Choose category values from the supplied "
                "active catalog taxonomy. Keep hard filters structured "
                "(brand, max_price_vnd, spicy). "
                "Put a constraint that applies to one need in that need's unverified_constraints; "
                "use hard_requirements/exclusions only when it applies to every proposed item. "
                "Mark satisfied_by_existing when the customer already has that need. "
                "Always preserve unrelated prior needs and constraints; "
                "use remove_need_ids to remove a need and clear_* flags to replace or clear a "
                "list. For a plan update that changes only budget/preferences, pass needs as an "
                "empty array. Keep stable need IDs across revisions. If asked for a different "
                "option, reject prior proposed product IDs; otherwise preserve prior SKUs that "
                "still satisfy constraints. Set quantity_is_explicit only for a unit count the "
                "customer clearly requested; otherwise use the default quantity. The backend "
                "may estimate servings for drinks and shareable snacks from party size and a "
                "package size printed in the catalog. This tool never mutates the cart."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "maxLength": 300},
                    "use_case": {"type": "string", "maxLength": 240},
                    "occasion": {"type": "string", "maxLength": 160},
                    "party_size": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
                    "total_budget_vnd": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 100000000,
                    },
                    "total_budget_exclusive": {"type": "boolean"},
                    "hard_requirements": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 160},
                        "maxItems": 12,
                    },
                    "exclusions": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 160},
                        "maxItems": 12,
                    },
                    "preferences": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 160},
                        "maxItems": 12,
                    },
                    "existing_items": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 160},
                        "maxItems": 12,
                    },
                    "unknown_details": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 160},
                        "maxItems": 12,
                    },
                    "pending_decision": {"type": ["string", "null"], "maxLength": 180},
                    "needs": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "properties": {
                                "need_id": {"type": "string", "minLength": 1, "maxLength": 40},
                                "label": {"type": "string", "minLength": 1, "maxLength": 100},
                                "rationale": {"type": "string", "maxLength": 180},
                                "category": {"type": "string", "minLength": 1, "maxLength": 160},
                                "alternative_categories": {
                                    "type": "array",
                                    "items": {"type": "string", "maxLength": 160},
                                    "maxItems": 3,
                                },
                                "source": {"type": "string", "enum": ["EXPLICIT", "INFERRED"]},
                                "required": {"type": "boolean"},
                                "quantity": {"type": "integer", "minimum": 1, "maximum": 200},
                                "quantity_is_explicit": {
                                    "type": "boolean",
                                    "description": (
                                        "True only when the customer clearly requested this "
                                        "exact unit count."
                                    ),
                                },
                                "product_query": {"type": "string", "maxLength": 160},
                                "brand": {"type": ["string", "null"], "maxLength": 120},
                                "max_price_vnd": {
                                    "type": ["integer", "null"],
                                    "minimum": 0,
                                    "maximum": 100000000,
                                },
                                "spicy": {"type": ["boolean", "null"]},
                                "preference_terms": {
                                    "type": "array",
                                    "items": {"type": "string", "maxLength": 100},
                                    "maxItems": 8,
                                },
                                "unverified_constraints": {
                                    "type": "array",
                                    "items": {"type": "string", "maxLength": 160},
                                    "maxItems": 8,
                                },
                                "satisfied_by_existing": {"type": "boolean"},
                            },
                            "required": ["need_id", "label", "category", "source", "required"],
                            "additionalProperties": False,
                        },
                    },
                    "remove_need_ids": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "maxItems": 6,
                    },
                    "rejected_product_ids": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 64},
                        "maxItems": 24,
                    },
                    "reset_mission": {"type": "boolean"},
                    "clear_total_budget": {"type": "boolean"},
                    "clear_party_size": {"type": "boolean"},
                    "clear_preferences": {"type": "boolean"},
                    "clear_exclusions": {"type": "boolean"},
                    "clear_hard_requirements": {"type": "boolean"},
                    "clear_existing_items": {"type": "boolean"},
                    "clear_unknown_details": {"type": "boolean"},
                    "clear_pending_decision": {"type": "boolean"},
                },
                "required": ["goal", "needs"],
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
    preference_text: str = Field(default="", max_length=2000)
    alternative_requested: bool = False
    exclude_product_ids: list[str] = Field(default_factory=list, max_length=300)
    category: str | None = Field(default=None, max_length=160)
    categories: list[str] | None = Field(
        default=None, min_length=2, max_length=MAX_RECOMMENDATION_CATEGORIES
    )
    total_budget_vnd: int | None = Field(default=None, ge=1, le=100_000_000)
    total_budget_exclusive: bool = False
    quantity_per_category: int = Field(default=1, ge=1, le=10)
    brand: str | None = Field(default=None, max_length=120)
    price_constraint: PriceConstraint | None = None
    spicy: bool | None = None
    limit: int = Field(default=MAX_RECOMMENDATION_RESULTS, ge=1, le=MAX_RECOMMENDATION_RESULTS)

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


def _budget_scope_clarification(
    name: str,
    repository: ProductRepository,
    understanding: QueryUnderstandingResult,
) -> ToolObservation:
    return _tool_error(
        name,
        "DATA_UNAVAILABLE",
        "The customer's budget scope or quantity needs clarification before filtering products.",
        source=repository.source_name,
        extra={
            "clarification_code": "budget_scope",
            "clarification_needed": True,
            "budget_vnd": understanding.budget_vnd,
            "quantity": understanding.quantity,
            "unavailable_fields": ["budget_scope", "quantity"],
        },
        response_mode="DETERMINISTIC_ERROR",
        terminal=True,
    )


def _budget_unit_price_cap(understanding: QueryUnderstandingResult) -> int | None:
    """Convert a clear item or total budget into a safe per-SKU price ceiling."""
    budget = understanding.budget_vnd
    if budget is None or understanding.budget_scope is None:
        return None
    exclusive = understanding.budget_operator == "LT"
    if understanding.budget_scope == "total":
        quantity = understanding.quantity
        if quantity is None or quantity < 1:
            return None
        return (budget - 1) // quantity if exclusive else budget // quantity
    return budget - 1 if exclusive else budget


def _has_budget_scope_ambiguity(understanding: QueryUnderstandingResult) -> bool:
    return any("budget" in ambiguity for ambiguity in understanding.ambiguity)


def _candidate_content(products: tuple[ProductSnapshot, ...], matched: list[str]) -> dict[str, Any]:
    items = []
    for product in products:
        item = {
            "product_id": product.product_id,
            "name": product.name,
            "category": product.category,
            "subcategory": category_leaf(product),
            "brand": product.brand,
            "matched_constraints": matched,
        }
        if not product.stock_is_current:
            item.update(
                {
                    "reference_price_vnd": product.price_vnd,
                    "stock_is_current": False,
                    "data_timestamp": product.data_timestamp.isoformat(),
                }
            )
        items.append(item)
    return {
        "items": items,
        "count": len(products),
        "data_timestamp": max(
            (item.data_timestamp for item in products), default=datetime.now(UTC)
        ).isoformat(),
    }


def _negative_constraint_status(
    query: str,
    constraints: tuple[str, ...],
    repository: ProductRepository,
    selected_category: str | None = None,
) -> tuple[tuple[str, ...], frozenset[str], bool | None, tuple[str, ...]]:
    """Separate constraints the active catalog can enforce from unknown attributes."""
    products = getattr(repository, "products", ())
    brands = [product.brand for product in products if getattr(product, "brand", None)]
    if not brands:
        list_brands = getattr(repository, "list_brand_names", None)
        brands = list(list_brands(limit=1000)) if callable(list_brands) else []
    excluded_brands = frozenset(excluded_catalog_brands(query, brands))
    if not constraints:
        # Brand exclusions are resolvable directly against the catalog. They
        # must still be enforced when the language parser intentionally omits
        # a broader/ambiguous negative-constraint span (for example,
        # "không phải hãng X").
        return (), excluded_brands, None, ()
    unsupported: list[str] = []
    excluded_categories: list[str] = []
    requested_spicy: bool | None = None
    for constraint in constraints:
        normalized = normalize_category_text(constraint)
        if excluded_brands and normalized in {
            "hang",
            "nhan hang",
            "nhan hieu",
            "thuong hieu",
            "brand",
        }:
            # A clipped span such as "thương hiệu" is already grounded by
            # the exact catalog brand found in the original query.
            continue
        if normalized == "duong" and re.search(
            r"\bkhong\s+duong\b", normalize_category_text(query)
        ):
            # Treat ``không đường`` as a positive zero-sugar variant only when
            # the selected catalog scope contains explicit matching titles.
            # Broader beverage scopes with no such evidence still abstain.
            try:
                zero_sugar_candidates = repository.search(
                    query="zero",
                    category=selected_category,
                    limit=20,
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                zero_sugar_candidates = ()
            if any(
                re.search(
                    r"\bzero\b|\bkhong\s+duong\b",
                    normalize_category_text(product.name),
                )
                for product in zero_sugar_candidates
            ):
                continue
        brand_is_supported = any(
            normalize_category_text(brand) in excluded_brands
            and normalize_category_text(brand) in normalized
            for brand in brands
            if brand
        )
        if brand_is_supported:
            continue
        if normalized in {"cay", "spicy", "hot"}:
            if getattr(repository, "supports_spicy_filter", False):
                requested_spicy = False
            else:
                unsupported.append(constraint)
            continue
        excluded_category = (
            _category_from_product_type(constraint, repository)
            if len(normalized.split()) >= 2
            else None
        )
        if (
            selected_category
            and excluded_category
            and _taxonomy_categories_are_disjoint(
                selected_category,
                excluded_category,
                repository,
            )
        ):
            excluded_categories.append(excluded_category)
            continue
        if (
            selected_category
            and excluded_category
            and _specific_category_covers_exclusion(
                selected_category,
                constraint,
                excluded_category,
                repository,
            )
        ):
            # An explicitly named leaf already rules out a sibling subtype
            # that the snapshot does not expose as its own taxonomy leaf.
            continue
        unsupported.append(constraint)
    return (
        tuple(dict.fromkeys(unsupported)),
        excluded_brands,
        requested_spicy,
        tuple(dict.fromkeys(excluded_categories)),
    )


def _specific_category_covers_exclusion(
    selected_category: str,
    constraint: str,
    excluded_category: str,
    repository: ProductRepository,
) -> bool:
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return False
    try:
        hierarchies = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return False
    selected = normalize_category_text(selected_category)
    selected_path = next(
        (path for path in hierarchies if path and normalize_category_text(path[-1]) == selected),
        (),
    )
    if not selected_path or category_is_parent(selected_category, hierarchies):
        return False
    excluded = normalize_category_text(constraint)
    selected_tokens = set(selected.split())
    excluded_tokens = set(excluded.split())
    common_tokens = selected_tokens & excluded_tokens
    selected_specific = selected_tokens - excluded_tokens
    excluded_specific = excluded_tokens - selected_tokens
    excluded_is_broader_parent = category_is_parent(excluded_category, hierarchies)
    return bool(
        excluded_is_broader_parent and common_tokens and selected_specific and excluded_specific
    )


def _taxonomy_categories_are_disjoint(
    selected_category: str,
    excluded_category: str,
    repository: ProductRepository,
) -> bool:
    """Return true only when no catalog path places the categories together."""
    if normalize_category_text(selected_category) == normalize_category_text(excluded_category):
        return False
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return False
    try:
        hierarchies = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return False
    selected = normalize_category_text(selected_category)
    excluded = normalize_category_text(excluded_category)
    return not any(
        selected in {normalize_category_text(segment) for segment in path}
        and excluded in {normalize_category_text(segment) for segment in path}
        for path in hierarchies
    )


def _product_is_in_excluded_category(
    product: ProductSnapshot,
    excluded_categories: tuple[str, ...],
) -> bool:
    if not excluded_categories:
        return False
    product_categories = {
        normalize_category_text(segment)
        for segment in (product.category_hierarchy or "").split(">")
        if segment.strip()
    }
    if not product_categories and product.category:
        product_categories.add(normalize_category_text(product.category))
    if any(
        normalize_category_text(category) in product_categories for category in excluded_categories
    ):
        return True
    title = normalize_category_text(product.name)
    for category in excluded_categories:
        category_key = normalize_category_text(category)
        # Catalogs sometimes place carbonated water under the mineral-water
        # leaf. Preserve the user's explicit category exclusion using the
        # product title's verified wording, including the common ``gas`` form.
        if category_key == "nuoc ngot co ga" and re.search(r"\b(?:co\s+ga|gas)\b", title):
            return True
    return False


def _catalog_supported_tokens(
    tokens: tuple[str, ...] | list[str],
    category: str,
    repository: ProductRepository,
) -> frozenset[str]:
    """Check positive-detail evidence in one repository operation when supported."""
    if not tokens:
        return frozenset()
    effective_category = _category_for_repository(category, repository)
    batched_lookup = getattr(repository, "catalog_terms_evidenced", None)
    if callable(batched_lookup):
        try:
            return frozenset(batched_lookup(tuple(tokens), effective_category or category))
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return frozenset()

    supported: set[str] = set()
    for token in tokens:
        try:
            candidates = repository.search(
                query=token,
                category=effective_category or category,
                limit=20,
            )
        except (OSError, RuntimeError, TimeoutError, ValueError):
            continue
        if any(
            token
            in set(
                normalize_category_text(
                    " ".join(filter(None, (product.name, product.brand, product.description)))
                ).split()
            )
            for product in candidates
        ):
            supported.add(token)
    return frozenset(supported)


def _supported_query_details_for_category(
    query: str,
    product_type: str | None,
    use_case: str | None,
    negative_constraints: tuple[str, ...],
    brand: str | None,
    category: str | None,
    repository: ProductRepository,
) -> str:
    """Keep only positive detail words evidenced by product names in a category."""
    if not category:
        return ""
    catalog_brands = [
        product.brand
        for product in getattr(repository, "products", ())
        if product.brand
    ]
    normalized = normalize_category_text(remove_excluded_brand_phrases(query, catalog_brands))
    normalized = re.sub(r"\bngan\s+sach\b", " ", normalized)
    # A package disjunction means either form is acceptable. Searching for
    # both tokens would incorrectly turn that OR into an AND requirement.
    normalized = re.sub(
        r"\b(?:chai|lon|hop|goi|tui|bich|thung|loc)\s+(?:hoac|hay|or)\s+"
        r"(?:chai|lon|hop|goi|tui|bich|thung|loc)\b",
        " ",
        normalized,
    )
    # A recognized budget is a structured filter, never a product-name token.
    budget_match = _budget_match(_normalize_vietnamese(query))
    if budget_match:
        budget_phrase = normalize_category_text(budget_match.group())
        if budget_phrase:
            normalized = re.sub(rf"(?<!\w){re.escape(budget_phrase)}(?!\w)", " ", normalized)
    # ``không đường`` is a positive catalog variant; the snapshot search uses
    # its explicit product-title alias ``zero``.
    normalized = re.sub(r"\bkhong\s+duong\b", "zero", normalized)
    phrases = [
        product_type,
        use_case,
        brand,
        *negative_constraints,
    ]
    alias_phrase = catalog_category_alias_source_phrase(query)
    alias_tokens = normalize_category_text(alias_phrase).split()
    if len(alias_tokens) > 1 and len(set(alias_tokens)) == 1:
        phrases.append(alias_phrase)
    for phrase in phrases:
        key = normalize_category_text(phrase)
        if not key:
            continue
        normalized = re.sub(rf"(?<!\w){re.escape(key)}(?!\w)", " ", normalized)

    ignored = _CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT | _CUSTOMER_GROUP_QUERY_WORDS
    tokens = list(dict.fromkeys(token for token in normalized.split() if len(token) > 1))
    evidence_terms = tuple(token for token in tokens if token not in ignored)
    supported = _catalog_supported_tokens(evidence_terms, category, repository)
    return " ".join(token for token in evidence_terms if token in supported)


def _supported_family_query(
    query: str,
    product_type: str | None,
    use_case: str | None,
    negative_constraints: tuple[str, ...],
    brand: str | None,
    repository: ProductRepository,
) -> tuple[str, bool]:
    """Search an unresolved product family using only title-backed modifiers.

    Some everyday product families (such as shampoo) span several catalog
    leaves. In that case, do not broaden the request to their common ancestor;
    search the family itself and retain only modifiers present in family item
    names. This keeps natural-language context (for example hair/scalp needs)
    from turning into unsupported product filters.
    """
    family = normalize_category_text(product_type)
    if not family:
        return "", False
    try:
        family_products = repository.search(query=product_type or "", limit=500)
    except (OSError, RuntimeError, TimeoutError, ValueError):
        family_products = []
    normalized_titles = [normalize_category_text(item.name) for item in family_products]
    title_tokens = {token for title in normalized_titles for token in title.split()}
    catalog_brands = [
        product.brand
        for product in getattr(repository, "products", ())
        if product.brand
    ]
    normalized = normalize_category_text(remove_excluded_brand_phrases(query, catalog_brands))
    normalized = re.sub(r"\bngan\s+sach\b", " ", normalized)
    budget_match = _budget_match(_normalize_vietnamese(query))
    if budget_match:
        budget_phrase = normalize_category_text(budget_match.group())
        if budget_phrase:
            normalized = re.sub(rf"(?<!\w){re.escape(budget_phrase)}(?!\w)", " ", normalized)
    for phrase in (product_type, use_case, brand, *negative_constraints):
        key = normalize_category_text(phrase)
        if key:
            normalized = re.sub(rf"(?<!\w){re.escape(key)}(?!\w)", " ", normalized)
    # Keep this list conservative: the broad category filler set contains
    # words such as ``nhanh`` and ``bết`` that can be the customer's actual
    # suitability criteria. Treating those as conversational filler would
    # silently turn a targeted request into a generic product browse.
    fillers = frozenset(
        "minh toi em anh chi ban muon can tim cho xem giup hang loai nhom san pham "
        "mot vai nao dung duoc gioi thieu dau toc dao nay tao nhieu qua shop nhe nha "
        "please show looking for product products type category".split()
    )
    runs: list[list[str]] = []
    current_run: list[str] = []
    family_tokens = set(family.split())
    for token in normalized.split():
        if len(token) <= 1 or token in fillers or token in family_tokens:
            if current_run:
                runs.append(current_run)
                current_run = []
            continue
        current_run.append(token)
    if current_run:
        runs.append(current_run)

    supported_details: list[str] = []
    unverified_detail = False
    for run in runs:
        phrase = " ".join(run)
        if any(
            re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", title) for title in normalized_titles
        ) or (len(run) == 1 and run[0] in title_tokens):
            supported_details.extend(run)
        else:
            unverified_detail = True
    return " ".join((family, *dict.fromkeys(supported_details))), unverified_detail


def _product_matches_unresolved_family(
    product: ProductSnapshot,
    product_type: str | None,
) -> bool:
    """Require the full family phrase in a product title or one taxonomy label."""
    family = normalize_category_text(product_type)
    if not family:
        return False
    sources = [product.name, product.category]
    if product.category_hierarchy:
        sources.extend(product.category_hierarchy.split(">"))
    return any(
        re.search(
            rf"(?<!\w){re.escape(family)}(?!\w)",
            normalize_category_text(source),
        )
        for source in sources
        if source
    )


def _constraints_outside_catalog_title(
    constraints: tuple[str, ...],
    matched_title: str | None,
) -> tuple[str, ...]:
    """Keep user filters while ignoring negation tokens that are part of a matched title."""
    if not matched_title:
        return constraints
    normalized_title = normalize_category_text(matched_title)
    remaining: list[str] = []
    for constraint in constraints:
        tokens = normalize_category_text(constraint).split()
        if tokens and f"khong {tokens[0]}" in normalized_title:
            continue
        remaining.append(constraint)
    return tuple(remaining)


def _unsupported_constraint_error(
    name: str,
    repository: ProductRepository,
    constraints: tuple[str, ...],
    product_type: str | None,
) -> ToolObservation:
    return _tool_error(
        name,
        "DATA_UNAVAILABLE",
        "The catalog cannot verify one or more requested product attributes.",
        source=repository.source_name,
        extra={
            "unavailable_fields": ["product_attributes"],
            "unverified_constraints": list(constraints),
            "product_type": product_type,
        },
        response_mode="DETERMINISTIC_ERROR",
        terminal=True,
    )


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
        "skincare": "chăm sóc da",
        "cosmetic": "mỹ phẩm",
    }
    candidate_groups: list[list[ProductSnapshot]] = []
    missing_categories: list[str] = []
    current_stock = bool(getattr(repo, "supports_current_stock", False))
    for category in categories:
        repository_category = _category_for_repository(category, repo)
        candidates = [
            item
            for item in repo.search(
                category=repository_category,
                brand=parsed.brand,
                spicy=parsed.spicy,
                in_stock=current_stock,
                limit=MAX_PRODUCT_RESULTS,
            )
            if not current_stock or item.sellable_stock >= parsed.quantity_per_category
        ]
        candidates.sort(key=lambda item: (item.price_vnd, item.product_id))
        if not candidates:
            missing_categories.append(category)
        candidate_groups.append(candidates)

    options: list[dict[str, Any]] = []
    if not missing_categories:
        valid_combos = []
        for combo in cart_product(*candidate_groups):
            total = sum(item.price_vnd * parsed.quantity_per_category for item in combo)
            within_budget = (
                total < parsed.total_budget_vnd
                if parsed.total_budget_exclusive
                else total <= parsed.total_budget_vnd
            )
            if within_budget:
                valid_combos.append((total, combo))
        valid_combos.sort(key=lambda row: (row[0], tuple(item.product_id for item in row[1])))
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
                        | (
                            {
                                "stock_is_current": False,
                                "data_timestamp": item.data_timestamp.isoformat(),
                            }
                            if not item.stock_is_current
                            else {}
                        )
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
        "total_budget_exclusive": parsed.total_budget_exclusive,
        "quantity_per_category": parsed.quantity_per_category,
        "options": options,
        "missing_categories": missing_categories,
        "count": len(options),
        "data_timestamp": timestamp.isoformat(),
        "cart_mutated": False,
    }
    if not current_stock:
        content["inventory_verified"] = False
    return ToolObservation(
        content=content,
        products=tuple(selected_products.values()),
        tool_name="recommend_products",
        source=repo.source_name,
        response_mode="DETERMINISTIC_RECOMMENDATION",
        terminal=True,
    )


def _category_for_repository(category: str | None, repository: ProductRepository) -> str | None:
    if not category:
        return category
    canonical_category = category.strip().casefold()
    if repository.source_name != "winmart_postgres_catalog":
        return category
    if canonical_category in {"haircare", "oral_care"}:
        # These departments span sibling taxonomy branches in the WinMart
        # snapshot (men's shampoo and children's dental products).
        return canonical_category
    # The WinMart source stores Vietnamese taxonomy labels rather than the
    # agent's stable category IDs. Translate only known IDs; keep free-form
    # customer/model categories intact.
    return {
        "drink": "nước uống",
        "snack": "bánh snack",
        "dairy": "sữa",
        "meal": "thực phẩm ăn liền",
        "skincare": "chăm sóc da",
        "haircare": "chăm sóc tóc",
        "oral_care": "chăm sóc răng miệng",
        "home_cleaning": "vệ sinh nhà",
        "laundry": "chất giặt tẩy",
        "baby": "đồ dùng chăm sóc mẹ và bé",
        "cosmetic": "mỹ phẩm, chăm sóc cá nhân",
    }.get(canonical_category, category)


def _category_has_children(category: str | None, repository: ProductRepository) -> bool:
    if not category:
        return False
    if normalize_category_text(category) == "baby":
        return True
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return False
    try:
        hierarchies = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return False
    canonical = category.strip().casefold()
    taxonomy_parent = _CATEGORY_TAXONOMY_PARENTS.get(canonical, category)
    return category_is_parent(taxonomy_parent, hierarchies)


_CATEGORY_QUERY_FILLER = frozenset(
    "a an ban biet can cho co con cua dang de gi giup hang hoi la loai mat minh muon mua nao "
    "nha nhe noi thoi tui di gia bao nhieu tien nhiu bn "
    "um uhm neu thi chon nen goi so vai phim nhanh y gioi thieu de xuat "
    "may trong tam nay "
    "uong hoc di tre em be "
    "nhom san tim toi tu van voi xem the nao phu hop khong duoc do mot san pham "
    "em anh chi ay minh vay va nhu co the cac nhung hay bat ky thu nao type products product "
    "some any please find looking for recommend recommend me suggest show me want need "
    "good best better "
    "what which do can could have is are the for in your category item items kind type "
    "i me to of and a an this that some any".split()
)
_CATEGORY_NEED_CONTEXT = frozenset(
    "da mat tay kho dau nhay cam mun toc rung gau bang bep chen bat quan ao giat "
    "rua lau sach mem phu hop nhu cau duong am lam dep don dep ve sinh cham soc "
    "dung cho cho be em gia dinh ca nhan nong lanh ngon cay man ngot re budget bet "
    "thich dung su dung mau da tone mua sam tu van dry sensitive oily skin healthy".split()
)
_CATEGORY_TAXONOMY_PARENTS = {
    "snack": "snack",
    "drink": "do uong thuoc la",
    "dairy": "bo sua trung",
    "meal": "thuc pham an lien",
    "skincare": "cham soc da",
    "haircare": "cham soc toc",
    "oral_care": "cham soc rang mieng",
    "home_cleaning": "ve sinh nha",
    "laundry": "chat giat tay",
    "cosmetic": "my pham cham soc ca nhan",
}
_CUSTOMER_GROUP_QUERY_WORDS = frozenset(
    {"tre", "em", "be", "nam", "nu", "kids", "child", "children", "men", "women"}
)
_BROAD_RECOMMENDATION_QUERY_CUES = frozenset(
    {
        "bim bim",
        "bimbim",
        "banh keo",
        "do an vat",
        "snack",
        "do uong",
        "nuoc uong",
        "drink",
        "drinks",
        "beverage",
        "beverages",
        "food",
        "meal",
        "meals",
        "cham soc da",
        "skincare",
        "skin care",
        "cham soc toc",
        "hair care",
        "haircare",
        "cham soc rang mieng",
        "oral care",
        "ve sinh nha",
        "ve sinh nha cua",
        "household cleaning",
        "chat giat tay",
        "laundry",
        "do cho me va be",
        "baby care",
        "personal care",
        "my pham",
    }
)


def _query_matches_catalog_title(
    query: str,
    repository: ProductRepository,
    category: str | None = None,
) -> bool:
    terms = set(normalize_category_text(query).split())
    if not terms:
        return False
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    if products is None:
        return False
    if category:
        products = repository.search(query=query, category=category, limit=20)
    return any(
        terms
        <= set(normalize_category_text(product.name).split())
        | set(normalize_category_text(product.brand).split())
        for product in products
    )


@lru_cache(maxsize=8)
def _catalog_product_title_rows(
    repository: ProductRepository,
) -> tuple[tuple[str, str, frozenset[str]], ...]:
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    return tuple(
        (
            product.name.strip(),
            normalize_category_text(product.name),
            frozenset(
                normalize_category_text(product.name).split()
                + normalize_category_text(product.brand).split()
                + normalize_category_text(PRODUCT_ALIASES.get(product.product_id, "")).split()
            ),
        )
        for product in products or ()
    )


@lru_cache(maxsize=8)
def _catalog_product_title_vocabulary(repository: ProductRepository) -> frozenset[str]:
    return frozenset(
        token
        for _title, _title_key, title_tokens in _catalog_product_title_rows(repository)
        for token in title_tokens
        if len(token) >= 4
    )


def _contains_catalog_identifier(query: str, product_id: str) -> bool:
    normalized_query = query.casefold()
    normalized_id = product_id.casefold()
    start = normalized_query.find(normalized_id)
    while start >= 0:
        end = start + len(normalized_id)
        before_is_word = start > 0 and (
            normalized_query[start - 1].isalnum() or normalized_query[start - 1] == "_"
        )
        after_is_word = end < len(normalized_query) and (
            normalized_query[end].isalnum() or normalized_query[end] == "_"
        )
        if not before_is_word and not after_is_word:
            return True
        start = normalized_query.find(normalized_id, start + 1)
    return False


def _catalog_tokens_are_typo_close(query_token: str, catalog_token: str) -> bool:
    matcher = SequenceMatcher(None, query_token, catalog_token)
    return matcher.quick_ratio() >= 0.72 and matcher.ratio() >= 0.72


def _catalog_product_title_match(
    query: str,
    repository: ProductRepository,
) -> str | None:
    """Avoid turning title words into hard category filters.

    The taxonomy can contain common words that also appear in product names
    (for example ``trà`` or ``thực phẩm``). Treat a sufficiently specific
    catalog-backed title/brand phrase as product identity, unless the customer
    explicitly asks to browse a group.
    """
    identity_query = _catalog_product_identity_phrase(query)
    normalized = normalize_category_text(query)
    category_alias_phrase = normalize_category_text(catalog_category_alias_source_phrase(query))
    category_alias_tokens = set(category_alias_phrase.split())
    explicit_group_browse = bool(
        re.search(r"\b(?:nhom|danh muc|category|department)\b", normalized)
    )
    title_rows = _catalog_product_title_rows(repository)
    if not title_rows:
        return None
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    for product in products or ():
        if _contains_catalog_identifier(query, product.product_id):
            return product.name.strip()
    # First look for one complete catalog title embedded in a natural sentence.
    # This handles utterances such as "nhà mình sắp hết [title], tìm giúp mình"
    # without letting the surrounding words dilute an otherwise exact identity.
    contained_titles: dict[str, str] = {}
    for title, title_key, _title_tokens in title_rows:
        title_tokens = set(title_key.split())
        if category_alias_tokens and title_tokens <= category_alias_tokens:
            # A generic title such as ``Khoai tây`` can be embedded inside
            # ``snack khoai tây``. It describes an ingredient/category cue,
            # not the specific product the customer is asking to buy.
            continue
        if len(title_key.split()) >= 2 and f" {title_key} " in f" {normalized} ":
            contained_titles[title_key] = title
    if contained_titles:
        longest_length = max(len(value.split()) for value in contained_titles)
        longest_titles = {
            key: title
            for key, title in contained_titles.items()
            if len(key.split()) == longest_length
        }
        if len(longest_titles) == 1:
            return next(iter(longest_titles.values()))
        return None
    if explicit_group_browse:
        return None
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if callable(list_hierarchies):
        try:
            hierarchies = list_hierarchies()
        except (OSError, RuntimeError, TimeoutError, ValueError):
            hierarchies = ()
        taxonomy_match = resolve_catalog_category(identity_query, hierarchies)
        if taxonomy_match is not None:
            taxonomy_phrase = normalize_category_text(taxonomy_match.category)
            normalized_identity_query = normalize_category_text(identity_query)
            residual_tokens = set(normalized_identity_query.split()) - set(
                taxonomy_phrase.split()
            ) - _CATEGORY_QUERY_FILLER - _CATEGORY_NEED_CONTEXT - _PRODUCT_IDENTITY_FILLER
            if (
                taxonomy_phrase
                and f" {taxonomy_phrase} " in f" {normalized_identity_query} "
                and not residual_tokens
            ):
                # A complete taxonomy label with no product-specific details
                # is a category browse, even when a fuzzy SKU title shares it.
                # Exact catalog titles have already been returned above.
                return None
    normalized_identity = normalize_category_text(identity_query)
    if category_alias_phrase:
        normalized_identity = re.sub(
            rf"(?<!\w){re.escape(category_alias_phrase)}(?!\w)",
            " ",
            normalized_identity,
        )
    query_tokens = {
        token
        for token in normalized_identity.split()
        if token not in _PRODUCT_IDENTITY_FILLER and len(token) > 1
    }
    if len(query_tokens) < 2:
        return None
    vocabulary = _catalog_product_title_vocabulary(repository)
    fuzzy_tokens = {
        token: frozenset(
            candidate
            for candidate in vocabulary
            if candidate != token
            and _catalog_tokens_are_typo_close(token, candidate)
        )
        for token in query_tokens
        if len(token) >= 4 and token not in vocabulary
    }
    matches: list[tuple[int, int, str]] = []
    for title, _normalized_title, title_tokens in title_rows:
        exact_tokens = query_tokens & title_tokens
        fuzzy_matches = {
            token
            for token in query_tokens - exact_tokens
            if title_tokens & fuzzy_tokens.get(token, frozenset())
        }
        if exact_tokens | fuzzy_matches == query_tokens:
            matches.append((len(exact_tokens), len(fuzzy_matches), title))
    if not matches:
        return None
    best_score = max((exact_count, -fuzzy_count) for exact_count, fuzzy_count, _ in matches)
    best_titles = {
        title
        for exact_count, fuzzy_count, title in matches
        if (exact_count, -fuzzy_count) == best_score
    }
    if len(best_titles) != 1:
        return None
    return next(iter(best_titles))


_CATALOG_PRODUCT_DETAIL_TAIL = re.compile(
    r"\b(?:gia\s+(?:bao\s+nhieu|bao\s+tien|nhiu|may\s+dong|bn)|"
    r"bao\s+nhieu(?:\s+tien)?|bao\s+tien|nhiu(?:\s+(?:vay|zay|ta))?|"
    r"bn(?:\s+(?:vay|ta))?|how\s+much|(?:what\s+is\s+)?the\s+price|price|cost|"
    r"ben\s+trong|gom\s+nhung\s+gi|thanh\s+phan|nguyen\s+lieu|ingredients|"
    r"loi\s+ich|cong\s+dung|benefits?|health\s+benefits?|suc\s+khoe|"
    r"o\s+dau|ke\s+(?:nao|may)|day\s+nao|khu\s+nao|quay\s+nao|vi\s+tri|"
    r"where\s+(?:is|can\s+i\s+find)|which\s+(?:aisle|shelf)|aisle|shelf|"
    r"con\s+bao\s+nhieu|ton\s+kho|con\s+hang|stock|het\s+han|han\s+su\s+dung|"
    r"expiry(?:\s+date)?|expiration(?:\s+date)?|use\s+by)\b.*$",
    re.IGNORECASE,
)


def _catalog_product_identity_phrase(query: str) -> str:
    """Remove a trailing fact request so product identity can be matched independently."""
    normalized = _normalize_vietnamese(query)
    return _CATALOG_PRODUCT_DETAIL_TAIL.sub("", normalized, count=1).strip(" ,.!?;:")


def _exact_product_title(query: str, repository: ProductRepository) -> bool:
    normalized = normalize_category_text(query)
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    return bool(normalized and products) and any(
        normalized == normalize_category_text(product.name) for product in products
    )


@lru_cache(maxsize=8)
def _catalog_brand_index(repository: ProductRepository) -> tuple[tuple[str, str], ...]:
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    if products is None:
        return ()
    brands = {
        normalize_category_text(product.brand): product.brand.strip()
        for product in products
        if product.brand and product.brand.strip()
    }
    return tuple(brands.items())


def _catalog_brand_tokens_in_query(query: str, repository: ProductRepository) -> set[str]:
    normalized = normalize_category_text(query)
    brands = dict(_catalog_brand_index(repository))
    if not brands:
        return set()
    excluded_brands = excluded_catalog_brands(query, list(brands))
    # A token from a multiword brand is not a brand mention. In particular,
    # Vietnamese pronouns must not match MINH HAO, TAO KAE NOI, or M&M.
    return {
        token
        for brand in brands
        if brand and brand not in excluded_brands and f" {brand} " in f" {normalized} "
        for token in brand.split()
    }


def _mentioned_candidate_brand(
    query: str,
    candidates: tuple[ProductSnapshot, ...] | list[ProductSnapshot],
    excluded_brands: frozenset[str] = frozenset(),
) -> str | None:
    """Resolve a clearly mentioned brand only from brands on retrieved candidates."""
    normalized_query = f" {normalize_category_text(query)} "
    matches: dict[str, str] = {}
    for product in candidates:
        brand = (product.brand or "").strip()
        normalized_brand = normalize_category_text(brand)
        if (
            normalized_brand
            and normalized_brand not in excluded_brands
            and f" {normalized_brand} " in normalized_query
        ):
            matches[normalized_brand] = brand
    return next(iter(matches.values())) if len(matches) == 1 else None


def _catalog_brand_from_query(query: str, repository: ProductRepository) -> str | None:
    """Find one exact catalog brand phrase without treating it as a full SKU title."""
    normalized_query = f" {normalize_category_text(query)} "
    brands = dict(_catalog_brand_index(repository))
    if not brands:
        return None
    matches = {
        normalized: brand
        for normalized, brand in brands.items()
        if normalized
        and f" {normalized} " in normalized_query
        and not (
            all(
                token in _CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT
                for token in normalized.split()
            )
            and not re.search(rf"(?<!\w){re.escape(brand.casefold())}(?!\w)", query.casefold())
        )
    }
    if len(matches) != 1:
        return None
    brand_key, brand = next(iter(matches.items()))
    ambiguous_brand = all(
        token in _CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT for token in brand_key.split()
    )
    if ambiguous_brand:
        query_category, _category_only = _query_category_hint(query, repository)
        if query_category and _is_category_only_request(query, query_category, repository):
            return None
    return None if brand_key in excluded_catalog_brands(query, list(brands)) else brand


def _query_is_only_catalog_brand(query: str, brand: str) -> bool:
    normalized_query = normalize_category_text(query)
    normalized_brand = normalize_category_text(brand)
    if not normalized_query or not normalized_brand:
        return False
    remaining = re.sub(
        rf"(?<!\w){re.escape(normalized_brand)}(?!\w)",
        " ",
        normalized_query,
    )
    tokens = set(remaining.split()) - _CATEGORY_QUERY_FILLER - _CATEGORY_NEED_CONTEXT
    return not tokens


def _remove_category_phrases(query: str, category: str | None) -> set[str]:
    normalized = normalize_category_text(query)
    remaining = normalized.split()
    cues = _category_cues(category or "")
    for cue in sorted(cues, key=len, reverse=True):
        cue_tokens = normalize_category_text(cue).split()
        if not cue_tokens:
            continue
        size = len(cue_tokens)
        index = 0
        while index <= len(remaining) - size:
            if remaining[index : index + size] == cue_tokens:
                del remaining[index : index + size]
            else:
                index += 1
    return set(remaining)


def _query_category_hint(
    query: str,
    repository: ProductRepository | None = None,
) -> tuple[str | None, bool]:
    """Resolve static intents or a phrase from the active catalog taxonomy."""
    product_modifier = _product_type_attribute_modifier(query)
    category = product_modifier[0] if product_modifier else _explicit_category(query)
    taxonomy_query = _normalize_catalog_query(product_modifier[1] if product_modifier else query)
    match: CatalogCategoryMatch | None = None
    hierarchies: tuple[tuple[str, ...], ...] = ()
    if repository is not None:
        list_hierarchies = getattr(repository, "list_category_hierarchies", None)
        if callable(list_hierarchies):
            try:
                hierarchies = list_hierarchies()
                exact_leaf = catalog_leaf_query_phrase(query, hierarchies)
                direct_match = resolve_catalog_category(taxonomy_query, hierarchies)
                if direct_match is None and exact_leaf:
                    direct_match = resolve_catalog_category(exact_leaf, hierarchies)
                alias_match = resolve_catalog_category_alias(query, hierarchies) or (
                    resolve_catalog_category_alias(taxonomy_query, hierarchies)
                )
                exact_leaf_is_named = bool(
                    direct_match is not None
                    and exact_leaf
                    and normalize_category_text(exact_leaf)
                    == normalize_category_text(direct_match.category)
                    and not category_is_parent(direct_match.category, hierarchies)
                )
                alias_refines_audience = bool(
                    direct_match is not None
                    and alias_match is not None
                    and category_alias_refines_explicit_audience(
                        query,
                        direct_match.category,
                        alias_match.category,
                    )
                )
                match = (
                    direct_match
                    if direct_match is not None
                    and (
                        alias_match is None or (exact_leaf_is_named and not alias_refines_audience)
                    )
                    else alias_match or direct_match
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                match = None
                hierarchies = ()
    related_match = False
    if category and match is not None:
        taxonomy_parent = _CATEGORY_TAXONOMY_PARENTS.get(category)
        related_match = bool(
            match.exact_phrase
            or catalog_leaf_query_phrase(taxonomy_query, hierarchies)
            or (
                taxonomy_parent
                and any(
                    normalize_category_text(taxonomy_parent) in normalize_category_text(segment)
                    for path in hierarchies
                    if match.category in path
                    for segment in path
                )
            )
        )
        if related_match:
            match_is_explicit_leaf = not category_is_parent(match.category, hierarchies) and (
                match.exact_phrase
                or bool(
                    re.search(
                        rf"(?<!\w){re.escape(normalize_category_text(match.category))}(?!\w)",
                        normalize_category_text(taxonomy_query),
                    )
                )
                or bool(catalog_leaf_query_phrase(taxonomy_query, hierarchies))
            )
            if match_is_explicit_leaf:
                category = match.category
        else:
            match = None
    if category is None and match is not None:
        category = match.category
    if category is None:
        return None, False

    normalized = normalize_category_text(_normalize_catalog_query(query))
    residual = set(normalized.split())
    if match is not None:
        residual.difference_update(match.matched_tokens)
    else:
        residual = _remove_category_phrases(taxonomy_query, category)
    # For static categories, also remove matched category words that occur in
    # a longer cue (for example the cue “da kho” after resolving skincare).
    cue_tokens = set()
    for cue in _category_cues(category):
        cue_tokens.update(normalize_category_text(cue).split())
    residual.difference_update(cue_tokens)
    residual.difference_update(_CATEGORY_QUERY_FILLER)
    residual.difference_update(_CATEGORY_NEED_CONTEXT)
    taxonomy_parent = _CATEGORY_TAXONOMY_PARENTS.get(category)
    parent_key = normalize_category_text(taxonomy_parent or category)
    for path in hierarchies:
        normalized_path = [normalize_category_text(segment) for segment in path]
        if parent_key not in normalized_path:
            continue
        for segment in path[: normalized_path.index(parent_key) + 1]:
            residual.difference_update(normalize_category_text(segment).split())
    if repository is not None and _query_matches_catalog_title(taxonomy_query, repository):
        return category, False
    return category, not residual


def _category_from_product_type(
    product_type: str | None,
    repository: ProductRepository,
) -> str | None:
    """Resolve the parsed product type against labels in the active catalog.

    The intent parser and the older category-cue router can see different
    phrases in one utterance. Prefer the parsed type when it resolves to an
    actual catalog segment, so a nearby cue such as "nước ngọt" cannot route
    a request for "cá nước ngọt" into beverages.
    """
    if not product_type:
        return None
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return None
    try:
        hierarchies = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return None
    if not hierarchies:
        return None

    labels: dict[str, tuple[str, tuple[str, ...]]] = {}
    for path in hierarchies:
        for index, label in enumerate(path):
            key = normalize_category_text(label)
            if key:
                labels.setdefault(key, (label, path[: index + 1]))

    direct = resolve_catalog_category(product_type, hierarchies)
    direct_key = normalize_category_text(direct.category) if direct else ""
    if direct_key in labels:
        return labels[direct_key][0]

    # A type can be a catalog-backed prefix whose closest exact segment is
    # longer (for example a parent label followed by a preparation subtype).
    # Resolve that relationship from taxonomy paths rather than a SKU map.
    query_tokens = normalize_category_text(product_type).split()
    candidates: list[tuple[int, str, tuple[str, ...]]] = []
    if len(query_tokens) >= 2:
        for key, (label, path) in labels.items():
            label_tokens = key.split()
            if len(label_tokens) < len(query_tokens):
                continue
            if any(
                label_tokens[start : start + len(query_tokens)] == query_tokens
                for start in range(len(label_tokens) - len(query_tokens) + 1)
            ):
                candidates.append((len(label_tokens) - len(query_tokens), label, path))
    if candidates:
        minimum_extra = min(item[0] for item in candidates)
        closest = [item for item in candidates if item[0] == minimum_extra]
        distinct = {normalize_category_text(item[1]): item for item in closest}
        if len(distinct) == 1:
            return next(iter(distinct.values()))[1]
        # Multiple sibling leaves (for example men's and women's shampoo)
        # do not justify searching their broad common department. The caller
        # can search the family phrase across the catalog instead.

    alias = resolve_catalog_category_alias(product_type, hierarchies)
    if alias and normalize_category_text(alias.category) in labels:
        return labels[normalize_category_text(alias.category)][0]
    return None


def _explicit_query_leaf_category(
    query: str,
    repository: ProductRepository,
) -> str | None:
    """Resolve a complete catalog leaf named in the utterance, when present."""
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return None
    try:
        hierarchies = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return None
    leaf_phrase = catalog_leaf_query_phrase(query, hierarchies)
    if not leaf_phrase:
        return None
    match = resolve_catalog_category(leaf_phrase, hierarchies)
    if match is None or category_is_parent(match.category, hierarchies):
        return None
    normalized_query = normalize_category_text(query)
    normalized_label = normalize_category_text(match.category)
    # Do not turn a near taxonomy alias into an explicit override. For
    # example, a query for "Lăn khử mùi Nam" must not be redirected to the
    # catalog's similarly named women's branch. Override parser output only
    # when the utterance actually names the complete leaf label.
    if len(normalized_label.split()) < 2 or not re.search(
        rf"(?<!\w){re.escape(normalized_label)}(?!\w)", normalized_query
    ):
        return None
    return match.category


def _is_category_only_request(
    query: str,
    category: str | None,
    repository: ProductRepository,
) -> bool:
    if not category:
        return False
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return False
    try:
        hierarchies = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return False
    category_key = normalize_category_text(category)
    if not any(
        normalize_category_text(segment) == category_key for path in hierarchies for segment in path
    ):
        return False
    query_category, category_only = _query_category_hint(query, repository)
    return bool(
        category_only
        and query_category
        and normalize_category_text(query_category) == category_key
        and _query_names_a_specific_category(query, category, repository)
    )


def _category_query_unmatched_details(
    query: str,
    category: str | None,
    repository: ProductRepository,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split category-query details by whether the snapshot contains evidence."""
    if not category:
        return (), ()
    resolved_category, _category_only = _query_category_hint(query, repository)
    if not resolved_category or normalize_category_text(
        resolved_category
    ) != normalize_category_text(category):
        return (), ()
    normalized_query = normalize_category_text(query)
    correction = re.search(r"\bkhong phai\b", normalized_query)
    if correction and not re.search(
        r"\b(?:ma|nhung|hay|chu|instead|rather than|but)\b",
        normalized_query[correction.end() :],
    ):
        # In speech, "không phải ..." often repairs the preceding phrase. The
        # correction tail is not reliable product-title evidence; the response
        # will still mark any fallback details as unverified.
        normalized_query = normalized_query[: correction.start()]
    remaining = _remove_category_phrases(normalized_query, category)
    remaining.difference_update(normalize_category_text(category).split())
    remaining.difference_update(_CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT)
    remaining = {token for token in remaining if len(token) > 1}
    supported_evidence = _catalog_supported_tokens(
        tuple(sorted(remaining)), category, repository
    )
    supported = tuple(sorted(supported_evidence & remaining))
    unsupported = tuple(sorted(remaining - set(supported)))
    return supported, unsupported


def _query_names_a_specific_category(
    query: str,
    category: str | None,
    repository: ProductRepository,
) -> bool:
    if not category:
        return False
    if _explicit_category(query) is not None:
        return True
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return False
    try:
        hierarchies = list_hierarchies()
        normalized_query = _normalize_catalog_query(query)
        match = resolve_catalog_category(normalized_query, hierarchies)
        if match is None:
            exact_leaf = catalog_leaf_query_phrase(query, hierarchies)
            if exact_leaf:
                match = resolve_catalog_category(exact_leaf, hierarchies)
        alias_match = resolve_catalog_category_alias(normalized_query, hierarchies)
        exact_leaf = catalog_leaf_query_phrase(query, hierarchies)
        exact_leaf_is_named = bool(
            match is not None
            and exact_leaf
            and normalize_category_text(exact_leaf) == normalize_category_text(match.category)
            and not category_is_parent(match.category, hierarchies)
        )
        alias_refines_audience = bool(
            match is not None
            and alias_match is not None
            and category_alias_refines_explicit_audience(
                query,
                match.category,
                alias_match.category,
            )
        )
        if (
            match is not None
            and alias_match
            and (not exact_leaf_is_named or alias_refines_audience)
        ):
            match = alias_match
        elif match is None:
            match = alias_match
        if match is not None:
            content_tokens = set(normalize_category_text(_normalize_catalog_query(query)).split())
            content_tokens.difference_update(_CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT)
            return bool(content_tokens) and content_tokens <= match.matched_tokens
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return False
    if match is None:
        return False
    content_tokens = set(normalize_category_text(_normalize_catalog_query(query)).split())
    content_tokens.difference_update(_CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT)
    return bool(content_tokens) and content_tokens <= match.matched_tokens


def _effective_catalog_search_query(
    query: str,
    category_only: bool,
    repository: ProductRepository | None = None,
) -> str:
    if category_only:
        return ""
    identity_surface = _restore_query_surface(_catalog_product_identity_phrase(query), query)
    normalized_query = _normalize_catalog_query(_catalog_product_identity_phrase(query))
    normalized_query = re.sub(
        r"\b(?:minh|toi|em|anh|chi|ban)?\s*co\s+(?:(?:khoang|tam)\s+)?"
        r"\d[\d.,]*(?:\s*(?:trieu|tr|k|ngan|nghin|dong|vnd|d))?\b",
        " ",
        normalized_query,
    )
    normalized_query = re.sub(
        r"\b(?:duoi|under|less than|toi da|khong qua|dung qua|khoang|trong muc|"
        r"trong tam|ngan sach(?: la| tam)?|up to|at most)\s+"
        r"(?:\d[\d.,]*|khong|mot|hai|ba|bon|tu|nam|lam|sau|bay|tam|chin|muoi)"
        r"(?:\s*(?:trieu|tr|k|ngan|nghin|dong|vnd|d))?\b",
        " ",
        normalized_query,
    )
    explicit_query = _normalize_catalog_query(_explicit_query(query) or "")
    hierarchies: tuple[tuple[str, ...], ...] = ()
    if repository is not None:
        list_hierarchies = getattr(repository, "list_category_hierarchies", None)
        if callable(list_hierarchies):
            try:
                hierarchies = list_hierarchies()
            except (OSError, RuntimeError, TimeoutError, ValueError):
                hierarchies = ()
    protect_product_type = bool(
        explicit_query and query_names_catalog_leaf(explicit_query, hierarchies)
    )
    protected_tokens = (
        set(normalize_category_text(explicit_query).split()) if protect_product_type else set()
    )
    if re.search(r"\b(?:tre em|em be|cho be|baby|kids|children)\b", normalized_query):
        protected_tokens.update(_CUSTOMER_GROUP_QUERY_WORDS)
    tokens = normalize_category_text(normalized_query).split()
    retained = [
        token
        for token in tokens
        if token in protected_tokens or token not in _CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT
    ]
    if not retained:
        return ""
    retained_tokens = set(retained)
    surface_tokens = re.findall(r"[^\W_]+", identity_surface, flags=re.UNICODE)
    surface_normalized = [
        normalize_category_text(token).split() for token in surface_tokens
    ]
    normalized_words = normalize_category_text(normalized_query).split()
    if (
        all(len(tokens_for_word) == 1 for tokens_for_word in surface_normalized)
        and [tokens_for_word[0] for tokens_for_word in surface_normalized] == normalized_words
    ):
        return " ".join(
            raw
            for raw, tokens_for_word in zip(surface_tokens, surface_normalized, strict=True)
            if tokens_for_word[0] in retained_tokens
        )[:160]
    raw_tokens = re.findall(r"[^\W_]+", normalized_query, flags=re.UNICODE)
    normalized_tokens = [normalize_category_text(token).split() for token in raw_tokens]
    if all(len(tokens_for_word) == 1 for tokens_for_word in normalized_tokens):
        return " ".join(
            raw
            for raw, tokens_for_word in zip(raw_tokens, normalized_tokens, strict=True)
            if tokens_for_word[0] in retained_tokens
        )[:160]
    return " ".join(dict.fromkeys(retained))[:160]


def _restore_query_surface(query: str, source: str) -> str:
    """Restore the user's spelling, accents, and capitalization for a parsed phrase."""
    query_tokens = normalize_category_text(query).split()
    source_matches = list(re.finditer(r"[^\W_]+", source, flags=re.UNICODE))
    source_tokens = [normalize_category_text(match.group()) for match in source_matches]
    if not query_tokens or len(query_tokens) > len(source_tokens):
        return query
    for start in range(len(source_tokens) - len(query_tokens) + 1):
        if source_tokens[start : start + len(query_tokens)] != query_tokens:
            continue
        return source[
            source_matches[start].start() : source_matches[start + len(query_tokens) - 1].end()
        ]
    return query


def resolve_catalog_request_category(
    query: str,
    repository: ProductRepository | None = None,
) -> str | None:
    """Expose query-to-catalog-category resolution for the request router."""
    category, _category_only = _query_category_hint(
        query,
        repository or get_product_repository(),
    )
    return category


_ENGLISH_CATALOG_TERMS = (
    ("household cleaning products", "ve sinh nha cua"),
    ("women s", "nu"),
    ("men s", "nam"),
    ("children", "tre em"),
    ("kids", "tre em"),
    ("child", "tre"),
    ("women", "nu"),
    ("men", "nam"),
    ("household cleaning", "ve sinh nha"),
    ("cleaning products", "ve sinh nha"),
    ("facial cleanser", "sua rua mat"),
    ("face wash", "sua rua mat"),
    ("sensitive skin", "da nhay cam"),
    ("dry skin", "da kho"),
    ("oily skin", "da dau"),
    ("skin care", "cham soc da"),
    ("skincare", "cham soc da"),
    ("baby care", "do dung cham soc me va be"),
    ("baby products", "do dung cham soc me va be"),
    ("laundry detergent", "chat giat tay"),
    ("dish soap", "nuoc rua chen"),
    ("floor cleaner", "nuoc lau san"),
    ("toothbrush", "ban chai danh rang"),
    ("mouthwash", "nuoc suc mieng"),
    ("toothpaste", "kem danh rang"),
    ("conditioner", "dau xa"),
    ("shampoo", "dau goi"),
    ("sunscreen", "kem chong nang"),
    ("moisturizer", "kem duong da mat"),
    ("laundry", "chat giat tay"),
    ("household cleaner", "ve sinh nha"),
    ("cleaner", "ve sinh nha"),
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
        "a",
        "an",
        "and",
        "are",
        "at",
        "can",
        "could",
        "do",
        "does",
        "for",
        "have",
        "has",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "please",
        "the",
        "this",
        "that",
        "to",
        "we",
        "what",
        "where",
        "which",
        "who",
        "with",
        "you",
        "your",
        "find",
        "get",
        "looking",
        "recommend",
        "suggest",
        "under",
        "below",
        "up",
        "less",
        "than",
        "no",
        "more",
        "most",
        "price",
        "cost",
        "shelf",
        "aisle",
        "stock",
        "available",
        "deal",
        "deals",
        "promotion",
        "promotions",
        "offer",
        "offers",
        "discount",
        "sale",
        "expiry",
        "expiration",
        "ingredients",
        "contains",
        "inside",
        "mild",
        "spicy",
        "not",
        "non",
        "budget",
        "left",
        "in",
        "many",
        "much",
        "today",
        "current",
        "good",
        "best",
        "better",
        "actually",
        "vnd",
    }
)


def _normalize_catalog_query(query: str) -> str:
    """Translate common English catalog terms and remove question filler before lookup."""
    if re.search(r"[à-ỹđ]", query.casefold()):
        # Vietnamese "cần/cân" is not English "can", and a brand's "Men"
        # must not be translated inside a Vietnamese product request.
        return query.strip()
    normalized = " ".join(_normalize_vietnamese(query).split())
    english_signals = (
        "where",
        "what",
        "which",
        "who",
        "can",
        "could",
        "does",
        "have",
        "find",
        "please",
        "recommend",
        "suggest",
        "looking for",
        "under",
        "below",
        "less than",
        "up to",
        "at most",
        "price",
        "cost",
        "in stock",
        "available",
        "promotion",
        "deal",
        "offer",
        "discount",
        "expiry",
        "expiration",
        "ingredients",
        "shelf",
        "aisle",
        "milk",
        "coffee",
        "water",
        "beverage",
        "drink",
        "dairy",
        "yogurt",
        "yoghurt",
        "cheese",
        "crisps",
        "chips",
        "noodles",
        "household",
        "cleaning",
        "facial cleanser",
        "face wash",
        "skin care",
        "skincare",
        "dry skin",
        "sensitive skin",
        "oily skin",
        "sunscreen",
        "moisturizer",
        "shampoo",
        "conditioner",
        "hair care",
        "haircare",
        "toothpaste",
        "toothbrush",
        "mouthwash",
        "oral care",
        "laundry",
        "baby",
        "baby care",
        "kids",
        "children",
        "women",
        "men",
        "dish soap",
        "floor cleaner",
        "cleaner",
        "breakfast",
        "lunch",
        "dinner",
        "food",
        "meal",
        "soda",
        "tea",
    )
    has_english_signal = any(
        re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", normalized) for signal in english_signals
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
        normalized = re.sub(rf"(?<!\w){re.escape(english)}(?!\w)", vietnamese, normalized)
    return " ".join(
        token for token in normalized.split() if token not in _ENGLISH_CATALOG_STOP_WORDS
    )


def recommendation_catalog_query(
    text: str,
    repository: ProductRepository | None = None,
) -> str:
    """Build a short catalog query from a natural product-discovery request.

    Product types are protected when they name or prefix a leaf in the active
    catalog. Need and suitability language stays out of the hard product-title
    filter, while unrecognized modifiers such as a brand or demographic remain.
    """
    repo = repository or get_product_repository()
    hierarchies: tuple[tuple[str, ...], ...] = ()
    list_hierarchies = getattr(repo, "list_category_hierarchies", None)
    if callable(list_hierarchies):
        try:
            hierarchies = list_hierarchies()
        except (OSError, RuntimeError, TimeoutError, ValueError):
            hierarchies = ()

    normalized = normalize_category_text(_normalize_catalog_query(text))
    normalized = re.sub(
        r"\b(?:duoi|toi da|khong qua|under|below|less than|no more than|at most|up to)"
        r"\s+(?:\d[\d\s]*(?:k|nghin|ngan|chuc|trieu|million|thousand|vnd|dong|d)?"
        r"|(?:hai chuc|muoi lam|hai muoi|ba muoi|bon muoi|nam muoi))\b",
        " ",
        normalized,
    )
    for phrase in (
        "co loai nao tuong tu",
        "loai nao tuong tu",
        "san pham nao tuong tu",
        "mon nao tuong tu",
        "co loai nao khac",
        "loai nao khac",
        "san pham nao khac",
        "mon nao khac",
        "loai khac",
        "san pham khac",
        "tuong tu",
        "cho minh xin y kien",
        "cho toi xin y kien",
        "xin y kien",
        "chon ho minh",
        "tui phan van",
        "toi phan van",
        "minh phan van",
        "phan van",
        "chon dum voi",
        "chon dum",
        "chọn dum",
        "doi thanh",
        "lai rai khi coi phim",
        "lai rai luc xem phim",
        "khi coi phim",
        "luc xem phim",
        "lai rai",
    ):
        normalized = re.sub(
            rf"(?<!\w){re.escape(normalize_category_text(phrase))}(?!\w)",
            " ",
            normalized,
        )
    normalized = re.sub(
        r"^(?:tui|toi|minh)\s+(?=(?:muon|can|hoi|tim|xem|mua|phan van|chon|thich)\b)",
        "",
        normalized,
    )
    # Spoken requests often include a planning phrase after the actual need.
    # Remove these complete discourse chunks while keeping product modifiers
    # such as brands, sizes, and explicitly named catalog types intact.
    for phrase in (
        "chua biet nen bat dau tu dau",
        "chua biet bat dau tu dau",
        "chua biet nen chon loai nao",
        "chua biet chon loai nao",
        "chua biet nen mua loai nao",
        "chua biet mua loai nao",
        "khong biet nen mua loai nao",
        "khong biet mua loai nao",
        "khong biet chon loai nao",
        "chua biet nen mua gi",
        "chua biet mua gi",
        "chua biet chon gi",
        "khong biet chon gi",
        "neu toi hoi",
    ):
        normalized = re.sub(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            " ",
            normalized,
        )
    modifier = _product_type_attribute_modifier(text)
    type_query = _normalize_catalog_query(modifier[1] if modifier else text)
    catalog_type_query = catalog_leaf_query_phrase(type_query, hierarchies)
    explicit = catalog_type_query or _normalize_catalog_query(_explicit_query(text) or "")
    normalized_explicit = normalize_category_text(explicit)
    explicit_is_specific_type = bool(
        explicit
        and (catalog_type_query or _explicit_query(text))
        and normalized_explicit not in _BROAD_RECOMMENDATION_QUERY_CUES
    )
    explicit_match = resolve_catalog_category(explicit, hierarchies) if explicit else None
    explicit_matches_title = bool(explicit and _query_matches_catalog_title(explicit, repo))
    explicit_is_parent = bool(
        explicit_match and category_is_parent(explicit_match.category, hierarchies)
    )
    explicit_is_context = bool(
        explicit
        and set(normalize_category_text(explicit).split())
        <= _CATEGORY_NEED_CONTEXT | _CATEGORY_QUERY_FILLER
    )
    taxonomy_match = resolve_catalog_category(type_query, hierarchies)
    category = _explicit_category(text) or (taxonomy_match.category if taxonomy_match else None)
    explicit_is_broad_category_cue = bool(
        category
        and explicit
        and normalize_category_text(explicit)
        in {normalize_category_text(cue) for cue in _category_cues(category)}
        and not query_names_catalog_leaf(explicit, hierarchies)
        and not explicit_matches_title
        and not explicit_is_specific_type
    )
    protect_explicit = bool(
        explicit
        and (
            query_names_catalog_leaf(explicit, hierarchies)
            or explicit_matches_title
            or explicit_is_specific_type
            or (
                not explicit_is_parent
                and not explicit_is_context
                and not explicit_is_broad_category_cue
            )
        )
    )
    protected_tokens = set(normalize_category_text(explicit).split()) if protect_explicit else set()

    broad_category_tokens: set[str] = set()
    if category:
        normalized_words = normalize_category_text(normalized).split()
        for cue in _category_cues(category):
            cue_text = _normalize_catalog_query(cue)
            cue_tokens = normalize_category_text(cue_text).split()
            if cue_tokens and any(
                normalized_words[index : index + len(cue_tokens)] == cue_tokens
                for index in range(len(normalized_words) - len(cue_tokens) + 1)
            ):
                broad_category_tokens.update(cue_tokens)
        taxonomy_parent = _CATEGORY_TAXONOMY_PARENTS.get(category)
        parent_key = normalize_category_text(taxonomy_parent or category)
        for path in hierarchies:
            normalized_path = [normalize_category_text(segment) for segment in path]
            if parent_key not in normalized_path:
                continue
            for segment in path[: normalized_path.index(parent_key) + 1]:
                broad_category_tokens.update(normalize_category_text(segment).split())

    # If the request names an exact taxonomy segment (including a concise
    # single-word department such as “thịt”), remove those category tokens
    # from the product-title filter. Explicit leaf types remain protected.
    taxonomy_match = resolve_catalog_category(type_query, hierarchies)
    if taxonomy_match and not query_names_catalog_leaf(normalized, hierarchies):
        broad_category_tokens.update(taxonomy_match.matched_tokens)

    # Explicit-intent extraction can return the last token of a broad phrase
    # (for example “vặt” from “đồ ăn vặt”). Do not let that token override the
    # already-resolved parent category and turn a category browse into a brittle
    # product-title search. A precise catalog leaf is kept above.
    if (
        explicit
        and not query_names_catalog_leaf(explicit, hierarchies)
        and not explicit_matches_title
        and not explicit_is_specific_type
    ):
        protected_tokens.difference_update(
            set(normalize_category_text(explicit).split()) & broad_category_tokens
        )

    retained: list[str] = []
    for token in normalize_category_text(normalized).split():
        if token in protected_tokens:
            retained.append(token)
        elif token not in broad_category_tokens and token not in (
            _CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT | _CUSTOMER_GROUP_QUERY_WORDS
        ):
            retained.append(token)
    result = " ".join(dict.fromkeys(retained))[:160]
    if explicit_is_specific_type and (
        getattr(repo, "products", None) is not None or repo.source_name == "demo_product_catalog"
    ):
        explicit_tokens = set(normalized_explicit.split())
        brand_tokens = _catalog_brand_tokens_in_query(normalized, repo)
        audience_tokens = {
            token
            for match in re.finditer(r"\b(?:tre em|em be|cho be|baby|kids|children)\b", normalized)
            for token in match.group(0).split()
            if token in _CUSTOMER_GROUP_QUERY_WORDS
        }
        size_tokens = {
            token
            for token in normalize_category_text(normalized).split()
            if re.fullmatch(r"\d+(?:[.,]\d+)?(?:ml|l|g|kg|gram|grams)", token)
        }
        selected_tokens = explicit_tokens | brand_tokens | audience_tokens | size_tokens
        if catalog_type_query:
            type_matches = list(
                re.finditer(
                    rf"(?<!\w){re.escape(catalog_type_query)}(?!\w)",
                    normalize_category_text(type_query),
                )
            )
            if type_matches:
                type_match = type_matches[-1]
                suffix = normalize_category_text(type_query)[type_match.end() :].split()
                for token in suffix:
                    if token in _CATEGORY_QUERY_FILLER or token in {
                        "gia",
                        "duoi",
                        "tam",
                        "khoang",
                        "tren",
                        "toi",
                        "khong",
                        "va",
                        "di",
                    }:
                        break
                    if token not in _CATEGORY_NEED_CONTEXT:
                        # Keep an adjacent variant or unknown brand ("XYZ")
                        # as an identity constraint, rather than silently
                        # substituting any product of this type.
                        selected_tokens.add(token)
        # Preserve the actual type phrase and its word order. Taking its words
        # from the entire utterance can reorder "kem dưỡng da" into "da kem
        # dưỡng", and a repeated "đầu" can destroy the phrase "dầu gội".
        extra_tokens = dict.fromkeys(
            token
            for token in normalize_category_text(normalized).split()
            if token in selected_tokens and token not in explicit_tokens
        )
        result = " ".join((normalized_explicit, *extra_tokens))[:160]
    if (
        category
        and result
        and not query_names_catalog_leaf(normalized, hierarchies)
        and not _query_matches_catalog_title(result, repo, category)
        and not explicit_is_specific_type
    ):
        # Do not turn an occasion, a vague need, or filler around a broad
        # category into a strict title filter. Exact local title/brand matches
        # remain searchable; a Postgres request performs no extra validation
        # query here, so the main catalog call stays within its latency budget.
        return ""
    return result


def execute_catalog_tool(
    name: str,
    raw_arguments: str,
    *,
    repository: ProductRepository | None = None,
    previous_mission: dict[str, Any] | None = None,
    previous_query: str | None = None,
    previous_queries: tuple[str, ...] = (),
    current_user_text: str | None = None,
) -> ToolObservation:
    if name not in {
        "search_products",
        "recommend_products",
        "plan_shopping_mission",
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
        if name == "plan_shopping_mission":
            result = build_shopping_mission_plan(arguments, repo, previous_mission)
            for option in result.content.get("options", []):
                for item in option.get("items", []):
                    item["data_source"] = repo.source_name
            return ToolObservation(
                content={**result.content, "mission_state": result.mission_state},
                products=result.products,
                tool_name=name,
                source=repo.source_name,
                response_mode="DETERMINISTIC_RECOMMENDATION",
                terminal=True,
            )
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
                if not getattr(repo, "supports_current_stock", False) or product.sellable_stock > 0
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
                        | ({"stock_is_current": False} if not product.stock_is_current else {})
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
            if parsed.spicy is not None and not getattr(repo, "supports_spicy_filter", True):
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The active catalog does not contain verified spice data.",
                    source=repo.source_name,
                    extra={"unavailable_fields": ["spicy"]},
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            if parsed.in_stock and not getattr(repo, "supports_current_stock", False):
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "Current store availability is not present in the dated catalog snapshot.",
                    source=repo.source_name,
                    extra={"unavailable_fields": ["current_stock"]},
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            list_hierarchies = getattr(repo, "list_category_hierarchies", None)
            hierarchies = list_hierarchies() if callable(list_hierarchies) else ()
            request_text = (current_user_text or parsed.query).strip() or parsed.query
            catalog_brands = dict(_catalog_brand_index(repo))
            if not catalog_brands:
                list_brands = getattr(repo, "list_brand_names", None)
                catalog_brands = {
                    normalize_category_text(brand): brand
                    for brand in (list_brands(limit=1000) if callable(list_brands) else [])
                    if brand
                }
            request_excluded_brands = excluded_catalog_brands(request_text, list(catalog_brands))
            brand_preference = parsed.brand
            if brand_preference:
                normalized_brand_preference = normalize_category_text(brand_preference)
                if (
                    normalized_brand_preference not in catalog_brands
                    or normalized_brand_preference in request_excluded_brands
                    or f" {normalized_brand_preference} "
                    not in f" {normalize_category_text(request_text)} "
                ):
                    brand_preference = None
                else:
                    brand_preference = catalog_brands[normalized_brand_preference]
            understanding = understand_catalog_query(
                request_text,
                hierarchies,
                previous_query=previous_query,
                previous_queries=previous_queries,
            )
            budget_understanding = understanding
            if budget_understanding.budget_vnd is not None and _has_budget_scope_ambiguity(
                budget_understanding
            ):
                return _budget_scope_clarification(name, repo, budget_understanding)
            budget_cap = _budget_unit_price_cap(budget_understanding)
            routed_product_title = _catalog_product_title_match(request_text, repo)
            routing_query = routed_product_title or (
                _restore_query_surface(
                    understanding.product_reference or parsed.query,
                    request_text,
                )
            )
            mentioned_brand = brand_preference or _catalog_brand_from_query(request_text, repo)
            brand_only_query = bool(
                mentioned_brand and _query_is_only_catalog_brand(routing_query, mentioned_brand)
            )
            matched_product_title = (
                None
                if brand_only_query
                else routed_product_title or _catalog_product_title_match(routing_query, repo)
            )
            product_identity = matched_product_title is not None
            identity_query = matched_product_title or routing_query
            category_context = request_text or parsed.query or routing_query
            query_category, category_only_query = _query_category_hint(category_context, repo)
            parsed_type_category = _category_from_product_type(understanding.product_type, repo)
            explicit_leaf_category = _explicit_query_leaf_category(category_context, repo)
            explicit_leaf_override = bool(
                explicit_leaf_category
                and understanding.product_type
                and normalize_category_text(explicit_leaf_category)
                != normalize_category_text(understanding.product_type)
                and (
                    not parsed_type_category
                    or normalize_category_text(explicit_leaf_category)
                    != normalize_category_text(parsed_type_category)
                )
                and not (
                    explicit_product_audience(category_context) == "child"
                    and parsed_type_category
                    and explicit_product_audience(understanding.product_type) == "child"
                )
            )
            if product_identity:
                query_category = None
                category_only_query = False
            elif explicit_leaf_override:
                query_category = explicit_leaf_category
                category_only_query = True
            elif parsed_type_category:
                query_category = parsed_type_category
                category_only_query = True
            elif understanding.product_type:
                # An unresolved family must not inherit a broad parser cue
                # such as the whole personal-care department.
                query_category = None
                category_only_query = False
            ambiguous_family = bool(
                understanding.product_type
                and not parsed_type_category
                and not product_identity
                and not explicit_leaf_override
            )
            category_source = (
                query_category
                if category_only_query and query_category
                else query_category
                if parsed_type_category or ambiguous_family or explicit_leaf_override
                else query_category
                if _query_names_a_specific_category(category_context, query_category, repo)
                else parsed.category or query_category
            )
            if product_identity:
                category_source = None
            category = _category_for_repository(category_source, repo)
            category_only_query = category_only_query or bool(category and not routing_query)
            category_only_query = category_only_query or _is_category_only_request(
                routing_query, category, repo
            )
            title_safe_constraints = _constraints_outside_catalog_title(
                understanding.negative_constraints,
                matched_product_title if product_identity else None,
            )
            (
                unsupported_constraints,
                excluded_brands,
                inferred_spicy,
                excluded_categories,
            ) = _negative_constraint_status(
                request_text,
                title_safe_constraints,
                repo,
                selected_category=category,
            )
            if unsupported_constraints:
                return _unsupported_constraint_error(name, repo, unsupported_constraints, category)
            requested_spicy = False if inferred_spicy is False else parsed.spicy
            if requested_spicy is not None and not getattr(repo, "supports_spicy_filter", True):
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The active catalog does not contain verified spice data.",
                    source=repo.source_name,
                    extra={"unavailable_fields": ["spicy"]},
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            if understanding.ranking_criterion in {"sales_popularity", "customer_rating"}:
                field = (
                    "sales_count"
                    if understanding.ranking_criterion == "sales_popularity"
                    else "customer_rating"
                )
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The catalog does not contain verified sales or customer-rating data.",
                    source=repo.source_name,
                    extra={
                        "requested_ranking": understanding.ranking_criterion,
                        "unavailable_fields": [field],
                        "product_type": category,
                        "alternative_sort": "price_ascending",
                    },
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            family_details_unverified = False
            effective_query = (
                identity_query
                if product_identity
                else _effective_catalog_search_query(
                    routing_query,
                    category_only_query,
                    repo,
                )
            )
            if brand_only_query and mentioned_brand:
                effective_query = mentioned_brand
            elif mentioned_brand and not product_identity:
                if parsed_type_category:
                    effective_query = ""
                elif not effective_query:
                    effective_query = routing_query
            if parsed_type_category and not product_identity and not explicit_leaf_override:
                # The catalog segment is the hard product-type constraint.
                # Do not feed negated phrases, question filler, or another
                # mentioned category back as positive title-search tokens.
                effective_query = _supported_query_details_for_category(
                    routing_query,
                    understanding.product_type,
                    understanding.use_case,
                    title_safe_constraints,
                    mentioned_brand,
                    category,
                    repo,
                )
            elif ambiguous_family:
                effective_query, family_details_unverified = _supported_family_query(
                    routing_query,
                    understanding.product_type,
                    understanding.use_case,
                    title_safe_constraints,
                    mentioned_brand,
                    repo,
                )
            elif explicit_leaf_override and not product_identity:
                effective_query = _supported_query_details_for_category(
                    routing_query,
                    explicit_leaf_category,
                    understanding.use_case,
                    title_safe_constraints,
                    mentioned_brand,
                    category,
                    repo,
                )
            if (
                category
                and not product_identity
                and normalize_category_text(effective_query) == normalize_category_text(category)
            ):
                # Once a recognized category is the only retrieval phrase,
                # browse that category and apply budget/quantity constraints
                # as structured filters instead of requiring those numbers in
                # product titles.
                effective_query = ""
                category_only_query = True
            if (
                understanding.ranking_criterion in {"price_ascending", "price_descending"}
                and category
                and not _exact_product_title(identity_query, repo)
            ):
                effective_query = ""
            category_browse = bool(category and not effective_query)
            diversify = (
                category_browse
                and _category_has_children(category, repo)
                and understanding.ranking_criterion == "relevance"
                and not excluded_brands
                and not excluded_categories
            )
            effective_max_price = parsed.max_price_vnd
            if budget_cap is not None:
                effective_max_price = (
                    budget_cap
                    if effective_max_price is None
                    else min(effective_max_price, budget_cap)
                )
            search_criteria = {
                "query": effective_query,
                "category": category,
                "brand": brand_preference,
                "max_price_vnd": effective_max_price,
                "spicy": requested_spicy,
                "in_stock": parsed.in_stock,
                "limit": 20
                if understanding.ranking_criterion in {"price_ascending", "price_descending"}
                else parsed.limit,
                "diversify_by_subcategory": diversify,
            }
            alternative_candidate_ids: set[str] = set()
            category_fallback_used = False
            products = tuple(repo.search(**search_criteria))
            brand_filter = search_criteria["brand"] or _mentioned_candidate_brand(
                request_text,
                products,
                excluded_brands,
            )
            if brand_filter:
                search_criteria["brand"] = brand_filter
                if not brand_preference:
                    products = tuple(repo.search(**search_criteria))
            supported_details, unmatched_details = (
                _category_query_unmatched_details(routing_query, category, repo)
                if not products
                else ((), ())
            )
            if not products and (
                category_only_query
                or (not product_identity and not brand_filter and unmatched_details)
            ):
                # Keep the product-name match when present (e.g. "Bim bim Poppedcorn");
                # keep details the snapshot can support, and broaden only within
                # an explicit category when some remaining details are unverified.
                search_criteria["query"] = " ".join(supported_details)
                products = tuple(repo.search(**search_criteria))
                category_fallback_used = bool(products and not category_only_query)
            if ambiguous_family:
                products = tuple(
                    product
                    for product in products
                    if _product_matches_unresolved_family(
                        product,
                        understanding.product_type,
                    )
                )
            audience_unverified = False
            requested_audience = explicit_product_audience(request_text)
            products = tuple(
                product
                for product in products
                if matches_explicit_product_audience(request_text, product)
                and not _product_is_in_excluded_category(product, excluded_categories)
            )
            if excluded_brands:
                products = tuple(
                    product
                    for product in products
                    if normalize_category_text(product.brand) not in excluded_brands
                )
            if not products and requested_audience == "child" and category and not product_identity:
                fallback_criteria = {
                    **search_criteria,
                    "query": "",
                    "category": category,
                    "limit": max(parsed.limit, 20),
                }
                fallback_candidates = tuple(repo.search(**fallback_criteria))
                fallback_candidates = tuple(
                    product
                    for product in fallback_candidates
                    if normalize_category_text(product.brand) not in excluded_brands
                    and not _product_is_in_excluded_category(product, excluded_categories)
                )
                labelled = tuple(
                    product
                    for product in fallback_candidates
                    if matches_explicit_product_audience(request_text, product)
                )
                if labelled:
                    products = labelled[: parsed.limit]
                else:
                    # When the catalog has no child-specific evidence, offer
                    # only generic candidates with an explicit label-check
                    # caveat; never imply they are child-safe or child-specific.
                    unverified = tuple(
                        product
                        for product in fallback_candidates
                        if product_audience_evidence(product) is None
                    )
                    products = unverified[: parsed.limit]
                    audience_unverified = bool(products)
            if understanding.ranking_criterion == "price_ascending":
                products = tuple(
                    sorted(products, key=lambda item: (item.price_vnd, item.name.casefold()))
                )[: parsed.limit]
            elif understanding.ranking_criterion == "price_descending":
                products = tuple(
                    sorted(products, key=lambda item: (-item.price_vnd, item.name.casefold()))
                )[: parsed.limit]
            if requested_audience == "adult" and any(
                product_audience_evidence(product) is None for product in products
            ):
                audience_unverified = True
            matched = [
                key
                for key, requested in (
                    ("category", "category" in parsed.model_fields_set),
                    ("brand", "brand" in parsed.model_fields_set or bool(brand_filter)),
                    (
                        "max_price_vnd",
                        "max_price_vnd" in parsed.model_fields_set or budget_cap is not None,
                    ),
                    ("spicy", "spicy" in parsed.model_fields_set),
                    ("in_stock", "in_stock" in parsed.model_fields_set),
                )
                if requested
            ]
            content = _candidate_content(products, matched)
            if family_details_unverified:
                content["family_details_unverified"] = True
            if budget_cap is not None:
                content["budget_constraint"] = {
                    "budget_vnd": budget_understanding.budget_vnd,
                    "budget_scope": budget_understanding.budget_scope,
                    "budget_operator": budget_understanding.budget_operator,
                    "quantity": budget_understanding.quantity,
                    "max_unit_price_vnd": budget_cap,
                }
            content["query_understanding"] = {
                "intent": understanding.intent,
                "secondary_intents": list(understanding.secondary_intents),
                "product_type": None
                if product_identity
                else understanding.product_type or category,
                "product_types": list(understanding.product_types),
                "product_reference": understanding.product_reference,
                "comparison_targets": list(understanding.comparison_targets),
                "brand_preference": brand_filter,
                "use_case": understanding.use_case,
                "ranking_criterion": understanding.ranking_criterion,
                "ranking_supported": understanding.ranking_criterion
                not in {"sales_popularity", "customer_rating"},
                "unverified_constraints": [
                    *([understanding.use_case] if understanding.use_case else []),
                    *(
                        ["Remaining product details could not be matched to catalog evidence"]
                        if category_fallback_used
                        else []
                    ),
                    *(
                        ["Catalog does not verify child suitability; check the product label"]
                        if audience_unverified or has_household_child_context(request_text)
                        else []
                    ),
                ],
            }
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
            if parsed.spicy is not None and not getattr(repo, "supports_spicy_filter", True):
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The active catalog does not contain verified spice data.",
                    source=repo.source_name,
                    extra={"unavailable_fields": ["spicy"]},
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            if parsed.categories is not None:
                return _whole_cart_recommendation(repo, parsed)
            price = parsed.price_constraint
            modifier = _product_type_attribute_modifier(parsed.preference_text)
            list_hierarchies = getattr(repo, "list_category_hierarchies", None)
            hierarchies = list_hierarchies() if callable(list_hierarchies) else ()
            understanding = understand_catalog_query(
                parsed.preference_text or parsed.query,
                hierarchies,
                previous_query=previous_query,
                previous_queries=previous_queries,
            )
            budget_understanding = understand_catalog_query(
                current_user_text or parsed.preference_text or parsed.query,
                hierarchies,
                previous_query=previous_query,
                previous_queries=previous_queries,
            )
            if budget_understanding.budget_vnd is not None and _has_budget_scope_ambiguity(
                budget_understanding
            ):
                return _budget_scope_clarification(name, repo, budget_understanding)
            budget_cap = _budget_unit_price_cap(budget_understanding)
            query_understanding = understand_catalog_query(
                parsed.query,
                hierarchies,
                previous_query=previous_query,
                previous_queries=previous_queries,
            )
            routing_query = (
                _restore_query_surface(
                    query_understanding.product_reference,
                    current_user_text or parsed.query,
                )
                if parsed.query
                else ""
                if parsed.category
                else understanding.product_reference
            )
            mentioned_brand = parsed.brand or _catalog_brand_from_query(routing_query, repo)
            brand_only_query = bool(
                mentioned_brand and _query_is_only_catalog_brand(routing_query, mentioned_brand)
            )
            matched_product_title = (
                None if brand_only_query else _catalog_product_title_match(routing_query, repo)
            )
            product_identity = matched_product_title is not None
            identity_query = matched_product_title or routing_query
            category_context = (
                parsed.preference_text if modifier else routing_query or parsed.preference_text
            )
            query_category, category_only_query = _query_category_hint(category_context, repo)
            parsed_type_category = _category_from_product_type(understanding.product_type, repo)
            explicit_leaf_category = _explicit_query_leaf_category(category_context, repo)
            explicit_leaf_override = bool(
                explicit_leaf_category
                and understanding.product_type
                and normalize_category_text(explicit_leaf_category)
                != normalize_category_text(understanding.product_type)
                and (
                    not parsed_type_category
                    or normalize_category_text(explicit_leaf_category)
                    != normalize_category_text(parsed_type_category)
                )
                and not (
                    explicit_product_audience(category_context) == "child"
                    and parsed_type_category
                    and explicit_product_audience(understanding.product_type) == "child"
                )
            )
            if product_identity:
                query_category = None
                category_only_query = False
            elif explicit_leaf_override:
                query_category = explicit_leaf_category
                category_only_query = True
            elif parsed_type_category:
                query_category = parsed_type_category
                category_only_query = True
            elif understanding.product_type:
                query_category = None
                category_only_query = False
            ambiguous_family = bool(
                understanding.product_type
                and not parsed_type_category
                and not product_identity
                and not explicit_leaf_override
            )
            category_source = (
                query_category
                if parsed_type_category or ambiguous_family or explicit_leaf_override
                else query_category
                if _query_names_a_specific_category(category_context, query_category, repo)
                else parsed.category or query_category
            )
            if product_identity:
                category_source = None
            category = _category_for_repository(category_source, repo)
            category_only_query = category_only_query or _is_category_only_request(
                routing_query, category, repo
            )
            title_safe_constraints = _constraints_outside_catalog_title(
                understanding.negative_constraints,
                matched_product_title if product_identity else None,
            )
            (
                unsupported_constraints,
                excluded_brands,
                inferred_spicy,
                excluded_categories,
            ) = _negative_constraint_status(
                parsed.preference_text or parsed.query,
                title_safe_constraints,
                repo,
                selected_category=category,
            )
            if unsupported_constraints:
                return _unsupported_constraint_error(name, repo, unsupported_constraints, category)
            requested_spicy = False if inferred_spicy is False else parsed.spicy
            if requested_spicy is not None and not getattr(repo, "supports_spicy_filter", True):
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The active catalog does not contain verified spice data.",
                    source=repo.source_name,
                    extra={"unavailable_fields": ["spicy"]},
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            family_details_unverified = False
            effective_query = (
                identity_query
                if product_identity
                else _effective_catalog_search_query(
                    routing_query,
                    category_only_query,
                    repo,
                )
            )
            if brand_only_query and mentioned_brand:
                effective_query = mentioned_brand
            elif mentioned_brand and not product_identity:
                if parsed_type_category:
                    effective_query = ""
                elif not effective_query:
                    effective_query = routing_query
            if parsed_type_category and not product_identity and not explicit_leaf_override:
                effective_query = _supported_query_details_for_category(
                    routing_query,
                    understanding.product_type,
                    understanding.use_case,
                    title_safe_constraints,
                    mentioned_brand,
                    category,
                    repo,
                )
            elif ambiguous_family:
                effective_query, family_details_unverified = _supported_family_query(
                    routing_query,
                    understanding.product_type,
                    understanding.use_case,
                    title_safe_constraints,
                    mentioned_brand,
                    repo,
                )
            elif explicit_leaf_override and not product_identity:
                effective_query = _supported_query_details_for_category(
                    routing_query,
                    explicit_leaf_category,
                    understanding.use_case,
                    title_safe_constraints,
                    mentioned_brand,
                    category,
                    repo,
                )
            if (
                understanding.ranking_criterion in {"price_ascending", "price_descending"}
                and category
                and not _exact_product_title(identity_query, repo)
            ):
                effective_query = ""
            named_type = bool(
                callable(list_hierarchies) and query_names_catalog_leaf(routing_query, hierarchies)
            )
            if (
                category_only_query
                and named_type
                and not parsed_type_category
                and not _is_category_only_request(routing_query, category, repo)
            ):
                effective_query = _effective_catalog_search_query(routing_query, False, repo)
            category_browse = bool(category and not effective_query)
            diversify = (
                category_browse
                and _category_has_children(category, repo)
                and understanding.ranking_criterion == "relevance"
                and not excluded_brands
                and not excluded_categories
            )
            search_criteria = {
                "query": effective_query,
                "category": category,
                "brand": parsed.brand,
                "max_price_vnd": budget_cap,
                "spicy": requested_spicy,
                "in_stock": bool(getattr(repo, "supports_current_stock", False)),
                "limit": 20
                if understanding.ranking_criterion in {"price_ascending", "price_descending"}
                else parsed.limit,
                "price_operator": price.operator if price else None,
                "price_value_vnd": price.value_vnd if price else None,
                "diversify_by_subcategory": diversify,
            }
            preference_text_for_ranking = (
                understanding.product_reference
                if understanding.use_case or understanding.ranking_criterion != "relevance"
                else parsed.preference_text
            )
            category_fallback_used = False
            products, preference_matches = search_with_preferences(
                repo,
                search_criteria,
                preference_text_for_ranking,
            )
            supported_details, unmatched_details = (
                _category_query_unmatched_details(routing_query, category, repo)
                if not products
                else ((), ())
            )
            if not products and category_only_query and not parsed.query:
                search_criteria["query"] = ""
                products, preference_matches = search_with_preferences(
                    repo,
                    search_criteria,
                    preference_text_for_ranking,
                )
            elif (
                not products and not product_identity and not mentioned_brand and unmatched_details
            ):
                search_criteria["query"] = " ".join(supported_details)
                products, preference_matches = search_with_preferences(
                    repo,
                    search_criteria,
                    preference_text_for_ranking,
                )
                category_fallback_used = bool(products)
            brand_query = " ".join((parsed.query, parsed.preference_text)).strip()
            brand_filter = parsed.brand or _mentioned_candidate_brand(
                brand_query,
                products,
                excluded_brands,
            )
            if brand_filter:
                search_criteria["brand"] = brand_filter
                if not parsed.brand:
                    products, preference_matches = search_with_preferences(
                        repo,
                        search_criteria,
                        preference_text_for_ranking,
                    )
            preparation = frozenset().union(
                preparation_terms(understanding.use_case or ""),
                preparation_terms(parsed.preference_text),
            )
            if category and title_mentions_preparation(category, preparation):
                preparation = frozenset()
            preparation_alternatives_unverified = False
            if preparation:
                products = tuple(
                    product
                    for product in products
                    if title_mentions_preparation(product.name, preparation)
                )
                preparation_title_match = bool(products)
                if not products and category and not product_identity:
                    category_words = normalize_category_text(category).split()
                    candidates: dict[str, ProductSnapshot] = {}
                    for method in preparation:
                        probe = {
                            **search_criteria,
                            "query": method,
                            "category": None,
                            "limit": 100,
                            "diversify_by_subcategory": False,
                        }
                        candidates.update(
                            (product.product_id, product) for product in repo.search(**probe)
                        )
                    excluded = excluded_catalog_brands(
                        parsed.preference_text,
                        [product.brand for product in candidates.values() if product.brand],
                    )
                    products = tuple(
                        product
                        for product in candidates.values()
                        if all(
                            word
                            in normalize_category_text(
                                product.category_hierarchy or product.category
                            ).split()
                            for word in category_words
                        )
                        and title_mentions_preparation(product.name, preparation)
                        and product.product_id not in parsed.exclude_product_ids
                        and normalize_category_text(product.brand) not in excluded
                    )[: parsed.limit]
                    preparation_title_match = bool(products)
                    preference_matches = {}
                if not products and parsed.alternative_requested and category:
                    candidates = tuple(
                        repo.search(
                            **{
                                **search_criteria,
                                "query": "",
                                "category": category,
                                "limit": 100,
                                "diversify_by_subcategory": False,
                            }
                        )
                    )
                    previous_items = tuple(
                        item
                        for product_id in parsed.exclude_product_ids
                        if (item := repo.get_product_snapshot(product_id)) is not None
                    )
                    previous_terms = set().union(
                        *(
                            set(normalize_category_text(item.name).split())
                            for item in previous_items
                        )
                    )
                    candidates = tuple(
                        product
                        for product in candidates
                        if product.product_id not in parsed.exclude_product_ids
                    )
                    if previous_terms:
                        candidates = tuple(
                            sorted(
                                candidates,
                                key=lambda product: (
                                    -len(
                                        previous_terms
                                        & set(normalize_category_text(product.name).split())
                                    ),
                                    product.price_vnd,
                                    product.name.casefold(),
                                ),
                            )
                        )
                    products = candidates[: parsed.limit]
                    preparation_alternatives_unverified = bool(products)
                    preference_matches = {}
            else:
                preparation_title_match = False
            audience_unverified = False
            if not products and parsed_type_category and category and not product_identity:
                fallback_criteria = {
                    **search_criteria,
                    "query": "",
                    "category": category,
                    "limit": max(parsed.limit, 20),
                }
                fallback_products = repo.search(**fallback_criteria)
                audience_query = " ".join(
                    part for part in (parsed.query, parsed.preference_text) if part
                )
                products = tuple(
                    product
                    for product in fallback_products
                    if product.product_id not in parsed.exclude_product_ids
                    and normalize_category_text(product.brand) not in excluded_brands
                    and matches_explicit_product_audience(audience_query, product)
                    and not _product_is_in_excluded_category(product, excluded_categories)
                )[: parsed.limit]
                preference_matches = {}
                category_fallback_used = bool(products)
                if preparation and products:
                    preparation_alternatives_unverified = True
            if excluded_brands:
                products = tuple(
                    product
                    for product in products
                    if normalize_category_text(product.brand) not in excluded_brands
                )
            if understanding.ranking_criterion in {"sales_popularity", "customer_rating"}:
                field = (
                    "sales_count"
                    if understanding.ranking_criterion == "sales_popularity"
                    else "customer_rating"
                )
                return _tool_error(
                    name,
                    "DATA_UNAVAILABLE",
                    "The catalog does not contain verified sales or customer-rating data.",
                    source=repo.source_name,
                    extra={
                        "requested_ranking": understanding.ranking_criterion,
                        "unavailable_fields": [field],
                        "product_type": category,
                        "alternative_sort": "price_ascending",
                    },
                    response_mode="DETERMINISTIC_ERROR",
                    terminal=True,
                )
            if understanding.ranking_criterion == "price_ascending":
                products = tuple(
                    sorted(products, key=lambda item: (item.price_vnd, item.name.casefold()))
                )[: parsed.limit]
            elif understanding.ranking_criterion == "price_descending":
                products = tuple(
                    sorted(products, key=lambda item: (-item.price_vnd, item.name.casefold()))
                )[: parsed.limit]
            if parsed.alternative_requested and parsed.exclude_product_ids:
                alternative_candidates = list(
                    repo.search(**{**search_criteria, "limit": 100})
                )
                alternative_candidate_ids = {
                    product.product_id for product in alternative_candidates
                }
                candidates = list(products) + alternative_candidates
                excluded_brands = excluded_catalog_brands(
                    parsed.preference_text, [p.brand for p in candidates if p.brand]
                )
                products = tuple(
                    {
                        p.product_id: p
                        for p in candidates
                        if p.product_id not in parsed.exclude_product_ids
                        and normalize_category_text(p.brand) not in excluded_brands
                    }.values()
                )[: parsed.limit]
            if ambiguous_family:
                products = tuple(
                    product
                    for product in products
                    if _product_matches_unresolved_family(
                        product,
                        understanding.product_type,
                    )
                )
            audience_query = " ".join(
                part for part in (parsed.query, parsed.preference_text) if part
            )
            products = tuple(
                product
                for product in products
                if matches_explicit_product_audience(audience_query, product)
                and not _product_is_in_excluded_category(product, excluded_categories)
            )
            requested_audience = explicit_product_audience(audience_query)
            if (
                not products
                and explicit_product_audience(audience_query) == "child"
                and category
                and not product_identity
            ):
                fallback_candidates = tuple(
                    repo.search(
                        **{
                            **search_criteria,
                            "query": "",
                            "category": category,
                            "limit": max(parsed.limit, 20),
                        }
                    )
                )
                fallback_candidates = tuple(
                    product
                    for product in fallback_candidates
                    if product.product_id not in parsed.exclude_product_ids
                    and normalize_category_text(product.brand) not in excluded_brands
                    and not _product_is_in_excluded_category(product, excluded_categories)
                )
                labelled = tuple(
                    product
                    for product in fallback_candidates
                    if matches_explicit_product_audience(audience_query, product)
                )
                if labelled:
                    products = labelled[: parsed.limit]
                else:
                    products = tuple(
                        product
                        for product in fallback_candidates
                        if product_audience_evidence(product) is None
                    )[: parsed.limit]
                    audience_unverified = bool(products)
            if requested_audience == "adult" and any(
                product_audience_evidence(product) is None for product in products
            ):
                audience_unverified = True
            preference_matches = {
                product.product_id: preference_matches[product.product_id]
                for product in products
                if product.product_id in preference_matches
            }
            matched = [
                "sellable_only"
                if getattr(repo, "supports_current_stock", False)
                else "current_availability_unverified"
            ]
            matched.extend(
                key
                for key in ("category", "brand", "spicy")
                if key in parsed.model_fields_set
                or (key == "brand" and brand_filter)
                or (key == "spicy" and requested_spicy is not None)
            )
            if price:
                matched.append(f"price_{price.operator.lower()}_{price.value_vnd}")
            if budget_cap is not None:
                matched.append("budget_vnd")
            content = _candidate_content(products, matched)
            content["alternative_requested"] = parsed.alternative_requested
            content["alternative_exhausted"] = bool(
                parsed.alternative_requested
                and parsed.exclude_product_ids
                and alternative_candidate_ids
                and alternative_candidate_ids.issubset(set(parsed.exclude_product_ids))
                and not products
            )
            if family_details_unverified:
                content["family_details_unverified"] = True
            if category_fallback_used:
                content["unverified_constraints"] = [
                    "Remaining product details could not be matched to catalog evidence"
                ]
            if audience_unverified or has_household_child_context(audience_query):
                content["unverified_constraints"] = list(
                    dict.fromkeys(
                        [
                            *content.get("unverified_constraints", []),
                            "Catalog does not verify child suitability; check the product label",
                        ]
                    )
                )
            if understanding.use_case:
                content["use_case"] = understanding.use_case
                content["use_case_verified"] = False
                content["unverified_constraints"] = list(
                    dict.fromkeys(
                        [*content.get("unverified_constraints", []), understanding.use_case]
                    )
                )
                content["recommendation_evidence"] = "CATALOG_CATEGORY_ONLY"
                if preparation and not preparation_title_match:
                    content["preparation_unverified"] = True
                    current_turn = understand_catalog_query(parsed.query, hierarchies)
                    content["preparation_followup"] = bool(
                        previous_queries and current_turn.use_case is None
                    )
            if preparation_alternatives_unverified:
                content["preparation_alternatives_unverified"] = True
            if preparation_title_match:
                content["use_case_title_match"] = True
            content["constraints"] = {
                "category": category,
                "brand": parsed.brand,
                "spicy": parsed.spicy,
                "price_operator": price.operator if price else None,
                "price_value_vnd": price.value_vnd if price else None,
                "budget_vnd": budget_understanding.budget_vnd,
                "budget_scope": budget_understanding.budget_scope,
                "budget_operator": budget_understanding.budget_operator,
                "quantity": budget_understanding.quantity,
                "max_unit_price_vnd": budget_cap,
            }
            content["current_stock_verified"] = bool(getattr(repo, "supports_current_stock", False))
            if preference_matches:
                content["preference_matches"] = preference_matches
                content["preference_evidence"] = "PRODUCT_TITLE_ONLY"
            subcategories = list(dict.fromkeys(category_leaf(product) for product in products))
            if category_browse and len(subcategories) > 1:
                content["browse_mode"] = "CATEGORY_OVERVIEW"
                content["category_label"] = category
                content["subcategories"] = subcategories
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
            data["stock"] = product.sellable_stock if product.stock_is_current else product.stock
            if not product.stock_is_current:
                data["stock_is_current"] = False
                data["availability_status"] = "NOT_CURRENTLY_VERIFIED"
            elif product.sellable_stock == 0:
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
