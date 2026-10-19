import json

import pytest

from backend.products.catalog import ProductSnapshot, get_product_snapshot
from backend.services.groq import GroqChatResult
from backend.services.retail_agent import (
    _unique_product_match_id,
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
        candidate_query="coca zero",
    )

    assert groq.calls == 2
    assert result.text == ""
    assert result.observations[-1].response_mode == "DETERMINISTIC_FACT"
    assert result.observations[-1].terminal is True


@pytest.mark.asyncio
async def test_timestamp_snapshot_round_trips_through_agent_tool_message() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [tool_call("search_products", {"query": "Coca Zero"})],
        ),
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_snapshot",
                    {
                        "product_id": "COKE_ZERO_330",
                        "fields": ["data_timestamp"],
                    },
                )
            ],
        ),
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero price?"}],
        SessionState.BROWSING,
        candidate_query="coca zero",
    )

    assert groq.calls == 2
    assert result.observations[-1].content["product"]["data_timestamp"] == (
        "2026-08-31T00:00:00+00:00"
    )
    assert result.observations[-1].terminal is True


def test_product_match_requires_specific_words_that_select_one_variant() -> None:
    candidates = (
        ProductSnapshot(
            product_id="LAY_110",
            name="Snack khoai tây Lay's Stax vị tự nhiên lon 110g",
            category="snack",
            price_vnd=35_000,
            stock=4,
        ),
        ProductSnapshot(
            product_id="LAY_150",
            name="Khoai tây Lay's vị tự nhiên classic 150g",
            category="snack",
            price_vnd=33_000,
            stock=4,
        ),
    )

    assert (
        _unique_product_match_id(
            "snack khoai tay lay s stax vi tu nhien lon 110g",
            candidates,
        )
        == "LAY_110"
    )
    assert _unique_product_match_id("lay s", candidates) is None
    assert _unique_product_match_id("lay s", (candidates[0],)) is None
    assert _unique_product_match_id(
        "lay s stax 110g",
        (candidates[0],),
    ) == "LAY_110"


def test_product_match_uses_verified_unit_when_name_uses_packaging_abbreviation() -> None:
    candidates = (
        ProductSnapshot(
            product_id="ZERO_600", name="Nước Giải Khát Coca-Cola Zero Pet 600ml",
            category="drink", price_vnd=9_900, stock=5, uom="Chai",
        ),
        ProductSnapshot(
            product_id="ZERO_1500", name="Coca-Cola Zero 1500ml",
            category="drink", price_vnd=15_900, stock=5, uom="Chai",
        ),
    )
    assert _unique_product_match_id("coca zero chai 600ml", candidates) == "ZERO_600"
    assert _unique_product_match_id("coca zero chai", candidates) is None


def test_repeated_bim_bim_phrase_can_match_a_single_verified_snack() -> None:
    candidates = (
        ProductSnapshot(
            product_id="BIMBIM_280",
            name="Bim bim Poppedcorn quẩy soắn 280g",
            category="Bánh Snack",
            price_vnd=57_500,
            stock=3,
        ),
    )

    assert _unique_product_match_id("bim bim", candidates) == "BIMBIM_280"


def test_product_match_uses_product_alias_with_a_flavor_modifier() -> None:
    snack = get_product_snapshot("SNACK01")

    assert snack is not None
    assert _unique_product_match_id("bim bim vi tu nhien", (snack,)) == "SNACK01"


@pytest.mark.asyncio
async def test_snapshot_tool_cannot_read_an_unsearched_or_unmatched_product() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "get_product_snapshot",
                    {"product_id": "COKE_ZERO_330", "fields": ["price_vnd"]},
                )
            ],
        )
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Coca Zero price?"}],
        SessionState.BROWSING,
        candidate_query="pepsi zero",
    )

    assert result.observations[0].status == "DENIED"
    assert result.products == ()
    assert result.tools_used == ()


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
async def test_explicit_natural_category_is_enforced_if_model_omits_it() -> None:
    groq = ScriptedGroq(
        GroqChatResult(
            "",
            "qwen/qwen3.8-27b",
            [
                tool_call(
                    "recommend_products",
                    {
                        "price_constraint": {"operator": "LT", "value_vnd": 20000},
                        "spicy": False,
                        "limit": 3,
                    },
                ),
            ],
        )
    )

    result = await run_retail_agent(
        groq,
        [{"role": "user", "content": "Tìm món ăn vặt không cay dưới hai chục nghìn."}],
        SessionState.BROWSING,
    )

    assert [product.product_id for product in result.products] == ["SNACK01"]
    assert "category" in result.observations[0].content["items"][0]["matched_constraints"]


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
