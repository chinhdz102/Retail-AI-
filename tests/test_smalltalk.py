import pytest

from backend.services.smalltalk import (
    classify_smalltalk_intent,
    localized_identity_response,
    localized_smalltalk_fallback,
    smalltalk_reply_is_safe,
    smalltalk_system_prompt,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Kể chuyện vui đi.", "joke"),
        (
            "Hôm nay mình hơi mệt, nói câu gì vui vui cho mình đi.",
            "joke",
        ),
        ("Hôm nay nóng thật đấy nhỉ.", "weather"),
        ("Chào bạn, hôm nay bạn thế nào?", "wellbeing"),
        ("Bạn nói chuyện dễ thương ghê, cảm ơn nha.", "compliment"),
        (
            "Bạn là ai, có phải người thật đang tư vấn cho mình không?",
            "identity",
        ),
        ("Tell me a short joke, please.", "joke"),
        ("Are you a real person?", "identity"),
        ("Hi, how are you?", "wellbeing"),
        ("Nghen nhé. Ngoài thích, chào.", "farewell"),
        ("你好，讲个笑话吧。", "joke"),
    ],
)
def test_classifies_clear_social_turns(text: str, expected: str) -> None:
    assert classify_smalltalk_intent(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Hôm nay nóng quá, gợi ý nước uống mát dưới 10 nghìn.",
        "Mình mệt quá, có snack nào dưới 20k không?",
        "Kể chuyện vui về giá Coca Zero đi.",
        "Bạn là ai? Có thể gợi ý đồ uống không?",
        "Can you recommend a drink? I'm tired.",
        "Coca Zero còn hàng không?",
        "Tell me a joke and check whether Coke is on promotion.",
    ],
)
def test_explicit_retail_requests_take_priority_over_smalltalk(text: str) -> None:
    assert classify_smalltalk_intent(text) is None


def test_social_prompt_allows_short_humor_without_unverified_store_claims() -> None:
    prompt = smalltalk_system_prompt("vi").lower()

    assert "chơi chữ hoặc câu đố về đồ vật" in prompt
    assert "tai nạn, bạo lực và chuyện cá nhân" in prompt
    assert "không bịa giá, tồn kho, khuyến mãi" in prompt
    assert "không ép chuyển chủ đề sang mua sắm" in prompt
    assert "không nhận mình là người thật" in prompt


def test_identity_response_is_transparent_in_supported_languages() -> None:
    assert "trợ lý AI" in localized_identity_response("vi")
    assert "không phải người thật" in localized_identity_response("vi")
    assert "AI assistant" in localized_identity_response("en")
    assert "AI 助手" in localized_identity_response("zh")


def test_smalltalk_fallback_is_available_without_provider() -> None:
    assert "chuyện vui" in localized_smalltalk_fallback("joke", "vi").lower()
    assert "water" in localized_smalltalk_fallback("weather", "en").lower()
    assert localized_smalltalk_fallback("greeting", "zh")
    assert "hẹn gặp lại" in localized_smalltalk_fallback("farewell", "vi").lower()


@pytest.mark.parametrize(
    ("intent", "reply", "expected"),
    [
        ("joke", "Cá nào biết hát? Cá-cá-cá!", True),
        ("joke", "Con mèo nhà mình bị điện giật rồi bỏ chạy.", False),
        ("emotion", "Cá sấu không bao giờ cắn bạn đâu!", False),
        ("emotion", "Nghe hôm nay không dễ chịu lắm.", True),
        ("weather", "Hôm nay nóng thật.", True),
    ],
)
def test_rejects_jokes_that_claim_personal_stories_or_make_harmful_punchlines(
    intent: str,
    reply: str,
    expected: bool,
) -> None:
    assert smalltalk_reply_is_safe(intent, reply) is expected
