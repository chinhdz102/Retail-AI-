from backend.services.shopping_guidance import shopping_guidance


def test_reference_lookup_uses_whole_phrases_and_preserves_sources():
    entries = shopping_guidance("Mua nước lau sàn dùng quanh mèo")
    assert [entry["id"] for entry in entries] == ["aspca-cleaners-pets"]
    assert entries[0]["url"].startswith("https://www.aspca.org/")
    assert "match_any" not in entries[0]
    assert "không xác nhận an toàn một mã hàng" in entries[0]["summary"]


def test_unrelated_category_does_not_receive_health_guidance():
    assert shopping_guidance("Cần bút bi rẻ cho trường học") == []
    assert shopping_guidance("Mua hộp sữa") == []


def test_guidance_is_bounded_and_does_not_select_product_id():
    entries = shopping_guidance("Đồ ít ngọt cho trẻ, nhà có mèo", limit=2)
    assert len(entries) == 2
    assert all("product_id" not in entry for entry in entries)


def test_child_guidance_keeps_age_scope_and_acne_source_does_not_add_toner():
    entry = shopping_guidance("Bé ăn đồ ăn vặt")[0]
    assert "không áp dụng mọi hạn chế cho mọi độ tuổi" in entry["summary"]
    entry = shopping_guidance("Tư vấn chăm sóc da mụn")[0]
    assert entry["id"] == "aad-acne-care"
    assert "tránh chất làm se, toner" in entry["summary"]


def test_owned_small_space_does_not_trigger_child_safety_context():
    for text in (
        "Em cần loại nhỏ gọn thôi, phòng em bé lắm.",
        "Bếp mình bé quá, mua đồ gấp gọn được không?",
        "Nhà tôi hơi bé xíu, chọn loại nào cho gọn?",
    ):
        assert shopping_guidance(text) == []
    assert shopping_guidance("Mua đồ ăn vặt cho em bé")
    assert shopping_guidance("Phòng cho em bé, mua loại nào?")


def test_equipment_reference_retains_the_source_and_does_not_turn_into_a_food_recipe():
    entries = shopping_guidance("Ăn sáng không quá ngọt, ở chỗ làm chỉ có ấm đun nước")
    by_id = {entry["id"]: entry for entry in entries}
    assert {"philips-water-kettle-use", "fda-ready-to-cook", "who-sugars"} <= by_id.keys()
    entry = by_id["philips-water-kettle-use"]
    assert entry["url"].startswith("https://www.philips.co.in/")
    assert "không tự đề xuất cho thức ăn vào nấu trực tiếp" in entry["summary"]
    assert "product_id" not in entry and "phở" not in entry["summary"]


def test_sugar_label_guidance_distinguishes_added_and_total_without_certifying_a_sku():
    entries = shopping_guidance("Em đang hạn chế đường, chọn đồ uống để đi làm")
    entry = next(item for item in entries if item["id"] == "fda-total-added-sugars")
    assert entry["url"].startswith("https://www.fda.gov/")
    assert "Không thêm đường khác với không có đường tổng số" in entry["summary"]
    assert "không xác nhận thành phần hay dinh dưỡng của một mã hàng" in entry["summary"]
    assert "product_id" not in entry
