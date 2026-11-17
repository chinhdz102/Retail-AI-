"""Regressions reproduced by real customer text audits, not a quality score."""

import json

import httpx
import pytest

from backend.api import main
from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.consultation import (
    Composition, Plan, Search, _canonical_searches, _render_structured,
    _retrieve_candidates, _review_advice, _strip_no_match_boilerplate,
    _diverse_candidates, consult, use_consultation,
)
from backend.services.groq import GroqProviderError
from backend.services.product_terms import equivalent_queries, identity_terms
from backend.services.reference_guidance import relevant_guidance
from backend.services.vietnamese_intent import requested_snapshot_fields
from tests.test_consultation import Repository
from tests.test_consultation_review import RecordedReplies


@pytest.mark.parametrize("text", [
    "Em thấy hai cái này gần giống nhau, sao giá chênh nhiều thế?",
    "Cùng một mức giá thì loại nào dùng thích hơn?",
    "Sao cùng dung tích mà giá chênh lệch vậy?",
])
def test_value_comparisons_are_not_treated_as_price_lookups(text):
    fields = requested_snapshot_fields(text)
    assert use_consultation(text, fields)
    assert main._broad_price_lookup_arguments(text, fields) is None


@pytest.mark.parametrize("text", [
    "Thịt bò bình thường hoặc ba chỉ cũng được, khoảng 200 nghìn.",
    "Tầm 100 nghìn thôi.",
    "Dưới 80 nghìn, lấy loại nhỏ hơn.",
])
def test_budget_and_variant_followups_keep_consultation_context(text):
    fields = requested_snapshot_fields(text)
    assert use_consultation(text, fields)
    assert main._broad_price_lookup_arguments(text, fields) is None


def test_subjective_preferences_do_not_become_impossible_label_filters():
    result = _canonical_searches([Search(query="nước trái cây",
        required_phrases=["không quá ngọt", "không đường"],
        any_phrases=["đỡ ngọt", "không có ga"])])[0]
    assert result.required_phrases == ["không đường"]
    assert result.any_phrases == ["không có ga"]


def test_generic_preparation_is_not_misread_as_a_sku_effect():
    result = _render_structured(Composition(advice=
        "Bạn có thể pha loãng nước trái cây để giảm độ ngọt."), {},
        Plan(need="đồ uống bớt ngọt"), "test")
    assert "pha loãng" in result.text


def test_no_match_removes_waiting_or_search_boilerplate_but_keeps_guidance():
    text = ("Mình chưa tìm được loại đúng. Bạn có thể điều chỉnh tiêu chí hoặc quay lại sau. "
            "Khi chọn thịt, ưu tiên màu hồng tươi và mỡ nạc xen kẽ.")
    result = _strip_no_match_boilerplate(text)
    assert "điều chỉnh" not in result and "quay lại" not in result
    assert "mỡ nạc xen kẽ" in result


def test_diverse_retrieval_keeps_different_product_forms():
    base = Repository().product
    products = [base.model_copy(update={"product_id": str(i), "name": name})
                for i, name in enumerate(("Thịt bò xay 200g", "Thịt bò xay 250g",
                    "Ba chỉ bò cắt lát 200g", "Thăn bò 300g", "Gầu bò cắt lát 400g"))]
    selected = _diverse_candidates(products, "thịt bò", limit=3)
    assert len(selected) == 3
    assert len({"xay" in p.name.lower() for p in selected}) == 2


@pytest.mark.asyncio
async def test_wrong_department_hint_cannot_hide_a_labelled_variant_of_the_same_type():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    question = "Da em khá nhạy cảm, có loại sữa tắm nào dịu nhẹ không?"
    candidates, missing = await _retrieve_candidates(repo,
        Plan(need=question, source_user_text=question,
             searches=[Search(query="sữa tắm", category="Chăm Sóc Cá Nhân Khác")]),
        sorted({p.category for p in repo.products}), [question])
    assert not missing
    assert any("da nhạy cảm" in p.name for p in candidates.values())
    assert all("sữa tắm" in p.name.lower() for p in candidates.values())


@pytest.mark.asyncio
async def test_cross_department_head_match_keeps_the_customers_size_and_variant():
    repo = Repository()
    base = repo.product
    repo.products = tuple(base.model_copy(update={"product_id": str(i), "name": name,
        "category": category}) for i, (name, category) in enumerate([
            ("Nước lau sàn không mùi 1L", "Chất Vệ Sinh"),
            ("Nước lau sàn không mùi 500ml", "Nước Tẩy Rửa"),
            ("Nước lau sàn hương hoa 1L", "Nước Tẩy Rửa"),
            ("Khăn ướt hương nước lau sàn không mùi 1L", "Khăn Ướt"),
        ]))
    repo.search = lambda **_: list(repo.products)
    plan = Plan(need="Nước lau sàn không mùi loại một lít", searches=[Search(
        query="nước lau sàn", category="Nước Tẩy Rửa", required_phrases=["không mùi"],
        package_constraints=[{"dimension": "volume_ml", "value": 1000}])])
    candidates, _ = await _retrieve_candidates(repo, plan,
        ["Nước Tẩy Rửa", "Chất Vệ Sinh", "Khăn Ướt"], [plan.need])
    assert set(candidates) == {"0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_category", [
    ("thịt ăn lẩu", "Thịt"),
    ("rau cho bữa tối", "Rau Lá"),
])
async def test_use_phrase_backoff_keeps_catalog_head_without_hardcoded_sku(
    query, expected_category,
):
    """Use wording must not erase a type that exists in the catalog.

    The expected value is a catalog taxonomy category, not a product name or
    a recipe rule.  This protects broad categories while leaving the model free
    to choose among the returned SKUs.
    """
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    candidates, missing = await _retrieve_candidates(
        repo,
        Plan(need=query, searches=[Search(query=query)], source_user_text=query),
        sorted({p.category for p in repo.products}),
        [query],
    )
    assert not missing and candidates
    assert any(p.category == expected_category for p in candidates.values())
    assert all(p.product_id in {row.product_id for row in repo.products}
               for p in candidates.values())


@pytest.mark.parametrize("claim", [
    "Thiết kế không gây cay mắt khi rửa, giúp giảm cảm giác khó chịu cho da nhạy cảm.",
    "Làm dịu kích ứng sau khi tắm.",
    "Hạn chế ngứa và đỏ rát trên da.",
])
def test_an_eye_claim_cannot_become_a_skin_symptom_relief_claim(claim):
    product = Repository().product.model_copy(update={
        "name": "Sữa tắm gội toàn thân không cay mắt 500ml"})
    with pytest.raises(GroqProviderError, match="Sửa lý do chưa có nhãn"):
        _render_structured(Composition(choices=[{"product_id": "P1", "reason": claim,
            "evidence_fields": ["name"]}]), {"P1": product}, Plan(need="Sữa tắm"), "test")


def test_customer_desired_attribute_is_not_misread_as_a_product_property():
    product = Repository().product.model_copy(update={"name": "Sữa tắm chăm sóc da nhạy cảm"})
    result = _render_structured(Composition(choices=[{"product_id": "P1", "reason":
        "Nhãn ghi rõ dành cho da nhạy cảm, phù hợp với nhu cầu tìm loại sữa tắm dịu nhẹ.",
        "evidence_fields": ["name"]}]), {"P1": product}, Plan(need="Sữa tắm"), "test")
    assert "nhu cầu tìm" in result.text
    with pytest.raises(GroqProviderError, match="Sửa lý do"):
        _render_structured(Composition(choices=[{"product_id": "P1", "reason":
            "Nhãn ghi da nhạy cảm, vì vậy công thức dịu nhẹ và không gây kích ứng.",
            "evidence_fields": ["name"]}]), {"P1": product}, Plan(need="Sữa tắm"), "test")


def test_repair_feedback_preserves_the_actual_vietnamese_claim():
    product = Repository().product
    with pytest.raises(GroqProviderError, match="đủ dùng cho một bữa"):
        _render_structured(Composition(choices=[{"product_id": "P1", "reason":
            "Tiện để nấu, đủ dùng cho một bữa lẩu gia đình.",
            "evidence_fields": ["name"]}]), {"P1": product}, Plan(need="Ăn lẩu"), "test")


@pytest.mark.parametrize("name", ["Bánh mì phô mai 70g", "Sữa hạt 180ml"])
def test_flavour_or_category_cannot_prove_how_long_someone_stays_full(name):
    product = Repository().product.model_copy(update={"name": name})
    with pytest.raises(GroqProviderError, match="Sửa lý do"):
        _render_structured(Composition(choices=[{"product_id": "P1", "reason":
            "Tiện cho bữa sáng và giúp no lâu.", "evidence_fields": ["name"]}]),
            {"P1": product}, Plan(need="Ăn sáng"), "test")


def test_reference_guidance_is_scoped_and_does_not_claim_to_describe_a_sku():
    skin = relevant_guidance("Da nhạy cảm", "chọn sữa tắm")
    assert skin[0]["id"] == "cosmetic-sensitivity"
    assert all(url.startswith("https://") for url in skin[0]["sources"])
    assert "Tear-free" in " ".join(skin[0]["principles"])
    assert relevant_guidance("Mua thức ăn cho mèo") == []
    assert relevant_guidance("Tìm bàn chải cho răng nhạy cảm") == []
    assert relevant_guidance("Nhà nuôi mèo, chọn nước lau sàn")[0]["id"] == "cleaning-around-pets"
    assert relevant_guidance("Mua thịt heo ăn lẩu") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query,customer,wrong_name,category", [
    ("thịt ba chỉ heo", "Mua ba chỉ heo", "Thịt ba chỉ bò 200g", "Thịt"),
    ("nước tẩy bồn cầu", "Mua nước tẩy bồn cầu", "Nước tẩy quần áo 1L", "Nước Tẩy Rửa"),
    ("sữa tắm trẻ em", "Mua sữa tắm trẻ em", "Sữa tắm người lớn 500ml", "Chăm Sóc Da"),
])
async def test_failed_customer_type_is_never_trimmed_into_a_different_subtype(query, customer, wrong_name, category):
    repo = Repository()
    wrong = repo.product.model_copy(update={"name": wrong_name, "category": category})
    repo.products = (wrong,)
    def search(**criteria):
        repo.calls.append(criteria)
        return [] if criteria['query'] == query else [wrong]
    repo.search = search
    candidates, missing = await _retrieve_candidates(repo,
        Plan(need=customer, searches=[Search(query=query, category=category)]), [category], [customer])
    assert candidates == {} and missing == [0]
    assert not any(c['query'] == ' '.join(query.split()[:-1]) for c in repo.calls)


@pytest.mark.asyncio
async def test_owned_items_are_filtered_before_candidate_limit():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need="Rau cho bữa cơm", decision_mode="basket",
        searches=[Search(query="rau", category="Rau Lá")],
        purchase_exclusions={"owned": Search(query="rau mầm")})
    candidates, _ = await _retrieve_candidates(repo, plan,
        sorted({p.category for p in repo.products}), ["Nhà có rau mầm rồi, chọn rau khác"])
    assert candidates and all("rau mầm" not in p.name.lower() for p in candidates.values())
    assert any("cải" in p.name.lower() for p in candidates.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category,expected", [
    ("thịt ba chỉ heo", "Thịt", "Ba Rọi"),
    ("thịt lợn", "Thịt", "heo"),
    ("nước rửa bát", "Nước Rửa Chén", "rửa chén"),
    ("mì chính", "Gia Vị Khác", "Bột ngọt"),
])
async def test_verified_terms_recover_old_catalog_without_changing_customer_words(query, category, expected):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need=query, searches=[Search(query=query, category=category)])
    candidates, _ = await _retrieve_candidates(repo, plan,
        sorted({p.category for p in repo.products}), [query])
    assert candidates and any(expected.lower() in p.name.lower() for p in candidates.values())
    assert plan.searches[0].query == query
    assert all(p.category == category for p in candidates.values())


def test_equivalence_does_not_erase_animal_variant_brand_size_or_accents():
    aliases = equivalent_queries("ba chỉ bò cắt lát KIAORA 500g", "Thịt")
    assert "ba rọi bò cắt lát kiaora 500g" in aliases
    assert all("bò" in q and "500g" in q and "kiaora" in q for q in aliases)
    assert identity_terms("bông") != identity_terms("băng")
    assert equivalent_queries("ba chỉ", "Bánh bao") == []


def test_planner_hallucinated_child_cannot_authorize_a_question_about_a_child():
    with pytest.raises(GroqProviderError, match="nhóm tuổi trẻ em"):
        _render_structured(Composition(question="Bé nhà bạn bao nhiêu tuổi?"), {},
            Plan(need="Chọn sữa tắm cho bé", action="ask", source_user_text="Da em nhạy cảm"), "test")
    result = _render_structured(Composition(advice="Lau bề mặt rồi để khô."), {},
        Plan(need="dọn nhà", source_user_text="Dọn nhà giúp mình"), "test")
    assert "bề mặt" in result.text


def test_verified_child_quote_survives_a_short_followup():
    plan = Plan(need="Ăn vặt", action="ask", source_user_text="ít ngọt nhé",
        requirement_quotes={"recipient": "Mua đồ ăn cho bé nhà em"})
    result = _render_structured(Composition(question="Bé nhà bạn bao nhiêu tuổi?"), {}, plan, "test")
    assert "bao nhiêu tuổi" in result.text


def test_no_match_cannot_guess_purchase_amount_from_budget():
    with pytest.raises(GroqProviderError, match="quy đổi ngân sách"):
        _render_structured(Composition(advice="Với ngân sách này mua 600g thịt."), {},
            Plan(need="mua thịt", budget_vnd=150000, source_user_text="Mua thịt khoảng 150 nghìn"), "test")


def test_review_accepts_detailed_grounded_check_without_spurious_retry():
    from backend.services.consultation import ReviewCheck
    check = ReviewCheck(criterion="grounding", passed=True, detail="Căn cứ vào nhãn và lượng mua. " * 12)
    assert len(check.detail) > 240


def test_clarification_about_owned_items_is_not_a_store_stock_claim():
    result = _render_structured(Composition(question="Bạn đã có sẵn chổi và khăn lau chưa?"), {},
        Plan(need="Dọn nhà", action="ask"), "test")
    assert "có sẵn" in result.text
    with pytest.raises(GroqProviderError, match="tồn thực tế"):
        _render_structured(Composition(advice="Bên mình có sẵn các loại này."), {},
            Plan(need="Dọn nhà"), "test")
    with pytest.raises(GroqProviderError, match="tồn thực tế"):
        _render_structured(Composition(question="Bên mình có sẵn loại này, bạn lấy nhé?"), {},
            Plan(need="Dọn nhà", action="ask"), "test")


def test_clarification_does_not_repeat_a_budget_already_in_state():
    with pytest.raises(GroqProviderError, match="Ngân sách khách đã nêu"):
        _render_structured(Composition(question="Bạn muốn ngân sách bao nhiêu?"), {},
            Plan(need="Quà tặng", action="ask", budget_vnd=300000,
                 source_user_text="Quà cho mẹ tầm 300 nghìn"), "test")


def test_general_label_preference_is_allowed_but_medical_promise_is_not():
    result = _render_structured(Composition(advice="Ưu tiên sản phẩm không có mùi thơm mạnh."), {},
        Plan(need="Sữa tắm nhạy cảm"), "test")
    assert "Ưu tiên" in result.text
    with pytest.raises(GroqProviderError, match="nhãn"):
        _render_structured(Composition(advice="Ưu tiên mỹ phẩm điều trị mụn."), {},
            Plan(need="Mụn"), "test")


def test_label_target_skin_supports_purpose_but_not_absence_of_irritation():
    product = Repository().product.model_copy(update={"name": "Sữa tắm chăm sóc da nhạy cảm"})
    result = _render_structured(Composition(choices=[{"product_id": "P1", "evidence_fields": ["name"],
        "reason": "Phù hợp cho da nhạy cảm."}]), {"P1": product}, Plan(need="Sữa tắm"), "test")
    assert "Phù hợp" in result.text
    with pytest.raises(GroqProviderError, match="nhãn"):
        _render_structured(Composition(choices=[{"product_id": "P1", "evidence_fields": ["name"],
            "reason": "Không gây kích ứng."}]), {"P1": product}, Plan(need="Sữa tắm"), "test")


@pytest.mark.asyncio
async def test_invented_child_is_removed_from_planner_context_before_composition():
    model = RecordedReplies(
        {"need": "Tư vấn sữa tắm cho bé có da nhạy cảm", "action": "ask",
         "clarification": "Da bạn có dễ bị khô sau tắm không?"},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Da em nhạy cảm", [], [])
    assert result.plan.need == "Da em nhạy cảm"
    assert "bé" not in result.plan.need


@pytest.mark.asyncio
async def test_repeated_unverified_sku_claim_recovers_with_reviewed_guidance():
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Sữa tắm hương hoa 500ml"})
    bad = {"choices": [{"product_id": "P1", "reason": "Thích hợp cho da nhạy cảm.",
                        "evidence_fields": ["name"]}]}
    model = RecordedReplies(
        {"need": "Chọn sữa tắm cho da nhạy cảm", "searches": [{"query": "sữa tắm"}]},
        bad, bad,
        {"advice": "Mình chưa xác nhận được loại phù hợp da nhạy cảm. Nên ưu tiên nhãn ghi rõ không hương liệu và thử trước trên vùng da nhỏ."},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, repo, "Da em nhạy cảm, chọn sữa tắm", [], [])
    assert not result.products and "thử trước" in result.text
    assert result.plan.decision_mode == "auto"
    recovery = json.loads(model.calls[3][0][1]["content"].split(": ", 1)[1])
    assert recovery["candidates"][0]["product_id"] == "P1"
    assert "unsupported claims" in model.calls[3][0][0]["content"]
    assert json.loads(model.calls[4][0][-1]["content"])["draft"] == result.text


@pytest.mark.asyncio
async def test_recovered_guidance_cannot_escape_the_semantic_review():
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Sữa tắm hương hoa"})
    bad = {"choices": [{"product_id": "P1", "reason": "Thích hợp cho da nhạy cảm.",
                        "evidence_fields": ["name"]}]}
    model = RecordedReplies(
        {"need": "Sữa tắm da nhạy cảm", "searches": [{"query": "sữa tắm"}]},
        bad, bad, {"advice": "Mua gì cũng được."},
        {"acceptable": False, "issues": ["Không có hướng dẫn hữu ích."]},
    )
    with pytest.raises(GroqProviderError, match="Không có hướng dẫn"):
        await consult(model, repo, "Sữa tắm da nhạy cảm", [], [])


@pytest.mark.asyncio
async def test_reviewer_can_switch_arbitrary_purchase_to_one_useful_question():
    model = RecordedReplies(
        {"need": "Quà tặng chưa rõ sở thích", "searches": [{"query": "thịt bò"}]},
        {"choices": [{"product_id": "P1", "reason": "Nấu chín để dùng trong bữa ăn.",
                      "evidence_fields": ["name"]}]},
        {"acceptable": False, "repair_action": "clarify",
         "issues": ["Chưa biết người nhận thích nhóm quà nào."]},
        {"question": "Người nhận thích đồ ăn hay đồ dùng trong nhà hơn?"},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Chọn quà giúp mình", [], [])
    assert result.plan.action == "ask" and not result.products
    assert result.text == "Người nhận thích đồ ăn hay đồ dùng trong nhà hơn?"
    assert "Repair a store employee's clarification" in model.calls[3][0][0]["content"]
    assert "Giữ các lựa chọn hợp lệ" not in model.calls[3][0][-1]["content"]


@pytest.mark.asyncio
async def test_first_turn_clarification_is_reviewed_and_wrong_slot_is_repaired():
    model = RecordedReplies(
        {"need": "Khách muốn mua hàng đáng tiền, chưa biết nhóm hàng.",
         "action": "ask", "clarification": "Bạn mua cho bao nhiêu người?"},
        {"acceptable": False, "issues": ["Chưa biết sản phẩm, phải hỏi nhóm hàng trước số người."]},
        {"question": "Bạn đang muốn mua nhóm hàng nào?"},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Loại nào đáng tiền là được.", [], [])
    assert result.text == "Bạn đang muốn mua nhóm hàng nào?"
    assert result.products == ()
    reviewed = json.loads(model.calls[1][0][-1]["content"])
    assert reviewed["request_action"] == "ask"
    assert "FIRST turn" in model.calls[1][0][0]["content"]
    assert len(model.calls) == 4


@pytest.mark.asyncio
async def test_first_turn_question_does_not_escape_if_review_is_unavailable():
    model = RecordedReplies(
        {"need": "Xác định loại sữa", "action": "ask", "clarification": "Bạn nhớ hãng sữa nào không?"},
        GroqProviderError("review unavailable"),
    )
    with pytest.raises(GroqProviderError, match="review unavailable"):
        await consult(model, Repository(), "Sữa màu xanh ấy", [], [])


@pytest.mark.asyncio
async def test_malformed_planner_question_can_be_repaired_and_reviewed():
    model = RecordedReplies(
        {"need": "Tìm loại quà mẹ thích", "action": "ask", "decision_mode": "guide",
         "clarification": "Mẹ thích gì? Đồ ăn hay đồ dùng?"},
        {"advice": "Mẹ thích đồ ăn?", "question": "Mẹ thích đồ ăn hay đồ dùng trong nhà hơn?"},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Mua quà cho mẹ", [], [])
    assert "đồ ăn" in result.text and "đồ dùng" in result.text
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_contradictory_review_cannot_approve_a_failed_criterion():
    answer = _render_structured(Composition(question="Bạn muốn mua nhóm hàng nào?"), {},
        Plan(need="chưa rõ mặt hàng", action="ask"), "test")
    model = RecordedReplies({"acceptable": True, "issues": [], "checks": [
        {"criterion": key, "passed": key != "decision", "detail": "Phải xác định loại hàng trước."}
        for key in ("intent", "decision", "grounding", "usefulness")
    ]})
    review = await _review_advice(model, answer, "Chọn giúp loại đáng tiền", [])
    assert not review.acceptable and "xác định" in review.issues[0]


@pytest.mark.asyncio
async def test_provider_json_mode_failure_falls_back_to_locally_validated_json():
    model = RecordedReplies(
        GroqProviderError("encoding failure", fallback_trace=[{
            "http_status": 400, "provider_code": "json_validate_failed"}]),
        {"need": "Xác định loại sữa", "action": "ask", "clarification": "Bạn nhớ nhãn hiệu nào không?"},
        {"acceptable": True, "issues": []},
    )
    result = await consult(model, Repository(), "Sữa màu xanh ấy", [], [])
    assert result.text == "Bạn nhớ nhãn hiệu nào không?"
    assert model.calls[0][1]["response_format"] == {"type": "json_object"}
    assert model.calls[1][1]["response_format"] is None


@pytest.mark.parametrize("name, reason", [
    ("Bánh bao chay sữa dừa 200g", "Ăn ngay nên no lâu."),
    ("Bánh bao chay sữa dừa 200g", "Có nhân béo, dễ ăn."),
    ("Nước lau sàn 3,8kg", "Đủ dùng cho toàn bộ sàn nhà trong một lần dọn."),
    ("Nước uống Aquafina 5L", "Có độ ngọt rất thấp."),
    ("Nước uống Aquafina 5L", "Uống mà không làm ngứa miệng."),
    ("Thịt bò 200g", "Đủ 200g cho 4 người lẩu."),
])
def test_reproduced_unsupported_label_claims_are_rejected(name, reason):
    product = Repository().product.model_copy(update={"name": name, "description": None})
    with pytest.raises(GroqProviderError, match="nhãn"):
        _render_structured(Composition(choices=[{
            "product_id": "P1", "reason": reason, "evidence_fields": ["name"],
        }]), {"P1": product}, Plan(need="chọn hàng"), "test")


def test_verified_ready_to_eat_label_and_general_cooking_are_still_allowed():
    product = Repository().product.model_copy(update={
        "name": "Bánh ăn ngay", "description": "Ăn ngay không cần hâm.",
    })
    result = _render_structured(Composition(choices=[{
        "product_id": "P1", "reason": "Ăn ngay, không cần hâm.", "evidence_fields": ["description"],
    }]), {"P1": product}, Plan(need="ăn tiện"), "test")
    assert "Ăn ngay" in result.text


@pytest.mark.asyncio
async def test_real_catalog_separates_tea_type_from_sugar_free_variant():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    searches = _canonical_searches([Search(query="trà không đường", category="Trà - Các Loại Khác")])
    assert searches[0].query == "trà"
    assert searches[0].required_phrases == ["không đường"]
    candidates, _ = await _retrieve_candidates(repo, Plan(need="đồ uống", searches=searches),
        sorted({p.category for p in repo.products}), ["trà không đường"])
    # The old flat 'Trà' category also contains sugar-free cacao. An empty
    # exact match is preferable to silently treating that cacao as tea.
    assert "WIN_10013350" not in candidates
    assert all("kẹo" not in p.name.lower() for p in candidates.values())
    assert all("trà" in p.name.lower() for p in candidates.values())
    assert all("không đường" in p.name.lower() for p in candidates.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [True, False])
async def test_location_answers_never_ask_purchase_quantity_or_invent_shelf(monkeypatch, known):
    settings = Settings(catalog_repository="snapshot")
    repo = Repository()
    repo.source_name = "test"
    repo.product = repo.product.model_copy(update={
        "name": "Khăn giấy 100 tờ", "category": "Khăn giấy", "shelf": "A3-B2",
        "shelf_source": "VERIFIED" if known else "RECOMMENDED_PLANOGRAM",
    })

    class NoModel:
        async def chat(self, *args, **kwargs):
            raise AssertionError("Location facts must not depend on an LLM")

    monkeypatch.setattr(main, "get_product_repository", lambda *_: repo)
    main.app.dependency_overrides[main.get_settings] = lambda: settings
    main.app.dependency_overrides[main.get_groq_service] = NoModel
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            session = (await client.post("/sessions", json={"kiosk_id": "shelf-test"})).json()
            response = await client.post("/agent/respond", json={
                "session_id": session["session_id"], "language": "vi",
                "text": "Em tìm mãi không thấy khăn giấy, nó nằm khu nào vậy?",
            })
        assert response.status_code == 200
        reply = response.json()["text"]
        assert ("A3-B2" in reply) is known
        assert "bao nhiêu" not in reply and "giá" not in reply
        if not known:
            assert "vị trí kệ chính xác" in reply
        assert repo.calls[-1]["query"] == "khan giay"
    finally:
        main.app.dependency_overrides.pop(main.get_settings, None)
        main.app.dependency_overrides.pop(main.get_groq_service, None)
