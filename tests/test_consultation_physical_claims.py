"""A positive LLM verdict cannot prove fit from package volume or sale unit."""

import json

import pytest
from test_customer_consultation import product

from backend.services.advice_claims import (
    unsupported_absence_claim,
    unsupported_comfort_guarantee,
    unsupported_fit_guarantee,
    unsupported_supply_duration,
)
from backend.services.consultation_text import decode_bound_text
from backend.services.consultation_verification import decode_verification, verification_context
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReply,
    _render,
)


@pytest.mark.parametrize("text", [
    "Chai 450ml nhỏ gọn, bỏ túi xách hay balo không vướng.",
    "Loại này vừa balo của bạn.",
    "Bạn bỏ vào túi xách không cồng kềnh.",
    "Loại này vừa gọn ngăn tủ.",
    "Món này không chiếm chỗ.",
    "Không thể khẳng định vừa balo, nhưng bỏ túi xách không vướng.",
    "Cần kiểm tra xem có vừa ngăn tủ không. Loại này không tốn diện tích.",
])
def test_fit_promises_are_not_proved_by_a_product_name_or_volume(text):
    assert unsupported_fit_guarantee(text)
    row = product(name="Hàng Nova 450ml", price=5500)
    with pytest.raises(ValueError, match="fit or space"):
        _render(ConsultationReply(text="{name:0} ({price:0}). " + text,
                                  picks=[{"candidate": 0}]),
                [row], ConsultationDecision(action="recommend"))


@pytest.mark.parametrize("text", [
    "Bạn có thể gấp lại rồi mang theo.",
    "Mình ưu tiên chai dung tích nhỏ hơn để mang đi làm.",
    "Chai này giá thấp hơn loại bạn vừa xem.",
    "Bạn nên kiểm tra xem có vừa balo không.",
    "Chưa thể khẳng định vừa ngăn tủ của bạn.",
    "Đừng mặc định món này vừa balo nhé.",
    "Bạn muốn loại vừa balo không?",
])
def test_practical_suggestions_price_comparisons_and_fit_questions_remain_possible(text):
    assert not unsupported_fit_guarantee(text)
    row = product(name="Hàng Nova", price=5500)
    rendered, selected = _render(ConsultationReply(
        text="{name:0} ({price:0}). " + text, picks=[{"candidate": 0}],
    ), [row], ConsultationDecision(action="recommend"))
    assert text in rendered and selected == [row]


def test_verifier_cannot_license_fit_from_name_and_uom_but_can_allow_carrying_advice():
    for text, expected in [
        ("Chai này bỏ túi xách hay balo không vướng.", False),
        ("Bạn có thể mang theo khi đi làm.", True),
    ]:
        data = verification_context({
            "customer": "Mua một chai nước mang đi làm.", "rendered_text": text,
            "selected_products": [{"candidate": 0, "name": "Nước Nova 450ml", "uom": "Chai",
                                   "sale_unit_evidence": {"unit": "chai", "quote": "Chai"}}],
        })
        value = {
            "dialogue_checks": {"answers_current_request": True, "preserves_constraints": True,
                                "advances_decision": True, "issue": ""},
            "checks": [{"statement": 0, "verdict": "supported",
                        "evidence": ["c0.name", "c0.uom"], "reason": "Tên và đơn vị."}],
            "product_checks": [{"candidate": 0, "name_quote": "Nước Nova 450ml",
                                "actual_type": "nước", "fits_need": True,
                                "claims_supported": True, "issue": ""}],
        }
        review = json.loads(decode_verification(json.dumps(value), data))
        assert review["acceptable"] is expected
        assert any(s["id"] == "c0.sale_unit_evidence" for s in data["sources"])


@pytest.mark.parametrize("text", [
    "Không ga nên uống dần trong ngày không lo đầy bụng.",
    "Sản phẩm không gây khó chịu.",
    "Loại này không làm khô da.",
    "Chưa thể khẳng định đủ no, nhưng uống loại này không lo đầy bụng.",
])
def test_absence_of_discomfort_is_not_an_ordinary_buying_suggestion(text):
    assert unsupported_comfort_guarantee(text)


@pytest.mark.parametrize("text", [
    "Bạn có thể mang đi làm rồi uống khi cần.",
    "Chưa thể khẳng định sản phẩm không gây khó chịu.",
    "Bạn có đang bị đầy bụng không?",
])
def test_ordinary_use_and_questions_are_not_discomfort_promises(text):
    assert not unsupported_comfort_guarantee(text)


@pytest.mark.parametrize("claim,sources,invalid", [
    ("Loại này không ga.", ["Nước khoáng Nova 450ml"], True),
    ("Loại này không có ga.", ["Nước Nova không gas 450ml"], False),
    ("Loại này không chứa cồn.", ["Sữa tắm Nova"], True),
    ("Loại này không chứa cồn.", ["Công thức không cồn"], False),
    ("Loại này không đường.", ["Đồ uống Nova không đường"], False),
    ("Loại này không thêm đường vào.", ["Đồ uống Nova không đường"], True),
    ("Loại này không thêm đường.", ["Không thêm đường"], False),
    ("Loại này không bổ sung đường.", ["Không thêm đường"], False),
    ("Loại này không có đường.", ["Không thêm đường"], True),
    ("Loại này không bổ sung cồn.", ["Công thức không cồn"], True),
    ("Loại này chỉ còn đường lactose tự nhiên có sẵn trong sữa.",
     ["Sữa Nova không đường"], True),
    ("Loại này chỉ chứa nước và muối.", ["Công thức chỉ chứa nước và muối"], False),
    ("Loại này chỉ chứa nước và muối.", ["Công thức chỉ chứa nước"], True),
    ("Loại này chỉ chứa nước.", ["Công thức chỉ chứa nước và muối"], True),
    ("Loại này chỉ chứa nước.", ["Dung dịch Nova"], True),
    ("Mình chỉ có hai món để so sánh.", [], False),
    ("Mình chỉ cần kiểm tra quy cách trước khi chọn.", [], False),
    ("Loại này không chứa caffeine.", ["Không cà phê"], True),
    ("Loại này không chứa cà phê.", ["Đồ uống sữa vị cà phê"], True),
    ("Loại này không có mùi nồng.", ["Nước lau nhà Nova"], True),
    ("Bạn nên kiểm tra nhãn xem có ghi không cồn hay không.", [], False),
    ("Nếu bạn muốn không ga thì mình ưu tiên nhóm nước lọc.", [], False),
    ("Chưa thể khẳng định loại này không chứa cồn.", [], False),
])
def test_absence_requires_positive_product_wording_not_an_omitted_ingredient(
    claim, sources, invalid,
):
    assert unsupported_absence_claim(claim, sources) is invalid


def test_one_product_cannot_borrow_another_products_absence_label_in_bound_blocks():
    facts = {
        0: {"name": "Sữa tắm Nova", "evidence": []},
        1: {"name": "Sữa tắm khác", "evidence": [{"value": "Công thức không cồn"}]},
    }
    for candidate, invalid in [(0, True), (1, False)]:
        raw = json.dumps({"picks": [{"candidate": candidate}], "text": [{
            "candidate": candidate, "explanation": "Mình chọn {product}, loại này không cồn.",
        }]})
        if invalid:
            with pytest.raises(ValueError, match="ingredient-absence"):
                decode_bound_text(raw, "text", list(facts), basket=False, product_facts=facts)
        else:
            assert "{name:1}" in decode_bound_text(
                raw, "text", list(facts), basket=False, product_facts=facts,
            )


def test_observed_milk_label_inference_is_rejected_before_publication():
    facts = {0: {"name": "Sữa Nova không đường túi 180ml", "evidence": []}}
    raw = json.dumps({"picks": [{"candidate": 0}], "text": [{
        "candidate": 0,
        "explanation": "Mình chọn {product}, nhãn không đường tức không thêm đường vào, "
                       "chỉ còn đường lactose tự nhiên có sẵn trong sữa.",
    }]})
    with pytest.raises(ValueError, match="ingredient-absence"):
        decode_bound_text(raw, "text", [0], basket=False, product_facts=facts)


@pytest.mark.parametrize("text,issue", [
    ("Chai 450ml cầm một tay thoải mái.", "fit in"),
    ("Không ga nên uống dần trong ngày không lo đầy bụng.", "bodily discomfort"),
    ("Nước này không chứa cà phê.", "ingredient absence"),
    ("Loại này không thêm đường vào.", "ingredient absence"),
    ("Loại này chỉ còn đường lactose tự nhiên có sẵn trong sữa.", "ingredient absence"),
    ("300g dùng hằng ngày được một thời gian khá dài.", "supply duration"),
])
def test_observed_false_suggestion_verdict_cannot_bypass_independent_boundaries(text, issue):
    context = verification_context({
        "customer": "Chọn một chai nước mang đi làm, không ga và không cà phê.",
        "rendered_text": text,
        "selected_products": [{"candidate": 0, "name": "Nước ion I-On Life 450ml",
                               "type": "Nước khoáng", "uom": "Chai"}],
    })
    value = {
        "dialogue_checks": {"answers_current_request": True, "preserves_constraints": True,
                            "advances_decision": True, "issue": ""},
        "checks": [{"statement": 0, "verdict": "suggestion", "evidence": [],
                    "reason": "Mua sắm thông thường."}],
        "product_checks": [{"candidate": 0, "name_quote": "Nước ion I-On Life 450ml",
                            "actual_type": "Nước khoáng", "fits_need": True,
                            "claims_supported": True, "issue": ""}],
    }
    review = json.loads(decode_verification(json.dumps(value), context))
    assert not review["acceptable"] and issue in review["issue"]


@pytest.mark.parametrize("text", [
    "300g dùng hằng ngày được một thời gian khá dài.",
    "Nếu nhà bạn rửa chén thường xuyên, chai này hợp để dùng lâu dài.",
    "Gói lớn đủ dùng cả tuần.",
    "Loại này dùng được lâu hơn.",
    "Chưa thể khẳng định dùng lâu, nhưng loại này dùng rất lâu.",
])
@pytest.mark.parametrize("category", ["Dầu gội", "Nước rửa chén", "Cà phê"])
def test_price_or_pack_is_not_a_supply_duration_estimate(text, category):
    row = product(name=f"{category} Nova 300g", price=30000)
    assert unsupported_supply_duration(text)
    with pytest.raises(ValueError, match="supply-duration"):
        _render(ConsultationReply(text="{name:0} ({price:0}). " + text,
                                  picks=[{"candidate": 0}]),
                [row], ConsultationDecision(action="recommend"))


@pytest.mark.parametrize("text", [
    "Bạn định dùng được lâu là khoảng bao lâu?",
    "Chưa thể khẳng định gói này đủ dùng cả tuần.",
    "Nếu bạn muốn dùng lâu, mình ưu tiên so giá theo cùng khối lượng.",
    "Bạn có thể chia nhỏ để dùng dần.",
    "Gói lớn hơn có tiền mua ban đầu cao hơn.",
    "Chai này giá thấp hơn cho cùng quy cách.",
])
def test_practical_pack_tradeoffs_do_not_promise_a_duration(text):
    assert not unsupported_supply_duration(text)
