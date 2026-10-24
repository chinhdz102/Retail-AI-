"""Freeze a fresh needs-driven shopping evaluation set before its live run."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

from evaluation.generate_shopping_mission_heldout_v2 import ARCHETYPES

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v3_20261021.json"
CATALOG = ROOT / "data/winmart_products.json"

BUDGETS = [180_000, 220_000, 250_000, 280_000, 320_000, 350_000, 380_000, 420_000, 450_000, 500_000]
PARTY_SIZES = [2, 3, 4, 5, 6, 7, 8, 10, 12, 15]
FACT_PRODUCT_ID = "WIN_10307381"
FACT_PRODUCT_NAME = "Coca-Cola Zero chai nhựa 1,5l"
FACT_PRICE_VND = 15_900

GOALS: dict[str, list[str]] = {
    "movie_evening": [
        "rủ người thân qua nhà xem phim sau bữa tối",
        "cả nhóm hẹn nhau xem trận bóng tại phòng khách",
        "nhà có bạn ghé ngồi xem chương trình buổi tối",
        "mấy người bạn tụ họp để xem phim cuối tuần",
        "gia đình muốn ngồi trò chuyện sau một buổi chiếu phim",
    ],
    "long_road_trip": [
        "cả nhà ngồi xe nhiều giờ để về thăm quê",
        "nhóm bạn đi xe khách tới một tỉnh gần đó",
        "gia đình chuẩn bị ngày đi chơi xa bằng ô tô",
        "đi tàu qua tỉnh để thăm người thân trong ngày",
        "nhóm mình đi cắm trại và ở ngoài trời từ sáng",
    ],
    "dry_skin_routine": [
        "da mặt thường căng khô sau khi rửa",
        "mình muốn bắt đầu thói quen chăm sóc da mỗi ngày",
        "da bị khô hơn khi thời tiết chuyển mùa",
        "đang tìm hiểu quy trình chăm sóc da buổi tối",
        "da mặt dễ khô sau một ngày đi ngoài trời",
    ],
    "weekend_house_cleaning": [
        "căn bếp cần được dọn trước khi gia đình ghé chơi",
        "nhà mình muốn tổng vệ sinh vào ngày nghỉ",
        "phòng tắm và sàn nhà cần được làm sạch cuối tuần",
        "vừa sửa sang xong nên cần dọn lại căn phòng",
        "mình muốn sắp xếp việc giặt giũ và lau dọn trong nhà",
    ],
    "day_out_with_baby": [
        "cả nhà đưa em bé ra công viên chơi một buổi",
        "cuối tuần này đi thăm người thân cùng em bé",
        "mình đưa bé đi chơi xa trong ngày bằng ô tô",
        "cả nhà có buổi dạo chơi ngoài trời cùng con nhỏ",
        "em bé sẽ theo gia đình tới buổi gặp mặt cuối tuần",
    ],
    "school_day_prep": [
        "con ở trường từ sáng tới chiều và có giờ ngoại khóa",
        "bé chuẩn bị ngày đầu tiên ở ngôi trường mới",
        "con có buổi học nhóm sau giờ tan trường",
        "mình sắp xếp đồ cho ngày học thêm kéo dài tới tối",
        "các bạn nhỏ có buổi sinh hoạt ngoại khóa cả ngày",
    ],
    "quick_family_breakfast": [
        "cả nhà cần ăn sáng gọn trước giờ đi làm",
        "mọi người sẽ ăn lót dạ trước chuyến đi sớm",
        "gia đình muốn chuẩn bị bữa sáng nhanh tại nhà",
        "nhóm mình có buổi họp sớm và cần ăn trước đó",
        "các thành viên trong nhà rời đi vào những giờ khác nhau",
    ],
    "team_shift_break": [
        "nhóm trực ca tối có giờ nghỉ ngắn cùng nhau",
        "mọi người ở văn phòng muốn nghỉ giữa buổi họp",
        "đội làm việc ngoài hiện trường cần nghỉ chân buổi trưa",
        "nhóm dự án gặp nhau sau giờ làm",
        "nhân viên cửa hàng có giờ giải lao giữa ca",
    ],
    "new_apartment_setup": [
        "mình vừa chuyển vào căn hộ thuê mới",
        "phòng trọ mới cần sắp xếp trước ngày dọn tới",
        "gia đình vừa chuyển đồ tới nơi ở mới",
        "căn nhà mới thuê cần được làm sạch để sinh hoạt",
        "mình muốn chuẩn bị vật dụng cơ bản cho chỗ ở mới",
    ],
    "afternoon_park_picnic": [
        "gia đình dự định ngồi chơi ở công viên vào buổi chiều",
        "nhóm bạn đi dạo rồi nghỉ ngoài trời gần bờ hồ",
        "cả nhà có buổi dã ngoại ở bãi cỏ cuối tuần",
        "các bạn nhỏ ra sân chơi sau giờ học",
        "người thân muốn gặp nhau ngoài trời vào ngày nghỉ",
    ],
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _budget(value: int) -> str:
    return f"{value:,}".replace(",", ".") + "đ"


def _groups_for(archetype: dict[str, Any], variant: int) -> list[list[str]]:
    groups = [list(group) for group in archetype["groups"]]
    if variant == 2:
        groups.append(list(archetype["extra_group"]))
    elif variant == 3:
        groups[archetype["replace_index"]] = list(archetype["replace_group"])
    elif variant == 4:
        groups.pop(archetype["existing_index"])
    return groups


def _turn3(
    archetype: dict[str, Any], variant: int, budget: int, people: int
) -> tuple[str, int, int, list[list[str]], dict[str, Any], list[str]]:
    groups = [list(group) for group in archetype["groups"]]
    next_budget = budget
    next_people = people
    expected: dict[str, Any] = {}
    tags: list[str] = []

    if variant == 0:
        next_budget = max(150_000, budget - 50_000)
        text = f"Mình phải hạ mức chi còn {_budget(next_budget)}; giữ nguyên số người và những phần đã chốt, báo nếu không đủ."
        tags = ["budget_revision", "minimum_viable_plan"]
    elif variant == 1:
        next_people = people + 2
        next_budget = budget + 30_000
        text = f"Có thêm hai người tham gia, giờ thành {next_people} người; cập nhật phần chuẩn bị và giữ các nhóm đã chọn nhé."
        tags = ["party_size_revision", "quantity_adjustment"]
    elif variant == 2:
        next_budget = budget + 20_000
        groups.append(list(archetype["extra_group"]))
        text = f"Bổ sung {archetype['extra_phrase']}; các nhóm trước vẫn cần. Có thể nâng khoản dự trù lên {_budget(next_budget)}."
        tags = ["category_addition", "state_preservation"]
    elif variant == 3:
        groups[archetype["replace_index"]] = list(archetype["replace_group"])
        text = f"Thay phần {archetype['replace_from']} bằng {archetype['replace_to']}; bỏ nhóm cũ, những nhóm khác giữ nguyên."
        tags = ["category_substitution", "remove_old_need"]
    elif variant == 4:
        groups.pop(archetype["existing_index"])
        text = f"Phần {archetype['existing_phrase']} nhà mình đã chuẩn bị đủ rồi, bỏ khỏi danh sách cần mua; các phần còn lại cứ giữ."
        expected["existing_items_any"] = [archetype["existing_phrase"]]
        tags = ["existing_items", "deduplication"]
    elif variant == 5:
        text = f"Mình {archetype['preference']}; đây là điều mình thích hơn thôi, nếu catalog không xác nhận được thì nói rõ."
        expected["response_term_groups_all"] = [["chưa xác minh", "không có dữ liệu", "chưa có thuộc tính", "không thể xác nhận"]]
        tags = ["soft_preference", "attribute_uncertainty"]
    elif variant == 6:
        text = f"Có điều kiện bắt buộc là {archetype['unknown_claim']}; nếu dữ liệu không chứng minh thì đừng khẳng định hay chọn bừa."
        expected["unverified_constraint_terms_any"] = [archetype["unknown_claim"]]
        expected["response_term_groups_all"] = [["chưa xác minh", "không có dữ liệu", "không thể xác nhận", "chưa đủ thông tin"]]
        tags = ["unsupported_hard_constraint", "abstention", "no_fabricated_claims"]
    elif variant == 7:
        text = "Món đầu trong gợi ý chưa hợp gu, kiếm giúp mình lựa chọn khác cùng nhóm nghen; đừng lặp món ấy, các nhóm còn lại giữ nguyên."
        expected["rejected_products_recorded"] = True
        tags = ["rejected_product", "alternative_selection", "regional_vietnamese"]
    elif variant == 8:
        text = f"Cho mình hỏi riêng {FACT_PRODUCT_NAME} giá bao nhiêu? Chỉ tra cứu giá thôi, đừng đổi kế hoạch đang lập."
        expected.update(
            {
                "mission_plan_required": False,
                "preserve_mission_state": True,
                "grounded_product_ids_any": [FACT_PRODUCT_ID],
                "fact_price_vnd": FACT_PRICE_VND,
                "response_terms_any": ["giá"],
                "minimum_plan_items": 0,
            }
        )
        tags = ["mixed_factual_question", "active_mission_state_preservation"]
    else:
        text = "Mình đang tham khảo thôi, chưa quyết định mua; đừng tự thêm bất kỳ món nào vào giỏ."
        expected["cart_unchanged"] = True
        tags = ["cart_write_safety", "explicit_no_purchase"]

    return text, next_budget, next_people, groups, expected, tags


def _turn4(
    archetype: dict[str, Any],
    variant: int,
    budget: int,
    people: int,
    groups: list[list[str]],
) -> tuple[str, int, int, dict[str, Any], list[str]]:
    expected: dict[str, Any] = {}
    next_budget = budget
    next_people = people

    if variant == 0:
        text = "Với mức mới đó, xem lại phương án tối thiểu giúp mình; nếu thiếu thì nêu cụ thể nhóm nào, đừng tự bỏ nhu cầu cần có."
        tags = ["budget_shortfall_explanation", "state_preservation"]
    elif variant == 1:
        text = f"Tính lại cho đủ {people} người nhé, phần ngân sách hiện tại giữ nguyên."
        tags = ["quantity_adjustment", "party_size_preservation"]
    elif variant == 2:
        text = "Nhớ đưa cả phần vừa bổ sung vào phương án chính và tính lại tổng tiền nhé."
        tags = ["category_addition_persistence", "budget_recalculation"]
    elif variant == 3:
        text = "Kiểm tra lại giúp mình: nhóm đã thay không còn trong danh sách, những nhu cầu khác vẫn đủ."
        tags = ["substitution_persistence", "state_preservation"]
    elif variant == 4:
        text = "Đưa phương án cho những phần còn thiếu thôi; đừng gợi ý mua lại phần mình đã có."
        expected["existing_items_any"] = [archetype["existing_phrase"]]
        tags = ["existing_items_persistence", "no_duplicate_recommendation"]
    elif variant == 5:
        text = f"Mình vẫn theo tiêu chí này: {archetype['preference']}; nếu catalog chưa có thông tin thì ghi rõ thay vì đoán."
        expected["response_term_groups_all"] = [["chưa xác minh", "không có dữ liệu", "chưa có thuộc tính", "không thể xác nhận"]]
        tags = ["soft_preference_persistence", "no_fabricated_attribute"]
    elif variant == 6:
        text = "Vậy bỏ điều kiện chưa kiểm chứng đó đi, tiếp tục với nhu cầu và mức chi ban đầu."
        expected["unverified_constraint_terms_any"] = []
        expected["response_term_groups_all"] = []
        tags = ["constraint_relaxation", "mission_recovery"]
    elif variant == 7:
        text = "Món vừa đổi vẫn chưa trúng ý; kiếm thêm món khác đúng nhóm giùm mình, đừng gợi lại mấy món tui đã chê nghen."
        expected["rejected_products_recorded"] = True
        tags = ["second_alternative", "rejected_item_safety"]
    elif variant == 8:
        next_budget = max(150_000, budget - 30_000)
        text = f"Cam on, tiep tuc phuong an cho cac nhom ban dau nhung ha muc chi xuong {_budget(next_budget)}. Giu cac nhu cau con lai."
        tags = ["resume_after_fact_question", "budget_revision", "asr_like_missing_diacritics"]
    else:
        text = "Cho mình xem phương án để tự cân nhắc; chỉ thay đổi giỏ khi mình yêu cầu rõ sau này."
        expected["cart_unchanged"] = True
        tags = ["advice_only", "cart_write_safety"]

    expected.update(
        {
            "goal_terms_any": archetype["goal_terms"],
            "need_category_groups": groups,
            "budget_vnd": next_budget,
            "party_size": next_people,
            "minimum_plan_items": 0 if variant == 6 else min(archetype["min_items"], len(groups)),
            "budget_compliant": True,
            "cart_unchanged": True,
            "mission_id_stable": True,
        }
    )
    if variant != 8:
        expected.setdefault("response_terms_any", ["phương án", "gợi ý", "kế hoạch", "chưa xác minh", "dữ liệu"])
    return text, next_budget, next_people, expected, tags


def build_case(archetype: dict[str, Any], variant: int, index: int) -> dict[str, Any]:
    budget = BUDGETS[(index * 3 + variant) % len(BUDGETS)]
    people = PARTY_SIZES[(index * 3 + variant) % len(PARTY_SIZES)]
    goal = GOALS[archetype["slug"]][(variant * 2 + index) % len(GOALS[archetype["slug"]])]
    first = (
        f"Mình cần tư vấn vì {goal}; dự tính {people} người và giới hạn chi {_budget(budget)}. "
        "Mình chưa rõ cần chuẩn bị những gì, tư vấn theo nhu cầu và gợi ý các nhóm giúp mình; "
        "chỉ tham khảo, đừng thao tác giỏ."
    )
    second = (
        f"Để đủ phần chính, mình cần {archetype['needs_text']}. "
        "Các nhóm này mình xác nhận là cần; thứ bạn tự gợi ý thêm thì để tùy chọn."
    )
    third, budget_after_third, people_after_third, groups_after_third, third_extra, third_tags = _turn3(
        archetype, variant, budget, people
    )
    fourth, final_budget, final_people, fourth_extra, fourth_tags = _turn4(
        archetype,
        variant,
        budget_after_third,
        people_after_third,
        groups_after_third,
    )

    initial_groups = [list(group) for group in archetype["groups"]]
    second_groups = [list(group) for group in archetype["groups"]]
    if variant == 5:
        second_extra = {
            "response_term_groups_all": [["chưa xác minh", "không có dữ liệu", "chưa có thuộc tính", "không thể xác nhận"]]
        }
    elif variant == 6:
        second_extra = {
            "unverified_constraint_terms_any": [archetype["unknown_claim"]],
            "response_term_groups_all": [["chưa xác minh", "không có dữ liệu", "không thể xác nhận", "chưa đủ thông tin"]],
        }
    else:
        second_extra = {}

    first_expect = {
        "goal_terms_any": archetype["goal_terms"],
        "need_category_groups": initial_groups,
        "budget_vnd": budget,
        "party_size": people,
        "minimum_plan_items": archetype["min_items"],
        "inferred_needs_optional": True,
        "budget_compliant": True,
        "cart_unchanged": True,
        "response_terms_any": ["phương án", "gợi ý", "kế hoạch", "dữ liệu"],
    }
    second_expect = {
        "goal_terms_any": archetype["goal_terms"],
        "need_category_groups": second_groups,
        "required_category_groups": second_groups,
        "budget_vnd": budget,
        "party_size": people,
        "minimum_plan_items": archetype["min_items"],
        "explicit_needs_required": True,
        "budget_compliant": True,
        "cart_unchanged": True,
        "response_terms_any": ["phương án", "gợi ý", "kế hoạch", "dữ liệu"],
        **second_extra,
    }

    third_expect = {
        "goal_terms_any": archetype["goal_terms"],
        "need_category_groups": groups_after_third,
        "budget_vnd": budget_after_third,
        "party_size": people_after_third,
        "minimum_plan_items": 0 if variant in {0, 6, 8} else min(archetype["min_items"], len(groups_after_third)),
        "budget_compliant": True,
        "cart_unchanged": True,
        "mission_id_stable": True,
        **third_extra,
    }
    if variant in {2, 3}:
        third_expect["required_category_groups"] = groups_after_third
        third_expect["explicit_needs_required"] = True
    if variant not in {8}:
        third_expect.setdefault("response_terms_any", ["phương án", "gợi ý", "kế hoạch", "chưa xác minh", "dữ liệu"])
    if variant == 6:
        third_expect["minimum_plan_items"] = 0

    fourth_expect = dict(fourth_extra)
    fourth_expect["required_category_groups"] = groups_after_third
    if variant in {2, 3}:
        fourth_expect["explicit_needs_required"] = True
    if variant == 6:
        fourth_expect["minimum_plan_items"] = min(archetype["min_items"], len(groups_after_third))
    if variant == 8:
        # The factual lookup must leave the prior mission intact, and this
        # final turn proves the customer can continue editing that same plan.
        fourth_expect["required_category_groups"] = initial_groups
        fourth_expect["mission_plan_required"] = True

    if variant == 9:
        first = (
            f"Mình có việc cần chuẩn bị nhưng chưa nói rõ là dịp nào; dự trù {_budget(budget)} "
            f"cho khoảng {people} người. Hỏi mình một câu về mục đích trước nhé, đừng tự đoán."
        )
        second = (
            f"À, mình chuẩn bị vì {goal}. Mình vẫn chưa biết cần những nhóm gì, "
            "bạn gợi ý theo nhu cầu giúp nhé."
        )
        third = (
            f"Để đủ phần chính, mình cần {archetype['needs_text']}. "
            "Các nhóm này mình xác nhận là cần; thứ bạn tự gợi ý thêm thì để tùy chọn."
        )
        fourth = "Mình vẫn chỉ tham khảo, chưa quyết định mua; cho xem phương án nhưng đừng tự thêm gì vào giỏ."
        first_expect = {
            "mission_plan_required": False,
            "clarification_terms_any": ["dịp nào", "mục đích", "hoàn cảnh", "chuẩn bị cho việc gì"],
            "no_grounded_products": True,
            "minimum_plan_items": 0,
            "cart_unchanged": True,
        }
        second_expect = {
            "goal_terms_any": archetype["goal_terms"],
            "need_category_groups": initial_groups,
            "budget_vnd": budget,
            "party_size": people,
            "minimum_plan_items": archetype["min_items"],
            "inferred_needs_optional": True,
            "budget_compliant": True,
            "cart_unchanged": True,
            "response_terms_any": ["phương án", "gợi ý", "kế hoạch", "dữ liệu"],
        }
        third_expect = {
            "goal_terms_any": archetype["goal_terms"],
            "need_category_groups": initial_groups,
            "required_category_groups": initial_groups,
            "budget_vnd": budget,
            "party_size": people,
            "minimum_plan_items": archetype["min_items"],
            "explicit_needs_required": True,
            "budget_compliant": True,
            "cart_unchanged": True,
            "mission_id_stable": True,
            "response_terms_any": ["phương án", "gợi ý", "kế hoạch", "dữ liệu"],
        }
        fourth_expect = {
            "goal_terms_any": archetype["goal_terms"],
            "need_category_groups": initial_groups,
            "required_category_groups": initial_groups,
            "budget_vnd": budget,
            "party_size": people,
            "minimum_plan_items": archetype["min_items"],
            "budget_compliant": True,
            "cart_unchanged": True,
            "mission_id_stable": True,
            "response_terms_any": ["phương án", "gợi ý", "kế hoạch", "dữ liệu"],
        }
        third_tags = ["incomplete_context", "goal_clarification", "explicit_needs"]
        fourth_tags = ["advice_only", "cart_write_safety"]

    tags = [
        "heldout_v3",
        "multi_turn",
        "needs_driven",
        archetype["slug"],
        *third_tags,
        *fourth_tags,
    ]
    if variant != 8:
        tags.append("no_product_names")
    turns = [
        {"text": first, "expect": first_expect},
        {"text": second, "expect": second_expect},
        {"text": third, "expect": third_expect},
        {"text": fourth, "expect": fourth_expect},
    ]
    return {
        "case_id": f"SMV3-{index:03d}",
        "archetype": archetype["slug"],
        "tags": list(dict.fromkeys(tags)),
        "turn_count": 4,
        "user_never_names_product": variant != 8,
        "case_description": f"V3 frozen mission evaluation; archetype={archetype['slug']}; revision={variant}.",
        "turns": turns,
    }


def _validate_product_free_cases(cases: list[dict[str, Any]]) -> None:
    names = [
        " ".join(str(row.get("name") or "").casefold().split())
        for row in json.loads(CATALOG.read_text(encoding="utf-8"))
    ]
    names = [name for name in names if len(name) >= 16]
    for case in cases:
        if not case["user_never_names_product"]:
            continue
        for turn in case["turns"]:
            text = " ".join(turn["text"].casefold().split())
            normalized_text = _normalize(text)
            for name in names:
                if name in text or _normalize(name) in normalized_text:
                    raise ValueError(f"Catalog product-name leakage in {case['case_id']}: {name}")


def main() -> None:
    cases = [
        build_case(archetype, variant, archetype_index * 10 + variant + 1)
        for archetype_index, archetype in enumerate(ARCHETYPES)
        for variant in range(10)
    ]
    _validate_product_free_cases(cases)
    dimensions = {
        "scenario_count": len(cases),
        "turn_count": sum(len(case["turns"]) for case in cases),
        "product_name_free_scenarios": sum(case["user_never_names_product"] for case in cases),
        "mixed_factual_lookups": sum("mixed_factual_question" in case["tags"] for case in cases),
        "budget_revisions": sum("budget_revision" in case["tags"] for case in cases),
        "party_or_quantity_changes": sum("party_size_revision" in case["tags"] for case in cases),
        "category_additions": sum("category_addition" in case["tags"] for case in cases),
        "category_substitutions": sum("category_substitution" in case["tags"] for case in cases),
        "existing_item_cases": sum("existing_items" in case["tags"] for case in cases),
        "soft_preference_cases": sum("soft_preference" in case["tags"] for case in cases),
        "unsupported_hard_constraints": sum("unsupported_hard_constraint" in case["tags"] for case in cases),
        "rejection_and_alternatives": sum("rejected_product" in case["tags"] for case in cases),
        "regional_vietnamese": sum("regional_vietnamese" in case["tags"] for case in cases),
        "asr_like_missing_diacritics": sum("asr_like_missing_diacritics" in case["tags"] for case in cases),
        "incomplete_or_ambiguous_goals": sum("incomplete_context" in case["tags"] for case in cases),
        "cart_safety_cases": sum("cart_write_safety" in case["tags"] for case in cases),
    }
    dataset = {
        "dataset_id": "vietnamese_shopping_mission_heldout_v3_20261021",
        "language_scope": "Vietnamese only",
        "split": "heldout_frozen",
        "created_at": "2026-10-21",
        "description": (
            "100 new four-turn Vietnamese shopping consultations. 90 conversations never "
            "name a product; 10 ask a grounded factual product-price question during an active "
            "mission and then continue editing that mission. Freeze before live model runs."
        ),
        "catalog_scope": "Current runtime WinMart JSON snapshot; stock is not live branch inventory.",
        "research_informed_by": [
            "https://arxiv.org/abs/2406.12045",
            "https://arxiv.org/abs/2408.04682",
            "https://aclanthology.org/2024.eacl-demo.16/",
        ],
        "coverage_target": {"scenario_count": 100, "turns_per_scenario": "3-8", **dimensions},
        "dimensions": dimensions,
        "case_id_prefix": "SMV3",
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(json.dumps({"path": str(OUTPUT), "sha256": digest, "dimensions": dimensions}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
