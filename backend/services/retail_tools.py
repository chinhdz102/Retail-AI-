from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product as cart_product
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.products.catalog import DEMO_PRODUCTS, PriceOperator, ProductSnapshot
from backend.products.category_taxonomy import (
    CatalogCategoryMatch,
    category_is_parent,
    category_leaf,
    normalize_category_text,
    query_names_catalog_leaf,
    resolve_catalog_category,
)
from backend.products.repository import ProductRepository, get_product_repository
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
from backend.services.vietnamese_intent import recommendation_category_cues as _category_cues
from backend.shared.contracts import SessionState

MAX_PRODUCT_RESULTS = 10
MAX_RECOMMENDATION_CATEGORIES = 4
MAX_RECOMMENDATION_RESULTS = 5
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
    "nha nhe noi thoi tui di "
    "um uhm neu thi chon nen goi so vai phim nhanh y "
    "nhom san tim toi tu van voi xem the nao phu hop khong duoc do mot san pham "
    "em anh chi ay minh vay va nhu co the cac nhung hay bat ky thu nao type products product "
    "some any please find looking for recommend recommend me suggest show me want need "
    "good best better "
    "what which do can could have is are the for in your category item items kind type "
    "i me to of and a an this that some any"
    .split()
)
_CATEGORY_NEED_CONTEXT = frozenset(
    "da mat tay kho dau nhay cam mun toc rung gau bang bep chen bat quan ao giat "
    "rua lau sach mem phu hop nhu cau duong am lam dep don dep ve sinh cham soc "
    "dung cho cho be em gia dinh ca nhan nong lanh ngon cay man ngot re budget bet "
    "thich dung su dung mau da tone mua sam tu van dry sensitive oily skin healthy"
    .split()
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


def _query_matches_catalog_title(query: str, repository: ProductRepository) -> bool:
    terms = set(normalize_category_text(query).split())
    if not terms:
        return False
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    if products is None:
        return False
    return any(
        terms
        <= set(normalize_category_text(product.name).split())
        | set(normalize_category_text(product.brand).split())
        for product in products
    )


def _catalog_brand_tokens_in_query(query: str, repository: ProductRepository) -> set[str]:
    query_tokens = set(normalize_category_text(query).split())
    products = getattr(repository, "products", None)
    if products is None and repository.source_name == "demo_product_catalog":
        products = DEMO_PRODUCTS
    if products is None:
        return set()
    return {
        token
        for product in products
        for token in normalize_category_text(product.brand).split()
        if token in query_tokens
    }


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
    category = _explicit_category(query)
    taxonomy_query = _normalize_catalog_query(query)
    match: CatalogCategoryMatch | None = None
    hierarchies: tuple[tuple[str, ...], ...] = ()
    if repository is not None:
        list_hierarchies = getattr(repository, "list_category_hierarchies", None)
        if callable(list_hierarchies):
            try:
                hierarchies = list_hierarchies()
                match = resolve_catalog_category(taxonomy_query, hierarchies)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                match = None
                hierarchies = ()
    related_match = False
    if category and match is not None:
        taxonomy_parent = _CATEGORY_TAXONOMY_PARENTS.get(category)
        related_match = bool(
            match.exact_phrase
            or (
                taxonomy_parent
                and any(
                    normalize_category_text(taxonomy_parent)
                    in normalize_category_text(segment)
                    for path in hierarchies
                    if match.category in path
                    for segment in path
                )
            )
        )
        if related_match:
            match_is_explicit_leaf = (
                not category_is_parent(match.category, hierarchies)
                and bool(
                    re.search(
                        rf"(?<!\w){re.escape(normalize_category_text(match.category))}(?!\w)",
                        normalize_category_text(taxonomy_query),
                    )
                )
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
    if query_names_catalog_leaf(taxonomy_query, hierarchies) or (
        repository is not None
        and _query_matches_catalog_title(taxonomy_query, repository)
    ):
        return category, False
    return category, not residual


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
        match = resolve_catalog_category(_normalize_catalog_query(query), hierarchies)
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
    normalized_query = _normalize_catalog_query(query)
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
        set(normalize_category_text(explicit_query).split())
        if protect_product_type
        else set()
    )
    tokens = normalize_category_text(normalized_query).split()
    retained = [
        token
        for token in tokens
        if token in protected_tokens
        or token not in _CATEGORY_QUERY_FILLER | _CATEGORY_NEED_CONTEXT
    ]
    if not retained:
        return ""
    retained_tokens = set(retained)
    raw_tokens = re.findall(r"[^\W_]+", normalized_query, flags=re.UNICODE)
    normalized_tokens = [
        normalize_category_text(token).split() for token in raw_tokens
    ]
    if all(len(tokens_for_word) == 1 for tokens_for_word in normalized_tokens):
        return " ".join(
            raw
            for raw, tokens_for_word in zip(raw_tokens, normalized_tokens, strict=True)
            if tokens_for_word[0] in retained_tokens
        )[:160]
    return " ".join(dict.fromkeys(retained))[:160]


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
    explicit = _normalize_catalog_query(_explicit_query(text) or "")
    normalized_explicit = normalize_category_text(explicit)
    explicit_is_specific_type = bool(
        explicit
        and _explicit_query(text)
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
    category = _explicit_category(text)
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
    protected_tokens = (
        set(normalize_category_text(explicit).split()) if protect_explicit else set()
    )

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
    taxonomy_match = resolve_catalog_category(normalized, hierarchies)
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
            _CATEGORY_QUERY_FILLER
            | _CATEGORY_NEED_CONTEXT
            | _CUSTOMER_GROUP_QUERY_WORDS
        ):
            retained.append(token)
    result = " ".join(dict.fromkeys(retained))[:160]
    if explicit_is_specific_type and (
        getattr(repo, "products", None) is not None
        or repo.source_name == "demo_product_catalog"
    ):
        explicit_tokens = set(normalized_explicit.split())
        brand_tokens = _catalog_brand_tokens_in_query(normalized, repo)
        audience_tokens = set(normalize_category_text(normalized).split()) & (
            _CUSTOMER_GROUP_QUERY_WORDS | {"tre", "em", "be"}
        )
        size_tokens = {
            token
            for token in normalize_category_text(normalized).split()
            if re.fullmatch(r"\d+(?:[.,]\d+)?(?:ml|l|g|kg|gram|grams)", token)
        }
        selected_tokens = explicit_tokens | brand_tokens | audience_tokens | size_tokens
        result = " ".join(
            dict.fromkeys(
                token
                for token in normalize_category_text(normalized).split()
                if token in selected_tokens
            )
        )[:160]
    if (
        category
        and result
        and not query_names_catalog_leaf(normalized, hierarchies)
        and not _query_matches_catalog_title(result, repo)
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
                if not getattr(repo, "supports_current_stock", False)
                or product.sellable_stock > 0
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
                        | (
                            {"stock_is_current": False}
                            if not product.stock_is_current
                            else {}
                        )
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
            query_category, category_only_query = _query_category_hint(parsed.query, repo)
            category_source = (
                query_category
                if _query_names_a_specific_category(parsed.query, query_category, repo)
                else parsed.category or query_category
            )
            category = _category_for_repository(category_source, repo)
            effective_query = _effective_catalog_search_query(
                parsed.query,
                category_only_query,
                repo,
            )
            category_browse = bool(category and not effective_query)
            diversify = category_browse and _category_has_children(category, repo)
            search_criteria = {
                "query": effective_query,
                "category": category,
                "brand": parsed.brand,
                "max_price_vnd": parsed.max_price_vnd,
                "spicy": parsed.spicy,
                "in_stock": parsed.in_stock,
                "limit": parsed.limit,
                "diversify_by_subcategory": diversify,
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
            query_category, category_only_query = _query_category_hint(parsed.query, repo)
            category_source = (
                query_category
                if _query_names_a_specific_category(parsed.query, query_category, repo)
                else parsed.category or query_category
            )
            category = _category_for_repository(category_source, repo)
            effective_query = _effective_catalog_search_query(
                parsed.query,
                category_only_query,
                repo,
            )
            category_browse = bool(category and not effective_query)
            diversify = category_browse and _category_has_children(category, repo)
            search_criteria = {
                "query": effective_query,
                "category": category,
                "brand": parsed.brand,
                "spicy": parsed.spicy,
                "in_stock": bool(getattr(repo, "supports_current_stock", False)),
                "limit": parsed.limit,
                "price_operator": price.operator if price else None,
                "price_value_vnd": price.value_vnd if price else None,
                "diversify_by_subcategory": diversify,
            }
            products = tuple(repo.search(**search_criteria))
            if not products and category_only_query:
                search_criteria["query"] = ""
                products = tuple(repo.search(**search_criteria))
            matched = [
                "sellable_only"
                if getattr(repo, "supports_current_stock", False)
                else "current_availability_unverified"
            ]
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
            subcategories = list(
                dict.fromkeys(category_leaf(product) for product in products)
            )
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
            data["stock"] = (
                product.sellable_stock if product.stock_is_current else product.stock
            )
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
