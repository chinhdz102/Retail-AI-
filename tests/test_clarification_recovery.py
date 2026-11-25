"""Focused contracts for typed clarification recovery and retries."""

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    PendingQuestion,
    _deferred_answer,
    _pending_for,
    _updated_answers,
    _validated_answer,
    consult_customer,
    safe_consult_customer,
)
from backend.services.sessions import InMemorySessionStore


def pending_record():
    record = InMemorySessionStore().create("clarification-regression")
    pending = _pending_for(
        "Bạn dùng ở bàn học hay đầu giường?",
        "đèn đọc sách",
        PendingQuestion(slot_key="usage", scope="đèn đọc sách"),
    )
    record.shopping.consultation = {"need": "đèn đọc sách", "pending_question": pending}
    return record, pending


def answer_for(pending, **updates):
    return {
        "status": "answered",
        "question_id": pending["question_id"],
        "slot_key": pending["slot_key"],
        "value": "bàn học",
        "source_quote": "bàn học",
        **updates,
    }


@pytest.mark.parametrize("quote", ["không biết", "mình không rõ nha", "chưa biết ạ", "."])
def test_matching_nonanswer_quote_cannot_resolve_a_slot(quote):
    record, pending = pending_record()
    decision = ConsultationDecision(
        action="recommend",
        clarification_answer=answer_for(
            pending,
            source_quote=quote,
            value="invented",
        ),
    )
    assert _validated_answer(decision, quote, record.shopping.consultation) is None


@pytest.mark.asyncio
async def test_answer_then_different_question_preserves_both_states():
    record, pending = pending_record()
    next_question = "Bạn muốn chi khoảng bao nhiêu?"
    next_plan = {
        "action": "clarify",
        "need": "đèn đọc sách",
        "question": next_question,
        "pending_question": {"slot_key": "budget", "scope": "đèn đọc sách"},
    }
    result = await consult_customer(
        text="để ở bàn học",
        record=record,
        repository=Repository([product()]),
        groq=Model({**next_plan, "clarification_answer": answer_for(pending)}, next_plan),
    )
    assert result.memory["answered_slots"][0]["value"] == "bàn học"
    assert result.memory["pending_question"]["slot_key"] == "budget"
    assert result.memory["pending_question"]["question"] == result.text == next_question


@pytest.mark.asyncio
async def test_later_turn_repeating_old_answer_replans_without_none_crash():
    record, pending = pending_record()
    record.shopping.consultation.update(
        pending_question=None,
        answered_slots=[
            {
                **pending,
                "value": "bàn học",
                "source_quote": "bàn học",
            }
        ],
    )
    model = Model(
        {
            "action": "clarify",
            "need": "đèn đọc sách",
            "question": pending["question"],
            "pending_question": {"slot_key": "usage", "scope": "đèn đọc sách"},
        },
        {"action": "recommend", "searches": [{"category": "Đèn đọc sách"}]},
        {"picks": [{"candidate": 0}]},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Cho mình xem các lựa chọn",
        record=record,
        repository=Repository([product()]),
        groq=model,
    )
    assert result.products and result.memory["pending_question"] is None
    assert result.memory["answered_slots"][0]["value"] == "bàn học"


@pytest.mark.asyncio
async def test_selector_cannot_reask_a_resolved_slot_with_other_wording():
    record, pending = pending_record()
    record.shopping.consultation.update(
        pending_question=None,
        answered_slots=[
            {
                **pending,
                "value": "bàn học",
                "source_quote": "bàn học",
            }
        ],
    )
    model = Model(
        {"action": "recommend", "searches": [{"category": "Đèn đọc sách"}]},
        {
            "picks": [],
            "question": "Bạn định đặt ở vị trí nào?",
            "pending_question": {"slot_key": "usage", "scope": "đèn đọc sách"},
        },
        {"picks": [{"candidate": 0}]},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Xem loại khác nhé",
        record=record,
        repository=Repository([product()]),
        groq=model,
    )
    assert result.products
    assert "vị trí nào" not in result.text
    assert result.memory["pending_question"] is None


@pytest.mark.asyncio
async def test_successful_retry_uses_saved_answer_and_original_provenance():
    record, pending = pending_record()
    saved = "bàn học, loại 500ml dưới 100 nghìn"
    record.shopping.consultation["unprocessed_answer"] = {
        "text": saved,
        "question_id": pending["question_id"],
        "source_turn_id": "original-turn",
    }
    repo = Repository([product()])
    model = Model(
        {
            "action": "recommend",
            "searches": [{"category": "Đèn đọc sách"}],
            "clarification_answer": answer_for(pending),
        },
        {"picks": [{"candidate": 0}]},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="thử lại",
        turn_id="retry-turn",
        record=record,
        repository=repo,
        groq=model,
    )
    assert result.products
    assert result.memory["constraints"]["required_size"] == "500ml"
    assert repo.queries[0]["max_price_vnd"] == 100_000
    assert result.memory["answered_slots"][0]["source_turn_id"] == "original-turn"
    assert "unprocessed_answer" not in result.memory
    assert model.calls[0][0][-1]["content"] == saved


def test_grounded_correction_replaces_the_earlier_answer():
    record, pending = pending_record()
    record.shopping.consultation.update(
        pending_question=None,
        answered_slots=[
            {
                **pending,
                "value": "đầu giường",
                "source_quote": "đầu giường",
            }
        ],
    )
    decision = ConsultationDecision(action="recommend", clarification_answer=answer_for(pending))
    answer = _validated_answer(decision, "à mình sửa lại, để bàn học", record.shopping.consultation)
    updated = _updated_answers(record.shopping.consultation, answer, False)
    assert len(updated) == 1 and updated[0]["value"] == "bàn học"


@pytest.mark.asyncio
async def test_nested_question_and_original_need_are_not_replaced_with_generic_fallback():
    original = "Mình muốn mua đồ cho góc đọc sách"
    question = "Bạn dùng ở bàn học hay đầu giường?"
    result = await consult_customer(
        text=original,
        record=InMemorySessionStore().create("nested-question"),
        repository=Repository([product()]),
        groq=Model(
            {
                "action": "clarify",
                "pending_question": {
                    "question": question,
                    "slot_key": "usage",
                },
            }
        ),
    )
    assert result.text == question
    assert result.memory["last_question"] == question
    assert result.memory["need"] == original
    assert result.memory["pending_question"]["scope"] == original


@pytest.mark.asyncio
async def test_legacy_pending_migration_does_not_mutate_the_record_before_persistence():
    record = InMemorySessionStore().create("legacy-isolated")
    record.shopping.consultation = {"need": "đèn đọc sách", "last_question": "Bạn cần cỡ nào?"}
    original = dict(record.shopping.consultation)
    result = await consult_customer(
        text="Mình chưa rõ",
        record=record,
        repository=Repository([product()]),
        groq=Model(
            {"action": "clarify", "question": "Bạn muốn chi khoảng bao nhiêu?"},
            {
                "question": "Bạn muốn chi khoảng bao nhiêu?",
                "pending_question": {
                    "slot_key": "budget", "question": "Bạn muốn chi khoảng bao nhiêu?",
                },
            },
            {"acceptable": True},
        ),
    )
    assert record.shopping.consultation == original
    assert result.memory["pending_question"]["question"] == result.text


def test_pending_question_id_is_scoped_and_retry_preserves_raw_answer():
    pending = _pending_for("Bạn dùng loại da nào?", "kem dưỡng", None)
    assert pending["question_id"]
    state = {
        "pending_question": pending,
        "unprocessed_answer": {
            "text": "da nhạy cảm",
            "question_id": pending["question_id"],
            "source_turn_id": "turn-7",
        },
    }
    recovered = _deferred_answer("thử lại", state)
    assert recovered["text"] == "da nhạy cảm"
    assert recovered["source_turn_id"] == "turn-7"


def test_retry_rejects_stale_question_or_non_retry_text():
    pending = _pending_for("Bạn dùng loại da nào?", "kem dưỡng", None)
    state = {
        "pending_question": pending,
        "unprocessed_answer": {"text": "da nhạy cảm", "question_id": "q_stale"},
    }
    assert _deferred_answer("thử lại", state) is None
    assert _deferred_answer("da nhạy cảm", state) is None


def test_unresolved_followup_keeps_previous_question_and_correction_replaces_value():
    prior = [{"question_id": "q1", "slot_key": "skin_type", "value": "da khô"}]
    pending = _pending_for("Bạn dùng loại da nào?", "kem dưỡng", None)
    answered = dict(pending, value="da nhạy cảm", source_quote="da nhạy cảm")
    updated = _updated_answers({"answered_slots": prior}, answered, changed_topic=False)
    assert [item["value"] for item in updated] == ["da khô", "da nhạy cảm"]
    assert (
        _updated_answers({"answered_slots": updated}, dict(pending, value="da dầu"), False)[-1][
            "value"
        ]
        == "da dầu"
    )


def test_topic_switch_drops_old_answered_slots_but_keeps_new_pending_scope():
    pending = _pending_for("Bạn dùng loại da nào?", "kem dưỡng", None)
    updated = _updated_answers(
        {"answered_slots": [{"value": "da khô"}]}, dict(pending, value="da nhạy cảm"), True
    )
    assert len(updated) == 1
    assert updated[0]["value"] == "da nhạy cảm"


@pytest.mark.asyncio
async def test_safe_fallback_keeps_raw_answer_and_source_turn_on_retry(monkeypatch):
    import backend.services.customer_consultation as consultation

    record = InMemorySessionStore().create("fallback-retry")
    pending = _pending_for("Bạn dùng loại da nào?", "kem dưỡng", None)
    record.shopping.consultation = {"pending_question": pending}

    async def unavailable(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(consultation, "consult_customer", unavailable)
    first = await safe_consult_customer(text="da nhạy cảm", turn_id="turn-12", record=record)
    record.shopping.consultation = first.memory
    assert first.products == []
    assert first.memory["unprocessed_answer"]["text"] == "da nhạy cảm"
    assert first.memory["unprocessed_answer"]["source_turn_id"] == "turn-12"

    retry = await safe_consult_customer(text="tiếp", turn_id="turn-13", record=record)
    assert retry.memory["unprocessed_answer"]["text"] == "da nhạy cảm"
    assert retry.memory["unprocessed_answer"]["source_turn_id"] == "turn-12"
