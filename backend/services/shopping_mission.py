from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from itertools import product as cartesian_product
from math import ceil
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import (
    catalog_category_query_aliases,
    normalize_category_text,
    resolve_catalog_category,
    resolve_catalog_category_alias,
)
from backend.products.repository import ProductRepository

MAX_MISSION_NEEDS = 6
MAX_NEED_CANDIDATES = 5
MAX_PLAN_OPTIONS = 3
MAX_ALTERNATIVE_CATEGORIES = 3
MAX_PLANNED_ITEM_QUANTITY = 200
BEVERAGE_SERVING_ML_PER_PERSON = 250
SHAREABLE_SNACK_GRAMS_PER_PERSON = 30
_SCENE_QUERY_WORDS = frozenset(
    {
        "an",
        "bua",
        "buoi",
        "ca",
        "chia",
        "cho",
        "de",
        "dung",
        "du",
        "gia",
        "giai",
        "group",
        "guests",
        "hop",
        "khoang",
        "khat",
        "lai",
        "mat",
        "mieng",
        "mon",
        "nhau",
        "nhe",
        "nhom",
        "nguoi",
        "party",
        "people",
        "phu",
        "rai",
        "se",
        "share",
        "sharing",
        "suat",
        "tuoi",
        "uong",
        "viewing",
        "watching",
        "xem",
    }
)


class ShoppingNeedSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    need_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=100)
    rationale: str = Field(default="", max_length=180)
    category: str = Field(min_length=1, max_length=160)
    alternative_categories: list[str] = Field(
        default_factory=list, max_length=MAX_ALTERNATIVE_CATEGORIES
    )
    source: Literal["EXPLICIT", "INFERRED"]
    required: bool
    quantity: int = Field(default=1, ge=1, le=MAX_PLANNED_ITEM_QUANTITY)
    quantity_is_explicit: bool = False
    product_query: str = Field(default="", max_length=160)
    brand: str | None = Field(default=None, max_length=120)
    max_price_vnd: int | None = Field(default=None, ge=0, le=100_000_000)
    spicy: bool | None = None
    preference_terms: list[str] = Field(default_factory=list, max_length=8)
    unverified_constraints: list[str] = Field(default_factory=list, max_length=8)
    satisfied_by_existing: bool = False

    @model_validator(mode="after")
    def inferred_needs_are_optional(self) -> ShoppingNeedSpec:
        if self.source == "INFERRED":
            self.required = False
        return self


class ShoppingMissionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goal: str = Field(default="", max_length=300)
    use_case: str = Field(default="", max_length=240)
    occasion: str = Field(default="", max_length=160)
    party_size: int | None = Field(default=None, ge=1, le=100)
    total_budget_vnd: int | None = Field(default=None, ge=1, le=100_000_000)
    total_budget_exclusive: bool = False
    hard_requirements: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=12)
    preferences: list[str] = Field(default_factory=list, max_length=12)
    existing_items: list[str] = Field(default_factory=list, max_length=12)
    unknown_details: list[str] = Field(default_factory=list, max_length=12)
    pending_decision: str | None = Field(default=None, max_length=180)
    needs: list[ShoppingNeedSpec] = Field(default_factory=list, max_length=MAX_MISSION_NEEDS)
    remove_need_ids: list[str] = Field(default_factory=list, max_length=MAX_MISSION_NEEDS)
    rejected_product_ids: list[str] = Field(default_factory=list, max_length=24)
    reset_mission: bool = False
    clear_total_budget: bool = False
    clear_party_size: bool = False
    clear_preferences: bool = False
    clear_exclusions: bool = False
    clear_hard_requirements: bool = False
    clear_existing_items: bool = False
    clear_unknown_details: bool = False
    clear_pending_decision: bool = False


@dataclass(frozen=True)
class ShoppingMissionResult:
    content: dict[str, Any]
    products: tuple[ProductSnapshot, ...]
    mission_state: dict[str, Any]


def _tokens(value: str) -> set[str]:
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return set(re.findall(r"[a-z0-9]+", unaccented))


def _is_structured_mission_metadata(value: str, mission: dict[str, Any]) -> bool:
    """Do not treat budget or party-size paraphrases as unverifiable SKU attributes."""
    normalized = normalize_category_text(value)
    amounts = {
        int(digits)
        for match in re.findall(r"\d[\d.,]*", str(value))
        if (digits := re.sub(r"\D", "", match))
    }
    budget = mission.get("total_budget_vnd")
    budget_cue = bool(
        re.search(r"\b(?:ngan sach|tong chi|chi phi|chi tieu|muc chi|budget|spend)\b", normalized)
    )
    if budget is not None and budget_cue and int(budget) in amounts:
        return True

    party_size = mission.get("party_size")
    party_cue = bool(
        re.search(r"\b(?:nguoi|be|tre em|people|persons|guests)\b", normalized)
    )
    party_context = bool(
        re.search(r"\b(?:du|cho|danh cho|phuc vu|nhom|party|group|for|people)\b", normalized)
    )
    party_metadata_words = {
        "du",
        "cho",
        "danh",
        "for",
        "all",
        "phuc",
        "vu",
        "nhom",
        "ca",
        "party",
        "group",
        "nguoi",
        "be",
        "tre",
        "em",
        "people",
        "persons",
        "guests",
        "khoang",
    }
    remaining_words = (
        _tokens(normalized)
        - party_metadata_words
        - {token for token in _tokens(normalized) if token.isdigit()}
    )
    serving_context = bool(
        re.search(
            r"\b(?:de chia se|chia nhau|du|dung cho|phu hop|moi suat|moi phan|"
            r"share|sharing|serving|serve)\b",
            normalized,
        )
    )
    return bool(
        party_size is not None
        and party_cue
        and int(party_size) in amounts
        and party_context
        and (
            not remaining_words
            or (
                serving_context
                and remaining_words <= {"de", "chia", "se", "du", "dung", "phu", "hop"}
            )
        )
    )


def _unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        cleaned = " ".join(str(value).split())[:160]
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result[:12]


def _resolve_need_category(
    category: str,
    repository: ProductRepository,
    *,
    semantic_query: str = "",
) -> str | None:
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return None
    try:
        hierarchies = tuple(list_hierarchies())
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return None

    semantic_alias = resolve_catalog_category_alias(semantic_query, hierarchies)
    if semantic_alias is not None:
        return semantic_alias.category
    semantic_category = resolve_catalog_category(semantic_query, hierarchies)
    if semantic_category is not None and semantic_category.exact_phrase:
        # An exact catalog leaf named in the product query is stronger
        # evidence than a neighboring category supplied by the planner.
        return semantic_category.category

    # The planner prompt may return the complete taxonomy path. Resolving that
    # whole string as one phrase can select a broad parent (or a normalized
    # concatenation that does not exist in the repository). Prefer the deepest
    # exact catalog label in the path, which is the category repository.search
    # expects.
    path_segments = [segment.strip() for segment in re.split(r"\s*(?:>|›|/)\s*", category)]
    for segment in reversed(path_segments):
        if not segment:
            continue
        normalized_segment = normalize_category_text(segment)
        exact_labels = {
            label
            for path in hierarchies
            for label in path
            if normalize_category_text(label) == normalized_segment
        }
        if len(exact_labels) == 1:
            return next(iter(exact_labels))

    fallback_category = path_segments[-1] if len(path_segments) > 1 else category
    match = resolve_catalog_category(fallback_category, hierarchies)
    if match:
        return match.category
    match = resolve_catalog_category(category, hierarchies)
    return match.category if match else None


def catalog_category_hints(
    repository: ProductRepository,
    *,
    limit: int = 48,
) -> tuple[str, ...]:
    """Return data-derived intermediate taxonomy nodes for semantic planning."""
    list_hierarchies = getattr(repository, "list_category_hierarchies", None)
    if not callable(list_hierarchies):
        return ()
    try:
        paths = list_hierarchies()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return ()

    occurrences: dict[str, int] = {}
    examples: dict[str, str] = {}
    for path in paths:
        for index, label in enumerate(path):
            if index < 1:
                continue
            normalized = " ".join(label.split())
            if not normalized:
                continue
            occurrences[normalized] = occurrences.get(normalized, 0) + 1
            examples.setdefault(normalized, " > ".join(path[: index + 1]))

    ranked = sorted(
        occurrences,
        key=lambda label: (-occurrences[label], len(label), label.casefold()),
    )
    return tuple(examples[label] for label in ranked[: max(1, limit)])


def _same_need_group(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    existing_category = _tokens(str(existing.get("category", "")).split(">")[-1])
    incoming_category = _tokens(str(incoming.get("category", "")).split(">")[-1])
    if not existing_category or existing_category != incoming_category:
        return False

    existing_label = _tokens(str(existing.get("label", "")))
    incoming_label = _tokens(str(incoming.get("label", "")))
    existing_query = _tokens(str(existing.get("product_query", "")))
    incoming_query = _tokens(str(incoming.get("product_query", "")))

    def overlap_coefficient(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / min(len(left), len(right))

    return (
        overlap_coefficient(existing_label, incoming_label) >= 0.75
        or overlap_coefficient(existing_query, incoming_query) >= 0.8
    )


def _merge_mission(
    parsed: ShoppingMissionArguments,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_revision = 0
    if isinstance(previous, dict):
        try:
            previous_revision = max(0, int(previous.get("revision", 0)))
        except (TypeError, ValueError):
            previous_revision = 0
    prior = {} if parsed.reset_mission or not isinstance(previous, dict) else previous
    prior_needs = prior.get("needs") if isinstance(prior.get("needs"), list) else []
    merged_needs: dict[str, dict[str, Any]] = {
        str(need.get("need_id")): dict(need)
        for need in prior_needs
        if isinstance(need, dict) and need.get("need_id")
    }
    for need in parsed.needs:
        incoming = need.model_dump(mode="json")
        existing_id = need.need_id
        if existing_id not in merged_needs:
            existing_id = next(
                (
                    candidate_id
                    for candidate_id, candidate in merged_needs.items()
                    if _same_need_group(candidate, incoming)
                ),
                existing_id,
            )
        prior_need = merged_needs.get(existing_id, {})
        merged_needs[existing_id] = {**prior_need, **incoming, "need_id": existing_id}
    for need_id in parsed.remove_need_ids:
        merged_needs.pop(need_id, None)
    needs = list(merged_needs.values())[:MAX_MISSION_NEEDS]

    def merge_list(key: str, supplied: list[str], clear: bool = False) -> list[str]:
        old = prior.get(key)
        old_values = old if isinstance(old, list) else []
        if clear:
            return _unique_text(supplied)
        return _unique_text([*old_values, *supplied])

    budget = (
        None
        if parsed.clear_total_budget
        else parsed.total_budget_vnd
        if parsed.total_budget_vnd is not None
        else prior.get("total_budget_vnd")
    )
    party_size = (
        None
        if parsed.clear_party_size
        else parsed.party_size
        if parsed.party_size is not None
        else prior.get("party_size")
    )
    rejected = prior.get("rejected_product_ids")
    rejected_ids = [
        str(value)
        for value in (rejected if isinstance(rejected, list) else []) + parsed.rejected_product_ids
        if str(value).strip()
    ]
    return {
        "mission_id": prior.get("mission_id") or uuid4().hex,
        "revision": previous_revision + 1,
        "goal": parsed.goal.strip() or str(prior.get("goal", "")),
        "use_case": parsed.use_case.strip() or str(prior.get("use_case", "")),
        "occasion": parsed.occasion.strip() or str(prior.get("occasion", "")),
        "party_size": party_size,
        "total_budget_vnd": budget,
        "total_budget_exclusive": (
            parsed.total_budget_exclusive
            if parsed.total_budget_vnd is not None
            else bool(prior.get("total_budget_exclusive", False))
        ),
        "hard_requirements": merge_list(
            "hard_requirements", parsed.hard_requirements, parsed.clear_hard_requirements
        ),
        "exclusions": merge_list("exclusions", parsed.exclusions, parsed.clear_exclusions),
        "preferences": merge_list("preferences", parsed.preferences, parsed.clear_preferences),
        "existing_items": merge_list(
            "existing_items", parsed.existing_items, parsed.clear_existing_items
        ),
        "unknown_details": (
            []
            if parsed.clear_unknown_details
            else _unique_text(parsed.unknown_details)
            if "unknown_details" in parsed.model_fields_set
            else _unique_text(
                [str(value) for value in prior.get("unknown_details", []) if isinstance(value, str)]
            )
        ),
        "pending_decision": (
            None
            if parsed.clear_pending_decision
            else parsed.pending_decision or prior.get("pending_decision")
        ),
        "needs": needs,
        "rejected_product_ids": list(dict.fromkeys(rejected_ids))[-24:],
        "proposed_items": [
            dict(item) for item in prior.get("proposed_items", []) if isinstance(item, dict)
        ],
        "unresolved_details": [],
        "last_updated_by": "plan_shopping_mission",
    }


def _preference_score(product: ProductSnapshot, preferences: list[str]) -> float:
    terms = set().union(*(_tokens(value) for value in preferences)) if preferences else set()
    if not terms:
        return 0.0
    searchable = _tokens(
        " ".join(
            filter(
                None,
                (
                    product.name,
                    product.brand or "",
                    product.category,
                    product.category_hierarchy or "",
                    product.description or "",
                ),
            )
        )
    )
    return len(terms & searchable) / len(terms)


def _fallback_query_matches_candidate(query: str, product: ProductSnapshot) -> bool:
    """Do not let a broad category fallback replace a specific requested item."""
    stop_words = {
        "an",
        "ca",
        "cho",
        "do",
        "da",
        "di",
        "duoc",
        "giup",
        "hang",
        "khong",
        "la",
        "mua",
        "nhe",
        "nguoi",
        "nhom",
        "o",
        "phu",
        "se",
        "xem",
        "y",
    }
    # Only compare against the sellable item's name and brand. Matching words
    # from a broad parent category or marketing description can make a related
    # but different product look like a valid substitute (e.g. a sanitary pad
    # whose name happens to contain “bông” for a request for cotton pads).
    candidate_terms = _tokens(" ".join(filter(None, (product.name, product.brand or ""))))

    verified_aliases = catalog_category_query_aliases(query)
    # Once a known synonym is identified, do not also accept a fuzzy match on
    # its original wording; that is how “khăn lau miệng” matched face wipes.
    variants = list(verified_aliases) if verified_aliases else [query]
    for variant in variants:
        query_terms = _tokens(variant) - stop_words - _SCENE_QUERY_WORDS
        if not query_terms:
            return True
        overlap = query_terms & candidate_terms
        # Product-type queries are deliberately short. Requiring all
        # significant terms for queries of up to four words avoids accepting
        # fuzzy one-word matches. Catalog synonyms may use a different but
        # equally specific wording, so each verified variant gets its own try.
        if len(query_terms) <= 4 and query_terms <= candidate_terms:
            return True
        if len(query_terms) > 4 and len(overlap) >= 2 and len(overlap) / len(query_terms) >= 0.8:
            return True
    return False


def _specific_skincare_need_type(need: dict[str, Any]) -> str | None:
    query = normalize_category_text(
        " ".join(str(need.get(key) or "") for key in ("label", "product_query"))
    )
    if any(
        phrase in query
        for phrase in (
            "kem chong nang",
            "chong nang",
            "sunscreen",
            "sun screen",
            "sunblock",
        )
    ):
        return "sunscreen"
    if any(
        phrase in query
        for phrase in (
            "kem duong am",
            "duong am da mat",
            "kem duong da mat",
            "facial moisturizer",
            "face moisturizer",
        )
    ):
        return "moisturizer"
    if any(
        phrase in query
        for phrase in (
            "sua rua mat",
            "lam sach da mat",
            "facial cleanser",
            "face wash",
        )
    ):
        return "cleanser"
    return None


def _candidate_matches_skincare_need(need: dict[str, Any], product: ProductSnapshot) -> bool:
    """Reject near-matches from a different skincare product type."""
    requested_type = _specific_skincare_need_type(need)
    if requested_type is None:
        return True
    product_terms = normalize_category_text(
        " ".join(
            str(value or "")
            for value in (product.name, product.category, product.category_hierarchy)
        )
    )
    product_is_sunscreen = any(
        phrase in product_terms
        for phrase in ("kem chong nang", "chong nang", "sunscreen", "sun screen", "sunblock")
    ) or bool(re.search(r"\bspf\b", product_terms))
    product_is_cleanser = any(
        phrase in product_terms
        for phrase in ("sua rua mat", "facial cleanser", "face wash")
    )
    if requested_type == "moisturizer" and product_is_sunscreen:
        return False
    if requested_type == "cleanser" and product_is_sunscreen:
        return False
    if requested_type == "sunscreen" and product_is_cleanser:
        return False
    return True


def _need_query_is_product_specific(
    need: dict[str, Any],
    mission: dict[str, Any],
    query: str,
    repository: ProductRepository,
) -> bool:
    """Keep named-item queries narrow while allowing scene wording to use its category."""
    query = query.strip()
    if not query:
        return False
    if need.get("brand") or re.search(r"\b(?:sku|barcode|item\s*no|id)\b", query, re.I):
        return True
    if re.search(r"\d", query):
        return True
    if catalog_category_query_aliases(query):
        return True

    # Decide specificity from the query and its resolved taxonomy leaf. Do not
    # use the LLM's paraphrased goal or rationale here: those often repeat the
    # exact requested product words and can hide a category mismatch.
    broad_need_terms = {
        "an",
        "ban",
        "bao",
        "bim",
        "bimbim",
        "bua",
        "ca",
        "co",
        "chia",
        "cho",
        "da",
        "do",
        "de",
        "dung",
        "duoc",
        "em",
        "face",
        "for",
        "giai",
        "group",
        "guests",
        "hop",
        "khoang",
        "khat",
        "lai",
        "mat",
        "mon",
        "nhau",
        "nhe",
        "nhom",
        "ngay",
        "ngoai",
        "nguoi",
        "product",
        "products",
        "party",
        "people",
        "pham",
        "phu",
        "rai",
        "san",
        "se",
        "share",
        "sharing",
        "skin",
        "skincare",
        "snack",
        "snacks",
        "sua",
        "suat",
        "thuc",
        "tuoi",
        "uong",
        "vat",
        "ve",
        "viewing",
        "watching",
        "xem",
        "duong",
        "kho",
        "nhay",
        "cam",
        "mun",
        "dau",
        "toc",
    }
    query_terms = _tokens(query) - broad_need_terms
    category_terms = _tokens(str(need.get("resolved_category") or need.get("category") or ""))
    return bool(query_terms - category_terms)


def _product_identity(product: ProductSnapshot) -> tuple[str, str, str]:
    """Collapse exact duplicate catalog rows without merging different pack sizes."""

    def canonical(value: str | None) -> str:
        decomposed = unicodedata.normalize("NFD", (value or "").casefold().replace("đ", "d"))
        unaccented = "".join(
            character for character in decomposed if unicodedata.category(character) != "Mn"
        )
        return " ".join(re.findall(r"[a-z0-9]+", unaccented))

    return canonical(product.brand), canonical(product.name), canonical(product.uom)


def _deduplicate_products(products: list[ProductSnapshot]) -> list[ProductSnapshot]:
    selected: dict[tuple[str, str, str], ProductSnapshot] = {}
    for product in products:
        identity = _product_identity(product)
        current = selected.get(identity)
        if current is None or (product.price_vnd, product.product_id) < (
            current.price_vnd,
            current.product_id,
        ):
            selected[identity] = product
    return list(selected.values())


def _pack_size(product: ProductSnapshot) -> tuple[int, str] | None:
    """Read only a clearly stated package volume or weight from the SKU name."""
    name = product.name.casefold().replace(",", ".")
    volume = re.search(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(ml|l[ií]t|liter|litre|l)\b", name
    )
    if volume:
        amount = float(volume.group(1))
        unit = volume.group(2)
        millilitres = round(amount) if unit == "ml" else round(amount * 1000)
        if millilitres > 0:
            return millilitres, "ml"

    weight = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(kg|g|gr|gram(?:s)?)\b", name)
    if weight:
        amount = float(weight.group(1))
        grams = round(amount * 1000) if weight.group(2) == "kg" else round(amount)
        if grams > 0:
            return grams, "g"

    unit = normalize_category_text(product.uom or "")
    if unit in {"kg", "kilogram", "kilograms"}:
        return 1000, "g"
    if unit in {"l", "lit", "liter", "litre", "liters", "litres"}:
        return 1000, "ml"
    if unit in {"g", "gr", "gram", "grams"}:
        return 1, "g"
    if unit in {"ml", "milliliter", "millilitre"}:
        return 1, "ml"
    return None


def _quantity_for_product(
    need: dict[str, Any], mission: dict[str, Any], product: ProductSnapshot
) -> tuple[int, str, dict[str, int | str] | None]:
    requested = max(1, int(need.get("quantity", 1)))
    if need.get("quantity_is_explicit"):
        return requested, "customer_specified", None

    party_size = mission.get("party_size")
    if not isinstance(party_size, int) or party_size < 1:
        return requested, "need_default", None

    category_text = " ".join(
        str(value or "")
        for value in (
            need.get("label"),
            need.get("category"),
            need.get("resolved_category"),
            product.category,
            product.category_hierarchy,
        )
    )
    tokens = _tokens(category_text)
    is_beverage = (
        {"do", "uong"} <= tokens
        or {"nuoc", "uong"} <= tokens
        or bool(tokens & {"drink", "drinks", "beverage", "beverages"})
    )
    is_shareable_snack = (
        "snack" in tokens
        or "chips" in tokens
        or "crisps" in tokens
        or "bim" in tokens
        or "bimbim" in tokens
        or {"banh", "keo"} <= tokens
        or {"do", "an", "vat"} <= tokens
        or {"do", "an", "nhe"} <= tokens
    )
    is_shareable_fruit = {"trai", "cay"} <= tokens or bool(tokens & {"fruit", "fruits"})
    if not (is_beverage or is_shareable_snack or is_shareable_fruit):
        return requested, "need_default", None

    package = _pack_size(product)
    expected_unit = "ml" if is_beverage else "g"
    if package is None or package[1] != expected_unit:
        return requested, "catalog_size_unknown", None

    package_size, measurement = package
    if is_beverage:
        target_per_person = BEVERAGE_SERVING_ML_PER_PERSON
    elif is_shareable_fruit:
        target_per_person = 100
    else:
        target_per_person = SHAREABLE_SNACK_GRAMS_PER_PERSON
    raw_quantity = ceil(party_size * target_per_person / package_size)
    quantity = max(requested, min(MAX_PLANNED_ITEM_QUANTITY, raw_quantity))
    return (
        quantity,
        "party_size_estimate",
        {
            "party_size": party_size,
            "target_per_person": target_per_person,
            "measurement": measurement,
            "package_size": package_size,
        },
    )


def _item_payload(
    need: dict[str, Any],
    product: ProductSnapshot,
    mission: dict[str, Any],
    *,
    inventory_verified: bool,
    data_source: str,
) -> dict[str, Any]:
    quantity, quantity_basis, quantity_estimate = _quantity_for_product(need, mission, product)
    return {
        "need_id": need["need_id"],
        "need_label": need["label"],
        "rationale": need.get("rationale", ""),
        "category": need.get("resolved_category") or need["category"],
        "source": need.get("source", "INFERRED"),
        "required": bool(need.get("required", False)),
        "product_id": product.product_id,
        "name": product.name,
        "brand": product.brand,
        "quantity": quantity,
        "quantity_basis": quantity_basis,
        "quantity_estimate": quantity_estimate,
        "quantity_needs_review": quantity_basis == "catalog_size_unknown",
        "unit_price_vnd": product.price_vnd,
        "line_total_vnd": product.price_vnd * quantity,
        "uom": product.uom,
        "stock_is_current": inventory_verified,
        "data_timestamp": product.data_timestamp.isoformat(),
        "data_source": data_source,
        "budget_share_vnd": product.price_vnd * quantity,
    }


def _choose_options(
    groups: list[tuple[dict[str, Any], list[ProductSnapshot]]],
    mission: dict[str, Any],
    previous_items: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    # Missing one required category should not hide useful, catalog-backed
    # matches for the other categories. Keep the unavailable need visible in
    # the response, but allow an explicitly partial plan when other groups
    # have verified candidates.
    if not groups:
        return [], None
    available_required_count = sum(
        bool(need.get("required")) and bool(candidates) for need, candidates in groups
    )

    alternatives: list[list[ProductSnapshot | None]] = [
        candidates
        if need.get("required") and candidates
        else [None]
        if need.get("required")
        else [None, *candidates]
        for need, candidates in groups
    ]
    preferences = list(mission.get("preferences", []))
    choice_metrics: dict[tuple[str, str], tuple[tuple[str, str, str], int, float]] = {}
    for need, candidates in groups:
        need_id = str(need.get("need_id"))
        need_preferences = preferences + list(need.get("preference_terms", []))
        for item in candidates:
            quantity = _quantity_for_product(need, mission, item)[0]
            choice_metrics[(need_id, item.product_id)] = (
                _product_identity(item),
                quantity * item.price_vnd,
                _preference_score(item, need_preferences),
            )
    prior_product_by_need = {
        str(item.get("need_id")): str(item.get("product_id"))
        for item in (previous_items or [])
        if isinstance(item, dict) and item.get("need_id") and item.get("product_id")
    }
    combos: list[tuple[int, float, int, tuple[tuple[dict[str, Any], ProductSnapshot], ...]]] = []
    budget = mission.get("total_budget_vnd")
    exclusive = bool(mission.get("total_budget_exclusive"))
    for combination in cartesian_product(*alternatives):
        chosen: list[tuple[dict[str, Any], ProductSnapshot]] = []
        used_products: set[tuple[str, str, str]] = set()
        for (need, _candidates), item in zip(groups, combination, strict=True):
            if item is None:
                continue
            identity = choice_metrics[(str(need.get("need_id")), item.product_id)][0]
            if identity in used_products:
                # A duplicate catalog row cannot stand in for two separate needs.
                chosen = []
                break
            used_products.add(identity)
            chosen.append((need, item))
        selected_required_count = sum(bool(need.get("required")) for need, _item in chosen)
        if not chosen or selected_required_count < available_required_count:
            continue
        total = sum(
            choice_metrics[(str(need.get("need_id")), item.product_id)][1]
            for need, item in chosen
        )
        if budget is not None and not (total < budget if exclusive else total <= budget):
            continue
        preference = sum(
            choice_metrics[(str(need.get("need_id")), item.product_id)][2]
            for need, item in chosen
        )
        preserved_count = sum(
            prior_product_by_need.get(str(need.get("need_id"))) == item.product_id
            for need, item in chosen
        )
        combos.append((total, preference, preserved_count, tuple(chosen)))

    if not combos:
        min_total = None
        if all(not need.get("required") or candidates for need, candidates in groups):
            cheapest_required = []
            for need, candidates in groups:
                if not need.get("required") or not candidates:
                    continue
                cheapest_item = min(
                    candidates,
                    key=lambda item: (
                        _quantity_for_product(need, mission, item)[0] * item.price_vnd,
                        item.product_id,
                    ),
                )
                quantity = _quantity_for_product(need, mission, cheapest_item)[0]
                cheapest_required.append((quantity, cheapest_item))
            if cheapest_required:
                min_total = sum(quantity * item.price_vnd for quantity, item in cheapest_required)
        return [], min_total

    best_coverage = max(len(chosen) for _total, _score, _preserved, chosen in combos)
    coverage_feasible = [row for row in combos if len(row[3]) == best_coverage]
    best_preserved = max(row[2] for row in coverage_feasible)
    feasible = [row for row in coverage_feasible if row[2] == best_preserved]
    cheapest = min(
        feasible,
        key=lambda row: (row[0], -row[1], tuple(x[1].product_id for x in row[3])),
    )
    selected = [cheapest]

    if len(selected) < MAX_PLAN_OPTIONS:
        if budget is not None:
            target = budget * 0.75
            balanced = min(
                feasible,
                key=lambda row: (
                    abs(target - row[0]),
                    -row[1],
                    tuple(x[1].product_id for x in row[3]),
                ),
            )
        else:
            balanced = max(
                feasible,
                key=lambda row: (row[1], -row[0], tuple(x[1].product_id for x in row[3])),
            )
        if balanced not in selected:
            selected.append(balanced)

    if len(selected) < MAX_PLAN_OPTIONS:
        selected_ids = {item.product_id for row in selected for _need, item in row[3]}
        diverse = next(
            (
                row
                for row in sorted(feasible, key=lambda item: (-item[1], item[0]))
                if any(item.product_id not in selected_ids for _need, item in row[3])
            ),
            None,
        )
        if diverse is not None:
            selected.append(diverse)

    options = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for index, (total, _score, _preserved, chosen) in enumerate(selected):
        identity = tuple((need["need_id"], item.product_id) for need, item in chosen)
        if identity in seen:
            continue
        seen.add(identity)
        label = ("Tiết kiệm", "Cân bằng", "Lựa chọn khác")[min(index, 2)]
        items = [
            _item_payload(
                need,
                item,
                mission,
                inventory_verified=bool(mission.get("inventory_verified", False)),
                data_source=str(mission.get("data_source", "unknown")),
            )
            for need, item in chosen
        ]
        options.append(
            {
                "option_id": f"option-{len(options) + 1}",
                "label": label,
                "items": items,
                "covered_need_ids": [need["need_id"] for need, _item in chosen],
                "total_vnd": total,
                "remaining_budget_vnd": max(0, int(budget) - total) if budget else None,
                "budget_fit": True,
            }
        )
    return options, None


def build_shopping_mission_plan(
    raw_arguments: dict[str, Any],
    repository: ProductRepository,
    previous_mission: dict[str, Any] | None = None,
) -> ShoppingMissionResult:
    """Resolve model-decomposed needs into catalog-backed, budget-checked plans.

    The model may interpret a customer's goal and propose need groups. This
    function owns catalog retrieval, hard filters, SKU provenance, quantity
    arithmetic, budget compliance, and the proposed (non-cart) plan.
    """
    parsed = ShoppingMissionArguments.model_validate(raw_arguments)
    mission = _merge_mission(parsed, previous_mission)
    for key in ("hard_requirements", "exclusions"):
        mission[key] = [
            value
            for value in mission.get(key, [])
            if not _is_structured_mission_metadata(str(value), mission)
        ]
    for need in mission["needs"]:
        constraints = need.get("unverified_constraints", [])
        need["unverified_constraints"] = [
            value
            for value in constraints
            if not _is_structured_mission_metadata(str(value), mission)
        ]
    if not mission["goal"]:
        raise ValueError("A shopping mission needs a customer goal")
    if not mission["needs"]:
        raise ValueError("A shopping mission needs at least one shopping need")

    inventory_verified = bool(getattr(repository, "supports_current_stock", False))
    mission["inventory_verified"] = inventory_verified
    mission["data_source"] = repository.source_name
    supports_spicy = bool(getattr(repository, "supports_spicy_filter", True))
    rejected = {value.casefold() for value in mission["rejected_product_ids"]}
    unknown_global_constraints = [
        *mission["hard_requirements"],
        *mission["exclusions"],
    ]
    groups: list[tuple[dict[str, Any], list[ProductSnapshot]]] = []
    need_results: list[dict[str, Any]] = []
    satisfied_needs: list[str] = []
    all_products: dict[str, ProductSnapshot] = {}
    alternative_products: dict[str, ProductSnapshot] = {}
    unresolved_details = list(mission.get("unresolved_details", []))
    quantity_measurements: set[str] = set()
    quantity_estimation_targets: set[tuple[int, str]] = set()
    quantity_size_unknown = False

    for need in mission["needs"]:
        primary_query = str(need.get("product_query", "")).strip()
        category = _resolve_need_category(
            str(need.get("category", "")),
            repository,
            semantic_query=primary_query or str(need.get("label", "")),
        )
        need["resolved_category"] = category
        if need.get("satisfied_by_existing"):
            satisfied_needs.append(str(need["label"]))
            need_results.append(
                {
                    "need_id": need["need_id"],
                    "label": need["label"],
                    "rationale": need.get("rationale", ""),
                    "requested_category": need["category"],
                    "resolved_category": category,
                    "source": need.get("source", "INFERRED"),
                    "required": bool(need.get("required", False)),
                    "quantity": int(need.get("quantity", 1)),
                    "candidate_count": 0,
                    "candidate_product_ids": [],
                    "alternative_categories": [],
                    "blocked_reasons": [],
                    "satisfied_by_existing": True,
                }
            )
            continue

        blockers: list[str] = []
        if not category:
            blockers.append("category_not_resolved_in_catalog")
        if need.get("spicy") is not None and not supports_spicy:
            blockers.append("spice_attribute_not_available")
        preference_tokens = (
            set().union(*(_tokens(str(value)) for value in need.get("preference_terms", [])))
            if need.get("preference_terms")
            else set()
        )
        if not supports_spicy and {"khong", "cay"} <= preference_tokens:
            unresolved_details.append("preference_unverified:spicy")
        blockers.extend(
            f"unverified:{value}"
            for value in need.get("unverified_constraints", [])
            if str(value).strip()
        )
        if unknown_global_constraints:
            blockers.extend(f"unverified:{value}" for value in unknown_global_constraints)

        candidates: list[ProductSnapshot] = []
        if category and not blockers:
            category_leaf_query = str(need.get("category", "")).split(">")[-1].strip()
            queries = list(dict.fromkeys((primary_query, category_leaf_query, "")))
            for query in queries:
                candidates = repository.search(
                    query=query,
                    category=category,
                    brand=need.get("brand"),
                    max_price_vnd=need.get("max_price_vnd"),
                    spicy=need.get("spicy"),
                    in_stock=inventory_verified,
                    limit=MAX_NEED_CANDIDATES,
                )
                if (
                    candidates
                    and primary_query
                    and _need_query_is_product_specific(need, mission, primary_query, repository)
                ):
                    candidates = [
                        item
                        for item in candidates
                        if _fallback_query_matches_candidate(primary_query, item)
                    ]
                candidates = [
                    item
                    for item in candidates
                    if _candidate_matches_skincare_need(need, item)
                ]
                if candidates:
                    need["retrieval_query_used"] = query
                    break
            candidates = [
                item
                for item in candidates
                if item.product_id.casefold() not in rejected
                and (
                    not inventory_verified
                    or item.sellable_stock >= _quantity_for_product(need, mission, item)[0]
                )
            ]
            candidates.sort(
                key=lambda item: (
                    -_preference_score(
                        item,
                        mission["preferences"] + list(need.get("preference_terms", [])),
                    ),
                    item.price_vnd,
                    item.product_id,
                )
            )
            candidates = _deduplicate_products(candidates)

        category_alternatives: list[dict[str, Any]] = []
        if not candidates and not blockers:
            for alternative in need.get("alternative_categories", []):
                resolved_alternative = _resolve_need_category(str(alternative), repository)
                if not resolved_alternative or resolved_alternative == category:
                    continue
                alternative_items = repository.search(
                    query="",
                    category=resolved_alternative,
                    brand=need.get("brand"),
                    max_price_vnd=need.get("max_price_vnd"),
                    spicy=need.get("spicy"),
                    in_stock=inventory_verified,
                    limit=MAX_NEED_CANDIDATES,
                )
                alternative_items = _deduplicate_products(
                    [
                        item
                        for item in alternative_items
                        if item.product_id.casefold() not in rejected
                        and (
                            not inventory_verified
                            or item.sellable_stock >= _quantity_for_product(need, mission, item)[0]
                        )
                    ]
                )
                if alternative_items:
                    category_alternatives.append(
                        {
                            "category": resolved_alternative,
                            "products": [
                                {
                                    "product_id": item.product_id,
                                    "name": item.name,
                                    "brand": item.brand,
                                    "price_vnd": item.price_vnd,
                                    "stock_is_current": inventory_verified,
                                    "data_timestamp": item.data_timestamp.isoformat(),
                                    "data_source": repository.source_name,
                                }
                                for item in alternative_items[:3]
                            ],
                        }
                    )
                    alternative_products.update(
                        {item.product_id: item for item in alternative_items[:3]}
                    )
                    break

        # A global requirement the catalog cannot prove blocks the plan. A
        # customer may remove it, provide a supported attribute, or ask for
        # clearly conditional candidates in a follow-up.
        need["resolved_category"] = category
        groups.append((need, candidates))
        for item in candidates:
            all_products[item.product_id] = item
        if not candidates:
            unresolved_details.extend(blockers or [f"no_candidate_for:{need['need_id']}"])
        for candidate in candidates:
            _quantity, quantity_basis, estimate = _quantity_for_product(
                need, mission, candidate
            )
            if estimate:
                quantity_measurements.add(str(estimate["measurement"]))
                quantity_estimation_targets.add(
                    (int(estimate["target_per_person"]), str(estimate["measurement"]))
                )
            elif quantity_basis == "catalog_size_unknown":
                quantity_size_unknown = True
        need_results.append(
            {
                "need_id": need["need_id"],
                "label": need["label"],
                "rationale": need.get("rationale", ""),
                "requested_category": need["category"],
                "resolved_category": category,
                "source": need.get("source", "INFERRED"),
                "required": bool(need.get("required", False)),
                "quantity": int(need.get("quantity", 1)),
                "quantity_is_explicit": bool(need.get("quantity_is_explicit", False)),
                "candidate_count": len(candidates),
                "candidate_product_ids": [item.product_id for item in candidates[:5]],
                "alternative_categories": category_alternatives,
                "blocked_reasons": blockers,
                "satisfied_by_existing": False,
            }
        )

    options, minimum_required_total = _choose_options(
        groups,
        mission,
        previous_items=mission.get("proposed_items", []),
    )
    options_products: dict[str, ProductSnapshot] = {}
    for option in options:
        for item in option["items"]:
            product = all_products.get(item["product_id"])
            if product is not None:
                options_products[product.product_id] = product

    missing_required = [
        need["label"] for need, candidates in groups if need.get("required") and not candidates
    ]
    missing_optional = [
        need["label"] for need, candidates in groups if not need.get("required") and not candidates
    ]
    covered_by_first_option = set(options[0]["covered_need_ids"]) if options else set()
    optional_not_selected = [
        need["label"]
        for need, candidates in groups
        if not need.get("required")
        and candidates
        and need["need_id"] not in covered_by_first_option
    ]
    budget = mission.get("total_budget_vnd")
    shortfall = None
    if minimum_required_total is not None and budget is not None:
        exclusive_adjustment = int(
            bool(mission.get("total_budget_exclusive")) and minimum_required_total >= int(budget)
        )
        shortfall = max(
            0,
            minimum_required_total - int(budget) + exclusive_adjustment,
        )
    mission["unresolved_details"] = list(dict.fromkeys(unresolved_details))[:24]
    mission["confirmed_needs"] = [
        need["label"] for need in mission["needs"] if need.get("source") == "EXPLICIT"
    ]
    mission["inferred_needs"] = [
        need["label"] for need in mission["needs"] if need.get("source") == "INFERRED"
    ]
    pending_decision = mission.get("pending_decision")
    if pending_decision == "choose_a_plan_option" and len(options) <= 1:
        pending_decision = None
    if not pending_decision and len(options) > 1:
        pending_decision = "choose_a_plan_option"
    mission["pending_decision"] = pending_decision
    mission["satisfied_needs"] = satisfied_needs
    mission["proposed_items"] = [dict(item) for item in options[0]["items"]] if options else []
    mission["inventory_verified"] = inventory_verified
    mission["data_source"] = repository.source_name
    products_for_response = dict(alternative_products)
    products_for_response.update(options_products)
    latest_product_timestamp = max(
        (item.data_timestamp for item in products_for_response.values()),
        default=None,
    )
    mission["data_timestamp"] = (
        latest_product_timestamp.isoformat() if latest_product_timestamp else None
    )

    content = {
        "plan_type": "needs_driven_multi_product",
        "mission": mission,
        "mission_id": mission["mission_id"],
        "revision": mission["revision"],
        "goal": mission["goal"],
        "use_case": mission["use_case"],
        "occasion": mission["occasion"],
        "party_size": mission["party_size"],
        "needs": need_results,
        "satisfied_needs": satisfied_needs,
        "options": options,
        "confirmed_needs": mission["confirmed_needs"],
        "inferred_needs": mission["inferred_needs"],
        "missing_required_needs": missing_required,
        "missing_optional_needs": missing_optional,
        "optional_needs_not_selected": optional_not_selected,
        "budget_shortfall_vnd": shortfall,
        "budget_vnd": budget,
        "budget_exclusive": bool(mission.get("total_budget_exclusive")),
        "pending_decision": mission["pending_decision"],
        "inventory_verified": inventory_verified,
        "quantity_estimation_measurements": sorted(quantity_measurements),
        "quantity_estimation_targets": [
            {"target_per_person": target, "measurement": measurement}
            for target, measurement in sorted(quantity_estimation_targets)
        ],
        "quantity_size_unknown": quantity_size_unknown,
        "data_source": repository.source_name,
        "data_timestamp": mission["data_timestamp"],
        "cart_mutated": False,
        "unresolved_details": mission["unresolved_details"],
        "unknown_details": mission["unknown_details"],
        "all_products_exist_in_catalog": all(
            item["product_id"] in all_products for option in options for item in option["items"]
        ),
        "alternative_products_exist_in_catalog": all(
            item_id in alternative_products for item_id in alternative_products
        ),
    }
    return ShoppingMissionResult(
        content=content,
        products=tuple(products_for_response.values()),
        mission_state=mission,
    )


def render_shopping_plan_summary(
    content: dict[str, Any],
    language: str = "vi",
    *,
    previous_mission: dict[str, Any] | None = None,
) -> str:
    """Render a concise grounded summary; all prices/totals come from the planner."""
    options = content.get("options") if isinstance(content.get("options"), list) else []
    missing = content.get("missing_required_needs") or []
    needs = content.get("needs") if isinstance(content.get("needs"), list) else []
    labels_by_need_id = {
        str(need.get("need_id")): str(need.get("label") or need.get("need_id"))
        for need in needs
        if isinstance(need, dict) and need.get("need_id")
    }
    missing_labels = [labels_by_need_id.get(str(need), str(need)) for need in missing]
    missing_optional = content.get("missing_optional_needs") or []
    missing_optional_labels = [
        labels_by_need_id.get(str(need), str(need)) for need in missing_optional
    ]
    satisfied = content.get("satisfied_needs") or []
    shortfall = content.get("budget_shortfall_vnd")
    mission = content.get("mission") if isinstance(content.get("mission"), dict) else {}
    inventory_verified = bool(content.get("inventory_verified"))
    spicy_preference_unverified = "preference_unverified:spicy" in (
        content.get("unresolved_details") or []
    )
    try:
        follow_up = int(mission.get("revision", 1)) > 1
    except (TypeError, ValueError):
        follow_up = False

    def format_vnd(amount: Any) -> str:
        value = int(amount or 0)
        if language == "en":
            return f"{value:,} VND"
        if language == "zh":
            return f"{value:,} 越南盾"
        return f"{value:,}".replace(",", ".") + "đ"

    def catalog_freshness_note(language: str) -> str:
        raw_timestamp = content.get("data_timestamp")
        snapshot_date = None
        if isinstance(raw_timestamp, str):
            try:
                snapshot_date = datetime.fromisoformat(
                    raw_timestamp.replace("Z", "+00:00")
                ).strftime("%d/%m/%Y")
            except ValueError:
                snapshot_date = None
        if language == "en":
            date_note = f"Catalog data is dated {snapshot_date}; " if snapshot_date else ""
            return date_note + "store stock has not been verified in real time. "
        if language == "zh":
            date_note = f"目录数据日期为{snapshot_date}；" if snapshot_date else ""
            return date_note + "门店库存尚未实时核实。"
        date_note = f"Dữ liệu catalog ghi ngày {snapshot_date}; " if snapshot_date else ""
        return date_note + "tồn kho tại cửa hàng chưa được xác nhận theo thời gian thực. "

    def skincare_party_size_note() -> str:
        if follow_up:
            return ""
        try:
            party_size = int(mission.get("party_size") or 0)
        except (TypeError, ValueError):
            party_size = 0
        if language != "vi" or party_size <= 1:
            return ""
        mission_needs = mission.get("needs") if isinstance(mission.get("needs"), list) else []
        is_skincare_plan = any(
            "cham soc da" in _normalize_category_words(need)
            or "da mat" in _normalize_category_words(need)
            for need in mission_needs
            if isinstance(need, dict)
        )
        if not is_skincare_plan:
            return ""
        return (
            " Mỗi mặt hàng hiện tính 1 đơn vị; mình chưa thể kết luận lượng này đủ cho "
            f"{party_size} người nếu chưa biết dùng chung hay mỗi người dùng riêng. "
            f"{party_size} người sẽ dùng chung sản phẩm hay mỗi người cần một bộ riêng?"
        )

    def _normalize_category_words(need: dict[str, Any]) -> str:
        value = " ".join(
            str(need.get(key) or "") for key in ("label", "category", "product_query")
        )
        decomposed = unicodedata.normalize("NFD", value.casefold())
        unaccented = "".join(
            char for char in decomposed if unicodedata.category(char) != "Mn"
        ).replace("đ", "d")
        return re.sub(r"[^\w\s]", " ", unaccented)

    def quantity_guidance(
        items: list[dict[str, Any]] | None = None, *, use_catalog_candidates: bool = False
    ) -> str:
        rows = items or []
        size_unknown_names = [
            str(item.get("name") or "")
            for item in rows
            if item.get("quantity_needs_review") and item.get("name")
        ]
        measurements = {
            str(estimate.get("measurement"))
            for item in rows
            if isinstance((estimate := item.get("quantity_estimate")), dict)
        }
        targets = {
            (int(estimate.get("target_per_person", 0)), str(estimate.get("measurement")))
            for item in rows
            if isinstance((estimate := item.get("quantity_estimate")), dict)
        }
        size_unknown = any(item.get("quantity_needs_review") for item in rows)
        if use_catalog_candidates:
            measurements.update(
                str(value) for value in content.get("quantity_estimation_measurements", [])
            )
            targets.update(
                (
                    int(estimate.get("target_per_person", 0)),
                    str(estimate.get("measurement")),
                )
                for estimate in content.get("quantity_estimation_targets", [])
                if isinstance(estimate, dict)
            )
            size_unknown = size_unknown or bool(content.get("quantity_size_unknown"))

        notes = []
        if (BEVERAGE_SERVING_ML_PER_PERSON, "ml") in targets:
            notes.append("250 ml đồ uống/người")
        if (SHAREABLE_SNACK_GRAMS_PER_PERSON, "g") in targets:
            notes.append("30 g đồ ăn nhẹ/người")
        if (100, "g") in targets:
            notes.append("100 g trái cây/người")
        if language == "en":
            text = (
                " Counts are estimates ("
                + "; ".join(notes)
                + ") based on catalog pack sizes."
                if notes
                else ""
            )
            if size_unknown:
                if size_unknown_names:
                    text += (
                        " The catalog has no pack size recorded for "
                        + ", ".join(size_unknown_names)
                        + "; I kept the original quantity, so please confirm it."
                    )
                else:
                    text += " The catalog omits some pack sizes, so please confirm the quantities."
            return text
        if language == "zh":
            text = "数量按目录包装规格估算（" + "；".join(notes) + "）。" if notes else ""
            if size_unknown:
                if size_unknown_names:
                    text += (
                        "目录没有记录"
                        + "、".join(size_unknown_names)
                        + "的包装规格；我保留了原数量，请你确认。"
                    )
                else:
                    text += "目录缺少部分包装规格，请确认这些数量。"
            return text
        text = (
            " Số lượng là ước tính ("
            + "; ".join(notes)
            + "), dựa trên quy cách ghi trong catalog."
            if notes
            else ""
        )
        if size_unknown:
            if size_unknown_names:
                text += (
                    " Catalog chưa ghi quy cách đóng gói của "
                    + ", ".join(size_unknown_names)
                    + "; mình giữ số lượng ban đầu nên bạn xác nhận giúp nhé."
                )
            else:
                text += (
                    " Catalog thiếu quy cách đóng gói cho một số mặt hàng; "
                    "bạn xác nhận số lượng giúp nhé."
                )
        return text

    def describe_option(option: dict[str, Any], language: str) -> tuple[str, str]:
        rows = [item for item in option.get("items", []) if isinstance(item, dict)]
        required_rows = [item for item in rows if item.get("required", True)]
        optional_rows = [item for item in rows if not item.get("required", True)]

        def describe_rows(items: list[dict[str, Any]]) -> str:
            if language == "en":
                return "; ".join(
                    f"{item['name']} × {item['quantity']} ({format_vnd(item['line_total_vnd'])})"
                    for item in items
                )
            if language == "zh":
                return "；".join(
                    f"{item['name']} × {item['quantity']}（{format_vnd(item['line_total_vnd'])}）"
                    for item in items
                )
            return "; ".join(
                f"{item['name']} × {item['quantity']} ({format_vnd(item['line_total_vnd'])})"
                for item in items
            )

        sections: list[str] = []
        if required_rows:
            items = describe_rows(required_rows)
            sections.append(
                f"Based on what you asked for: {items}."
                if language == "en"
                else f"按你的要求：{items}。"
                if language == "zh"
                else f"Theo yêu cầu của bạn: {items}."
            )
        if optional_rows:
            items = describe_rows(optional_rows)
            sections.append(
                f"If useful, you could also add: {items}."
                if language == "en"
                else f"如果你需要，也可以加上：{items}。"
                if language == "zh"
                else f"Nếu muốn mua thêm, bạn có thể chọn: {items}."
            )

        total = int(option.get("total_vnd") or 0)
        optional_total = sum(int(item.get("line_total_vnd") or 0) for item in optional_rows)
        if language == "en":
            total_text = f"Total: {format_vnd(total)}"
            if optional_rows:
                total_text += f" (optional items: {format_vnd(optional_total)})."
            else:
                total_text += "."
        elif language == "zh":
            total_text = f"合计：{format_vnd(total)}"
            if optional_rows:
                total_text += f"（可选商品：{format_vnd(optional_total)}）。"
            else:
                total_text += "。"
        else:
            total_text = f"Tổng: {format_vnd(total)}"
            if optional_rows:
                total_text += f" (phần mua thêm: {format_vnd(optional_total)})."
            else:
                total_text += "."
        return " ".join(sections), total_text

    def render_followup_delta(option: dict[str, Any]) -> str | None:
        """Summarize only what changed since the previous advisory plan."""
        if language != "vi" or not isinstance(previous_mission, dict):
            return None

        previous_items = previous_mission.get("proposed_items")
        if not isinstance(previous_items, list):
            return None
        current_items = [item for item in option.get("items", []) if isinstance(item, dict)]
        previous_by_id = {
            str(item.get("product_id")): item
            for item in previous_items
            if isinstance(item, dict) and item.get("product_id")
        }
        current_by_id = {
            str(item.get("product_id")): item
            for item in current_items
            if item.get("product_id")
        }
        if len(previous_by_id) != len(previous_items):
            return None

        added_items = [
            item for product_id, item in current_by_id.items() if product_id not in previous_by_id
        ]
        removed_items = [
            item for product_id, item in previous_by_id.items() if product_id not in current_by_id
        ]
        changed_quantities = [
            item
            for product_id, item in current_by_id.items()
            if product_id in previous_by_id
            and int(item.get("quantity") or 0)
            != int(previous_by_id[product_id].get("quantity") or 0)
        ]
        current_mission = mission
        previous_needs = previous_mission.get("needs")
        current_needs = current_mission.get("needs")
        if not isinstance(previous_needs, list) or not isinstance(current_needs, list):
            return None
        previous_needs_by_id = {
            str(need.get("need_id")): need
            for need in previous_needs
            if isinstance(need, dict) and need.get("need_id")
        }
        promoted_needs = [
            need
            for need in current_needs
            if isinstance(need, dict)
            and need.get("need_id") in previous_needs_by_id
            and need.get("required")
            and not previous_needs_by_id[str(need.get("need_id"))].get("required")
        ]
        previous_budget = previous_mission.get("total_budget_vnd")
        current_budget = current_mission.get("total_budget_vnd")
        budget_changed = previous_budget != current_budget and current_budget is not None

        parts: list[str] = []
        if added_items:
            rendered = "; ".join(
                f"{item.get('name', 'Mặt hàng trong catalog')} × "
                f"{int(item.get('quantity') or 1)} "
                f"({format_vnd(item.get('line_total_vnd'))})"
                for item in added_items
            )
            parts.append("Mình thêm vào phương án: " + rendered + ".")
        if removed_items:
            parts.append(
                "Mình bỏ khỏi phương án: "
                + "; ".join(str(item.get("name") or "mặt hàng") for item in removed_items)
                + "."
            )
        if changed_quantities:
            parts.append(
                "Mình cập nhật số lượng: "
                + "; ".join(
                    f"{item.get('name', 'Mặt hàng trong catalog')} × "
                    f"{int(item.get('quantity') or 0)}"
                    for item in changed_quantities
                )
                + "."
            )
        if promoted_needs:
            promoted_items = [
                item
                for item in current_items
                if any(
                    str(item.get("need_id")) == str(need.get("need_id"))
                    for need in promoted_needs
                )
            ]
            if promoted_items:
                rendered = "; ".join(
                    f"{item.get('name', 'Mặt hàng trong catalog')} × "
                    f"{int(item.get('quantity') or 1)}"
                    for item in promoted_items
                )
                parts.append("Nhóm vừa được chốt đã có trong phương án: " + rendered + ".")
        if not added_items and not removed_items and not changed_quantities and not promoted_needs:
            parts.append(
                "Mình giữ nguyên các mặt hàng trong phương án."
                if budget_changed
                else "Mình đã cộng lại theo phương án hiện tại."
            )
        if budget_changed:
            parts.append(f"Mức dự trù mới là {format_vnd(current_budget)}.")

        total = int(option.get("total_vnd") or 0)
        total_note = f"Tổng phương án hiện tại: {format_vnd(total)}."
        if current_budget is not None:
            difference = int(current_budget) - total
            if difference >= 0:
                total_note += f" Còn {format_vnd(difference)} trong mức dự trù."
            else:
                total_note += f" Đang vượt mức dự trù {format_vnd(-difference)}."
        parts.append(total_note)
        if missing_labels:
            parts.append(
                "Catalog chưa xác nhận được mặt hàng phù hợp cho nhu cầu bắt buộc: "
                + ", ".join(missing_labels)
                + "; mình chưa tính nhóm này vào tổng."
            )
            current_needs_by_id = {
                str(need.get("need_id")): need
                for need in current_needs
                if isinstance(need, dict) and need.get("need_id")
            }
            newly_missing_broad_skincare = any(
                str(need.get("label") or "") in missing_labels
                and normalize_category_text(str(need.get("label") or ""))
                in {"san pham duong da mat", "san pham cham soc da", "duong da mat"}
                and (
                    str(need.get("need_id")) not in previous_needs_by_id
                    or not previous_needs_by_id[str(need.get("need_id"))].get("required")
                )
                for need in current_needs_by_id.values()
            )
            if newly_missing_broad_skincare:
                parts.append(
                    "Bạn muốn tìm cụ thể loại nào, chẳng hạn kem dưỡng ẩm, "
                    "để mình tra đúng nhóm sản phẩm?"
                )
        elif missing_optional_labels:
            parts.append(
                "Catalog chưa tìm thấy sản phẩm phù hợp cho: "
                + ", ".join(missing_optional_labels)
                + "."
            )
        return " ".join(parts)

    if follow_up and options:
        delta_summary = render_followup_delta(options[0])
        if delta_summary:
            return delta_summary
    if language == "en":
        intro = "" if follow_up else "Here's a draft plan: "
        caveat = catalog_freshness_note("en") if not inventory_verified and not follow_up else ""
        if not options:
            if missing:
                return (
                    intro
                    + caveat
                    + "I couldn't verify suitable catalog matches for "
                    + ", ".join(missing_labels)
                    + ". I haven't substituted a different item."
                )
            if satisfied:
                return (
                    intro
                    + caveat
                    + "You already have what this plan needs, so there is nothing extra to buy."
                )
            if shortfall:
                return (
                    intro
                    + "The minimum required items exceed the budget by "
                    + f"{format_vnd(shortfall)}. "
                    "Which need should I prioritize?"
                    + quantity_guidance(use_catalog_candidates=True)
                )
            return (
                intro + caveat + "I couldn't verify a complete plan from this catalog. "
                "Which requirement can we relax?"
            )
        option = options[0]
        option_details, total_text = describe_option(option, language)
        already_have = (
            f" I skipped {', '.join(map(str, satisfied))} because you already have them."
            if satisfied
            else ""
        )
        return (
            intro
            + caveat
            + option_details
            + " "
            + total_text
            + quantity_guidance(option["items"])
            + already_have
            + (
                " The catalog does not confirm whether these items are spicy."
                if spicy_preference_unverified
                else ""
            )
            + (
                " The catalog couldn't verify a suitable item for the required need: "
                + ", ".join(missing_labels)
                + "; the total above includes only the items I could match."
                if missing_labels
                else ""
            )
            + (" I haven't added anything to the cart." if not follow_up else "")
        )
    if language == "zh":
        intro = "" if follow_up else "先给你一份参考方案："
        caveat = catalog_freshness_note("zh") if not inventory_verified and not follow_up else ""
        if not options:
            if missing:
                return (
                    intro
                    + caveat
                    + "目录中暂时找不到能确认符合这些需求的商品："
                    + "、".join(missing_labels)
                    + "。我没有用别的商品替代。"
                )
            if satisfied:
                return intro + caveat + "你已经有方案所需的物品，不需要额外购买。"
            if shortfall:
                return (
                    intro
                    + f"满足必需需求至少还差 {format_vnd(shortfall)} 预算。你想优先保留哪一项？"
                    + quantity_guidance(use_catalog_candidates=True)
                )
            return intro + caveat + "目前无法从目录核实完整方案。你愿意调整哪个条件？"
        option = options[0]
        option_details, total_text = describe_option(option, language)
        already_have = (
            "我已略过你已有的" + "、".join(map(str, satisfied)) + "。" if satisfied else ""
        )
        return (
            intro
            + caveat
            + option_details
            + total_text
            + quantity_guidance(option["items"])
            + already_have
            + ("目录没有信息确认这些商品是否辣。" if spicy_preference_unverified else "")
            + (
                "目录尚无法核实这些必需需求："
                + "、".join(missing_labels)
                + "；上面的总价只包含已匹配到的商品。"
                if missing_labels
                else ""
            )
            + ("尚未加入购物车。" if not follow_up else "")
        )

    # Follow-ups already begin with the requested-item section or the specific
    # catalog limitation. Dropping the repeated canned lead-in sounds more
    # conversational and keeps the answer focused on the actual change.
    intro = "" if follow_up else "Mình gợi ý một phương án tham khảo: "
    caveat = catalog_freshness_note("vi") if not inventory_verified and not follow_up else ""
    if not options:
        if missing:
            return (
                intro
                + caveat
                + "Catalog hiện chưa giúp mình xác nhận được mặt hàng phù hợp cho: "
                + ", ".join(missing_labels)
                + ". Mình chưa tự chọn món khác để thay thế."
            )
        if satisfied:
            return intro + caveat + "Bạn đã có đủ các nhóm cần thiết nên chưa cần mua thêm món nào."
        if shortfall:
            return (
                intro + f"Các nhu cầu bắt buộc rẻ nhất vẫn vượt ngân sách {format_vnd(shortfall)}. "
                "Bạn muốn ưu tiên nhu cầu nào?"
                + quantity_guidance(use_catalog_candidates=True)
            )
        details = content.get("unresolved_details") or []
        if details:
            return (
                intro
                + "Mình chưa thể xác nhận các điều kiện: "
                + ", ".join(map(str, details[:3]))
                + ". Bạn muốn nới điều kiện nào?"
            )
        return (
            intro + caveat + "Mình chưa xác minh được phương án trọn vẹn trong catalog. "
            "Bạn muốn đổi điều kiện nào?"
        )
    option = options[0]
    option_details, total_text = describe_option(option, language)
    suffix = " Mình chưa thêm món nào vào giỏ." if not follow_up else ""
    if satisfied:
        suffix = " Mình bỏ qua " + ", ".join(map(str, satisfied)) + " vì bạn đã có rồi." + suffix
    if missing_optional_labels:
        suffix += " Chưa tìm thấy trong catalog: " + ", ".join(missing_optional_labels) + "."
    if missing_labels:
        suffix += (
            " Catalog chưa xác nhận được mặt hàng phù hợp cho nhu cầu bắt buộc: "
            + ", ".join(missing_labels)
            + "; tổng trên chỉ tính các món đã tìm thấy."
        )
    if spicy_preference_unverified:
        suffix += " Catalog chưa có thuộc tính vị cay nên mình chưa xác minh được món không cay."
    return (
        intro
        + caveat
        + option_details
        + " "
        + total_text
        + (quantity_guidance(option["items"]) if not follow_up else "")
        + skincare_party_size_note()
        + suffix
    )
