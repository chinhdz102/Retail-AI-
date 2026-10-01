from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.products.catalog import ProductSnapshot
from backend.services.groq import GroqProviderError, GroqService
from backend.services.retail_tools import ToolObservation, execute_catalog_tool, tools_for_state
from backend.shared.contracts import SessionState

MAX_AGENT_TOOL_ROUNDS = 2
MAX_AGENT_TOOL_CALLS = 5
CATALOG_TOOL_TIMEOUT_SECONDS = 2.0
TOOL_TIMEOUT_RETRIES = 1


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
            name, observation = await _execute_tool_call(call, calls_executed)
            calls_executed += 1
            observations.append(observation)
            terminal_observation = terminal_observation or observation.terminal
            if name:
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
