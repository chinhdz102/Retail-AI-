"""Decision boundaries use search evidence and dialogue, across unrelated taxonomies."""

import json

import pytest
from test_customer_consultation import Model, Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _advice_followup,
    _facet_context,
    _ground_catalog_scope,
    _native_catalog_category_hint,
    _planner_paths,
    _render_selection,
    _retrieval_observation,
    _sample_candidates,
    consult_customer,
)
from backend.services.retail_tools import resolve_catalog_request_category
from backend.services.sessions import InMemorySessionStore


def context(call):
    return json.loads(call[0][1]["content"].split("\n", 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("group,guidance", [
    ("Sổ", "Mình ưu tiên khổ nhỏ nếu bạn cần mang theo, rồi so số trang và giá."),
    ("Khăn", "Mình sẽ so kích thước và tiền mua ban đầu để chọn một chiếc trước."),
    ("Pin", "Bạn nên đối chiếu cỡ pin của thiết bị trước khi mua vỉ thay thế."),
])
async def test_realization_and_review_keep_planner_direction_and_bounded_search(group, guidance):
    row = product(name=f"{group} Nova", path=f"Cửa hàng > Đồ dùng > {group}")
    model = Model(
        {"action": "recommend", "need": f"chọn {group}", "guidance": guidance,
         "searches": [{"category": group}]},
        {"picks": [{"candidate": 0}],
         "text": "Mình nghiêng về {name:0} ({price:0}) theo tiền mua ban đầu."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=f"Chọn {group} giúp mình", groq=model, repository=Repository([row]),
        record=InMemorySessionStore().create("direction"),
    )
    assert result.products == [row]
    assert "planner_guidance" not in context(model.calls[1])
    # The independent review gets the shopper, draft and evidence, rather than
    # being primed again by the planner's unverified conclusions.
    assert "planner_guidance" not in context(model.calls[2])
    for call in model.calls[1:]:
        data = context(call)
        assert data["retrieval_observation"]["candidate_count"] == 1
        assert not data["retrieval_observation"]["exhaustive"]


def test_empty_search_and_unperformed_search_remain_distinct_without_absence_claims():
    plan = ConsultationDecision(action="advise")
    assert _retrieval_observation(plan, [])["status"] == "not_searched"
    plan = ConsultationDecision(action="advise", searches=[{"category": "Sổ"}])
    observation = _retrieval_observation(plan, [])
    assert observation["status"] == "no_candidates"
    assert not observation["exhaustive"] and not observation["stock_verified"]


@pytest.mark.parametrize("reply", [
    "Hiện hệ thống không tìm thấy sản phẩm phù hợp với nhu cầu của bạn.",
    "Cửa hàng không bán nhóm sản phẩm mà bạn cần mua hôm nay.",
    "Bên mình chỉ có các nhóm bạn đã xem trong danh sách này.",
    "Nhóm hàng này không nằm trong danh mục hiện có của cửa hàng.",
])
def test_bounded_retrieval_cannot_be_turned_into_store_absence(reply):
    with pytest.raises(ValueError):
        _render_selection(
            GroundedSelection(text=reply), [], ConsultationDecision(action="advise"), {},
        )


@pytest.mark.parametrize("with_text", [False, True])
def test_permission_to_search_is_rejected_even_when_the_question_is_typed(with_text):
    reply = GroundedSelection(
        text=(
            "Mình có thể tìm thêm sản phẩm bôi điều trị mụn. Bạn có muốn mình tìm "
            "sản phẩm bôi điều trị mụn không?"
        ) if with_text else "",
        question="Bạn có muốn mình tìm sản phẩm bôi điều trị mụn không?",
        pending_question={
            "slot_key": "treatment_search", "scope": "product_type",
            "question": "Bạn có muốn mình tìm sản phẩm bôi điều trị mụn không?",
        },
    )
    with pytest.raises(ValueError, match="permission"):
        _render_selection(reply, [], ConsultationDecision(action="recommend"), {})


@pytest.mark.parametrize("pronoun", ["mình", "tôi", "em", "anh", "chị"])
def test_permission_question_is_rejected_across_staff_address_forms(pronoun):
    question = f"Bạn có muốn {pronoun} tìm sản phẩm để so sánh không?"
    with pytest.raises(ValueError, match="permission"):
        _render_selection(
            GroundedSelection(question=question), [],
            ConsultationDecision(action="compare"), {},
        )


@pytest.mark.parametrize("text,expected", [
    ("Mình chưa biết chọn Sổ thế nào", "Sổ"),
    ("Mình muốn xem số món đã mua", None),
    ("Mình mang đồ đi làm", None),
    ("minh chon so", "Sổ"),
    ("Mình cần sổ tay", "Sổ tay"),
    ("Mình cần sổ và khăn", None),
])
def test_taxonomy_hint_keeps_accents_and_does_not_collapse_multiple_types(text, expected):
    paths = tuple(("Hàng hóa", "Đồ dùng", group) for group in ["Sổ", "Sổ tay", "Khăn", "Măng"])
    assert _native_catalog_category_hint(text, paths) == expected


def test_generic_product_nouns_do_not_rank_food_while_seafood_words_still_match():
    paths = (("Cửa hàng", "Thực phẩm", "Thủy hải sản"), ("Cửa hàng", "Đồ dùng", "Sổ"))
    assert _facet_context(paths, "Có sản phẩm nào không?") == ""
    assert "Thủy hải sản" in _facet_context(paths, "Mua thủy hải sản")


def test_focused_planner_context_retains_siblings_and_unscoped_tasks_keep_full_tree():
    paths = (
        ("Cửa hàng", "Chăm sóc", "Rửa mặt", "Dạng nước"),
        ("Cửa hàng", "Chăm sóc", "Rửa mặt", "Dạng gel"),
        ("Cửa hàng", "Đồ dùng", "Sổ"),
        ("Cửa hàng", "Đồ dùng", "Đèn"),
    )
    focused = _planner_paths(paths, "Rửa mặt", "Mình muốn rửa mặt")
    assert set(focused) == set(paths[:2])
    assert _planner_paths(paths, None, "Mình muốn mua đồ") == paths


def test_question_in_prose_cannot_skip_typed_dialogue_state():
    with pytest.raises(ValueError, match="typed question"):
        _render_selection(
            GroundedSelection(text="Bạn dùng với thiết bị nào để mình đối chiếu cỡ cho đúng?"),
            [], ConsultationDecision(action="advise"), {},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["Sổ", "Khăn", "Đèn"])
@pytest.mark.parametrize("guidance", ["", "Mình sẽ so loại phổ thông và tiền mua ban đầu."])
async def test_known_family_is_searched_before_a_blocking_preference_question(group, guidance):
    row = product(name=f"{group} Nova", path=f"Cửa hàng > Đồ dùng > {group}")
    model = Model(
        {"action": "clarify", "need": f"chọn {group}",
         "guidance": guidance,
         "question": "Bạn muốn chọn dạng sản phẩm chuyên dụng nào?"},
        {"picks": [{"candidate": 0}],
         "text": "Mình nghiêng về {name:0} ({price:0}) theo tiền mua ban đầu."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=f"Mình chưa biết chọn {group} thế nào", groq=model, repository=Repository([row]),
        record=InMemorySessionStore().create("planning-repair"),
    )
    assert result.products == [row] and len(model.calls) == 3
    assert context(model.calls[1])["retrieval_observation"]["status"] == "candidates"
    assert context(model.calls[1])["decision"]["searches"][0]["category"] == group
    assert result.memory["pending_question"] is None


@pytest.mark.parametrize("group", ["Sổ", "Khăn", "Đèn", "Ống nối"])
def test_scope_seeding_uses_active_taxonomy_labels_not_a_use_case_playbook(group):
    row = product(name=f"{group} Nova", path=f"Cửa hàng > Đồ dùng > {group}")
    repo = Repository([row])
    category = resolve_catalog_request_category(f"Mình cần mua {group}", repo, canonical=True)
    assert category == group
    plan = ConsultationDecision(action="clarify", question="Bạn muốn hãng nào?")
    assert _ground_catalog_scope(plan, category, has_visible=False)
    assert plan.action == "recommend" and plan.searches[0].category == group


def test_seeded_retrieval_does_not_erase_an_essential_compatibility_question():
    question = "Thiết bị dùng cỡ nào, bạn xem ký hiệu trên linh kiện cũ giúp mình nhé?"
    plan = ConsultationDecision(
        action="clarify", question=question,
        pending_question={"slot_key": "size", "scope": "compatibility", "question": question},
        max_price_vnd=90_000, strict_price_limit=True,
    )
    assert _ground_catalog_scope(plan, "Linh kiện", has_visible=False)
    assert plan.question == question and plan.pending_question.slot_key == "size"
    assert plan.max_price_vnd == 90_000 and plan.strict_price_limit


def test_missing_visible_products_cannot_prevent_a_known_scope_lookup():
    plan = ConsultationDecision(action="recommend", use_visible_products=True)
    assert _ground_catalog_scope(plan, "Sổ", has_visible=False)
    assert not plan.use_visible_products and plan.searches[0].category == "Sổ"


def test_scope_seeding_preserves_an_existing_multi_role_plan_and_unknown_identity():
    plan = ConsultationDecision(action="recommend", searches=[{"category": "Khăn"}])
    assert not _ground_catalog_scope(plan, "Sổ", has_visible=False)
    assert plan.searches[0].category == "Khăn"
    plan = ConsultationDecision(action="clarify", question="Bạn đang nói tới món gì?")
    assert not _ground_catalog_scope(plan, None, has_visible=False)
    assert plan.action == "clarify" and not plan.searches


@pytest.mark.asyncio
async def test_reviewer_sees_original_known_context_not_only_planner_summary():
    record = InMemorySessionStore().create("known-context")
    original = "Mình dùng thiết bị loại A, đang tìm linh kiện thay thế."
    record.messages.append({"role": "user", "content": original})
    model = Model(
        {"action": "advise", "need": "tìm linh kiện"},
        {"text": (
            "Bạn đối chiếu mã linh kiện trên món cũ với hướng dẫn thiết bị trước khi mua nhé."
        )},
        {"acceptable": True},
    )
    await consult_customer(
        text="Không rõ mã, tư vấn giúp mình với", record=record,
        repository=Repository([]), groq=model,
    )
    assert context(model.calls[2])["recent_customer_turns"] == [original]


@pytest.mark.parametrize("detail,other", [
    ("đầu nhỏ", "đầu lớn"),
    ("kẻ ngang", "ô ly"),
    ("gối", "gói"),
])
def test_candidate_sampling_preserves_literal_details_and_price_endpoints(detail, other):
    rows = [product(pid=str(i), name=f"Món {other} {i}", price=1000 + i) for i in range(30)]
    rows[13] = product(pid="wanted", name=f"Món {detail}", price=1013)
    selected = _sample_candidates(rows, f"Mình chọn loại {detail}")
    assert rows[13] in selected and rows[0] in selected and rows[-1] in selected
    assert len(selected) == 8 and len({row.product_id for row in selected}) == 8


@pytest.mark.parametrize("text", [
    "Tư vấn cho mình với, mình chưa biết nên chọn thế nào.",
    "Mình không biết loại nào phù hợp máy đâu, tư vấn giúp mình với.",
    "Mình chưa biết cách chọn, bạn gợi ý giúp mình nhé.",
    "Không rõ loại nào. Chọn giúp mình với.",
    "tư vấn cho t với",
])
def test_multiclause_requests_for_advice_keep_the_existing_shopping_purpose(text):
    assert _advice_followup(text)


@pytest.mark.parametrize("text", [
    "Tư vấn cho mình dầu gội với.",
    "Mình không biết chọn pin AA hay AAA.",
    "Chọn giúp mình, dưới 100 nghìn thôi.",
    "Tư vấn cho mình với, không lấy màu đỏ.",
    "Chọn giúp mình hộp dùng được trong lò vi sóng.",
])
def test_new_product_scope_and_constraints_still_reach_semantic_planning(text):
    assert not _advice_followup(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["Sổ", "Khăn", "Đèn"])
async def test_advice_followup_discovers_missing_search_scope(
    group,
):
    row = product(name=f"{group} Nova", path=f"Cửa hàng > Đồ dùng > {group}")
    record = InMemorySessionStore().create("no-search-yet")
    record.shopping.consultation = {
        "need": f"chọn {group}", "constraints": {},
        "last_question": "Bạn muốn dạng sản phẩm nào?",
        "pending_question": {"question_id": "q_type", "slot_key": "type", "scope": group,
                             "question": "Bạn muốn dạng sản phẩm nào?"},
    }
    model = Model(
        {"action": "recommend", "need": f"chọn {group}", "searches": [{"category": group}]},
        {"picks": [{"candidate": 0}],
         "text": "Mình nghiêng về {name:0} ({price:0}) theo tiền mua ban đầu."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Tư vấn cho mình với, mình chưa biết nên chọn thế nào.", groq=model,
        repository=Repository([row]), record=record,
    )
    assert result.products == [row]
    assert model.calls[0][1]["tool_choice"]["function"]["name"] == "ConsultationDecision"
    assert "dialogue_goal" in context(model.calls[0])
    assert "bỏ qua tiêu chí" not in result.text


@pytest.mark.asyncio
async def test_unknown_compatibility_followup_still_plans_a_way_to_identify_it_before_selecting():
    record = InMemorySessionStore().create("compatibility-unknown")
    record.shopping.consultation = {
        "need": "thay pin thiết bị", "constraints": {"searches": [{"category": "Pin"}]},
        "pending_question": {"slot_key": "battery_size", "scope": "Pin",
                             "question": "Thiết bị dùng cỡ pin nào?"},
    }
    guidance = "Bạn mở ngăn pin và xem ký hiệu trên viên cũ hoặc trong ngăn trước khi mua nhé."
    model = Model(
        {"action": "advise", "need": "thay pin thiết bị", "guidance": guidance},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Không biết, tư vấn giúp mình với", record=record,
        repository=Repository([product(name="Pin AAA", path="Cửa hàng > Điện > Pin")]), groq=model,
    )
    assert result.text == guidance and not result.products
    assert model.calls[0][1]["tool_choice"]["function"]["name"] == "ConsultationDecision"
