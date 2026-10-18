from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

import backend.services.retail_tools as retail_tools
from backend.api.main import (
    _fact_product_query,
    _foreign_currency_budget_requested,
    _implicit_followup_fields,
    _interaction_analytics_requested,
    _price_constraint_from_text,
    _product_ranking_requested,
    _recommendation_tool_arguments,
    _requested_snapshot_fields,
    _store_service_policy_requested,
    _system_prompt,
    _unparsed_recommendation_budget_requested,
    _unsupported_action_claim,
    _unsupported_catalog_attribute_requested,
    app,
    get_groq_service,
)
from backend.products.repository import DemoProductRepository
from backend.services.groq import GroqService
from backend.services.retail_tools import execute_catalog_tool


class NoCallGroqService(GroqService):
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        self.calls += 1
        raise AssertionError("This English request should be resolved without the LLM.")


class WrongPriceGroqService(GroqService):
    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        return "Coca-Cola Zero 330ml costs 99 VND."


@pytest.fixture(autouse=True)
def clear_app_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("How much does Coca Zero cost?", ("price_vnd",)),
        ("Is Coca Zero in stock?", ("stock",)),
        ("Where can I find milk?", ("shelf",)),
        ("Are there any promotions on Coca Zero?", ("promotion",)),
        ("What is the expiry date of this milk?", ("expiry_at",)),
        ("What are the ingredients in this snack?", ("ingredients",)),
        ("How spicy is the noodle cup?", ("spicy",)),
        ("Does Coca Zero contain peanuts?", ()),
        ("How many calories are in this drink?", ()),
    ],
)
def test_english_catalog_fact_intents(text: str, expected: tuple[str, ...]) -> None:
    assert _requested_snapshot_fields(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "What does Coca Zero do?",
        "Is this safe for someone with a peanut allergy?",
        "Is this suitable for a pregnant customer?",
        "Can children eat this?",
        "Is this sugar free?",
    ],
)
def test_english_unsupported_product_claims_are_rejected(text: str) -> None:
    assert _unsupported_catalog_attribute_requested(text)


def test_english_recommendations_keep_category_budget_and_spice_constraints() -> None:
    assert _recommendation_tool_arguments("Recommend a non-spicy snack under 20,000 VND.") == {
        "category": "snack",
        "price_constraint": {"operator": "LT", "value_vnd": 20_000},
        "spicy": False,
    }
    assert _recommendation_tool_arguments("I'm looking for coffee under 20k VND.") == {
        "category": "drink",
        "query": "coffee",
        "price_constraint": {"operator": "LT", "value_vnd": 20_000},
    }
    assert _recommendation_tool_arguments(
        "Actually, up to 15 thousand VND.", "Recommend a snack."
    ) == {
        "category": "snack",
        "price_constraint": {"operator": "LTE", "value_vnd": 15_000},
    }
    assert _price_constraint_from_text("Recommend a snack under 20,000") == {
        "operator": "LT",
        "value_vnd": 20_000,
    }
    assert _foreign_currency_budget_requested("Recommend a drink under $1.")
    assert _unparsed_recommendation_budget_requested("Recommend a drink under 10.")


def test_english_search_terms_are_normalized_for_vietnamese_catalog() -> None:
    result = execute_catalog_tool(
        "search_products",
        '{"query":"Where can I find milk?"}',
        repository=DemoProductRepository(),
    )

    assert result.status == "SUCCESS"
    assert [product.product_id for product in result.products] == ["MILK01"]
    assert _fact_product_query("Where can I find Coca-Cola Zero?") == "coca cola zero"


def test_english_followups_and_ranking_requests_are_recognized() -> None:
    assert _implicit_followup_fields("What about the price?") == ("price_vnd",)
    assert _product_ranking_requested("Which snack is the best-selling?")
    assert _recommendation_tool_arguments("Don't recommend anything.") is None


def test_english_store_policy_requests_are_flagged_and_prompt_requires_abstention() -> None:
    assert _store_service_policy_requested("How do I earn loyalty points?")
    assert _interaction_analytics_requested("Which products are asked about most?")
    prompt = _system_prompt("no loyalty policy supplied", "en")
    assert "Store policies" in prompt
    assert "Do not invent an answer" in prompt


def test_english_catalog_freshness_warning_is_not_mistaken_for_a_write_claim() -> None:
    assert not _unsupported_action_claim("Store prices and stock may have changed.", ())
    assert _unsupported_action_claim("The Coca Zero price was changed to 1,000 VND.", ())


@pytest.mark.asyncio
async def test_english_recommendation_returns_catalog_items_with_hard_filters(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: DemoProductRepository())
    groq = NoCallGroqService()
    app.dependency_overrides[get_groq_service] = lambda: groq
    created = await client.post("/sessions", json={"kiosk_id": f"english-{uuid4()}"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Recommend a non-spicy snack under 20,000 VND.",
            "language": "en",
        },
    )

    assert response.status_code == 200
    assert response.json()["grounded_product_ids"] == ["SNACK01"]
    assert response.json()["tools_used"] == ["recommend_products"]
    assert "Snack khoai tây vị tự nhiên" in response.json()["text"]
    assert "18.000 VND" in response.json()["text"]
    assert groq.calls == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_english_price_answer_is_replaced_with_verified_catalog_fact(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: DemoProductRepository())
    app.dependency_overrides[get_groq_service] = lambda: WrongPriceGroqService()
    created = await client.post("/sessions", json={"kiosk_id": f"english-{uuid4()}"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "How much does Coca-Cola Zero cost?",
            "language": "en",
        },
    )

    assert response.status_code == 200
    assert response.json()["grounded_product_ids"] == ["COKE_ZERO_330"]
    assert set(response.json()["tools_used"]) == {"search_products", "get_product_snapshot"}
    assert "price 13.000 VND" in response.json()["text"]
    assert "99 VND" not in response.json()["text"]
    await client.aclose()


@pytest.mark.asyncio
async def test_english_loyalty_question_abstains_without_calling_the_model(
    client: httpx.AsyncClient,
) -> None:
    groq = NoCallGroqService()
    app.dependency_overrides[get_groq_service] = lambda: groq
    created = await client.post("/sessions", json={"kiosk_id": f"english-{uuid4()}"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "How do I earn loyalty points?",
            "language": "en",
        },
    )

    assert response.status_code == 200
    assert "don't have verified information" in response.json()["text"]
    assert response.json()["tools_used"] == []
    assert groq.calls == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_english_foreign_currency_budget_is_clarified_without_dropping_the_filter(
    client: httpx.AsyncClient,
) -> None:
    groq = NoCallGroqService()
    app.dependency_overrides[get_groq_service] = lambda: groq
    created = await client.post("/sessions", json={"kiosk_id": f"english-{uuid4()}"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Recommend a drink under $1.",
            "language": "en",
        },
    )

    assert response.status_code == 200
    assert "uses VND" in response.json()["text"]
    assert response.json()["tools_used"] == []
    assert groq.calls == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_english_interaction_analytics_abstains_without_data(
    client: httpx.AsyncClient,
) -> None:
    groq = NoCallGroqService()
    app.dependency_overrides[get_groq_service] = lambda: groq
    created = await client.post("/sessions", json={"kiosk_id": f"english-{uuid4()}"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Which products are asked about most?",
            "language": "en",
        },
    )

    assert response.status_code == 200
    assert "don't have verified" in response.json()["text"]
    assert response.json()["tools_used"] == []
    assert groq.calls == 0
    await client.aclose()
