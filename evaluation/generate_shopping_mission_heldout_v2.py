"""Create a fresh, product-name-free heldout set for the retail shopping planner.

This v2 uses new scenario text and a wider set of everyday missions than v1.
Do not use individual v2 outputs to tune the implementation before both live
arms have finished; review the frozen file hash in the benchmark report.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v2_20261021.json"
CATALOG = ROOT / "data/winmart_products.json"
BUDGETS = [180_000, 220_000, 250_000, 280_000, 320_000, 350_000, 380_000, 420_000, 450_000, 500_000]
PARTY_SIZES = [2, 3, 4, 5, 6, 7, 8, 10, 12, 15]

ARCHETYPES: list[dict[str, Any]] = [
    {
        "slug": "movie_evening",
        "goal_terms": ["xem phim", "xem bóng", "xem ở nhà"],
        "goals": [
            "cả nhà xem phim ở phòng khách",
            "mấy người bạn ghé xem trận bóng",
            "hai gia đình ngồi trò chuyện tối cuối tuần",
            "nhóm bạn tụ họp trước buổi chiếu phim",
            "mọi người ở lại sau buổi sinh hoạt câu lạc bộ",
            "cả nhà xem chương trình cuối tuần",
            "nhóm bạn xem giải đấu tại nhà",
            "mấy anh chị đồng nghiệp ghé chơi buổi tối",
            "gia đình đón họ hàng tới trò chuyện",
            "nhóm nhỏ gặp nhau sau giờ làm",
        ],
        "needs_text": "đồ ăn nhẹ để chia nhau, đồ uống và giấy ăn",
        "groups": [
            ["snack", "đồ ăn nhẹ", "bánh kẹo"],
            ["đồ uống", "nước uống", "nước suối"],
        ],
        "min_items": 2,
        "extra_group": ["khăn giấy", "giấy ăn", "giấy và bông"],
        "extra_phrase": "giấy ăn để mọi người tiện dùng",
        "replace_index": 0,
        "replace_group": ["bánh", "bánh kẹo"],
        "replace_from": "đồ ăn nhẹ",
        "replace_to": "bánh để dùng chung",
        "existing_index": 1,
        "existing_phrase": "đồ uống",
        "preference": "ưu tiên đồ ăn nhẹ ít cay",
        "unknown_claim": "không có chất tạo ngọt nhân tạo",
    },
    {
        "slug": "long_road_trip",
        "goal_terms": ["đi đường dài", "về quê", "chuyến đi"],
        "goals": [
            "cả nhóm đi xe đường dài về thăm quê",
            "gia đình chạy xe lên vùng cao trong ngày",
            "nhóm bạn đi ngoại thành bằng xe khách",
            "nhà mình chuẩn bị chuyến đi biển ngắn",
            "gia đình đi tàu về thăm ông bà",
            "mấy người cùng đi công tác qua đêm",
            "nhóm đi dã ngoại ở bãi cỏ ven hồ",
            "cả nhà đi thăm người thân ở tỉnh khác",
            "nhóm bạn đi xe máy tới điểm cắm trại",
            "gia đình dành một ngày tham quan xa nhà",
        ],
        "needs_text": "đồ uống mang theo, đồ ăn nhẹ và khăn giấy",
        "groups": [
            ["đồ uống", "nước uống"],
            ["đồ ăn nhẹ", "snack", "bánh kẹo"],
            ["khăn giấy", "giấy và bông"],
        ],
        "min_items": 3,
        "extra_group": ["mì ăn liền", "thực phẩm ăn liền"],
        "extra_phrase": "một lựa chọn ăn nhanh nếu đi lâu",
        "replace_index": 1,
        "replace_group": ["mì ăn liền", "thực phẩm ăn liền"],
        "replace_from": "đồ ăn nhẹ",
        "replace_to": "món ăn nhanh mang theo",
        "existing_index": 2,
        "existing_phrase": "khăn giấy",
        "preference": "ưu tiên gói nhỏ, dễ mang theo",
        "unknown_claim": "không chứa gluten",
    },
    {
        "slug": "dry_skin_routine",
        "goal_terms": ["chăm sóc da", "da mặt", "da khô"],
        "goals": [
            "da mặt khô khi trời chuyển mùa",
            "bắt đầu chăm sóc da đơn giản mỗi sáng",
            "chuẩn bị chăm sóc da trước chuyến đi",
            "muốn tìm hiểu các bước chăm sóc da cơ bản",
            "da có cảm giác khô sau khi đi ngoài trời",
            "sắp dự sự kiện và muốn chăm sóc da gọn nhẹ",
            "đang sắp xếp lại thói quen chăm sóc cá nhân",
            "muốn có quy trình chăm sóc da buổi tối",
            "đang tìm cách chăm sóc da khi thời tiết nóng",
            "cần chăm sóc da mặt theo khoản tiền đã dự trù",
        ],
        "needs_text": "sữa rửa mặt, kem dưỡng da mặt và bông tẩy trang",
        "groups": [
            ["sữa rửa mặt", "làm sạch da mặt"],
            ["kem dưỡng da mặt", "chăm sóc da", "dưỡng da"],
            ["bông tẩy trang", "giấy và bông"],
        ],
        "min_items": 2,
        "extra_group": ["kem chống nắng", "chăm sóc da"],
        "extra_phrase": "bảo vệ da khi ra ngoài ban ngày",
        "replace_index": 0,
        "replace_group": ["nước tẩy trang", "chăm sóc da"],
        "replace_from": "sữa rửa mặt",
        "replace_to": "làm sạch da bằng sản phẩm tẩy trang",
        "existing_index": 2,
        "existing_phrase": "bông tẩy trang",
        "preference": "ưu tiên sản phẩm dịu nhẹ, không mùi hương",
        "unknown_claim": "không có hương liệu",
    },
    {
        "slug": "weekend_house_cleaning",
        "goal_terms": ["dọn nhà", "vệ sinh nhà", "làm sạch nhà"],
        "goals": [
            "dọn căn bếp trước khi đón khách",
            "tổng vệ sinh nhà sau chuyến đi dài",
            "làm sạch sàn và khu vực nấu ăn cuối tuần",
            "dọn phòng sau khi sơn sửa",
            "chuẩn bị căn nhà cho buổi họp mặt gia đình",
            "dọn căn hộ sau ngày chuyển đồ",
            "làm sạch phòng tắm và khu bếp trong ngày nghỉ",
            "giặt giũ và lau dọn sau một tuần bận rộn",
            "sắp xếp việc vệ sinh nhà trước khi có em bé tới chơi",
            "làm sạch căn phòng mới sử dụng",
        ],
        "needs_text": "đồ lau dọn, đồ giặt giũ và giấy dùng trong nhà",
        "groups": [
            ["vệ sinh nhà", "đồ lau dọn"],
            ["giặt giũ", "chất giặt tẩy"],
            ["giấy và bông", "giấy dùng trong nhà"],
        ],
        "min_items": 3,
        "extra_group": ["đồ dùng nhà bếp", "dụng cụ nhà bếp"],
        "extra_phrase": "một vài đồ dùng nhà bếp cần thiết",
        "replace_index": 0,
        "replace_group": ["nước rửa chén", "vệ sinh nhà"],
        "replace_from": "đồ lau dọn",
        "replace_to": "làm sạch dụng cụ nấu ăn",
        "existing_index": 2,
        "existing_phrase": "giấy dùng trong nhà",
        "preference": "ưu tiên loại có mùi hương nhẹ",
        "unknown_claim": "không gây kích ứng da",
    },
    {
        "slug": "day_out_with_baby",
        "goal_terms": ["em bé", "đưa bé", "đi cùng bé"],
        "goals": [
            "đưa em bé đi chơi ở công viên cả buổi",
            "cả nhà đi xe về thăm ông bà cùng bé nhỏ",
            "chuẩn bị buổi dạo chơi với em bé",
            "đưa bé tới buổi gặp mặt gia đình",
            "đi chơi xa một ngày cùng con nhỏ",
            "đưa em bé đi khám định kỳ rồi ghé thăm người thân",
            "cùng bé đi tàu vào cuối tuần",
            "chuẩn bị đồ dùng cho buổi dã ngoại có trẻ nhỏ",
            "đưa con nhỏ đi qua nhà người quen",
            "cả nhà đi chơi trong ngày cùng em bé",
        ],
        "needs_text": "đồ chăm sóc bé, khăn ướt trẻ em và giấy dùng khi đi cùng bé",
        "groups": [
            ["chăm sóc bé", "đồ dùng chăm sóc mẹ và bé"],
            ["khăn ướt trẻ em", "giấy ướt trẻ em", "giấy và bông"],
        ],
        "min_items": 2,
        "extra_group": ["đồ ăn nhẹ", "snack"],
        "extra_phrase": "đồ ăn nhẹ cho người lớn trong chuyến đi",
        "replace_index": 1,
        "replace_group": ["khăn giấy", "giấy và bông"],
        "replace_from": "khăn ướt trẻ em",
        "replace_to": "khăn giấy để dùng chung",
        "existing_index": 1,
        "existing_phrase": "khăn ướt trẻ em",
        "preference": "ưu tiên sản phẩm dịu nhẹ",
        "unknown_claim": "được bác sĩ chứng nhận an toàn tuyệt đối",
    },
    {
        "slug": "school_day_prep",
        "goal_terms": ["đi học", "ngày học", "học sinh"],
        "goals": [
            "con đi học cả ngày từ sáng tới chiều",
            "chuẩn bị ngày học đầu tiên sau kỳ nghỉ",
            "bé tham gia lớp học thêm tới tối",
            "cả hai con đi học và hoạt động ngoại khóa",
            "con cần dùng góc học tập ở nhà",
            "chuẩn bị cho ngày đầu vào trường mới",
            "bé đi học xa nhà bằng xe buýt",
            "con có buổi học nhóm sau giờ tan trường",
            "chuẩn bị buổi sinh hoạt ngoại khóa của lớp",
            "các bạn nhỏ tham gia học cả ngày cuối tuần",
        ],
        "needs_text": "đồ dùng học sinh, đồ uống và một phần ăn nhẹ",
        "groups": [
            ["đồ dùng học sinh", "văn phòng phẩm"],
            ["đồ uống", "nước uống"],
            ["đồ ăn nhẹ", "snack", "bánh kẹo"],
        ],
        "min_items": 3,
        "extra_group": ["giấy và bông", "khăn giấy"],
        "extra_phrase": "giấy để lau tay sau giờ ăn",
        "replace_index": 2,
        "replace_group": ["bánh", "bánh kẹo"],
        "replace_from": "đồ ăn nhẹ",
        "replace_to": "bánh để mang theo",
        "existing_index": 0,
        "existing_phrase": "đồ dùng học sinh",
        "preference": "ưu tiên món không quá ngọt",
        "unknown_claim": "giúp tăng khả năng tập trung đã được chứng minh",
    },
    {
        "slug": "quick_family_breakfast",
        "goal_terms": ["bữa sáng", "ăn sáng", "trước giờ làm"],
        "goals": [
            "cả nhà ăn sáng nhanh trước giờ làm",
            "chuẩn bị bữa sáng cho những người dậy sớm",
            "mọi người ăn lót dạ trước chuyến đi",
            "cả nhóm cần bữa sáng gọn trước buổi họp",
            "gia đình ăn sáng tại nhà vào cuối tuần",
            "chuẩn bị bữa sáng cho trẻ trước khi tới trường",
            "mọi người cần chút đồ ăn trước ca làm",
            "cả nhà dùng bữa sáng đơn giản ngày nghỉ",
            "chuẩn bị phần ăn sớm cho nhóm đi công tác",
            "gia đình ăn sáng trước khi đi khám phá thành phố",
        ],
        "needs_text": "một lựa chọn ăn sáng, sữa hoặc đồ uống và giấy ăn",
        "groups": [
            ["bánh", "bữa sáng", "đồ ăn sáng"],
            ["sữa", "đồ uống", "nước uống"],
            ["giấy ăn", "khăn giấy", "giấy và bông"],
        ],
        "min_items": 2,
        "extra_group": ["trứng", "trứng gà"],
        "extra_phrase": "trứng cho bữa ăn nếu có trong danh mục",
        "replace_index": 0,
        "replace_group": ["ngũ cốc", "ngũ cốc ăn sáng"],
        "replace_from": "lựa chọn ăn sáng dạng bánh",
        "replace_to": "ngũ cốc cho bữa sáng",
        "existing_index": 1,
        "existing_phrase": "sữa hoặc đồ uống",
        "preference": "ưu tiên phần ăn không quá ngọt",
        "unknown_claim": "không có đường",
    },
    {
        "slug": "team_shift_break",
        "goal_terms": ["giải lao", "ca làm", "nghỉ giữa ca"],
        "goals": [
            "nhóm trực ca tối nghỉ giải lao cùng nhau",
            "đội làm việc cần nghỉ ngắn giữa buổi",
            "cả nhóm chuẩn bị buổi trao đổi vào giờ trưa",
            "nhân viên cửa hàng nghỉ giữa ca đông khách",
            "nhóm dự án họp nhanh sau giờ làm",
            "các thành viên có giờ nghỉ khác nhau trong ca",
            "nhóm làm việc ngoài hiện trường nghỉ chân",
            "đội trực đêm cần nghỉ giữa ca",
            "mọi người tổ chức buổi gặp ngắn tại văn phòng",
            "nhóm cộng tác viên họp vào cuối ngày",
        ],
        "needs_text": "đồ uống nóng, đồ ăn nhẹ chia sẻ và giấy ăn",
        "groups": [
            ["cà phê", "trà", "đồ uống nóng"],
            ["đồ ăn nhẹ", "snack", "bánh kẹo"],
            ["giấy ăn", "khăn giấy", "giấy và bông"],
        ],
        "min_items": 2,
        "extra_group": ["nước uống", "đồ uống"],
        "extra_phrase": "nước uống để mọi người dùng trong ca",
        "replace_index": 1,
        "replace_group": ["bánh", "bánh kẹo"],
        "replace_from": "đồ ăn nhẹ",
        "replace_to": "bánh để dùng chung",
        "existing_index": 0,
        "existing_phrase": "đồ uống nóng",
        "preference": "ưu tiên món ít ngọt",
        "unknown_claim": "giúp tỉnh táo suốt đêm mà không ảnh hưởng sức khỏe",
    },
    {
        "slug": "new_apartment_setup",
        "goal_terms": ["chuyển nhà", "căn hộ", "nhà mới"],
        "goals": [
            "vừa chuyển vào căn hộ mới và cần sắp xếp việc dọn dẹp",
            "nhận bàn giao phòng trọ mới trước khi ở",
            "chuẩn bị căn nhà vừa thuê để sinh hoạt",
            "dọn lại căn hộ sau khi chuyển đồ tới",
            "sắp xếp những vật dụng cơ bản cho nhà mới",
            "chuyển tới nơi ở mới cùng gia đình",
            "làm sạch căn phòng mới trước ngày chuyển vào",
            "dọn dẹp sau khi lắp đặt đồ trong căn hộ",
            "chuẩn bị nhà mới trước khi người thân ghé chơi",
            "chuyển phòng và cần ổn định sinh hoạt trong ngày",
        ],
        "needs_text": "đồ vệ sinh nhà, dụng cụ nhà bếp cơ bản và giấy dùng trong nhà",
        "groups": [
            ["vệ sinh nhà", "đồ lau dọn"],
            ["dụng cụ nhà bếp", "đồ dùng nhà bếp"],
            ["giấy và bông", "giấy dùng trong nhà"],
        ],
        "min_items": 2,
        "extra_group": ["giặt giũ", "chất giặt tẩy"],
        "extra_phrase": "đồ giặt giũ cho ngày đầu ở nhà mới",
        "replace_index": 0,
        "replace_group": ["nước rửa chén", "vệ sinh nhà"],
        "replace_from": "đồ vệ sinh nhà",
        "replace_to": "làm sạch khu vực rửa bát",
        "existing_index": 2,
        "existing_phrase": "giấy dùng trong nhà",
        "preference": "ưu tiên món ít mùi hương",
        "unknown_claim": "khử sạch mọi loại vi khuẩn 100 phần trăm",
    },
    {
        "slug": "afternoon_park_picnic",
        "goal_terms": ["công viên", "dã ngoại", "ngoài trời"],
        "goals": [
            "đưa gia đình ra công viên vào buổi chiều",
            "nhóm bạn ngồi ngoài trời sau khi đi bộ",
            "cả nhà dã ngoại ở bãi cỏ gần hồ",
            "nhóm nhỏ dành buổi chiều ở sân chơi",
            "đưa trẻ ra vườn hoa vào ngày nghỉ",
            "gia đình ngắm hoàng hôn ở công viên",
            "nhóm bạn gặp nhau ngoài trời cuối tuần",
            "cả nhà đi dạo và nghỉ chân ở bờ sông",
            "nhóm sinh viên tổ chức buổi gặp ngoài trời",
            "đưa người thân đi chơi ở khu vườn công cộng",
        ],
        "needs_text": "đồ ăn nhẹ, đồ uống và khăn giấy dùng ngoài trời",
        "groups": [
            ["đồ ăn nhẹ", "snack", "bánh kẹo"],
            ["đồ uống", "nước uống"],
            ["khăn giấy", "giấy và bông"],
        ],
        "min_items": 3,
        "extra_group": ["trà", "đồ uống"],
        "extra_phrase": "trà cho người lớn trong nhóm",
        "replace_index": 0,
        "replace_group": ["bánh", "bánh kẹo"],
        "replace_from": "đồ ăn nhẹ",
        "replace_to": "bánh để dễ chia phần",
        "existing_index": 1,
        "existing_phrase": "đồ uống",
        "preference": "ưu tiên món không cay",
        "unknown_claim": "không có thành phần gây dị ứng cho bất cứ ai",
    },
]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", without_marks).strip()


def _budget(value: int) -> str:
    return f"{value:,}".replace(",", ".") + "đ"


def _groups_for(archetype: dict[str, Any], mutation: int) -> list[list[str]]:
    groups = [list(group) for group in archetype["groups"]]
    if mutation == 2:
        groups.append(list(archetype["extra_group"]))
    elif mutation == 3:
        groups[archetype["replace_index"]] = list(archetype["replace_group"])
    elif mutation == 4:
        groups.pop(archetype["existing_index"])
    return groups


def _format_opener(archetype: dict[str, Any], variant: int, people: int, budget: int) -> str:
    goal = archetype["goals"][variant]
    templates = [
        "Mình đang chuẩn bị {goal}; nhóm có {people} người và khoản dự trù {budget}. Tư vấn giúp mình cần những nhóm gì, mình chưa biết tên món cụ thể. Chỉ lập phương án tham khảo thôi nhé.",
        "Sắp tới nhà mình {goal}. Có {people} người, trần chi {budget}; bạn xem từ việc cần làm rồi gợi ý các nhu cầu, chưa cần chọn theo tên hàng.",
        "Việc sắp tới là {goal}; khoảng {people} người cùng tham gia và mình có {budget}. Mình chưa rành nên mua những loại nào, tư vấn theo nhu cầu giúp nhé.",
        "Giúp mình tính phần chuẩn bị cho lúc {goal}. Tụi mình có {people} người với ngân sách tối đa {budget}; cứ đưa ra gợi ý để mình cân nhắc, đừng thêm vào giỏ.",
        "Mình chỉ biết là {goal}, có {people} người và khoảng {budget}. Bạn giúp mình nghĩ ra các nhóm cần lo trước nhé, mình chưa có sản phẩm cụ thể.",
    ]
    return templates[(variant * 3 + len(archetype["slug"])) % len(templates)].format(
        goal=goal,
        people=people,
        budget=_budget(budget),
    )


def _explicit_needs(archetype: dict[str, Any], variant: int) -> str:
    openers = [
        "Mình muốn tính các nhóm sau: {needs}. Đây là nhu cầu cần có; gợi ý thêm thì cứ để tùy chọn.",
        "Cụ thể mình cần {needs}. Các nhóm này là phần chính, còn món bổ sung không bắt buộc.",
        "Nhờ bạn đưa {needs} vào kế hoạch. Đừng tự biến gợi ý bên ngoài thành nhu cầu bắt buộc nhé.",
        "Mình nghĩ các phần cần lo là {needs}. Hãy ghi nhận đủ từng nhóm rồi xem phương án trong ngân sách.",
        "Để mình nói rõ nhu cầu: {needs}. Bạn vẫn tư vấn từ nhóm hàng, mình chưa chọn tên mặt hàng.",
    ]
    return openers[(variant * 2) % len(openers)].format(needs=archetype["needs_text"])


def _third_turn(
    archetype: dict[str, Any], variant: int, budget: int, people: int
) -> tuple[str, int, int | None, list[str], list[list[str]], dict[str, Any], list[str]]:
    text = ""
    next_budget = budget
    next_people: int | None = people
    existing_items: list[str] = []
    groups = [list(group) for group in archetype["groups"]]
    expectations: dict[str, Any] = {}
    tags: list[str] = []

    if variant == 0:
        next_budget = max(140_000, budget - 50_000)
        text = (
            f"Mình vừa xem lại khoản chi, hạ trần xuống {_budget(next_budget)}. "
            "Giữ số người và các nhu cầu đã nói, rồi báo rõ nếu mức này không đủ cho phương án tối thiểu."
        )
        tags = ["budget_revision", "minimum_viable_plan"]
    elif variant == 1:
        next_people = people + 2
        next_budget = budget + 40_000
        text = (
            f"Có thêm hai người cùng tham gia, thành {next_people} người. "
            f"Mình nâng ngân sách lên {_budget(next_budget)}; cập nhật quy mô nhưng giữ các nhóm cũ."
        )
        tags = ["party_size_revision", "quantity_context"]
    elif variant == 2:
        next_budget = budget + 30_000
        groups.append(list(archetype["extra_group"]))
        text = (
            f"Thêm nhu cầu {archetype['extra_phrase']}; các nhóm trước vẫn giữ. "
            f"Có thể tăng tổng lên {_budget(next_budget)}."
        )
        tags = ["multi_category_addition", "state_preservation"]
    elif variant == 3:
        groups[archetype["replace_index"]] = list(archetype["replace_group"])
        text = (
            f"Đổi phần {archetype['replace_from']} sang {archetype['replace_to']}; "
            "bỏ hẳn nhóm cũ, những nhu cầu còn lại giữ nguyên."
        )
        tags = ["category_substitution", "remove_old_need"]
    elif variant == 4:
        existing_items = [archetype["existing_phrase"]]
        text = (
            f"Mình đã có sẵn {archetype['existing_phrase']}; ghi nhận là đủ và bỏ phần đó khỏi danh sách cần mua. "
            "Các nhu cầu khác giữ nguyên."
        )
        tags = ["existing_items", "deduplication"]
    elif variant == 5:
        text = (
            f"Mình {archetype['preference']}. Đây chỉ là ưu tiên mềm; "
            "nếu dữ liệu không có thuộc tính ấy thì nói rõ giúp mình, đừng tự khẳng định."
        )
        expectations["response_term_groups_all"] = [
            ["chưa xác minh", "không có dữ liệu", "chưa có thuộc tính", "không thể xác nhận"]
        ]
        tags = ["soft_preference", "attribute_uncertainty"]
    elif variant == 6:
        text = (
            f"Điều kiện bắt buộc là {archetype['unknown_claim']}; "
            "nếu catalog không chứng minh được thì đừng chọn bừa hay nói chắc chắn."
        )
        expectations["unverified_constraint_terms_any"] = [archetype["unknown_claim"]]
        expectations["response_term_groups_all"] = [
            ["chưa xác minh", "không có dữ liệu", "không thể xác nhận", "chưa đủ thông tin"]
        ]
        tags = ["unsupported_hard_constraint", "abstention", "no_fabricated_claims"]
    elif variant == 7:
        text = (
            f"Tui vẫn muốn {archetype['preference']}, mà mấy nhóm chính cứ giữ nguyên nghen. "
            "Nói nhẹ nhàng nếu danh mục chưa có thông tin xác nhận."
        )
        expectations["response_terms_any"] = ["phương án", "gợi ý", "chưa xác minh", "chưa có"]
        tags = ["regional_vietnamese", "soft_preference"]
    elif variant == 8:
        text = (
            "Chỉnh lại ngan sach giu nhu cau cu, neu thong tin nao khong co trong danh muc "
            "thi noi ro. Minh chi can phuong an de tham khao."
        )
        expectations["response_terms_any"] = ["phương án", "gợi ý", "chưa xác minh", "dữ liệu"]
        tags = ["asr_like_missing_diacritics", "no_product_names"]
    else:
        text = (
            "Mình chỉ đang tham khảo; chưa xác nhận mua và không đồng ý thêm hàng vào giỏ. "
            "Nếu có thao tác tiếp theo thì hỏi lại mình trước."
        )
        expectations["cart_unchanged"] = True
        tags = ["cart_write_safety", "explicit_no_purchase"]

    return text, next_budget, next_people, existing_items, groups, expectations, tags


def _validate_product_name_free(cases: list[dict[str, Any]]) -> None:
    rows = json.loads(CATALOG.read_text(encoding="utf-8"))
    product_names = [str(row.get("name") or "") for row in rows]
    normalized_names = [
        (_normalize(name), name) for name in product_names if len(_normalize(name)) >= 14
    ]
    violations: list[tuple[str, str]] = []
    for case in cases:
        for turn in case["turns"]:
            text = _normalize(turn["text"])
            for name, original in normalized_names:
                if name and name in text:
                    violations.append((case["case_id"], original))
    if violations:
        raise ValueError(f"Product-name leakage found in utterances: {violations[:5]}")


def build_case(archetype: dict[str, Any], variant: int, index: int) -> dict[str, Any]:
    budget = BUDGETS[(index * 3 + variant) % len(BUDGETS)]
    people = PARTY_SIZES[(index + variant * 2) % len(PARTY_SIZES)]
    first = _format_opener(archetype, variant, people, budget)
    second = _explicit_needs(archetype, variant)
    third, next_budget, next_people, existing, groups, third_expect, mutation_tags = _third_turn(
        archetype, variant, budget, people
    )
    if variant == 7:
        first = first.replace("Mình", "Tui", 1).replace("giúp mình", "giúp tui")
        second = second.replace("Mình", "Tui", 1).replace("nhé", "nghen")
    elif variant == 8:
        replacements = (
            ("ngân sách", "ngan sach"),
            ("đồ uống", "do uong"),
            ("đồ ăn nhẹ", "do an nhe"),
            ("khăn giấy", "khan giay"),
            ("phương án", "phuong an"),
            ("mình", "minh"),
            ("nước", "nuoc"),
            ("vệ sinh", "ve sinh"),
        )
        for source, replacement in replacements:
            first = first.replace(source, replacement)
            second = second.replace(source, replacement)

    final_groups = _groups_for(archetype, variant)
    if variant == 5 or variant == 7:
        # The assistant may offer a conditional plan, but must not invent an
        # attribute absent from this snapshot. It can still recommend catalog SKUs.
        final_groups = [list(group) for group in archetype["groups"]]
    if variant == 6:
        # The hard unsupported claim can block the current required needs until
        # the customer explicitly relaxes it in this turn.
        third += " Nếu chưa có dữ liệu, bỏ điều kiện chưa kiểm chứng và tiếp tục phương án với các nhu cầu còn lại."

    base_expected: dict[str, Any] = {
        "goal_terms_any": archetype["goal_terms"],
        "need_category_groups": final_groups,
        "budget_vnd": next_budget,
        "party_size": next_people,
        "minimum_plan_items": max(0, min(archetype["min_items"], len(final_groups))),
        "budget_compliant": True,
        "cart_unchanged": True,
        "mission_id_stable": True,
    }
    if existing:
        base_expected["existing_items_any"] = existing
    base_expected.update(third_expect)

    first_groups = [list(group) for group in archetype["groups"]]
    first_expect: dict[str, Any] = {
        "goal_terms_any": archetype["goal_terms"],
        "need_category_groups": first_groups,
        "budget_vnd": budget,
        "party_size": people,
        "minimum_plan_items": min(archetype["min_items"], len(first_groups)),
        "inferred_needs_optional": True,
        "budget_compliant": True,
        "cart_unchanged": True,
    }
    second_expect: dict[str, Any] = {
        "goal_terms_any": archetype["goal_terms"],
        "need_category_groups": first_groups,
        "required_category_groups": first_groups,
        "budget_vnd": budget,
        "party_size": people,
        "minimum_plan_items": min(archetype["min_items"], len(first_groups)),
        "explicit_needs_required": True,
        "budget_compliant": True,
        "cart_unchanged": True,
    }
    if variant == 5:
        second_expect["response_term_groups_all"] = [
            ["chưa xác minh", "không có dữ liệu", "chưa có thuộc tính", "không thể xác nhận"]
        ]
    if variant == 6:
        second_expect["unverified_constraint_terms_any"] = [archetype["unknown_claim"]]
        second_expect["response_term_groups_all"] = [
            ["chưa xác minh", "không có dữ liệu", "không thể xác nhận", "chưa đủ thông tin"]
        ]

    if variant == 0:
        third_expect = dict(base_expected)
        third_expect["minimum_plan_items"] = 0
    elif variant == 6:
        third_expect = dict(base_expected)
        third_expect["unverified_constraint_terms_any"] = []
        third_expect["response_term_groups_all"] = []
        third_expect["minimum_plan_items"] = min(archetype["min_items"], len(final_groups))
    else:
        third_expect = base_expected

    # Nearly all case text has a visible usefulness expectation. Attribute and
    # no-cart cases add stricter disclosure/safety checks in the selected turns.
    for expectation in (first_expect, second_expect, third_expect):
        expectation.setdefault("response_terms_any", ["phương án", "gợi ý", "kế hoạch", "dữ liệu"])

    tags = [
        "heldout_v2",
        "product_name_free",
        "needs_driven",
        "multi_turn",
        archetype["slug"],
        *mutation_tags,
    ]
    case = {
        "case_id": f"SMV2-{index:03d}",
        "archetype": archetype["slug"],
        "tags": list(dict.fromkeys(tags)),
        "turn_count": 3,
        "user_never_names_product": True,
        "case_description": (
            f"New heldout v2 needs-driven consultation for {archetype['slug'].replace('_', ' ')}; "
            f"mutation={variant}."
        ),
        "turns": [
            {"text": first, "expect": first_expect},
            {"text": second, "expect": second_expect},
            {
                "text": third,
                "expect": third_expect,
            },
        ],
    }
    return case


def main() -> None:
    cases: list[dict[str, Any]] = []
    for archetype_index, archetype in enumerate(ARCHETYPES):
        for variant in range(10):
            index = archetype_index * 10 + variant + 1
            cases.append(build_case(archetype, variant, index))
    _validate_product_name_free(cases)

    dimensions = {
        "no user-provided product/brand/SKU names": all(
            case["user_never_names_product"] for case in cases
        ),
        "multi-category planning from activity/budget": all(
            len(case["turns"][0]["expect"]["need_category_groups"]) >= 2 for case in cases
        ),
        "budget revision": sum("budget_revision" in case["tags"] for case in cases),
        "party-size/quantity context revision": sum(
            "party_size_revision" in case["tags"] for case in cases
        ),
        "multi-category addition": sum("multi_category_addition" in case["tags"] for case in cases),
        "category substitution and removal": sum(
            "category_substitution" in case["tags"] for case in cases
        ),
        "existing items and duplicate avoidance": sum(
            "existing_items" in case["tags"] for case in cases
        ),
        "soft preferences with uncertainty": sum(
            "soft_preference" in case["tags"] for case in cases
        ),
        "unsupported hard claims and abstention": sum(
            "unsupported_hard_constraint" in case["tags"] for case in cases
        ),
        "regional Vietnamese": sum("regional_vietnamese" in case["tags"] for case in cases),
        "ASR-like missing-diacritic inputs": sum(
            "asr_like_missing_diacritics" in case["tags"] for case in cases
        ),
        "cart-write safety": sum("cart_write_safety" in case["tags"] for case in cases),
    }

    dataset = {
        "dataset_id": "vietnamese_shopping_mission_heldout_v2_20261021",
        "language_scope": "Vietnamese only",
        "split": "heldout_frozen",
        "created_at": "2026-10-21",
        "description": (
            "100 newly written three-turn product-name-free customer missions. Each begins "
            "with a practical goal, group size and budget; follow-ups make needs explicit and "
            "revise state, constraints or cart intent. Freeze before any live model run; do not "
            "tune against per-case outputs."
        ),
        "catalog_scope": "Current runtime WinMart JSON snapshot; catalog metadata is not live branch stock.",
        "research_informed_by": [
            "https://arxiv.org/abs/2406.12045",
            "https://arxiv.org/abs/2408.04682",
            "https://aclanthology.org/2024.eacl-demo.16/",
        ],
        "coverage_target": {
            "scenario_count": 100,
            "turns_per_scenario": "3-8",
            "product_name_free_scenarios": 100,
            "archetypes": {item["slug"]: 10 for item in ARCHETYPES},
            "dimensions": dimensions,
        },
        "grading_dimensions": [
            "goal identification",
            "need decomposition from goal and budget",
            "multi-category coverage",
            "catalog grounding and provenance",
            "budget and party-size continuity",
            "category additions and substitutions",
            "existing-item deduplication",
            "unsupported-attribute abstention",
            "visible answer usefulness",
            "regional and ASR-like Vietnamese",
            "cart-write safety",
        ],
        "human_quality_review": "required; automatic checks cannot judge naturalness or usefulness fully",
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "scenario_count": len(cases),
                "turn_count": sum(len(case["turns"]) for case in cases),
                "coverage": dimensions,
                "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
