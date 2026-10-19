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
    explicit_recommendation_category as _parse_recommendation_category,
)
from backend.shared.contracts import SessionState

MAX_AGENT_TOOL_ROUNDS = 2
MAX_AGENT_TOOL_CALLS = 5
CATALOG_TOOL_TIMEOUT_SECONDS = 2.0
TOOL_TIMEOUT_RETRIES = 1


def _normalize_vietnamese(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(unaccented.replace("đ", "d").split())


def _explicit_recommendation_category(text: str) -> str | None:
    return _parse_recommendation_category(text)


def _enforce_recommendation_category(
    tool_calls: list[dict[str, Any]], user_text: str
) -> list[dict[str, Any]]:
    category = _explicit_recommendation_category(user_text)
    if category is None:
        return tool_calls
    normalized_calls = []
    for call in tool_calls:
        copied_call = copy.deepcopy(call)
        function = copied_call.get("function") or {}
        if function.get("name") == "recommend_products":
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
            except (json.JSONDecodeError, TypeError):
                arguments = None
            if isinstance(arguments, dict):
                arguments["category"] = category
                function["arguments"] = json.dumps(arguments, ensure_ascii=False)
                copied_call["function"] = function
        normalized_calls.append(copied_call)
    return normalized_calls


async def lookup_followup_snapshot(
    product_id: str, fields: tuple[str, ...]
) -> ToolObservation:
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


async def lookup_product_candidates(query: str) -> ToolObservation:
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
    _, observation = await _execute_tool_call(call, calls_executed=0)
    return observation


async def lookup_recommendation_products(arguments: dict[str, Any]) -> ToolObservation:
    """Run a structured recommendation through the same bounded tool gateway."""
    call = {
        "function": {
            "name": "recommend_products",
            "arguments": json.dumps(arguments, ensure_ascii=False),
        }
    }
    _, observation = await _execute_tool_call(call, calls_executed=0)
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
    for attempt in range(TOOL_TIMEOUT_RETRIES + 1):
        try:
            observation = await asyncio.wait_for(
                asyncio.to_thread(execute_catalog_tool, name, raw_arguments),
                timeout=CATALOG_TOOL_TIMEOUT_SECONDS,
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
) -> RetailAgentResult:
    tools = tools_for_state(state)
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

    for _ in range(MAX_AGENT_TOOL_ROUNDS):
        result = await groq.chat(messages, tools=tools, tool_choice="auto")
        tool_calls = getattr(result, "tool_calls", None) or []
        latest_user_text = next(
            (str(message.get("content", "")) for message in reversed(messages)
             if message.get("role") == "user"),
            "",
        )
        tool_calls = _enforce_recommendation_category(tool_calls, latest_user_text)
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
                    name, observation = await _execute_tool_call(call, calls_executed)
            else:
                name, observation = await _execute_tool_call(call, calls_executed)
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
        token
        for token in re.findall(r"\w+", _normalize_vietnamese(query))
        if len(token) > 1
    ]
    repeated_bim_bim_phrase = query_tokens.count("bim") >= 2
    if len(set(query_tokens)) < 2 and not repeated_bim_bim_phrase:
        return None
    matches = []
    for product in candidates:
        product_tokens = re.findall(
            r"\w+",
            _normalize_vietnamese(
                f"{product.name} {product.uom or ''} "
                f"{PRODUCT_ALIASES.get(product.product_id, '')}"
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
