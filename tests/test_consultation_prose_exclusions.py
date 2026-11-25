"""General advice must not reintroduce an excluded physical form or category."""

import pytest

from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _render_selection,
)


@pytest.mark.parametrize("category,excluded,text", [
    ("Sổ", "lò xo", "Bạn chọn sổ không lò xo. Thường có dạng lò co hoặc bìa cứng."),
    ("Đồ uống", "cà phê", "Mình ưu tiên cà phê không đường cho buổi sáng."),
    ("Thịt", "băm", "Bạn chọn phần thịt xay để chuẩn bị theo món mình muốn."),
    ("Nước rửa tay", "túi châm", "Mình chọn dạng refill để bổ sung vào bình đang có."),
    ("Sổ", "lò xo", "Không lò xo, nhưng mình chọn loại spiral để ghi chép."),
    ("Đồ uống", "cà phê", "Không bỏ qua cà phê, mình chọn loại này trước."),
    ("Đồ uống", "cà phê", "Cà phê thì bỏ vào túi mang đi làm nhé."),
])
def test_plain_advice_is_checked_against_the_same_exclusions_as_a_sku(category, excluded, text):
    plan = ConsultationDecision(action="advise", searches=[
        {"category": category, "excluded": [excluded]},
    ])
    with pytest.raises(ValueError, match="excluded"):
        _render_selection(GroundedSelection(text=text), [], plan, {})


@pytest.mark.parametrize("category,excluded,text", [
    ("Sổ", "lò xo", "Bạn chọn sổ không lò xo để mang theo ghi chép nhé."),
    ("Sổ", "lò xo", "Bạn tránh loại có lò co. So kích thước sổ với chỗ để trong túi nhé."),
    ("Sổ", "lò xo", "Sổ lò co thì bỏ nhé. Mình ưu tiên bìa mềm cho hướng chọn này."),
    ("Đồ uống", "cà phê", "Không lấy cà phê. Mình ưu tiên nước lọc để mang theo."),
    ("Đồ uống", "cà phê", "Mình không chọn cà phê, bạn có thể so các loại nước lọc."),
    ("Thịt", "băm", "Đừng lấy thịt xay. Bạn chọn nguyên miếng rồi tự cắt theo món nhé."),
    ("Nước rửa tay", "túi châm", "Nếu thấy túi refill thì bỏ qua. Mình chọn chai trước."),
    ("Đồ uống", "cà phê", "Đừng lấy cà phê. Mình chọn nước lọc trước."),
])
def test_exclusion_warnings_and_negative_buying_criteria_remain_possible(category, excluded, text):
    plan = ConsultationDecision(action="advise", searches=[
        {"category": category, "excluded": [excluded]},
    ])
    result, products = _render_selection(GroundedSelection(text=text), [], plan, {})
    assert result and not products


def test_planner_advice_fast_path_uses_the_same_exclusion_boundary():
    from backend.services.customer_consultation import _general_guidance

    plan = ConsultationDecision(
        action="advise", guidance="Không chọn cà phê, nhưng cà phê thì mua trước nhé.",
        searches=[{"category": "Đồ uống", "excluded": ["cà phê"]}],
    )
    assert _general_guidance(plan) == ""

