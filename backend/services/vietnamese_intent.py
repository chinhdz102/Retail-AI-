"""Lightweight multilingual retail intent parsing for reliable tool routing.

This is deliberately a multi-label, high-recall parser for common spoken and
typed Vietnamese and English customer phrasing. It routes only facts supported by catalog tools; the
LLM still handles open-ended language and composition, while the API keeps the
final grounding and unsupported-attribute checks.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_vietnamese(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    normalized = normalized.replace("đ", "d").replace("_", " ")
    # Common keyboard/chat and speech-to-text spellings.
    replacements = (
        (r"\b(?:ko|khum|hong|hem)\b", "khong"),
        (r"\b(?:z|dzay|zay)\b", "vay"),
        # Common spoken and written regional variants, applied only where they
        # have a clear conversational meaning. Keep the rest of the utterance
        # intact so product names are not aggressively rewritten.
        (
            r"\b(ngon|chay|ban chay|dat hang|ua chuong|pho bien|hot|duoc mua nhieu)\s+nhut\b",
            r"\1 nhat",
        ),  # Southern colloquial spelling of "nhất", in ranking phrases only
        (r"\b(mon|cai|loai)\s+mo\b", r"\1 nao"),  # Central "mô" = which
        (r"\b(mon|cai|loai)\s+chi(?=\s+(?:ngon|hop|re|tot|duoc)\b)", r"\1 nao"),
        (r"\b(an|uong)\s+chi\b", r"\1 gi"),
        (r"\bmo(?=\s+(?:ngon|an|uong|ban|chay|hop|re|tot|duoc)\b)", "nao"),
        (r"\b(?:nhiu|nhieu)(?=\s+(?:tien|dong|vay|the|a|ban)\b)", "bao nhieu"),
        (r"\b(?:nhiu|nhieu)(?=\W*$)", "bao nhieu"),
        (r"\b(?:bao\s+nhiu|bao\s+nhieu)\b", "bao nhieu"),
        (r"\b(?:bn|bnh)\b", "bao nhieu"),
        (r"\b(?:hien\s+gio|bay\s+gio)\b", "hien tai"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    return re.sub(r"[^\w\s]", " ", normalized)


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) for phrase in phrases)


def requested_snapshot_fields(text: str) -> tuple[str, ...]:
    """Recognize supported catalog facts across colloquial Vietnamese paraphrases."""
    normalized = " ".join(normalize_vietnamese(text).split())
    tokens = set(normalized.split())

    nutrition = bool(
        tokens & {"calo", "kcal", "calorie", "calories", "protein", "sugar"}
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
    price = (
        _has_phrase(
            normalized,
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
    # A discount question is not a price lookup unless the customer also asks
    # for the actual price (e.g. "giá sau giảm còn bao nhiêu?").
    promotion = promotion or bool(re.search(r"\bmua\s+\w+\s+tang\s+\w+\b", normalized))
    promotion_stripped = normalized
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
            "co san",
            "con ban",
            "co ban khong",
            "con khong",
            "het chua",
            "con chua",
            "con bao nhieu",
            "con may",
            "con nhieu",
            "ton kho",
            "ton bao nhieu",
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
        re.search(r"\b(?:con|co)\s+(?:chai|lon|hop|goi|ly|cai)\b.{0,40}\bkhong\b", normalized)
        or re.search(r"\bcon\s+(?:chai|lon|hop|goi|ly|cai)\b.{0,20}\bnao\b", normalized)
        or re.search(r"\bmua duoc\b.{0,45}\bkhong\b", normalized)
        or re.search(r"\bco ban\b.{0,40}\bkhong\b", normalized)
        or re.search(r"\bco san\b.{0,40}\bkhong\b", normalized)
        or re.search(r"\bcon\s+\d+\s+(?:chai|lon|hop|goi|ly|cai)\b", normalized)
    )
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
        ),
    ) or bool(
        re.search(
            r"\b(?:nhan|ben trong|trong chai|trong hop|trong goi|trong san pham)\b"
            r".{0,30}\b(?:co|gom|chua)\b.{0,20}\b(?:gi|nhung gi)\b",
            normalized,
        )
    )
    if tokens & {
        "allergy", "allergic", "allergen", "allergens", "peanut", "peanuts",
        "gluten", "lactose", "diabetic", "diabetes", "pregnant", "vegan",
        "vegetarian", "sugar", "calorie", "calories", "protein", "kcal",
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
        "do lai rai",
        "an lai rai",
        "an choi",
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
        "sua chua", "sua tuoi", "sua", "yogurt", "yaourt", "pho mai", "dairy",
        "milk", "yoghurt", "cheese",
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
}


def explicit_recommendation_category(text: str) -> str | None:
    normalized = " ".join(normalize_vietnamese(text).split())
    # Resolve overlapping broad phrases by the most specific phrase. An
    # explicit "đồ ăn vặt" therefore wins over the shorter "đồ ăn" cue.
    matches = [
        (len(cue.split()), category)
        for category, cues in _CATEGORY_CUES.items()
        for cue in cues
        if _has_phrase(normalized, (cue,))
        and not (
            category == "snack"
            and cue == "qua vat"
            and _has_phrase(normalized, ("qua vat chat",))
        )
    ]
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    categories = {category for length, category in matches if length == longest}
    return next(iter(categories)) if len(categories) == 1 else None


_RECOMMENDATION_QUERY_CUES = (
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
    "milk tea",
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


def explicit_recommendation_query(text: str) -> str | None:
    """Keep a clearly named subcategory as a catalog filter during consultation."""
    normalized = " ".join(normalize_vietnamese(text).split())
    matches = [cue for cue in _RECOMMENDATION_QUERY_CUES if _has_phrase(normalized, (cue,))]
    return max(matches, key=len, default=None)


_RECOMMENDATION_CUES = (
    "goi y",
    "goi y giup",
    "tu van",
    "xin loi khuyen",
    "cho minh xin y kien",
    "chon giup",
    "chon ho",
    "chon dum",
    "tim giup",
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
    "dang tim",
    "thich an",
    "thich uong",
    "muon an",
    "muon uong",
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
)


_CONSULTATION_FILLER_TOKENS = frozenset(
    {
        "a", "an", "bao", "ban", "be", "biet", "cai", "can", "cho", "chay", "co", "dang",
        "de", "di", "do", "duoi", "em", "gi", "gia", "giup", "hai", "ho", "hop",
        "khong", "la", "loai", "ma", "minh", "mon", "mot", "muon", "mua", "nao", "nam",
        "nha", "nhe", "nhat", "nhieu", "o", "phan", "phu", "pho", "qua", "re", "sau", "sang",
        "the", "toi", "tot", "trua", "tui", "tuoi", "van", "voi", "y", "xem", "ngon",
        "nhu", "ba", "bon", "nam", "bay", "tam", "chin", "muoi", "chuc", "nghin",
        "ngan", "dong", "vnd", "k", "d", "gio", "luc", "nay", "kien", "xin", "hang",
        "dat", "duoc", "san", "pham", "ten", "ma", "id", "hot", "best", "seller",
        "popular", "ua", "chuong", "pho bien",
        "a", "an", "are", "any", "anything", "buy", "can", "could", "do", "does",
        "for", "get", "good", "help", "i", "is", "it", "me", "my", "need", "of",
        "or", "please", "recommend", "some", "something", "suggest", "the", "to",
        "want", "we", "what", "which", "with", "would", "you", "your",
        "under", "below", "up", "at", "most", "less", "more", "than", "budget",
        "price", "non", "spicy", "mild", "hot", "not", "no",
        "m", "am", "drink", "drinks", "beverage", "beverages", "water", "soda",
        "coffee", "tea", "dairy", "milk", "yogurt", "yoghurt", "cheese", "meal",
        "meals", "food", "breakfast", "lunch", "dinner", "snack", "snacks", "crisps",
        "chips", "candy", "noodles", "thirsty", "hungry",
    }
)


def consultation_product_query(text: str) -> str:
    """Extract a named product or brand from a recommendation-style utterance."""
    normalized = " ".join(normalize_vietnamese(text).split())
    for cue in sorted(_RECOMMENDATION_CUES, key=len, reverse=True):
        normalized = re.sub(rf"(?<!\w){re.escape(cue)}(?!\w)", " ", normalized)
    return " ".join(
        token
        for token in normalized.split()
        if token not in _CONSULTATION_FILLER_TOKENS
        and not token.isdigit()
        and not re.fullmatch(r"\d+(?:k|d|dong|vnd)", token)
    )


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


def is_recommendation_request(text: str) -> bool:
    normalized = " ".join(normalize_vietnamese(text).split())
    if is_recommendation_negated(text):
        return False
    return _has_phrase(normalized, _RECOMMENDATION_CUES) or bool(
        re.search(r"(?<!giai )\bkhat(?:\s+nuoc)?\b", normalized)
    )


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
        "cach dung",
        "cach su dung",
        "huong dan su dung",
        "cach pha",
        "cach nau",
        "bao quan nhu the nao",
        "co tot khong",
        "tot cho suc khoe",
        "bo duong",
        "dinh duong",
        "calo",
        "lượng calo",
        "luong duong",
        "it duong",
        "co it duong",
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
    return (
        contextual_use_or_safety
        or _has_phrase(normalized, unsupported_phrases)
        or bool(tokens & {"kcal", "calorie", "calories", "protein", "sugar", "allergy", "allergic"})
    )
