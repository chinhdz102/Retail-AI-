"""Regression checks for catalog-wide Vietnamese consultation routing."""

import json

import pytest

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.consultation import (
    Search,
    _availability_query,
    _attach_catalog_options,
    _catalog_price_followup_plan,
    _catalog_family_plan,
    _purchase_weight_followup_plan,
    _fast_catalog_advice,
    _fast_catalog_plan,
    _hotpot_requested,
    _matching_candidates,
    _retrieve_candidates,
)
from backend.services.retail_tools import execute_catalog_tool


@pytest.fixture(scope="module")
def snapshot():
    return get_product_repository(Settings(catalog_repository="snapshot"))


def test_availability_parser_drops_yes_no_particle(snapshot):
    assert _availability_query("Ở đây có bán sữa tươi không?") == "sữa tươi"
    assert _availability_query("có viên thả bồn cầu không") == "viên thả bồn cầu"


def test_broad_milk_family_uses_catalog_category_heads(snapshot):
    plan = _catalog_family_plan("thế có các loại sữa nào", snapshot)
    assert plan and len(plan.searches) >= 2
    assert all(search.category.startswith("Sữa") for search in plan.searches)


def test_broad_meat_question_with_món_framing_uses_catalog_category_heads(snapshot):
    plan = _catalog_family_plan("Cho tao biết là có món thịt nào nhỉ?", snapshot)
    assert plan and len(plan.searches) >= 2
    assert all(search.category.startswith("Thịt") for search in plan.searches)


def test_ready_drink_family_is_catalog_browseable_without_llm(snapshot):
    plan = _fast_catalog_plan("t muốn uống nước giải khát", None, [], snapshot)
    assert plan and plan.searches
    assert plan.searches[0].category == "Nước Ngọt"


def test_family_count_uses_catalog_taxonomy(snapshot):
    plan = _fast_catalog_plan("Cho ta hỏi có bao nhiêu loài thịt?", None, [], snapshot)
    assert plan and "family_count" in plan.constraints
    assert [search.category for search in plan.searches] == ["Thịt", "Thịt Đông Lạnh"]


def test_noisy_multiword_product_phrase_stays_catalog_grounded(snapshot):
    plan = _fast_catalog_plan("Ồ, tôi đau thịt bò đi.", None, [], snapshot)
    assert plan and plan.searches[0].query == "Thịt bò"


def test_loose_weight_followup_keeps_catalog_scope(snapshot):
    previous = _fast_catalog_plan("Ở tao muốn mua thịt bò.", None, [], snapshot)
    assert previous
    current = _purchase_weight_followup_plan(
        "Vào tầm 1 cân thịt bò ấy.", previous.model_dump(), snapshot,
    )
    assert current and current.requirement_updates
    assert current.requirement_updates[0].purchase_weight
    assert current.requirement_updates[0].purchase_weight.weight_g == 1000
    assert current.searches[0].query == previous.searches[0].query


def test_price_followup_sorts_the_active_catalog_scope(snapshot):
    previous = _fast_catalog_plan(
        "Ờ thì cho tao hỏi là có loại dầu gội đầu nào không?", None, [], snapshot
    )
    assert previous
    current = _catalog_price_followup_plan("Loại nào rẻ nhất?", previous.model_dump())
    assert current and current.decision_mode == "recommend"
    assert "price_order:min" in current.constraints


@pytest.mark.asyncio
async def test_browse_keeps_full_catalog_for_chat_expansion(snapshot):
    plan = _fast_catalog_plan("t muốn uống nước giải khát", None, [], snapshot)
    shortlist, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    full_catalog, full_missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need], 0
    )
    assert not missing and not full_missing
    assert len(full_catalog) > len(shortlist) >= 3
    advice = _fast_catalog_advice(shortlist, plan)
    assert advice
    expanded = _attach_catalog_options(advice, shortlist, plan, full_catalog)
    assert len(expanded.catalog_products) == len(full_catalog)


@pytest.mark.asyncio
async def test_breakfast_no_cook_scope_uses_catalog_food_categories(snapshot):
    plan = _fast_catalog_plan(
        "Tôi muốn mua đồ ăn sáng nhanh, không cần nấu nướng.", None, [], snapshot
    )
    assert plan and {search.category for search in plan.searches} >= {"Cháo", "Mì", "Bánh bao"}
    candidates, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    assert not missing
    advice = _fast_catalog_advice(candidates, plan)
    assert advice and "Bữa sáng" in advice.text


def test_breakfast_followup_can_exclude_bread(snapshot):
    previous = _fast_catalog_plan(
        "Tôi muốn mua đồ ăn sáng nhanh, không cần nấu nướng.", None, [], snapshot
    )
    current = _fast_catalog_plan(
        "Tôi không muốn ăn bánh mì, có lựa chọn khác không?",
        previous.model_dump(), [], snapshot,
    )
    assert current and all(search.category != "Bánh mì" for search in current.searches)


@pytest.mark.asyncio
async def test_hotpot_scope_excludes_mince_sauce_and_processed_food(snapshot):
    plan = _fast_catalog_plan("t muốn ăn lẩu thì ăn với cái gì", None, [], snapshot)
    candidates, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    assert not missing
    names = " ".join(p.name.casefold() for p in candidates.values())
    assert "nước mắm" not in names and "xay" not in names and "nộm" not in names
    assert {p.category for p in candidates.values()} >= {"Thịt", "Hải Sản"}
    advice = _fast_catalog_advice(candidates, plan)
    assert advice and len(advice.products) == 5
    assert {p.category for p in advice.products} >= {
        "Thịt", "Hải Sản", "Rau Lá", "Cá - Bò Viên",
    }
    assert any("nấm" in p.name.casefold() for p in advice.products)
    assert any("viên" in p.name.casefold() for p in advice.products)
    for line in advice.text.splitlines():
        if any(p.name in line for p in advice.products if p.category != "Thịt"):
            assert "Thái mỏng" not in line


@pytest.mark.asyncio
async def test_label_followup_keeps_zero_sugar_rows_across_drink_taxonomy(snapshot):
    previous = {
        "need": "t muốn uống nước giải khát",
        "searches": [{"query": "nước giải khát", "category": "Nước Ngọt"}],
    }
    plan = _fast_catalog_plan("cáo nào ít đường", previous, [], snapshot)
    assert plan and len(plan.searches) >= 1
    candidates, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    assert not missing
    assert candidates
    assert all(
        any(label.casefold() in product.name.casefold()
            for label in ("ít đường", "không đường"))
        for product in candidates.values()
    )
    advice = _fast_catalog_advice(candidates, plan)
    assert advice and len(advice.products) >= 2
    assert any("không đường" in product.name.casefold() for product in advice.products)


@pytest.mark.asyncio
async def test_fresh_drink_label_query_excludes_functional_drinks_and_honors_budget(snapshot):
    plan = _fast_catalog_plan(
        "các loại nước giải khát không đường dưới 100k", None, [], snapshot
    )
    assert plan and plan.budget_vnd == 100_000 and plan.strict_budget
    candidates, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    assert missing and all(index < len(plan.searches) for index in missing)
    assert candidates
    assert all(product.price_vnd < 100_000 for product in candidates.values())
    assert all("chức năng" not in product.category_hierarchy.casefold()
               for product in candidates.values())
    assert all("không đường" in product.name.casefold()
               for product in candidates.values())


@pytest.mark.asyncio
async def test_late_night_scope_keeps_single_serving_quick_food(snapshot):
    plan = _fast_catalog_plan("đồ ăn đêm có cái gì ngon ko", None, [], snapshot)
    candidates, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    assert not missing
    assert {p.category for p in candidates.values()} <= {"Cháo", "Mì", "Bánh bao"}
    assert all("thùng" not in p.name.casefold() for p in candidates.values())


@pytest.mark.asyncio
async def test_strict_snack_budget_never_reintroduces_over_budget_rows(snapshot):
    plan = _fast_catalog_plan("có loại snack nào giá dưới 20.000 đồng không?", None, [], snapshot)
    candidates, missing = await _retrieve_candidates(
        snapshot, plan, sorted({p.category for p in snapshot.products}), [plan.need]
    )
    assert not missing
    assert candidates and all(p.price_vnd < 20_000 for p in candidates.values())


def test_named_promotion_query_filters_before_top_k(snapshot):
    observation = execute_catalog_tool(
        "list_product_promotions",
        json.dumps({"category": "dairy", "query": "sữa tươi", "limit": 5}),
        repository=snapshot,
    )
    assert observation.status == "SUCCESS"
    assert observation.products
    assert all("sữa tươi" in product.name.casefold() for product in observation.products)


def test_category_only_match_cannot_borrow_another_group(snapshot):
    fish = next(p for p in snapshot.products if p.category == "Hải Sản")
    assert not _matching_candidates([fish], Search(category="Thịt", preparation="slice"))


def test_cleanup_words_cannot_be_misread_as_a_meal(snapshot):
    assert _fast_catalog_plan("Mình cần khăn giấy hoặc khăn lau.", None, [], snapshot) is None


@pytest.mark.parametrize("text,expected", [
    ("t muốn ăn lẩu thì ăn với cái gì", True),
    ("ăn lấu thì nên mua gì", True),
    ("khăn lau có loại nào không", False),
    ("mình cần khăn lau nhà", False),
    ("nước lau sàn loại nào tốt", False),
    ("có nước lau kính không", False),
])
def test_hotpot_detection_requires_hotpot_context(text, expected):
    assert _hotpot_requested(text) is expected


@pytest.mark.parametrize("text,query", [
    ("nước giặt dưới 150 nghìn", "nước giặt"),
    ("dầu gội dưới 150 nghìn", "dầu gội"),
    ("khăn giấy dưới 30 nghìn", "khăn giấy"),
])
def test_budgeted_catalog_heads_do_not_need_filler_words(snapshot, text, query):
    plan = _fast_catalog_plan(text, None, [], snapshot)
    assert plan and plan.budget_vnd is not None
    assert query in plan.searches[0].query.casefold()


def test_new_occasion_drops_the_previous_product_scope(snapshot):
    previous = _fast_catalog_plan("t muốn ăn lẩu thì ăn với cái gì", None, [], snapshot)
    current = _fast_catalog_plan("đồ ăn đêm có cái gì ngon ko", previous.model_dump(), [], snapshot)
    assert current.topic_changed and not current.reuse_previous
    assert {s.category for s in current.searches} == {"Cháo", "Mì", "Bánh bao"}


@pytest.mark.parametrize("text", [
    "Ăn lẩu hải sản thôi, không lấy thịt",
    "Ăn đêm dưới 30 nghìn, không cay",
    "Có các loại sữa không đường nào?",
])
def test_complex_requirements_keep_the_semantic_planner(text, snapshot):
    assert _fast_catalog_plan(text, None, [], snapshot) is None
