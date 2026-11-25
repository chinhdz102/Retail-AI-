"""Literal/alias constraints must also apply to products from earlier turns."""

import pytest
from test_customer_consultation import Repository, product

from backend.services.customer_consultation import ConsultationDecision, SearchNeed, _retrieve
from backend.services.product_constraints import title_has_descriptor


@pytest.mark.parametrize("title,term,expected", [
    ("Sổ lò co dọc Nova A5", "lò xo", True),
    ("Vở spiral A6", "lò xo", True),
    ("Sổ không lò xo A5", "lò xo", False),
    ("Sổ bìa mềm A5", "lò xo", False),
    ("Thịt dê cắt lát", "thái lát", True),
    ("Thịt chưa tẩm ướp", "ướp sẵn", False),
    ("Thịt dê xay", "băm", True),
    ("Túi refill nước rửa tay", "túi châm", True),
    ("Sữa không đường", "đường", False),
    ("Sữa không đường", "không đường", True),
    ("Sữa ít đường", "đường", True),
    ("Mặt hàng có vị bơ", "bò", False),
])
def test_title_constraints_respect_word_boundaries_negation_and_equivalent_forms(
    title, term, expected,
):
    assert title_has_descriptor(title, term) is expected


@pytest.mark.parametrize("title,excluded", [
    ("Sổ lò co dọc Nova A5", "lò xo"),
    ("Thịt dê cắt lát", "thái lát"),
    ("Thịt dê băm", "xay"),
])
def test_previous_display_cannot_bypass_new_exclusion(title, excluded):
    row = product(name=title)
    catalog = Repository([row])
    paths = catalog.list_category_hierarchies()
    plan = ConsultationDecision(
        action="recommend", use_visible_products=True,
        searches=[SearchNeed(category=paths[0][-1], excluded=[excluded])],
    )
    assert _retrieve(catalog, paths, plan, [row], {}) == []


def test_visible_product_from_another_task_cannot_bypass_new_role():
    old = product(pid="old", name="Khăn Nova", path="Nhà cửa > Đồ dùng > Khăn")
    current = product(pid="new", name="Sổ Nova", path="Đồ dùng > Văn phòng > Sổ")
    catalog = Repository([current])
    plan = ConsultationDecision(action="recommend", use_visible_products=True,
                                searches=[SearchNeed(category="Sổ")])
    assert _retrieve(catalog, catalog.list_category_hierarchies(), plan, [old], {}) == [current]
