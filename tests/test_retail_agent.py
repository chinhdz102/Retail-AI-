import json

import pytest

from backend.services.groq import GroqChatResult
from backend.services.retail_agent import (
    _requires_sellable_recommendation,
    run_retail_agent,
)
from backend.shared.contracts import SessionState


class ScriptedGroq:
    def __init__(self, *results: GroqChatResult) -> None:
        self.results = list(results)
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, tool_choice=None):
        self.calls += 1
        return self.results.pop(0)


def tool_call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


@pytest.mark.asyncio
async def test_terminal_snapshot_skips_redundant_final_llm_call() -> None:
    groq = ScriptedGroq(
        GroqChatResult("", "qwen/qwen3.8-27b", [
            tool_call("search_products", {"query": "Coca Zero"}),
        ]),
        GroqChatResult("", "qwen/qwen3.8-27b", [
            tool_call("get_product_snapshot", {
                "product_id": "COKE_ZERO_330",
                "fields": ["price_vnd"],
            }),
        ]),
        GroqChatResult("This final call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero price?"}],
        SessionState.BROWSING,
    )

    assert groq.calls == 2
    assert result.text == ""
    assert result.observations[-1].response_mode == "DETERMINISTIC_FACT"
    assert result.observations[-1].terminal is True


@pytest.mark.asyncio
async def test_empty_search_result_is_terminal_without_followup_llm_call() -> None:
    groq = ScriptedGroq(
        GroqChatResult("", "qwen/qwen3.8-27b", [
            tool_call("search_products", {"query": "not-a-real-retail-product-9382"}),
        ]),
        GroqChatResult("This followup call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Find an unknown product."}],
        SessionState.BROWSING,
    )

    assert groq.calls == 1
    assert result.observations[-1].content["count"] == 0
    assert result.observations[-1].response_mode == "DETERMINISTIC_ERROR"
    assert result.observations[-1].terminal is True


@pytest.mark.asyncio
async def test_recommendation_forces_sellable_filter_even_if_model_omits_it() -> None:
    groq = ScriptedGroq(
        GroqChatResult("", "qwen/qwen3.8-27b", [
            tool_call("search_products", {"query": "MILK01", "in_stock": False}),
        ]),
        GroqChatResult("This followup call should not happen.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Gợi ý sản phẩm MILK01 cho tôi."}],
        SessionState.BROWSING,
    )

    assert groq.calls == 1
    observation = result.observations[-1]
    assert observation.content["count"] == 0
    assert observation.content["enforced_constraints"] == ["in_stock"]
    assert observation.terminal is True


def test_explicit_unavailable_lookup_does_not_force_sellable_filter() -> None:
    assert _requires_sellable_recommendation(
        [{"role": "user", "content": "Gợi ý một sản phẩm đã hết hạn."}]
    ) is False
    assert _requires_sellable_recommendation(
        [{"role": "user", "content": "Cho tôi biết sữa MILK01 còn bao nhiêu?"}]
    ) is False
    assert _requires_sellable_recommendation(
        [{"role": "user", "content": "Gợi ý đồ uống không hết hạn."}]
    ) is True
    assert _requires_sellable_recommendation(
        [{"role": "user", "content": "推荐没有过期的饮料。"}]
    ) is True
