"""Multiple negative clauses must survive as separate native descriptors."""

import pytest

from backend.services.query_understanding import normalize_vietnamese, understand_catalog_query


@pytest.mark.parametrize("text,expected", [
    ("Mua nước, không nước có ga và không cà phê.", {"nước có ga", "cà phê"}),
    ("Mua đồ ăn, đừng thịt heo, không đồ ướp sẵn, mấy loại viên thì bỏ nhé.",
     {"thịt heo", "đồ ướp sẵn", "viên"}),
    ("Mua vở, không lò xo và không bìa cứng.", {"lò xo", "bìa cứng"}),
    ("Tư vấn nước rửa tay, túi refill thì bỏ qua.", {"túi refill"}),
    ("Cần chai rửa chén, mấy loại hương nồng thì bỏ đi.", {"hương nồng"}),
    ("Mua nước, không nước có ga và không cà phê. Chọn giúp một chai thôi.",
     {"nước có ga", "cà phê"}),
    ("Mua vở, không lò xo. Chỉ chọn giúp một cuốn.", {"lò xo"}),
    ("Tìm đồ dùng, không loại 1.5L. Chọn loại khác.", {"1.5L"}),
])
def test_prefix_and_postposed_exclusions_survive_coordination(text, expected):
    found = {
        normalize_vietnamese(term) for term in understand_catalog_query(text).negative_constraints
    }
    assert found == {normalize_vietnamese(term) for term in expected}


@pytest.mark.parametrize("text", [
    "Dùng nước rửa tay hằng ngày.",
    "Chọn giúp một cuốn và nói vì sao, đừng chỉ liệt kê tên.",
    "Sữa thì bỏ vào giỏ nhé.",
    "Loại này thì bò ăn được không?",
    "Tìm Dung dịch vs nam nữ",
])
def test_use_instructions_meta_instructions_and_accent_collisions_are_not_exclusions(text):
    assert not understand_catalog_query(text).negative_constraints
