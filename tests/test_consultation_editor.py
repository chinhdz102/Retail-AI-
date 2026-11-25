"""Prose repairs cannot change product decisions or bypass review boundaries."""

import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _render_selection,
    consult_customer,
    safe_consult_customer,
)
from backend.services.sessions import InMemorySessionStore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group,path",
    [
        ("Sổ", "Cửa hàng > Văn phòng > Sổ"),
        ("Khăn", "Cửa hàng > Gia dụng > Khăn"),
        ("Đèn", "Cửa hàng > Điện > Đèn"),
    ],
)
async def test_optional_preference_cannot_block_a_supported_product_scope(group, path):
    row = product(name=f"{group} Nova", path=path)
    model = Model(
        {
            "action": "clarify",
            "need": f"Tìm {group} dùng hằng ngày",
            "question": "Bạn muốn chọn thương hiệu nào?",
            "question_priority": "optional",
            "searches": [{"category": group}],
        },
        {
            "picks": [{"candidate": 0}],
            "text": "Mình ưu tiên {name:0} ({price:0}) cho mức tiền bạn dành.",
        },
        {"acceptable": True},
    )
    result = await consult_customer(
        text=f"Chọn giúp mình {group} dùng hằng ngày, ngân sách khoảng 100 nghìn",
        record=InMemorySessionStore().create("optional"),
        repository=Repository([row]),
        groq=model,
    )
    assert result.products == [row]
    assert result.memory["pending_question"] is None
    assert "thương hiệu nào" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("safety", ["child", "label", "essential"])
async def test_preliminary_choice_gate_preserves_safety_and_identity_questions(safety):
    question = "Bạn cần dùng với thiết bị nào vậy?"
    decision = {
        "action": "clarify",
        "need": "Tìm đồ dùng tương thích",
        "question": question,
        "question_priority": "optional",
        "searches": [{"category": "Đèn đọc sách"}],
    }
    if safety == "child":
        decision["child_snack"] = True
    elif safety == "label":
        decision.update(needs_label_verification=True, label_requirement_quote="an toàn")
    else:
        decision["question_priority"] = "essential"
    repo = Repository([product()])
    result = await consult_customer(
        text="Tìm loại an toàn cho mình",
        record=InMemorySessionStore().create("essential"),
        repository=repo,
        groq=Model(decision),
    )
    assert result.products == [] and result.text == question
    assert not repo.queries


@pytest.mark.asyncio
async def test_editor_can_fix_invalid_alternative_sum_without_another_selection_call():
    rows = [
        product(pid="a", name="Sổ Nova A5", price=20_000, path="Cửa hàng > Văn phòng > Sổ"),
        product(pid="b", name="Sổ Luna A4", price=30_000, path="Cửa hàng > Văn phòng > Sổ"),
    ]
    final = (
        "Mình nghiêng về {name:0} ({price:0}) vì khổ A5 dễ mang theo. "
        "Nếu cần khổ lớn hơn thì có {name:1} ({price:1})."
    )
    model = Model(
        {"action": "recommend", "need": "Sổ mang đi làm", "searches": [{"category": "Sổ"}]},
        {
            "picks": [{"candidate": 0}, {"candidate": 1}],
            "text": "Bạn xem {name:0} và {name:1}; tổng cộng {total} cho hai lựa chọn.",
        },
        {"acceptable": True, "revised_text": final},
    )
    result = await consult_customer(
        text="Tìm sổ mang đi làm cho mình",
        record=InMemorySessionStore().create("edit"),
        repository=Repository(rows),
        groq=model,
    )
    assert len(model.calls) == 3 and result.products == rows
    assert result.memory["selection"]["text"] == final
    assert "50.000đ" not in result.text and "tổng cộng" not in result.text
    review = json.loads(model.calls[2][0][1]["content"].split("\n", 1)[1])
    assert (
        review["local_validation_issue"] and review["selection_contract"]["mode"] == "alternatives"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revision",
    [
        "Mình chọn {name:99} ({price:99}) vì đây là món khác tốt hơn.",
        "Mình chọn {name:0}, loại này trị dứt điểm mụn cho bạn nhé.",
        "Mình chọn {name:0}, giá chỉ 1.000đ cho sản phẩm này nhé.",
        "Mình chọn {name:0}. Bạn thích thương hiệu nào vậy?",
        "Mình chọn {name:0}. Tổng cộng {total} cho lựa chọn này nhé.",
    ],
)
async def test_approved_editor_revision_still_has_to_pass_local_validation(revision):
    class NoMoreCalls(Model):
        async def chat(self, *args, **kwargs):
            if len(self.calls) >= 3:
                from backend.services.groq import GroqProviderError

                raise GroqProviderError("unavailable after review")
            return await super().chat(*args, **kwargs)

    model = NoMoreCalls(
        {"action": "recommend", "searches": [{"category": "Đèn đọc sách"}]},
        {"picks": [{"candidate": 0}], "text": "Mình chọn {name:0} ({price:0}) theo giá bạn đưa."},
        {"acceptable": True, "revised_text": revision},
    )
    result = await safe_consult_customer(
        text="Chọn đèn giúp mình dưới 100 nghìn",
        record=InMemorySessionStore().create("reject"),
        repository=Repository([product()]),
        groq=model,
    )
    assert result.model == "consultation-fallback" and result.products == []
    assert "Nova" not in result.text and revision not in result.text


@pytest.mark.asyncio
async def test_editor_cannot_override_a_wrong_function_product_check():
    class EndAfterReview(Model):
        async def chat(self, *args, **kwargs):
            if len(self.calls) >= 3:
                from backend.services.groq import GroqProviderError

                raise GroqProviderError("unavailable")
            return await super().chat(*args, **kwargs)

    model = EndAfterReview(
        {"action": "recommend", "searches": [{"category": "Đèn đọc sách"}]},
        {"picks": [{"candidate": 0}], "text": "Mình chọn {name:0} ({price:0}) cho bạn."},
        {
            "acceptable": True,
            "revised_text": "Mình vẫn chọn {name:0} ({price:0}) cho bạn.",
            "product_checks": [
                {
                    "candidate": 0,
                    "name_quote": "Đèn",
                    "actual_type": "Đèn",
                    "fits_need": False,
                    "claims_supported": True,
                }
            ],
        },
    )
    result = await safe_consult_customer(
        text="Tìm đồ phù hợp cho mình",
        record=InMemorySessionStore().create("wrong-function"),
        repository=Repository([product()]),
        groq=model,
    )
    assert result.products == [] and result.model == "consultation-fallback"


def test_alternatives_are_individually_budgeted_and_never_totalled():
    rows = [product(pid="a", price=80_000), product(pid="b", price=90_000)]
    plan = ConsultationDecision(action="recommend", total_budget_vnd=100_000)
    text, products = _render_selection(
        GroundedSelection(
            picks=[{"candidate": 0}, {"candidate": 1}],
            text="Mình chọn {name:0} ({price:0}); phương án kia là {name:1} ({price:1}).",
        ),
        rows,
        plan,
        {},
    )
    assert products == rows and "170.000đ" not in text and "Tổng" not in text
    with pytest.raises(ValueError, match="alternatives"):
        _render_selection(
            GroundedSelection(
                picks=[{"candidate": 0}], text="Mình chọn {name:0}; tổng cộng {total} nhé."
            ),
            rows,
            plan,
            {},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["Sổ", "Khăn", "Đèn"])
@pytest.mark.parametrize("approval", [False, True])
async def test_rejected_editor_prose_is_not_an_authoritative_retry_answer(group, approval):
    row = product(name=f"{group} Nova", path=f"Cửa hàng > Đồ dùng > {group}")
    rejected = "Mình chọn {name:0} vì loại này có độ bền vượt trội."
    final = "Mình nghiêng về {name:0} ({price:0}) theo tiền mua ban đầu."
    model = Model(
        {"action": "recommend", "searches": [{"category": group}]},
        {"picks": [{"candidate": 0}], "text": rejected},
        {
            "acceptable": approval, "revised_text": rejected,
            "next_question": "Bạn có muốn mình tìm tiếp không?",
            "pending_question": {"slot_key": "search_permission"},
            "product_checks": [{
                "candidate": 0, "name_quote": row.name, "actual_type": group,
                "fits_need": True, "claims_supported": False,
                "issue": "Không có bằng chứng về độ bền.",
            }],
        },
        {"picks": [{"candidate": 0}], "text": final},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=f"Chọn {group} giúp mình", groq=model, repository=Repository([row]),
        record=InMemorySessionStore().create("rejected-revision"),
    )
    retry = json.loads(model.calls[3][0][1]["content"].split("\n", 1)[1])
    feedback = retry["review_feedback"]
    assert not feedback["acceptable"]
    assert not feedback["product_checks"][0]["claims_supported"]
    assert "revised_text" not in feedback and "next_question" not in feedback
    assert "pending_question" not in feedback
    assert retry["previous_draft"]["text"] == rejected
    assert result.products == [row] and result.memory["selection"]["text"] == final


@pytest.mark.parametrize("group", ["Sổ", "Khăn", "Đèn"])
@pytest.mark.parametrize("text", [
    "Mình chọn {name:0} ({price:0}). Kèm theo {name:1} ({price:1}) nhé.",
    "Mình chọn {name:0} và {name:1}; bạn nên mua cả hai sản phẩm này.",
    "Mình chọn {name:0} và {name:1}; bạn nên dùng hai sản phẩm này.",
    "Mình chọn {name:0} và {name:1}; kết hợp hai loại này nhé.",
    "Mình chọn {name:0} và {name:1}. Hai sản phẩm kết hợp với nhau nhé.",
])
def test_alternatives_cannot_be_pitched_as_a_joint_purchase(group, text):
    rows = [product(pid=str(i), name=f"{group} {i}") for i in range(2)]
    with pytest.raises(ValueError, match="alternatives"):
        _render_selection(
            GroundedSelection(picks=[{"candidate": 0}, {"candidate": 1}], text=text),
            rows, ConsultationDecision(action="recommend"), {},
        )


@pytest.mark.parametrize("text", [
    "Mình chọn {name:0} ({price:0}); {name:1} ({price:1}) là lựa chọn khác. "
    "Không cần mua cả hai sản phẩm.",
    "Mình chọn {name:0} ({price:0}); {name:1} ({price:1}) là lựa chọn khác. "
    "Cả hai đều thuộc cùng nhóm hàng.",
    "Mình chọn {name:0} ({price:0}); {name:1} ({price:1}) là lựa chọn khác. "
    "Nếu bạn chỉ dùng hai lần thì nên so tiền mua ban đầu.",
])
def test_comparison_negation_and_frequency_do_not_become_copurchases(text):
    rows = [product(pid=str(i)) for i in range(2)]
    answer, products = _render_selection(
        GroundedSelection(picks=[{"candidate": 0}, {"candidate": 1}], text=text),
        rows, ConsultationDecision(action="recommend"), {},
    )
    assert products == rows and answer


def test_explicit_basket_still_allows_joint_purchase_language():
    rows = [product(pid=str(i)) for i in range(2)]
    plan = ConsultationDecision(
        action="recommend", basket=True, searches=[{"category": "Đèn đọc sách"}],
    )
    _, products = _render_selection(
        GroundedSelection(
            picks=[{"candidate": i, "search_index": 0} for i in range(2)],
            text="Mình chọn {name:0} ({price:0}). Kèm theo {name:1} ({price:1}) nhé.",
        ),
        rows, plan, {row.product_id: [0] for row in rows},
    )
    assert products == rows
