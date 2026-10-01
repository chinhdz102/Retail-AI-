from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.products.catalog import ProductSnapshot
from backend.services.groq import GroqProviderError, GroqService
from backend.services.retail_tools import (
    ToolObservation,
    execute_catalog_tool,
    tools_for_state,
)
from backend.shared.contracts import SessionState

MAX_AGENT_TOOL_ROUNDS = 2
MAX_AGENT_TOOL_CALLS = 5


@dataclass(frozen=True)
class RetailAgentResult:
    text: str
    model: str
    tools_used: tuple[str, ...]
    products: tuple[ProductSnapshot, ...]


def _execute_tool_call(
    call: dict[str, Any],
    calls_executed: int,
) -> tuple[str, ToolObservation]:
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    if calls_executed >= MAX_AGENT_TOOL_CALLS:
        observation = ToolObservation({"error": "Tool-call limit reached for this turn."})
        return name, observation

    raw_arguments = function.get("arguments", "{}")
    if not isinstance(raw_arguments, str):
        raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)
    observation = execute_catalog_tool(name, raw_arguments)
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
            )

        messages.append(
            {
                "role": "assistant",
                "content": str(result) or None,
                "tool_calls": tool_calls,
            }
        )

        for call_number, call in enumerate(tool_calls, start=1):
            name, observation = _execute_tool_call(
                call,
                calls_executed,
            )
            calls_executed += 1
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
                    "content": json.dumps(observation.content, ensure_ascii=False),
                }
            )

    final_result = await groq.chat(messages, tools=tools, tool_choice="none")
    if getattr(final_result, "tool_calls", None):
        raise GroqProviderError("Retail agent exceeded its tool-call limit")
    return RetailAgentResult(
        text=str(final_result),
        model=getattr(final_result, "model", ""),
        tools_used=tuple(dict.fromkeys(tools_used)),
        products=tuple(products_by_id.values()),
    )
