import json

import pytest

from backend.services.groq import GroqChatResult
from backend.services.retail_agent import (
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
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("search_products", {"query": "Coca Zero"}),
            ],
        ),
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_snapshot",
                    {
                        "product_id": "COKE_ZERO_330",
                        "fields": ["price_vnd"],
                    },
                ),
            ],
        ),
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
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("search_products", {"query": "not-a-real-retail-product-9382"}),
            ],
        ),
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
async def test_recommendation_is_sellable_and_terminal_in_one_llm_call() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("recommend_products", {"query": "MILK01"}),
            ],
        ),
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
    assert observation.response_mode == "DETERMINISTIC_RECOMMENDATION"
    assert observation.terminal is True


@pytest.mark.asyncio
async def test_lookup_tool_choice_is_not_overridden_by_message_substrings() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call("search_products", {"query": "MILK01", "in_stock": False}),
            ],
        ),
        GroqChatResult("The product exists in the catalog.", "qwen/qwen3.8-27b"),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Không cần gợi ý, chỉ tra mặt hàng hết hạn MILK01."}],
        SessionState.BROWSING,
    )

    assert groq.calls == 2
    assert result.observations[0].tool_name == "search_products"
    assert [product.product_id for product in result.observations[0].products] == ["MILK01"]
