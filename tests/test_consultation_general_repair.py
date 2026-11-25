import json

import pytest
from test_customer_consultation import Model

from backend.services.consultation_repair import ordinary_repair_allowed, recover_general_advice
from backend.services.customer_consultation import ConsultationDecision, ConsultationResult
from backend.services.sessions import InMemorySessionStore


@pytest.mark.asyncio
async def test_repair_keeps_customer_constraints_and_reviews_only_new_advice():
    record = InMemorySessionStore().create("repair")
    record.messages = [{"role": "user", "content": "Cần cuốn sổ gọn, không lò xo"}]
    memory = {
        "constraints": {"max_price_vnd": 40000},
        "last_question": "Khổ nào?",
        "unprocessed_request": {"text": "old"},
    }
    original = json.loads(json.dumps(memory))
    text = (
        "Mình ưu tiên gáy may hoặc gáy dán để tránh lò xo. Hãy so số trang và độ dày khi mang theo."
    )
    model = Model({"picks": [], "text": text}, {"acceptable": True})
    result = await recover_general_advice(
        groq=model,
        record=record,
        text="Tư vấn tiếp cho mình",
        plan=ConsultationDecision(action="recommend", need="sổ mang theo"),
        memory=memory,
    )
    assert result.text == text and not result.products
    assert result.memory["constraints"] == memory["constraints"] and memory == original
    assert result.memory["last_action"] == "advise"
    assert "unprocessed_request" not in result.memory
    assert len(model.calls) == 2
    context = json.loads(model.calls[0][0][1]["content"].split("\n", 1)[1])
    assert "không lò xo" in context["recent_customer_turns"][0]
    assert not context["candidates"]


@pytest.mark.asyncio
async def test_independent_rejection_does_not_publish_helpful_sounding_advice():
    model = Model(
        {"text": "Bạn nên chọn loại bề mặt nhỏ để tiện mang theo."},
        {"acceptable": False, "issue": "Unjustified compatibility"},
    )
    with pytest.raises(ValueError, match="independent review"):
        await recover_general_advice(
            groq=model,
            record=InMemorySessionStore().create("repair"),
            text="Chọn giúp",
            plan=ConsultationDecision(action="recommend"),
            memory={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft",
    [
        {
            "text": "Bạn muốn mình tìm thêm loại nào?",
            "question": "Bạn muốn mình tìm thêm loại nào?",
        },
        {"text": "Mình chọn {name:0}.", "picks": [{"candidate": 0}]},
        {"text": "Chọn loại 20.000đ cho gọn."},
        {"text": "Loại này điều trị mụn và kháng viêm."},
    ],
)
async def test_repair_cannot_bypass_purchase_question_price_or_medical_boundary(draft):
    with pytest.raises(ValueError):
        await recover_general_advice(
            groq=Model(draft),
            record=InMemorySessionStore().create("repair"),
            text="Chọn giúp",
            plan=ConsultationDecision(action="recommend"),
            memory={},
        )


@pytest.mark.parametrize(
    "need",
    [
        "cách chọn đồ điều trị mụn",
        "vệ sinh nhà có mèo",
        "pin AAA tương thích với thiết bị",
        "ăn vặt cho trẻ",
        "sữa tắm cho da nhạy cảm",
    ],
)
@pytest.mark.asyncio
async def test_general_recovery_does_not_expand_risky_advice_from_prior_user_turn(need):
    record = InMemorySessionStore().create("risk-boundary")
    record.messages = [{"role": "user", "content": need}]
    model = Model()
    with pytest.raises(ValueError, match="outside ordinary shopping scope"):
        await recover_general_advice(
            groq=model,
            record=record,
            text="tư vấn tiếp",
            plan=ConsultationDecision(action="recommend", need=need),
            memory={},
        )
    assert not model.calls


def test_general_recovery_excludes_baskets_and_required_label_claims():
    for changes in ({"basket": True}, {"needs_label_verification": True}, {"child_snack": True}):
        assert not ordinary_repair_allowed(
            ConsultationDecision(action="recommend", **changes),
            "mua đồ dùng",
        )
    assert ordinary_repair_allowed(
        ConsultationDecision(action="recommend", need="sổ đi học"),
        "dưới 40 nghìn",
    )


@pytest.mark.asyncio
async def test_general_recovery_keeps_the_actual_budget_and_exclusions_through_review():
    model = Model(
        {"text": "Bạn ưu tiên quy cách gọn trong ngân sách 40 nghìn để mang theo."},
        {"acceptable": True},
    )
    plan = ConsultationDecision(
        action="recommend", need="sổ để mang theo", max_price_vnd=40000,
        strict_price_limit=True,
        searches=[{"category": "Sổ", "excluded": ["lò xo"]}],
    )
    result = await recover_general_advice(
        groq=model, record=InMemorySessionStore().create("repair-constraints"),
        text="chọn sổ dưới 40 nghìn", plan=plan,
        memory={"constraints": plan.model_dump()},
    )
    assert "40 nghìn" in result.text and not result.products
    review = json.loads(model.calls[1][0][1]["content"].split("\n", 1)[1])
    assert review["constraints"]["max_price_vnd"] == 40000
    assert review["constraints"]["strict_price_limit"] is True
    assert review["constraints"]["searches"][0]["excluded"] == ["lò xo"]


@pytest.mark.asyncio
async def test_editorial_revision_is_independently_checked_before_publication():
    model = Model(
        {"text": "Mình ưu tiên loại gọn để mang đi."},
        {"acceptable": True, "revised_text": "Mình ưu tiên loại gọn để mang đi học."},
        {"acceptable": False, "issue": "Not appropriate to the actual need"},
    )
    with pytest.raises(ValueError, match="Revised general advice"):
        await recover_general_advice(
            groq=model,
            record=InMemorySessionStore().create("repair"),
            text="chọn sổ",
            plan=ConsultationDecision(action="recommend", need="sổ đi học"),
            memory={},
        )
    assert len(model.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed,expected_repair_timeout", [(31, None), (29, 3)])
async def test_recovery_shares_original_turn_deadline(
    monkeypatch, elapsed, expected_repair_timeout
):
    from backend.services import consultation_repair as repair
    from backend.services import customer_consultation as consultation

    plan = ConsultationDecision(action="recommend", need="sổ đi học")
    clock = iter([0, elapsed])
    monkeypatch.setattr(consultation, "monotonic", lambda: next(clock))

    async def failed(**kwargs):
        kwargs["_progress"].update(plan=plan, memory={"need": plan.need})
        raise ValueError("Draft did not pass validation")

    async def recovered(**kwargs):
        return ConsultationResult(
            "Bạn nên so khổ giấy và số trang trước khi chọn.", [], {}, "repair"
        )

    timeouts = []
    original_wait_for = consultation.asyncio.wait_for

    async def observe_deadline(awaitable, *, timeout):
        timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    model = Model()
    model.supports_bound_consultation = True
    monkeypatch.setattr(consultation, "consult_customer", failed)
    monkeypatch.setattr(repair, "recover_general_advice", recovered)
    monkeypatch.setattr(consultation.asyncio, "wait_for", observe_deadline)
    result = await consultation.safe_consult_customer(
        groq=model,
        record=InMemorySessionStore().create("deadline"),
        text="chọn sổ",
    )
    if expected_repair_timeout is None:
        assert timeouts == [32] and result.model == "consultation-fallback"
    else:
        assert timeouts == [32, expected_repair_timeout] and result.model == "repair"
