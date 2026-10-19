import pytest

from backend.api.main import (
    _implicit_followup_fields,
    _recommendation_tool_arguments,
    _requested_snapshot_fields,
    _unsupported_catalog_attribute_requested,
)
from backend.services.retail_agent import _explicit_recommendation_category
from backend.services.vietnamese_intent import (
    consultation_product_query,
    explicit_recommendation_query,
    has_specific_product_reference,
    is_recommendation_request,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Báo mình giá chai Coca Zero với.", ("price_vnd",)),
        ("Con này bao tiền vậy bạn?", ("price_vnd",)),
        ("Món này giờ bán mấy đồng?", ("price_vnd",)),
        ("Coca Zero giá sao nhỉ?", ("price_vnd",)),
        ("Coca Zero nhiu z?", ("price_vnd",)),
        ("Coca Zero bn vậy?", ("price_vnd",)),
        ("Coca Zero 13k à?", ("price_vnd",)),
        ("Coca Zero còn hàng chứ?", ("stock",)),
        ("Coca Zero còn bán được không?", ("stock",)),
        ("Ở đây còn bán loại này không?", ("stock",)),
        ("Món này hết chưa vậy?", ("stock",)),
        ("Còn chai Coca nào không?", ("stock",)),
        ("Nước suối còn mấy chai?", ("stock",)),
        ("Mình mua được Coca Zero ở đây không?", ("stock",)),
        ("Coca Zero để quầy nào ấy nhỉ?", ("shelf",)),
        ("Cho mình hỏi tìm món này ở đâu?", ("shelf",)),
        ("Nước suối bày khu nào vậy?", ("shelf",)),
        ("Lấy sữa ở chỗ nào?", ("shelf",)),
        ("Coca đang có deal gì không?", ("promotion",)),
        ("Nay có chương trình ưu đãi nào cho Pepsi?", ("promotion",)),
        ("Có mã giảm giá cho món này không?", ("promotion",)),
        ("Giá sau khuyến mãi còn bao nhiêu?", ("price_vnd", "promotion")),
        ("Món này dùng đến bao giờ?", ("expiry_at",)),
        ("Hạn trên sản phẩm còn tới ngày nào?", ("expiry_at",)),
        ("Bên trong món này gồm những gì?", ("ingredients",)),
        ("Coca Zero có những gì?", ("ingredients",)),
        ("Coca Zero có gì bên trong?", ("ingredients",)),
        ("Cái này làm từ nguyên liệu gì?", ("ingredients",)),
        ("Nhân bánh có những gì?", ("ingredients",)),
        ("Vị này cay không bạn?", ("spicy",)),
        ("Cho hỏi món này có cay nhiều không?", ("spicy",)),
        ("Mì này ít cay hay cay tê?", ("spicy",)),
        ("Bao nhiêu calo trong lon này?", ()),
        ("Coca Zero còn nhiều đường không?", ()),
        ("Coca Zero có bao nhiêu loại?", ()),
        ("Coca Zero có gì ngon?", ()),
        ("Có mã giảm giá cho Coca Zero không?", ("promotion",)),
        ("Coca Zero giảm giá còn bao nhiêu tiền?", ("price_vnd", "promotion")),
        ("Dữ liệu giá Coca Zero cập nhật ngày nào?", ("data_timestamp",)),
        ("Giá này được cập nhật ngày nào?", ("data_timestamp",)),
        ("Catalog có dữ liệu mới không?", ("data_timestamp",)),
        ("When was the data updated for Coca Zero?", ("data_timestamp",)),
    ],
)
def test_snapshot_intents_cover_colloquial_and_indirect_vietnamese(
    text: str, expected: tuple[str, ...]
) -> None:
    assert _requested_snapshot_fields(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Coca Zero có công dụng gì vậy?",
        "Uống cái này có tốt cho sức khỏe không?",
        "Ăn món này có sao không?",
        "Nó có bao nhiêu calo?",
        "Lượng đường trong chai là bao nhiêu?",
        "Bánh này có thành phần gây dị ứng không?",
        "Sản phẩm này có gluten hay lactose không?",
        "Món này hợp với người tiểu đường chứ?",
        "Bé nhỏ ăn món này được không?",
        "Bánh có phù hợp cho người ăn chay không?",
        "Dùng nó thế nào cho đúng?",
        "Mì này nấu kiểu gì?",
        "Bảo quản chai này ra sao?",
        "Món này có an toàn không?",
        "Nước giải khát Coca Zero có gì ngon?",
        "Coca Zero vị gì?",
        "Nước này có ít đường không?",
        "Bánh có chất bảo quản không?",
        "Món nào phù hợp cho người ăn kiêng?",
        "Tư vấn đồ uống giúp tỉnh táo.",
        "Có món nào dành cho người cao tuổi không?",
    ],
)
def test_unsupported_use_health_and_allergen_intents_are_not_guessed(text: str) -> None:
    assert _unsupported_catalog_attribute_requested(text)


@pytest.mark.parametrize(
    "text",
    [
        "Coca Zero giá bao nhiêu?",
        "Còn chai nước suối không?",
        "Sữa này để kệ nào?",
        "Thành phần của chiếc bánh này là gì?",
        "Mì này có cay không?",
    ],
)
def test_supported_product_facts_are_not_mistaken_for_unsupported_attributes(text: str) -> None:
    assert not _unsupported_catalog_attribute_requested(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Gợi ý cho mình món ăn vặt đi.", "snack"),
        ("Tư vấn đồ nhắm nhâm nhi dưới 20k.", "snack"),
        ("Tư vấn đồ lai rai dưới 20k.", "snack"),
        ("Bày tui món ăn vặt lai rai khi coi phim với.", "snack"),
        ("Tìm món ăn nhẹ cho mình.", "snack"),
        ("Tư vấn món mô ăn vặt ngon nhứt.", "snack"),
        ("Món quà vặt mô ngon nhứt?", "snack"),
        ("Chọn giúp đồ uống giải khát.", "drink"),
        ("Cho mình xin ý kiến chai nước nào dưới 10 nghìn.", "drink"),
        ("Tư vấn trà sữa cho mình.", "drink"),
        ("Mình khát nước, có gì uống lạnh không?", "drink"),
        ("Tư vấn cà phê ngon.", "drink"),
        ("Gợi ý sữa chua ít đường.", "dairy"),
        ("Mình muốn mua phô mai.", "dairy"),
        ("Chọn giúp mình món ăn sáng.", "meal"),
        ("Tư vấn bữa tối no bụng dưới 20 nghìn.", "meal"),
        ("Có mì ly nào ổn không?", "meal"),
        ("Tư vấn một món ăn no bụng.", "meal"),
        ("Gợi ý đồ ăn vặt, đừng lấy bữa chính.", "snack"),
        ("Mình đói, ăn gì bây giờ?", "meal"),
        ("Mình muốn uống gì mát mát.", "drink"),
    ],
)
def test_recommendation_category_handles_natural_vietnamese_paraphrases(
    text: str, expected: str
) -> None:
    assert _explicit_recommendation_category(text) == expected


def test_explicit_recommendation_language_forces_tool_even_without_extra_filters() -> None:
    assert _recommendation_tool_arguments("Gợi ý cho mình món ăn vặt đi.") == {"category": "snack"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Bày tui món ăn vặt lai rai khi coi phim với, dưới 20 nghìn.",
            {"category": "snack", "price_constraint": {"operator": "LT", "value_vnd": 20_000}},
        ),
        (
            "Tư vấn giúp mình bữa tối no bụng, tối đa 20k nha.",
            {"category": "meal", "price_constraint": {"operator": "LTE", "value_vnd": 20_000}},
        ),
        (
            "Cho mình xin ý kiến chai nước suối nào dưới 10 nghìn.",
            {
                "category": "drink",
                "query": "nuoc suoi",
                "price_constraint": {"operator": "LT", "value_vnd": 10_000},
            },
        ),
        (
            "Tui phân vân cà phê dưới hai chục, chọn dùm với.",
            {
                "category": "drink",
                "query": "ca phe",
                "price_constraint": {"operator": "LT", "value_vnd": 20_000},
            },
        ),
        (
            "Chọn hộ mình sữa chua nhé.",
            {"category": "dairy", "query": "sua chua"},
        ),
        (
            "Tư vấn Coca Zero giúp mình.",
            {"query": "coca zero"},
        ),
    ],
)
def test_consultation_routes_keep_the_customer_category_and_constraints(
    text: str, expected: dict[str, object]
) -> None:
    assert _recommendation_tool_arguments(text) == expected


def test_open_ended_consultation_is_recognized_but_does_not_guess_a_category() -> None:
    text = "Tư vấn giúp mình với, mình đang phân vân."
    assert is_recommendation_request(text)
    assert _recommendation_tool_arguments(text) is None
    assert not has_specific_product_reference(text)
    assert has_specific_product_reference("Tư vấn Coca Zero giúp mình.")
    assert consultation_product_query("Tư vấn Coca Zero giúp mình.") == "coca zero"
    assert not has_specific_product_reference("Cái nào bán chạy nhất?")


def test_contextual_consultation_reuses_category_only_from_a_prior_recommendation() -> None:
    assert _recommendation_tool_arguments(
        "Chọn hộ mình đi.",
        "Gợi ý món ăn vặt dưới 20 nghìn.",
    ) == {"category": "snack"}
    assert _recommendation_tool_arguments(
        "Tư vấn Pepsi giúp mình.",
        "Gợi ý món ăn vặt dưới 20 nghìn.",
    ) == {"query": "pepsi"}


def test_consultation_negation_does_not_trigger_product_recommendation() -> None:
    assert _recommendation_tool_arguments("Mình không cần tư vấn đồ uống nữa.") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mình muốn uống cà phê dưới 20k.", "ca phe"),
        ("Tư vấn sữa chua cho mình.", "sua chua"),
        ("Bày tui món ăn vặt.", None),
    ],
)
def test_specific_subcategory_is_preserved_without_overfiltering(
    text: str, expected: str | None
) -> None:
    assert explicit_recommendation_query(text) == expected


def test_regional_snack_recommendation_keeps_category_and_budget() -> None:
    assert _recommendation_tool_arguments(
        "Tư vấn đồ lai rai dưới hai chục nghìn nghen."
    ) == {
        "category": "snack",
        "price_constraint": {"operator": "LT", "value_vnd": 20_000},
    }


def test_regional_ranking_recognizes_snack_alias_and_avoids_non_food_gift() -> None:
    assert _recommendation_tool_arguments("Món quà vặt mô ngon nhứt?") == {
        "category": "snack"
    }
    assert _explicit_recommendation_category("Gợi ý quà vật chất cho giáo viên.") is None


def test_negated_recommendation_does_not_call_recommendation_tool() -> None:
    assert _recommendation_tool_arguments("Đừng gợi ý đồ ăn vặt dưới 20 nghìn nhé.") is None


def test_product_fact_about_a_drink_is_not_misrouted_as_a_recommendation() -> None:
    assert (
        _recommendation_tool_arguments("Nước giải khát Coca-Cola Zero Pet 600ml giá bao nhiêu?")
        is None
    )


def test_recommendation_keeps_natural_budget_and_spice_constraints() -> None:
    assert _recommendation_tool_arguments(
        "Chọn giúp mình đồ ăn vặt dưới hai chục nghìn, không cay nhé."
    ) == {
        "category": "snack",
        "price_constraint": {"operator": "LT", "value_vnd": 20_000},
        "spicy": False,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Giá ko?",
        "Thế giá sao?",
        "Còn hàng chứ?",
        "Để kệ nào ấy nhỉ?",
        "Còn hạn không?",
        "Trong này gồm những gì?",
    ],
)
def test_followup_phrasing_stays_elliptical_for_grounded_session_context(text: str) -> None:
    assert _implicit_followup_fields(text) == _requested_snapshot_fields(text)
