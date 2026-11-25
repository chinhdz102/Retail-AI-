"""Truthful item facts alone cannot approve an unhelpful or off-goal reply."""

import json

import pytest

from backend.services.consultation_verification import decode_verification, verification_context


def review_case():
    data = verification_context({
        "customer": "Chọn một món để ghi chi tiêu dưới 50 nghìn, không lấy lò xo.",
        "rendered_text": "Sổ Nova A6 giá 20.000đ.",
        "selected_products": [{"candidate": 0, "name": "Sổ Nova A6", "price_vnd": 20_000}],
    })
    verdict = {
        "dialogue_checks": {
            "answers_current_request": True,
            "preserves_constraints": True,
            "advances_decision": True,
            "issue": "",
        },
        "checks": [{"statement": 0, "verdict": "supported",
                    "evidence": ["c0.name", "c0.price_vnd"], "reason": "Đúng tên và giá."}],
        "product_checks": [{"candidate": 0, "name_quote": "Sổ Nova A6", "actual_type": "Sổ",
                            "fits_need": True, "claims_supported": True, "issue": ""}],
    }
    return data, verdict


@pytest.mark.parametrize("failed_check,issue", [
    ("answers_current_request", "Khách nhờ chọn một cuốn nhưng nháp chỉ liệt kê giá."),
    ("preserves_constraints", "Lượt mới vẫn loại lò xo nhưng nháp bỏ điều kiện đó."),
    ("advances_decision", "Cần lý do gắn với việc mang theo ghi chi tiêu."),
])
def test_positive_fact_and_product_flags_cannot_override_a_dialogue_failure(failed_check, issue):
    data, value = review_case()
    value["dialogue_checks"].update({failed_check: False, "issue": issue})
    result = json.loads(decode_verification(json.dumps(value), data))
    assert result["acceptable"] is False
    assert issue in result["issue"]


@pytest.mark.parametrize("change", ["absent", "omitted_flag", "string_flag", "no_issue"])
def test_dialogue_check_contract_cannot_default_or_coerce_an_approval(change):
    data, value = review_case()
    if change == "absent":
        value.pop("dialogue_checks")
    elif change == "omitted_flag":
        value["dialogue_checks"].pop("preserves_constraints")
    elif change == "string_flag":
        value["dialogue_checks"]["advances_decision"] = "true"
    else:
        value["dialogue_checks"]["answers_current_request"] = False
        rejected = json.loads(decode_verification(json.dumps(value), data))
        assert not rejected["acceptable"] and "answers_current_request" in rejected["issue"]
        return
    with pytest.raises(ValueError):
        decode_verification(json.dumps(value), data)


def test_complete_dialogue_approval_still_needs_complete_statement_checks():
    data, value = review_case()
    value["checks"] = []
    with pytest.raises(ValueError):
        decode_verification(json.dumps(value), data)

