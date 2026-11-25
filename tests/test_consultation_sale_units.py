"""Sale units stay tied to customer roles, not inner units or title keywords."""

import json

import pytest
from test_customer_consultation import Repository, product

from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _allowed,
    _candidate_facts,
    _carry_alternative_sale_unit,
    _decision_context,
    _ground_sale_units,
    _preserve_constraints,
    _render_selection,
    _retrieve,
)
from backend.services.product_packaging import (
    explicit_sale_unit,
    sale_unit_matches,
    sale_unit_observation,
)


@pytest.mark.parametrize("name,uom,unit,count", [
    ("Lốc 6 chai nước Nova 450ml", "Gói 6", "lốc", 6),
    ("Thùng 24 lon đồ uống Nova 330ml", "Lon", "thùng", 24),
    ("Vỉ 4 hộp sữa Nova 180ml", "Hộp", "vỉ", 4),
    ("Bộ 3 cái khăn Nova", "Cái", "bộ", 3),
    ("Nước rửa chén Nova chai 750ml", "Chai", "chai", None),
    ("Nước rửa chén Nova túi 750ml", "", "túi", None),
    ("Nước rửa chén Nova túi 2kg", "Gói", "túi", None),
    ("Nước giặt Nova túi 2,4kg", "Gói", "túi", None),
    ("Trà Nova túi 100g", "Gói", "túi", None),
    ("Lốc 6 túi nước Nova 100ml", "Gói", "lốc", 6),
    ("Hộp 2 túi trà Nova 100g", "Gói", "hộp", 2),
    ("Khăn giấy Nova", "Gói 4", "gói", None),
    ("Sữa Nova", "Lốc, Lô, Lố", "lốc", None),
])
def test_outer_sale_unit_overrides_inner_unit(name, uom, unit, count):
    row = product(name=name).model_copy(update={"uom": uom})
    observed = sale_unit_observation(row)
    assert observed["unit"] == unit and observed.get("outer_count") == count
    assert observed["quote"] in getattr(row, observed["source_field"])


def test_unknown_packaging_is_unknown_not_a_bottle_from_volume_or_category():
    row = product(name="Nước Nova 450ml").model_copy(update={"uom": "Đơn vị"})
    assert sale_unit_observation(row) == {}
    assert sale_unit_matches(row, "chai")  # No known contradiction; not confirmation.
    facts = _candidate_facts(row, 0, Repository([row]), {})
    assert "sale_unit_evidence" not in facts


@pytest.mark.parametrize("category", ["Nước rửa chén bát", "Nước giặt", "Trà"])
def test_literal_pouch_with_generic_imported_uom_remains_retrievable(category):
    row = product(name=f"{category} Nova túi 2kg", price=53000,
                  path=f"Hàng > Nhóm > {category}").model_copy(update={"uom": "Gói"})
    repository = Repository([row])
    plan = ConsultationDecision(action="recommend", max_price_vnd=60000,
                                searches=[{"category": category, "required_sale_unit": "túi"}])
    assert _retrieve(repository, repository.list_category_hierarchies(), plan, []) == [row]
    assert not sale_unit_matches(row, "chai")
    facts = _candidate_facts(row, 0, repository, {row.product_id: [0]})
    assert facts["sale_unit_evidence"]["unit"] == "túi"
    assert facts["sale_unit_evidence"]["source_field"] == "name"
    plan.max_price_vnd = 50000
    assert _retrieve(repository, repository.list_category_hierarchies(), plan, []) == []


@pytest.mark.parametrize("text,unit", [
    ("Chọn giúp một chai thôi.", "chai"),
    ("Mua hai cuộn giấy nhé.", "cuộn"),
    ("Mua nước dạng túi cho mình.", "túi"),
    ("Mua 1 hộp thôi, không mua cả lốc.", "hộp"),
    ("Đừng mua một lốc nhé.", None),
    ("Không cần một chai, mình xem giá đã.", None),
    ("Mua một chai và hai hộp.", None),
    ("Mình cần nước uống 1 lít.", None),
    ("Mỗi ngày mình uống một chai.", None),
    ("Nhà còn một cuộn giấy.", None),
    ("Mình dùng một chai mỗi ngày.", None),
])
def test_only_positive_purchase_units_form_an_independent_requirement(text, unit):
    assert explicit_sale_unit(text) == unit


@pytest.mark.parametrize("category,unit,single_title,pack_title", [
    ("Nước khoáng", "chai", "Nước Nova chai 450ml", "Lốc 6 chai nước Nova 450ml"),
    ("Giấy vệ sinh", "cuộn", "Giấy Nova", "Gói 6 cuộn giấy Nova"),
    ("Vở", "quyển", "Vở Nova 96 trang", "Bộ 5 quyển vở Nova"),
])
def test_retrieval_and_publication_reject_inner_units(category, unit, single_title, pack_title):
    path = f"Cửa hàng > Hàng hóa > {category}"
    single = product("single", single_title, path=path).model_copy(update={"uom": unit})
    pack = product("pack", pack_title, path=path).model_copy(update={"uom": "Gói"})
    repository = Repository([single, pack])
    plan = ConsultationDecision(action="recommend", searches=[{
        "category": category, "required_sale_unit": unit,
    }])
    assert _allowed(single, plan, plan.searches[0])
    assert not _allowed(pack, plan, plan.searches[0])
    sources = {}
    paths = repository.list_category_hierarchies()
    assert _retrieve(repository, paths, plan, [pack], sources) == [single]
    wrong = GroundedSelection(picks=[{"candidate": 0}],
                              text="Mình chọn {name:0} ({price:0}).")
    with pytest.raises(ValueError, match="wrong sale unit"):
        _render_selection(wrong, [pack], plan, {pack.product_id: [0]})


def test_role_specific_basket_units_are_not_distributed_across_roles():
    plan = ConsultationDecision(action="recommend", basket=True, searches=[
        {"category": "Nước", "required_sale_unit": "chai"},
        {"category": "Giấy", "required_sale_unit": "cuộn"},
    ])
    _ground_sale_units(plan, "Mua một chai nước và hai cuộn giấy.")
    assert [s.required_sale_unit for s in plan.searches] == ["chai", "cuộn"]
    repaired = ConsultationDecision(action="recommend", basket=True,
                                    searches=[{"category": "Đồ dùng"}])
    _carry_alternative_sale_unit(plan, repaired)
    assert repaired.searches[0].required_sale_unit is None


def test_negated_sale_unit_cannot_be_grounded_as_a_positive_constraint():
    plan = ConsultationDecision(action="recommend", searches=[
        {"category": "Nước", "required_sale_unit": "lốc"},
    ])
    _ground_sale_units(plan, "Không mua cả lốc nhé, mình chỉ cần nước mang theo.")
    assert plan.searches[0].required_sale_unit is None


def test_alternative_replanning_preserves_purchase_unit_without_selecting_a_sku():
    old = ConsultationDecision(action="recommend", searches=[
        {"category": "Nước khoáng", "required_sale_unit": "chai"},
        {"category": "Nước tinh khiết", "required_sale_unit": "chai"},
    ])
    new = ConsultationDecision(action="recommend", searches=[{"category": "Nước trà"}])
    _carry_alternative_sale_unit(old, new)
    assert new.searches[0].required_sale_unit == "chai"


def test_same_scope_refinement_retains_unit_and_explicit_change_replaces_it():
    previous = {"constraints": {"searches": [{
        "category": "Nước khoáng", "required_sale_unit": "chai",
    }]}}
    for text, proposed, expected in [
        ("Có loại nhỏ hơn không?", None, "chai"),
        ("Mình đổi sang một lốc nhé.", "lốc", "lốc"),
        ("Không bắt buộc chai, loại nào cũng được.", None, None),
    ]:
        plan = ConsultationDecision(action="recommend", searches=[{
            "category": "Nước khoáng", "required_sale_unit": proposed,
        }])
        _ground_sale_units(plan, text)
        _preserve_constraints(plan, previous, text, parsed_budget=None, explicit_sizes=set())
        assert plan.searches[0].required_sale_unit == expected


def test_functional_role_reaches_selector_even_when_products_are_alternatives():
    plan = ConsultationDecision(action="recommend", searches=[{
        "category": "Thịt bò", "role": "nguyên liệu để khách thái lát nhúng lẩu",
    }])
    context = json.loads(json.dumps(_decision_context(plan), ensure_ascii=False))
    assert context["searches"][0]["role"] == plan.searches[0].role


def test_visible_only_followup_cannot_bypass_new_sale_unit_requirement():
    single = product("single", "Nước Nova chai 450ml", path="Hàng > Uống > Nước")
    pack = product("pack", "Lốc 6 chai nước Nova 450ml", path="Hàng > Uống > Nước")
    plan = ConsultationDecision(action="recommend", use_visible_products=True)
    _ground_sale_units(plan, "Chọn một chai thôi, mình không mua cả lốc.", [pack, single])
    repository = Repository([single, pack])
    assert _retrieve(repository, repository.list_category_hierarchies(), plan, [pack]) == [single]


def test_replan_cannot_invent_a_container_requirement_from_an_example():
    old = ConsultationDecision(action="recommend", searches=[{"category": "Giấy"}])
    new = ConsultationDecision(action="recommend", searches=[{
        "category": "Giấy vệ sinh", "required_sale_unit": "cuộn",
    }])
    _carry_alternative_sale_unit(old, new)
    assert new.searches[0].required_sale_unit is None


def test_bad_source_role_is_a_validation_error_instead_of_an_index_error():
    row = product()
    plan = ConsultationDecision(action="recommend", searches=[{"category": "Đèn"}])
    with pytest.raises(ValueError, match="unknown task role"):
        _render_selection(GroundedSelection(picks=[{"candidate": 0}]), [row], plan,
                          {row.product_id: [99]})
