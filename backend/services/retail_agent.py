from __future__ import annotations

import asyncio
import copy
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.products.catalog import PRODUCT_ALIASES, ProductSnapshot
from backend.services.groq import GroqProviderError, GroqService
from backend.services.retail_tools import (
    ToolObservation,
    execute_catalog_tool,
    execute_retail_information_tool,
    tools_for_state,
)
from backend.services.vietnamese_intent import (
    explicit_recommendation_categories as _parse_explicit_recommendation_categories,
)
from backend.services.vietnamese_intent import (
    explicit_recommendation_category as _parse_recommendation_category,
)
from backend.services.vietnamese_intent import (
    is_alternative_product_request,
)
from backend.shared.contracts import SessionState

MAX_AGENT_TOOL_ROUNDS = 2
MAX_AGENT_TOOL_CALLS = 5
CATALOG_TOOL_TIMEOUT_SECONDS = 2.0
CATALOG_RECOMMENDATION_TIMEOUT_SECONDS = 5.0
SHOPPING_MISSION_TOOL_TIMEOUT_SECONDS = 15.0
TOOL_TIMEOUT_RETRIES = 1
_OPEN_ENDED_NEED_DISCOVERY_CUE = re.compile(
    r"\b(?:chua biet can (?:nhung )?nhom (?:gi|nao)|"
    r"chua ro can (?:nhung )?nhom (?:gi|nao)|"
    r"chua biet can gi|chua biet nen mua gi|chua biet ten (?:mon|hang|san pham)|"
    r"what should i buy|what do i need|help me (?:shop|choose|plan|prepare))\b"
)


def _catalog_tool_timeout_policy(tool_name: str) -> tuple[float, int]:
    """Give deterministic multi-category planning a realistic execution budget.

    Mission planning searches several catalog groups and then evaluates bounded
    combinations. Retrying that CPU-bound operation after a timeout would run a
    duplicate worker in the background, so it gets one longer attempt.
    """
    if tool_name == "plan_shopping_mission":
        return SHOPPING_MISSION_TOOL_TIMEOUT_SECONDS, 0
    if tool_name == "recommend_products":
        return CATALOG_RECOMMENDATION_TIMEOUT_SECONDS, 0
    return CATALOG_TOOL_TIMEOUT_SECONDS, TOOL_TIMEOUT_RETRIES


def _normalize_vietnamese(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(unaccented.replace("đ", "d").split())


def _explicit_budget_amount(text: str) -> int | None:
    normalized = _normalize_vietnamese(text)
    if not re.search(
        r"\b(?:ngan sach|tong chi|chi phi|chi tieu|muc chi|budget|spend|"
        r"chi khoang|chi toi da|tang|giam|nang|ha|increase|decrease)\b",
        normalized,
    ):
        return None

    matches = list(
        re.finditer(
            r"(?<!\w)(\d+(?:[.,]\d+)*)\s*(k|nghin|ngan|trieu|vnd|dong|d)?(?!\w)",
            normalized,
        )
    )
    amounts: list[int] = []
    for match in matches:
        raw, unit = match.groups()
        if not unit and not re.search(r"[.,]", raw):
            following = normalized[match.end() : match.end() + 16]
            if re.match(r"\s*(?:nguoi|be|people|persons|guests)\b", following):
                continue
        if "." in raw or "," in raw:
            separator = "." if "." in raw else ","
            parts = raw.split(separator)
            if len(parts) == 2 and len(parts[1]) != 3:
                numeric = float(f"{parts[0]}.{parts[1]}")
            else:
                numeric = float(re.sub(r"[.,]", "", raw))
        else:
            numeric = float(raw)
        multiplier = (
            1_000_000 if unit == "trieu" else 1_000 if unit in {"k", "nghin", "ngan"} else 1
        )
        amount = round(numeric * multiplier)
        if 0 < amount <= 100_000_000:
            amounts.append(amount)
    return amounts[-1] if amounts else None


def _explicit_party_size(text: str) -> int | None:
    normalized = _normalize_vietnamese(text)
    match = re.search(r"\b(\d+)\s*(?:nguoi|be|tre em|people|persons|guests)\b", normalized)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 100 else None


def _only_explicit_needs_are_required(text: str) -> bool:
    """Honor a customer instruction that extra recommendations stay optional."""
    normalized = _normalize_vietnamese(text)
    confirms_needs = bool(
        re.search(
            r"\b(?:xac nhan|confirmed?|required|needed|must have|bat buoc|phai co|can)\b",
            normalized,
        )
    )
    keeps_suggestions_optional = bool(
        re.search(
            r"\b(?:goi y|de xuat|suggest(?:ion|ions)?|recommend(?:ation|ations)?)\b"
            r".{0,55}\b(?:tuy chon|khong bat buoc|optional|not required)\b",
            normalized,
        )
    )
    return confirms_needs and keeps_suggestions_optional


_SKINCARE_NEED_CUES: dict[str, tuple[str, ...]] = {
    "cleanser": ("sua rua mat", "lam sach da mat", "lam sach mat"),
    "moisturizer": (
        "kem duong am",
        "duong am da mat",
        "kem duong da mat",
        "duong da mat",
    ),
    "sunscreen": (
        "kem chong nang",
        "chong nang",
        "bao ve da khi ra ngoai",
        "bao ve da ban ngay",
        "bao ve da khi di ra ngoai",
        "sun screen",
        "sunscreen",
        "sun protection",
    ),
}


def _specific_skincare_need_was_named(need_text: str, normalized_user_text: str) -> bool:
    """Only harden a skincare component when the user named that component.

    Broad phrases like ``chăm sóc da`` must not make every model-decomposed
    routine step mandatory. A few unambiguous Vietnamese paraphrases are kept
    here for common spoken requests such as ``đồ làm sạch da mặt``.
    """
    normalized_need = _normalize_vietnamese(need_text)
    normalized_need = re.sub(r"[^\w\s]", " ", normalized_need)
    for cues in _SKINCARE_NEED_CUES.values():
        need_is_this_component = any(
            re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", normalized_need) for cue in cues
        )
        if need_is_this_component and any(
            re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", normalized_user_text) for cue in cues
        ):
            return True
    return False


def _explicit_recommendation_category(text: str) -> str | None:
    return _parse_recommendation_category(text)


def _enforce_recommendation_category(
    tool_calls: list[dict[str, Any]], user_text: str
) -> list[dict[str, Any]]:
    category = _explicit_recommendation_category(user_text)
    normalized_calls = []
    for call in tool_calls:
        copied_call = copy.deepcopy(call)
        function = copied_call.get("function") or {}
        if function.get("name") == "recommend_products":
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = (
                    json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                )
            except (json.JSONDecodeError, TypeError):
                arguments = None
            if isinstance(arguments, dict):
                if category is not None:
                    arguments["category"] = category
                # Rank from the actual need, never from an invented benefit.
                arguments["preference_text"] = user_text
                function["arguments"] = json.dumps(arguments, ensure_ascii=False)
                copied_call["function"] = function
        normalized_calls.append(copied_call)
    return normalized_calls


def _enforce_shopping_mission_call_semantics(
    tool_calls: list[dict[str, Any]],
    user_text: str,
    previous_mission: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Keep plan edits from silently rejecting SKUs or hardening preferences."""
    normalized_text = _normalize_vietnamese(user_text)
    soft_spicy_preference = bool(
        re.search(r"\b(?:uu tien|thich|prefer|preferably|if possible)\b", normalized_text)
        and re.search(r"\b(?:khong cay|cay|non spicy|mild)\b", normalized_text)
        and not re.search(
            r"\b(?:bat buoc|phai|must|only)\b.{0,25}\b(?:khong cay|cay|non spicy|mild)\b|"
            r"\b(?:khong cay|cay|non spicy|mild)\b.{0,25}\b(?:bat buoc|phai|must|only)\b",
            normalized_text,
        )
    )
    is_alternative = is_alternative_product_request(user_text)
    previous = previous_mission if isinstance(previous_mission, dict) else {}
    previous_rejected = [
        str(value) for value in previous.get("rejected_product_ids", []) if str(value).strip()
    ]
    previous_proposed = [
        str(item.get("product_id"))
        for item in previous.get("proposed_items", [])
        if isinstance(item, dict) and item.get("product_id")
    ]
    normalized_calls: list[dict[str, Any]] = []
    for call in tool_calls:
        copied_call = copy.deepcopy(call)
        function = copied_call.get("function") or {}
        if function.get("name") != "plan_shopping_mission":
            normalized_calls.append(copied_call)
            continue
        raw_arguments = function.get("arguments", "{}")
        try:
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
        except (json.JSONDecodeError, TypeError):
            arguments = None
        if not isinstance(arguments, dict):
            normalized_calls.append(copied_call)
            continue

        if previous and not arguments.get("reset_mission"):
            explicit_budget = _explicit_budget_amount(user_text)
            clear_budget = bool(
                re.search(
                    r"\b(?:bo|xoa|huy|khong can|khong dung|remove|clear|no longer need)\s+"
                    r"(?:ngan sach|budget)\b",
                    normalized_text,
                )
            )
            if explicit_budget is not None:
                arguments["total_budget_vnd"] = explicit_budget
                arguments["clear_total_budget"] = False
            elif clear_budget:
                arguments["total_budget_vnd"] = None
                arguments["clear_total_budget"] = True
            else:
                arguments["total_budget_vnd"] = previous.get("total_budget_vnd")
                arguments["clear_total_budget"] = False

            explicit_party_size = _explicit_party_size(user_text)
            clear_party_size = bool(
                re.search(
                    r"\b(?:bo|xoa|khong can|khong tinh|remove|clear)\s+"
                    r"(?:so nguoi|party size|number of people)\b",
                    normalized_text,
                )
            )
            if explicit_party_size is not None:
                arguments["party_size"] = explicit_party_size
                arguments["clear_party_size"] = False
            elif clear_party_size:
                arguments["party_size"] = None
                arguments["clear_party_size"] = True
            else:
                arguments["party_size"] = previous.get("party_size")
                arguments["clear_party_size"] = False

        if previous and not arguments.get("reset_mission"):
            rejected_ids = [*previous_rejected]
            if is_alternative:
                rejected_ids.extend(previous_proposed)
            arguments["rejected_product_ids"] = list(dict.fromkeys(rejected_ids))[-24:]

        if soft_spicy_preference:
            preference = "không cay" if "khong cay" in normalized_text else "cay nhẹ"
            for need in arguments.get("needs", []):
                if not isinstance(need, dict):
                    continue
                # A soft taste preference must never become a hard catalog filter,
                # including on needs (such as drinks) that do not have this field.
                need["spicy"] = None
                need_text = " ".join(
                    str(need.get(key) or "") for key in ("label", "category", "product_query")
                )
                need_category = _parse_recommendation_category(need_text)
                if need_category in {"snack", "meal"}:
                    terms = need.get("preference_terms")
                    if not isinstance(terms, list):
                        terms = []
                    if preference not in terms:
                        terms.append(preference)
                    need["preference_terms"] = terms[:8]
                constraints = need.get("unverified_constraints")
                if isinstance(constraints, list):
                    need["unverified_constraints"] = [
                        value
                        for value in constraints
                        if not re.search(
                            r"\b(?:khong cay|cay nhe|vi cay|non[_ -]?spicy|mild|"
                            r"spicy\s*[:=]\s*(?:false|true))\b",
                            _normalize_vietnamese(str(value)),
                        )
                    ]
                need["product_query"] = re.sub(
                    r"\b(?:không\s+cay|khong\s+cay|cay\s+nhe|non[_ -]?spicy|mild)\b",
                    " ",
                    str(need.get("product_query") or ""),
                    flags=re.IGNORECASE,
                ).strip()

            for key in ("hard_requirements", "exclusions"):
                values = arguments.get(key)
                if isinstance(values, list):
                    arguments[key] = [
                        value
                        for value in values
                        if not re.search(
                            r"\b(?:khong cay|cay nhe|vi cay|non[_ -]?spicy|mild|"
                            r"spicy\s*[:=]\s*(?:false|true))\b",
                            _normalize_vietnamese(str(value)),
                        )
                    ]

        needs = arguments.get("needs")
        if isinstance(needs, list) and needs:
            shared_use_explicit = bool(
                re.search(r"\b(?:dung chung|share(?:d)?|shared use)\b", normalized_text)
            )
            if not shared_use_explicit:
                for need in needs:
                    if not isinstance(need, dict):
                        continue
                    rationale = str(need.get("rationale") or "")
                    rationale = re.sub(
                        r"(?:[,;:]?\s*)\b(?:dùng chung(?: cho cả nhóm)?|"
                        r"cả nhóm dùng chung|dung chung(?: cho ca nhom)?|ca nhom dung chung|"
                        r"share(?:d)? (?:by|with) (?:the )?(?:whole )?group)\b",
                        "",
                        rationale,
                        flags=re.IGNORECASE,
                    )
                    rationale = re.sub(r"\s+([,;.])", r"\1", rationale)
                    rationale = re.sub(r"[,;\s]+$", "", rationale).strip()
                    if rationale:
                        need["rationale"] = rationale
                    preference_terms = need.get("preference_terms")
                    if isinstance(preference_terms, list):
                        need["preference_terms"] = [
                            value
                            for value in preference_terms
                            if not re.search(
                                r"\b(?:dung chung|dung cho ca nhom|dung duoc cho nhieu nguoi|"
                                r"shared use|for group use)\b",
                                _normalize_vietnamese(str(value)),
                            )
                        ]
            for need in needs:
                if not isinstance(need, dict):
                    continue
                constraints = need.get("unverified_constraints")
                if not isinstance(constraints, list):
                    continue
                retained_constraints: list[str] = []
                contextual_preferences: list[str] = []
                for value in constraints:
                    normalized_constraint = _normalize_vietnamese(str(value))
                    is_negated = bool(
                        re.search(
                            r"\b(?:khong|tranh|khong duoc|not|avoid|without|must not)\b",
                            normalized_constraint,
                        )
                    )
                    is_contextual_use = any(
                        phrase in normalized_constraint
                        for phrase in (
                            "dung cho da mat",
                            "for face",
                            "facial use",
                            "dung moi ngay",
                            "su dung hang ngay",
                            "hang ngay",
                            "daily use",
                            "every day",
                        )
                    )
                    if is_contextual_use and not is_negated:
                        contextual_preferences.append(str(value))
                    else:
                        retained_constraints.append(str(value))
                need["unverified_constraints"] = retained_constraints
                if contextual_preferences:
                    preference_terms = need.get("preference_terms")
                    if not isinstance(preference_terms, list):
                        preference_terms = []
                    need["preference_terms"] = list(
                        dict.fromkeys([*preference_terms, *contextual_preferences])
                    )[:8]
            explicit_categories = set(_parse_explicit_recommendation_categories(user_text))
            explicit_needs_only = _only_explicit_needs_are_required(user_text)
            previous_needs = {
                str(need.get("need_id")): need
                for need in previous.get("needs", [])
                if isinstance(need, dict) and need.get("need_id")
            }
            user_tokens = set(re.findall(r"[a-z0-9]+", normalized_text))
            open_ended_need_discovery = bool(_OPEN_ENDED_NEED_DISCOVERY_CUE.search(normalized_text))
            generic_need_tokens = {
                "an",
                "ban",
                "can",
                "cho",
                "be",
                "em",
                "do",
                "di",
                "gi",
                "hang",
                "la",
                "minh",
                "mon",
                "mua",
                "nhom",
                "nhu",
                "pham",
                "san",
                "toi",
                "tu",
                "va",
                "voi",
                "cung",
                "gia",
                "dinh",
                "chuyen",
                "tham",
                "ong",
                "ba",
                "tre",
                "baby",
                "babies",
                "kid",
                "kids",
                "child",
                "children",
                "family",
                "trip",
            }
            user_need_tokens = user_tokens - generic_need_tokens
            supported_categories = {
                "snack",
                "drink",
                "dairy",
                "meal",
                "skincare",
                "haircare",
                "oral_care",
                "home_cleaning",
                "laundry",
                "baby",
                "cosmetic",
                "paper_goods",
            }
            for need in needs:
                if not isinstance(need, dict):
                    continue
                need_text = " ".join(
                    str(need.get(key) or "") for key in ("label", "category", "product_query")
                )
                canonical_need = _parse_recommendation_category(need_text)
                need_id = str(need.get("need_id") or "")
                previous_need = previous_needs.get(need_id)
                need_is_lexically_named = any(
                    len(phrase_tokens := _normalize_vietnamese(str(need.get(key) or "")).split())
                    >= 2
                    and re.search(
                        rf"(?<!\w){re.escape(' '.join(phrase_tokens))}(?!\w)",
                        normalized_text,
                    )
                    for key in ("label", "product_query")
                )
                if previous_need is not None and re.search(
                    r"\b(?:giu nguyen|giu lai|nhu cau da chot|keep (?:the )?same needs?)\b",
                    normalized_text,
                ):
                    need["source"] = previous_need.get("source", need.get("source", "INFERRED"))
                    need["required"] = previous_need.get("required", need.get("required", False))
                    continue
                category_leaf = str(need.get("category") or "").split(">")[-1]
                category_leaf_intent = _parse_recommendation_category(category_leaf)
                canonical_need = canonical_need or category_leaf_intent
                # The model can echo occasion words into a need label (for
                # example, "snack xem bóng"). Only the catalog leaf can make a
                # lexical match explicit; the label may contain activity context.
                direct_need_text = category_leaf or str(need.get("label") or "")
                need_tokens = (
                    set(re.findall(r"[a-z0-9]+", _normalize_vietnamese(direct_need_text)))
                    - generic_need_tokens
                )
                if canonical_need == "skincare":
                    directly_named = need_is_lexically_named or _specific_skincare_need_was_named(
                        need_text,
                        normalized_text,
                    )
                elif canonical_need in supported_categories:
                    # A broad intent such as "snack" must not turn every
                    # inferred child category (candy, sausage, chips, etc.)
                    # into an explicit requirement. The canonical category
                    # only counts when the catalog leaf itself names that
                    # broad group; otherwise require a lexical mention of the
                    # specific leaf by the customer.
                    directly_named = (
                        (
                            canonical_need in explicit_categories
                            and category_leaf_intent == canonical_need
                        )
                        or need_is_lexically_named
                        or len(need_tokens & user_need_tokens) >= 1
                    )
                    if canonical_need == "baby" and not re.search(
                        r"\b(?:do cho|do dung cho|cham soc|ta bim|baby care)\b",
                        normalized_text,
                    ):
                        directly_named = False
                else:
                    directly_named = (
                        need_is_lexically_named or len(need_tokens & user_need_tokens) >= 1
                    )
                if open_ended_need_discovery and not need_is_lexically_named:
                    category_tokens = set(
                        re.findall(r"[a-z0-9]+", _normalize_vietnamese(category_leaf))
                    )
                    query_tokens = set(
                        re.findall(
                            r"[a-z0-9]+",
                            _normalize_vietnamese(str(need.get("product_query") or "")),
                        )
                    )
                    specific_query_tokens = query_tokens - category_tokens - generic_need_tokens
                    if specific_query_tokens - user_tokens:
                        # A broad goal such as routine skincare can name the
                        # context while leaving its component product groups
                        # open for advice. Keep those inferred groups optional
                        # until the customer names or confirms them.
                        directly_named = False
                if not directly_named:
                    if previous_need is not None and not explicit_needs_only:
                        need["source"] = previous_need.get("source", need.get("source", "INFERRED"))
                        need["required"] = previous_need.get(
                            "required", need.get("required", False)
                        )
                    else:
                        need["source"] = "INFERRED"
                        need["required"] = False
                else:
                    need["source"] = "EXPLICIT"
                    need["required"] = True

            has_existing_item_cue = bool(
                re.search(
                    r"\b(?:da co|co san|mang theo|da mua|da chuan bi|already have|"
                    r"already bought|already packed|bringing)\b",
                    normalized_text,
                )
            )
            if has_existing_item_cue and explicit_categories:
                existing_items = arguments.get("existing_items")
                if not isinstance(existing_items, list):
                    existing_items = []
                for need in needs:
                    if not isinstance(need, dict):
                        continue
                    need_text = " ".join(
                        str(need.get(key) or "") for key in ("label", "category", "product_query")
                    )
                    if _parse_recommendation_category(need_text) not in explicit_categories:
                        continue
                    need["satisfied_by_existing"] = True
                    label = str(need.get("label") or "").strip()
                    if label and label not in existing_items:
                        existing_items.append(label)
                arguments["existing_items"] = existing_items[:12]

        function["arguments"] = json.dumps(arguments, ensure_ascii=False)
        copied_call["function"] = function
        normalized_calls.append(copied_call)
    return normalized_calls


async def lookup_followup_snapshot(product_id: str, fields: tuple[str, ...]) -> ToolObservation:
    """Use the catalog tool boundary to resolve an elliptical follow-up safely."""
    call = {
        "function": {
            "name": "get_product_snapshot",
            "arguments": json.dumps(
                {"product_id": product_id, "fields": list(fields)},
                ensure_ascii=False,
            ),
        }
    }
    _, observation = await _execute_tool_call(call, calls_executed=0)
    return observation


async def lookup_product_candidates(
    query: str,
    current_user_text: str = "",
) -> ToolObservation:
    """Search the active catalog through the same bounded tool gateway as the agent."""
    call = {
        "function": {
            "name": "search_products",
            "arguments": json.dumps(
                {"query": query, "limit": 5},
                ensure_ascii=False,
            ),
        }
    }
    _, observation = await _execute_tool_call(
        call,
        calls_executed=0,
        current_user_text=current_user_text,
    )
    return observation


async def lookup_recommendation_products(
    arguments: dict[str, Any],
    previous_queries: tuple[str, ...] = (),
    current_user_text: str = "",
) -> ToolObservation:
    """Run a structured recommendation through the same bounded tool gateway."""
    call = {
        "function": {
            "name": "recommend_products",
            "arguments": json.dumps(arguments, ensure_ascii=False),
        }
    }
    _, observation = await _execute_tool_call(
        call,
        calls_executed=0,
        previous_queries=previous_queries,
        current_user_text=current_user_text,
    )
    return observation


async def lookup_product_promotions(arguments: dict[str, Any] | None = None) -> ToolObservation:
    """List recorded product offers through the same bounded tool gateway."""
    call = {
        "function": {
            "name": "list_product_promotions",
            "arguments": json.dumps(arguments or {"limit": 5}, ensure_ascii=False),
        }
    }
    _, observation = await _execute_tool_call(call, calls_executed=0)
    return observation


async def lookup_catalog_additions(arguments: dict[str, Any] | None = None) -> ToolObservation:
    """Compare imported catalog versions to identify products newly listed."""
    call = {
        "function": {
            "name": "list_new_catalog_products",
            "arguments": json.dumps(arguments or {"limit": 5}, ensure_ascii=False),
        }
    }
    _, observation = await _execute_tool_call(call, calls_executed=0)
    return observation


async def lookup_branch_information(
    name: str,
    kiosk_id: str,
    arguments: dict[str, Any] | None = None,
) -> ToolObservation:
    """Read branch-scoped operational facts with the kiosk bound by the API."""
    raw_arguments = json.dumps(arguments or {}, ensure_ascii=False)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                execute_retail_information_tool,
                name,
                raw_arguments,
                kiosk_id=kiosk_id,
            ),
            timeout=CATALOG_TOOL_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return ToolObservation(
            content={"error": "Retail information lookup timed out."},
            tool_name=name,
            status="TEMPORARY_FAILURE",
            source="tool_gateway",
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )
    except Exception as exc:  # Keep connector failures inside the tool boundary.
        return ToolObservation(
            content={
                "error": "Retail information lookup is temporarily unavailable.",
                "failure_type": type(exc).__name__,
            },
            tool_name=name,
            status="TEMPORARY_FAILURE",
            source="tool_gateway",
            response_mode="DETERMINISTIC_ERROR",
            terminal=True,
        )


@dataclass(frozen=True)
class RetailAgentResult:
    text: str
    model: str
    tools_used: tuple[str, ...]
    products: tuple[ProductSnapshot, ...]
    observations: tuple[ToolObservation, ...] = ()


async def _execute_tool_call(
    call: dict[str, Any],
    calls_executed: int,
    shopping_mission: dict[str, Any] | None = None,
    previous_queries: tuple[str, ...] = (),
    current_user_text: str = "",
) -> tuple[str, ToolObservation]:
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    if calls_executed >= MAX_AGENT_TOOL_CALLS:
        observation = ToolObservation(
            content={"error": "Tool-call limit reached for this turn."},
            tool_name=name,
            status="DENIED",
            source="policy_gateway",
        )
        return name, observation

    raw_arguments = function.get("arguments", "{}")
    if not isinstance(raw_arguments, str):
        raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)

    last_error: Exception | None = None
    operation_timeout, retries = _catalog_tool_timeout_policy(name)
    for attempt in range(retries + 1):
        try:
            tool_kwargs = {"previous_mission": shopping_mission}
            if name in {"search_products", "recommend_products"}:
                tool_kwargs["previous_queries"] = previous_queries
                if current_user_text:
                    tool_kwargs["current_user_text"] = current_user_text
            observation = await asyncio.wait_for(
                asyncio.to_thread(
                    execute_catalog_tool,
                    name,
                    raw_arguments,
                    **tool_kwargs,
                ),
                timeout=operation_timeout,
            )
            return name, observation
        except TimeoutError as exc:
            last_error = exc
            if attempt < TOOL_TIMEOUT_RETRIES:
                continue
        except Exception as exc:  # Keep tool implementation errors inside the tool boundary.
            last_error = exc
            break

    error_name = type(last_error).__name__ if last_error else "UnknownError"
    observation = ToolObservation(
        content={
            "error": "Catalog lookup is temporarily unavailable. Please try again.",
            "failure_type": error_name,
        },
        tool_name=name,
        status="TEMPORARY_FAILURE",
        source="tool_gateway",
        provenance_id=uuid4().hex,
        response_mode="DETERMINISTIC_ERROR",
        terminal=True,
    )
    return name, observation


async def run_retail_agent(
    groq: GroqService,
    messages: list[dict[str, Any]],
    state: SessionState,
    candidate_query: str = "",
    shopping_mission: dict[str, Any] | None = None,
    force_tool_name: str | None = None,
) -> RetailAgentResult:
    tools = tools_for_state(state)
    tool_choice: str | dict[str, Any] = "auto"
    if force_tool_name:
        matching_tool = next(
            (tool for tool in tools if (tool.get("function") or {}).get("name") == force_tool_name),
            None,
        )
        if matching_tool is None:
            raise GroqProviderError("Requested retail tool is not allowed in this session state")
        tools = [matching_tool]
        tool_choice = {
            "type": "function",
            "function": {"name": force_tool_name},
        }
    if not tools:
        result = await groq.chat(messages)
        if getattr(result, "tool_calls", None):
            raise GroqProviderError("Retail agent returned a tool call outside an allowed state")
        return RetailAgentResult(
            text=str(result),
            model=getattr(result, "model", ""),
            tools_used=(),
            products=(),
        )

    products_by_id: dict[str, ProductSnapshot] = {}
    observations: list[ToolObservation] = []
    tools_used: list[str] = []
    calls_executed = 0
    user_message_indexes = [
        index for index, message in enumerate(messages) if message.get("role") == "user"
    ]
    previous_queries = tuple(
        str(messages[index].get("content", ""))[:2000] for index in user_message_indexes[:-1]
    )[-4:]

    for _ in range(MAX_AGENT_TOOL_ROUNDS):
        result = await groq.chat(messages, tools=tools, tool_choice=tool_choice)
        tool_calls = getattr(result, "tool_calls", None) or []
        if force_tool_name and not tool_calls:
            raise GroqProviderError("Groq did not call the required retail planning tool")
        latest_user_text = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        tool_calls = _enforce_recommendation_category(tool_calls, latest_user_text)
        tool_calls = _enforce_shopping_mission_call_semantics(
            tool_calls,
            latest_user_text,
            shopping_mission,
        )
        if force_tool_name and any(
            str((call.get("function") or {}).get("name") or "") != force_tool_name
            for call in tool_calls
        ):
            raise GroqProviderError("Groq called a different tool than the required planning tool")
        if not tool_calls:
            return RetailAgentResult(
                text=str(result),
                model=getattr(result, "model", ""),
                tools_used=tuple(dict.fromkeys(tools_used)),
                products=tuple(products_by_id.values()),
                observations=tuple(observations),
            )

        terminal_observation = False
        messages.append(
            {
                "role": "assistant",
                "content": str(result) or None,
                "tool_calls": tool_calls,
            }
        )

        for call_number, call in enumerate(tool_calls, start=1):
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name == "get_product_snapshot":
                raw_arguments = function.get("arguments", "{}")
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                except (json.JSONDecodeError, TypeError):
                    arguments = None
                product_id = arguments.get("product_id") if isinstance(arguments, dict) else None
                candidates = tuple(products_by_id.values())
                matched_product_id = _unique_product_match_id(candidate_query, candidates)
                is_allowed_candidate = matched_product_id == product_id
                if not is_allowed_candidate:
                    observation = ToolObservation(
                        content={
                            "error": (
                                "Search the catalog first and select only a uniquely matching "
                                "product candidate."
                            )
                        },
                        tool_name=name,
                        status="DENIED",
                        source="policy_gateway",
                        response_mode="DETERMINISTIC_ERROR",
                        terminal=True,
                    )
                else:
                    name, observation = await _execute_tool_call(
                        call,
                        calls_executed,
                        shopping_mission,
                        previous_queries,
                        latest_user_text,
                    )
            else:
                name, observation = await _execute_tool_call(
                    call,
                    calls_executed,
                    shopping_mission,
                    previous_queries,
                    latest_user_text,
                )
            calls_executed += 1
            observations.append(observation)
            terminal_observation = terminal_observation or observation.terminal
            if name and observation.status != "DENIED":
                tools_used.append(name)
            for product in observation.products:
                products_by_id[product.product_id] = product

            tool_call_id = str(call.get("id") or f"unidentified-tool-call-{call_number}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "content": json.dumps(observation.to_envelope(), ensure_ascii=False),
                }
            )

            if observation.status == "TEMPORARY_FAILURE":
                return RetailAgentResult(
                    text="",
                    model=getattr(result, "model", ""),
                    tools_used=tuple(dict.fromkeys(tools_used)),
                    products=tuple(products_by_id.values()),
                    observations=tuple(observations),
                )

        if terminal_observation:
            return RetailAgentResult(
                text="",
                model=getattr(result, "model", ""),
                tools_used=tuple(dict.fromkeys(tools_used)),
                products=tuple(products_by_id.values()),
                observations=tuple(observations),
            )

    final_result = await groq.chat(messages, tools=tools, tool_choice="none")
    if getattr(final_result, "tool_calls", None):
        raise GroqProviderError("Retail agent exceeded its tool-call limit")
    return RetailAgentResult(
        text=str(final_result),
        model=getattr(final_result, "model", ""),
        tools_used=tuple(dict.fromkeys(tools_used)),
        products=tuple(products_by_id.values()),
        observations=tuple(observations),
    )


def _unique_product_match_id(
    query: str,
    candidates: tuple[ProductSnapshot, ...],
) -> str | None:
    if not query or not candidates:
        return None
    query_tokens = [
        token for token in re.findall(r"\w+", _normalize_vietnamese(query)) if len(token) > 1
    ]
    repeated_bim_bim_phrase = query_tokens.count("bim") >= 2
    if len(set(query_tokens)) < 2 and not repeated_bim_bim_phrase:
        return None
    matches = []
    for product in candidates:
        product_tokens = re.findall(
            r"\w+",
            _normalize_vietnamese(
                f"{product.name} {product.uom or ''} {PRODUCT_ALIASES.get(product.product_id, '')}"
            ),
        )
        product_counts = Counter(product_tokens)
        is_match = set(query_tokens).issubset(product_counts) and (
            not repeated_bim_bim_phrase or product_counts["bim"] >= 2
        )
        if is_match:
            matches.append(product)
    if len(matches) != 1:
        return None
    if len(candidates) > 1 and len(query_tokens) < 3:
        return None
    return matches[0].product_id
