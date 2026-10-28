import pytest

from backend.services.product_comparison import (
    comparison_metric,
    is_comparison_request,
    parse_comparison_queries,
    unsupported_comparison_dimension,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "So sánh Coca Zero với nước suối",
            ("coca zero", "nuoc suoi"),
        ),
        (
            "Compare Coca Zero and water",
            ("coca zero", "water"),
        ),
        (
            "Coca Zero với nước suối cái nào rẻ hơn?",
            ("coca zero", "nuoc suoi"),
        ),
        (
            "So Coca Zero với nước suối xem chai nào rẻ hơn.",
            ("coca zero", "nuoc suoi"),
        ),
        (
            "So sánh giữa Coca Zero và nước suối, giúp mình",
            ("coca zero", "nuoc suoi"),
        ),
    ],
)
def test_comparison_parser_extracts_two_product_queries(
    text: str,
    expected: tuple[str, str],
) -> None:
    assert parse_comparison_queries(text) == expected


def test_comparison_parser_inherits_product_name_for_variant_followup() -> None:
    text = "Coca Zero 330 với chai 600 khác gì nhau?"

    assert is_comparison_request(text)
    assert parse_comparison_queries(text) == ("coca zero 330", "coca zero chai 600")


def test_natural_difference_question_uses_shared_query_understanding_operands() -> None:
    text = "Nước tinh khiết đóng chai với nước khoáng có gì khác theo nhãn?"

    assert is_comparison_request(text)
    assert parse_comparison_queries(text) == (
        "Nước tinh khiết đóng chai",
        "nước khoáng",
    )


def test_conjoined_product_availability_question_is_not_a_comparison() -> None:
    text = "Nước tinh khiết đóng chai và nước khoáng có bán không?"

    assert not is_comparison_request(text)
    assert parse_comparison_queries(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cái nào rẻ hơn?", "price"),
        ("Which one is cheaper?", "price"),
        ("So sánh giá Coca Zero với nước suối", "price"),
        ("Chênh nhau đúng 6 nghìn hả?", "price"),
        ("Compare prices for Coca Zero and water", "price"),
        ("Bên nào còn nhiều hàng hơn?", "stock"),
        ("Which has more stock?", "stock"),
        ("So sánh lượng tồn kho của hai món", "stock"),
        ("So sánh hai món này", None),
    ],
)
def test_comparison_metric_only_recognizes_supported_facts(
    text: str,
    expected: str | None,
) -> None:
    assert comparison_metric(text) == expected


@pytest.mark.parametrize("text", ["So sánh", "Compare Coca with water", "Cái nào rẻ hơn?"])
def test_comparison_request_detection(text: str) -> None:
    assert is_comparison_request(text)


def test_ordinary_retail_question_is_not_misclassified_as_comparison() -> None:
    assert not is_comparison_request("Coca Zero giá bao nhiêu?")


@pytest.mark.parametrize(
    "text",
    [
        "Cái nào ngon nhất và bán chạy nhất?",
        "Món mô ngon nhứt và bán chạy nhứt?",
        "Cái chi bán đắt hàng nhứt?",
        "Món quà vặt mô ngon nhứt?",
    ],
)
def test_ranking_questions_are_not_misclassified_as_comparisons(text: str) -> None:
    assert not is_comparison_request(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Món nào ngon hơn?", "taste"),
        ("Which one tastes better?", "taste"),
        ("Cái nào bán chạy hơn?", "popularity"),
        ("Which one is the bestseller?", "popularity"),
        ("Cái nào rẻ hơn?", None),
    ],
)
def test_unsupported_comparison_dimension_is_explicit(
    text: str,
    expected: str | None,
) -> None:
    assert unsupported_comparison_dimension(text) == expected
