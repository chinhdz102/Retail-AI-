from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

import backend.api.main as api
from backend.services.query_understanding import non_catalog_question_kind, understand_catalog_query
from backend.services.retail_tools import _catalog_product_identity_phrase


@pytest.mark.parametrize("text", [
    "Cho tao biết hôm nay là thứ mấy.", "Hôm nay thứ nào vậy?",
    "Hôm nay ngày bao nhiêu?", "What day is it today?",
    "What date is it?", "tao đang mệt", "cho tao biết bạn là ai",
    "tao muốn nói chuyện",
])
def test_general_questions_have_no_catalog_identity_even_with_previous_product(text):
    result = understand_catalog_query(text, previous_query="Tìm táo Breeze")
    assert result.intent == "general"
    assert not result.product_reference
    assert not result.product_type
    assert not result.conversation_reference


@pytest.mark.parametrize("text", [
    "táo ở đâu", "tao muốn mua táo", "tim tao", "tao muốn ăn lẩu",
    "Táo này hết hạn ngày nào?", "Khuyến mãi hôm nay đến ngày nào?",
    "tao đang mệt muốn mua nước uống",
])
def test_shopping_and_product_dates_are_not_general_questions(text):
    assert non_catalog_question_kind(text) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("tao đang mệt", "dang met"),
    ("Cho tao biết hôm nay là thứ mấy", "biet hom nay la thu may"),
    ("tao muốn mua táo", "muon mua tao"),
    ("tim tao", "tim tao"), ("táo ở đâu", "tao"),
])
def test_pronouns_are_removed_before_accent_folding_without_erasing_product_nouns(text, expected):
    assert _catalog_product_identity_phrase(text) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("with_product_context", [False, True])
@pytest.mark.parametrize("text", [
    "Cho tao biết hôm nay là thứ mấy.", "What date is it?",
    "tao đang mệt", "tao muốn nói chuyện", "cho tao biết bạn là ai",
])
async def test_calendar_answer_uses_vietnam_date_without_catalog_or_provider(
    monkeypatch, with_product_context, text,
):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC Monday, but already Tuesday in Vietnam.
            return datetime(2026, 11, 2, 18, 30, tzinfo=UTC).astimezone(tz)

    def unexpected(*args, **kwargs):
        raise AssertionError("General questions must not invoke product/provider work")

    monkeypatch.setattr(api, "datetime", FixedDatetime)
    monkeypatch.setattr(api, "get_product_repository", unexpected)
    api.app.dependency_overrides[api.get_groq_service] = lambda: object()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test",
        ) as client:
            created = await client.post("/sessions", json={"kiosk_id": "calendar-regression"})
            session_id = created.json()["session_id"]
            if with_product_context:
                api.session_store.update_shopping(
                    UUID(session_id), selected_product_ids=["APPLE_BREEZE"],
                )
            response = await client.post("/agent/respond", json={
                "session_id": session_id, "text": text,
            })
        assert response.status_code == 200, response.text
        payload = response.json()
        if non_catalog_question_kind(text) == "calendar":
            assert "thứ Ba" in payload["text"]
            assert "03/11/2026" in payload["text"]
        else:
            assert "trợ lý AI" in payload["text"]
        assert payload["tools_used"] == []
        assert payload["products"] == []
        assert payload["model"] == "local-general"
    finally:
        api.app.dependency_overrides.clear()
