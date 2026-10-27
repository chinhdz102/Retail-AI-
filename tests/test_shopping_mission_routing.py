from backend.services.shopping_mission_routing import (
    is_budget_amount_only,
    needs_shopping_goal_clarification,
    should_route_to_shopping_mission,
)


def _route(
    text: str,
    *,
    active: bool = False,
    facts: bool = False,
    product: bool = False,
    recommendation: bool = False,
    recommendation_tool: bool = False,
    ranking: bool = False,
    explicit_category: bool = False,
    multi_category: bool = False,
) -> bool:
    return should_route_to_shopping_mission(
        text,
        active_mission=active,
        has_factual_lookup=facts,
        has_specific_product_reference=product,
        recommendation_requested=recommendation,
        recommendation_tool_ready=recommendation_tool,
        ranking_requested=ranking,
        has_explicit_category_request=explicit_category,
        has_multi_category_request=multi_category,
    )


def test_routes_broad_goals_without_a_product_name_to_mission_planner() -> None:
    assert _route(
        "Tối nay bạn mình đến chơi, 5 người, 200k, tư vấn giúp mình.",
        recommendation=True,
        recommendation_tool=True,
    )
    assert _route(
        "Mình cần chuẩn bị đón bạn bè qua nhà tối nay, có khoảng 3 người và ngân sách 80.000đ. "
        "Chưa biết nên bắt đầu từ đâu, tư vấn giúp mình.",
        recommendation=True,
        recommendation_tool=True,
    )
    assert _route("Mình đi du lịch, cần chuẩn bị gì?", recommendation=False)
    assert _route("What should I buy for a picnic?", recommendation=False)


def test_keeps_specific_product_requests_on_existing_catalog_routes() -> None:
    assert not _route("Gợi ý Coca Zero cho mình.", recommendation=True, recommendation_tool=True)
    assert not _route("Coca Zero giá bao nhiêu?", facts=True, product=True)
    assert not _route("Có Coca Zero không?", product=True)


def test_active_mission_routes_revisions_but_not_factual_or_ranking_questions() -> None:
    assert _route("Đổi nước ngọt thành nước lọc, tiền còn lại mua bánh quy.", active=True)
    assert _route("Thêm lựa chọn không cay và giảm ngân sách còn 150 nghìn.", active=True)
    assert not _route("Coca Zero giá bao nhiêu?", active=True, facts=True, product=True)
    assert _route(
        "Thêm bảo vệ da khi ra ngoài ban ngày vào nữa; các phần trước vẫn cần.",
        active=True,
        facts=True,
        product=True,
    )
    assert _route(
        "Thêm đồ ăn nhẹ cho người lớn trong chuyến đi vào nữa; các phần trước vẫn cần.",
        active=True,
        facts=True,
        product=True,
    )
    assert not _route("Thêm nước suối vào giỏ giúp mình.", active=True)
    assert _route(
        "Mình vẫn chỉ tham khảo, chưa quyết định mua; cho xem phương án "
        "nhưng đừng tự thêm gì vào giỏ.",
        active=True,
    )
    assert not _route(
        "Cho mình biết thêm thông tin về Coca Zero, giá bao nhiêu?",
        active=True,
        facts=True,
        product=True,
    )
    assert not _route(
        "Coca-Cola Zero chai 1,5l giá bao nhiêu? Đừng đổi kế hoạch đang lập.",
        active=True,
        facts=True,
        product=True,
    )
    assert not _route("Món nào bán chạy nhất?", active=True, ranking=True)
    assert _route(
        "Có thêm hai người tham gia, giờ thành 12 người; cập nhật phần chuẩn bị.",
        active=True,
        facts=True,
        product=True,
    )
    assert not _route(
        "Coca Zero giá bao nhiêu cho 12 người?", active=True, facts=True, product=True
    )
    assert _route(
        "Mình ưu tiên món ít cay; những nhu cầu đã chốt giữ nguyên nhé.",
        active=True,
    )
    assert _route(
        "Tui vẫn muốn món dễ mang theo, mấy nhóm chính cứ giữ nguyên nghen.",
        active=True,
    )
    assert _route(
        "Điều kiện bắt buộc là không có đường; nếu catalog không chứng minh được "
        "thì bỏ điều kiện và tiếp tục phương án.",
        active=True,
        facts=True,
        product=True,
    )


def test_new_mission_does_not_steal_known_category_or_product_recommendations() -> None:
    assert not _route("Gợi ý snack không cay.", recommendation=True, recommendation_tool=True)
    assert not _route(
        "Mình muốn đồ ăn vặt xem phim, chưa biết chọn loại nào.",
        recommendation=True,
        recommendation_tool=True,
        explicit_category=True,
    )
    assert not _route("Coca Zero giá 15.000đ?", product=True)
    assert not _route(
        "Gợi ý snack không cay xem phim.",
        recommendation=True,
        recommendation_tool=True,
        explicit_category=True,
    )
    assert not _route(
        "Da mình khô, mình chưa biết nên mua loại nào.",
        recommendation=True,
        recommendation_tool=True,
        explicit_category=True,
    )
    assert _route(
        "Giúp mình lên phương án cho việc bắt đầu chăm sóc da mặt vì da đang khô, "
        "chi khoảng 120.000đ.",
        recommendation_tool=True,
        explicit_category=True,
    )
    assert _route(
        "Mình muốn chăm sóc da, có 220.000đ thì nên ưu tiên nhu cầu nào trước?",
        recommendation_tool=True,
        explicit_category=True,
    )
    assert _route(
        "Mình chuẩn bị bữa sáng trước giờ học, chưa biết phải chuẩn bị những gì.",
        recommendation_tool=True,
        explicit_category=True,
    )


def test_routes_goals_and_activities_without_shopping_keywords_or_product_names() -> None:
    assert _route("Tối nay bạn mình đến chơi, 5 người, 200k, giúp mình với.")
    assert _route(
        "I have friends visiting tonight. There are five of us, and I can spend around 200k."
    )
    assert _route("Bọn mình sắp đi biển ba ngày, có mấy đứa trẻ con.")
    assert _route("Tôi đang chuẩn bị cho sinh nhật con, có 10 bé.")
    assert _route("Da mình hơi khô, có gì dùng không?", recommendation=True)
    assert _route("Coca Zero nhà mình có sẵn, tối nay bạn đến chơi, còn thiếu gì?", product=True)
    assert _route(
        "Giúp mình lên phương án cho việc bắt đầu chăm sóc da mặt vì da đang khô, "
        "chi khoảng 120.000đ; đừng cho vào giỏ vội.",
        recommendation_tool=True,
        explicit_category=True,
    )
    assert _route(
        "Cho mình xin kế hoạch mua sắm để chuẩn bị cho con vào năm học mới, "
        "dự trù 200.000đ cho 8 người; mình chưa có sản phẩm cụ thể trong đầu."
    )
    assert _route(
        "Mình chỉ có mục tiêu là lo đồ cho buổi họp ở văn phòng, khoảng 250.000đ "
        "cho 4 người. Cứ tư vấn từ vấn đề giúp mình, chưa cần biết tên hàng."
    )
    assert _route(
        "Tối ni mình phải mới dọn vào căn hộ và cần sắm đồ thiết yếu, có 300.000đ. "
        "Chưa biết cần chi, chỉ giúp mình lên phương án tham khảo."
    )
    assert _route(
        "Mình sắp đi dã ngoại ngoài công viên vào chủ nhật; nếu có 350.000đ "
        "cho 12 người thì nên ưu tiên nhu cầu nào trước."
    )
    assert _route(
        "Mình muốn vệ sinh căn nhà mới, có khoảng 100.000đ. Chưa biết nên bắt đầu từ đâu."
    )
    assert _route("Tôi muốn chuẩn bị bữa sáng cho gia đình; có 150.000đ cho 4 người.")
    assert _route(
        "Tôi có buổi đào tạo nội bộ cho 6 người, ngân sách 100.000đ; giúp chọn thứ tự ưu tiên."
    )
    assert _route("Tôi đưa bé qua nhà ông bà, có 80.000đ; tư vấn từ nhu cầu giúp tôi.")
    assert not _route(
        "Tư vấn snack nào ngon giúp mình.",
        recommendation=True,
        recommendation_tool=True,
        explicit_category=True,
    )


def test_activity_plus_budget_preempts_a_false_product_name_match() -> None:
    text = (
        "Giúp mình tính phần chuẩn bị cho lúc cả nhà xem phim ở phòng khách. "
        "Tụi mình có 3 người với ngân sách tối đa 280.000đ; cứ đưa ra gợi ý để mình "
        "cân nhắc, đừng thêm vào giỏ."
    )
    assert _route(
        text,
        product=True,
        recommendation=True,
        recommendation_tool=True,
    )


def test_multi_need_consultation_cue_preempts_a_product_name_false_positive() -> None:
    text = (
        "Sắp tới nhà mình mấy người bạn ghé xem trận bóng. Có 6 người, trần chi "
        "420.000đ; bạn xem từ việc cần làm rồi gợi ý các nhu cầu, chưa cần chọn theo tên hàng."
    )
    assert _route(
        text,
        product=True,
        recommendation=True,
        recommendation_tool=True,
    )


def test_unknown_category_request_routes_to_needs_discovery_without_activity_words() -> None:
    assert _route(
        "Tư vấn giúp mình cần những nhóm gì, mình chưa biết tên món cụ thể.",
        product=True,
        recommendation=True,
        recommendation_tool=True,
    )


def test_named_product_request_with_party_size_remains_a_single_product_recommendation() -> None:
    assert not _route(
        "Tư vấn Coca Zero cho 5 người, có 200.000đ.",
        product=True,
        recommendation=True,
        recommendation_tool=True,
    )


def test_explicit_product_price_question_stays_on_fact_lookup_with_activity_context() -> None:
    assert not _route(
        "Coca Zero giá 20.000đ cho buổi xem phim 5 người có đúng không?",
        facts=True,
        product=True,
        recommendation=True,
        recommendation_tool=True,
    )


def test_a_bare_currency_amount_is_a_budget_not_a_product_price_lookup() -> None:
    assert is_budget_amount_only("Mình có khoảng 250.000đ cho gia đình.")
    assert not is_budget_amount_only("Coca Zero giá 15.000đ.")
    assert not is_budget_amount_only("Sản phẩm này giá 200.000đ.")
    assert _route(
        "Tối nay bạn mình đến chơi, 5 người, 200k, giúp mình với.",
        facts=True,
    )
    assert not _route(
        "Coca Zero giá 15.000đ nhé.",
        facts=True,
        product=True,
    )


def test_asks_for_the_goal_when_customer_explicitly_says_the_context_is_missing() -> None:
    vague_request = (
        "Mình có việc cần chuẩn bị nhưng chưa nói rõ là dịp nào. "
        "Hỏi mình một câu về mục đích trước nhé."
    )

    assert needs_shopping_goal_clarification(vague_request, active_mission=False)
    assert not needs_shopping_goal_clarification(vague_request, active_mission=True)
