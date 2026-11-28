from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

LanguageCode = Literal["vi", "en", "zh"]

_PLANOGRAM_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "thanh_lam_planogram_assignments.json"
)
_DATASET_HASH = re.compile(r"^[a-f0-9]{64}$")
_LOCATION_CODE = re.compile(r"^A(\d{2})-B(\d{2})-L(\d{2})-P(\d{2})$")


@dataclass(frozen=True)
class AisleLayoutGuide:
    zone_vi: str
    zone_en: str
    zone_zh: str
    direction_vi: str
    short_direction_vi: str
    direction_en: str
    short_direction_en: str
    direction_zh: str
    short_direction_zh: str


# Grounded directly on the 13-aisle 3D store map (data/thanh_lam_planogram_3d_each_aisle.png)
# and data/thanh_lam_planogram_assignments.json.
_AISLE_LAYOUT_GUIDES: dict[str, AisleLayoutGuide] = {
    "A01": AisleLayoutGuide(
        zone_vi="Dệt may",
        zone_en="Textiles",
        zone_zh="纺织区",
        direction_vi=(
            "từ cửa vào đi thẳng qua khu kệ trung tâm vào góc trong cùng bên trái, "
            "sát vách sau và kế bên khu Hóa mỹ phẩm A02"
        ),
        short_direction_vi="góc trong cùng bên trái, sát khu Hóa mỹ phẩm A02",
        direction_en=(
            "walk past the center aisles to the far back-left corner along the back wall, "
            "right next to Cosmetics aisle A02"
        ),
        short_direction_en="far back-left corner, next to aisle A02",
        direction_zh="从入口穿过中间货架区到最里面的左后角，靠后墙、紧邻日化美妆区 A02",
        short_direction_zh="最里面左后角，紧邻 A02",
    ),
    "A02": AisleLayoutGuide(
        zone_vi="Hóa mỹ phẩm",
        zone_en="Cosmetics & Personal Care",
        zone_zh="日化美妆区",
        direction_vi=(
            "từ cửa vào đi qua khu kệ trung tâm về phía hàng kệ sau bên trái, "
            "nằm ngay cạnh khu Dệt may A01"
        ),
        short_direction_vi="hàng kệ phía sau bên trái, cạnh khu Dệt may A01",
        direction_en=(
            "walk past the center aisles to the left end of the rear row, "
            "next to Textiles aisle A01"
        ),
        short_direction_en="rear row on the left, next to aisle A01",
        direction_zh="从入口穿过中间货架区到后排左侧货架，紧邻纺织区 A01",
        short_direction_zh="后排左侧，紧邻 A01",
    ),
    "A03": AisleLayoutGuide(
        zone_vi="Hóa mỹ phẩm",
        zone_en="Cosmetics & Personal Care",
        zone_zh="日化美妆区",
        direction_vi=(
            "từ cửa vào đi thẳng qua khu kệ trung tâm vào hàng kệ phía sau, "
            "nằm ở dãy giữa khu Hóa mỹ phẩm, giữa dãy A02 và A04"
        ),
        short_direction_vi="hàng kệ phía sau, giữa dãy A02 và A04",
        direction_en=(
            "walk straight past the center aisles to the middle aisle of the rear Cosmetics row, "
            "between aisles A02 and A04"
        ),
        short_direction_en="rear Cosmetics row, between aisles A02 and A04",
        direction_zh="从入口直穿中间货架区到后排日化美妆区中间，位于 A02 和 A04 之间",
        short_direction_zh="后排中间，位于 A02 和 A04 之间",
    ),
    "A04": AisleLayoutGuide(
        zone_vi="Hóa mỹ phẩm",
        zone_en="Cosmetics & Personal Care",
        zone_zh="日化美妆区",
        direction_vi=(
            "từ cửa vào đi qua khu kệ trung tâm vào hàng kệ phía sau, "
            "phía bên phải khu Hóa mỹ phẩm và sát khu đồ gia dụng Homeline A05"
        ),
        short_direction_vi="hàng kệ phía sau, sát khu Homeline A05",
        direction_en=(
            "walk past the center aisles to the right side of the rear Cosmetics row, "
            "next to Homeline aisle A05"
        ),
        short_direction_en="rear row right of Cosmetics, next to aisle A05",
        direction_zh="从入口穿过中间货架区到后排日化美妆区右侧，紧邻家居百货区 A05",
        short_direction_zh="后排右侧，紧邻 A05",
    ),
    "A05": AisleLayoutGuide(
        zone_vi="Homeline (Đồ gia dụng)",
        zone_en="Homeline (Household Goods)",
        zone_zh="家居百货区",
        direction_vi=(
            "từ cửa vào đi thẳng sâu vào góc trong phía sau ở giữa-phải cửa hàng, "
            "kế bên khu Hóa mỹ phẩm A04 và rẽ phải là khu Tươi sống A13"
        ),
        short_direction_vi="góc trong phía sau giữa-phải, cạnh A04 và gần khu Tươi sống A13",
        direction_en=(
            "walk straight through to the back center-right corner, "
            "next to Cosmetics A04 and just before Fresh Food A13 on the right"
        ),
        short_direction_en="back center-right corner, next to A04",
        direction_zh="从入口一直向里走到后排中右侧角落，紧邻 A04，右转即到生鲜区 A13",
        short_direction_zh="后排中右侧角落，紧邻 A04",
    ),
    "A06": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "từ cửa vào rẽ trái tới dãy kệ đầu tiên ngoài cùng bên trái "
            "của khu kệ trung tâm, sát vách trái cửa hàng"
        ),
        short_direction_vi="khu kệ trung tâm ngoài cùng bên trái, sát vách trái",
        direction_en=(
            "turn left from the entrance to the leftmost aisle in the center section, "
            "along the left wall"
        ),
        short_direction_en="leftmost center aisle along the left wall",
        direction_zh="从入口左转到中间货架区最左侧第一排，靠左墙",
        short_direction_zh="中间区最左侧，靠左墙",
    ),
    "A07": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "từ cửa vào đi chếch sang trái tới dãy thứ 2 từ trái sang "
            "ở khu kệ trung tâm, giữa dãy A06 và A08"
        ),
        short_direction_vi="khu kệ trung tâm, dãy thứ 2 từ trái sang, giữa A06 và A08",
        direction_en=(
            "head slightly left from the entrance to the 2nd aisle from the left "
            "in the center section, between aisles A06 and A08"
        ),
        short_direction_en="center section, 2nd aisle from left, between A06 and A08",
        direction_zh="从入口向左前方走到中间货架区从左数第 2 排，位于 A06 和 A08 之间",
        short_direction_zh="中间区左数第 2 排，A06 与 A08 之间",
    ),
    "A08": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "từ cửa vào đi thẳng tới khu kệ trung tâm, hơi lệch sang trái một chút, "
            "giữa dãy A07 và A09"
        ),
        short_direction_vi="khu kệ trung tâm hơi lệch trái, giữa A07 và A09",
        direction_en=(
            "walk straight into the center aisles and slightly left, "
            "between aisles A07 and A09"
        ),
        short_direction_en="center section slightly left, between A07 and A09",
        direction_zh="从入口直走进入中间货架区，略微偏左，位于 A07 和 A09 之间",
        short_direction_zh="中间区略偏左，A07 与 A09 之间",
    ),
    "A09": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "từ cửa vào đi thẳng là gặp ngay ở chính giữa khu kệ trung tâm, "
            "giữa dãy A08 và A10"
        ),
        short_direction_vi="chính giữa khu kệ trung tâm, ngay trước cửa vào",
        direction_en=(
            "walk straight ahead from the entrance into the very center aisle, "
            "between aisles A08 and A10"
        ),
        short_direction_en="very center aisle straight ahead from entrance",
        direction_zh="从入口直走即可看到，就在中间货架区正中央，位于 A08 和 A10 之间",
        short_direction_zh="中间区正中央，入口正前方",
    ),
    "A10": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "từ cửa vào đi thẳng rồi hơi lệch sang phải ở khu kệ trung tâm, "
            "giữa dãy A09 và A11"
        ),
        short_direction_vi="khu kệ trung tâm hơi lệch phải, giữa A09 và A11",
        direction_en=(
            "walk straight into the center aisles and slightly right, "
            "between aisles A09 and A11"
        ),
        short_direction_en="center section slightly right, between A09 and A11",
        direction_zh="从入口直走进入中间货架区，略微偏右，位于 A09 和 A11 之间",
        short_direction_zh="中间区略偏右，A09 与 A11 之间",
    ),
    "A11": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "từ cửa vào đi chếch sang phải, ngay phía trên khu quầy thu ngân, "
            "giữa dãy A10 và A12"
        ),
        short_direction_vi="bên phải khu kệ trung tâm, phía trên quầy thu ngân",
        direction_en=(
            "head toward the right side of the center section just above checkout, "
            "between aisles A10 and A12"
        ),
        short_direction_en="right side of center section above checkout",
        direction_zh="从入口向右前方走，就在收银台上方区域，位于 A10 和 A12 之间",
        short_direction_zh="中间区偏右，收银台上方",
    ),
    "A12": AisleLayoutGuide(
        zone_vi="Thực phẩm công nghệ",
        zone_en="Packaged Food & Grocery",
        zone_zh="包装食品区",
        direction_vi=(
            "ở rìa bên phải khu kệ trung tâm, phía trên quầy thu ngân "
            "và nằm ngay trước dãy tủ mát tươi sống A13"
        ),
        short_direction_vi="rìa phải khu trung tâm, ngay trước dãy tủ mát A13",
        direction_en=(
            "at the right edge of the center section, past checkout "
            "and right in front of the A13 chilled cases"
        ),
        short_direction_en="right edge of center section, in front of A13",
        direction_zh="在中间货架区最右侧边缘，收银台上方、紧靠 A13 冷藏柜前",
        short_direction_zh="中间区最右侧，紧靠 A13 冷藏柜",
    ),
    "A13": AisleLayoutGuide(
        zone_vi="Thực phẩm tươi sống, chế biến",
        zone_en="Fresh & Chilled Food",
        zone_zh="生鲜及熟食冷藏区",
        direction_vi=(
            "từ cửa vào đi qua khu quầy thu ngân rồi rẽ phải, "
            "nằm ở dãy tủ mát dọc sát vách bên phải cửa hàng"
        ),
        short_direction_vi="dãy tủ mát dọc sát vách bên phải cửa hàng",
        direction_en=(
            "walk past the checkout counter on your right and follow the chilled cases "
            "along the right wall"
        ),
        short_direction_en="chilled cases along the right wall",
        direction_zh="从入口经过右侧收银台后右转，沿右侧墙面的生鲜冷藏柜陈列",
        short_direction_zh="右侧墙面生鲜冷藏柜",
    ),
}

_LEVEL_LABELS: dict[int, dict[LanguageCode, str]] = {
    1: {
        "vi": "tầng 1 (tầng dưới cùng, sát chân kệ)",
        "en": "level 1 (bottom shelf, near the floor)",
        "zh": "第 1 层（最底层，贴近底部）",
    },
    2: {
        "vi": "tầng 2 (tầng thấp, phía dưới tầm mắt)",
        "en": "level 2 (lower shelf, below eye level)",
        "zh": "第 2 层（较低层，视线下方）",
    },
    3: {
        "vi": "tầng 3 (tầng giữa, ngang tầm mắt, dễ thấy nhất)",
        "en": "level 3 (middle shelf, right at eye level)",
        "zh": "第 3 层（中间层，平视位置，最易看到）",
    },
    4: {
        "vi": "tầng 4 (tầng trên, hơi cao hơn tầm mắt)",
        "en": "level 4 (upper shelf, just above eye level)",
        "zh": "第 4 层（较高层，略高于视线）",
    },
    5: {
        "vi": "tầng 5 (tầng trên cùng)",
        "en": "level 5 (top shelf)",
        "zh": "第 5 层（最顶层）",
    },
}

_LEVEL_SHORT_LABELS: dict[int, dict[LanguageCode, str]] = {
    1: {
        "vi": "tầng 1 dưới cùng",
        "en": "level 1 (bottom)",
        "zh": "第 1 层（最底层）",
    },
    2: {
        "vi": "tầng 2 phía dưới tầm mắt",
        "en": "level 2 (lower)",
        "zh": "第 2 层（较低层）",
    },
    3: {
        "vi": "tầng 3 ngang tầm mắt",
        "en": "level 3 (eye level)",
        "zh": "第 3 层（平视层）",
    },
    4: {
        "vi": "tầng 4 phía trên tầm mắt",
        "en": "level 4 (upper)",
        "zh": "第 4 层（较高层）",
    },
    5: {
        "vi": "tầng 5 trên cùng",
        "en": "level 5 (top)",
        "zh": "第 5 层（最顶层）",
    },
}


@dataclass(frozen=True)
class PlanogramLocationDetails:
    code: str
    aisle: str
    aisle_num: int
    bay: str
    bay_num: int
    bay_count: int | None
    level: str
    level_num: int
    slot: str
    slot_num: int
    zone_vi: str
    zone_en: str
    zone_zh: str
    direction_vi: str
    short_direction_vi: str
    direction_en: str
    short_direction_en: str
    direction_zh: str
    short_direction_zh: str
    category_cue: str | None = None

    def bay_phrase(self, language: LanguageCode = "vi") -> str:
        total = self.bay_count
        if total is None or total <= 0:
            if language == "en":
                return f"bay {self.bay_num}"
            if language == "zh":
                return f"第 {self.bay_num} 货架段"
            return f"khoang {self.bay_num}"

        if total == 1:
            pos_vi, pos_en, pos_zh = (
                "khoang duy nhất của dãy",
                "the only bay in the aisle",
                "唯一货架段",
            )
        elif total == 2:
            if self.bay_num == 1:
                pos_vi, pos_en, pos_zh = "đầu dãy", "start of the aisle", "通道前段"
            else:
                pos_vi, pos_en, pos_zh = "cuối dãy", "end of the aisle", "通道后段"
        else:
            if self.bay_num <= 2:
                pos_vi, pos_en, pos_zh = "đầu dãy", "near the start of the aisle", "通道前段"
            elif self.bay_num >= total - 1:
                pos_vi, pos_en, pos_zh = "cuối dãy", "near the end of the aisle", "通道后段"
            else:
                pos_vi, pos_en, pos_zh = "đoạn giữa dãy", "middle of the aisle", "通道中段"

        if language == "en":
            return f"bay {self.bay_num}/{total} ({pos_en})"
        if language == "zh":
            return f"第 {self.bay_num}/{total} 货架段（{pos_zh}）"
        return f"khoang {self.bay_num}/{total} ở {pos_vi}"

    def level_phrase(self, language: LanguageCode = "vi", *, short: bool = False) -> str:
        table = _LEVEL_SHORT_LABELS if short else _LEVEL_LABELS
        mapped = table.get(self.level_num)
        if mapped:
            return mapped[language]
        if language == "en":
            return f"level {self.level_num}"
        if language == "zh":
            return f"第 {self.level_num} 层"
        return f"tầng {self.level_num}"

    def slot_phrase(self, language: LanguageCode = "vi") -> str:
        if self.slot_num <= 2:
            pos_vi, pos_en, pos_zh = "đầu khoang", "near the start of the bay", "货架段前侧"
        elif self.slot_num >= 7:
            pos_vi, pos_en, pos_zh = "cuối khoang", "near the end of the bay", "货架段后侧"
        else:
            pos_vi, pos_en, pos_zh = "giữa khoang", "middle of the bay", "货架段中段"
        if language == "en":
            return f"slot {self.slot_num} ({pos_en})"
        if language == "zh":
            return f"第 {self.slot_num} 格（{pos_zh}）"
        return f"ô số {self.slot_num} ở {pos_vi}"

    def zone(self, language: LanguageCode = "vi") -> str:
        return {"vi": self.zone_vi, "en": self.zone_en, "zh": self.zone_zh}[language]

    def direction(self, language: LanguageCode = "vi", *, short: bool = False) -> str:
        if short:
            return {
                "vi": self.short_direction_vi,
                "en": self.short_direction_en,
                "zh": self.short_direction_zh,
            }[language]
        return {
            "vi": self.direction_vi,
            "en": self.direction_en,
            "zh": self.direction_zh,
        }[language]

    def compact_label(self, language: LanguageCode = "vi") -> str:
        """Concise readable label for multi-field answers and UI metadata."""
        if language == "en":
            return (
                f"{self.zone_en}, aisle {self.aisle} ({self.short_direction_en}), "
                f"{self.bay_phrase('en')}, {self.level_phrase('en', short=True)}, "
                f"slot {self.slot_num} ({self.code})"
            )
        if language == "zh":
            return (
                f"{self.zone_zh} {self.aisle} 通道（{self.short_direction_zh}）、"
                f"{self.bay_phrase('zh')}、{self.level_phrase('zh', short=True)}、"
                f"第 {self.slot_num} 格（编码 {self.code}）"
            )
        return (
            f"khu {self.zone_vi}, dãy {self.aisle} ({self.short_direction_vi}), "
            f"{self.bay_phrase('vi')}, {self.level_phrase('vi', short=True)}, "
            f"ô số {self.slot_num}, mã {self.code}"
        )

    def shelf_position_clause(self, language: LanguageCode = "vi") -> str:
        """Bay + height + slot + code, without repeating zone/aisle."""
        if language == "en":
            return (
                f"{self.bay_phrase('en')}, {self.level_phrase('en', short=True)}, "
                f"slot {self.slot_num} ({self.code})"
            )
        if language == "zh":
            return (
                f"{self.bay_phrase('zh')}、{self.level_phrase('zh', short=True)}、"
                f"第 {self.slot_num} 格（{self.code}）"
            )
        return (
            f"{self.bay_phrase('vi')}, {self.level_phrase('vi', short=True)}, "
            f"ô số {self.slot_num} ({self.code})"
        )

    def detailed_sentence(
        self,
        product_name: str,
        language: LanguageCode = "vi",
        *,
        include_caveat: bool = True,
    ) -> str:
        """Full shopper-friendly Vietnamese/EN/ZH step-by-step shelf directions."""
        clean_name = " ".join(product_name.split())
        cue = self.category_cue
        if language == "en":
            cue_part = (
                f" ({cue} section)" if cue and cue.casefold() != self.zone_en.casefold() else ""
            )
            text = (
                f"According to the suggested planogram, {clean_name} is in the "
                f"{self.zone_en} zone, aisle {self.aisle}{cue_part}: {self.direction_en}. "
                f"Look at {self.bay_phrase('en')}, {self.level_phrase('en')}, "
                f"{self.slot_phrase('en')} (locator {self.code})."
            )
            if include_caveat:
                text += (
                    " Please note this is a proposed layout"
                    " and actual store placement is unverified."
                )
            return text
        if language == "zh":
            cue_part = f"（{cue}品类）" if cue else ""
            text = (
                f"根据建议货架图，{clean_name} 位于 {self.zone_zh} 的 {self.aisle} 通道{cue_part}："
                f"{self.direction_zh}。请查看 {self.bay_phrase('zh')}、{self.level_phrase('zh')}、"
                f"{self.slot_phrase('zh')}（位置编码 {self.code}）。"
            )
            if include_caveat:
                text += " 请注意这是建议陈列位置，门店实际摆放尚未核实。"
            return text
        cue_part = (
            f" (nhóm {cue})"
            if cue and cue.casefold() != self.zone_vi.casefold()
            else ""
        )
        text = (
            f"Theo sơ đồ kệ đề xuất, {clean_name} nằm ở khu {self.zone_vi}, "
            f"dãy {self.aisle}{cue_part}: {self.direction_vi}. "
            f"Bạn nhìn tới {self.bay_phrase('vi')}, {self.level_phrase('vi')}, "
            f"{self.slot_phrase('vi')} (mã vị trí {self.code}) nhé."
        )
        if include_caveat:
            text += (
                " Lưu ý đây là vị trí gợi ý trên sơ đồ, "
                "chưa xác minh cách bày thực tế tại cửa hàng."
            )
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "aisle": self.aisle,
            "bay": self.bay,
            "bay_count": self.bay_count,
            "level": self.level,
            "slot": self.slot,
            "zone_vi": self.zone_vi,
            "zone_en": self.zone_en,
            "category_cue": self.category_cue,
            "direction_vi": self.direction_vi,
            "direction_en": self.direction_en,
            "bay_vi": self.bay_phrase("vi"),
            "level_vi": self.level_phrase("vi"),
            "slot_vi": self.slot_phrase("vi"),
            "summary_vi": self.compact_label("vi"),
            "summary_en": self.compact_label("en"),
        }


@dataclass(frozen=True)
class _PlanogramCache:
    source_hashes: frozenset[str] | None
    locations: dict[str, str]
    aisle_bay_counts: dict[str, int]
    aisle_sub_departments: dict[str, str]
    bay_top_categories: dict[tuple[str, str], str]
    product_categories: dict[str, str]


@lru_cache(maxsize=1)
def _load_planogram_data() -> _PlanogramCache:
    """Load recommended locators and layout metadata once; invalid files fail closed."""
    empty = _PlanogramCache(None, {}, {}, {}, {}, {})
    try:
        payload: dict[str, Any] = json.loads(_PLANOGRAM_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty

    if not isinstance(payload, dict):
        return empty
    dataset_hash = payload.get("source_dataset_sha256")
    canonical_dataset_hash = payload.get("source_dataset_canonical_sha256")
    assignments = payload.get("assignments")
    if (
        not isinstance(dataset_hash, str)
        or not _DATASET_HASH.fullmatch(dataset_hash)
        or not isinstance(assignments, list)
    ):
        return empty

    dataset_hashes = {dataset_hash}
    if isinstance(canonical_dataset_hash, str) and _DATASET_HASH.fullmatch(
        canonical_dataset_hash
    ):
        dataset_hashes.add(canonical_dataset_hash)

    aisle_bay_counts: dict[str, int] = {}
    aisle_sub_departments: dict[str, str] = {}
    for aisle_entry in payload.get("aisles") or ():
        if not isinstance(aisle_entry, dict):
            continue
        aisle_code = aisle_entry.get("aisle")
        if not isinstance(aisle_code, str) or not aisle_code:
            continue
        bay_count = aisle_entry.get("bay_count")
        if isinstance(bay_count, int) and bay_count > 0:
            aisle_bay_counts[aisle_code] = bay_count
        sub_dept = aisle_entry.get("sub_department")
        if isinstance(sub_dept, str) and sub_dept.strip():
            aisle_sub_departments[aisle_code] = sub_dept.strip()

    locations: dict[str, str] = {}
    product_categories: dict[str, str] = {}
    bay_cat_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        product_id = assignment.get("product_id")
        location_code = assignment.get("location_code")
        if (
            assignment.get("placement_status") == "RECOMMENDED_SYNTHETIC_PLANOGRAM"
            and isinstance(product_id, str)
            and isinstance(location_code, str)
            and product_id
            and _LOCATION_CODE.fullmatch(location_code)
        ):
            locations[product_id] = location_code
            cat_name = assignment.get("category_name")
            if isinstance(cat_name, str) and cat_name.strip():
                cleaned_cat = cat_name.strip()
                product_categories[product_id] = cleaned_cat
                aisle_code = assignment.get("aisle")
                bay_code = assignment.get("bay")
                if isinstance(aisle_code, str) and isinstance(bay_code, str):
                    bay_cat_counts[(aisle_code, bay_code)][cleaned_cat] += 1

    bay_top_categories = {
        key: counter.most_common(1)[0][0]
        for key, counter in bay_cat_counts.items()
        if counter
    }
    return _PlanogramCache(
        source_hashes=frozenset(dataset_hashes),
        locations=locations,
        aisle_bay_counts=aisle_bay_counts,
        aisle_sub_departments=aisle_sub_departments,
        bay_top_categories=bay_top_categories,
        product_categories=product_categories,
    )


def _load_planogram() -> tuple[frozenset[str] | None, dict[str, str]]:
    """Backwards-compatible accessor returning (source_hashes, product_id -> location_code)."""
    cache = _load_planogram_data()
    return cache.source_hashes, cache.locations


def recommended_shelf(product_id: str, dataset_version: str | None) -> str | None:
    """Return a locator only when the catalog row matches the map's source data."""
    if not dataset_version:
        return None
    source_hashes, locations = _load_planogram()
    if source_hashes is None or dataset_version not in source_hashes:
        return None
    return locations.get(product_id)


def describe_planogram_location(
    location_code: str | None,
    *,
    product_id: str | None = None,
    category_hint: str | None = None,
) -> PlanogramLocationDetails | None:
    """Parse a valid 13-aisle planogram code (A01..A13-Bxx-Lxx-Pxx) into shopper directions.

    Returns None for non-planogram codes (e.g., legacy codes like 'A2-L2' or 'A1-B2')
    so callers never invent walking directions for unmapped layouts.
    """
    if not location_code:
        return None
    match = _LOCATION_CODE.fullmatch(location_code.strip())
    if match is None:
        return None
    aisle_s, bay_s, level_s, slot_s = match.groups()
    aisle_code = f"A{aisle_s}"
    bay_code = f"B{bay_s}"
    level_code = f"L{level_s}"
    slot_code = f"P{slot_s}"
    guide = _AISLE_LAYOUT_GUIDES.get(aisle_code)
    if guide is None:
        return None

    cache = _load_planogram_data()
    bay_count = cache.aisle_bay_counts.get(aisle_code)
    cue: str | None = None
    if product_id and product_id in cache.product_categories:
        cue = cache.product_categories[product_id]
    elif (aisle_code, bay_code) in cache.bay_top_categories:
        cue = cache.bay_top_categories[(aisle_code, bay_code)]
    elif category_hint:
        segments = [seg.strip() for seg in category_hint.split(">") if seg.strip()]
        if segments:
            cue = segments[-1]

    return PlanogramLocationDetails(
        code=location_code.strip(),
        aisle=aisle_code,
        aisle_num=int(aisle_s),
        bay=bay_code,
        bay_num=int(bay_s),
        bay_count=bay_count,
        level=level_code,
        level_num=int(level_s),
        slot=slot_code,
        slot_num=int(slot_s),
        zone_vi=guide.zone_vi,
        zone_en=guide.zone_en,
        zone_zh=guide.zone_zh,
        direction_vi=guide.direction_vi,
        short_direction_vi=guide.short_direction_vi,
        direction_en=guide.direction_en,
        short_direction_en=guide.short_direction_en,
        direction_zh=guide.direction_zh,
        short_direction_zh=guide.short_direction_zh,
        category_cue=cue,
    )
