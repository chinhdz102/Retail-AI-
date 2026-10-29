"""Lightweight multilingual retail intent parsing for reliable tool routing.

This is deliberately a multi-label, high-recall parser for common spoken and
typed Vietnamese and English customer phrasing. It routes only facts supported by catalog tools; the
LLM still handles open-ended language and composition, while the API keeps the
final grounding and unsupported-attribute checks.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


def normalize_vietnamese(text: str) -> str:
    # Resolve genuinely accentless chat spellings before accent stripping.
    # Doing this afterward turns valid words such as "hồng" into "không"
    # (both collapse to ``hong``), which can create false negation filters.
    source = text.casefold()
    source = re.sub(r"\b(?:ko|khum)\b", "khong", source)
    source = re.sub(
        r"\b(?:nhiu|nhieu)\s+(?:zậy|dzậy)\b",
        "bao nhieu vay",
        source,
    )
    source = re.sub(r"\b(?:zậy|dzậy)\b", "vay", source)
    source = re.sub(
        r"\b(?:hong|hem)(?=\s+(?:cay|duong|man|ngot|muon|co|thich|can|biet|dung|lay|mua))",
        "khong",
        source,
    )
    source = re.sub(r"\b(?:z|dzay|zay)\b", "vay", source)
    decomposed = unicodedata.normalize("NFD", source)
    normalized = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    normalized = normalized.replace("đ", "d").replace("_", " ")
    # Common keyboard/chat and speech-to-text spellings.
    replacements = (
        # Common spoken and written regional variants, applied only where they
        # have a clear conversational meaning. Keep the rest of the utterance
        # intact so product names are not aggressively rewritten.
        (
            r"\b(ngon|tot|re|chay|ban chay|dat hang|ua chuong|pho bien|"
            r"hot|duoc mua nhieu)\s+nhut\b",
            r"\1 nhat",
        ),  # Southern colloquial spelling of "nhất", in ranking phrases only
        (r"\b(mon|cai|loai)\s+mo\b", r"\1 nao"),  # Central "mô" = which
        (r"\bo\s+mo\s+rua\b", "o dau vay"),  # Central "ở mô rứa" = where
        (r"\bo\s+mo\b", "o dau"),  # Central "ở mô" = where
        (r"\b(mon|cai|loai)\s+chi(?=\s+(?:ngon|hop|re|tot|duoc)\b)", r"\1 nao"),
        (r"\b(an|uong)\s+chi\b", r"\1 gi"),
        (r"\bmo(?=\s+(?:ngon|an|uong|ban|chay|hop|re|tot|duoc)\b)", "nao"),
        (r"(?<!bao )\b(?:nhiu|nhieu)(?=\s+(?:tien|dong|vay|the|a|ban)\b)", "bao nhieu"),
        (r"(?<!bao )\b(?:nhiu|nhieu)(?=\W*$)", "bao nhieu"),
        (r"\b(?:bao\s+nhiu|bao\s+nhieu)\b", "bao nhieu"),
        (r"\b(?:bn|bnh)\b", "bao nhieu"),
        (r"\b(?:hien\s+gio|bay\s+gio)\b", "hien tai"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    return re.sub(r"[^\w\s]", " ", normalized)


@lru_cache(maxsize=2048)
def _boundary_phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Compile reusable static phrase cues once instead of on every parser call."""
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_boundary_phrase_pattern(phrase).search(text) for phrase in phrases)


def requested_snapshot_fields(text: str) -> tuple[str, ...]:
    """Recognize supported catalog facts across colloquial Vietnamese paraphrases."""
    normalized = " ".join(normalize_vietnamese(text).split())
    tokens = set(normalized.split())

    nutrition = bool(
        tokens
        & {
            "calo",
            "kcal",
            "calorie",
            "calories",
            "protein",
            "sugar",
            "caffeine",
            "cafein",
        }
    ) or _has_phrase(
        normalized,
        (
            "dinh duong",
            "luong duong",
            "bao nhieu duong",
            "co duong khong",
            "con nhieu duong",
            "sugar content",
            "nutrition information",
            "nutritional information",
            "chat beo",
            "luong caffeine",
            "luong cafein",
            "caffeine content",
            "cafein bao nhieu",
        ),
    )
    promotion = _has_phrase(
        normalized,
        (
            "khuyen mai",
            "uu dai",
            "giam gia",
            "discount",
            "on sale",
            "sale off",
            "sale",
            "deal",
            "deals",
            "offer",
            "offers",
            "promotion",
            "promotions",
            "special offer",
            "special offers",
            "ma giam gia",
            "chuong trinh uu dai",
            "chuong trinh khuyen mai",
            "mua 1 tang",
            "mua hai tang",
            "促销",
        ),
    )
    # ``giá`` and ``gia đình`` normalize to the same token sequence. Do not
    # classify an activity about family as a price lookup just because the
    # latter phrase starts with the unaccented token ``gia``.
    price_query_text = re.sub(
        r"\btham\s+gia\b|\bgia\s+(?:dinh|vi|nhap|han|tang|cong|tri)\b",
        " ",
        normalized,
    )
    price = (
        _has_phrase(
            price_query_text,
            (
                "gia",
                "price",
                "cost",
                "how much",
                "how much does",
                "what is the price",
                "what s the price",
                "whats the price",
                "how much is",
                "bao nhieu tien",
                "bao tien",
                "may dong",
                "may ngan",
                "may nghin",
                "gia sao",
                "gia the nao",
                "gia hien tai",
                "gia luc nay",
                "bao gia",
                "tien nao",
                "tam gia",
                "多少钱",
                "价格",
            ),
        )
        or (
            _has_phrase(normalized, ("bao nhieu", "how much", "多少钱"))
            and not nutrition
            and not _has_phrase(
                normalized,
                (
                    "bao nhieu loai",
                    "bao nhieu vi",
                    "bao nhieu chai",
                    "bao nhieu lon",
                    "bao nhieu hop",
                    "bao nhieu goi",
                    "bao nhieu ly",
                    "bao nhieu mon",
                    "bao nhieu phan",
                    "bao nhieu mieng",
                    "bao nhieu cai",
                    "con bao nhieu",
                    "ton bao nhieu",
                    "han bao nhieu",
                ),
            )
        )
        or (
            bool(re.search(r"\b\d+\s*(?:k|ngan|nghin|dong|d)\b", normalized))
            and not _has_phrase(normalized, ("duoi", "toi da", "khong qua", "budget", "ngan sach"))
        )
    )
    dosage_question = _has_phrase(
        normalized,
        (
            "lieu luong",
            "lieu dung",
            "dose",
            "dosage",
            "dosing instructions",
            "how much to use",
        ),
    )
    if dosage_question:
        # "Bao nhiêu" in a dosage question is not a request for price.
        price = False
    # A discount question is not a price lookup unless the customer also asks
    # for the actual price (e.g. "giá sau giảm còn bao nhiêu?").
    promotion = promotion or bool(re.search(r"\bmua\s+\w+\s+tang\s+\w+\b", normalized))
    promotion_stripped = price_query_text
    for cue in ("ma giam gia", "giam gia", "khuyen mai", "uu dai", "deal", "on sale", "sale"):
        promotion_stripped = re.sub(rf"(?<!\w){re.escape(cue)}(?!\w)", " ", promotion_stripped)
    explicit_price_with_promotion = _has_phrase(
        promotion_stripped, ("gia", "price", "cost", "bao nhieu tien", "bao tien", "may dong")
    )
    if promotion and not explicit_price_with_promotion:
        price = False

    stock = _has_phrase(
        normalized,
        (
            "con hang",
            "co hang",
            "het hang",
            "con san",
            "con ban",
            "co ban khong",
            "dang ban",
            "van ban",
            "con ban duoc khong",
            "ban duoc khong",
            "con khong",
            "het chua",
            "con chua",
            "con may",
            "con nhieu",
            "con may chai",
            "con may lon",
            "con may hop",
            "con may goi",
            "con may ly",
            "con may cai",
            "ton kho",
            "ton bao nhieu",
            "ton ghi trong du lieu",
            "so luong ton",
            "available",
            "in stock",
            "availability",
            "stock level",
            "left in stock",
            "how many left",
            "how many",
            "库存",
            "有货",
        ),
    ) or bool(
        re.search(r"\bton\b.{0,40}\b(?:bao nhieu|how many)\b", normalized)
        or
        re.search(r"\b(?:con|co)\s+(?:chai|lon|hop|goi|ly|cai)\b.{0,40}\bkhong\b", normalized)
        or re.search(r"\bcon\s+(?:chai|lon|hop|goi|ly|cai)\b.{0,20}\bnao\b", normalized)
        or re.search(r"\bcon\b.{0,30}\bban duoc\b.{0,15}\bkhong\b", normalized)
        or re.search(r"\bmua duoc\b.{0,45}\bkhong\b", normalized)
        or re.search(r"\bco ban\b.{0,40}\bkhong\b", normalized)
        or re.search(r"\bco san\b(?!\s+pham\b).{0,40}\bkhong\b", normalized)
        or re.search(r"\bcon\s+\d+\s+(?:chai|lon|hop|goi|ly|cai)\b", normalized)
        or re.search(
            r"\b(?:con|co)\s+\d+(?:(?:[.,]|\s)\d+)?\s*(?:ml|l|lit|g|kg)\b",
            normalized,
        )
        or re.search(
            r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg)\b.{0,30}\b(?:co|ban|ton)\b.{0,15}\bkhong\b",
            normalized,
        )
    )
    # In a promotion query, "đang có" describes the offer rather than stock.
    asks_for_a_product_type = _has_phrase(
        normalized,
        ("co san pham", "san pham nao", "mat hang nao"),
    )
    stock = stock or (
        _has_phrase(normalized, ("dang co",)) and not promotion and not asks_for_a_product_type
    )
    stock = stock or _has_phrase(
        normalized,
        ("co san hang", "co san trong kho", "co san de ban"),
    )
    if asks_for_a_product_type and not _has_phrase(
        normalized,
        (
            "con hang",
            "het hang",
            "ton kho",
            "so luong ton",
            "con ban",
            "het chua",
            "con bao nhieu",
        ),
    ):
        stock = False
    remaining_quantity_question = _has_phrase(normalized, ("con bao nhieu",)) or bool(
        re.search(r"\bcon\s+may\s+(?:chai|lon|hop|goi|ly|cai)\b", normalized)
    )
    remaining_amount_is_price = _has_phrase(
        normalized, ("con bao nhieu tien", "con bao nhieu dong")
    ) or bool(
        re.search(
            r"\b(?:gia|price|cost)\b.{0,60}\bcon bao nhieu\b",
            normalized,
        )
    )
    if remaining_quantity_question and not remaining_amount_is_price:
        stock = True
    if re.search(r"\bton\b.{0,40}\b(?:bao nhieu|how many)\b", normalized):
        stock = True
        price = False
    if nutrition:
        stock = False

    shelf = _has_phrase(
        normalized,
        (
            "o dau",
            "o ke",
            "ke nao",
            "ke may",
            "de cho nao",
            "day nao",
            "day nao vay",
            "de dau",
            "dat o dau",
            "nam o dau",
            "vi tri",
            "tim o dau",
            "lay o dau",
            "mua o dau",
            "quay nao",
            "khu nao",
            "cho nao",
            "aisle",
            "which aisle",
            "shelf",
            "where",
            "在哪",
            "哪里",
            "货架",
        ),
    ) or bool(
        re.search(r"\b(?:chi\s+)?ke\b.{0,25}\b(?:giup|o|day|dau|nao|may)\b", normalized)
        or re.search(r"\bo\s+day\s+(?:nao|may)\b", normalized)
        or re.search(r"\bke\s+o\s+day\s+(?:nao|may)\b", normalized)
    )
    expiry = _has_phrase(
        normalized,
        (
            "han dung",
            "han su dung",
            "han su dung den",
            "het han",
            "con han khong",
            "han den bao gio",
            "dung den bao gio",
            "ngay het han",
            "expiry",
            "expiry date",
            "expiration",
            "expiration date",
            "best before",
            "use by",
            "保质期",
        ),
    ) or bool(re.search(r"\bhan\b.{0,35}\b(?:ngay nao|toi ngay nao|den ngay nao)\b", normalized))
    ingredients = _has_phrase(
        normalized,
        (
            "thanh phan",
            "nguyen lieu",
            "ingredients",
            "what is inside",
            "what s inside",
            "whats inside",
            "made of",
            "what does it contain",
            "what does this contain",
            "配料",
            "gom nhung gi",
            "lam tu gi",
            "duoc lam tu gi",
            "co nhung gi",
            "co gi ben trong",
            "co nhung gi ben trong",
            "ben trong co gi",
            "nhan gom gi",
            "nhan co gi",
            "trong nay co gi",
            "co duong khong",
            "co duong",
            "has sugar",
            "does it have sugar",
            "does this have sugar",
            "does it contain sugar",
            "does this contain sugar",
        ),
    ) or bool(
        re.search(
            r"\b(?:nhan|ben trong|trong chai|trong hop|trong goi|trong san pham)\b"
            r".{0,30}\b(?:co|gom|chua)\b.{0,20}\b(?:gi|nhung gi)\b",
            normalized,
        )
    )
    if tokens & {
        "allergy",
        "allergic",
        "allergen",
        "allergens",
        "peanut",
        "peanuts",
        "gluten",
        "lactose",
        "diabetic",
        "diabetes",
        "pregnant",
        "vegan",
        "vegetarian",
        "sugar",
        "calorie",
        "calories",
        "protein",
        "kcal",
    }:
        ingredients = False
    spicy = _has_phrase(
        normalized,
        (
            "khong cay",
            "co cay",
            "vi cay",
            "do cay",
            "cay khong",
            "cay nhieu khong",
            "it cay",
            "cay te",
            "spicy",
            "not spicy",
            "mild",
            "heat level",
            "how spicy",
            "辣",
            "不辣",
        ),
    )
    data_timestamp = _has_phrase(
        normalized,
        (
            "du lieu cap nhat",
            "du lieu duoc cap nhat",
            "cap nhat ngay nao",
            "cap nhat hom nao",
            "cap nhat luc nao",
            "ngay cap nhat",
            "ngay du lieu",
            "ghi nhan ngay nao",
            "du lieu ghi nhan ngay nao",
            "gia ghi nhan ngay nao",
            "du lieu tu ngay nao",
            "lay du lieu tu ngay nao",
            "muc gia ay lay du lieu tu ngay nao",
            "muc gia nay lay du lieu tu ngay nao",
            "du lieu moi khong",
            "du lieu co moi khong",
            "thong tin moi khong",
            "cap nhat truc tiep",
            "cap nhat truc tiep khong",
            "co cap nhat truc tiep khong",
            "kiem tra truc tiep",
            "duoc kiem tra truc tiep",
            "kiem tra truc tiep hom nay",
            "duoc kiem tra truc tiep hom nay",
            "du lieu truc tiep",
            "real time",
            "real-time",
            "up to date",
            "bao gio cap nhat",
            "when was this updated",
            "when was it updated",
            "when was the data updated",
            "what date was the data recorded",
            "recorded on what date",
            "data freshness",
            "last updated",
            "updated when",
            "更新时间",
            "数据更新时间",
        ),
    )
    freshness_question = data_timestamp and _has_phrase(
        normalized,
        (
            "ngay nao",
            "hom nao",
            "luc nao",
            "bao gio cap nhat",
            "when was",
            "updated when",
            "cap nhat truc tiep",
            "kiem tra truc tiep",
            "duoc kiem tra truc tiep",
            "real time",
            "real-time",
            "up to date",
            "du lieu moi khong",
            "thong tin moi khong",
        ),
    )
    explicit_price_amount_question = _has_phrase(
        normalized,
        (
            "gia bao nhieu",
            "bao nhieu tien",
            "bao tien",
            "may dong",
            "how much",
            "what is the price",
            "what s the price",
            "whats the price",
        ),
    )
    if freshness_question and not explicit_price_amount_question:
        price = False

    fields: list[str] = []
    if price:
        fields.append("price_vnd")
    if stock:
        fields.append("stock")
    if shelf:
        fields.append("shelf")
    if promotion:
        fields.append("promotion")
    if expiry:
        fields.append("expiry_at")
    if ingredients:
        fields.append("ingredients")
    if spicy:
        fields.append("spicy")
    if data_timestamp:
        fields.append("data_timestamp")
    return tuple(fields)


_CATEGORY_CUES: dict[str, tuple[str, ...]] = {
    "snack": (
        "do an vat",
        "mon an vat",
        "an vat",
        "snack",
        "do an nhe",
        "an nhe",
        "mon an nhe",
        "qua vat",
        "do nham",
        "nham nhi",
        "mon nham nhi",
        "do lai rai",
        "an lai rai",
        "mon lai rai",
        "an choi",
        "mon an choi",
        "banh keo",
        "bim bim",
        "khoai tay chien",
        "do an khi xem phim",
        "an vat xem phim",
        "snacks",
        "crisps",
        "chips",
        "candy",
    ),
    "drink": (
        "do uong",
        "thuc uong",
        "nuoc uong",
        "chai nuoc",
        "nuoc nao",
        "nuoc yen",
        "nuoc mat",
        "giai khat",
        "nuoc suoi",
        "nuoc loc",
        "do uong lanh",
        "ca phe",
        "tra sua",
        "tra uong",
        "nuoc ngot",
        "uong gi",
        "khat nuoc",
        "khat",
        "drink",
        "drinks",
        "beverage",
        "beverages",
        "water",
        "soft drink",
        "soft drinks",
        "soda",
        "coffee",
        "tea",
        "milk tea",
        "thirsty",
    ),
    "dairy": (
        "sua chua",
        "sua tuoi",
        "sua",
        "yogurt",
        "yaourt",
        "pho mai",
        "dairy",
        "milk",
        "yoghurt",
        "cheese",
    ),
    "meal": (
        "bua an",
        "bua sang",
        "bua trua",
        "bua toi",
        "an sang",
        "an trua",
        "an toi",
        "mon an sang",
        "mon an trua",
        "mon an toi",
        "mi ly",
        "mi goi",
        "mi an lien",
        "do an nong",
        "do an nhanh",
        "an nhanh",
        "an no",
        "no bung",
        "an gi",
        "mon no",
        "mon chinh",
        "do an",
        "mon an",
        "meal",
        "meals",
        "food",
        "breakfast",
        "lunch",
        "dinner",
        "ready meal",
        "instant noodles",
        "noodles",
        "hot food",
        "hungry",
    ),
    "skincare": (
        "cham soc da",
        "cham soc da mat",
        "sua rua mat",
        "da kho",
        "dry skin",
        "da nhay cam",
        "sensitive skin",
        "da mun",
        "mun",
        "mun trung ca",
        "da dau",
        "oily skin",
        "duong da",
        "da mat",
        "skincare",
        "skin care",
        "skin-care",
        "facial skin care",
    ),
    "haircare": (
        "cham soc toc",
        "dau goi",
        "dau xa",
        "toc kho",
        "toc dau",
        "gau dau",
        "rung toc",
        "hair care",
        "haircare",
        "shampoo",
        "conditioner",
    ),
    "oral_care": (
        "cham soc rang mieng",
        "rang mieng",
        "kem danh rang",
        "ban chai danh rang",
        "nuoc suc mieng",
        "chi nha khoa",
        "oral care",
        "mouth care",
        "toothpaste",
        "toothbrush",
        "mouthwash",
    ),
    "home_cleaning": (
        "ve sinh nha cua",
        "ve sinh nha",
        "don dep nha",
        "lau nha",
        "rua chen bat",
        "rua bat",
        "nuoc lau san",
        "lau kinh",
        "lau bon cau",
        "tay rua bep",
        "household cleaning",
        "cleaning products",
        "household cleaning products",
    ),
    "laundry": (
        "giat giu quan ao",
        "giat quan ao",
        "do giat",
        "nuoc giat",
        "bot giat",
        "nuoc xa vai",
        "laundry detergent",
        "laundry",
    ),
    "baby": (
        "do cho em be",
        "do cho be",
        "do cho tre em",
        "do dung cho be",
        "cham soc be",
        "me va be",
        "em be",
        "tre em",
        "ta bim",
        "baby care",
        "baby",
        "kids",
        "children",
        "for kids",
        "for children",
    ),
    "cosmetic": (
        "my pham",
        "hoa my pham",
        "bong tay trang",
        "mieng bong tay trang",
        "cotton pad",
        "cotton pads",
        "cosmetic",
        "cosmetics",
        "makeup",
    ),
    "paper_goods": (
        "khan lau mieng",
        "khan an",
        "khan giay",
        "giay an",
        "giay lua",
        "giay rut",
        "paper napkin",
        "napkin",
        "tissue",
    ),
}

# Category cue tables are static. Normalize and compile them once so each
# request does not repeatedly normalize every cue and churn Python's regex
# cache while scanning the same phrases in several parser stages.
_CATEGORY_CUE_MATCHERS = tuple(
    (
        category,
        cue,
        " ".join(normalize_vietnamese(cue).split()),
    )
    for category, cues in _CATEGORY_CUES.items()
    for cue in cues
)
_NORMALIZED_CATEGORY_CUE_MATCHERS = tuple(
    (
        category,
        cue,
        cue_text,
        _boundary_phrase_pattern(cue_text),
    )
    for category, cue, cue_text in _CATEGORY_CUE_MATCHERS
    if cue_text
)
_RAW_CATEGORY_CUE_MATCHERS = tuple(
    (category, cue, _boundary_phrase_pattern(cue_text))
    for category, cue, cue_text in _CATEGORY_CUE_MATCHERS
    if cue_text
)

# These phrases describe the customer's use case or intended recipient. When a
# product department is also named (for example, laundry for children), keep
# the department as the hard catalog filter and retain the qualifier in the
# product query instead of switching to a broad baby or skincare category.
_CONTEXT_ONLY_CATEGORY_CUES: dict[str, frozenset[str]] = {
    "skincare": frozenset(
        {
            "da kho",
            "dry skin",
            "da nhay cam",
            "sensitive skin",
            "da mun",
            "mun",
            "mun trung ca",
            "da dau",
            "oily skin",
        }
    ),
    "haircare": frozenset({"toc kho", "toc dau", "gau dau", "rung toc"}),
    "baby": frozenset({"tre em", "em be", "baby", "kids", "children"}),
}


def product_type_attribute_modifier(text: str) -> tuple[str, str, int, int] | None:
    """Return the product group and category-only query for a flavor qualifier.

    In phrases such as ``bim bim vị phô mai``, the last category-looking phrase
    is a flavor, not the product department. Keep ``bim bim`` as the category
    signal while leaving the full utterance available to product retrieval.
    The returned offsets mark only the flavor clause in normalized text.
    """
    normalized = " ".join(normalize_vietnamese(text).split())
    if not normalized:
        return None

    category_spans: list[tuple[int, int, str, int]] = []
    for category, _cue, cue_text, pattern in _NORMALIZED_CATEGORY_CUE_MATCHERS:
        if (
            category == "dairy"
            and _cue in {"sua", "milk"}
            and any(
                phrase in normalized
                for phrase in ("ca phe sua", "tra sua", "coffee milk", "milk tea")
            )
        ):
            continue
        for match in pattern.finditer(normalized):
            category_spans.append(
                (match.start(), match.end(), category, len(cue_text.split()))
            )

    marker_pattern = re.compile(
        r"(?<!\w)(?:vi|huong|mui|flavor|flavour|taste)(?!\w)"
    )
    for marker in marker_pattern.finditer(normalized):
        preceding = [span for span in category_spans if span[1] <= marker.start()]
        following = [span for span in category_spans if span[0] >= marker.end()]
        if not preceding or not following:
            continue
        head = max(preceding, key=lambda span: (span[1], span[3]))
        flavor = min(following, key=lambda span: (span[0], -span[3]))
        connector_words = normalized[marker.end() : flavor[0]].split()
        if (
            marker.start() - head[1] > 60
            or flavor[0] - marker.end() > 32
            or len(connector_words) > 2
            or any(word in {"va", "voi", "nhung", "hoac", "and", "or", "but"}
                   for word in connector_words)
        ):
            continue
        category_query = (
            normalized[: marker.start()] + " " + normalized[flavor[1] :]
        ).strip()
        return head[2], " ".join(category_query.split()), marker.start(), flavor[1]
    return None


def explicit_recommendation_category(text: str) -> str | None:
    normalized = " ".join(normalize_vietnamese(text).split())
    is_banh_da_product = bool(re.search(r"\b(?:bánh\s+đa|banh\s+da)\b", text.casefold()))
    modifier = product_type_attribute_modifier(normalized)
    if modifier is not None:
        return modifier[0]
    named_query = explicit_recommendation_query(text)
    if named_query:
        matching_categories = {
            category
            for category, cue, pattern in _RAW_CATEGORY_CUE_MATCHERS
            if cue == named_query and pattern.search(normalized)
        }
        if len(matching_categories) == 1:
            return next(iter(matching_categories))
    # Resolve overlapping broad phrases by the most specific phrase. An
    # explicit "đồ ăn vặt" therefore wins over the shorter "đồ ăn" cue.
    matches = [
        (len(cue_text.split()), category, cue)
        for category, cue, cue_text, pattern in _NORMALIZED_CATEGORY_CUE_MATCHERS
        if pattern.search(normalized)
        and not (
            is_banh_da_product
            and category == "skincare"
            and cue in {"da kho", "da nhay cam", "da dau", "da mun"}
        )
        and not (
            category == "snack"
            and cue == "qua vat"
            and _has_phrase(normalized, ("qua vat chat",))
        )
    ]
    # Spoken Vietnamese often inserts a subject or verb between the need and
    # the body area ("da mình bị khô", "tóc nhanh bết"). Keep that as use-case
    # context for catalog lookup, without claiming any product is suitable.
    if re.search(
        r"\bda\b(?:\s+(?:cua\s+)?(?:mat|minh|toi|em|ban|rat|hoi|bi|dang)){0,3}"
        r"\s+(?:kho|dau|nhay cam|mun)\b",
        normalized,
    ) and not re.search(r"\b(?:bánh\s+đa|banh\s+da)\b", text.casefold()):
        matches.append((2, "skincare", "da kho"))
    if re.search(
        r"\btoc\b(?:\s+(?:cua\s+)?(?:minh|toi|em|ban|rat|hoi|bi|dang|nhanh)){0,3}"
        r"\s+(?:kho|dau|bet|gau|rung)\b",
        normalized,
    ):
        matches.append((2, "haircare", "toc dau"))
    if not matches:
        return None
    has_product_department = any(
        cue not in _CONTEXT_ONLY_CATEGORY_CUES.get(category, frozenset())
        for _length, category, cue in matches
    )
    if has_product_department:
        matches = [
            item
            for item in matches
            if item[2] not in _CONTEXT_ONLY_CATEGORY_CUES.get(item[1], frozenset())
        ]
    longest = max(length for length, _category, _cue in matches)
    categories = {
        category for length, category, _cue in matches if length == longest
    }
    return next(iter(categories)) if len(categories) == 1 else None


def recommendation_category_cues(category: str) -> tuple[str, ...]:
    """Return the phrases that explicitly name a supported recommendation group."""
    return _CATEGORY_CUES.get(category, ())


def explicit_recommendation_categories(text: str) -> list[str]:
    """Return explicitly named recommendation categories in the user's order."""
    normalized = " ".join(normalize_vietnamese(text).split())
    matches: list[tuple[int, int, str, str]] = []
    negated_matches: list[tuple[int, str]] = []

    def is_negated(start: int) -> bool:
        prefix = normalized[max(0, start - 48) : start]
        return bool(
            re.search(
                r"(?:\b(?:khong|dung|bo|tranh|without|except|no)\b"
                r"(?:\s+\w+){0,2}|\bdon\s+t\s+(?:want|need)\b)\s*$",
                prefix,
            )
        )

    for category, cue, cue_text, pattern in _NORMALIZED_CATEGORY_CUE_MATCHERS:
        if (
            category == "dairy"
            and cue in {"sua", "milk"}
            and any(
                phrase in normalized
                for phrase in ("ca phe sua", "tra sua", "coffee milk", "milk tea")
            )
        ):
            continue
        for match in pattern.finditer(normalized):
            if is_negated(match.start()):
                negated_matches.append((match.start(), category))
            else:
                matches.append((match.start(), match.end(), category, cue))

    modifier = product_type_attribute_modifier(normalized)
    if modifier is not None:
        _head_category, _category_query, modifier_start, modifier_end = modifier
        matches = [
            item for item in matches
            if not (item[0] >= modifier_start and item[1] <= modifier_end)
        ]

    # "nước" by itself is often part of a product name. Treat it as a drink
    # category only when the same request explicitly names another category.
    other_category_found = any(category != "drink" for _, _, category, _ in matches)
    if other_category_found:
        for match in re.finditer(r"(?<!\w)nuoc(?!\w)", normalized):
            if is_negated(match.start()):
                negated_matches.append((match.start(), "drink"))
            else:
                matches.append((match.start(), match.end(), "drink", "nuoc"))

    latest_positive = {
        category: max(item[0] for item in matches if item[2] == category)
        for category in {item[2] for item in matches}
    }
    latest_negative = {
        category: max(
            position for position, item_category in negated_matches if item_category == category
        )
        for category in {item_category for _, item_category in negated_matches}
    }
    matches = [
        item
        for item in matches
        if latest_negative.get(item[2], -1) <= latest_positive.get(item[2], -1)
    ]

    # Drop broad matches contained by a more specific phrase ("đồ ăn" inside
    # "đồ ăn vặt") and keep the most specific cue for each category/location.
    filtered = [
        item
        for item in matches
        if not any(
            other[0] <= item[0]
            and other[1] >= item[1]
            and (item[0], item[1]) != (other[0], other[1])
            and len(other[3].split()) > len(item[3].split())
            for other in matches
        )
    ]
    categories: list[str] = []
    for _, _, category, _ in sorted(filtered):
        if category not in categories:
            categories.append(category)
    return categories


_RECOMMENDATION_QUERY_CUES = (
    "ve sinh nha cua",
    "cham soc rang mieng",
    "cham soc toc",
    "cham soc da",
    "do cho em be",
    "do dung cho be",
    "giat quan ao",
    "household cleaning products",
    "household cleaning",
    "cleaning products",
    "baby care",
    "baby products",
    "hair care",
    "oral care",
    "laundry detergent",
    "da kho",
    "da nhay cam",
    "da dau",
    "da mun",
    "toc kho",
    "gau dau",
    "rung toc",
    "dry skin",
    "sensitive skin",
    "oily skin",
    "skin care",
    "skincare",
    "nuoc suoi",
    "nuoc loc",
    "nuoc ngot",
    "tra sua",
    "tra uong",
    "ca phe",
    "sua chua",
    "sua tuoi",
    "pho mai",
    "mi ly",
    "mi goi",
    "bim bim",
    "banh keo",
    "dau goi",
    "dau xa",
    "kem danh rang",
    "ban chai danh rang",
    "toothpaste",
    "toothbrush",
    "mouthwash",
    "shampoo",
    "conditioner",
    "nuoc suc mieng",
    "nuoc rua chen",
    "nuoc giat",
    "bot giat",
    "khan mat",
    "khan tam",
    "khan giay",
    "mat na",
    "milk tea",
    "sua rua mat",
    "kem duong da",
    "kem chong nang",
    "duong am",
    "serum",
    "face wash",
    "moisturizer",
    "sunscreen",
    "skincare",
    "skin care",
    "instant noodles",
    "soft drink",
    "soft drinks",
    "milk",
    "yogurt",
    "yoghurt",
    "cheese",
    "coffee",
    "tea",
    "water",
    "soda",
    "chips",
    "crisps",
    "noodles",
)

_RECOMMENDATION_CONTEXT_QUERY_CUES = frozenset(
    {
        "da kho",
        "da nhay cam",
        "da dau",
        "da mun",
        "toc kho",
        "toc dau",
        "gau dau",
        "rung toc",
        "dry skin",
        "sensitive skin",
        "oily skin",
    }
)


def explicit_recommendation_query(text: str) -> str | None:
    """Keep a clearly named subcategory as a catalog filter during consultation."""
    normalized = " ".join(normalize_vietnamese(text).split())
    is_banh_da_product = bool(re.search(r"\b(?:bánh\s+đa|banh\s+da)\b", text.casefold()))
    matches = [
        cue
        for cue in _RECOMMENDATION_QUERY_CUES
        if _has_phrase(normalized, (cue,))
        and not (
            is_banh_da_product
            and cue in {"da kho", "da nhay cam", "da dau", "da mun"}
        )
    ]
    product_type_matches = [
        cue for cue in matches if cue not in _RECOMMENDATION_CONTEXT_QUERY_CUES
    ]
    if product_type_matches:
        matches = product_type_matches
    return max(matches, key=len, default=None)


_RECOMMENDATION_CUES = (
    "gioi thieu",
    "de xuat",
    "goi y",
    "goi y giup",
    "tu van",
    "xin loi khuyen",
    "cho minh xin y kien",
    "chon giup",
    "chon ho",
    "chon dum",
    "tim giup",
    "tim mon",
    "tim do an",
    "coi giup",
    "xem giup",
    "bay tui",
    "bay minh",
    "chon cho minh",
    "chon mon",
    "nen mua",
    "nen chon",
    "mua gi",
    "tim",
    "co mon nao",
    "co loai nao",
    "co gi an",
    "co gi uong",
    "mi ly nao",
    "sua nao",
    "co mi ly nao",
    "loai nao",
    "loai nao hop",
    "mon nao",
    "mon nao hop",
    "cai nao",
    "cai nao hop",
    "co gi hop",
    "dang phan van",
    "phan van",
    "dang can nhac",
    "do uong nao",
    "muon mua",
    "can mua",
    "can mon",
    "can do an",
    "dang tim",
    "thich an",
    "thich uong",
    "muon an",
    "muon uong",
    "nham nhi",
    "mon an vat",
    "them mon",
    "uong gi",
    "an gi",
    "dang doi",
    "recommend",
    "recommendation",
    "recommendations",
    "suggest",
    "suggestion",
    "suggestions",
    "advise me",
    "help me choose",
    "help me pick",
    "help me find",
    "what should i buy",
    "what should i use",
    "what can i use",
    "what do i need for",
    "what do you recommend",
    "what would you recommend",
    "what do you suggest",
    "what would you suggest",
    "what should i get",
    "which one should i get",
    "which should i choose",
    "what goes well with",
    "what would go well with",
    "pair with",
    "what is a good",
    "what s a good",
    "any good",
    "looking for",
    "i need",
    "i want",
    "co san pham nao",
    "san pham nao",
    "co gi dung",
    "co gi su dung",
    "dung gi cho",
)


_CONSULTATION_FILLER_TOKENS = frozenset(
    {
        "a",
        "an",
        "um",
        "uhm",
        "neu",
        "hoi",
        "trong",
        "danh",
        "muc",
        "ay",
        "cac",
        "thi",
        "khoa",
        "bao",
        "ban",
        "be",
        "biet",
        "cai",
        "can",
        "cho",
        "choi",
        "den",
        "da",
        "kho",
        "chuan",
        "bi",
        "nhay",
        "cam",
        "dung",
        "nguoi",
        "khach",
        "sap",
        "bien",
        "chuyen",
        "ngay",
        "tuan",
        "tre",
        "con",
        "may",
        "dua",
        "sinh",
        "cuoi",
        "chay",
        "co",
        "dang",
        "de",
        "di",
        "do",
        "duoi",
        "em",
        "gi",
        "gia",
        "giup",
        "hai",
        "ho",
        "hop",
        "khong",
        "la",
        "loai",
        "ma",
        "minh",
        "mon",
        "mot",
        "muon",
        "mua",
        "nao",
        "nam",
        "nha",
        "nhe",
        "nhat",
        "nhieu",
        "o",
        "phan",
        "phu",
        "pho",
        "qua",
        "re",
        "sau",
        "sang",
        "the",
        "toi",
        "tot",
        "trua",
        "tui",
        "tuoi",
        "van",
        "voi",
        "y",
        "xem",
        "ngon",
        "nhu",
        "ba",
        "bon",
        "nam",
        "bay",
        "tam",
        "chin",
        "muoi",
        "chuc",
        "nghin",
        "ngan",
        "dong",
        "vnd",
        "k",
        "d",
        "gio",
        "luc",
        "nay",
        "kien",
        "xin",
        "hang",
        "dat",
        "duoc",
        "san",
        "pham",
        "ten",
        "ma",
        "id",
        "hot",
        "best",
        "seller",
        "popular",
        "ua",
        "chuong",
        "pho bien",
        "a",
        "an",
        "are",
        "friends",
        "visiting",
        "tonight",
        "there",
        "have",
        "five",
        "six",
        "us",
        "and",
        "spend",
        "around",
        "coming",
        "over",
        "guests",
        "family",
        "trip",
        "travel",
        "vacation",
        "beach",
        "camping",
        "picnic",
        "party",
        "birthday",
        "wedding",
        "moving",
        "apartment",
        "dry",
        "skin",
        "sensitive",
        "oily",
        "any",
        "anything",
        "buy",
        "can",
        "could",
        "do",
        "does",
        "for",
        "get",
        "good",
        "help",
        "i",
        "is",
        "it",
        "me",
        "my",
        "need",
        "of",
        "or",
        "please",
        "recommend",
        "some",
        "something",
        "suggest",
        "the",
        "to",
        "want",
        "we",
        "what",
        "which",
        "with",
        "would",
        "you",
        "your",
        "under",
        "below",
        "up",
        "at",
        "most",
        "less",
        "more",
        "than",
        "budget",
        "price",
        "non",
        "spicy",
        "mild",
        "hot",
        "not",
        "no",
        "m",
        "am",
        "drink",
        "drinks",
        "beverage",
        "beverages",
        "water",
        "soda",
        "coffee",
        "tea",
        "dairy",
        "milk",
        "yogurt",
        "yoghurt",
        "cheese",
        "meal",
        "meals",
        "food",
        "breakfast",
        "lunch",
        "dinner",
        "snack",
        "snacks",
        "crisps",
        "chips",
        "candy",
        "noodles",
        "kem",
        "thirsty",
        "hungry",
        # Generic goal, category and revision words are not product names.
        "va",
        "cau",
        "them",
        "vao",
        "nhom",
        "phuong",
        "ke",
        "hoach",
        "giu",
        "tinh",
        "day",
        "sach",
        "khac",
        "nhau",
        "giay",
        "vai",
        "vua",
        "uong",
        "chua",
        "bat",
        "nhung",
        "uu",
        "tien",
        "theo",
        "len",
        "buoc",
        "khoang",
        "tong",
        "nguyen",
        "cu",
        "so",
        "trai",
        "cham",
        "soc",
        "thanh",
        "du",
        "ep",
        "tu",
        "ca",
        "bua",
        "ve",
        "moi",
        "chi",
        "lai",
        "viec",
        "bo",
        "vat",
        "dau",
        "cung",
        "giam",
        "doi",
        "xuong",
        "chot",
        "dinh",
        "nang",
        "hoc",
        "banh",
        "tran",
        "nhan",
        "phai",
        "nen",
        "tru",
        "buoi",
        "ghi",
        "khoi",
        "bep",
        "nghi",
        "ra",
        "lo",
        "tham",
        "duong",
        "giat",
        "thuc",
        "nua",
        "chinh",
        "mang",
        "truoc",
        "noi",
        "sua",
        "don",
        "tap",
        "dieu",
        "lieu",
        "xac",
        "nuoc",
        "cuoc",
        "ro",
        "thu",
        "gon",
        "huong",
        "chia",
        "sam",
        "khang",
        "hoac",
        "phuc",
        "vu",
        "ngoai",
        "cong",
        "vien",
        "hieu",
        "can",
        "ho",
        "le",
        "tiec",
        "van",
        "phong",
        "hop",
        "truong",
        "con",
        "nho",
        "tre",
        "gio",
        "lam",
        "khach",
        "gia",
        "dinh",
        "dong",
        "nghiep",
        "chuyen",
        "lich",
        "que",
        "dua",
        "tiep",
        "lang",
        "cach",
        "dung",
        "muc",
        "tieu",
        "nguon",
        "ngan",
        "tien",
        "nguoi",
        "dem",
        "ban",
        "be",
        "kien",
        "troi",
        "sinh",
        "nhat",
        "mat",
        "phim",
        "hoa",
        "qua",
        "do",
        "an",
        "nhu",
        "vao",
        "the",
        "nao",
        "chua",
        "biet",
        "nhung",
        "gi",
        "san",
        "pham",
        "cay",
        "khi",
        "chon",
        "tang",
        "bong",
        "ni",
        "khao",
        "lua",
        "xep",
        "dai",
        "khan",
        "chieu",
        "tac",
        "ky",
        "thiet",
        "lop",
        "cua",
        "ghe",
        "gap",
        "hoan",
        "tro",
        "xa",
        "bang",
        "xe",
        "thoi",
        "mui",
        "gioi",
        "han",
        "roi",
        "trung",
        "gian",
        "dep",
        "ong",
        "mai",
        "phe",
        "tra",
        "yeu",
        "chat",
        "tay",
        "ngoi",
        "lien",
        "xom",
        "tau",
        "quan",
        "vi",
        "chup",
        "anh",
        "tiet",
        "hanh",
        "quen",
        "giai",
        "quyet",
        "nhanh",
        "chau",
        "dip",
        "trao",
        "dao",
        "tao",
        "keo",
        "tieng",
        "ket",
        "dan",
        "thao",
        "luan",
        "rieng",
        "thien",
        "thieu",
        "thue",
        "on",
        "hoat",
        "chu",
        "ben",
        "khu",
        "bai",
    }
)


def consultation_product_query(text: str) -> str:
    """Extract a named product or brand from a recommendation-style utterance."""
    normalized = " ".join(normalize_vietnamese(text).split())
    for cue in sorted(_RECOMMENDATION_CUES, key=len, reverse=True):
        normalized = re.sub(rf"(?<!\w){re.escape(cue)}(?!\w)", " ", normalized)
    query_words: list[str] = []
    seen: set[str] = set()
    for token in normalized.split():
        if (
            token in _CONSULTATION_FILLER_TOKENS
            or token.isdigit()
            or re.fullmatch(r"\d+(?:k|d|dong|vnd)", token)
            or (token in seen and not (token == "bim" and query_words[-1:] == ["bim"]))
        ):
            continue
        query_words.append(token)
        seen.add(token)
    return " ".join(query_words)


def has_specific_product_reference(text: str) -> bool:
    """Tell an open-ended advice request from one that names a product to inspect."""
    return bool(consultation_product_query(text))


def is_recommendation_negated(text: str) -> bool:
    normalized = " ".join(normalize_vietnamese(text).split())
    return _has_phrase(
        normalized,
        (
            "khong goi y",
            "khong can goi y",
            "khong can tu van",
            "dung goi y",
            "dung tu van",
            "dung tim",
            "dung chon",
            "khong tim",
            "khong can tim",
            "khong muon mua",
            "chua can mua",
            "khong can mua",
            "khong muon an",
            "khong muon uong",
            "do not recommend",
            "dont recommend",
            "don t recommend",
            "do not suggest",
            "dont suggest",
            "don t suggest",
            "no recommendations",
            "not looking for",
            "not looking to buy",
            "do not need a recommendation",
            "dont need a recommendation",
            "don t need a recommendation",
            "don t want recommendations",
            "dont want recommendations",
            "do not want recommendations",
        ),
    )


def is_alternative_product_request(text: str) -> bool:
    """Recognize a request for substitutes after an item is unavailable."""
    normalized = " ".join(normalize_vietnamese(text).split())
    if is_recommendation_negated(text):
        return False
    cues = (
        "neu chua co",
        "neu khong co",
        "neu het hang",
        "neu het",
        "neu khong tim thay",
        "nuoc nao khac",
        "mon nao khac",
        "lua chon khac",
        "loai nao khac",
        "san pham nao khac",
        "loai khac",
        "mon khac",
        "nao khac",
        "thay the",
        "tuong tu",
        "another option",
        "another one",
        "any other",
        "something else",
        "alternative",
        "if unavailable",
        "if out of stock",
        "similar product",
    )
    return _has_phrase(normalized, cues) or bool(
        re.search(
            r"\b(?:loai|san pham|mat hang|mon|hang)\s+(?:[a-z0-9]+\s+){0,4}khac\b"
            r"(?!\s+(?:gi|nhau|biet))",
            normalized,
        )
    )


def is_recommendation_request(text: str) -> bool:
    normalized = " ".join(normalize_vietnamese(text).split())
    if is_recommendation_negated(text):
        return False
    return (
        is_alternative_product_request(text)
        or _has_phrase(normalized, _RECOMMENDATION_CUES)
        or bool(re.search(r"(?<!giai )\bkhat(?:\s+nuoc)?\b", normalized))
    )


def recommendation_content(text: str) -> str:
    """Remove request phrases while retaining the customer's product needs."""
    content = f" {' '.join(normalize_vietnamese(text).split())} "
    for cue in sorted(_RECOMMENDATION_CUES, key=len, reverse=True):
        content = content.replace(f" {cue} ", " ")
    return " ".join(content.split())


def unsupported_catalog_attribute_requested(text: str) -> bool:
    """Catch product-use, nutrition, allergen and health/suitability questions."""
    normalized = " ".join(normalize_vietnamese(text).split())
    tokens = set(normalized.split())
    unsupported_phrases = (
        "loi ich",
        "cong dung",
        "tac dung",
        "de lam gi",
        "dung de lam gi",
        "dung the nao",
        "su dung the nao",
        "dung ra sao",
        "su dung ra sao",
        "cach dung",
        "cach su dung",
        "huong dan su dung",
        "how to use",
        "how should i use",
        "how to prepare",
        "how do i prepare",
        "how should i prepare",
        "pha the nao",
        "pha nhu the nao",
        "pha ra sao",
        "cach pha",
        "nau the nao",
        "nau nhu the nao",
        "nau ra sao",
        "cach nau",
        "bao quan nhu the nao",
        "co tot khong",
        "rung toc",
        "dry skin",
        "sensitive skin",
        "oily skin",
        "dry hair",
        "sensitive scalp",
        "tot cho suc khoe",
        "bo duong",
        "dinh duong",
        "calo",
        "lượng calo",
        "luong duong",
        "co it duong",
        "it duong khong",
        "chat beo",
        "co chat beo",
        "an chay duoc khong",
        "co phu hop an chay khong",
        "hop voi nguoi an chay",
        "hop nguoi an chay",
        "phu hop cho nguoi an chay",
        "phu hop voi nguoi an chay",
        "co an chay duoc khong",
        "gay di ung",
        "di ung",
        "an toan",
        "chat bao quan",
        "chat phu gia",
        "ngon khong",
        "co ngon khong",
        "co gi ngon",
        "ngon ra sao",
        "vi gi",
        "vi nhu nao",
        "co vi gi",
        "dam vi",
        "it ngot",
        "do ngot",
        "vi man",
        "ngot khong",
        "man khong",
        "allergy",
        "allergic",
        "peanut",
        "peanuts",
        "gluten",
        "lactose",
        "tieu duong",
        "dai thao duong",
        "cao huyet ap",
        "ba bau",
        "mang thai",
        "tre em",
        "tre so sinh",
        "child",
        "children",
        "kids",
        "cao tuoi",
        "nguoi cao tuoi",
        "an kieng",
        "nguoi an kieng",
        "tap gym",
        "nguoi tap gym",
        "tinh tao",
        "tinh ngu",
        "bo sung nang luong",
        "giam can",
        "tang can",
        "chua benh",
        "chua khoi",
        "tri dut diem",
        "khoi han",
        "dieu tri",
        "health",
        "healthy",
        "benefit",
        "benefits",
        "how to use",
        "what is it for",
        "what does it do",
        "what does this do",
        "what does this product do",
        "what is the purpose",
    )
    contextual_use_or_safety = bool(
        re.search(
            r"\b(?:dung|su dung|uong|an)\b.{0,35}\b(?:the nao|ra sao|co sao khong)\b",
            normalized,
        )
        or re.search(r"\bwhat does\b.{1,60}\bdo\b", normalized)
        or re.search(r"\bbao quan\b.{0,35}\b(?:ra sao|the nao|nhu the nao)\b", normalized)
        or re.search(
            r"\b(?:be|tre)\b.{0,35}\b(?:an|uong)\b.{0,25}\b(?:duoc khong|duoc chu)\b",
            normalized,
        )
        or re.search(
            r"\b(?:nau|pha|che bien)\b.{0,25}\b(?:kieu gi|the nao|ra sao)\b",
            normalized,
        )
    )
    personal_care_condition = bool(
        re.search(
            r"\b(?:da(?:\s+(?:mặt|mat|đầu|dau|dầu))?|tóc|toc|skin|hair|scalp)\b"
            r"(?:\s+(?:của|cua|mình|minh|tôi|toi|em|anh|chị|chi|bạn|ban|rất|rat|"
            r"hơi|hoi|nhanh|đang|dang|bị|bi|dễ|de)){0,5}"
            r"\s+(?:khô|kho|dầu|dau|nhạy\s+cảm|nhay\s+cam|mụn|mun|bết|bet|"
            r"gàu|gau|rụng|rung|hỗn\s+hợp|hon\s+hop)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    return (
        contextual_use_or_safety
        or personal_care_condition
        or _has_phrase(normalized, unsupported_phrases)
        or bool(
            re.search(
                r"\btoc\b(?:\s+(?:cua\s+)?(?:minh|toi|em|ban|rat|hoi|bi|dang|nhanh)){0,3}"
                r"\s+(?:kho|dau|bet|gau|rung)\b",
                normalized,
            )
        )
        or bool(
            tokens
            & {
                "kcal",
                "calorie",
                "calories",
                "protein",
                "sugar",
                "caffeine",
                "cafein",
                "allergy",
                "allergic",
            }
        )
    )
