"""Catalogue-wide contracts for exploratory shopping, not example SKU rules."""

import json

import pytest

from backend.config import Settings
from backend.products.catalog import ProductSnapshot
from backend.products.repository import get_product_repository
from backend.services.consultation import (
    Composition,
    Plan,
    Search,
    _fallback_grounded_advice,
    _fast_catalog_plan,
    _catalog_occasion_plan,
    _catalog_recovery_plan,
    _fast_catalog_advice,
    _infer_catalog_query,
    _json_call,
    _product_type_rank,
    _render_structured,
    _retrieve_candidates,
    _unsupported_claim,
    consult,
)
from backend.services.groq import GroqChatResult, GroqProviderError, GroqService
from backend.services.vietnamese_intent import is_greeting_only, normalize_vietnamese
from tests.test_consultation_review import RecordedReplies


@pytest.mark.parametrize("text", ["hello", "hello Sky!", "Xin chào", "hi bạn", "chào anh"])
def test_complete_greetings(text):
    assert is_greeting_only(text)


@pytest.mark.parametrize("text", [
    "hello, cho mình hỏi sữa", "chào bạn, nước giặt ở đâu?", "mua bánh", "hi mua sữa",
])
def test_shopping_with_a_greeting_keeps_its_intent(text):
    assert not is_greeting_only(text)


@pytest.mark.parametrize("text,expected", [
    ("Tìm dung dịch vệ sinh phụ nữ giúp mình", "dung dịch vệ sinh"),
    ("Tư vấn dầu gội giúp mình", "dầu gội"),
    ("Có loại nước giặt nào không", "nước giặt"),
    ("Mình cần mua khăn giấy", "khăn giấy"),
])
def test_catalog_head_phrases_keep_accents_and_identity_words(text, expected):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    query = _infer_catalog_query(repo, text, allow_broad=True)
    assert query and normalize_vietnamese(expected) in normalize_vietnamese(query)
    assert repo.search(query=query, in_stock=True)


def test_followup_replans_instead_of_copying_old_constraints():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert _fast_catalog_plan("Mình cần mua dầu gội", {"need": "Dầu gội cho nam"}, [], repo) is None


@pytest.mark.parametrize("text,expected_query", [
    ("Thế có đồ gì ăn sáng không?", "ăn sáng"),
    ("Có gì ăn đêm không?", "ăn liền"),
])
def test_occasion_fast_plan_uses_catalog_taxonomy(text, expected_query):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = _catalog_occasion_plan(text, repo, conservative=True)
    assert plan and plan.decision_mode == "browse"
    assert plan.searches and all(expected_query in search.query for search in plan.searches)
    assert all(search.category for search in plan.searches)


def test_occasion_fast_plan_keeps_unparsed_preferences_for_llm():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert _catalog_occasion_plan("Ăn sáng nhanh mà vẫn no, dưới 50 nghìn", repo,
                                  conservative=True) is None


def test_catalog_recovery_plan_does_not_replay_old_state():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert _catalog_recovery_plan("Có gì ăn đêm không?", {"need": "nước giặt"}, repo) is None


def test_catalog_recovery_plan_accepts_explicit_new_product_type_on_followup():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = _catalog_recovery_plan(
        "Có mấy loại sữa rửa mặt?",
        {"need": "nước giặt", "searches": [{"query": "nước giặt"}],
         "budget_vnd": 150000, "source_user_text": "nước giặt dưới 150 nghìn"},
        repo,
    )
    assert plan and plan.topic_changed
    assert plan.searches[0].query.casefold() == "sữa rửa mặt"
    assert plan.budget_vnd is None


def test_catalog_recovery_plan_reuses_previous_type_for_short_browse_followup():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = _catalog_recovery_plan(
        "Có mấy loại?",
        {"need": "nước giặt", "searches": [{"query": "nước giặt"}]}, repo,
    )
    assert plan and plan.reuse_previous and plan.searches[0].query == "nước giặt"


def test_deterministic_fast_advice_stays_catalog_grounded():
    # Use a small explicit fixture to test the renderer without making this
    # unit test depend on the network or the live repository rank.
    products = options()
    answer = _fast_catalog_advice(products, Plan(need="Xem vài món", decision_mode="browse",
        searches=[Search(query="bánh mì"), Search(query="ngũ cốc")]))
    assert answer and "12.000đ" in answer.text and "35.000đ" in answer.text
    assert "catalog" not in answer.text.casefold()


def options():
    return {str(i): ProductSnapshot(product_id=str(i), name=name,
        category=category, price_vnd=price, stock=5)
        for i, (name, category, price) in enumerate([
            ("Bánh mì 100g", "Bánh mì", 12000),
            ("Ngũ cốc 200g", "Ngũ cốc", 35000),
            ("Sữa tươi 180ml", "Sữa tươi", 9000),
        ])}


def test_browse_does_not_silently_collapse_to_one_choice_or_make_a_basket():
    candidates = options()
    plan = Plan(need="Xem vài lựa chọn", decision_mode="browse")
    with pytest.raises(GroqProviderError, match="2-3"):
        _render_structured(Composition(choices=[{"product_id": "0"}]), candidates, plan, "test")
    answer = _render_structured(Composition(choices=[
        {"product_id": "0", "evidence_fields": ["name"]},
        {"product_id": "1", "evidence_fields": ["name"]},
    ]), candidates, plan, "test")
    assert len(answer.products) == 2 and "Tổng" not in answer.text


def test_outage_fallback_preserves_diverse_real_options_and_exact_prices():
    candidates = options()
    plan = Plan(need="Xem đồ ăn", decision_mode="browse",
        searches=[Search(query="bánh mì"), Search(query="ngũ cốc"), Search(query="sữa tươi")])
    answer = _fallback_grounded_advice(candidates, plan)
    assert answer and len(answer.products) == 3
    assert all(p.name in answer.text for p in candidates.values())
    assert "12.000đ" in answer.text and "35.000đ" in answer.text
    assert not any(word in answer.text.casefold()
                   for word in ("catalog", "snapshot", "tồn", "nhãn"))
    assert "Tổng" not in answer.text


@pytest.mark.parametrize("sentence", [
    "[[0]]: Dùng mỗi ngày theo hướng dẫn trên nhãn.",
    "[[0]]: Cất trong túi để mang theo trong ngày.",
])
def test_day_is_not_misread_as_a_creamy_texture_claim(sentence):
    assert _unsupported_claim(sentence, options(), {"0": object()}) is None


@pytest.mark.parametrize("sentence", ["[[0]]: Vị ngậy.", "[[0]]: Có thể ăn ngay."])
def test_texture_and_ready_to_eat_claims_still_need_label_evidence(sentence):
    assert _unsupported_claim(sentence, options(), {"0": object()})


def test_trying_a_product_does_not_claim_it_will_suit_everyone():
    assert _unsupported_claim("[[0]]: Dùng thử để xem da có phù hợp không.",
                              options(), {"0": object()}) is None
    assert _unsupported_claim("[[0]]: Dùng thử sẽ không gây kích ứng.",
                              options(), {"0": object()})


def test_outage_fallback_cannot_bypass_basket_budget_guards():
    plan = Plan(need="Mua cùng nhau", decision_mode="basket",
                budget_scope="basket", budget_vnd=40000)
    assert _fallback_grounded_advice(options(), plan) is None


def test_literal_title_head_outranks_umbrella_category_only_match():
    bread = options()["0"]
    other = bread.model_copy(update={"name": "Bánh cốm 85g"})
    assert _product_type_rank(bread, "bánh mì") < _product_type_rank(other, "bánh mì")


@pytest.mark.asyncio
async def test_diversity_does_not_replace_liquid_detergent_with_cheaper_powder():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    candidates, _ = await _retrieve_candidates(repo, Plan(need="Xem nước giặt",
        decision_mode="browse", searches=[Search(query="nước giặt")]),
        sorted({p.category for p in repo.products}), ["Mua nước giặt"])
    assert candidates and all("nước giặt" in p.name.casefold() for p in candidates.values())


@pytest.mark.asyncio
async def test_generic_planner_property_query_cannot_leak_another_category():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(
        need="Da dầu nhiều mụn thì nên chọn sản phẩm nào?",
        decision_mode="browse",
        source_user_text="Da dầu nhiều mụn thì nên chọn sản phẩm nào?",
        searches=[
            Search(query="sữa rửa mặt dầu nhờn", category="Chăm sóc da"),
            Search(query="sản phẩm trị mụn dạng gel", category="Chăm sóc da"),
        ],
    )
    candidates, missing = await _retrieve_candidates(
        repo, plan, sorted({p.category for p in repo.products}), [plan.need])
    assert missing == [1]
    assert candidates
    assert all(p.category == "Chăm Sóc Da" for p in candidates.values())


@pytest.mark.parametrize("name,allowed", [("Nước khoáng LaVie 5", False),
                                        ("Nước khoáng LaVie 500ml", True)])
def test_number_in_a_truncated_name_does_not_authorize_guessing_pack_size(name, allowed):
    product = options()["0"].model_copy(update={"product_id": "WATER_FIXTURE",
                                               "name": name, "category": "Nước khoáng"})
    candidates = {product.product_id: product}
    draft = Composition(choices=[{"product_id": product.product_id, "evidence_fields": ["name"],
                                 "reason": "Chai 500ml tiện mang theo."}])
    if allowed:
        assert _render_structured(draft, candidates, Plan(need="Nước"), "test")
    else:
        with pytest.raises(GroqProviderError, match="quy cách"):
            _render_structured(draft, candidates, Plan(need="Nước"), "test")


@pytest.mark.parametrize("text,mode", [
    ("Mình muốn xem vài loại nước giặt để chọn", "browse"),
    ("Chọn giúp mình loại nước giặt", "recommend"),
])
def test_exploring_before_choosing_does_not_mean_choose_one_now(text, mode):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert _fast_catalog_plan(text, None, [], repo).decision_mode == mode


@pytest.mark.parametrize("sentence", [
    "[[0]]: Hấp nóng rồi ăn ngay.",
    "[[0]]: Nấu chín theo hướng dẫn rồi ăn ngay.",
])
def test_eating_after_explicit_cooking_is_not_uncooked_ready_to_eat_claim(sentence):
    assert _unsupported_claim(sentence, options(), {"0": object()}) is None


def test_skipping_cooking_is_still_an_unverified_ready_to_eat_claim():
    assert _unsupported_claim("[[0]]: Không cần nấu, ăn ngay.", options(), {"0": object()})


def test_portability_and_food_pairing_do_not_claim_shelf_location():
    result = _render_structured(Composition(choices=[{
        "product_id": "0", "evidence_fields": ["name"],
        "reason": "Tiện mang theo, kèm thực phẩm khác khi cần.",
    }]), options(), Plan(need="Đồ ăn mang đi"), "fixture")
    assert "Tiện mang theo" in result.text


def test_specific_shelf_location_still_fails_closed():
    with pytest.raises(GroqProviderError, match="tồn thực tế"):
        _render_structured(Composition(advice="Ở kệ thực phẩm bên trái nhé."), {},
                           Plan(need="Mua hàng"), "fixture")


@pytest.mark.asyncio
async def test_browse_with_no_generated_choices_still_passes_semantic_review():
    class RealProviderDouble(GroqService):
        def __init__(self):
            self.settings = Settings(groq_api_key="test", groq_model="test")
            self.script = RecordedReplies(
                {"need": "Xem vài lựa chọn", "decision_mode": "browse",
                 "searches": [{"query": "bánh mì"}, {"query": "ngũ cốc"}]},
                {"advice": "Bạn thích loại nào?"},
                {"choices": [{"product_id": "0", "evidence_fields": ["name"],
                              "reason": "Mang theo để dùng khi cần."},
                             {"product_id": "1", "evidence_fields": ["name"],
                              "reason": "Chuẩn bị theo hướng dẫn trên gói."}]},
                {"acceptable": True, "issues": []},
            )

        async def chat(self, messages, **kwargs):
            return await self.script.chat(messages, **kwargs)

    class Repo:
        def search(self, **kwargs):
            query = normalize_vietnamese(kwargs.get("query") or "")
            if "banh mi" in query:
                return [options()["0"]]
            if "ngu coc" in query:
                return [options()["1"]]
            return list(options().values())

        def get_product_snapshot(self, pid):
            return options().get(pid)

    model = RealProviderDouble()
    result = await consult(model, Repo(), "Mình muốn xem vài loại đồ ăn", [], [])
    assert len(result.products) >= 2
    review = json.loads(model.script.calls[-1][0][-1]["content"])
    assert review["draft"] == result.text
    assert review["available_candidates"]


@pytest.mark.asyncio
async def test_rejected_provider_encoding_is_not_repeated_on_next_turn():
    class Provider(GroqService):
        def __init__(self):
            self.settings = Settings(groq_api_key="test", groq_strict_outputs=True)
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise GroqProviderError("Encoding failed", fallback_trace=[{
                    "http_status": 400, "provider_code": "json_validate_failed"}])
            return GroqChatResult('{"choices":[]}', self.settings.groq_model)

    model = Provider()
    messages = [{"role": "system", "content": "Return Composition JSON."}]
    for _ in range(2):
        await _json_call(model, messages, Composition, 100)
    assert [call["response_format"]["type"] for call in model.calls] == [
        "json_schema", "json_object", "json_object"]
    assert all(call["defer_encoding_errors"] for call in model.calls)


@pytest.mark.parametrize("sentence", [
    "[[0]]: Dùng hằng ngày theo hướng dẫn.",
    "[[0]]: Dùng hàng ngày theo hướng dẫn.",
    "[[0]]: Mang theo ban ngày.",
])
def test_claim_words_cannot_match_inside_other_vietnamese_words(sentence):
    assert _unsupported_claim(sentence, options(), {"0": object()}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category,required", [
    ("nước giặt", "Nước Giặt", []),
    ("trà", "Trà - Các Loại Khác", ["không đường"]),
])
async def test_flat_category_cannot_override_actual_product_head(query, category, required):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    candidates, _ = await _retrieve_candidates(repo, Plan(need=query, decision_mode="browse",
        searches=[Search(query=query, category=category, required_phrases=required)]),
        sorted({p.category for p in repo.products}), [query])
    assert all(query in p.name.casefold() for p in candidates.values())
    if query == "nước giặt":
        assert len(candidates) >= 2
        assert all("bột giặt" not in p.name.casefold() for p in candidates.values())


@pytest.mark.asyncio
async def test_recipient_gender_is_soft_when_catalog_only_tags_the_other_gender():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need="Dầu gội chống gàu cho nam", decision_mode="browse",
        searches=[Search(query="dầu gội", category="Chăm Sóc Tóc",
                         required_phrases=["chống gàu"], any_phrases=["nam"])])
    candidates, _ = await _retrieve_candidates(repo, plan,
        sorted({p.category for p in repo.products}), [plan.need])
    assert candidates
    assert all("chống gàu" in p.name.casefold() for p in candidates.values())


@pytest.mark.parametrize("text", [
    "Mình mua thịt ăn lẩu, bò thường hoặc ba chỉ đều được, có những loại nào?",
    "Mình cần dầu gội cho nam, dùng khi nhiều gàu.",
    "Tìm nước giặt không có mùi nồng.",
    "Mình cần khăn giấy hoặc khăn lau.",
])
def test_fast_plan_never_drops_unparsed_customer_requirements(text):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert _fast_catalog_plan(text, None, [], repo) is None


@pytest.mark.asyncio
async def test_default_encoding_skips_failed_strict_generation_but_keeps_id_schema():
    script = RecordedReplies({"choices": []})
    model = GroqService(Settings(groq_api_key="test"))
    model.chat = script.chat
    await _json_call(model, [{"role": "system", "content": "Return Composition JSON."}],
                     Composition, 100, allowed_ids=options(), max_choices=3)
    messages, options_used = script.calls[0]
    assert options_used["response_format"] == {"type": "json_object"}
    schema = json.loads(messages[0]["content"].split("following this schema: ", 1)[1])
    assert schema["$defs"]["Choice"]["properties"]["product_id"]["enum"] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_provider_outage_cannot_erase_prior_semantic_rejection():
    from tests.test_consultation import Repository
    from tests.test_consultation_review import PLAN, BAD_DRAFT

    script = RecordedReplies(PLAN, BAD_DRAFT,
        {"acceptable": False, "issues": ["Cách dùng không hợp dạng hàng."]},
        GroqProviderError("Provider unavailable", fallback_trace=[{"http_status": 429}]))
    model = GroqService(Settings(groq_api_key="test"))
    model.chat = script.chat
    with pytest.raises(GroqProviderError) as raised:
        await consult(model, Repository(), "Chọn giúp", [], [])
    assert raised.value.semantic_rejected is True


@pytest.mark.asyncio
async def test_customer_can_choose_one_even_when_planner_inherits_browse_mode():
    model = RecordedReplies(
        {"need": "Chọn một món", "decision_mode": "browse", "reuse_previous": True,
         "searches": [{"query": "bánh mì"}]},
        {"choices": [{"product_id": "0", "evidence_fields": ["name"],
                      "reason": "Tiện mang theo để dùng khi cần."}]},
        {"acceptable": True, "issues": []},
    )

    class Repo:
        def search(self, **kwargs):
            return [options()["0"]]

        def get_product_snapshot(self, pid):
            return options().get(pid)

    result = await consult(model, Repo(), "Chốt một loại thôi, đừng giới thiệu thêm.", [], ["0"],
        {"need": "Xem đồ ăn", "decision_mode": "browse", "searches": [{"query": "bánh mì"}]})
    assert result.plan.decision_mode == "recommend"
    assert len(result.products) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fields,can_render", [(["name", "price_vnd"], True), (["price_vnd"], False)])
async def test_local_evidence_schema_cleanup_keeps_render_validation(fields, can_render):
    model = RecordedReplies({"choices": [{"product_id": "0", "evidence_fields": fields,
                                          "reason": "Mang theo để dùng khi cần."}]})
    composition, name = await _json_call(model, [{"role": "system", "content": "JSON"}],
                                         Composition, 100)
    assert len(model.calls) == 1
    assert composition.choices[0].evidence_fields == (["name"] if can_render else [])
    plan = Plan(need="Chọn một món", decision_mode="recommend")
    if can_render:
        assert _render_structured(composition, options(), plan, name).products
    else:
        with pytest.raises(GroqProviderError, match="evidence"):
            _render_structured(composition, options(), plan, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,match", [
    ({"product_id": "unknown", "reason": "Mang theo."}, "ID candidates"),
    ({"product_id": "0", "reason": "Giúp điều trị mụn."}, "nhãn chứng minh"),
])
async def test_local_schema_cleanup_cannot_authorize_unknown_skus_or_claims(choice, match):
    choice = {**choice, "evidence_fields": ["name", "price_vnd"]}
    model = RecordedReplies({"choices": [choice]})
    composition, name = await _json_call(model, [{"role": "system", "content": "JSON"}],
                                         Composition, 100)
    with pytest.raises(GroqProviderError, match=match):
        _render_structured(composition, options(), Plan(need="Chọn một món", decision_mode="recommend"), name)


@pytest.mark.asyncio
async def test_local_schema_cleanup_does_not_hide_other_schema_errors():
    bad = {"choices": [{"product_id": "0", "quantity": 0,
                         "evidence_fields": ["name", "price_vnd"]}]}
    model = RecordedReplies(bad, bad)
    with pytest.raises(GroqProviderError, match="invalid structured"):
        await _json_call(model, [{"role": "system", "content": "JSON"}], Composition, 100)
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,scope,explicit,has_candidates,should_repair", [
    ("browse", "none", False, True, False),
    ("recommend", "item", False, True, False),
    ("browse", "none", True, True, True),
    ("browse", "none", False, False, True),
    ("basket", "basket", False, True, True),
    ("browse", "basket", False, True, True),
    ("compare", "none", False, True, True),
])
async def test_optional_alternative_repair_does_not_skip_required_basket_types(
        monkeypatch, mode, scope, explicit, has_candidates, should_repair):
    from types import SimpleNamespace
    from backend.services.consultation import Advice

    products = options()
    plan = {"need": "Mua đồ ăn", "decision_mode": mode, "budget_scope": scope,
            "searches": [{"query": "bánh mì"}, {"query": "ngũ cốc"}]}
    model = RecordedReplies(plan)
    repaired_indices = []

    async def retrieve(*args):
        return ({"0": products["0"]} if has_candidates else {}), [1]

    async def repair(groq, repository, current, missing, *args):
        repaired_indices.extend(missing)
        return current.searches

    async def compose(groq, messages, candidates, current, *args):
        return Advice("Đã kiểm tra luồng.", "test", tuple(candidates.values()), current)

    monkeypatch.setattr("backend.services.consultation._retrieve_candidates", retrieve)
    monkeypatch.setattr("backend.services.consultation._repair_retrieval", repair)
    monkeypatch.setattr("backend.services.consultation._compose_and_review", compose)
    repo = SimpleNamespace(products=tuple(products.values()), get_product_snapshot=products.get)
    await consult(model, repo, "Mua bánh mì và ngũ cốc" if explicit else "Mua đồ ăn", [], [])
    assert repaired_indices == ([1] if should_repair else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("text,changed,expected", [
    ("Thế mua cho nữ thì sao?", False, "dầu gội"),
    ("Dầu gội cho nữ nhé", False, "dầu gội"),
    ("Giờ mình cần khăn giấy", True, "khăn giấy"),
])
async def test_topic_override_requires_a_new_complete_type_in_normalized_spelling(
        monkeypatch, text, changed, expected):
    from backend.services.consultation import Advice

    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    previous = Plan(need="Dầu gội chống gàu", decision_mode="recommend",
        searches=[Search(query="dầu gội", required_phrases=["chống gàu"])],
        budget_vnd=150000, budget_scope="item", requirements={"purpose": "chống gàu"},
        requirement_quotes={"purpose": "chống gàu"}, source_user_text="Dầu gội chống gàu").model_dump()
    script = RecordedReplies({"need": "Dầu gội chống gàu cho nữ", "decision_mode": "recommend",
        "reuse_previous": True, "searches": [{"query": "dầu gội",
                                                  "required_phrases": ["chống gàu"]}]})
    model = GroqService(Settings(groq_api_key="test"))
    model.chat = script.chat

    async def compose(groq, messages, candidates, current, *args):
        return Advice("Đã kiểm tra chủ đề.", "test", tuple(candidates.values()), current)

    monkeypatch.setattr("backend.services.consultation._compose_and_review", compose)
    prior = repo.search(query="Selsun", in_stock=True, limit=1)
    result = await consult(model, repo, text, [], [p.product_id for p in prior], previous)
    assert result.plan.topic_changed is changed
    assert normalize_vietnamese(result.plan.searches[0].query) == normalize_vietnamese(expected)
    assert result.products
    assert all(expected in p.name.casefold() for p in result.products)
    if changed:
        assert result.plan.requirements == {}
    else:
        assert result.plan.requirements["purpose"] == "chống gàu"
        if text == "Thế mua cho nữ thì sao?":
            assert result.plan.requirements["recipient"] == text
    assert result.plan.budget_vnd == (None if changed else 150000)
