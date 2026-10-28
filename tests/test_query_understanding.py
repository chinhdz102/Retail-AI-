from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api.main import _localized_recommendation_candidates
from backend.products.catalog import ProductSnapshot
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.query_understanding import (
    prior_product_page_reference_kind,
    understand_catalog_query,
)
from backend.services.retail_tools import ToolObservation, execute_catalog_tool
from backend.services.vietnamese_intent import normalize_vietnamese

ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(ROOT / "data" / "winmart_products.json")


def test_query_understanding_separates_product_type_from_use_case() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Mình muốn tìm thịt để ăn lẩu",
        repository.list_category_hierarchies(),
    )

    assert result.intent == "recommendation"
    assert result.product_type == "Thịt"
    assert result.use_case == "ăn lẩu"
    assert "lẩu" not in result.product_reference.casefold()


@pytest.mark.parametrize(
    ("query", "expected_use_case"),
    [
        ("Tìm thịt để ăn lẩu giúp mình.", "ăn lẩu"),
        ("Tìm dầu gội để chăm sóc da đầu nhanh bết giúp t", "chăm sóc da đầu nhanh bết"),
        ("Tìm đồ ăn phù hợp cho trẻ", "trẻ"),
    ],
)
def test_use_case_keeps_customer_need_and_drops_only_polite_request_closing(
    query: str, expected_use_case: str
) -> None:
    repository = _snapshot()

    result = understand_catalog_query(
        query,
        repository.list_category_hierarchies(),
    )

    assert result.use_case == expected_use_case


def test_catalog_category_wording_is_not_mistaken_for_a_preparation_request() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Cho mình xem nhóm Đồ dùng nấu nướng",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Đồ dùng nấu nướng"
    assert result.use_case is None

    with_purpose = understand_catalog_query(
        "Cho mình xem nhóm Đồ dùng nấu nướng để nấu nướng",
        repository.list_category_hierarchies(),
    )
    assert with_purpose.product_type == "Đồ dùng nấu nướng"
    assert with_purpose.use_case == "nấu nướng"


@pytest.mark.parametrize(
    ("query", "expected_category"),
    [
        ("Mình đang cần Bút, cho mình xem vài loại.", "Bút"),
        ("Mình đang cần Muối, cho mình xem vài loại.", "Muối"),
        ("Mình đang cần Dầu hướng dương, cho mình xem vài loại.", "Dầu hướng dương"),
        ("Mình đang cần Lăn khử mùi Nam, cho mình xem vài loại.", "Lăn khử mùi Nam"),
    ],
)
def test_natural_request_prefix_preserves_catalog_leaf(
    query: str, expected_category: str
) -> None:
    repository = _snapshot()

    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.product_type == expected_category


def test_package_size_is_not_misread_as_purchase_quantity() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Mình muốn tìm Miến khô Vifon gói 300g",
        repository.list_category_hierarchies(),
    )

    assert result.quantity is None


def test_zero_quantity_like_negation_is_ignored_instead_of_rejecting_the_query() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Tìm nước ngọt không chai, dưới 20 nghìn.",
        repository.list_category_hierarchies(),
    )

    assert result.quantity is None
    assert result.budget_vnd == 20_000


def test_accented_hong_in_product_names_is_not_normalized_as_negation() -> None:
    repository = _snapshot()
    query = "Mình muốn tìm Giấy vệ sinh Elene hồng 3 lớp 10 cuộn"
    result = understand_catalog_query(query, repository.list_category_hierarchies())
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert "hồng" in query
    assert "khong" not in normalize_vietnamese("hồng")
    assert result.negative_constraints == ()
    assert "WIN_10020669" in {product.product_id for product in observation.products}


def test_accentless_colloquial_hong_still_means_khong() -> None:
    assert normalize_vietnamese("Sản phẩm này hong cay") == "san pham nay khong cay"


def test_product_name_words_that_overlap_category_do_not_filter_out_exact_item() -> None:
    repository = _snapshot()
    query = "Mình muốn tìm Hộp thực phẩm tròn Hokkaido 1000ml"
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert "WIN_10250121" in {product.product_id for product in observation.products}
    assert observation.content["query_understanding"]["product_type"] is None


def test_search_uses_the_original_utterance_when_the_tool_query_is_compacted() -> None:
    repository = _snapshot()
    user_text = "Nhà mình sắp hết Hộp thực phẩm tròn Hokkaido 1000ml, tìm giúp mình."
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "Hộp thực phẩm", "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=user_text,
    )

    assert "WIN_10250121" in {product.product_id for product in observation.products}
    assert observation.content["query_understanding"]["product_type"] is None


@pytest.mark.parametrize(
    "prompt_template",
    [
        "Có {name} không? Tìm đúng tên này giúp mình.",
        "Bạn tìm giúp {name} trong danh mục hiện tại với.",
    ],
)
def test_natural_wrappers_keep_exact_catalog_title_and_ignore_question_particle(
    prompt_template: str,
) -> None:
    repository = _snapshot()
    title_counts: dict[str, int] = {}
    for product in repository.products:
        key = product.name.casefold()
        title_counts[key] = title_counts.get(key, 0) + 1
    product = next(
        item
        for item in repository.products
        if title_counts[item.name.casefold()] == 1 and len(item.name.split()) >= 4
    )
    query = prompt_template.format(name=product.name)
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert observation.status == "SUCCESS"
    assert product.product_id in {item.product_id for item in observation.products}
    assert observation.content["query_understanding"]["unverified_constraints"] == []


def test_yes_no_particle_followed_by_comma_search_is_not_a_negative_filter() -> None:
    repository = _snapshot()
    query = "Cửa hàng có bán Bia nội địa không, tìm giúp mình với."
    understanding = understand_catalog_query(query, repository.list_category_hierarchies())
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert understanding.product_type == "Bia nội địa"
    assert understanding.negative_constraints == ()
    assert observation.status == "SUCCESS"
    assert observation.products


def test_single_word_catalog_type_survives_store_availability_wrapper() -> None:
    repository = _snapshot()
    query = "Cửa hàng có bán Bút không, tìm giúp mình với."
    understanding = understand_catalog_query(query, repository.list_category_hierarchies())
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )

    assert understanding.product_type == "Bút"
    assert understanding.negative_constraints == ()
    assert observation.status == "SUCCESS"
    assert observation.products
    assert all("Bút" in product.category_hierarchy for product in observation.products)


def test_single_character_brand_typo_does_not_turn_title_word_into_wrong_category() -> None:
    repository = _snapshot()
    query = "tìm giúp Hộp thực phẩm tròn Hokkido 1000ml"
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert "WIN_10250121" in {product.product_id for product in observation.products}


def test_catalog_category_request_using_dung_cu_is_not_parsed_as_negation() -> None:
    repository = _snapshot()
    query = "Cho mình xem nhóm Dụng cụ cọ rửa bếp"
    understanding = understand_catalog_query(query, repository.list_category_hierarchies())
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert understanding.negative_constraints == ()
    assert observation.status == "SUCCESS"
    assert observation.products


def test_elliptical_follow_up_inherits_only_the_previous_product_scope() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "loại nào rẻ nhất",
        repository.list_category_hierarchies(),
        previous_query="Mình muốn tìm dầu gội",
    )

    assert result.ranking_criterion == "price_ascending"
    previous = understand_catalog_query(
        "Mình muốn tìm dầu gội",
        repository.list_category_hierarchies(),
    )
    assert result.product_type == previous.product_type


@pytest.mark.parametrize(
    ("query", "expected_type", "expected_ranking"),
    [
        ("Mì gói có những loại nào?", "Mì ăn liền", "relevance"),
        ("Tã bỉm cho bé sơ sinh", "Tã dán trẻ em", "relevance"),
        ("Tìm kem chống nắng cho bé đi biển.", "Kem chống nắngtrẻ em", "relevance"),
        ("Mua giấy ướt em bé gói nhỏ có không?", "Giấy ướt trẻ em", "relevance"),
        ("Xà phòng tắm loại nào rẻ?", "Xà bông tắm", "price_ascending"),
    ],
)
def test_query_understanding_uses_catalog_synonyms_and_colloquial_price_language(
    query: str,
    expected_type: str,
    expected_ranking: str,
) -> None:
    repository = _snapshot()
    understanding = understand_catalog_query(query, repository.list_category_hierarchies())

    assert understanding.product_type == expected_type
    assert understanding.ranking_criterion == expected_ranking


def test_follow_up_chain_keeps_scope_across_an_intermediate_ranking_turn() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Có loại nào khác không?",
        repository.list_category_hierarchies(),
        previous_queries=("Mình muốn tìm dầu gội", "loại nào rẻ nhất"),
    )

    assert result.intent == "alternative"
    assert (
        result.product_type
        == understand_catalog_query(
            "Mình muốn tìm dầu gội",
            repository.list_category_hierarchies(),
        ).product_type
    )


@pytest.mark.parametrize(
    ("query", "expected_ranking"),
    [
        ("Món mô ngon nhứt?", "customer_rating"),
        ("Snack nào bán chạy nhứt?", "sales_popularity"),
    ],
)
def test_regional_vietnamese_superlatives_are_parsed_without_product_name_matching(
    query: str,
    expected_ranking: str,
) -> None:
    repository = _snapshot()
    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.ranking_criterion == expected_ranking


def test_category_lowest_price_query_returns_ascending_catalog_prices() -> None:
    repository = _snapshot()
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "thịt rẻ nhất", "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    prices = [product.price_vnd for product in observation.products]
    assert len(prices) == 5
    assert prices == sorted(prices)
    assert observation.products[0].price_vnd == min(
        product.price_vnd for product in repository.search("", category="Thịt", limit=20)
    )


def test_unavailable_best_seller_data_never_returns_an_unranked_product_as_best_seller() -> None:
    repository = _snapshot()
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "dầu gội bán chạy nhất", "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert observation.content["unavailable_fields"] == ["sales_count"]
    assert observation.products == ()


def test_unverified_negative_product_attribute_does_not_return_false_matches() -> None:
    repository = _snapshot()
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "nước uống không caffeine", "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert [
        normalize_vietnamese(value) for value in observation.content["unverified_constraints"]
    ] == ["caffeine"]
    assert observation.products == ()


def test_zero_sugar_request_returns_only_products_with_title_evidence() -> None:
    repository = _snapshot()
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": "nước uống không đường", "limit": 5}, ensure_ascii=False),
        repository=repository,
    )

    assert observation.status == "SUCCESS"
    assert observation.products
    assert all(
        "zero" in normalize_vietnamese(product.name)
        or "khong duong" in normalize_vietnamese(product.name)
        for product in observation.products
    )


def test_catalog_title_negation_is_not_misread_as_a_customer_filter() -> None:
    repository = _snapshot()
    for query in (
        "Mình muốn tìm Bột cà ri không cay Ottogi gói 100g",
        (
            "Mình muốn tìm Máy tăm nước không dây Lock&Lock ENR156BLU "
            "Cordless Oral Irrigator, 200ml - Màu xanh da trời"
        ),
        "Mình muốn tìm Khăn ướt không mùi Huggies 64 Miếng",
    ):
        observation = execute_catalog_tool(
            "search_products",
            json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
            repository=repository,
        )

        expected_name = query.removeprefix("Mình muốn tìm ")
        assert observation.status == "SUCCESS"
        assert any(
            normalize_vietnamese(product.name) == normalize_vietnamese(expected_name)
            for product in observation.products
        )


def test_use_case_recommendation_is_returned_as_unverified_category_options() -> None:
    repository = _snapshot()
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {
                "query": "thịt",
                "preference_text": "Mình muốn tìm thịt để ăn lẩu",
                "limit": 5,
            },
            ensure_ascii=False,
        ),
        repository=repository,
    )

    assert observation.products
    assert observation.content["use_case"] == "ăn lẩu"
    assert observation.content["use_case_verified"] is False
    assert observation.content["recommendation_evidence"] == "CATALOG_CATEGORY_ONLY"


@pytest.mark.parametrize(
    ("query", "expected_type"),
    [
        ("Dầu gội dành cho nữ bên mình chai nào giá thấp nhất?", "Dầu gội nữ"),
        ("Dầu gội dành cho nam rẻ nhất hiện bao nhiêu?", "Dầu gội nam"),
        ("Có kem đánh răng cho bé loại nhỏ không?", "Kem đánh răng trẻ em"),
        ("Có gói bim bim lát khoai tây giòn không?", "Snack kh.tây chiên"),
        ("Mì tôm nằm ở dãy nào?", "Mì ăn liền"),
        ("Có tuýp chống nắng nào gọn để mang đi chơi không?", "Kem chống nắng"),
        ("Loại giấy ăn mini để trong túi xách có không?", "Khăn giấy gói"),
        ("Nước suối để ở tủ mát hay khu đồ uống?", "Nước khoáng"),
        ("Cửa hàng đang có cải ngọt hay cải thìa?", "Rau cải"),
        ("Có cá đồng tươi nào để kho không?", "Cá nước ngọt tươi"),
        ("Có cà đỏ chín để làm sốt không?", "Cà chua"),
    ],
)
def test_catalog_synonyms_resolve_to_the_specific_catalog_type(
    query: str, expected_type: str
) -> None:
    repository = _snapshot()

    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert normalize_vietnamese(result.product_type) == normalize_vietnamese(expected_type)


@pytest.mark.parametrize(
    ("query", "expected_type"),
    [
        ("Cà chua ở khu rau ăn quả phải không?", "Cà chua"),
        ("Gạo tẻ ở khu thực phẩm khô hay đâu?", "Gạo tẻ"),
        ("Rau cải nằm khu rau củ phía nào?", "Rau cải"),
        ("Sữa tươi tiệt trùng nằm trong tủ mát hay kệ thường?", "Sữa tươi tiệt trùng"),
    ],
)
def test_location_context_does_not_replace_the_requested_product_type(
    query: str, expected_type: str
) -> None:
    repository = _snapshot()

    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.intent == "location"
    assert normalize_vietnamese(result.product_type) == normalize_vietnamese(expected_type)


@pytest.mark.parametrize(
    ("query", "expected_negative"),
    [
        ("Tìm nước rửa chén, đừng lấy loại hương chanh.", "hương chanh"),
        ("Mình tìm mì ăn liền nhưng bỏ qua loại vị hải sản.", "vị hải sản"),
        ("Tìm nước ngọt có ga nhưng loại trừ vị cola.", "vị cola"),
        ("Mình cần nước giặt, không muốn loại hương nồng.", "hương nồng"),
    ],
)
def test_vietnamese_negative_request_forms_extract_the_excluded_attribute(
    query: str, expected_negative: str
) -> None:
    repository = _snapshot()

    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert normalize_vietnamese(expected_negative) in {
        normalize_vietnamese(value) for value in result.negative_constraints
    }


@pytest.mark.parametrize(
    ("query", "expected_budget"),
    [
        ("Mình có khoảng 120k để mua dầu gội.", 120_000),
        ("Ngân sách 150 nghìn, chọn nước giặt.", 150_000),
        ("Mình có 30 nghìn, mua mì ăn liền.", 30_000),
        ("Nước rửa chén dưới 70 nghìn.", 70_000),
    ],
)
def test_budget_is_understood_from_common_spoken_forms(query: str, expected_budget: int) -> None:
    repository = _snapshot()

    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.budget_vnd == expected_budget


def test_comparison_and_location_acts_take_priority_over_mentions_of_a_place() -> None:
    repository = _snapshot()
    hierarchies = repository.list_category_hierarchies()

    comparison = understand_catalog_query(
        "Nước tinh khiết đóng chai với nước khoáng có gì khác theo nhãn?", hierarchies
    )
    location = understand_catalog_query("Chỉ giúp mình vị trí quầy kem đánh răng.", hierarchies)

    assert comparison.intent == "comparison"
    assert comparison.comparison_scope
    assert location.intent == "location"
    assert normalize_vietnamese(location.product_type) == normalize_vietnamese("Kem đánh răng")


def test_comparison_preserves_multiple_catalog_entities_instead_of_one_category() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "So sánh nước khoáng và nước ngọt, loại nào rẻ hơn?",
        repository.list_category_hierarchies(),
    )

    assert result.intent == "comparison"
    assert result.comparison_targets == ("nước khoáng", "nước ngọt")
    assert result.product_types == ("Nước khoáng", "Nước ngọt có ga")
    assert "price" in result.secondary_intents


def test_postposed_comparison_keeps_both_entities_and_multi_intent_turns() -> None:
    repository = _snapshot()
    hierarchies = repository.list_category_hierarchies()
    comparison = understand_catalog_query("Thịt bò với thịt heo khác nhau gì?", hierarchies)
    multi_intent = understand_catalog_query(
        "Tìm nước rửa chén, cho mình xem giá và ở kệ nào?", hierarchies
    )

    assert comparison.comparison_targets == ("Thịt bò", "thịt heo")
    assert comparison.product_types == ("Thịt",)
    assert multi_intent.intent == "location"
    assert {"price", "product_search"} <= set(multi_intent.secondary_intents)


def test_dialogue_reference_keeps_scope_after_alternative_and_ranking_turns() -> None:
    repository = _snapshot()
    history = [
        "Mình đang cần BVS Chu kỳ, cho mình xem vài loại.",
        "Trong mấy loại đó, loại nào rẻ nhất?",
        "Còn lựa chọn nào khác cùng nhóm không?",
    ]
    result = understand_catalog_query(
        "Giá của loại đầu tiên là bao nhiêu?",
        repository.list_category_hierarchies(),
        previous_query=history[-1],
        previous_queries=history,
    )

    assert result.intent == "price"
    assert result.conversation_reference == "loại đầu tiên"
    assert normalize_vietnamese(result.product_type) == normalize_vietnamese("BVS Chu kỳ")


@pytest.mark.parametrize(
    "query",
    [
        "Quay lại món ban đầu, giá bao nhiêu?",
        "Trở lại loại ban đầu nhé",
        "Sản phẩm ban đầu",
        "Back to the original item, please",
    ],
)
def test_explicit_original_product_reference_is_detected(query: str) -> None:
    assert prior_product_page_reference_kind(query) == "original"


def test_original_product_reference_does_not_capture_unrelated_text() -> None:
    assert prior_product_page_reference_kind("Món đầu tiên có vị gì?") is None


def test_original_product_price_followup_keeps_conversation_reference() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Quay lại món ban đầu, giá bao nhiêu?",
        repository.list_category_hierarchies(),
        previous_query="Gợi ý dầu gội",
        previous_queries=("Gợi ý dầu gội",),
    )

    assert result.intent == "price"
    assert result.conversation_reference == "Quay lại món ban đầu"
    assert result.product_type is not None


def test_normalized_location_spans_preserve_gendered_product_category() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Cho hỏi dầu gội nam ở dãy kệ nào vậy?",
        repository.list_category_hierarchies(),
    )

    assert result.intent == "location"
    assert result.product_type == "Dầu gội nam"
    assert "nam" in result.product_reference.casefold()


def test_long_direct_category_phrase_beats_embedded_short_alias() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Có cá nước ngọt sơ chế sạch sẵn không?",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Cá nước ngọt"
    assert result.product_type != "Nước ngọt có ga"


def test_yes_no_question_followed_by_discourse_marker_is_not_a_negative_filter() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Có mì ly và mì gói không, trước tiên cho mình xem mì gói.",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Mì ăn liền"
    assert result.negative_constraints == ()


@pytest.mark.parametrize(
    ("query", "expected_type"),
    [
        (
            "Nước giặt với bột giặt khác nhau gì? Trước mắt cho mình xem nước giặt.",
            "Nước giặt",
        ),
        (
            "Gạo tẻ với gạo nếp khác nhau thế nào? Mình đang cần gạo tẻ.",
            "Gạo tẻ",
        ),
        (
            "Sữa thanh trùng với tiệt trùng khác gì? Giới thiệu nhóm tiệt trùng trước.",
            "Sữa tươi tiệt trùng",
        ),
    ],
)
def test_explicit_final_selection_scopes_comparison_to_the_chosen_taxonomy_leaf(
    query: str, expected_type: str
) -> None:
    repository = _snapshot()
    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.intent == "comparison"
    assert result.product_type == expected_type
    assert result.comparison_scope == query.rstrip(".")


def test_zero_sugar_phrase_is_resolved_against_catalog_before_rejection() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Có lon nước ngọt loại không đường không?",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Nước ngọt có ga"
    assert "duong" in {normalize_vietnamese(value) for value in result.negative_constraints}


def test_product_correction_does_not_become_a_zero_currency_budget() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Mình muốn nước khoáng, không phải nước có ga.",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Nước khoáng"
    assert result.budget_vnd is None
    assert result.negative_constraints


def test_epistemic_safety_instruction_is_not_a_product_attribute_filter() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        (
            "Tư vấn nước giặt cho quần áo trẻ em, nhưng đừng khẳng định loại nào an toàn "
            "nếu nhãn không ghi."
        ),
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Nước giặt"
    assert result.negative_constraints == ()


def test_vocative_after_a_yes_no_question_is_not_an_exclusion() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Có bịch giấy cuộn nào to, dùng được lâu không shop?",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Giấy vệ sinh"
    assert result.negative_constraints == ()


def test_rau_cai_price_question_keeps_the_category_word_cai() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Rau cải nào rẻ nhất theo giá một ký?",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Rau cải"
    assert result.ranking_criterion == "price_ascending"


@pytest.mark.parametrize(
    ("query", "expected_type"),
    [
        ("Có dầu gội đầu cho nam mùi đừng nồng quá nha.", "Dầu gội nam"),
        ("Có snack khoai tây vị phô mai không?", "Snack kh.tây chiên"),
    ],
)
def test_product_attribute_words_do_not_replace_the_product_family(
    query: str,
    expected_type: str,
) -> None:
    repository = _snapshot()
    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.product_type == expected_type


@pytest.mark.parametrize(
    ("query", "expected_intent", "expected_ranking"),
    [
        (
            "Nhà mình hay nấu cơm mỗi ngày, gợi ý gạo tẻ theo quy cách và giá đang ghi nhé.",
            "recommendation",
            "relevance",
        ),
        (
            "Tã dán cho bé loại nào có giá dễ chịu nhất?",
            "price",
            "price_ascending",
        ),
        (
            "Dầu gội nam rẻ nhất hiện bao nhiêu?",
            "price",
            "price_ascending",
        ),
    ],
)
def test_recommendation_price_and_ranking_cues_are_separated(
    query: str,
    expected_intent: str,
    expected_ranking: str,
) -> None:
    repository = _snapshot()
    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.intent == expected_intent
    assert result.ranking_criterion == expected_ranking


def test_everyday_rice_wording_maps_to_catalog_taxonomy() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Cho mình loại gạo nấu cơm thường, không phải nếp nhé.",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Gạo tẻ"


def test_indirect_customer_need_is_kept_separate_from_product_category() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Da đầu mình nhanh bết, có dầu gội nào trong cửa hàng không?",
        repository.list_category_hierarchies(),
    )

    assert result.intent == "recommendation"
    assert result.product_reference == "dầu gội"
    assert result.use_case == "Da đầu mình nhanh bết"
    assert result.soft_preferences == ("Da đầu mình nhanh bết",)


def test_unverified_need_is_not_rendered_as_a_suitability_recommendation() -> None:
    repository = _snapshot()
    query = "Da đầu mình nhanh bết, có dầu gội nào trong cửa hàng không?"
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {"query": "dầu gội", "preference_text": query, "limit": 5},
            ensure_ascii=False,
        ),
        repository=repository,
    )
    answer = _localized_recommendation_candidates(observation, "vi")

    assert observation.content["use_case_verified"] is False
    assert "Catalog chưa có đủ thông tin" in answer
    assert "phù hợp với nhu cầu này" in answer
    assert "Da đầu mình nhanh bết" not in answer
    assert "Mình gợi ý" not in answer


def test_single_unverified_catalog_match_is_described_as_one_option() -> None:
    product = ProductSnapshot(
        product_id="SAMPLE-1",
        name="Dầu gội chống gàu 100ml",
        category="Chăm sóc tóc",
        price_vnd=95_200,
        stock=5,
        stock_is_current=False,
        uom="Chai",
    )
    observation = ToolObservation(
        content={"family_details_unverified": True, "current_stock_verified": False},
        products=(product,),
        tool_name="recommend_products",
    )

    answer = _localized_recommendation_candidates(observation, "vi")

    assert "một sản phẩm" in answer
    assert "Dầu gội chống gàu 100ml" in answer
    assert "chưa có bằng chứng độc lập" in answer
    assert "một số món cùng nhóm" not in answer


def test_total_budget_collocation_is_not_misread_as_a_product_benefit_question() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Mình cần hai chai nước rửa tay, tổng cộng đừng quá sáu chục nghìn.",
        repository.list_category_hierarchies(),
    )

    assert result.intent == "product_search"
    assert result.budget_vnd == 60_000
    assert result.budget_scope == "total"
    assert result.budget_operator == "LTE"
    assert result.quantity == 2
    assert not result.clarification_needed


def test_southern_vietnamese_address_and_chuc_budget_are_not_parsed_as_quantity() -> None:
    result = understand_catalog_query("Bày tui món ăn vặt dưới hai chục nghìn.")
    grouped_digits = understand_catalog_query("Gợi ý đồ uống dưới 10.000đ.")

    assert result.quantity is None
    assert result.budget_vnd == 20_000
    assert result.budget_scope == "per_item"
    assert grouped_digits.budget_vnd == 10_000
    assert grouped_digits.budget_operator == "LT"


def test_mens_body_wash_category_is_not_misread_as_a_five_dong_budget() -> None:
    repository = _snapshot()
    result = understand_catalog_query(
        "Cho mình xem Sữa tắm nam",
        repository.list_category_hierarchies(),
    )

    assert result.product_type == "Sữa tắm nam"
    assert result.budget_vnd is None


@pytest.mark.parametrize(
    ("query", "expected_scope", "expected_operator", "needs_clarification"),
    [
        (
            "Mình cần hai chai nước rửa tay, tổng cộng dưới sáu chục nghìn.",
            "total",
            "LT",
            False,
        ),
        (
            "Mình cần hai chai nước rửa tay, dưới sáu chục nghìn mỗi chai.",
            "per_item",
            "LT",
            False,
        ),
        ("Mình cần hai chai nước rửa tay dưới sáu chục nghìn.", None, "LT", True),
        ("Nước rửa tay tổng cộng dưới sáu chục nghìn.", "total", "LT", True),
    ],
)
def test_budget_parser_preserves_scope_and_asks_when_scope_is_ambiguous(
    query: str,
    expected_scope: str | None,
    expected_operator: str,
    needs_clarification: bool,
) -> None:
    repository = _snapshot()
    result = understand_catalog_query(query, repository.list_category_hierarchies())

    assert result.budget_scope == expected_scope
    assert result.budget_operator == expected_operator
    assert result.clarification_needed is needs_clarification


def test_snapshot_spice_exclusion_is_reported_as_unavailable() -> None:
    repository = _snapshot()
    observation = execute_catalog_tool(
        "recommend_products",
        json.dumps(
            {"query": "mì", "preference_text": "Mình muốn mì không cay", "limit": 5},
            ensure_ascii=False,
        ),
        repository=repository,
    )

    assert observation.status == "DATA_UNAVAILABLE"
    assert observation.content["unavailable_fields"] == ["product_attributes"]
