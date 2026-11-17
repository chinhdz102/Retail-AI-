"""Latency shortcuts must preserve catalog scope and fail closed on new meaning."""
import json

import pytest

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.consultation import (
    Composition, PackageConstraint, Plan, Search, _catalog_head_backoff,
    _compose_and_review, _fast_catalog_advice, _fast_catalog_plan, _json_call,
    _locally_verified_catalog_composition, _package_followup_plan,
    _recipient_followup, _render_structured, _repair_retrieval,
    _retrieve_candidates, _review_advice, consult,
)
from backend.services.groq import GroqProviderError, GroqService
from tests.test_consultation import Repository
from tests.test_consultation_review import RecordedReplies


@pytest.fixture(scope="module")
def snapshot():
    return get_product_repository(Settings(catalog_repository="snapshot"))


@pytest.mark.asyncio
@pytest.mark.parametrize("product_type,limit,strict", [
    ("nước giặt", 150000, True), ("khăn giấy", 50000, True),
    ("sữa rửa mặt", 100000, True), ("bánh quy", 80000, False),
])
async def test_explicit_type_and_price_need_no_remote_planning(snapshot, product_type, limit, strict):
    cue = "dưới" if strict else "tối đa"
    text = f"{product_type} {cue} {limit // 1000} nghìn nên chọn loại nào?"
    model = GroqService(Settings(groq_api_key="test"))

    async def unexpected(*args, **kwargs):
        raise AssertionError("Fully parsed catalog facts must not consume a model request")

    model.chat = unexpected
    answer = await consult(model, snapshot, text, [], [])
    assert answer.products and len(answer.products) == 1
    assert answer.model == "deterministic-fast-path"
    assert answer.plan.budget_vnd == limit and answer.plan.strict_budget == strict
    assert all(p.price_vnd < limit if strict else p.price_vnd <= limit for p in answer.products)
    assert all(product_type in p.name.casefold() for p in answer.products)
    assert "bao bì" not in answer.text and "catalog" not in answer.text.casefold()


@pytest.mark.parametrize("text", [
    "Không mua nước giặt dưới 150 nghìn.",
    "Nước giặt dưới 150 nghìn an toàn cho mèo.",
    "Sữa rửa mặt dưới 100 nghìn cho da nhạy cảm.",
    "Khăn giấy hoặc nước giặt dưới 100 nghìn.",
    "Dầu gội từ 100 đến 150 nghìn.",
    "Nước giặt không có mùi nồng dưới 150 nghìn.",
])
def test_price_shortcut_does_not_discard_unparsed_constraints(snapshot, text):
    assert _fast_catalog_plan(text, None, [], snapshot) is None


@pytest.mark.parametrize("text,dimension,value,operator", [
    ("Chọn loại từ 400ml trở lên.", "volume_ml", 400, "gte"),
    ("Lấy loại 1 lít nhé.", "volume_ml", 1000, "eq"),
    ("Chọn loại tối đa 500g.", "mass_g", 500, "lte"),
    ("Tìm loại 20cm.", "length_cm", 20, "eq"),
])
def test_package_update_keeps_product_type_and_other_filters(text, dimension, value, operator):
    prior = Plan(need="Chọn một sản phẩm", decision_mode="recommend", budget_vnd=150000,
        searches=[Search(query="sữa rửa mặt", required_phrases=["không dầu"],
            package_constraints=[PackageConstraint(dimension=dimension, value=100,
                                                   source_quote="100ml")])])
    result = _package_followup_plan(text, prior.model_dump())
    assert result and result.searches[0].query == prior.searches[0].query
    assert result.searches[0].required_phrases == ["không dầu"]
    assert result.budget_vnd == 150000 and not result.topic_changed
    assert [(p.dimension, p.value, p.operator) for p in result.searches[0].package_constraints] == [
        (dimension, value, operator)]


@pytest.mark.parametrize("text", [
    "Dưới 400ml.", "Hơn 400ml.", "Loại >=400ml.", "Không lấy 400ml.",
    "400ml nhưng rẻ hơn.", "Đổi sang nước giặt 400ml.",
    "Từ 400ml đến 500ml.", "Chọn loại 400ml " + "nhé " * 45,
])
def test_ambiguous_package_turn_requires_semantic_planning(text):
    prior = Plan(need="Dầu gội", decision_mode="recommend", searches=[Search(query="dầu gội")])
    assert _package_followup_plan(text, prior.model_dump()) is None


@pytest.mark.asyncio
async def test_failed_shampoo_variant_never_backs_off_to_cooking_oil(snapshot):
    query = Search(query="dầu gội chống gàu nữ", category="Chăm Sóc Tóc",
        package_constraints=[PackageConstraint(dimension="volume_ml", operator="gte",
                                               value=400, source_quote="từ 400ml")])
    assert _catalog_head_backoff(snapshot, query, dict(in_stock=True, limit=300)) is None
    plan = Plan(need="Dầu gội cho nữ", decision_mode="recommend", searches=[query])
    candidates, _ = await _retrieve_candidates(snapshot, plan,
        sorted({p.category for p in snapshot.products}), ["Dầu gội cho nữ", "từ 400ml"])
    assert all("dầu gội" in p.name.casefold() for p in candidates.values())


def test_literal_facts_are_locally_verifiable_but_added_claims_are_not():
    product = Repository().product.model_copy(update={"name": "Nước giặt hương hoa túi 2kg"})
    candidates = {"P1": product}
    plan = Plan(need="Chọn nước giặt", decision_mode="recommend")
    literal = Composition(advice="Mình gợi ý loại này:", choices=[{
        "product_id": "P1", "reason": "hương hoa", "evidence_fields": ["name"]}])
    assert _locally_verified_catalog_composition(literal, candidates, plan)
    for reason in ("hương hoa nhẹ", "không có hương hoa", "hương hoa, không gây dị ứng"):
        changed = literal.model_copy(update={"choices": [literal.choices[0].model_copy(
            update={"reason": reason})]})
        assert not _locally_verified_catalog_composition(changed, candidates, plan)
    assert not _locally_verified_catalog_composition(literal.model_copy(
        update={"advice": "An toàn cho thú cưng."}), candidates, plan)


@pytest.mark.asyncio
async def test_local_fact_gate_skips_only_the_remote_reviewer():
    product = Repository().product.model_copy(update={"name": "Nước giặt hương hoa túi 2kg"})
    model = RecordedReplies({"choices": [{"product_id": "P1", "reason": "hương hoa",
                                          "evidence_fields": ["name"]}]})
    answer = await _compose_and_review(model, [{"role": "system", "content": "Compose."}],
        {"P1": product}, Plan(need="Nước giặt", decision_mode="recommend"),
        "Chọn nước giặt", [], True)
    assert answer.products == (product,) and len(model.calls) == 1


@pytest.mark.asyncio
async def test_review_has_short_flags_and_failure_cannot_pass_without_detail():
    model = RecordedReplies({"checks": [{"criterion": key, "passed": key != "grounding"}
        for key in ("intent", "decision", "grounding", "usefulness")],
        "acceptable": True, "issues": []})
    repo = Repository()
    answer = _render_structured(Composition(choices=[{"product_id": "P1",
        "evidence_fields": ["name"], "reason": "Thái mỏng để xào chín."}]),
        {"P1": repo.product}, Plan(need="Mua thịt"), "fixture")
    review = await _review_advice(model, answer, "Mua thịt", [])
    assert not review.acceptable and review.issues
    policy = model.calls[0][0][0]["content"]
    schema = json.loads(policy.split("following this schema: ", 1)[1])
    assert "detail" not in schema["$defs"]["ReviewCheck"]["properties"]


@pytest.mark.parametrize("text,expected", [
    ("Thế mua cho nữ thì sao?", True), ("Vậy mua cho mẹ nhé.", True),
    ("Thế mua sữa tắm cho nữ thì sao?", False),
    ("Mua thịt bò cho mẹ.", False), ("Chọn dòng riêng cho nữ.", False),
    ("Mua cho nữ loại 400ml.", False), ("Mua cho nữ không có mùi nồng.", False),
])
def test_recipient_update_cannot_discard_a_product_or_another_requirement(text, expected):
    assert _recipient_followup(text, Repository()) is expected


@pytest.mark.asyncio
async def test_recipient_followup_does_not_reset_type_or_prior_budget():
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Dầu gội chống gàu 100ml"})
    prior = Plan(need="Dầu gội chống gàu dưới 150 nghìn", decision_mode="recommend",
        budget_vnd=150000, budget_scope="item", strict_budget=True,
        searches=[Search(query="dầu gội", required_phrases=["chống gàu"])])
    model = RecordedReplies({"need": "Dầu gội chống gàu cho nữ", "topic_changed": True,
        "decision_mode": "recommend", "searches": [{"query": "dầu gội nữ"}]},
        {"choices": [{"product_id": "P1", "evidence_fields": ["name"],
                      "reason": "Cỡ chai nhỏ để dùng thử trước khi mua chai lớn."}]},
        {"acceptable": True, "issues": []})
    answer = await consult(model, repo, "Thế mua cho nữ thì sao?", [], ["P1"], prior.model_dump())
    assert answer.plan.searches[0].query == "dầu gội"
    assert answer.plan.searches[0].required_phrases == ["chống gàu"]
    assert not answer.plan.topic_changed and answer.plan.budget_vnd == 150000


@pytest.mark.asyncio
async def test_real_route_recipient_and_size_need_no_remote_replanning(snapshot, monkeypatch):
    from backend.services.consultation import Advice

    model = GroqService(Settings(groq_api_key="test"))

    async def unexpected(*args, **kwargs):
        raise AssertionError("A parsed state update must not replan or repair its literal type")

    async def composed(groq, messages, candidates, current, *args):
        return Advice("Đã kiểm tra ngữ nghĩa.", "test", tuple(candidates.values()), current)

    model.chat = unexpected
    monkeypatch.setattr("backend.services.consultation._compose_and_review", composed)
    prior = Plan(need="Dầu gội chống gàu", decision_mode="recommend", budget_vnd=150000,
        budget_scope="item", searches=[Search(query="dầu gội", required_phrases=["chống gàu"])])
    remembered = snapshot.search(query="Selsun", in_stock=True, limit=1)
    result = await consult(model, snapshot, "Thế mua cho nữ thì sao?", [],
        [p.product_id for p in remembered], prior.model_dump())
    assert result.products and result.plan.requirements["recipient"]
    sized = await consult(model, snapshot, "Chọn loại từ 400ml trở lên.", [],
        [p.product_id for p in result.products], result.plan.model_dump())
    assert all(p.name != remembered[0].name for p in sized.products)
    for product in sized.products:
        assert "dầu gội" in product.name.casefold()
        from backend.services.retail_measurements import package_values
        assert any(value >= 400 for value in package_values(product.name, "volume_ml"))
    if not sized.products:
        assert sized.model == "deterministic-no-package-match"
        assert "400ml" in sized.text and "150.000đ" in sized.text


@pytest.mark.parametrize("recipient", ["cho nữ", "cho bé", "cho người lớn tuổi"])
def test_recipient_change_still_needs_suitability_review(recipient):
    product = Repository().product
    assert _fast_catalog_advice({product.product_id: product}, Plan(
        need="Đổi người dùng", decision_mode="recommend",
        requirements={"recipient": recipient},
    )) is None


def test_recipient_gender_is_not_a_label_claim():
    product = Repository().product.model_copy(update={"name": "Dầu gội chống gàu 100ml"})
    composition = Composition(choices=[{"product_id": "P1", "evidence_fields": ["name"],
        "reason": "Dầu gội này được nhãn hiệu dành cho nữ."}])
    with pytest.raises(GroqProviderError, match="đối tượng ghi trên nhãn"):
        _render_structured(composition, {"P1": product}, Plan(need="Dầu gội cho nữ"), "fixture")


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category", [
    ("sản phẩm chứa niacinamide cho da dầu", "Chăm Sóc Da"),
    ("mặt hàng làm sạch dịu nhẹ", "Chất Tẩy Rửa"),
    ("sản phẩm giúp no lâu", "Bánh"),
])
async def test_unidentified_property_search_cannot_expand_an_aisle_or_consume_repair(
    snapshot, query, category,
):
    plan = Plan(need="Tư vấn theo nhu cầu", decision_mode="browse",
                searches=[Search(query=query, category=category)])
    topics = sorted({p.category for p in snapshot.products})
    candidates, missing = await _retrieve_candidates(snapshot, plan, topics, [plan.need])
    assert not candidates and missing == [0]

    class NoRemoteCalls:
        async def chat(self, *args, **kwargs):
            raise AssertionError("A benefit without a product type is not lexically repairable")

    assert await _repair_retrieval(
        NoRemoteCalls(), snapshot, plan, missing, topics, plan.need,
    ) == plan.searches
