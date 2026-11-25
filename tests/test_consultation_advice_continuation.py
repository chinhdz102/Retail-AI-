"""Real text-probe failures: choose requests retain purpose; no search can still advise."""

import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReply,
    GroundedSelection,
    Pick,
    _accept_default_only_json,
    _advice_followup,
    _ground_search_identities,
    _render,
    _render_selection,
    consult_customer,
)
from backend.services.sessions import InMemorySessionStore


@pytest.mark.parametrize("text", [
    "Tư vấn chọn giúp t mấy món cần nhất đi",
    "Chọn giúp mình vài món phù hợp nhé",
    "Vậy gợi ý rồi chọn giúp mình",  # conjunction is handled by planning
    "Tư vấn cho mình, mình chưa biết chọn thế nào",
    "Chọn mấy loại này cho mình đi",
    "Tư vấn giúp mình vì sao nên chọn loại này.",
    "Tại sao chọn sản phẩm đó?",
    "Vì sao nên mua cái vừa rồi?",
    "Giải thích cho em lý do gì chọn loại đó nhé",
])
def test_grammatical_advice_continuation_has_no_new_scope(text):
    if text == "Vậy gợi ý rồi chọn giúp mình":
        assert not _advice_followup(text)
    else:
        assert _advice_followup(text)


@pytest.mark.parametrize("text", [
    "Tư vấn chọn sữa cho mình",
    "Chọn giúp mình vài món không đường nhé",
    "Gợi ý vài món dưới 100 nghìn",
    "Tư vấn chọn hai món giúp mình",
    "Chọn loại rẻ nhất đi",
    "Tư vấn mua đồ cho mèo đi",
    "Mình không biết chọn pin AAA thế nào",
    "Tại sao chọn sữa cho trẻ nhỏ?",
    "Tư vấn giúp mình vì sao nên chọn loại không đường",
    "Vì sao nên mua cái này dưới 50 nghìn?",
])
def test_new_product_constraint_or_ranking_still_needs_planning(text):
    assert not _advice_followup(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["Sổ", "Khăn", "Đèn"])
async def test_choose_generic_items_reuses_scope_without_another_planning_call(group):
    row = product(name=f"{group} Nova", path=f"Hàng hóa > Đồ dùng > {group}")
    record = InMemorySessionStore().create("choose-followup")
    record.shopping.consultation = {
        "need": f"mua {group} cho sinh hoạt",
        "constraints": {"searches": [{"category": group}], "max_price_vnd": 90_000},
    }
    model = Model(
        {"picks": [{"candidate": 0}],
         "text": "Mình ưu tiên {name:0} ({price:0}) theo mức tiền mua ban đầu."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Tư vấn chọn giúp t mấy món cần nhất đi", record=record,
        repository=Repository([row]), groq=model,
    )
    assert result.products == [row]
    assert len(model.calls) == 2  # Selection and review; the accepted scope is reused.
    assert result.memory["need"] == record.shopping.consultation["need"]
    assert result.memory["constraints"]["max_price_vnd"] == 90_000
    assert result.memory["pending_question"] is None
    assert model.calls[0][0][-1]["content"] == "Tư vấn chọn giúp t mấy món cần nhất đi"


@pytest.mark.asyncio
async def test_followup_to_general_advice_does_not_force_a_product_purchase():
    row = product(name="Chậu Nova", path="Hàng hóa > Đồ dùng > Chậu")
    record = InMemorySessionStore().create("advice-followup")
    record.shopping.consultation = {
        "need": "Cách chọn đồ để ngâm rửa", "last_action": "advise",
        "constraints": {"searches": [{"category": "Chậu"}]},
    }
    answer = "Bạn xem kích thước chỗ đặt trước, rồi chọn quy cách vừa chỗ đó."
    model = Model({"picks": [], "text": answer}, {"acceptable": True})
    catalog = Repository([row])
    result = await consult_customer(text="Tư vấn cho mình, mình chưa biết chọn thế nào",
        record=record, repository=catalog, groq=model)
    context = json.loads(model.calls[0][0][1]["content"].split("\n", 1)[1])
    assert context["decision"]["action"] == "advise"
    assert context["candidates"] == []
    assert context["retrieval_observation"]["status"] == "not_searched"
    assert not catalog.queries
    assert "dialogue_goal" in context and len(model.calls) == 2
    assert result.text == answer and result.memory["last_action"] == "advise"


@pytest.mark.asyncio
@pytest.mark.parametrize("searches", [[], [{"category": "Chậu"}]])
async def test_unresolved_followup_keeps_purpose_without_forcing_products(searches):
    record = InMemorySessionStore().create("unresolved-followup")
    record.shopping.consultation = {
        "need": "Cách chọn đồ ngâm rửa cho chỗ hẹp", "last_action": "recommend",
        "constraints": {"searches": searches, "max_price_vnd": 40_000},
        "unprocessed_request": {"text": "chọn đồ ngâm rửa cho chỗ hẹp"},
    }
    answer = "Bạn đo chỗ đặt trước, rồi so kích thước và mức tiền mua ban đầu."
    model = Model({"picks": [], "text": answer}, {"acceptable": True})
    result = await consult_customer(
        text="Tư vấn cho mình, mình chưa biết chọn thế nào", record=record,
        repository=Repository([product(name="Chậu Nova", path="Nhà cửa > Đồ dùng > Chậu")]),
        groq=model,
    )
    context = json.loads(model.calls[0][0][1]["content"].split("\n", 1)[1])
    assert context["decision"]["action"] == "advise"
    assert context["selection_contract"]["max_picks"] == 0
    assert context["resolved_need"] == record.shopping.consultation["need"]
    assert result.text == answer and not result.products
    assert result.memory["constraints"]["max_price_vnd"] == 40_000
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["recommend", "compare"])
async def test_general_guidance_is_reviewed_instead_of_replaced_by_generic_scope_question(action):
    row = product(name="Sổ Nova", path="Hàng hóa > Đồ dùng > Sổ")
    guidance = "Nên so cách dùng và mức tiền mua ban đầu trước khi chọn."
    reply = "Mình ưu tiên loại đúng cách dùng hiện tại, rồi so mức tiền mua ban đầu."
    model = Model(
        {"action": action, "need": "chọn đồ sinh hoạt", "guidance": guidance,
         "searches": [], "use_visible_products": False},
        {"acceptable": False, "issue": "Chưa đưa hướng cụ thể cho mục đích khách."},
        {"picks": [], "text": reply},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Mình muốn chọn đồ sinh hoạt sao cho hợp lý", groq=model,
        record=InMemorySessionStore().create("general-direction"), repository=Repository([row]),
    )
    assert result.text == reply
    assert not result.products
    assert len(model.calls) == 4
    assert result.memory["pending_question"] is None
    assert "scope-clarification" not in result.model
    assert "tên loại hàng" not in result.text


@pytest.mark.parametrize("name,text", [
    ("Ngũ cốc hộp 150g",
     "Mình gợi ý {name:0} ({price:0}); chỉ cần đổ ngũ cốc vào bát rồi thêm sữa."),
    ("Sổ A5 80 trang",
     "Nếu chỉ cần ghi chép ngắn, mình ưu tiên {name:0} ({price:0}) theo số trang và giá."),
    ("Khăn 30x30cm",
     "Bạn chỉ cần mua một chiếc {name:0} ({price:0}) trước, rồi cân nhắc có cần thêm."),
    ("Thịt bò 300g",
     "Bạn xem {name:0} ({price:0}); có thể cắt mỏng để chuẩn bị và nấu chín kỹ trước khi ăn."),
])
def test_ordinary_instructions_are_not_rejected_by_generic_chi_can_phrase(name, text):
    row = product(name=name)
    answer, rows = _render(
        ConsultationReply(text=text, picks=[Pick(candidate=0)]),
        [row], ConsultationDecision(action="recommend"),
    )
    assert rows == [row] and name in answer


@pytest.mark.parametrize("claim", [
    "mở ra là dùng được",
    "mở gói ra ăn ngay",
    "mang theo ăn trực tiếp trên đường",
    "cầm đi ăn luôn trên đường, không cần thìa",
    "chỉ cần hấp lại là dùng được ngay",
    "không cần lắp ráp",
])
@pytest.mark.parametrize("name", ["Mặt hàng A", "Mặt hàng B"])
def test_package_identity_cannot_establish_direct_use_even_if_reviewer_would_approve(name, claim):
    with pytest.raises(ValueError, match="Unsupported immediate-use"):
        _render(
            ConsultationReply(text=f"Mình gợi ý {{name:0}} ({{price:0}}), bạn {claim}.",
                              picks=[Pick(candidate=0)]),
            [product(name=name)], ConsultationDecision(action="recommend"),
        )


@pytest.mark.parametrize("text", [
    "Bạn xem {name:0} ({price:0}); đừng mặc định mở ra ăn ngay, hãy đọc hướng dẫn chuẩn bị.",
    "Bạn xem {name:0} ({price:0}); đừng ăn trực tiếp khi chưa kiểm tra hướng dẫn sử dụng.",
    "Bạn xem {name:0} ({price:0}); nấu chín kỹ trước rồi ăn ngay để tránh để lâu ngoài trời.",
    "Bạn nên chọn loại mở ra ăn ngay nếu không có chỗ nấu tại nơi làm việc.",
])
def test_readiness_warnings_and_general_buying_criteria_remain_valid(text):
    picks = [Pick(candidate=0)] if "{name:" in text else []
    answer, _ = _render(
        ConsultationReply(text=text, picks=picks), [product()],
        ConsultationDecision(action="recommend"),
    )
    assert answer


@pytest.mark.parametrize("quantity", ["0.2 kg", "1.5 lít", "2.25 mét"])
def test_one_question_can_include_decimal_measurement(quantity):
    question = f"Bạn cần mua khoảng {quantity} hay một quy cách khác?"
    selection = GroundedSelection(
        picks=[{"candidate": 0}], question=question,
        pending_question={"slot_key": "size", "question": question},
    )
    answer, rows = _render_selection(
        selection, [product()], ConsultationDecision(action="recommend"), {},
    )
    assert rows and question in answer and answer.count("?") == 1


@pytest.mark.parametrize("question", [
    "Bạn xem loại này. Bạn cần quy cách nào?",
    "Bạn cần 0.2 kg? Hay muốn loại khác?",
    "Bạn xem loại này! Bạn muốn loại nào?",
])
def test_decimal_fix_does_not_allow_multiple_sentences_or_questions(question):
    with pytest.raises(ValueError, match="single question"):
        _render_selection(
            GroundedSelection(picks=[{"candidate": 0}], question=question),
            [product()], ConsultationDecision(action="recommend"), {},
        )


@pytest.mark.parametrize("claim", [
    "đủ dùng trong vài ngày đầu", "đủ cho hai tuần", "đủ dùng cả tháng", "đủ ba bữa",
    "đủ dùng cho gia đình mới", "đủ cho hai người", "đủ dùng cho việc này",
    "đủ ăn cho cả nhà", "đủ uống cho cả nhóm",
])
def test_pack_price_and_count_do_not_establish_how_long_a_purchase_lasts(claim):
    with pytest.raises(ValueError, match="Unsupported"):
        _render(
            ConsultationReply(text=f"Mình chọn {{name:0}} ({{price:0}}), {claim}.",
                              picks=[Pick(candidate=0)]),
            [product()], ConsultationDecision(action="recommend"),
        )


@pytest.mark.parametrize("category,query,customer,expected", [
    ("Thịt", "thịt bò sườn", "Tư vấn thịt nhúng cho bữa tối", ""),
    ("Khăn", "Khăn Nova cao cấp", "Mình muốn mua khăn", ""),
    ("Đèn", "Đèn Nova chống cận", "Mình cần đèn Nova", "Đèn Nova"),
    ("Sữa", "Sữa TH True Milk", "Mình cần sữa TH", "Sữa TH"),
    ("Pin", "Pin AAA Nova", "Mua pin AAA Nova", "Pin AAA Nova"),
])
def test_inferred_types_do_not_become_unrequested_literal_product_filters(
    category, query, customer, expected,
):
    plan = ConsultationDecision(
        action="recommend", searches=[{"category": category, "query": query}],
    )
    _ground_search_identities(plan, customer)
    assert plan.searches[0].query == expected
    assert plan.searches[0].query_kind == ("identity" if expected else "category")


def test_short_followup_retains_previously_accepted_identity():
    plan = ConsultationDecision(
        action="recommend", searches=[{"category": "Pin", "query": "Nova AAA"}],
    )
    _ground_search_identities(plan, "tư vấn giúp", accepted=["Nova AAA"])
    assert plan.searches[0].query == "Nova AAA"


@pytest.mark.parametrize("value,expected", [
    ({"action": "recommend"}, True),
    ({"action": "recommend", "searches": [{"category": "Sổ"}]}, True),
    ({}, False),
    ({"action": "recommend", "unknown": "extra"}, False),
    ({"action": "recommend", "max_price_vnd": "120000"}, False),
    ({"action": "recommend", "child_snack": 1}, False),
    ({"action": "recommend", "guidance": "x" * 801}, False),
    ({"action": "recommend", "question": None}, True),
    ({"action": "recommend", "searches": [{"query": "Nova"}]}, False),
])
def test_schema_recovery_only_supplies_defaults_and_never_changes_a_provided_value(value, expected):
    assert _accept_default_only_json(ConsultationDecision, json.dumps(value)) is expected


def test_full_locally_valid_object_cannot_be_called_default_omission_recovery():
    complete = ConsultationDecision(action="advise").model_dump_json()
    assert not _accept_default_only_json(ConsultationDecision, complete)
