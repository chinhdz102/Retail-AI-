"""A purchase must not turn into a field listing when the model omits prose."""

import json

import pytest

from backend.config import Settings
from backend.services.consultation_text import decode_bound_text
from backend.services.customer_consultation import GroundedSelection, _structured
from backend.services.groq import GroqChatResult, GroqService


@pytest.mark.parametrize("parts", [[], [{"candidate": None, "explanation": "  "}]])
def test_selected_product_without_conversational_advice_needs_repair(parts):
    raw = json.dumps({"picks": [{"candidate": 0}], "text": parts})
    with pytest.raises(ValueError, match="Selected products need conversational advice"):
        decode_bound_text(raw, "text", [0], basket=False)


def test_question_only_reply_and_no_edit_revision_remain_valid():
    question = {"picks": [], "text": [], "pending_question": {"question": "Bạn tìm loại nào?"}}
    decoded = json.loads(decode_bound_text(json.dumps(question), "text", [], basket=False))
    assert decoded["text"] == ""
    assert json.loads(decode_bound_text('{"revised_text": []}', "revised_text", [],
                                       basket=False))["revised_text"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("has_candidates", [False, True])
async def test_native_candidate_selection_requires_a_nonempty_prose_block(
    monkeypatch, has_candidates,
):
    service = GroqService(Settings(_env_file=None, groq_api_key="offline"))

    async def respond(messages, **kwargs):
        text = kwargs["schema"]["properties"]["text"]
        assert (text.get("minItems") == 1) is has_candidates
        assert (text["items"]["properties"]["explanation"].get("minLength") == 1) is has_candidates
        return GroqChatResult(json.dumps({"picks": [], "text": [{"candidate": None,
            "explanation": "Bạn xem quy cách trước khi chọn nhé."}]}), "offline")

    monkeypatch.setattr(service, "structured_chat", respond)
    await _structured(service, GroundedSelection, "Chọn", {
        "customer": "Tư vấn giúp", "candidates": [{"candidate": 0}] if has_candidates else [],
    })
