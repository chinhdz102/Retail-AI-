import json

import pytest

from backend.products.catalog import ProductSnapshot
from backend.services.consultation import (
    Plan,
    Search,
    _explicit_budget,
    _matching_candidates,
    _parse_answer,
    _rank_fallback,
    _product_type_rank,
    _render_answer,
    _render_structured,
    Composition,
    consult,
    use_consultation,
)
from backend.services.groq import GroqChatResult, GroqProviderError


@pytest.fixture(autouse=True)
def isolate_structural_checks_from_semantic_reviewer(monkeypatch):
    # These tests exercise catalog/price/state rules; semantic rejection and
    # repair have their own integration tests in test_consultation_review.py.
    from backend.services.consultation import AdviceReview

    async def accept(*args):
        return AdviceReview(acceptable=True)

    monkeypatch.setattr("backend.services.consultation._review_advice", accept)


class Scripted:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def chat(self, messages, **options):
        self.calls.append((messages, options))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if "choices" in result:
            result = {**result, "choices": [dict(evidence=["Thịt"], **c)
                if "evidence" not in c else c for c in result["choices"]]}
        text = json.dumps(result, ensure_ascii=False)
        return GroqChatResult(text, "actual-model")


class Repository:
    def __init__(self):
        self.product = ProductSnapshot(
            product_id="P1",
            name="Thịt bò 200g",
            category="thịt",
            price_vnd=50_000,
            stock=2,
        )
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return [self.product]

    def get_product_snapshot(self, pid):
        return self.product if pid == "P1" else None


@pytest.mark.asyncio
async def test_composition_sees_full_context_and_price_and_total_come_from_data():
    repo = Repository()
    model = Scripted(
        {
            "need": "bữa tối",
            "searches": [{"query": "thịt bò"}],
            "budget_vnd": 200000,
            "budget_scope": "basket",
        },
        {
            "advice": "Mình chọn thịt để nấu nhanh.",
            "choices": [{"product_id": "P1", "quantity": 2}],
            "selection_mode": "basket",
            "question": "Nhà mình có rau chưa?",
        },
    )
    history = [{"role": "user", "content": "Nhà có gạo rồi, không lấy gạo."}]
    answer = await consult(model, repo, "Chọn giúp t", history, ["P1"])
    assert "Thịt bò 200g × 2 (100.000đ)" in answer.text
    assert "Tổng: 100.000đ" in answer.text
    assert answer.model == "actual-model"
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert model.calls[1][0][2:-1] == history
    assert context["previous_products"][0]["product_id"] == "P1"
    assert context["candidates"][0]["price_vnd"] == 50000
    assert model.calls[0][1]["max_tokens"] >= 1000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choice", [{"product_id": "INVENTED"}, {"product_id": "P1", "quantity": 3}]
)
async def test_unknown_product_and_over_budget_basket_never_escape_to_customer(choice):
    model = Scripted(
        {
            "need": "bữa tối",
            "searches": [{"query": "thịt bò"}],
            "budget_vnd": 100000,
            "budget_scope": "basket",
        },
        {"advice": "Gợi ý", "choices": [choice], "selection_mode": "basket"},
        {"advice": "Gợi ý", "choices": [choice], "selection_mode": "basket"},
    )
    with pytest.raises(GroqProviderError):
        await consult(model, Repository(), "Mua đồ ăn", [], [])


@pytest.mark.asyncio
async def test_strict_item_budget_excludes_equal_price_before_model_selection():
    model = Scripted(
        {
            "need": "thịt",
            "searches": [{"query": "thịt bò"}],
            "budget_vnd": 50000,
            "budget_scope": "item",
            "strict_budget": True,
        },
        {"advice": "Mình đổi sang món khác cho vừa ngân sách.", "choices": []},
    )
    repo = Repository()
    await consult(model, repo, "Dưới 50 nghìn", [], [])
    assert json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])["candidates"] == []
    assert repo.calls[0]["price_operator"] == "LT"
    assert repo.calls[0]["price_value_vnd"] == 50000


@pytest.mark.asyncio
async def test_invalid_choice_is_repaired_before_answer_is_saved():
    model = Scripted(
        {"need": "thịt", "searches": [{"query": "thịt"}]},
        {"advice": "Gợi ý", "choices": [{"product_id": "NONEXISTENT"}]},
        {"advice": "Chọn loại này để nấu nhanh.", "choices": [{"product_id": "P1"}]},
    )
    answer = await consult(model, Repository(), "Tư vấn giúp", [], [])
    assert "NONEXISTENT" not in answer.text
    assert [p.product_id for p in answer.products] == ["P1"]


@pytest.mark.asyncio
async def test_schema_repair_recovers_without_replacing_customer_history():
    model = Scripted(
        {"need": "thịt", "searches": [{"query": "thịt"}]},
        {"advice": "", "choices": []},
        {"advice": "Chọn loại này để nấu nhanh.", "choices": [{"product_id": "P1"}]},
    )
    history = [{"role": "user", "content": "Mua thịt nấu lấu"}]
    await consult(model, Repository(), "Tư vấn giúp", history, [])
    assert history == [{"role": "user", "content": "Mua thịt nấu lấu"}]
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_fresh_topic_does_not_reuse_old_products_when_searches_are_empty():
    model = Scripted(
        {"need": "vị trí cửa hàng", "searches": []},
        {"advice": "Bạn muốn tìm khu nào?", "choices": []},
    )
    await consult(model, Repository(), "Hỏi chỗ khác", [], ["P1"])
    assert json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])["candidates"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed,expected", [(False, 30000), (True, None)])
async def test_budget_memory_survives_followup_and_clears_on_new_topic(changed, expected):
    model = Scripted(
        {"need": "mua hàng", "searches": [{"query": "hàng"}], "topic_changed": changed},
        {"advice": "Bạn chọn nhóm nào?", "choices": []},
    )
    result = await consult(model, Repository(), "Chọn giúp", [], [], {
        "budget_vnd": 30000, "budget_scope": "item", "strict_budget": True,
    })
    assert result.plan.budget_vnd == expected


@pytest.mark.asyncio
async def test_named_type_can_cross_store_category_boundaries():
    repo = Repository()
    repo.products = (repo.product,)
    model = Scripted(
        {"need": "mua hàng", "searches": [{"query": "thịt bò", "category": "nhóm khác"}]},
        {"advice": "Chọn món này.", "choices": [{"product_id": "P1"}]},
    )
    result = await consult(model, repo, "Mua thịt", [], [])
    assert result.products == (repo.product,)
    assert repo.calls[0]["category"] is None


def test_precise_scalar_facts_keep_fact_path_but_budget_and_advice_use_llm():
    assert not use_consultation("Dầu gội X giá bao nhiêu?", ("price_vnd",))
    assert not use_consultation("Thành phần sữa này?", ("ingredients",))
    assert use_consultation("Nước giặt ở đâu?", ("shelf",))
    assert use_consultation("Sữa hộp xanh còn hàng không?", ("stock",))
    assert use_consultation("200 nghìn mua đồ ăn tối cho hai người", ("price_vnd",))
    assert use_consultation("Có loại nào điều trị mụn không?", ())
    assert use_consultation("Tư vấn cho t với", ())


@pytest.mark.asyncio
async def test_customer_ledger_survives_history_truncation_and_supports_explicit_removal():
    previous = {"requirements": {"avoid": "Không lấy thịt gà", "size": "chai 1 lít"}}
    model = Scripted({"need": "thịt", "requirement_updates": [
        {"key": "size", "action": "remove", "source_quote": "Bỏ yêu cầu dung tích"},
        {"key": "invented", "value": "An toàn cho mèo", "source_quote": "khách không hề nói"},
    ], "requirements": {"injected": "không có nguồn"}}, {"advice": "Mình giữ các yêu cầu còn lại."})
    answer = await consult(model, Repository(), "Bỏ yêu cầu dung tích nhé", [], [], previous)
    assert answer.plan.requirements == {"avoid": "Không lấy thịt gà"}
    evidence = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert evidence["plan"]["requirements"] == {"avoid": "Không lấy thịt gà"}


@pytest.mark.asyncio
async def test_validated_package_quote_survives_outside_recent_messages():
    spec = {"dimension": "mass_g", "operator": "eq", "value": 500, "source_quote": "gói 500g"}
    model = Scripted({"need": "thịt", "searches": [{"query": "thịt", "package_constraints": [spec]}]},
                     {"advice": "Chưa có gói đúng cỡ này."})
    result = await consult(model, Repository(), "Nhắc lại yêu cầu giúp mình", [], [],
                           {"searches": [{"package_constraints": [spec]}]})
    assert result.plan.searches[0].package_constraints[0].value == 500
    assert not result.products


def test_named_ingredient_prefers_its_category_over_a_flavoured_snack():
    meat = Repository().product.model_copy(update={"category": "Thịt"})
    snack = meat.model_copy(update={"product_id": "S", "name": "Bánh bao nhân thịt bò", "category": "Bánh bao"})
    assert _matching_candidates([snack, meat], Search(query="thịt bò", category="Thịt")) == [meat]


def test_category_supplies_generic_noun_without_inventing_an_ingredient():
    vegetable = Repository().product.model_copy(update={"name": "Cải ngọt WinEco 300g", "category": "Rau Lá"})
    assert _matching_candidates([vegetable], Search(query="rau", category="Rau Lá")) == [vegetable]
    assert not _matching_candidates([vegetable], Search(query="rau chân vịt", category="Rau Lá"))


def test_head_product_and_taxonomy_rank_ahead_of_flavour_mentions():
    base = Repository().product
    rice = base.model_copy(update={"name": "Gạo thơm túi 1kg", "category": "Gạo - Nông Sản Khô"})
    snack = base.model_copy(update={"name": "Bánh gạo vị bò", "category": "Bánh Xốp - Bánh Quy"})
    assert _product_type_rank(rice, "gạo") < _product_type_rank(snack, "gạo")
    chicken = base.model_copy(update={"name": "Ức gà 500g", "category": "Thịt"})
    pastry = base.model_copy(update={"name": "Bánh bao nhân thịt gà", "category": "Bánh bao"})
    assert _product_type_rank(chicken, "thịt gà") < _product_type_rank(pastry, "thịt gà")


@pytest.mark.asyncio
@pytest.mark.parametrize("named_by_customer", [True, False])
async def test_only_planner_chosen_type_can_back_off_inside_category(named_by_customer):
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Cải ngọt 300g", "category": "Rau Lá"})
    repo.products = (repo.product,)
    choices = [] if named_by_customer else [{"product_id": "P1", "evidence": ["Cải ngọt"]}]
    model = Scripted({"need": "chọn rau cho bữa tối", "searches": [{"query": "rau muống", "category": "Rau Lá"}]},
                     {"advice": "Chọn loại rau đang phù hợp.", "choices": choices})
    result = await consult(model, repo, "Mua rau muống" if named_by_customer else "Chọn rau cho bữa tối", [], [])
    assert bool(result.products) is not named_by_customer


def test_alternative_gifts_are_not_added_as_a_single_basket():
    p = Repository().product
    other = p.model_copy(update={"product_id": "P2", "name": "Hộp quà 300g"})
    answer = _render_structured(Composition(choices=[
        {"product_id": "P1", "evidence": ["Thịt"]},
        {"product_id": "P2", "evidence": ["Hộp quà"]},
    ], selection_mode="alternatives"), {"P1": p, "P2": other},
        Plan(need="chọn quà", decision_mode="compare", budget_scope="basket", budget_vnd=50000), "model")
    assert len(answer.products) == 2 and "Tổng:" not in answer.text


def test_meal_basket_cannot_avoid_total_budget_by_calling_its_items_alternatives():
    p = Repository().product
    other = p.model_copy(update={"product_id": "P2"})
    with pytest.raises(GroqProviderError, match="ngân sách tổng"):
        _render_structured(Composition(choices=[
            {"product_id": "P1", "evidence": ["Thịt"]},
            {"product_id": "P2", "evidence": ["Thịt"]},
        ], selection_mode="alternatives"), {"P1": p, "P2": other},
            Plan(need="một bữa", decision_mode="basket", budget_scope="basket", budget_vnd=50000), "model")


@pytest.mark.parametrize("text", ["Không có trong catalog.", "Theo dữ liệu hiện tại, chưa có.",
                                  "Hiện không có trong danh sách sản phẩm."])
def test_internal_lookup_language_is_repaired_before_delivery(text):
    with pytest.raises(GroqProviderError, match="nội bộ"):
        _parse_answer(text, {}, Plan(need="chọn đồ"))


def test_unstated_age_group_and_upsell_are_rejected():
    with pytest.raises(GroqProviderError, match="nhóm tuổi"):
        _render_structured(Composition(advice="Dùng cho da bé nhé."), {},
                           Plan(need="da nhạy cảm"), "model")
    with pytest.raises(GroqProviderError, match="upsell"):
        _render_structured(Composition(advice="Mình chưa rõ vị trí.",
                                       question="Bạn có muốn mình chọn thêm không?"), {},
                           Plan(need="hỏi vị trí"), "model")


def test_label_alternatives_use_or_while_required_variants_stay_and():
    p = Repository().product.model_copy(update={"name": "Trà không đường 500ml", "category": "Trà"})
    assert _matching_candidates([p], Search(query="trà", any_phrases=["ít đường", "không đường"])) == [p]
    assert not _matching_candidates([p], Search(query="trà", required_phrases=["ít đường", "không đường"]))


def test_duplicate_followup_is_rendered_once():
    question = "Bạn có cần loại dành cho tóc khô không?"
    answer = _render_structured(Composition(advice="Mình chọn theo nhu cầu làm sạch. " + question,
                                           question=question), {}, Plan(need="dầu gội"), "model")
    assert answer.text.count(question) == 1


def test_exact_selected_identity_in_reason_is_deduplicated_without_losing_usage():
    p = Repository().product
    answer = _render_structured(Composition(choices=[{
        "product_id": "P1", "reason": p.name + " dùng để nấu chín món xào.",
        "evidence": ["Thịt bò"],
    }]), {"P1": p}, Plan(need="thịt"), "model")
    assert answer.text.count(p.name) == 1
    assert "dùng để nấu chín món xào" in answer.text


def test_label_selection_guidance_is_allowed_but_selected_sku_claim_is_rejected():
    answer = _parse_answer("Bạn nên chọn sữa tắm có nhãn ghi rõ dịu nhẹ cho da nhạy cảm.",
                           {}, Plan(need="sữa tắm"))
    assert answer.advice
    with pytest.raises(GroqProviderError):
        _parse_answer("[[P1]] hương hoa Lily nhẹ, phù hợp với môi trường có mèo.",
                      {"P1": Repository().product}, Plan(need="nước lau sàn"))


def test_literal_full_name_resolves_unicode_spacing_to_real_card_and_price():
    product = Repository().product.model_copy(update={"name": "Thịt bò Úc 200g"})
    candidates = {"P1": product}
    plan = Plan(need="bữa tối", budget_vnd=110000, budget_scope="basket")
    answer = _parse_answer("Mình chọn thịt  bò  úc  200g.", candidates, plan)
    rendered = _render_answer(answer, candidates, plan, "test")
    assert rendered.products == (product,)
    assert rendered.text == "Mình chọn Thịt bò Úc 200g (50.000đ).\n\nTổng: 50.000đ."
    # Similar words must never be used to map a different item to this SKU.
    assert not _parse_answer("Thịt bò Úc 500g", candidates, plan).choices


@pytest.mark.parametrize("text", [
    "Chọn [[P1]] giá 40.000đ.",
    "Chọn [[UNKNOWN]].",
    "Chọn [[P1|21]].",
    "Chọn [[P1]] rồi lấy [[P1|2]].",
    "Chọn [[P1|3]].",
    "Chọn [[P1|]].",
])
def test_grounding_rejects_unknown_prices_ids_quantities_and_over_budget_total(text):
    with pytest.raises(GroqProviderError):
        _parse_answer(text, {"P1": Repository().product},
                      Plan(need="bữa tối", budget_vnd=100000, budget_scope="basket"))


@pytest.mark.parametrize("text", [
    "[[P1]] không gây kích ứng.",
    "[[P1]] phù hợp cho da nhạy cảm.",
    "[[P1]] an toàn cho mèo.",
    "[[P1]] không nồng.",
    "[[P1]] tốt cho tiêu hóa.",
    "Mình không bán thêm. [[P1]] ít gây kích ứng hơn.",
    "[[P1]] chứa sữa và yến mạch, công thức dịu nhẹ phù hợp cho da nhạy cảm.",
    "[[P1]] giàu canxi.",
    "[[P1]] vị rau giúp giảm độ ngọt và dễ tiêu.",
    "[[P1]] hâm trong lò vi sóng 30-45 giây rồi ăn lạnh.",
    "[[P1]] ít đường và không quá ngọt.",
])
def test_specific_safety_benefits_require_product_evidence(text):
    with pytest.raises(GroqProviderError):
        _parse_answer(text, {"P1": Repository().product}, Plan(need="chọn sản phẩm"))


def test_general_selection_criteria_and_limits_are_not_rejected_as_product_claims():
    text = "Nên chọn loại phù hợp cho da nhạy cảm; mình chưa biết nhãn của [[P1]] có ghi không."
    assert _parse_answer(text, {"P1": Repository().product}, Plan(need="chọn sản phẩm"))


@pytest.mark.parametrize("text", [
    "Sữa rửa mặt không phải thuốc điều trị mụn.",
    "Mình chưa có cơ sở khẳng định sản phẩm này phù hợp cho da nhạy cảm.",
    "Không thể khẳng định [[P1]] an toàn cho mèo.",
])
def test_a_relevant_limit_is_not_mistaken_for_a_positive_product_claim(text):
    assert _parse_answer(text, {"P1": Repository().product}, Plan(need="tư vấn"))


@pytest.mark.parametrize("text", [
    "Mình chưa xem nhãn nhưng [[P1]] an toàn cho mèo.",
    "Không thể chữa bệnh nhưng [[P1]] không gây kích ứng.",
    "Nên chọn [[P1]] vì dịu nhẹ và không nồng.",
])
def test_unrelated_negation_or_selection_verb_does_not_waive_a_claim(text):
    with pytest.raises(GroqProviderError):
        _parse_answer(text, {"P1": Repository().product}, Plan(need="tư vấn"))


def test_basket_does_not_treat_price_per_kg_as_price_per_package():
    product = Repository().product.model_copy(update={"name": "Thịt bò", "uom": "kg"})
    with pytest.raises(GroqProviderError):
        _parse_answer("Lấy [[P1]].", {"P1": product}, Plan(need="bữa tối", budget_scope="basket"))


def test_weighed_item_uses_grams_and_integer_subtotal_in_mixed_basket():
    product = Repository().product.model_copy(update={
        "name": "Thịt ba chỉ", "uom": "kg", "price_vnd": 199900,
    })
    vegetable = Repository().product.model_copy(update={
        "product_id": "P2", "name": "Rau 300g", "price_vnd": 15000,
    })
    candidates = {"P1": product, "P2": vegetable}
    plan = Plan(need="bữa tối", budget_vnd=120000, budget_scope="basket")
    composition = _parse_answer("Lấy [[P1|500g]] và [[P2]].", candidates, plan)
    answer = _render_answer(composition, candidates, plan, "test")
    assert "Thịt ba chỉ — 500g (99.950đ)" in answer.text
    assert "Tổng: 114.950đ" in answer.text
    with pytest.raises(GroqProviderError):
        _parse_answer("Lấy [[P1|1000g]] và [[P2]].", candidates, plan)
    with pytest.raises(GroqProviderError):
        _parse_answer("Lấy [[P2|500g]].", candidates, plan)
    item_budget = Plan(need="thịt", budget_vnd=120000, budget_scope="item")
    with pytest.raises(GroqProviderError):
        _parse_answer("Lấy [[P1]].", candidates, item_budget)
    with pytest.raises(GroqProviderError):
        _parse_answer("Lấy [[P1|2]].", candidates, item_budget)
    assert _parse_answer("Lấy [[P1|500g]].", candidates, item_budget).choices


@pytest.mark.asyncio
async def test_budget_resolved_from_unanswered_turn_overrides_older_saved_plan():
    model = Scripted(
        {"need": "bữa tối", "searches": [{"query": "thịt bò"}],
         "budget_vnd": 100000, "budget_scope": "basket"},
        {"advice": "Lấy món này.", "choices": [{"product_id": "P1"}]},
    )
    answer = await consult(model, Repository(), "Chọn giúp", [
        {"role": "user", "content": "Giảm còn 100 nghìn nhé."},
    ], [], {"budget_vnd": 200000, "budget_scope": "basket"})
    assert answer.plan.budget_vnd == 100000


@pytest.mark.asyncio
async def test_ambiguous_identity_returns_one_planner_question_without_inventing_products():
    model = Scripted({
        "need": "Tìm sữa màu xanh, chưa rõ nhãn hiệu",
        "action": "ask", "clarification": "Bạn nhớ nhãn hiệu hoặc loại sữa không?",
    })
    answer = await consult(model, Repository(), "Sữa màu xanh ấy", [], [])
    assert answer.text == "Bạn nhớ nhãn hiệu hoặc loại sữa không?"
    assert answer.products == ()
    assert len(model.calls) == 1


@pytest.mark.parametrize("text", [
    "Mình có sẵn loại đó.",
    "Nước giặt nằm ở khu phía trong cửa hàng.",
    "Bạn tới dãy kệ gia dụng nhé.",
])
def test_composer_cannot_invent_current_inventory_or_physical_store_layout(text):
    with pytest.raises(GroqProviderError):
        _parse_answer(text, {}, Plan(need="hỏi hàng"))


def test_rendered_product_price_and_basket_total_are_shown_once():
    candidates = {"P1": Repository().product}
    plan = Plan(need="bữa tối", budget_vnd=100000, budget_scope="basket")
    composition = _parse_answer("Lấy [[P1]] (50.000đ).\n\nTổng: 50.000đ.", candidates, plan)
    answer = _render_answer(composition, candidates, plan, "test")
    assert "(50.000đ) (50.000đ)" not in answer.text
    assert answer.text.count("Tổng:") == 1


def test_category_backoff_does_not_add_unrelated_cheap_items_to_named_type():
    product = Repository().product
    products = [product.model_copy(update={"product_id": pid, "name": name}) for pid, name in [
        ("SRM", "Sữa rửa mặt làm sạch 100g"),
        ("MASK", "Mặt nạ dưỡng da 20g"),
        ("SOAP", "Sữa tắm thơm 500ml"),
    ]]
    assert [p.product_id for p in _rank_fallback(products, "sữa rửa mặt da dầu")] == ["SRM"]
    assert _rank_fallback(products, "dụng cụ nhà bếp") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query,product_type", [
    ("thịt bò nấu món ngon", "thịt bò"),
    ("dầu gội cho tóc dầu", "dầu gội"),
    ("sữa tắm dịu nhẹ cho da", "sữa tắm"),
])
async def test_query_backoff_recovers_product_type_without_use_case_in_title(query, product_type):
    class TypeRepository(Repository):
        def search(self, **criteria):
            self.calls.append(criteria)
            return [self.product] if criteria["query"] == product_type else []

    repo = TypeRepository()
    repo.product = repo.product.model_copy(update={"name": product_type + " 200g"})
    model = Scripted(
        {"need": "mua hàng", "searches": [{"query": query}],
         "budget_vnd": 100000, "budget_scope": "item"},
        {"advice": "Chọn loại này.", "choices": [{"product_id": "P1", "evidence": [product_type]}]},
    )
    answer = await consult(model, repo, "Tư vấn giúp", [], [])
    assert answer.products == (repo.product,)
    # The second call intentionally probes weighed items without a per-kg
    # price cap. The actual suffix-backoff calls must keep the spending limit.
    assert all(c["price_value_vnd"] == 100000 for c in [repo.calls[0], *repo.calls[2:]])


@pytest.mark.parametrize("text,expected", [
    ("Thịt bò hay ba chỉ cũng được, khoảng 200 nghìn.", (200000, False)),
    ("Tổng 100.000đ mua đồ ăn", (100000, False)),
    ("Dầu gội dưới 150k", (150000, True)),
    ("Ngân sách 1,5 triệu", (1500000, False)),
    ("Loại 1 lít, chai trước giá 50 nghìn", None),
    ("Lúc đầu tầm 100 nghìn, giờ lấy dưới 80 nghìn thôi.", (80000, True)),
    ("Tăng ngân sách lên 150 nghìn", (150000, False)),
    ("Giảm còn 100 nghìn nhé", (100000, False)),
    ("Giá tăng lên 150 nghìn rồi", None),
    ("Giá giảm còn 100 nghìn", None),
])
def test_explicit_numeric_budget_is_kept_without_confusing_volume_or_item_price(text, expected):
    assert _explicit_budget(text) == expected


@pytest.mark.asyncio
async def test_explicit_budget_cannot_be_dropped_by_model_or_previous_plan():
    model = Scripted(
        {"need": "thịt", "searches": [{"query": "thịt"}], "budget_vnd": None},
        {"advice": "Chọn loại này.", "choices": [{"product_id": "P1"}]},
    )
    answer = await consult(model, Repository(), "Thịt bò, khoảng 100 nghìn", [], [], {
        "budget_vnd": None, "budget_scope": "none", "strict_budget": False,
    })
    assert answer.plan.budget_vnd == 100000


def test_package_spec_uses_individual_size_and_does_not_sum_smaller_packages():
    base = Repository().product
    products = [base.model_copy(update={"product_id": pid, "name": name}) for pid, name in [
        ("small", "Nước uống chai 500ml"),
        ("two", "Lốc 2 chai nước uống x 500ml"),
        ("large", "Nước uống chai 1L"),
        ("ambiguous", "Nước uống chai 900ml/1L"),
    ]]
    query = Search(query="nước uống", package_constraints=[{
        "dimension": "volume_ml", "operator": "eq", "value": 1000,
    }])
    assert [p.product_id for p in _matching_candidates(products, query)] == ["large"]


def test_label_variants_keep_negation_and_do_not_use_category_as_evidence():
    base = Repository().product
    products = [base.model_copy(update={"product_id": pid, "name": name,
        "category": "Sữa không đường"}) for pid, name in [
        ("sweet", "Sữa có đường 1lít"),
        ("plain", "Sữa không đường 1lít"),
        ("small", "Sữa không đường 180ml"),
    ]]
    query = Search(query="sữa", required_phrases=["không đường"], package_constraints=[{
        "dimension": "volume_ml", "operator": "eq", "value": 1000,
    }])
    assert [p.product_id for p in _matching_candidates(products, query)] == ["plain"]


def test_named_type_prefers_exact_accented_label_over_scattered_metadata_words():
    base = Repository().product
    sponge = base.model_copy(update={"product_id": "sponge", "name": "Bông tắm tròn"})
    pad = base.model_copy(update={"product_id": "pad", "name": "Băng vệ sinh ban đêm",
                                  "description": "Bông, sữa tắm, phụ kiện"})
    assert _matching_candidates([pad, sponge], Search(query="bông tắm")) == [sponge]


def test_minimum_length_spec_excludes_short_and_unconfirmed_packages():
    base = Repository().product
    products = [base.model_copy(update={"product_id": str(i), "name": name})
                for i, name in enumerate([
                    "Băng vệ sinh ban đêm 30cm 8 miếng",
                    "Băng vệ sinh ban đêm 35cm 3 miếng",
                    "Băng vệ sinh ban đêm 40cm 8 miếng",
                    "Băng vệ sinh ban đêm 8 miếng",
                ])]
    query = Search(query="băng vệ sinh", package_constraints=[{
        "dimension": "length_cm", "operator": "gte", "value": 35,
    }])
    assert [p.product_id for p in _matching_candidates(products, query)] == ["1", "2"]


@pytest.mark.asyncio
async def test_new_variant_constraints_also_filter_remembered_products():
    model = Scripted(
        {"need": "thịt", "reuse_previous": True, "searches": [{
            "query": "thịt bò", "package_constraints": [{
                "dimension": "mass_g", "operator": "eq", "value": 500, "source_quote": "500g",
            }],
        }]},
        {"advice": "Bạn muốn lấy thịt cân lẻ không?"},
    )
    await consult(model, Repository(), "Gói 500g ấy", [], ["P1"])
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert context["candidates"] == []


def test_one_sku_cannot_borrow_another_skus_label_claim():
    base = Repository().product
    candidates = {
        "plain": base.model_copy(update={"product_id": "plain", "name": "Sữa không đường 1L"}),
        "sweet": base.model_copy(update={"product_id": "sweet", "name": "Sữa có đường 1L"}),
    }
    with pytest.raises(GroqProviderError):
        _parse_answer("[[plain]] ít đường. [[sweet]] ít đường.", candidates,
                      Plan(need="mua sữa"))


def test_structured_render_requires_repair_instead_of_silently_removing_the_reason():
    base = Repository().product.model_copy(update={"name": "Sữa rửa mặt 100g"})
    composition = Composition(advice="Da đang có mụn thì mình ưu tiên bước làm sạch trước.",
        choices=[{"product_id": "P1", "reason": "Làm sạch sâu, không gây kích ứng.",
                  "evidence": ["Sữa rửa mặt"]}])
    with pytest.raises(GroqProviderError, match="Sửa lý do"):
        _render_structured(composition, {"P1": base}, Plan(need="rửa mặt"), "test")


def test_processed_food_category_does_not_supply_the_missing_fresh_type_noun():
    base = Repository().product
    fresh = base.model_copy(update={"name": "Ba chỉ bò cắt lát 500g", "category": "Thịt"})
    processed = base.model_copy(update={"name": "Giò bò 500g", "category": "Xúc xích - Thịt nguội"})
    assert _matching_candidates([processed, fresh], Search(query="thịt bò")) == [fresh]


@pytest.mark.asyncio
async def test_failed_customer_turn_budget_is_not_lost_if_model_drops_it():
    model = Scripted(
        {"need": "thịt", "searches": [{"query": "thịt bò"}], "budget_vnd": None},
        {"advice": "Chọn loại này.", "choices": [{"product_id": "P1"}]},
    )
    result = await consult(model, Repository(), "Chọn giúp t", [{
        "role": "user", "content": "Thịt bò khoảng 100 nghìn.",
    }], [])
    assert result.plan.budget_vnd == 100000


@pytest.mark.asyncio
@pytest.mark.parametrize("quote,dimension,value", [
    ("một lít", "volume_ml", 1000),
    ("nửa cân", "mass_g", 500),
    ("ba mươi lăm xen ti mét", "length_cm", 35),
])
async def test_customer_spoken_package_source_survives_numeric_plan_validation(quote, dimension, value):
    model = Scripted(
        {"need": "chọn đúng quy cách", "searches": [{"query": "thịt bò",
         "package_constraints": [{"dimension": dimension, "value": value,
                                  "source_quote": quote}]}]},
        {"advice": "Bạn cần đúng quy cách này, mình sẽ giữ yêu cầu đó."},
    )
    result = await consult(model, Repository(), f"Loại {quote} ấy.", [], [])
    assert result.plan.searches[0].package_constraints[0].value == value


@pytest.mark.asyncio
async def test_serving_suggestion_cannot_become_a_customer_package_requirement():
    model = Scripted(
        {"need": "thịt ăn lẩu", "searches": [{"query": "thịt bò", "package_constraints": [
            {"dimension": "mass_g", "value": 500, "source_quote": "500g"}]}]},
        {"advice": "Chọn thịt thái mỏng để nhúng lẩu.", "choices": [{"product_id": "P1"}]},
    )
    result = await consult(model, Repository(), "Mua thịt ăn lẩu cho 4 người.", [{
        "role": "assistant", "content": "Gợi ý khoảng 500g thịt.",
    }], [])
    assert not result.plan.searches[0].package_constraints


def test_valid_introduction_and_multiple_reasons_do_not_hit_intro_field_length_cap():
    base = Repository().product
    candidates = {f"P{i}": base.model_copy(update={"product_id": f"P{i}", "name": name})
        for i, name in enumerate(["Thịt bò 200g", "Thịt heo 200g", "Thịt gà 200g"], 1)}
    text = "Mình gợi ý chọn món theo cách chế biến bạn thường dùng. " * 5
    for pid in candidates:
        text += f"\n[[{pid}]]: " + "Bạn chia thành từng phần rồi nấu chín trước khi ăn. " * 4
    assert 700 < len(text) < 1800
    result = _parse_answer(text, candidates, Plan(need="chọn món"))
    assert len(result.choices) == 3
    answer = _render_answer(result, candidates, Plan(need="chọn món"), "test")
    assert "Thịt heo 200g (50.000đ)" in answer.text


def test_multiplying_packages_cannot_bypass_an_item_spending_limit():
    with pytest.raises(GroqProviderError):
        _parse_answer("Lấy [[P1|3]].", {"P1": Repository().product},
                      Plan(need="mua thịt", budget_vnd=100000, budget_scope="item"))


@pytest.mark.asyncio
async def test_owned_items_filter_new_and_remembered_candidates_and_survive_short_history():
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Gạo thơm túi 1kg", "category": "Gạo"})
    previous = {"requirements": {"owned_rice": "đã có gạo"}, "purchase_exclusions": {
        "owned_rice": {"query": "gạo", "category": "Gạo"},
    }}
    model = Scripted({"need": "món ăn kèm cơm", "reuse_previous": True,
                      "searches": [{"query": "gạo"}]},
                     {"advice": "Mình chọn món ăn kèm cơm, không mua thêm gạo."})
    answer = await consult(model, repo, "Nhắc lại phương án cuối", [], ["P1"], previous)
    assert not answer.products
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert not context["candidates"]
    assert answer.plan.purchase_exclusions["owned_rice"].query == "gạo"


@pytest.mark.asyncio
@pytest.mark.parametrize("topic_changed,action,expected", [
    (False, "set", False), (False, "remove", True), (True, "set", True),
])
async def test_purchase_exclusion_has_source_and_can_be_released_or_reset(topic_changed, action, expected):
    repo = Repository()
    previous = {"requirements": {"refused": "không lấy thịt bò"},
                "purchase_exclusions": {"refused": {"query": "thịt bò"}}}
    plan = {"need": "mua thịt", "searches": [{"query": "thịt bò"}],
            "topic_changed": topic_changed, "requirement_updates": []}
    if not topic_changed:
        plan["requirement_updates"] = [{"key": "refused", "action": action,
            "value": "không lấy thịt bò", "source_quote": "thịt bò",
            "purchase_exclusion": {"query": "thịt bò"}}]
    model = Scripted(plan, {"advice": "Mình giữ đúng yêu cầu bạn vừa đổi."})
    answer = await consult(model, repo, "thịt bò", [], [], previous)
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert bool(context["candidates"]) is expected


@pytest.mark.asyncio
async def test_response_style_cannot_inject_an_unquoted_product_exclusion():
    model = Scripted({"need": "mua thịt bò", "searches": [{"query": "thịt bò"}],
        "purchase_exclusions": {"invented": {"query": "thịt bò"}},
        "requirement_updates": [{"key": "style", "value": "nói ngắn",
            "source_quote": "không cần quảng cáo công dụng",
            "purchase_exclusion": {"query": "thịt bò"}}]},
        {"choices": [{"product_id": "P1"}]})
    answer = await consult(model, Repository(), "không cần quảng cáo công dụng", [], [])
    assert len(answer.products) == 1 and not answer.plan.purchase_exclusions


def test_owned_rice_does_not_exclude_a_different_category_with_rice_as_ingredient():
    from backend.services.consultation import _excluded_purchase
    snack = Repository().product.model_copy(update={"name": "Bánh gạo 100g", "category": "Bánh"})
    assert not _excluded_purchase(snack, [Search(query="gạo", category="Gạo")])


@pytest.mark.asyncio
async def test_removed_budget_is_not_resurrected_from_a_stale_user_turn():
    model = Scripted({"need": "mua thịt", "searches": [{"query": "thịt bò"}]},
                     {"choices": [{"product_id": "P1"}]})
    answer = await consult(model, Repository(), "Chọn chốt giúp mình", [
        {"role": "user", "content": "Mua thịt khoảng 40 nghìn"},
        {"role": "user", "content": "Thôi bỏ giới hạn tiền đi"},
    ], [], {"budget_vnd": None, "budget_scope": "none", "budget_action": "remove"})
    assert answer.plan.budget_vnd is None and len(answer.products) == 1


@pytest.mark.asyncio
async def test_failed_planner_budget_correction_after_processed_turn_is_recovered():
    model = Scripted({"need": "mua thịt", "searches": [{"query": "thịt bò"}],
                      "budget_vnd": 100000}, {"choices": [{"product_id": "P1"}]})
    answer = await consult(model, Repository(), "Chọn chốt", [
        {"role": "user", "content": "Mua thịt khoảng 100 nghìn"},
        {"role": "assistant", "content": "Mình gợi ý thịt."},
        {"role": "user", "content": "Tăng ngân sách lên 150 nghìn"},
    ], [], {"budget_vnd": 100000, "budget_scope": "item",
            "source_user_text": "Mua thịt khoảng 100 nghìn"})
    assert answer.plan.budget_vnd == 150000
    assert answer.plan.source_user_text == "Chọn chốt"


@pytest.mark.asyncio
async def test_keep_budget_cannot_restore_amount_from_previous_topic_even_if_model_emits_it():
    model = Scripted({"need": "mua thịt", "searches": [{"query": "thịt bò"}],
                      "budget_vnd": 40000, "budget_action": "keep"},
                     {"choices": [{"product_id": "P1"}]})
    result = await consult(model, Repository(), "Chọn giúp", [
        {"role": "user", "content": "Mua nước dưới 40 nghìn"},
        {"role": "user", "content": "Giờ chọn thịt, chưa giới hạn tiền"},
    ], [], {"budget_vnd": None, "budget_scope": "none",
            "source_user_text": "Giờ chọn thịt, chưa giới hạn tiền"})
    assert result.plan.budget_vnd is None and result.products
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert "previous_plan" not in context


def test_recommendation_requires_a_decision_and_guidance_does_not_upsell():
    p = Repository().product
    candidates = {"P1": p, "P2": p.model_copy(update={"product_id": "P2"})}
    with pytest.raises(GroqProviderError, match="chốt một"):
        _render_structured(Composition(choices=[{"product_id": "P1"}, {"product_id": "P2"}]),
                           candidates, Plan(need="chọn giúp", decision_mode="recommend"), "test")
    with pytest.raises(GroqProviderError, match="hướng dẫn chung"):
        _render_structured(Composition(choices=[{"product_id": "P1"}]), candidates,
                           Plan(need="cách dùng", decision_mode="guide"), "test")


@pytest.mark.parametrize("claim", [
    "Thích hợp cho da đầu dầu.", "Phù hợp với tóc dầu.", "Hợp cho da nhạy cảm.",
])
def test_mint_or_brand_does_not_prove_compatibility_with_hair_or_skin(claim):
    p = Repository().product.model_copy(update={"name": "Dầu gội bạc hà chai 180g"})
    with pytest.raises(GroqProviderError, match="Sửa lý do"):
        _render_structured(Composition(choices=[{"product_id": "P1", "reason": claim,
                                               "evidence": ["Dầu gội bạc hà"]}]),
                           {"P1": p}, Plan(need="dầu gội"), "test")


@pytest.mark.parametrize("price,valid", [("50.000đ", True), ("50\u202f000\u202fđ", True), ("49.000đ", False)])
def test_only_a_verified_repeated_price_can_be_removed_from_the_reason(price, valid):
    p = Repository().product
    draft = Composition(choices=[{"product_id": "P1", "reason": f"Giá {price}, thái mỏng để xào.",
                                  "evidence": ["Thịt bò"]}])
    if valid:
        answer = _render_structured(draft, {"P1": p}, Plan(need="thịt"), "test")
        assert answer.text == "Thịt bò 200g (50.000đ): thái mỏng để xào"
    else:
        with pytest.raises(GroqProviderError, match="giá/tổng"):
            _render_structured(draft, {"P1": p}, Plan(need="thịt"), "test")


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Chưa thấy trong catalog, bạn muốn loại nào?", "Bạn mua gì? Ngân sách bao nhiêu?",
])
async def test_planner_clarifications_cannot_bypass_customer_facing_text_rules(question):
    model = Scripted({"need": "xác định sản phẩm", "action": "ask", "clarification": question})
    with pytest.raises(GroqProviderError) as raised:
        await consult(model, Repository(), "Chọn giúp", [], [])
    assert raised.value.consultation_plan["source_user_text"] == "Chọn giúp"


def test_shampoo_mass_is_not_described_as_volume():
    p = Repository().product.model_copy(update={"name": "Dầu gội bạc hà 180g"})
    with pytest.raises(GroqProviderError, match="khối lượng"):
        _render_structured(Composition(choices=[{"product_id": "P1",
            "reason": "Dung tích 180 g, tiện mang theo.", "evidence": ["180g"]}]),
            {"P1": p}, Plan(need="dầu gội"), "test")


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [True, False])
async def test_planner_pantries_are_not_automatically_added_to_a_meal(requested):
    repo = Repository()
    repo.product = repo.product.model_copy(update={"name": "Nước mắm cá cơm 500ml", "category": "Nước mắm"})
    model = Scripted({"need": "nấu bữa tối", "searches": [{"query": "nước mắm", "role": "pantry"}]},
                     {"advice": "Mình giữ các món chính, không mua thêm đồ chưa cần."})
    await consult(model, repo, "Mua thêm nước mắm" if requested else "Mua nguyên liệu nấu bữa tối", [], [])
    context = json.loads(model.calls[1][0][1]["content"].split(": ", 1)[1])
    assert bool(context["candidates"]) is requested


@pytest.mark.asyncio
async def test_all_consultation_stages_keep_both_configured_fallback_models():
    from types import SimpleNamespace
    model = Scripted({"need": "thịt bò", "searches": [{"query": "thịt bò"}]},
                     {"choices": [{"product_id": "P1"}]})
    model.settings = SimpleNamespace(groq_fallback_model="openai/gpt-oss-120b",
                                     groq_fallback_model_2="openai/gpt-oss-20b")
    answer = await consult(model, Repository(), "Chọn thịt bò giúp mình", [], [])
    assert answer.products
    assert all(options["fallback_models"] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
               for _, options in model.calls)
