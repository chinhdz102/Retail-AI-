from __future__ import annotations

import pytest

from evaluation.semantic_intelligence_v2_benchmark import _asks_for_alternative


@pytest.mark.parametrize(
    ("query", "expected_category", "expected"),
    [
        (
            "Mình đang cần Các loại bột khác, cho mình xem vài loại.",
            "Các loại bột khác",
            False,
        ),
        (
            "Còn lựa chọn nào khác cùng nhóm không?",
            "Các loại bột khác",
            True,
        ),
        (
            "Cho mình xem loại khác trong Các loại bột khác.",
            "Các loại bột khác",
            True,
        ),
        (
            "Hai loại này khác nhau thế nào?",
            "Các loại bột khác",
            False,
        ),
        (
            "Do you have other options in Các loại bột khác?",
            "Các loại bột khác",
            True,
        ),
    ],
)
def test_alternative_grader_ignores_taxonomy_label_wording(
    query: str,
    expected_category: str,
    expected: bool,
) -> None:
    assert _asks_for_alternative(query, expected_category) is expected
