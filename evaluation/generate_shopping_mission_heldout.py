from __future__ import annotations

# Scenario utterances and the dataset description are intentionally kept as
# readable single strings; long lines here are evaluation data, not code.
# ruff: noqa: E501
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v1_20261021.json"

OPENERS = [
    "Mình cần chuẩn bị {purpose}, có khoảng {people} người và ngân sách {budget}. Chưa biết nên bắt đầu từ đâu, tư vấn giúp mình.",
    "Sắp tới {purpose}; mình dự trù {budget} cho {people} người. Mình không biết tên món cần mua, gợi ý theo nhu cầu nhé.",
    "Giúp mình lên phương án cho việc {purpose}. Nhóm có {people} người, chi khoảng {budget}; đừng cho vào giỏ vội.",
    "Tụi mình chuẩn bị {purpose}, khoảng {people} người. Ngân sách tầm {budget}, mình chưa rõ cần những nhóm gì.",
    "Mình đang lo việc {purpose}; {people} người, tổng khoảng {budget}. Bạn giúp mình chọn thứ tự ưu tiên với.",
    "Cho mình xin kế hoạch mua sắm để {purpose}, dự trù {budget} cho {people} người; mình chưa có sản phẩm cụ thể trong đầu.",
    "Cuối tuần này {purpose}. Có {people} người và {budget}; giúp mình nghĩ xem cần chuẩn bị gì cho gọn.",
    "Mình chỉ có mục tiêu là {purpose}, khoảng {budget} cho {people} người. Cứ tư vấn từ vấn đề giúp mình, chưa cần biết tên hàng.",
    "Tối ni mình phải {purpose}, có {people} người với {budget}. Chưa biết cần chi, chỉ giúp mình lên phương án tham khảo.",
    "Mình sắp {purpose}; nếu có {budget} cho {people} người thì nên ưu tiên nhu cầu nào trước?",
]
OPENERS_NO_PEOPLE = [
    "Mình cần {purpose} và có khoảng {budget}. Chưa biết nên bắt đầu từ đâu, tư vấn giúp mình.",
    "Sắp tới {purpose}; mình dự trù {budget}. Mình không biết tên món cần mua, gợi ý theo nhu cầu nhé.",
    "Giúp mình lên phương án cho việc {purpose}, chi khoảng {budget}; đừng cho vào giỏ vội.",
    "Mình chuẩn bị {purpose}, ngân sách tầm {budget}. Chưa rõ cần những nhóm gì.",
    "Mình đang lo việc {purpose}; tổng khoảng {budget}. Bạn giúp mình chọn thứ tự ưu tiên với.",
    "Cho mình xin kế hoạch mua sắm để {purpose}, dự trù {budget}; mình chưa có sản phẩm cụ thể trong đầu.",
    "Cuối tuần này {purpose}. Có {budget}; giúp mình nghĩ xem cần chuẩn bị gì cho gọn.",
    "Mình chỉ có mục tiêu là {purpose}, khoảng {budget}. Cứ tư vấn từ vấn đề giúp mình, chưa cần biết tên hàng.",
    "Tối ni mình phải {purpose}, có {budget}. Chưa biết cần chi, chỉ giúp mình lên phương án tham khảo.",
    "Mình sắp {purpose}; nếu có {budget} thì nên ưu tiên nhu cầu nào trước?",
]
BUDGETS = [80000, 100000, 120000, 150000, 180000, 200000, 220000, 250000, 300000, 350000]
PEOPLE = [3, 4, 5, 6, 7, 8, 10, 12, 15, 20]

ARCHETYPES = [
    {
        "slug": "gathering",
        "tag": "party_gathering",
        "goal_terms": ["bạn", "tụ họp", "tiếp khách", "xem phim", "gặp mặt"],
        "purposes": [
            "đón bạn bè qua nhà tối nay",
            "xem phim với gia đình cuối tuần",
            "mời đồng nghiệp ghé chơi sau giờ làm",
            "tổ chức buổi gặp mặt nhỏ ở nhà",
            "xem trận bóng cùng mấy người bạn",
            "tiếp họ hàng tới chơi vào ngày nghỉ",
            "làm buổi liên hoan nhỏ cho nhóm",
            "đón khách tới nhà vào buổi chiều",
            "tụ tập với mấy đứa bạn trong xóm",
            "tổ chức buổi trò chuyện cuối tuần",
        ],
        "groups": [["snack", "ăn vặt", "bánh kẹo"], ["đồ uống", "nước uống", "nước suối"]],
        "needs": "đồ ăn nhẹ để chia nhau và đồ uống",
        "extra": ["trái cây", "hoa quả"],
        "replace": ["đồ ăn nhẹ", "trái cây"],
        "remove": ["đồ ăn nhẹ"],
        "existing": "đồ ăn nhẹ",
        "min_items": 2,
    },
    {
        "slug": "travel",
        "tag": "travel_preparation",
        "goal_terms": ["du lịch", "chuyến đi", "về quê", "đi biển", "tham quan"],
        "purposes": [
            "đi biển hai ngày với gia đình",
            "về quê thăm người thân cuối tuần",
            "đi công tác ngắn ngày",
            "đi chơi xa bằng xe khách",
            "đi tàu trong chuyến thăm quê",
            "đi dã ngoại ở ngoại thành",
            "đi nghỉ cuối tuần cùng gia đình",
            "đưa cả nhà đi thăm bảo tàng",
            "đi đường dài bằng ô tô",
            "đi tham quan trong ngày với nhóm bạn",
        ],
        "groups": [
            ["đồ uống", "nước uống", "nước suối"],
            ["đồ ăn nhẹ", "snack", "bánh kẹo"],
            ["giấy và bông", "khăn giấy"],
        ],
        "needs": "đồ uống, đồ ăn nhẹ và giấy dùng khi đi đường",
        "extra": ["trái cây", "hoa quả"],
        "replace": ["đồ ăn nhẹ", "trái cây"],
        "remove": ["giấy dùng khi đi đường"],
        "existing": "giấy dùng khi đi đường",
        "min_items": 3,
    },
    {
        "slug": "skincare",
        "tag": "skincare_discovery",
        "goal_terms": ["chăm sóc da", "da mặt"],
        "purposes": [
            "bắt đầu chăm sóc da mặt vì da đang khô",
            "chuẩn bị cho buổi chụp ảnh cuối tuần",
            "tìm hiểu cách chăm sóc da khi thời tiết hanh",
            "sắp đi du lịch và muốn chăm sóc da gọn nhẹ",
            "tìm cách chăm sóc da mặt theo ngân sách nhỏ",
            "chuẩn bị đi dự tiệc và muốn chăm sóc da",
            "tìm hiểu các bước chăm sóc da hằng ngày",
            "đổi mùa nên muốn xem lại việc chăm sóc da",
            "bắt đầu chăm sóc da sau thời gian bỏ quên",
            "chuẩn bị đi công tác và muốn chăm sóc da",
        ],
        "groups": [["chăm sóc da", "mỹ phẩm chăm sóc cá nhân"]],
        "needs": "nhóm sản phẩm chăm sóc da theo nhu cầu đã nói",
        "extra": ["giấy và bông", "khăn giấy"],
        "replace": ["một món chăm sóc da đang có", "nhu cầu chăm sóc da khác"],
        "remove": ["nhu cầu chăm sóc da đang có"],
        "existing": "một bước chăm sóc da",
        "min_items": 1,
        "unknown_claim": "không hương liệu",
    },
    {
        "slug": "household_cleaning",
        "tag": "household_shopping",
        "goal_terms": ["dọn nhà", "vệ sinh nhà", "làm sạch"],
        "purposes": [
            "tổng vệ sinh nhà vào cuối tuần",
            "dọn dẹp sau khi sửa sang phòng khách",
            "làm sạch nhà trước khi có khách",
            "sắp xếp việc dọn nhà sau kỳ nghỉ",
            "vệ sinh căn nhà vừa chuyển tới",
            "chuẩn bị đồ dùng cho buổi tổng vệ sinh",
            "dọn căn bếp và phòng tắm trong ngày nghỉ",
            "làm sạch nhà trước khi đón người thân",
            "giải quyết việc giặt giũ và dọn dẹp cuối tuần",
            "dọn nhà sau một buổi tụ họp",
        ],
        "groups": [
            ["vệ sinh nhà", "đồ lau dọn"],
            ["chất giặt tẩy", "giặt giũ"],
            ["giấy và bông", "giấy dùng trong nhà"],
        ],
        "needs": "đồ vệ sinh nhà, giặt giũ và giấy dùng trong nhà",
        "extra": ["đồ dùng nhà bếp", "vật dụng bếp"],
        "replace": ["đồ lau dọn", "đồ giặt giũ"],
        "remove": ["giấy dùng trong nhà"],
        "existing": "giấy dùng trong nhà",
        "min_items": 3,
    },
    {
        "slug": "baby_trip",
        "tag": "household_shopping",
        "purposes": [
            "đưa bé nhỏ về quê ba ngày",
            "đi chơi cuối tuần cùng em bé",
            "chuẩn bị chuyến đi ngắn với con nhỏ",
            "đưa hai bé đi thăm ông bà",
            "đi chơi xa cùng trẻ nhỏ trong một ngày",
            "chuẩn bị cho bé qua nhà người thân",
            "đưa bé đi nghỉ ở ngoại thành",
            "đi xe đường dài cùng con",
            "chuẩn bị chuyến về quê cho gia đình có em bé",
            "đưa bé đi chơi cả ngày cuối tuần",
        ],
        "groups": [
            ["đồ dùng chăm sóc mẹ và bé", "chăm sóc cá nhân cho bé"],
            ["giấy và bông", "khăn giấy"],
        ],
        "needs": "đồ chăm sóc bé và giấy dùng khi đi cùng bé",
        "extra": ["đồ ăn nhẹ", "snack"],
        "replace": ["khăn giấy", "đồ dùng chăm sóc bé"],
        "remove": ["khăn giấy"],
        "existing": "khăn giấy",
        "min_items": 2,
    },
    {
        "slug": "school_prep",
        "tag": "school_household",
        "purposes": [
            "chuẩn bị cho con vào năm học mới",
            "lo đồ cần thiết cho ngày đầu đi học",
            "sắp xếp đồ cho bé đi học cả ngày",
            "chuẩn bị cho con tham gia lớp học thêm",
            "lo việc học tập của hai bé trong nhà",
            "chuẩn bị đồ dùng cho buổi học ngoại khóa",
            "giúp con sẵn sàng cho học kỳ mới",
            "chuẩn bị cho bé đi học sau kỳ nghỉ",
            "đưa con tới lớp học cuối tuần",
            "sắp xếp đồ cần dùng ở trường",
        ],
        "groups": [
            ["văn phòng phẩm", "đồ dùng học tập"],
            ["snack", "đồ ăn nhẹ", "bánh kẹo"],
            ["đồ uống", "nước uống"],
        ],
        "needs": "đồ dùng học tập, món ăn nhẹ và nước uống",
        "extra": ["giấy và bông", "khăn giấy"],
        "replace": ["món ăn nhẹ", "trái cây"],
        "remove": ["nước uống mang theo"],
        "existing": "nước uống mang theo",
        "min_items": 3,
    },
    {
        "slug": "family_breakfast",
        "tag": "household_shopping",
        "purposes": [
            "chuẩn bị bữa sáng cho gia đình ngày mai",
            "mời cả nhà ăn sáng vào ngày nghỉ",
            "lo bữa sáng nhanh cho gia đình đông người",
            "chuẩn bị bữa sáng trước khi mọi người đi làm",
            "ăn sáng cùng ông bà và các cháu cuối tuần",
            "chuẩn bị bữa sáng cho cả nhà trước chuyến đi",
            "lo bữa sáng gọn cho gia đình ở nhà",
            "chuẩn bị bữa sáng cho nhóm người thân ghé thăm",
            "cả nhà cùng ăn sáng vào dịp cuối tuần",
            "chuẩn bị bữa sáng cho mọi người trước giờ học",
        ],
        "groups": [["bánh", "bánh mì"], ["bơ sữa trứng", "sữa"], ["trái cây", "hoa quả"]],
        "needs": "bánh, sữa và trái cây cho bữa sáng",
        "extra": ["đồ uống", "nước uống"],
        "replace": ["sữa", "đồ uống"],
        "remove": ["trái cây cho bữa sáng"],
        "existing": "trái cây cho bữa sáng",
        "min_items": 2,
    },
    {
        "slug": "office_meeting",
        "tag": "office_meeting",
        "purposes": [
            "lo đồ cho buổi họp ở văn phòng",
            "đón nhóm khách tới buổi trao đổi công việc",
            "chuẩn bị một buổi đào tạo nội bộ",
            "tổ chức buổi họp nhóm vào sáng mai",
            "tiếp đối tác trong cuộc họp ngắn",
            "chuẩn bị phòng họp cho buổi chiều",
            "lo đồ dùng cho cuộc họp kéo dài vài tiếng",
            "tổ chức buổi tổng kết của nhóm nhỏ",
            "chuẩn bị cho buổi hướng dẫn nhân viên",
            "đón mọi người tới buổi thảo luận cuối tuần",
        ],
        "groups": [
            ["cà phê", "trà", "đồ uống"],
            ["snack", "bánh kẹo", "đồ ăn nhẹ"],
            ["đồ dùng bàn ăn", "giấy và bông"],
        ],
        "needs": "đồ uống, bánh ăn nhẹ và vật dụng phục vụ cuộc họp",
        "extra": ["giấy và bông", "khăn giấy"],
        "replace": ["cà phê", "trà"],
        "remove": ["đồ dùng bàn ăn"],
        "existing": "đồ dùng bàn ăn",
        "min_items": 3,
    },
    {
        "slug": "new_apartment",
        "tag": "household_shopping",
        "purposes": [
            "mới dọn vào căn hộ và cần sắm đồ thiết yếu",
            "chuẩn bị cho căn phòng trọ mới",
            "sắp xếp căn bếp sau khi chuyển nhà",
            "bắt đầu ở riêng trong căn hộ nhỏ",
            "hoàn thiện đồ dùng cơ bản cho nhà mới",
            "chuyển tới chỗ ở mới vào tuần này",
            "chuẩn bị các vật dụng tối thiểu cho phòng mới",
            "dọn vào nhà thuê và cần mua vài thứ thiết yếu",
            "sắp xếp nhà mới cho hai người ở",
            "ổn định sinh hoạt sau khi chuyển chỗ ở",
        ],
        "groups": [
            ["vệ sinh nhà", "đồ lau dọn"],
            ["giấy và bông", "giấy dùng trong nhà"],
            ["đồ dùng nhà bếp", "vật dụng bếp"],
        ],
        "needs": "đồ vệ sinh, giấy và vật dụng bếp cơ bản",
        "extra": ["chất giặt tẩy", "giặt giũ"],
        "replace": ["vật dụng bếp", "đồ vệ sinh nhà"],
        "remove": ["giấy dùng trong nhà"],
        "existing": "giấy dùng trong nhà",
        "min_items": 2,
    },
    {
        "slug": "picnic_outdoor",
        "tag": "party_gathering",
        "purposes": [
            "đi dã ngoại ngoài công viên vào chủ nhật",
            "đi picnic bên hồ cùng gia đình",
            "ngồi chơi ngoài trời với nhóm bạn",
            "dành một ngày ở khu cắm trại",
            "đi bộ đường dài cùng nhóm nhỏ",
            "tổ chức buổi gặp ngoài trời cho gia đình",
            "đi chơi ở bãi biển trong ngày",
            "đưa trẻ ra công viên cả buổi chiều",
            "tham gia chuyến dã ngoại của lớp",
            "ngồi thư giãn ngoài trời vào ngày nghỉ",
        ],
        "groups": [
            ["trái cây", "hoa quả"],
            ["đồ uống", "nước uống"],
            ["snack", "đồ ăn nhẹ", "bánh kẹo"],
        ],
        "needs": "trái cây, đồ uống và món ăn nhẹ mang theo",
        "extra": ["giấy và bông", "khăn giấy"],
        "replace": ["món ăn nhẹ", "trái cây"],
        "remove": ["đồ uống mang theo"],
        "existing": "đồ uống mang theo",
        "min_items": 3,
    },
]

GOAL_TERMS = {
    "gathering": ["bạn", "tụ họp", "tiếp khách", "xem phim", "gặp mặt"],
    "travel": ["du lịch", "chuyến đi", "về quê", "đi biển", "tham quan"],
    "skincare": ["chăm sóc da", "da mặt"],
    "household_cleaning": ["dọn nhà", "vệ sinh nhà", "làm sạch"],
    "baby_trip": ["bé", "trẻ nhỏ", "em bé", "về quê"],
    "school_prep": ["đi học", "năm học", "trường", "học tập"],
    "family_breakfast": ["bữa sáng", "ăn sáng"],
    "office_meeting": ["họp", "văn phòng", "đào tạo", "đối tác"],
    "new_apartment": ["nhà mới", "căn hộ", "chuyển nhà", "chỗ ở"],
    "picnic_outdoor": ["dã ngoại", "picnic", "cắm trại", "ngoài trời"],
}
REMOVE_INDEX = {
    "gathering": 0,
    "travel": 2,
    "household_cleaning": 2,
    "baby_trip": 1,
    "school_prep": 2,
    "family_breakfast": 2,
    "office_meeting": 2,
    "new_apartment": 1,
    "picnic_outdoor": 1,
}


def money(v: int) -> str:
    return f"{v:,}đ".replace(",", ".")


def normalize_groups(groups):
    return [list(g) for g in groups]


def build_case(group_index: int, variant: int, case_index: int):
    a = ARCHETYPES[group_index]
    people = PEOPLE[(variant + group_index * 3) % len(PEOPLE)]
    budget = BUDGETS[(variant * 2 + group_index) % len(BUDGETS)]
    purpose = a["purposes"][variant]
    opener = (
        OPENERS_NO_PEOPLE[(variant + group_index) % len(OPENERS_NO_PEOPLE)]
        if group_index in (2, 3, 8)
        else OPENERS[(variant + group_index) % len(OPENERS)]
    )
    incomplete = variant == 9
    if incomplete:
        first = f"Mình sắp {purpose}, mà chưa biết phải chuẩn bị những gì. Tư vấn từ nhu cầu giúp mình với."
        first_people = None
        first_budget = None
    else:
        first = opener.format(purpose=purpose, people=people, budget=money(budget))
        first_people = people if group_index not in (2, 3, 8) else None
        first_budget = budget
    groups = normalize_groups(a["groups"])
    second = f"Thêm vào kế hoạch {a['needs']}; đây là các nhu cầu mình muốn tính đến. "
    if a["slug"] == "skincare":
        second += f"Điều kiện bắt buộc là {a['unknown_claim']}; nếu dữ liệu không xác minh được thì đừng khẳng định hoặc chọn bừa."
        tags = ["missing_attribute", "no_fabricated_claims"]
    elif a["slug"] in ("gathering", "picnic_outdoor", "school_prep"):
        second += "Ưu tiên món không cay, nhưng không cần ép các gợi ý thêm thành bắt buộc."
        tags = ["hard_constraint", "optional_inference"]
    else:
        second += "Mình vẫn muốn xem vài phương án khác nhau; các nhóm vừa nêu thì cần có."
        tags = ["multi_category", "required_needs"]
    if incomplete:
        second += f" Tổng ngân sách tối đa là {money(budget)}."
        second_budget = budget
    else:
        second_budget = budget
    revision = variant % 5
    revised_groups = normalize_groups(groups)
    revised_budget = budget
    revised_people = first_people
    existing = []
    if revision == 0:
        revised_budget = max(50000, budget - 20000)
        third = f"Giảm ngân sách xuống tối đa {money(revised_budget)}, giữ nguyên số người và các nhu cầu đã chốt nhé."
        tags += ["budget_revision", "preserve_state"]
    elif revision == 1:
        revised_people = people + 2 if first_people is not None else None
        if revised_people is not None:
            third = f"Thực ra tăng thêm hai người nữa, giờ là {revised_people} người; chỉnh phương án theo số người mới và giữ ngân sách {money(budget)}."
        else:
            revised_budget = budget + 30000
            third = f"Tăng ngân sách lên {money(revised_budget)} để có thêm lựa chọn, còn các nhóm đã nêu thì giữ lại."
        tags += ["quantity_adjustment", "preserve_state"]
    elif revision == 2:
        extra = a["extra"]
        revised_groups.append(extra)
        revised_budget = budget + 30000
        third = f"Thêm cả nhu cầu {extra[0]} vào phương án và nâng tổng lên {money(revised_budget)}; các nhóm cũ vẫn giữ."
        tags += ["category_addition", "budget_revision"]
    elif revision == 3:
        source = a["replace"][0]
        replacement = a["replace"][1]
        if a["slug"] == "skincare":
            third = f"Nếu không thể xác minh điều kiện về mùi thì bỏ điều kiện đó; giữ tư vấn chăm sóc da trong mức {money(budget)} và nói rõ giới hạn dữ liệu."
            tags += ["remove_unsupported_constraint", "no_fabricated_claims"]
        else:
            replacement_map = {
                "gathering": (0, ["trái cây", "hoa quả"], "món ăn nhẹ", "trái cây"),
                "travel": (1, ["trái cây", "hoa quả"], "món ăn nhẹ", "trái cây"),
                "household_cleaning": (
                    2,
                    ["đồ dùng nhà bếp", "vật dụng bếp"],
                    "giấy dùng trong nhà",
                    "đồ dùng nhà bếp",
                ),
                "baby_trip": (1, ["đồ uống", "nước uống"], "giấy dùng khi đi cùng bé", "đồ uống"),
                "school_prep": (1, ["trái cây", "hoa quả"], "món ăn nhẹ", "trái cây"),
                "family_breakfast": (1, ["đồ uống", "nước uống"], "sữa", "đồ uống"),
                "office_meeting": (0, ["trà", "đồ uống"], "cà phê", "trà"),
                "new_apartment": (2, ["chất giặt tẩy", "giặt giũ"], "vật dụng bếp", "đồ giặt giũ"),
                "picnic_outdoor": (2, ["giấy và bông", "khăn giấy"], "món ăn nhẹ", "khăn giấy"),
            }
            index, new_group, source_text, replacement_text = replacement_map.get(
                a["slug"], (None, None, source, replacement)
            )
            if index is not None and index < len(revised_groups):
                revised_groups[index] = new_group
            revised_budget = max(50000, budget - 10000)
            third = f"Đổi nhu cầu {source_text} sang {replacement_text}, giữ nguyên các nhóm còn lại và đặt trần {money(revised_budget)}."
            tags += ["category_substitution", "budget_revision"]
        if a["slug"] == "skincare":
            revised_budget = budget
    elif revision == 4:
        existing = [a["existing"]]
        source = a["remove"][0]
        if a["slug"] == "skincare":
            third = f"Mình đã có một bước chăm sóc da cơ bản rồi; tính là có sẵn, đừng gợi ý mua trùng. Phần còn lại tối đa {money(budget)}."
            tags += ["existing_items", "deduplication"]
        else:
            remove_index = REMOVE_INDEX.get(a["slug"])
            if remove_index is not None and remove_index < len(revised_groups):
                revised_groups.pop(remove_index)
            third = f"Mình đã có {source}; ghi nhận là có sẵn và bỏ khỏi danh sách mua. Giữ các nhu cầu khác, ngân sách {money(budget)}."
            tags += ["existing_items", "need_removal"]
    tags.extend(["heldout", "multi_turn", "no_product_names", a["tag"]])
    if variant in (3, 7):
        third = (
            third.replace("ngân sách", "ngan sach")
            .replace("phương án", "phuong an")
            .replace("trái cây", "trai cay")
        )
        tags += ["asr_like"]
    if variant in (2, 6):
        first = first.replace("Mình", "Tui", 1).replace("giúp mình", "giúp tui")
        tags += ["regional_vietnamese"]
    goal_terms = GOAL_TERMS[a["slug"]]
    initial_min_items = min(a["min_items"], len(groups))
    final_min_items = (
        0
        if revision == 4 and a["slug"] == "skincare"
        else 1
        if a["slug"] == "skincare"
        else min(a["min_items"], len(revised_groups))
    )
    expected_common = {
        "goal_terms_any": goal_terms,
        "need_category_groups": normalize_groups(revised_groups),
        "budget_vnd": revised_budget,
        "party_size": revised_people,
        "minimum_plan_items": final_min_items,
        "budget_compliant": True,
        "all_products_in_catalog": True,
        "cart_unchanged": True,
        "mission_id_stable": True,
    }
    first_expect = {
        "goal_terms_any": goal_terms,
        "need_category_groups": normalize_groups(groups),
        "budget_vnd": first_budget,
        "party_size": first_people,
        "minimum_plan_items": initial_min_items,
        "inferred_needs_optional": True,
        "all_products_in_catalog": True,
        "cart_unchanged": True,
    }
    if first_budget is not None:
        first_expect["budget_compliant"] = True
    t1 = {"text": first, "expect": first_expect}
    t2 = {
        "text": second,
        "expect": {
            "goal_terms_any": goal_terms,
            "need_category_groups": normalize_groups(groups),
            "required_category_groups": normalize_groups(groups),
            "budget_vnd": second_budget,
            "party_size": first_people,
            "minimum_plan_items": 0
            if a["slug"] == "skincare"
            else min(a["min_items"], len(groups)),
            "explicit_needs_required": True,
            "budget_compliant": True,
            "all_products_in_catalog": True,
            "cart_unchanged": True,
        },
    }
    t3 = {"text": third, "expect": expected_common}
    if existing:
        t3["expect"]["existing_items_any"] = existing
    if a["slug"] == "skincare":
        t2["expect"]["unverified_constraint_terms_any"] = [a["unknown_claim"]]
        if revision == 3:
            t3["expect"]["unverified_constraint_terms_any"] = []
        if revision == 4:
            t3["expect"]["existing_items_any"] = ["một bước chăm sóc da"]
    if incomplete:
        tags += ["incomplete_context"]
    case = {
        "case_id": f"SMH-{case_index:03d}",
        "archetype": a["slug"],
        "tags": list(dict.fromkeys(tags)),
        "turn_count": 3,
        "user_never_names_product": True,
        "case_description": f"Needs-driven {a['slug'].replace('_', ' ')} consultation; variant {variant + 1}.",
        "turns": [t1, t2, t3],
    }
    return case


cases = []
for group_index, _ in enumerate(ARCHETYPES):
    for variant in range(10):
        cases.append(build_case(group_index, variant, len(cases) + 1))

dataset = {
    "dataset_id": "vietnamese_shopping_mission_heldout_v1_20261021",
    "language_scope": "Vietnamese only",
    "split": "heldout",
    "created_at": "2026-10-21",
    "description": "100 fresh, three-turn, product-name-free shopping mission conversations. This frozen evaluation set is separate from development cases. Every scenario begins with a customer goal/use case; follow-ups add explicit needs and revise budget, quantities, preferences, substitutions, or existing items. Do not tune against individual cases. Human review remains required for recommendation usefulness and naturalness.",
    "catalog_scope": "Runtime WinMart JSON snapshot; 3,099 catalog rows in the current snapshot. Not a live branch inventory feed.",
    "research_informed_by": [
        "https://arxiv.org/abs/2406.12045",
        "https://arxiv.org/abs/2408.04682",
        "https://aclanthology.org/2024.eacl-demo.16/",
    ],
    "coverage_target": {
        "scenario_count": 100,
        "turns_per_scenario": "3-8",
        "product_name_free_scenarios": 100,
        "archetypes": {a["slug"]: 10 for a in ARCHETYPES},
        "dimensions": [
            "no product names",
            "multi-category discovery",
            "budget changes",
            "quantity changes",
            "category substitutions",
            "existing items",
            "missing attributes",
            "unsupported claim abstention",
            "regional Vietnamese",
            "ASR-like transcript errors",
            "cart-write safety",
        ],
    },
    "grading_dimensions": [
        "goal identification",
        "need decomposition",
        "catalog retrieval relevance",
        "hard constraints",
        "cross-category coverage",
        "budget compliance",
        "conversation state",
        "recommendation usefulness",
        "grounded explanations",
        "fabricated claims",
        "cart/checkout safety",
    ],
    "cases": cases,
}
OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(
    "wrote",
    OUTPUT,
    "cases",
    len(cases),
    "turns",
    sum(len(c["turns"]) for c in cases),
    "product_name_free",
    sum(c["user_never_names_product"] for c in cases),
)
