"""Request grammar must work with catalog labels beyond a single example."""

import pytest

from backend.products.category_taxonomy import normalize_category_text
from backend.services.query_understanding import understand_catalog_query


@pytest.mark.parametrize("category", ["Gối", "Sổ", "Đèn học", "Cát vệ sinh", "Ống tưới"])
@pytest.mark.parametrize("template", [
    "t đang cần mua {} ở đâu vậy", "tui muốn mua {}", "Trong các loại {}, loại nào rẻ nhất?",
])
def test_shared_grammar_resolves_labels_from_the_supplied_taxonomy(category, template):
    paths = (("Ngành mới", "Nhóm mới", category),)
    result = understand_catalog_query(template.format(category), paths)
    assert normalize_category_text(result.product_type) == normalize_category_text(category)
    assert not result.negative_constraints
    if "ở đâu" in template:
        assert result.intent == "location"


@pytest.mark.parametrize("category", ["Bơ động vật", "Bơ đóng hộp", "Bò, Bê NK", "Nấm"])
def test_native_category_spelling_is_not_a_request_operator(category):
    paths = (("Ngành mới", "Nhóm mới", category),)
    for text in (f"Trong các loại {category}, loại nào rẻ nhất?", f"t mua {category} ở đâu"):
        result = understand_catalog_query(text, paths)
        assert normalize_category_text(result.product_type) == normalize_category_text(category)
        assert not result.negative_constraints


@pytest.mark.parametrize("text", [
    "Gợi ý đèn học, loại bỏ hãng Nova", "Gợi ý đèn học, bỏ qua hãng Nova",
    "Gợi ý đèn học, đừng lấy hãng Nova", "Goi y den hoc, loai bo hang Nova",
])
def test_real_negative_instructions_remain_active(text):
    result = understand_catalog_query(text, (("Hàng hóa", "Đèn học"),))
    assert any("nova" in normalize_category_text(value) for value in result.negative_constraints)
