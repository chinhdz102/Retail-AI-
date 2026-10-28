from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.api.schemas import ShoppingPlanResponse
from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.products.repository import DemoProductRepository
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.retail_tools import execute_catalog_tool
from backend.services.shopping_mission import (
    _is_structured_mission_metadata,
    _quantity_for_product,
    build_shopping_mission_plan,
    render_shopping_plan_summary,
)


def _need(
    need_id: str,
    category: str,
    *,
    source: str = "EXPLICIT",
    required: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    return {
        "need_id": need_id,
        "label": need_id.replace("_", " "),
        "category": category,
        "source": source,
        "required": required,
        **overrides,
    }


def _plan(
    needs: list[dict[str, Any]],
    **arguments: Any,
):
    return build_shopping_mission_plan(
        {"goal": "Help the customer shop", "needs": needs, **arguments},
        DemoProductRepository(),
    )


def test_builds_grounded_multi_category_plan_and_forces_inferred_needs_optional() -> None:
    result = _plan(
        [
            _need("snacks", "snack", rationale="For sharing with guests"),
            _need("drinks", "drink", source="INFERRED", required=True),
        ],
        total_budget_vnd=30_000,
    )

    assert result.content["all_products_exist_in_catalog"] is True
    assert result.content["cart_mutated"] is False
    assert result.content["missing_required_needs"] == []
    assert result.content["needs"][1]["required"] is False
    assert result.content["inferred_needs"] == ["drinks"]
    option = result.content["options"][0]
    assert {item["need_id"] for item in option["items"]} == {"snacks", "drinks"}
    assert option["total_vnd"] == sum(item["line_total_vnd"] for item in option["items"])
    assert option["total_vnd"] <= 30_000
    assert {item["product_id"] for item in option["items"]} <= {
        product.product_id for product in result.products
    }


def test_summary_labels_optional_suggestions_and_separates_their_cost() -> None:
    result = _plan(
        [
            _need("snacks", "snack", label="Đồ ăn nhẹ"),
            _need("drinks", "drink", label="Đồ uống", source="INFERRED", required=False),
        ],
        party_size=5,
    )

    for language, required_term, optional_term in (
        ("vi", "theo yêu cầu của bạn", "nếu muốn mua thêm"),
        ("en", "based on what you asked for", "if useful, you could also add"),
        ("zh", "按你的要求", "如果你需要，也可以加上"),
    ):
        summary = render_shopping_plan_summary(result.content, language=language).casefold()
        assert required_term.casefold() in summary
        assert optional_term.casefold() in summary
        assert ".." not in summary
    assert "phần mua thêm" in render_shopping_plan_summary(result.content).casefold()


def test_summary_varies_on_follow_up_and_formats_vietnamese_prices_naturally() -> None:
    content = {
        "mission": {"goal": "Chuẩn bị đồ ăn nhẹ", "revision": 1},
        "needs": [
            {"need_id": "snacks", "label": "Đồ ăn nhẹ"},
            {"need_id": "drinks", "label": "Đồ uống"},
        ],
        "options": [
            {
                "items": [
                    {
                        "name": "Snack khoai tây",
                        "quantity": 1,
                        "line_total_vnd": 12_500,
                        "required": True,
                    },
                    {
                        "name": "Nước suối",
                        "quantity": 1,
                        "line_total_vnd": 8_000,
                        "required": False,
                    },
                ],
                "total_vnd": 20_500,
            }
        ],
        "data_timestamp": "2026-09-15T13:53:35.404439Z",
        "inventory_verified": False,
        "cart_mutated": False,
    }

    first = render_shopping_plan_summary(content)
    updated = render_shopping_plan_summary(
        {**content, "mission": {**content["mission"], "revision": 2}}
    )

    assert "12.500đ" in first and "20.500đ" in first and "8.000đ" in first
    assert "Dữ liệu catalog ghi ngày 15/09/2026" in first
    assert "tồn kho tại cửa hàng chưa được xác nhận theo thời gian thực" in first
    assert "chưa thêm món nào vào giỏ" in first
    assert "Mình cập nhật phương án nhé" not in updated
    assert updated.startswith("Theo yêu cầu của bạn:")
    assert "tồn kho tại cửa hàng chưa được xác nhận" not in updated
    assert "chưa thêm món nào vào giỏ" not in updated
    assert len(updated) < len(first)


def test_followup_summary_reports_only_added_catalog_items_and_recomputed_total() -> None:
    previous = {
        "revision": 1,
        "total_budget_vnd": 220_000,
        "needs": [
            {"need_id": "cleanser", "label": "Sữa rửa mặt", "required": False},
            {"need_id": "moisturizer", "label": "Kem dưỡng ẩm", "required": False},
            {"need_id": "sunscreen", "label": "Kem chống nắng", "required": False},
        ],
        "proposed_items": [
            {
                "product_id": "cleanser-sku",
                "name": "Sữa rửa mặt",
                "quantity": 1,
                "line_total_vnd": 54_000,
                "required": False,
            },
            {
                "product_id": "sunscreen-sku",
                "name": "Kem chống nắng",
                "quantity": 1,
                "line_total_vnd": 68_800,
                "required": False,
            },
        ],
    }
    content = {
        "mission": {
            "revision": 2,
            "total_budget_vnd": 220_000,
            "needs": [
                {"need_id": "cleanser", "label": "Sữa rửa mặt", "required": True},
                {
                    "need_id": "moisturizer",
                    "label": "Sản phẩm dưỡng da mặt",
                    "required": True,
                },
                {"need_id": "sunscreen", "label": "Kem chống nắng", "required": False},
                {"need_id": "cotton", "label": "Bông tẩy trang", "required": True},
            ],
        },
        "needs": [
            {"need_id": "cleanser", "label": "Sữa rửa mặt"},
            {"need_id": "moisturizer", "label": "Sản phẩm dưỡng da mặt"},
            {"need_id": "sunscreen", "label": "Kem chống nắng"},
            {"need_id": "cotton", "label": "Bông tẩy trang"},
        ],
        "options": [
            {
                "items": [
                    {
                        "product_id": "cleanser-sku",
                        "need_id": "cleanser",
                        "name": "Sữa rửa mặt",
                        "quantity": 1,
                        "line_total_vnd": 54_000,
                        "required": True,
                    },
                    {
                        "product_id": "sunscreen-sku",
                        "need_id": "sunscreen",
                        "name": "Kem chống nắng",
                        "quantity": 1,
                        "line_total_vnd": 68_800,
                        "required": False,
                    },
                    {
                        "product_id": "cotton-sku",
                        "need_id": "cotton",
                        "name": "Bông tẩy trang Jomi 120 miếng",
                        "quantity": 1,
                        "line_total_vnd": 39_000,
                        "required": True,
                    },
                ],
                "total_vnd": 161_800,
                "remaining_budget_vnd": 58_200,
            }
        ],
        "missing_required_needs": ["moisturizer"],
        "inventory_verified": False,
    }

    summary = render_shopping_plan_summary(content, previous_mission=previous)

    assert "Mình thêm vào phương án" in summary
    assert "Bông tẩy trang Jomi 120 miếng × 1 (39.000đ)" in summary
    assert "Tổng phương án hiện tại: 161.800đ" in summary
    assert "Còn 58.200đ trong mức dự trù" in summary
    assert "Sản phẩm dưỡng da mặt" in summary
    assert "Nếu bạn cho biết loại cụ thể hơn" in summary
    assert "Sữa rửa mặt × 1 (54.000đ)" not in summary


def test_followup_summary_acknowledges_new_required_need_already_in_plan() -> None:
    previous = {
        "revision": 2,
        "total_budget_vnd": 220_000,
        "needs": [{"need_id": "sunscreen", "label": "Kem chống nắng", "required": False}],
        "proposed_items": [
            {
                "product_id": "sunscreen-sku",
                "name": "Kem chống nắng",
                "quantity": 1,
                "line_total_vnd": 68_800,
                "required": False,
            }
        ],
    }
    content = {
        "mission": {
            "revision": 3,
            "total_budget_vnd": 240_000,
            "needs": [{"need_id": "sunscreen", "label": "Kem chống nắng", "required": True}],
        },
        "needs": [{"need_id": "sunscreen", "label": "Kem chống nắng"}],
        "options": [
            {
                "items": [
                    {
                        "product_id": "sunscreen-sku",
                        "need_id": "sunscreen",
                        "name": "Kem chống nắng",
                        "quantity": 1,
                        "line_total_vnd": 68_800,
                        "required": True,
                    }
                ],
                "total_vnd": 68_800,
                "remaining_budget_vnd": 171_200,
            }
        ],
        "missing_required_needs": [],
        "inventory_verified": False,
    }

    summary = render_shopping_plan_summary(content, previous_mission=previous)

    assert "Nhóm vừa được chốt đã có trong phương án: Kem chống nắng × 1" in summary
    assert "Mức dự trù mới là 240.000đ" in summary
    assert "Tổng phương án hiện tại: 68.800đ" in summary
    assert "Còn 171.200đ trong mức dự trù" in summary


def test_product_query_does_not_cross_match_a_different_catalog_product_type() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )

    cotton_pads = build_shopping_mission_plan(
        {
            "goal": "Mua bông tẩy trang",
            "needs": [
                _need(
                    "cotton-pads",
                    "Phi thực phẩm > Hoá mỹ phẩm > Giấy và bông > Băng vệ sinh",
                    label="Bông tẩy trang",
                    product_query="bông tẩy trang",
                )
            ],
        },
        repository,
    )
    cotton_plan = cotton_pads.content["options"][0]["items"]
    assert cotton_pads.content["needs"][0]["resolved_category"] == "Bông tẩy trang"
    assert cotton_plan
    assert all("bông tẩy trang" in item["name"].casefold() for item in cotton_plan)
    assert all("diana" not in item["name"].casefold() for item in cotton_plan)

    face_towels = build_shopping_mission_plan(
        {
            "goal": "Mua khăn mặt",
            "needs": [
                _need(
                    "face-towel",
                    "Phi thực phẩm > Hoá mỹ phẩm > Giấy và bông > Giấy ăn, giấy vệ sinh",
                    label="Khăn mặt",
                    product_query="khăn mặt",
                )
            ],
        },
        repository,
    )
    towel_plan = face_towels.content["options"][0]["items"]
    assert face_towels.content["needs"][0]["resolved_category"] == "Khăn mặt"
    assert towel_plan
    assert all("khăn mặt" in item["name"].casefold() for item in towel_plan)
    assert all("khăn giấy" not in item["name"].casefold() for item in towel_plan)

    napkins = build_shopping_mission_plan(
        {
            "goal": "Chuẩn bị giấy lau miệng khi ăn",
            "needs": [
                _need(
                    "napkins",
                    "Phi thực phẩm > Dệt may > Bông vải sợi, giày dép > Khăn > Khăn lau",
                    label="Khăn lau miệng",
                    product_query="khăn lau miệng cho cả nhóm",
                )
            ],
        },
        repository,
    )
    napkin_plan = napkins.content["options"][0]["items"]
    assert napkin_plan
    assert all(
        "giấy" in item["name"].casefold() or "khăn giấy" in item["name"].casefold()
        for item in napkin_plan
    )
    assert all("lau mặt" not in item["name"].casefold() for item in napkin_plan)

    broad_skincare = build_shopping_mission_plan(
        {
            "goal": "Chăm sóc da mặt cơ bản",
            "needs": [
                _need(
                    "skincare",
                    "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
                    label="Sản phẩm dưỡng da mặt",
                    product_query="Sản phẩm dưỡng da mặt",
                )
            ],
        },
        repository,
    )
    assert broad_skincare.content["options"]
    assert broad_skincare.content["needs"][0]["candidate_count"] > 0

    moisturizer = build_shopping_mission_plan(
        {
            "goal": "Tìm kem dưỡng ẩm da mặt",
            "needs": [
                _need(
                    "moisturizer",
                    "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
                    label="Kem dưỡng ẩm",
                    product_query="kem dưỡng ẩm da mặt",
                )
            ],
        },
        repository,
    )
    assert moisturizer.content["needs"][0]["candidate_count"] == 0
    assert moisturizer.content["missing_required_needs"] == ["Kem dưỡng ẩm"]

    sunscreen = build_shopping_mission_plan(
        {
            "goal": "Tìm kem chống nắng",
            "needs": [
                _need(
                    "sunscreen",
                    "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > Chăm sóc da",
                    label="Kem chống nắng",
                    product_query="kem chống nắng",
                )
            ],
        },
        repository,
    )
    assert sunscreen.content["needs"][0]["candidate_count"] > 0
    assert all(
        "chống nắng" in item["name"].casefold() for item in sunscreen.content["options"][0]["items"]
    )

    face_cleanser = build_shopping_mission_plan(
        {
            "goal": "Mua sữa rửa mặt",
            "needs": [
                _need(
                    "face-cleanser",
                    "Nước rửa chén",
                    label="Sữa rửa mặt",
                    product_query="nước rửa mặt",
                )
            ],
        },
        repository,
    )
    face_cleanser_items = face_cleanser.content["options"][0]["items"]
    assert face_cleanser_items
    assert all("rửa mặt" in item["name"].casefold() for item in face_cleanser_items)
    assert all("rửa chén" not in item["name"].casefold() for item in face_cleanser_items)


def test_unknown_package_size_names_item_and_says_original_quantity_needs_confirmation() -> None:
    content = {
        "mission": {"goal": "Đồ ăn nhẹ cho nhóm bạn"},
        "needs": [],
        "options": [
            {
                "items": [
                    {
                        "name": "Cam cara Úc (3 trái/hộp)",
                        "quantity": 1,
                        "line_total_vnd": 109_000,
                        "required": False,
                        "quantity_needs_review": True,
                    }
                ],
                "total_vnd": 109_000,
            }
        ],
        "inventory_verified": False,
        "cart_mutated": False,
    }

    summary = render_shopping_plan_summary(content)

    assert "nếu muốn mua thêm" in summary.casefold()
    assert "cam cara úc" in summary.casefold()
    assert "giữ số lượng ban đầu" in summary.casefold()


def test_budget_and_party_paraphrases_are_not_treated_as_unverified_product_attributes() -> None:
    planning_metadata = "tổng chi khoảng 150.000đ cho cả nhóm 7 người"

    result = _plan(
        [
            _need(
                "snacks",
                "snack",
                unverified_constraints=[planning_metadata],
            )
        ],
        total_budget_vnd=150_000,
        party_size=7,
        hard_requirements=[planning_metadata],
        exclusions=[planning_metadata],
    )

    assert result.content["options"]
    assert result.content["missing_required_needs"] == []
    assert result.mission_state["needs"][0]["unverified_constraints"] == []
    assert result.mission_state["hard_requirements"] == []
    assert result.mission_state["exclusions"] == []
    assert _is_structured_mission_metadata("đủ cho 7 người", {"party_size": 7})
    assert _is_structured_mission_metadata("dễ chia sẻ cho khoảng 7 người", {"party_size": 7})
    assert _is_structured_mission_metadata("dùng cho khoảng 7 người", {"party_size": 7})
    assert not _is_structured_mission_metadata("không cay cho 7 người", {"party_size": 7})


def test_mission_can_represent_six_distinct_category_needs() -> None:
    result = _plan(
        [
            _need(
                f"need_{index}",
                f"uncatalogued category {index}",
                source="INFERRED",
                required=False,
            )
            for index in range(1, 7)
        ]
    )

    assert len(result.mission_state["needs"]) == 6


def test_plan_tool_executes_successfully_through_the_catalog_gateway() -> None:
    observation = execute_catalog_tool(
        "plan_shopping_mission",
        json.dumps(
            {
                "goal": "Prepare snacks for guests",
                "needs": [_need("snacks", "snack")],
            }
        ),
        repository=DemoProductRepository(),
    )

    assert observation.status == "SUCCESS"
    assert observation.content["all_products_exist_in_catalog"] is True
    assert observation.content["cart_mutated"] is False
    assert observation.products


def test_quantity_math_reports_shortfall_without_fabricating_a_plan() -> None:
    result = _plan(
        [
            _need("snacks", "snack", quantity=2),
            _need("drinks", "drink", quantity=2),
        ],
        total_budget_vnd=30_000,
    )

    assert result.content["options"] == []
    assert result.content["budget_shortfall_vnd"] == 20_000
    assert result.content["cart_mutated"] is False


def test_party_size_estimates_grounded_beverage_and_snack_units_with_exact_totals() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    result = build_shopping_mission_plan(
        {
            "goal": "Chuẩn bị đồ ăn uống cho cả nhóm xem bóng",
            "party_size": 5,
            "total_budget_vnd": 120_000,
            "needs": [
                _need(
                    "drinks",
                    "Nước ngọt có ga",
                    label="Đồ uống",
                    product_query="Coca-Cola Sleek 235ml",
                ),
                _need(
                    "snacks",
                    "Snack rau, củ, quả",
                    label="Snack chia sẻ",
                    product_query="Oishi Snack Bắp Vị Sữa 34gr",
                ),
                _need(
                    "fruit",
                    "Trái cây nội địa",
                    label="Trái cây",
                    product_query="Cam Sành loại nhỏ",
                ),
            ],
        },
        repository,
    )

    option = result.content["options"][0]
    by_need = {item["need_id"]: item for item in option["items"]}
    assert by_need["drinks"]["product_id"] == "WIN_10150435"
    assert by_need["drinks"]["quantity"] == 6  # ceil(5 * 250ml / 235ml)
    assert by_need["drinks"]["quantity_estimate"] == {
        "party_size": 5,
        "target_per_person": 250,
        "measurement": "ml",
        "package_size": 235,
    }
    assert by_need["snacks"]["product_id"] == "WIN_10639592"
    assert by_need["snacks"]["quantity"] == 5  # ceil(5 * 30g / 34g)
    assert by_need["fruit"]["product_id"] == "WIN_10054858"
    assert by_need["fruit"]["quantity"] == 1  # 1kg pack covers the 500g target
    assert by_need["fruit"]["quantity_estimate"] == {
        "party_size": 5,
        "target_per_person": 100,
        "measurement": "g",
        "package_size": 1000,
    }
    assert option["total_vnd"] == sum(item["line_total_vnd"] for item in option["items"])
    assert option["total_vnd"] == 114_800
    assert option["total_vnd"] <= 120_000
    api_plan = ShoppingPlanResponse.model_validate(result.content)
    assert api_plan.options[0].items[0].quantity_basis == "party_size_estimate"
    assert {target.target_per_person for target in api_plan.quantity_estimation_targets} == {
        30,
        100,
        250,
    }
    summary = render_shopping_plan_summary(result.content)
    assert "250 ml đồ uống/người" in summary
    assert "30 g đồ ăn nhẹ/người" in summary
    assert "100 g trái cây/người" in summary


def test_vietnamese_liter_sku_size_is_used_for_beverage_serving_estimate() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    product = repository.get_product_snapshot("WIN_10011250")
    assert product is not None
    quantity, basis, estimate = _quantity_for_product(
        {"label": "Đồ uống", "category": "Nước uống", "quantity": 1},
        {"party_size": 5},
        product,
    )

    assert quantity == 1
    assert basis == "party_size_estimate"
    assert estimate == {
        "party_size": 5,
        "target_per_person": 250,
        "measurement": "ml",
        "package_size": 1500,
    }


def test_large_party_plan_can_exceed_cart_write_quantity_limit_without_mutating_cart() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    result = build_shopping_mission_plan(
        {
            "goal": "Đồ uống cho một sự kiện lớn",
            "party_size": 100,
            "total_budget_vnd": 1_000_000,
            "needs": [
                _need(
                    "drinks",
                    "Nước ngọt có ga",
                    label="Đồ uống",
                    product_query="Coca-Cola Sleek 235ml",
                )
            ],
        },
        repository,
    )

    item = next(
        item
        for option in result.content["options"]
        for item in option["items"]
        if item["product_id"] == "WIN_10150435"
    )
    assert item["quantity"] == 107
    assert item["line_total_vnd"] == 941_600
    assert result.content["cart_mutated"] is False


def test_party_size_revision_recalculates_estimated_quantity_but_explicit_count_is_kept() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    initial = build_shopping_mission_plan(
        {
            "goal": "Đồ uống cho nhóm",
            "party_size": 5,
            "needs": [
                _need(
                    "drinks",
                    "Nước ngọt có ga",
                    label="Đồ uống",
                    product_query="Coca-Cola Sleek 235ml",
                )
            ],
        },
        repository,
    )
    revised = build_shopping_mission_plan(
        {"goal": "", "party_size": 2, "needs": []},
        repository,
        initial.mission_state,
    )
    assert revised.content["options"][0]["items"][0]["quantity"] == 3

    fixed = build_shopping_mission_plan(
        {
            "goal": "Đồ uống cho nhóm",
            "party_size": 5,
            "needs": [
                _need(
                    "drinks",
                    "Nước ngọt có ga",
                    label="Đồ uống",
                    product_query="Coca-Cola Sleek 235ml",
                    quantity=2,
                    quantity_is_explicit=True,
                )
            ],
        },
        repository,
    )
    fixed_item = fixed.content["options"][0]["items"][0]
    assert fixed_item["quantity"] == 2
    assert fixed_item["quantity_basis"] == "customer_specified"


def test_party_size_does_not_scale_personal_care_and_unknown_pack_size_is_disclosed() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    skincare_paths = next(
        path for path in repository.list_category_hierarchies() if "Chăm sóc da" in path
    )
    skincare_category = " > ".join(skincare_paths[: skincare_paths.index("Chăm sóc da") + 1])
    personal_care = build_shopping_mission_plan(
        {
            "goal": "Mua đồ chăm sóc da cho nhóm",
            "party_size": 5,
            "needs": [_need("care", skincare_category)],
        },
        repository,
    )
    assert all(
        item["quantity"] == 1
        for option in personal_care.content["options"]
        for item in option["items"]
    )

    unknown_size = ProductSnapshot(
        product_id="DRINK-UNKNOWN-SIZE",
        name="Nước uống đóng chai",
        category="drink",
        price_vnd=10_000,
        stock=10,
        uom="chai",
    )
    quantity, basis, estimate = _quantity_for_product(
        {"label": "Đồ uống", "category": "Nước uống", "quantity": 1},
        {"party_size": 5},
        unknown_size,
    )
    assert quantity == 1
    assert basis == "catalog_size_unknown"
    assert estimate is None


def test_specific_product_phrase_stays_narrow_for_non_skincare_categories() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    plan = build_shopping_mission_plan(
        {
            "goal": "Tìm thịt thăn bò",
            "needs": [
                _need(
                    "beef_tenderloin",
                    "Thịt",
                    label="Thịt thăn bò",
                    product_query="thịt thăn bò",
                )
            ],
        },
        repository,
    )

    items = plan.content["options"][0]["items"]
    assert items
    assert all("thăn bò" in item["name"].casefold() for item in items)


def test_local_unverified_requirement_blocks_only_its_own_optional_need() -> None:
    result = _plan(
        [
            _need(
                "snacks",
                "snack",
                source="INFERRED",
                required=False,
                unverified_constraints=["must be safe for a severe peanut allergy"],
            ),
            _need("drinks", "drink"),
        ]
    )

    assert result.content["missing_optional_needs"] == ["snacks"]
    assert result.content["missing_required_needs"] == []
    assert result.content["needs"][0]["blocked_reasons"] == [
        "unverified:must be safe for a severe peanut allergy"
    ]
    assert [item["need_id"] for item in result.content["options"][0]["items"]] == ["drinks"]


def test_global_unknown_requirement_fails_closed_for_all_proposed_items() -> None:
    result = _plan(
        [_need("snacks", "snack"), _need("drinks", "drink", source="INFERRED", required=False)],
        hard_requirements=["all items must be medically safe for this customer"],
    )

    assert result.content["options"] == []
    assert result.content["missing_required_needs"] == ["snacks"]
    assert all(not need["candidate_product_ids"] for need in result.content["needs"])
    assert result.content["unresolved_details"]


def test_partial_plan_names_required_need_the_catalog_cannot_verify() -> None:
    content = {
        "mission": {"goal": "Chuẩn bị chuyến chuyển nhà"},
        "needs": [{"need_id": "moving_service", "label": "dịch vụ chuyển nhà"}],
        "missing_required_needs": ["moving_service"],
        "options": [
            {
                "items": [{"name": "Nước suối", "quantity": 1, "line_total_vnd": 8_000}],
                "total_vnd": 8_000,
            }
        ],
        "inventory_verified": False,
        "cart_mutated": False,
    }

    for language in ("vi", "en", "zh"):
        summary = render_shopping_plan_summary(content, language=language)
        assert "dịch vụ chuyển nhà" in summary
        assert "moving_service" not in summary
    assert "nhu cầu bắt buộc" in render_shopping_plan_summary(content).casefold()
    assert "required need" in render_shopping_plan_summary(content, language="en").casefold()
    assert "必需需求" in render_shopping_plan_summary(content, language="zh")


def test_partial_plan_keeps_found_items_when_another_required_group_has_no_match() -> None:
    result = _plan(
        [
            _need("drinks", "drink", label="Nước uống"),
            _need(
                "face_moisturizer",
                "unlisted catalog category",
                label="Kem dưỡng ẩm mặt",
                product_query="kem dưỡng ẩm mặt",
            ),
        ],
        total_budget_vnd=100_000,
    )

    assert result.content["missing_required_needs"] == ["Kem dưỡng ẩm mặt"]
    assert result.content["options"]
    option = result.content["options"][0]
    assert [item["need_id"] for item in option["items"]] == ["drinks"]
    assert option["total_vnd"] == sum(item["line_total_vnd"] for item in option["items"])
    summary = render_shopping_plan_summary(result.content)
    assert "Nước" in summary
    assert "Kem dưỡng ẩm mặt" in summary
    assert "tổng trên chỉ tính các món đã tìm thấy" in summary.casefold()


def test_baby_wipes_wording_uses_catalog_child_wipes_not_broad_baby_care() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    result = build_shopping_mission_plan(
        {
            "goal": "Đồ dùng cho em bé đi cùng gia đình",
            "needs": [
                _need(
                    "baby_wipes",
                    "Phi thực phẩm > Hoá mỹ phẩm > Mỹ phẩm, chăm sóc cá nhân > "
                    "Đồ dùng chăm sóc mẹ và bé",
                    label="Khăn ướt cho bé",
                    product_query="khăn ướt trẻ em",
                )
            ],
        },
        repository,
    )

    need = result.content["needs"][0]
    assert need["resolved_category"] == "Giấy ướt trẻ em"
    assert need["candidate_count"] > 0
    assert any("trẻ em" in product.name.casefold() for product in result.products)


def test_dry_tissue_for_baby_does_not_resolve_to_wet_wipes() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    result = build_shopping_mission_plan(
        {
            "goal": "Đồ dùng mang theo khi đi cùng em bé",
            "needs": [
                _need(
                    "paper_tissues",
                    "Phi thực phẩm > Hoá mỹ phẩm > Giấy và bông > "
                    "Giấy ăn, giấy vệ sinh > Giấy ướt trẻ em",
                    label="Khăn giấy cho bé",
                    product_query="khăn giấy cho bé",
                )
            ],
        },
        repository,
    )

    need = result.content["needs"][0]
    assert need["resolved_category"] == "Khăn giấy gói"
    assert need["candidate_count"] > 0
    # Dry tissues can be named "giấy lụa rút" or "khăn rút" in the catalog.
    assert all(
        (product.category_hierarchy or "").split(">")[-1].strip() == "Khăn giấy gói"
        and "ướt" not in product.name.casefold()
        for product in result.products
    )


def test_existing_items_are_not_recommended_again() -> None:
    result = _plan(
        [
            _need("snacks", "snack", satisfied_by_existing=True),
            _need("drinks", "drink"),
        ],
        existing_items=["I already have enough snacks"],
    )

    assert result.content["satisfied_needs"] == ["snacks"]
    assert result.content["missing_required_needs"] == []
    assert [item["need_id"] for item in result.content["options"][0]["items"]] == ["drinks"]
    assert "bạn đã có rồi" in render_shopping_plan_summary(result.content).casefold()


def test_all_needs_already_satisfied_does_not_claim_a_catalog_failure() -> None:
    result = _plan([_need("snacks", "snack", satisfied_by_existing=True)])

    assert result.content["options"] == []
    assert result.content["missing_required_needs"] == []
    assert "chưa cần mua thêm" in render_shopping_plan_summary(result.content).casefold()


def test_revision_preserves_unchanged_proposed_sku_when_it_still_fits() -> None:
    previous = {
        "mission_id": "mission-one",
        "revision": 1,
        "goal": "Prepare a small gathering",
        "needs": [_need("drinks", "drink")],
        "proposed_items": [{"need_id": "drinks", "product_id": "COFFEE01"}],
    }
    revised = build_shopping_mission_plan(
        {
            "goal": "Prepare a small gathering",
            "needs": [_need("snacks", "snack")],
            "total_budget_vnd": 40_000,
        },
        DemoProductRepository(),
        previous,
    )

    items = {item["need_id"]: item for item in revised.content["options"][0]["items"]}
    assert items["drinks"]["product_id"] == "COFFEE01"
    assert items["snacks"]["product_id"] == "SNACK01"
    assert revised.content["options"][0]["total_vnd"] == 34_000
    assert revised.content["revision"] == 2
    assert revised.content["mission_id"] == "mission-one"


def test_revision_merges_a_paraphrased_duplicate_need_and_keeps_its_stable_id() -> None:
    result = build_shopping_mission_plan(
        {
            "needs": [
                _need(
                    "snack-rephrased",
                    "snack",
                    label="Snack ăn nhẹ chia nhau",
                    product_query="snack ăn nhẹ",
                )
            ]
        },
        DemoProductRepository(),
        {
            "mission_id": "mission-one",
            "revision": 1,
            "goal": "Prepare a gathering",
            "needs": [
                _need(
                    "snack",
                    "snack",
                    label="Snack ăn nhẹ",
                    product_query="snack",
                )
            ],
        },
    )

    assert len(result.mission_state["needs"]) == 1
    assert result.mission_state["needs"][0]["need_id"] == "snack"
    assert len(result.content["options"][0]["items"]) == 1


def test_soft_spicy_preference_uses_catalog_candidates_but_discloses_unknown_attribute() -> None:
    repository = DemoProductRepository()
    repository.supports_spicy_filter = False
    result = build_shopping_mission_plan(
        {
            "goal": "Prepare a small snack",
            "needs": [
                _need(
                    "snack",
                    "snack",
                    label="Snack",
                    preference_terms=["không cay"],
                )
            ],
        },
        repository,
    )

    assert result.content["options"]
    assert "preference_unverified:spicy" in result.content["unresolved_details"]
    assert (
        "chưa xác minh được món không cay"
        in render_shopping_plan_summary(result.content).casefold()
    )


def test_catalog_retries_the_category_leaf_when_the_llm_query_is_too_specific() -> None:
    result = _plan(
        [
            _need(
                "snacks",
                "snack",
                label="Snack ăn vặt",
                product_query="snack ăn vặt",
            )
        ]
    )

    assert result.content["options"]
    assert result.content["needs"][0]["candidate_count"] > 0


def test_catalog_category_fallback_does_not_replace_a_specific_product_query() -> None:
    result = _plan(
        [
            _need(
                "dip",
                "snack",
                label="Snack sốt mayonnaise",
                product_query="sốt mayonnaise",
            )
        ]
    )

    assert result.content["needs"][0]["candidate_count"] == 0
    assert result.content["missing_required_needs"] == ["Snack sốt mayonnaise"]
    assert result.content["options"] == []


def test_scene_descriptions_use_category_candidates_without_query_title_overlap() -> None:
    result = _plan(
        [
            _need(
                "snacks",
                "snack",
                label="Món lai rai chia nhau",
                rationale="Đồ ăn nhẹ cho cả nhóm xem bóng, dễ chia sẻ.",
                product_query="món lai rai ăn nhẹ chia nhau",
                preference_terms=["lai rai", "chia nhau"],
            )
        ],
        goal="Cả nhà xem bóng cùng người thân",
        party_size=5,
    )

    assert result.content["needs"][0]["candidate_count"] > 0
    assert result.content["options"]


def test_napkin_need_resolves_to_catalog_paper_category_not_fabric_towels() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    result = build_shopping_mission_plan(
        {
            "goal": "Đồ ăn uống cho cả nhóm xem bóng",
            "party_size": 5,
            "total_budget_vnd": 280_000,
            "needs": [
                _need(
                    "napkins",
                    "Phi thực phẩm > Dệt may > Bông vải sợi, giày dép > Khăn",
                    label="Khăn lau miệng",
                    rationale="Khăn để lau miệng khi ăn uống cùng nhóm.",
                    product_query="khăn lau miệng",
                )
            ],
        },
        repository,
    )

    need = result.content["needs"][0]
    assert need["resolved_category"] == "Khăn giấy gói"
    assert need["candidate_count"] > 0
    assert all(item.category_hierarchy.endswith("> Khăn giấy gói") for item in result.products)


def test_full_catalog_hierarchy_resolves_to_its_leaf_category() -> None:
    repository = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
    )
    hierarchies = repository.list_category_hierarchies()

    for leaf in ("Vệ sinh nhà", "Chăm sóc da"):
        matching_path = next(
            (path, index)
            for path in hierarchies
            for index, label in enumerate(path)
            if normalize_category_text(label) == normalize_category_text(leaf)
        )
        path, leaf_index = matching_path
        category_path = path[: leaf_index + 1]
        result = build_shopping_mission_plan(
            {
                "goal": f"Mua đồ thuộc nhóm {leaf}",
                "needs": [_need("need", " > ".join(category_path))],
            },
            repository,
        )

        need = result.content["needs"][0]
        assert need["resolved_category"] == leaf
        assert need["candidate_count"] > 0


def test_rejected_product_is_excluded_on_alternative_request() -> None:
    result = _plan(
        [_need("drinks", "drink")],
        rejected_product_ids=["WATER01"],
    )

    assert all(
        item["product_id"] != "WATER01"
        for option in result.content["options"]
        for item in option["items"]
    )


def test_category_alternatives_keep_brand_price_spice_and_stock_constraints() -> None:
    result = _plan(
        [
            _need(
                "meal",
                "meal",
                source="INFERRED",
                required=False,
                max_price_vnd=7_000,
                spicy=False,
                alternative_categories=["drink"],
            )
        ]
    )

    alternative = result.content["needs"][0]["alternative_categories"][0]
    assert [item["product_id"] for item in alternative["products"]] == ["WATER01"]
    assert alternative["products"][0]["price_vnd"] <= 7_000


def test_unknown_details_persist_until_cleared_and_stale_unresolved_details_reset() -> None:
    previous = {
        "mission_id": "mission-one",
        "revision": 1,
        "goal": "Prepare a gathering",
        "needs": [_need("snacks", "snack")],
        "unknown_details": ["Does the customer prefer sweet or savory?"],
        "unresolved_details": ["old catalog error"],
    }
    revised = build_shopping_mission_plan(
        {"goal": "", "needs": [], "clear_pending_decision": True},
        DemoProductRepository(),
        previous,
    )
    cleared = build_shopping_mission_plan(
        {"goal": "", "needs": [], "clear_unknown_details": True},
        DemoProductRepository(),
        previous,
    )

    assert revised.mission_state["unknown_details"] == ["Does the customer prefer sweet or savory?"]
    assert revised.mission_state["unresolved_details"] == []
    assert cleared.mission_state["unknown_details"] == []


def test_already_unverified_custom_requirement_is_cleared_after_revision() -> None:
    initial = _plan(
        [_need("snacks", "snack")],
        hard_requirements=["unsupported constraint"],
    )
    revised = build_shopping_mission_plan(
        {
            "goal": "Help the customer shop",
            "needs": [],
            "clear_hard_requirements": True,
        },
        DemoProductRepository(),
        initial.mission_state,
    )

    assert revised.content["options"]
    assert revised.content["unresolved_details"] == []
