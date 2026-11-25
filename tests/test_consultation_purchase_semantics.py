"""A partial lexical parse must not erase a multi-purpose semantic purchase."""

import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import consult_customer
from backend.services.sessions import InMemorySessionStore


@pytest.mark.asyncio
@pytest.mark.parametrize("customer,groups", [
    (
        "Cần đồ rửa chén và lau bếp, khoảng 100 nghìn thôi, nhà có khăn lau rồi.",
        ["Nước rửa chén bát", "Nước tẩy rửa nhà bếp"],
    ),
    (
        "Mua sổ và bút để ghi chi tiêu, khoảng 100 nghìn, chọn giúp mấy món cần nhất.",
        ["Sổ", "Bút bi"],
    ),
])
async def test_partial_type_parse_preserves_joint_purchase_total_and_roles(customer, groups):
    repo = Repository([
        product(pid=str(i), name=group, price=10_000, path=f"Cửa hàng > Đồ dùng > {group}")
        for i, group in enumerate(groups)
    ])
    model = Model(
        {"action": "recommend", "basket": True, "total_budget_vnd": 100_000,
         "searches": [{"category": group, "role": group} for group in groups]},
        {"picks": [{"candidate": i, "search_index": i} for i in range(2)],
         "text": "Mình chọn {name:0} và {name:1} trước, tổng cộng {total}."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=customer, repository=repo, groq=model,
        record=InMemorySessionStore().create("joint-purchase"),
    )
    assert result.memory["constraints"]["basket"] is True
    assert result.memory["constraints"]["total_budget_vnd"] == 100_000
    assert len(result.products) == 2 and "20.000đ" in result.text
    selection_context = json.loads(model.calls[1][0][1]["content"].split("\n", 1)[1])
    assert selection_context["selection_contract"]["mode"] == "basket"
    assert len(selection_context["decision"]["searches"]) == 2


@pytest.mark.asyncio
async def test_general_advice_reuses_a_reviewed_direction_without_a_duplicate_realizer_call():
    guidance = (
        "Nếu cần mang theo ghi chi tiêu, mình ưu tiên sổ khổ nhỏ. "
        "Bạn đo thử chỗ để trong túi rồi so với kích thước cuốn sổ trước khi chọn."
    )
    model = Model(
        {"action": "advise", "need": "chọn sổ mang theo", "guidance": guidance},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Tư vấn cách chọn sổ để mang theo, chưa cần chọn hãng",
        repository=Repository([product(name="Sổ A6", path="Cửa hàng > Đồ dùng > Sổ")]),
        groq=model, record=InMemorySessionStore().create("reviewed-direction"),
    )
    assert result.text == guidance and not result.products
    assert len(model.calls) == 2
    stages = [call[1]["tool_choice"]["function"]["name"] for call in model.calls]
    assert stages == ["ConsultationDecision", "ConsultationReview"]
    assert result.model.endswith(":general-advice-reviewed")


@pytest.mark.asyncio
async def test_native_known_scope_question_cannot_bypass_realization_and_review(monkeypatch):
    from backend.config import Settings
    from backend.services import customer_consultation as consultation
    from backend.services.groq import GroqService

    record = InMemorySessionStore().create("known-purpose")
    record.shopping.consultation = {
        "need": "nước mang theo đi làm dưới 20 nghìn, không cà phê",
        "constraints": {"searches": [{"category": "Nước uống đóng chai"}],
                        "max_price_vnd": 20_000},
    }
    record.messages = [{"role": "user", "content": record.shopping.consultation["need"]}]
    calls = []
    reply = (
        "Nếu muốn không đường, mình ưu tiên nước lọc mang theo. "
        "Bạn so quy cách với chỗ để trong túi nhé."
    )

    async def structured(groq, schema, policy, data):
        calls.append(schema.__name__)
        if schema is consultation.ConsultationDecision:
            return schema(action="clarify", question="Bạn muốn mua loại nước nào?",
                          searches=[{"category": "Nước uống đóng chai"}]), "mock"
        if schema is consultation.GroundedSelection:
            return schema(text=reply), "mock"
        return schema(acceptable=True), "mock"

    monkeypatch.setattr(consultation, "_structured", structured)
    result = await consult_customer(
        text="Mình muốn không đường, giữ các thứ đã loại nhé.", record=record,
        repository=Repository([product(name="Nước lọc 500ml", price=5000,
                                      path="Cửa hàng > Thức uống > Nước uống đóng chai")]),
        groq=GroqService(Settings(_env_file=None, groq_api_key="offline")),
    )
    assert calls == ["ConsultationDecision", "GroundedSelection", "ConsultationReview"]
    assert result.text == reply and "Bạn muốn mua loại nước nào" not in result.text

