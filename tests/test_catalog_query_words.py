import pytest

from backend.api.main import _fact_product_query


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dầu ăn giá bao nhiêu?", "dau an"),
        ("Dầu gội giá bao nhiêu?", "dau goi"),
        ("Thịt bò giá bao nhiêu?", "thit bo"),
        ("Trà xanh ở đâu?", "tra xanh"),
        ("Bánh bao giá bao nhiêu?", "banh bao"),
        ("Mì Hảo Hảo giá bao nhiêu?", "mi hao hao"),
        ("Nước lau sàn giá bao nhiêu?", "nuoc lau san"),
        ("Giá bao nhiêu vậy?", ""),
        ("Còn lấy ở kệ nào?", ""),
    ],
)
def test_fact_query_keeps_product_words_instead_of_deleting_them(text, expected):
    assert _fact_product_query(text) == expected
