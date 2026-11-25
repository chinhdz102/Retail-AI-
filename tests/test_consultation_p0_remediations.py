"""Regression tests for P0/P1 catalog consultation, retrieval, routing, and RAGChecker fixes."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.api import main as api_main
from backend.products.repository import ProductSnapshot
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedPick,
    GroundedSelection,
    SearchNeed,
    _allowed,
    _explicit_sugar_level,
    _render,
    _retrieve,
)
from backend.services.product_comparison import parse_comparison_queries
from backend.services.query_understanding import understand_catalog_query
from backend.services.retail_tools import has_unverified_negative_catalog_constraint
from backend.services.vietnamese_intent import is_recommendation_request


@pytest.fixture(scope="module")
def thanh_lam_repo() -> SnapshotProductRepository:
    return SnapshotProductRepository(Path("data/thanh_lam_products.json"))


def test_q58_iced_coffee_pack_title_does_not_trigger_customer_care_claim() -> None:
    reply = (
        "Mình gợi ý Cà phê đen hòa tan G7 hộp 30g (giá tham khảo 42.500đ), "
        "Cà phê hòa tan 3 trong 1 Nescafé sữa đá hộp 200g (10 gói x 20g) (giá tham khảo 58.900đ)."
    )
    assert api_main._unsupported_customer_care_action_claim(reply) is False
    assert (
        api_main._unsupported_customer_care_action_claim(
            "Mình đã gọi nhân viên quản lý hỗ trợ bạn ngay ạ."
        )
        is True
    )
    assert (
        api_main._unsupported_customer_care_action_claim(
            "Mình đã ghi nhận phản ánh của bạn và sẽ chuyển cho cửa hàng xử lý."
        )
        is True
    )


def test_q93_ooc_gate_allows_roadtrip_snacks_and_blocks_true_ooc() -> None:
    assert (
        api_main._non_supermarket_out_of_catalog_requested(
            "Chuẩn bị đồ ăn nhẹ mang đi du lịch xa bằng ô tô nên mua gì?"
        )
        is False
    )
    assert (
        api_main._non_supermarket_out_of_catalog_requested(
            "Ở đây có bán máy lọc không khí Xiaomi hoặc nồi chiên không dầu không?"
        )
        is True
    )
    assert (
        api_main._non_supermarket_out_of_catalog_requested(
            "Mình muốn mua thức ăn hạt Whiskas cho mèo"
        )
        is True
    )


def test_q71_q72_comparison_propagates_shared_product_type(
    thanh_lam_repo: SnapshotProductRepository,
) -> None:
    hier = thanh_lam_repo.list_category_hierarchies()
    assert parse_comparison_queries(
        "So sánh kem đánh răng Sensodyne với Colgate loại nào tiết kiệm hơn?", hier
    ) == ("kem danh rang sensodyne", "kem danh rang colgate")
    assert parse_comparison_queries(
        "So sánh dầu ăn Simply với Neptune loại nào rẻ hơn theo lít?", hier
    ) == ("dau an simply", "dau an neptune")


@pytest.mark.parametrize(
    ("query", "expected_type_sub"),
    [
        ("Mình muốn mua xúc xích ăn liền cho bé mang đi học", "xúc xích"),
        ("Tìm mấy loại củ quả để hầm canh xương", "rau ăn củ"),
        ("Có bánh mì tươi hay bánh mì sandwich nào ngon không?", "banh my"),
        ("Có sữa chua uống nào tốt cho tiêu hóa không?", "scu"),
        ("Gợi ý vài loại bia nhẹ dễ uống cho buổi tụ tập cuối tuần", "bia"),
        ("Gợi ý vài loại nước ép trái cây đóng hộp cho cả nhà", "nước hoa quả"),
        ("Tư vấn các loại gia vị ướp thịt nướng BBQ tại nhà", "các loại sốt"),
        ("Mình muốn mua socola ngon làm quà tặng bạn gái", "chocolate"),
        ("Có kẹo dẻo trái cây nào ngon cho các bé không?", "kẹo mềm"),
        ("Tìm hạt điều hoặc đậu phộng rang để nhậu cùng bia", "hạt sấy khô"),
        ("Có khăn ướt không mùi an toàn cho da bé không?", "giấy ướt trẻ em"),
        ("Tìm snack ăn vặt cho người ăn chay, tránh vị thịt bò và hải sản", "snack"),
    ],
)
def test_taxonomy_retrieval_recall_on_thanh_lam_catalog(
    thanh_lam_repo: SnapshotProductRepository,
    query: str,
    expected_type_sub: str,
) -> None:
    hier = thanh_lam_repo.list_category_hierarchies()
    parsed = understand_catalog_query(query, hier)
    assert parsed.product_type is not None
    assert expected_type_sub in parsed.product_type.casefold()
    plan = ConsultationDecision(
        action="recommend",
        need=query,
        searches=[SearchNeed(category=parsed.product_type, role="primary")],
    )
    candidates = _retrieve(thanh_lam_repo, hier, plan, [])
    assert len(candidates) >= 1


def test_q17_q45_q48_q77_verified_attributes_and_sugar_or_logic(
    thanh_lam_repo: SnapshotProductRepository,
) -> None:
    hier = thanh_lam_repo.list_category_hierarchies()
    q17 = "Tìm sữa chua ăn không đường hoặc ít đường cho người giảm cân"
    assert _explicit_sugar_level(q17) == "less"
    assert has_unverified_negative_catalog_constraint(q17, thanh_lam_repo) is False
    plan_q17 = ConsultationDecision(
        action="recommend",
        need=q17,
        searches=[SearchNeed(category="Sữa chua ăn", role="primary")],
        sugar="less",
    )
    yogurts = _retrieve(thanh_lam_repo, hier, plan_q17, [])
    assert any("ít đường" in p.name.casefold() for p in yogurts)

    q45 = "Có nước rửa chén nào dịu nhẹ không hại da tay không?"
    assert is_recommendation_request(q45) is True
    assert has_unverified_negative_catalog_constraint(q45, thanh_lam_repo) is False

    q48 = "Có khăn ướt không mùi an toàn cho da bé không?"
    assert is_recommendation_request(q48) is True
    assert has_unverified_negative_catalog_constraint(q48, thanh_lam_repo) is False

    q77 = "sua tuoi vinamilk ko duong bao nhiu tien z?"
    assert has_unverified_negative_catalog_constraint(q77, thanh_lam_repo) is False


def test_q64_oreo_cocoa_exclusion_and_q67_peanut_allergy(
    thanh_lam_repo: SnapshotProductRepository,
) -> None:
    oreo = ProductSnapshot(
        product_id="TLM_OREO",
        name="Bánh quy nhân kem vani Oreo gói 66.5g",
        category="Bánh Xốp - Bánh Quy",
        brand="Oreo",
        price_vnd=10_000,
        stock=10,
    )
    butter = ProductSnapshot(
        product_id="TLM_DANISA",
        name="Bánh quy bơ Danisa hộp 454g",
        category="Bánh Xốp - Bánh Quy",
        brand="Danisa",
        price_vnd=146_000,
        stock=10,
    )
    plan = ConsultationDecision(action="recommend", need="bánh quy không vị socola")
    search = SearchNeed(category="Bánh biscuit", excluded=["socola"])
    assert _allowed(oreo, plan, search) is False
    assert _allowed(butter, plan, search) is True

    hier = thanh_lam_repo.list_category_hierarchies()
    parsed_q67 = understand_catalog_query(
        "Mình bị dị ứng đậu phộng, gợi ý vài loại hạt hoặc bánh ăn vặt an toàn nhé",
        hier,
    )
    assert any(
        "đậu phộng" in c.casefold() or "dau phong" in c.casefold()
        for c in parsed_q67.negative_constraints
    )
    assert parsed_q67.product_type is not None
    assert "đậu" not in parsed_q67.product_type.casefold()


def test_ragchecker_deterministic_claim_guards() -> None:
    cheap = ProductSnapshot(
        product_id="P1",
        name="Snack Oishi bắp ngọt 42g",
        category="Bánh Snack",
        brand="Oishi",
        price_vnd=5_500,
        stock=10,
    )
    pricier = ProductSnapshot(
        product_id="P2",
        name="Snack Big sheet truyền thống 3.2g",
        category="Bánh Snack",
        brand="Tao Kae Noi",
        price_vnd=8_700,
        stock=10,
    )
    plan = ConsultationDecision(action="recommend", need="snack không cay", max_options=2)
    inverted = GroundedSelection(
        picks=[GroundedPick(candidate=0), GroundedPick(candidate=1)],
        text=(
            "Dạ bạn xem {name:0} ({price:0}) gói 42g. "
            "Ngoài ra {name:1} ({price:1}) có giá thấp hơn món còn lại ạ."
        ),
    )
    with pytest.raises(ValueError, match="Comparative price claim contradicts"):
        _render(inverted, [cheap, pricier], plan)

    carton = ProductSnapshot(
        product_id="P3",
        name="Thùng 30 gói mì ăn liền vị gà nấm Micoem 70g",
        category="Mì",
        brand="Micoem",
        price_vnd=124_000,
        stock=10,
    )
    carton_sel = GroundedSelection(
        picks=[GroundedPick(candidate=0)],
        text="Dạ mình chọn {name:0} ({price:0}) dạng gói nhỏ tiện cho bé ạ.",
    )
    with pytest.raises(ValueError, match="full cartons"):
        _render(carton_sel, [carton], plan)

    sweet_rice = ProductSnapshot(
        product_id="P4",
        name="Bánh gạo ngọt Orion An vị tự nhiên 14 gói",
        category="Bánh Xốp - Bánh Quy",
        brand="Orion",
        price_vnd=21_600,
        stock=10,
    )
    salty_sel = GroundedSelection(
        picks=[GroundedPick(candidate=0)],
        text="Dạ bạn xem {name:0} ({price:0}) có vị mặn nhẹ cân lại vị ngọt ạ.",
    )
    with pytest.raises(ValueError, match="ngọt"):
        _render(salty_sel, [sweet_rice], plan)


def test_chua_cay_is_spicy_q01_fresh_fruit_and_q39_savory_rice_cracker(
    thanh_lam_repo: SnapshotProductRepository,
) -> None:
    from backend.services.product_constraints import title_has_descriptor

    assert (
        title_has_descriptor(
            "Thùng 30 gói mì ăn liền vị tôm chua cay Reeva gói 65g", "cay"
        )
        is True
    )
    assert title_has_descriptor("Mì chưa cay vị gà", "cay") is False

    hier = thanh_lam_repo.list_category_hierarchies()
    parsed_q01 = understand_catalog_query(
        "Gợi ý vài loại trái cây tươi ăn tráng miệng cho gia đình", hier
    )
    assert parsed_q01.product_type is not None
    assert (
        "trái cây" in parsed_q01.product_type.casefold()
        or "trai cay" in parsed_q01.product_type.casefold()
    )

    q39 = "Có bánh quy mặn hoặc bánh gạo nào ít ngọt không?"
    plan_q39 = ConsultationDecision(
        action="recommend",
        need=q39,
        searches=[SearchNeed(category="Bánh gạo", role="primary")],
        sugar="less",
    )
    crackers = _retrieve(thanh_lam_repo, hier, plan_q39, [])
    assert len(crackers) >= 1
    assert all("ngọt" not in p.name.casefold() for p in crackers)
