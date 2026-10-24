"""Freeze a fresh, paraphrased needs-driven shopping mission evaluation set."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

from evaluation.generate_shopping_mission_heldout_v2 import ARCHETYPES
from evaluation.generate_shopping_mission_heldout_v3 import (
    BUDGETS,
    FACT_PRODUCT_NAME,
    PARTY_SIZES,
    _validate_product_free_cases,
)
from evaluation.generate_shopping_mission_heldout_v3 import (
    build_case as build_reference_case,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v4_20261021.json"

GOALS: dict[str, list[str]] = {
    "movie_evening": [
        "mấy người bạn hẹn xem phim ở nhà",
        "cả nhà muốn xem bóng cùng người thân",
        "mời bạn bè sang xem phim buổi tối",
        "gia đình tụ họp xem ở nhà vào cuối tuần",
    ],
    "long_road_trip": [
        "sắp có chuyến đi đường dài về quê",
        "cả nhà chuẩn bị đi đường dài bằng ô tô",
        "chuyến đi xa sẽ kéo dài vài tiếng",
        "tụi mình đi đường dài rồi ghé thăm người thân",
    ],
    "dry_skin_routine": [
        "da mặt khô và mình muốn tìm hiểu cách chăm sóc da",
        "cần chăm sóc da mặt cơ bản mỗi ngày",
        "da khô hơn khi thời tiết đổi mùa",
        "mình muốn có thói quen chăm sóc da buổi tối",
    ],
    "weekend_house_cleaning": [
        "cuối tuần này cần dọn nhà sau khi sửa bếp",
        "muốn vệ sinh nhà trước khi người thân ghé",
        "cần làm sạch nhà sau một tuần bận rộn",
        "căn hộ đang bừa bộn và cần dọn nhà theo từng khu vực",
    ],
    "day_out_with_baby": [
        "cuối tuần đưa bé ra ngoài chơi cả buổi",
        "em bé đi cùng gia đình trong chuyến thăm ông bà",
        "cả nhà đưa bé đi chơi xa trong ngày",
        "đi cùng bé tới buổi gặp mặt ngoài trời",
    ],
    "school_day_prep": [
        "chuẩn bị vật dụng cho ngày học kéo dài tới chiều",
        "học sinh có hoạt động sau giờ học",
        "ngày học đầu tiên ở trường mới sắp tới",
        "con đi học cả ngày rồi tham gia câu lạc bộ",
    ],
    "quick_family_breakfast": [
        "cả nhà cần bữa sáng nhanh trước giờ làm",
        "ăn sáng gọn trước khi mọi người đi làm",
        "chuẩn bị bữa sáng cho buổi họp bắt đầu sớm",
        "cần ăn sáng trước giờ làm mà không mất nhiều thời gian",
    ],
    "team_shift_break": [
        "mọi người cần giải lao giữa ca làm",
        "nhóm trực ca tối có thời gian nghỉ ngắn",
        "đội làm việc muốn nghỉ giữa ca cùng nhau",
        "cả nhóm giải lao sau buổi họp kéo dài",
    ],
    "new_apartment_setup": [
        "mình vừa chuyển vào căn hộ mới",
        "nhà mới cần sắp xếp trước ngày dọn tới",
        "đang chuyển nhà và cần chuẩn bị vật dụng cơ bản",
        "căn hộ mới thuê còn trống nhiều đồ dùng",
    ],
    "afternoon_park_picnic": [
        "gia đình đi dã ngoại ngoài trời ở công viên",
        "nhóm bạn định ngồi chơi ngoài trời buổi chiều",
        "cả nhà mang đồ ra công viên nghỉ cuối tuần",
        "mấy đứa nhỏ chơi ngoài trời rồi cả nhà ăn nhẹ",
    ],
}

NEED_SYNONYMS: dict[str, tuple[str, ...]] = {
    "đồ uống nóng": ("thức uống nóng", "đồ uống nóng", "nước uống nóng"),
    "đồ ăn nhẹ": ("món nhâm nhi", "đồ ăn vặt", "món lai rai", "đồ ăn nhẹ"),
    "đồ uống": ("thức uống", "nước uống", "đồ uống"),
    "giấy dùng trong nhà": ("giấy dùng trong nhà", "giấy lau dọn trong nhà"),
    "giấy dùng khi đi cùng bé": ("giấy mang theo cho bé", "giấy dùng khi đi cùng bé"),
    "giấy dùng ngoài trời": ("khăn giấy dùng ngoài trời", "giấy dùng ngoài trời"),
    "giấy ăn": ("khăn giấy", "giấy ăn", "khăn lau miệng"),
    "khăn giấy": ("giấy lau tay", "khăn giấy", "giấy ăn"),
    "đồ chăm sóc bé": ("đồ dùng chăm sóc em bé", "vật dụng chăm sóc bé", "đồ chăm sóc bé"),
    "khăn ướt trẻ em": ("khăn ướt cho bé", "khăn ướt trẻ em", "giấy ướt cho trẻ nhỏ"),
    "sữa rửa mặt": ("sản phẩm rửa mặt", "sữa rửa mặt", "đồ làm sạch da mặt"),
    "kem dưỡng da mặt": ("kem dưỡng ẩm cho mặt", "kem dưỡng da mặt", "sản phẩm dưỡng da mặt"),
    "bông tẩy trang": ("miếng bông tẩy trang", "bông tẩy trang", "bông dùng tẩy trang"),
    "đồ lau dọn": ("vật dụng vệ sinh nhà cửa", "đồ lau dọn", "đồ làm sạch nhà"),
    "đồ giặt giũ": ("đồ giặt quần áo", "chất giặt xả", "đồ giặt giũ"),
    "đồ dùng học sinh": ("vật dụng đi học", "đồ dùng học sinh", "đồ học tập"),
    "dụng cụ nhà bếp cơ bản": ("đồ dùng cơ bản cho bếp", "dụng cụ nhà bếp cơ bản"),
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn")


def _money(value: int) -> str:
    return f"{value:,}".replace(",", ".") + "đ"


def _needs_text(archetype: dict[str, Any], index: int) -> str:
    text = str(archetype["needs_text"])
    for offset, (phrase, options) in enumerate(
        sorted(NEED_SYNONYMS.items(), key=lambda pair: len(pair[0]), reverse=True)
    ):
        if phrase in text:
            text = text.replace(phrase, options[(index + offset) % len(options)])
    return text


def _third_turn(
    archetype: dict[str, Any], variant: int, budget: int, people: int, needs_text: str
) -> str:
    if variant == 0:
        return (
            f"Mình cần hạ khoản chuẩn bị xuống {_money(max(150_000, budget - 50_000))}; "
            "giữ số người và các phần đã xác nhận, thiếu gì thì nói rõ."
        )
    if variant == 1:
        return f"Có thêm hai người, thành {people + 2} người rồi; tính lại số lượng và giữ các nhóm hàng cũ nhé."
    if variant == 2:
        return (
            f"Thêm {archetype['extra_phrase']} vào nữa; các phần trước vẫn cần. "
            f"Mức chi có thể tăng lên {_money(budget + 20_000)}."
        )
    if variant == 3:
        return (
            f"Đổi phần {archetype['replace_from']} sang {archetype['replace_to']}; "
            "bỏ nhu cầu cũ đó, các nhóm khác cứ giữ nguyên."
        )
    if variant == 4:
        return (
            f"Phần {archetype['existing_phrase']} nhà mình có sẵn rồi, bỏ khỏi đồ cần mua; "
            "những phần khác vẫn để lại."
        )
    if variant == 5:
        return f"Mình thích kiểu này hơn: {archetype['preference']}; chưa có dữ liệu thì cứ ghi rõ."
    if variant == 6:
        return f"Mình cần điều kiện này là bắt buộc: {archetype['unknown_claim']}; catalog không xác nhận thì đừng khẳng định."
    if variant == 7:
        return "Món đầu chưa hợp gu rồi, kiếm lựa chọn khác cùng nhóm giùm tui nghen; đừng lặp món đó."
    if variant == 8:
        return (
            f"Tra giúp mình giá {FACT_PRODUCT_NAME} thôi nhé, đừng sửa những phần trong phương án đang làm."
        )
    return (
        f"À mình chuẩn bị cho {GOALS[archetype['slug']][(variant + 1) % len(GOALS[archetype['slug']])]}. "
        "Mình vẫn chưa biết cần mua nhóm nào, tư vấn theo mục đích đó giúp nhé."
    )


def _fourth_turn(archetype: dict[str, Any], variant: int, budget: int, people: int) -> str:
    if variant == 0:
        return "Theo mức chi mới, đưa phương án ít tốn nhất nhưng vẫn đủ các phần mình đã yêu cầu."
    if variant == 1:
        return f"Chốt số lượng theo {people + 2} người; ngân sách vừa cập nhật giữ nguyên nhé."
    if variant == 2:
        return "Nhớ tính cả phần mới thêm vào phương án và cộng lại tổng tiền giúp mình."
    if variant == 3:
        return "Xem lại danh sách: phần cũ đã bỏ, phần thay thế cùng những nhu cầu khác vẫn còn đủ chứ?"
    if variant == 4:
        return "Chỉ đưa vào gợi ý những gì còn thiếu; đừng tính lại phần mình đã chuẩn bị."
    if variant == 5:
        return f"Mình vẫn ưu tiên như này: {archetype['preference']}; thiếu dữ liệu thì đừng tự khẳng định."
    if variant == 6:
        return "Bỏ điều kiện không kiểm chứng được đó đi rồi tiếp tục các nhu cầu ban đầu."
    if variant == 7:
        return "Món đổi cũng chưa vừa ý, kiếm món khác đúng nhóm nữa nha; đừng gợi lại món tui chê nghen."
    if variant == 8:
        return (
            f"Cam on, tiep tuc phuong an ban dau nhung ha muc chi xuong "
            f"{_money(max(150_000, budget - 30_000))}; giu cac nhu cau con lai."
        )
    return "Mình chỉ xem để tham khảo thôi; cho xem lựa chọn nhưng tuyệt đối chưa thêm gì vào giỏ."


def build_case(archetype: dict[str, Any], variant: int, index: int) -> dict[str, Any]:
    """Reuse the reviewed state oracle while giving every conversation fresh wording."""
    case = build_reference_case(archetype, variant, index)
    budget = BUDGETS[(index * 3 + variant) % len(BUDGETS)]
    people = PARTY_SIZES[(index * 3 + variant) % len(PARTY_SIZES)]
    goals = GOALS[archetype["slug"]]
    goal = goals[(index + variant * 3) % len(goals)]
    needs_text = _needs_text(archetype, index + variant)

    if variant == 9:
        first = (
            f"Mình có việc cần chuẩn bị cho khoảng {people} người, dự trù {_money(budget)}, "
            "nhưng chưa nói rõ mục đích. Hỏi mình một câu trước khi gợi ý nhé."
        )
        second = (
            f"Mình chuẩn bị vì {goal}. Còn các nhóm cần mua thì chưa rõ, "
            "bạn tư vấn theo nhu cầu của việc này giúp mình nhé."
        )
        third = (
            f"Mình xác nhận là cần có {needs_text}; thứ gì bạn gợi ý thêm thì cứ để tùy chọn."
        )
        fourth = "Cho mình xem lại phương án để cân nhắc, chưa quyết định mua và đừng đụng tới giỏ."
    else:
        first = (
            f"Tư vấn giùm mình: {goal}. Có khoảng {people} người, tiền dự trù {_money(budget)}. "
            "Mình chưa biết cần những nhóm gì cho việc đó; cứ tham khảo theo nhu cầu, "
            "chưa cần thêm gì vào giỏ."
        )
        second = (
            f"Mình xác nhận cần có {needs_text}. Những nhóm này là bắt buộc; "
            "thứ gì bạn gợi ý thêm thì cứ để tùy chọn."
        )
        third = _third_turn(archetype, variant, budget, people, needs_text)
        if variant == 9:
            # The ninth revision variant tests clarification before the goal is known.
            first = (
                f"Có việc cần chuẩn bị cho {people} người, khoảng {_money(budget)}, "
                "nhưng mình chưa nói dịp gì. Hỏi mình một câu để hiểu mục đích trước nhé."
            )
            second = (
                f"À, là vì {goal}. Mình chưa biết phải mua những nhóm nào; "
                "bạn tư vấn theo nhu cầu của việc này giúp."
            )
            third = (
                f"Mình xác nhận cần có {needs_text}; món nào gợi ý thêm thì chỉ là tùy chọn."
            )
            fourth = "Mình chỉ đang xem thôi, đưa phương án để tự cân nhắc và đừng thêm hàng vào giỏ."
        else:
            after_third_budget = (
                max(150_000, budget - 50_000)
                if variant == 0
                else budget + 30_000
                if variant == 1
                else budget + 20_000
                if variant == 2
                else budget
            )
            after_third_people = people + 2 if variant == 1 else people
            fourth = _fourth_turn(archetype, variant, after_third_budget, after_third_people)

    texts = [first, second, third, fourth]
    for turn, text in zip(case["turns"], texts, strict=True):
        turn["text"] = text
    case["case_id"] = f"SMV4-{index:03d}"
    case["tags"] = ["heldout_v4" if tag == "heldout_v3" else tag for tag in case["tags"]]
    case["case_description"] = (
        f"V4 frozen paraphrase holdout; archetype={archetype['slug']}; revision={variant}."
    )
    return case


def _all_existing_turn_texts() -> set[str]:
    texts: set[str] = set()
    for version in ("v1", "v2", "v3"):
        path = ROOT / f"evaluation/cases/vietnamese_shopping_mission_heldout_{version}_20261021.json"
        if not path.exists():
            continue
        dataset = json.loads(path.read_text(encoding="utf-8"))
        texts.update(
            " ".join(turn["text"].casefold().split())
            for case in dataset.get("cases", [])
            for turn in case.get("turns", [])
        )
    return texts


def main() -> None:
    cases = [
        build_case(archetype, variant, archetype_index * 10 + variant + 1)
        for archetype_index, archetype in enumerate(ARCHETYPES)
        for variant in range(10)
    ]
    _validate_product_free_cases(cases)
    previous_texts = _all_existing_turn_texts()
    exact_overlap = [
        turn["text"]
        for case in cases
        for turn in case["turns"]
        if " ".join(turn["text"].casefold().split()) in previous_texts
    ]
    if exact_overlap:
        raise ValueError(f"V4 contains {len(exact_overlap)} exact turns from a previous holdout")

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
        "exact_turn_overlap_with_v1_v3": 0,
    }
    dataset = {
        "dataset_id": "vietnamese_shopping_mission_heldout_v4_20261021",
        "language_scope": "Vietnamese only",
        "split": "heldout_frozen",
        "created_at": "2026-10-21",
        "description": (
            "A fresh 100-conversation, four-turn Vietnamese shopping mission holdout. "
            "It uses paraphrased goals, needs, corrections, and optionality instructions; "
            "90 conversations avoid catalog product names, while 10 include one grounded "
            "price question during a mission. Frozen before live runs."
        ),
        "catalog_scope": "Runtime WinMart JSON snapshot; stock is not live branch inventory.",
        "derived_oracle": "Expected state/coverage checks share the reviewed v3 evaluation contract.",
        "coverage_target": {"scenario_count": 100, "turns_per_scenario": "3-8", **dimensions},
        "dimensions": dimensions,
        "case_id_prefix": "SMV4",
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(
        json.dumps(
            {"path": str(OUTPUT), "sha256": digest, "dimensions": dimensions},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
