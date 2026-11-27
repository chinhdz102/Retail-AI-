from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from backend.data.catalog_import import analyze_dataset
from backend.products.catalog import (
    QUERY_STOP_WORDS,
    PriceOperator,
    ProductSnapshot,
    explicit_product_audience,
    matches_price_constraint,
    product_audience_evidence,
)
from backend.products.category_taxonomy import (
    catalog_query_with_aliases,
    collect_category_hierarchies,
    diversify_by_category_leaf,
    normalize_category_text,
)
from backend.products.planogram import recommended_shelf
from backend.products.postgres_repository import normalize_search_text
from backend.products.repository import (
    CatalogAdditions,
    catalog_brand_names,
    catalog_speech_terms,
)

_FILLER_WORDS = QUERY_STOP_WORDS | frozenset(
    {
        "loai",
        "cai",
        "mon",
        "gi",
        "nha",
        "nhe",
        "nhi",
        "vay",
        "size",
        "kich",
        "co",
        "may",
        "khac",
        "nhau",
        "vs",
        "versus",
        "container",
        "package",
        "packaging",
        "chung",
        "tao",
        "ve",
        "thi",
        "biet",
        "muon",
        "dang",
        "cung",
    }
)
_PACKAGING_WORDS = frozenset(
    {
        "chai",
        "lon",
        "hop",
        "goi",
        "tui",
        "thung",
        "loc",
        "can",
        "tuyp",
        "binh",
        "bich",
        "khay",
        "hu",
        "lo",
        "cuon",
        "vi",
        "ly",
    }
)
_SIZE_PREFERENCE_WORDS = frozenset({"nho", "mini", "small", "smaller"})
_LARGE_SIZE_PREFERENCE_WORDS = frozenset({"to", "dai", "large", "big", "larger"})
_PACKAGING_EQUIVALENTS: dict[str, frozenset[str]] = {
    "bich": frozenset({"bich", "tui", "goi"}),
    "jumbo": frozenset({"jumbo", "jumpo"}),
    "jumpo": frozenset({"jumpo", "jumbo"}),
}
_VIETNAMESE_TONE_MARKS = frozenset({"\u0300", "\u0301", "\u0303", "\u0309", "\u0323"})
_WORD_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_NATIVE_NO_COMPOUND_WORDS = frozenset(
    {
        "có",
        "cá",
        "cà",
        "cả",
        "là",
        "vị",
        "vì",
        "mà",
        "mã",
        "gì",
        "ở",
        "đâu",
        "đậu",
        "dầu",
        "bò",
        "bơ",
        "gà",
        "mì",
        "sữa",
        "trà",
        "kem",
        "bánh",
        "nước",
        "giá",
        "kệ",
        "hộp",
        "gói",
        "chai",
        "lon",
        "túi",
        "bịch",
        "cửa",
        "nhà",
        "cho",
        "bạn",
        "mình",
        "tôi",
        "thì",
        "như",
        "ấy",
        "mục",
        "danh",
        "nếu",
        "khi",
        "vậy",
        "thế",
        "sao",
        "với",
        "và",
        "để",
        "từ",
        "về",
        "trong",
        "ngoài",
        "trên",
        "dưới",
        "đang",
        "đã",
        "sẽ",
        "cũng",
        "vẫn",
        "chỉ",
        "không",
        "chưa",
        "được",
        "bị",
        "phải",
        "nên",
        "cần",
        "muốn",
        "thích",
        "hỏi",
        "xem",
        "tìm",
        "mua",
        "lấy",
        "chọn",
        "giúp",
        "nhé",
        "nha",
        "ạ",
        "nào",
        "này",
        "đó",
        "kia",
        "của",
        "các",
        "những",
        "một",
        "nhiều",
        "ít",
    }
)
_PROTECTED_QUERY_WORDS = _FILLER_WORDS | frozenset(
    {
        "nhung",
        "thanh",
        "phan",
        "cong",
        "dung",
        "tac",
        "nhieu",
        "tien",
        "gom",
        "trong",
        "ngoai",
        "duoi",
        "tren",
        "khoang",
        "ngan",
        "sach",
        "nghin",
        "trieu",
        "dong",
        "calo",
        "kcal",
        "protein",
        "duong",
        "khong",
        "tot",
        "khoe",
        "nguoi",
        "benh",
        "tieu",
        "chua",
        "biet",
        "thich",
        "muon",
        "chon",
        "giup",
        "tham",
        "khao",
        "danh",
        "muc",
        "cua",
        "hang",
        "san",
        "pham",
        "bnhiu",
        "bnhieu",
        "nhiu",
        "coi",
        "tam",
        "bn",
    }
)
_COMPARISON_PATTERN = re.compile(r"\b(?:khac\s+gi|khac\s+nhau|so\s+sanh|compare|comparison)\b")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_MEASURE_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(ml|milliliter|millilitre|lit|liter|litre|l|kg|g)\b"
)


_BABY_DEPARTMENT_SEGMENT = frozenset({"do", "dung", "cham", "soc", "me", "va", "be"})
_PACK_WORDS = frozenset({"thung", "loc", "combo"})
_MEASURE_TOKEN_RE = re.compile(r"(\d+)(ml|lit|l|kg|g|gr|gam)")
_BABY_QUERY_RE = re.compile(r"\b(?:tre em|em be|cho be|baby|kids|children)\b")


_MODIFIER_PREPOSITION_RE = re.compile(
    r"\b(?:vị|hương\s+vị|hương|chiết\s+xuất|tinh\s+chất|tinh\s+dầu|ruột|nhân)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class _Candidate:
    product: ProductSnapshot
    title: str
    brand: str
    category: str
    uom: str
    identifiers: frozenset[str]
    words: tuple[str, ...]
    title_words: frozenset[str]
    brand_words: frozenset[str]
    category_words: frozenset[str]
    category_segment_words: tuple[frozenset[str], ...]
    category_segment_texts: tuple[str, ...]
    uom_words: frozenset[str]
    native_words: frozenset[str]
    brand_native_words: frozenset[str]
    baby_audience: bool
    audience_evidence: str | None = None
    native_title_clean: str = ""
    folded_to_native: dict[str, tuple[str, ...]] = field(
        default_factory=dict, hash=False, compare=False
    )
    measure: tuple[float, str] | None = None
    has_pack_title_or_uom: bool = False
    has_pack_uom: bool = False
    is_baby_dept_parent: bool = False
    title_space_text: str = ""
    head_words: frozenset[str] = frozenset()
    lead_words: frozenset[str] = frozenset()
    modifier_only_words: frozenset[str] = frozenset()


@lru_cache(maxsize=32768)
def _vowel_skeleton(native_word: str) -> str:
    decomposed = unicodedata.normalize("NFD", native_word.casefold())
    stripped = "".join(ch for ch in decomposed if ch not in _VIETNAMESE_TONE_MARKS)
    return unicodedata.normalize("NFC", stripped)


@lru_cache(maxsize=16384)
def _phonetic_fold(word: str) -> str:
    w = word.casefold().strip()
    if not w or not w.isalpha():
        return w
    w = re.sub(r"onnaise$", "one", w)
    w = re.sub(r"ight", "ai", w)
    w = re.sub(r"igh", "ai", w)
    w = re.sub(r"buoy", "boi", w)
    w = re.sub(r"ew", "iu", w)
    w = re.sub(r"pie\b", "pai", w)
    w = re.sub(r"ie\b", "i", w)
    w = re.sub(r"ear\b", "ia", w)
    w = re.sub(r"own", "aon", w)
    w = re.sub(r"(?:ein|ain)", "en", w)
    w = re.sub(r"ph", "f", w)
    w = re.sub(r"qu", "kw", w)
    w = re.sub(r"ck", "k", w)
    w = w.replace("x", "s")
    w = re.sub(r"([a-z])\1+", r"\1", w)
    if len(w) >= 5 and re.search(r"[bcdfghjklmnpqrstvwxz]e$", w):
        w = w[:-1]
    w = w.replace("y", "i")
    w = re.sub(r"lk$", "l", w)
    w = re.sub(r"iu$", "il", w)
    w = re.sub(r"([aeiou])l$", r"\1n", w)
    w = re.sub(r"rt$", "t", w)
    w = re.sub(r"^c[aeo]l(?=[aeiou])", "cl", w)
    w = re.sub(r"c(?!h)", "k", w)
    return w


def _phonetic_variants(word: str) -> tuple[str, ...]:
    base = _phonetic_fold(word)
    if not base:
        return ()
    variants = [base]
    u_to_a = re.sub(r"^([bcdfghjklmnpqrstvwxz]+)u([mnlptk])", r"\1a\2", base)
    if u_to_a != base:
        variants.append(u_to_a)
    raw_lower = word.casefold().strip()
    # English -pie endings are often pronounced "-pi" in Vietnamese (e.g. Kewpie -> kiu pi).
    if raw_lower.endswith("pie") and base.endswith("pai"):
        variants.append(base[:-3] + "pi")
    # English -yne/-ine endings sound like Vietnamese "-ai" (e.g. Sensodyne -> sen so dai).
    if re.search(r"[yi]ne$", raw_lower) and len(base) >= 5:
        ai_form = re.sub(r"in$", "ai", base)
        if ai_form != base:
            variants.append(ai_form)
    # Vietnamese syllable phonology simplifies medial English "-st-" clusters to "-t-"
    # (e.g. Listerine -> lit-to-rin, Nestle -> net-le, Castrol -> kat-tron).
    medial_st = re.sub(r"([aeiou])st", r"\1t", base)
    if medial_st != base and len(base) >= 5:
        variants.append(medial_st)
    # Spoken Vietnamese frequently drops final English stop "-t" (e.g. Comfort -> com pho).
    if len(base) >= 5 and base.endswith("t"):
        variants.append(base[:-1])
    return tuple(dict.fromkeys(variants))


def _dehyphenated_tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    base = set(_tokens(value))
    extras: list[str] = []
    if "-" in value:
        merged = re.sub(r"(?<=[a-zA-Z0-9])-(?=[a-zA-Z0-9])", "", value)
        for t in _tokens(merged):
            if t not in base and t not in extras:
                extras.append(t)
    # Also index possessive stems (e.g. "Johnson's" -> "johnson", "Lay's" -> "lay")
    # alongside the folded apostrophe-free form ("johnsons", "lays").
    if "'" in value or "\u2019" in value:
        for match in re.finditer(r"([a-zA-Z]{3,})['\u2019]s\b", value):
            stem = match.group(1).casefold()
            if stem not in base and stem not in extras:
                extras.append(stem)
    return tuple(extras)


@lru_cache(maxsize=32768)
def _tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = normalize_search_text(value)
    normalized = re.sub(r"(?<=[a-z])['’](?=[a-z])", "", normalized)
    return tuple(_TOKEN_PATTERN.findall(normalized))


@lru_cache(maxsize=32768)
def _native_tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = unicodedata.normalize("NFC", value.casefold())
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _has_vietnamese_diacritic(value: str) -> bool:
    return "đ" in value or any(
        unicodedata.category(character) == "Mn"
        for character in unicodedata.normalize("NFD", value)
    )


@lru_cache(maxsize=128)
def _category_key(value: str) -> str:
    return normalize_search_text(value).strip()


_CATEGORY_PHRASE_ALIASES = {
    "drink": ("nuoc uong", "nuoc ngot", "nuoc suoi", "ca phe", "tra"),
    "snack": ("banh snack", "do an vat", "snack"),
    "dairy": ("sua", "sua chua", "pho mai"),
    "meal": ("thuc pham an lien", "thuc an san", "mi", "do an nong"),
    "skincare": ("cham soc da",),
    "haircare": (
        "cham soc toc",
        "dau goi",
        "dau xa",
        "dau goi tre em",
        "dau xa tre em",
    ),
    "oral_care": (
        "cham soc rang mieng",
        "kem danh rang",
        "ban chai danh rang",
        "nuoc suc mieng",
        "chi nha khoa",
    ),
    "home_cleaning": ("ve sinh nha",),
    "cosmetic": (
        "cham soc da",
        "cham soc toc",
        "cham soc rang mieng",
        "cham soc ca nhan",
        "cham soc suc khoe",
        "my pham",
        "my pham cham soc ca nhan",
        "oral care",
        "personal care",
    ),
    "diaper": ("ta bim", "ta quan", "giay quan"),
    "laundry": ("bot giat", "nuoc giat", "giat xa", "chat giat tay"),
}


def _category_alias_key(normalized: str) -> str:
    if normalized in _CATEGORY_PHRASE_ALIASES:
        return normalized
    underscored = normalized.replace(" ", "_")
    return underscored if underscored in _CATEGORY_PHRASE_ALIASES else normalized


@lru_cache(maxsize=128)
def _category_phrase_tokens(normalized: str) -> tuple[tuple[str, ...], ...]:
    alias_key = _category_alias_key(normalized)
    return tuple(
        _tokens(phrase)
        for phrase in _CATEGORY_PHRASE_ALIASES.get(alias_key, (normalized,))
        if _tokens(phrase)
    )


_DIACRITIC_PRODUCT_WORDS = frozenset(
    {
        "dầu",
        "đậu",
        "dâu",
        "trà",
        "ăn",
        "túi",
        "hội",
        "vị",
        "vỉ",
        "hàn",
        "ý",
        "số",
        "bao",
        "táo",
        "tảo",
        "tạo",
        "tỏi",
        "cá",
        "cà",
        "bò",
        "bơ",
        "gà",
        "mì",
        "đá",
        "ớt",
        "lê",
        "ổi",
        "mận",
        "dứa",
        "dưa",
        "bưởi",
        "vệ",
        "thị",
        "chưng",
        "đắng",
        "dạng",
        "cứng",
        "cưng",
    }
)
_TAO_PRODUCT_NEIGHBORS = frozenset(
    {
        "nuoc",
        "ep",
        "vi",
        "envy",
        "fuji",
        "do",
        "xanh",
        "my",
        "uc",
        "bien",
        "xoan",
        "kieu",
        "bot",
        "gion",
        "say",
        "mut",
        "dam",
        "giam",
        "nhap",
        "nho",
        "le",
        "cam",
    }
)
_DAU_PRODUCT_FOLLOWERS = frozenset(
    {
        "an",
        "goi",
        "xa",
        "hao",
        "me",
        "oliu",
        "o",
        "thuc",
        "dau",
        "nanh",
        "gao",
        "cai",
        "huong",
        "phong",
        "xanh",
        "do",
        "den",
        "tuong",
        "tam",
        "tay",
    }
)
_TRA_PRODUCT_FOLLOWERS = frozenset(
    {
        "xanh",
        "den",
        "o",
        "lai",
        "gung",
        "hoa",
        "tui",
        "hop",
        "goi",
        "ceylon",
        "dao",
        "chanh",
        "vai",
        "sua",
        "thao",
        "sen",
        "tan",
        "cuong",
        "dilmah",
        "lipton",
        "cozy",
        "phuc",
    }
)
_TRA_NON_PRODUCT_NEIGHBORS = frozenset(
    {"cuu", "loi", "kiem", "tien", "lai", "ve", "gop", "sau", "truoc"}
)
_RELAXABLE_BRAND_MODIFIERS = frozenset(
    {
        "nhat",
        "ban",
        "han",
        "quoc",
        "my",
        "uc",
        "phap",
        "duc",
        "thai",
        "lan",
        "viet",
        "thuong",
        "nguyen",
        "chinh",
        "nhap",
        "khau",
        "muot",
        "toc",
        "thom",
        "mat",
        "ngon",
        "sach",
        "tot",
    }
)


_CJK_RETAIL_PHRASES: tuple[tuple[str, str], ...] = (
    ("无糖鲜牛奶", " sữa tươi không đường "),
    ("无糖鲜奶", " sữa tươi không đường "),
    ("无糖牛奶", " sữa tươi không đường "),
    ("无糖酸奶", " sữa chua không đường "),
    ("无糖可乐", " coca zero "),
    ("无糖", " không đường "),
    ("低糖", " ít đường "),
    ("鲜牛奶", " sữa tươi "),
    ("鲜奶", " sữa tươi "),
    ("纯牛奶", " sữa tươi "),
    ("牛奶", " sữa "),
    ("酸奶", " sữa chua "),
    ("豆奶", " sữa đậu nành "),
    ("方便面", " mì ăn liền "),
    ("泡面", " mì ăn liền "),
    ("矿泉水", " nước suối "),
    ("纯净水", " nước suối "),
    ("可口可乐", " coca cola "),
    ("洗发水", " dầu gội "),
    ("沐浴露", " sữa tắm "),
    ("牙膏", " kem đánh răng "),
    ("洗衣液", " nước giặt "),
    ("纸巾", " giấy vệ sinh "),
    ("辣椒酱", " tương ớt "),
    ("辣酱", " tương ớt "),
    ("酱油", " nước tương "),
    ("鱼露", " nước mắm "),
    ("咖啡", " cà phê "),
    ("零食", " snack "),
    ("饮料", " nước ngọt "),
)
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]+")


def _translate_cjk_retail_terms(text: str, *, strip_leftover_cjk: bool = False) -> str:
    if not _CJK_CHAR_RE.search(text):
        return text
    out = text
    matched = False
    for zh_term, vi_term in _CJK_RETAIL_PHRASES:
        if zh_term in out:
            out = out.replace(zh_term, vi_term)
            matched = True
    if matched and strip_leftover_cjk:
        out = _CJK_CHAR_RE.sub(" ", out)
    return " ".join(out.split()) if matched else text


@lru_cache(maxsize=4096)
def _query_terms(query: str) -> tuple[tuple[str, ...], bool, bool]:
    translated_query = _translate_cjk_retail_terms(query, strip_leftover_cjk=True)
    # Fix common Vietnamese shopper typos & protect native homographs BEFORE accent folding
    # ("ăn vặt" != "thực vật", "đói" != "bộ đôi", "nhỏ" != "nho khô", "tương tự" != "tương ớt").
    translated_query = re.sub(r"\btương\s+ót\b", "tương ớt", translated_query, flags=re.I)
    translated_query = re.sub(r"\bsửa\s+tươi\b", "sữa tươi", translated_query, flags=re.I)
    translated_query = re.sub(r"\b(?:ko|k)\s+đường\b", "không đường", translated_query, flags=re.I)
    translated_query = re.sub(r"\b(?:đồ\s+)?ăn\s+vặt\b", "snack", translated_query, flags=re.I)
    translated_query = re.sub(r"\btương\s+tự\b", " ", translated_query, flags=re.I)
    translated_query = re.sub(r"\b(?:đang\s+)?đói\b", " ", translated_query, flags=re.I)
    translated_query = re.sub(
        r"\btổng(?:\s+(?:tiền|cộng|hết))?\b", " ", translated_query, flags=re.I
    )
    translated_query = re.sub(
        r"(?<!trái\s)(?<!quả\s)\bnhỏ(?=\s+(?:hơn|nhất|gọn|xíu)\b|\s*$|[.,?!])",
        " small ",
        translated_query,
        flags=re.I,
    )
    if _CJK_CHAR_RE.search(query):
        # Avoid turning Chinese "无糖鲜牛奶" into "zero" which only matches "Coca Zero" titles.
        translated_query = re.sub(r"\bkhông\s+đường\b", " ", translated_query, flags=re.I)
    normalized = normalize_search_text(translated_query).strip()
    normalized = re.sub(r"\b(?:less|more)\s+than\b", " ", normalized)
    explicit_diaper = bool(re.search(r"\b(?:bỉm|tã\s+quần)\b", query.casefold()))
    comparison = bool(_COMPARISON_PATTERN.search(normalized)) or bool(
        re.search(r"\b(?:vs|versus)\b", normalized)
    )
    # Preserve Vietnamese product words whose ASCII fold collides with stop words
    # (e.g. dầu/đậu/dâu vs đâu, trà vs tra cứu, ăn vs English "an", túi vs tui).
    protected_tokens: set[str] = set()
    for native_word in _native_tokens(query):
        if native_word in _DIACRITIC_PRODUCT_WORDS:
            folded = _tokens(native_word)
            if folded:
                protected_tokens.add(folded[0])
    raw_plain_tokens = _tokens(normalized)
    for idx, tok in enumerate(raw_plain_tokens):
        prev_tok = raw_plain_tokens[idx - 1] if idx > 0 else ""
        next_tok = raw_plain_tokens[idx + 1] if idx + 1 < len(raw_plain_tokens) else ""
        if tok == "dau" and (
            next_tok in _DAU_PRODUCT_FOLLOWERS
            or prev_tok in {"sua", "banh", "keo", "hat", "vi", "kem", "nuoc", "tuong"}
        ):
            protected_tokens.add("dau")
        elif tok == "an" and (
            prev_tok in {"dau", "mi", "banh", "khan", "giay", "do", "mon", "chao", "sot", "xot"}
            or next_tok in {"lien", "kieng", "vat", "sang", "dam"}
        ):
            protected_tokens.add("an")
        elif tok == "tra" and (
            prev_tok not in _TRA_NON_PRODUCT_NEIGHBORS
            and next_tok not in _TRA_NON_PRODUCT_NEIGHBORS
            and (len(raw_plain_tokens) <= 4 or next_tok in _TRA_PRODUCT_FOLLOWERS)
        ):
            protected_tokens.add("tra")
        elif tok == "y" and prev_tok in {"mi", "sot", "xot"}:
            protected_tokens.add("y")
        elif tok == "so" and next_tok.isdigit():
            protected_tokens.add("so")
        elif tok == "bao" and prev_tok == "banh":
            protected_tokens.add("bao")
        elif tok == "hoi" and prev_tok == "lo":
            protected_tokens.add("hoi")
        elif tok == "ve" and next_tok == "sinh":
            protected_tokens.add("ve")
        elif tok == "thi" and next_tok == "la":
            protected_tokens.add("thi")
        elif tok == "chung" and prev_tok == "banh":
            protected_tokens.add("chung")
        elif tok == "biet" and prev_tok == "dac":
            protected_tokens.add("biet")
        elif tok == "tao" and (
            prev_tok in _TAO_PRODUCT_NEIGHBORS or next_tok in _TAO_PRODUCT_NEIGHBORS
        ):
            protected_tokens.add("tao")
        elif tok == "dang" and (
            prev_tok in {"socola", "chocolate", "muop", "kho", "qua", "ca", "phe"}
            or next_tok in {"goi", "chai", "bot", "long", "vien", "tui", "hop"}
        ):
            protected_tokens.add("dang")
        elif tok == "cung" and prev_tok in {"keo", "thu", "long"}:
            protected_tokens.add("cung")
        elif tok == "san" and prev_tok == "lau":
            protected_tokens.add("san")
        elif tok in {"my", "mi"} and prev_tok == "banh":
            protected_tokens.add(tok)
        elif tok == "tui" and (
            next_tok in {"lon", "nho", "to", "cham", "refill"}
            or (bool(next_tok) and next_tok[0].isdigit())
        ):
            protected_tokens.add("tui")
    normalized = re.sub(r"\bbon\s+(?:minh|tao)\b", " ", normalized)
    normalized = re.sub(r"\bkhong\s+duong\b", "zero", normalized)
    normalized = re.sub(
        r"\b(?:sugar[\s-]*free|no\s+sugar|without\s+sugar)\b",
        "zero",
        normalized,
    )
    normalized = re.sub(r"\b(?:kids?|children?)\b", "be", normalized)
    snack_slang = not explicit_diaper and bool(
        re.search(r"\bbim\s+bim\b|\bbimbim\b", normalized)
    )
    if explicit_diaper:
        normalized = re.sub(r"\bbim\b", "ta", normalized)
    else:
        normalized = re.sub(r"\bbim\s+bim\b|\bbimbim\b", "snack", normalized)
    normalized = re.sub(
        r"\b(\d+)\s+(ml|lit|l|kg|g|gr|gam)\b",
        r"\1\2",
        normalized,
    )
    terms = [
        token
        for token in _tokens(normalized)
        if token not in _FILLER_WORDS or token in protected_tokens
    ]
    return tuple(terms), comparison, snack_slang


def _token_lookup_variants(token: str) -> tuple[str, ...]:
    variants = list(_PACKAGING_EQUIVALENTS.get(token, (token,)))
    if re.fullmatch(r"\d+l", token):
        variants.append(f"{token}it")
    elif re.fullmatch(r"\d+lit", token):
        variants.append(token[:-2])
    return tuple(dict.fromkeys(variants))


@dataclass(frozen=True)
class _PreparedTerm:
    token: str
    variants: tuple[str, ...]
    measure_digits: str | None
    measure_unit_aliases: tuple[str, ...]
    short_alpha_prefix: bool
    is_digit: bool
    accent_variants: frozenset[str]
    accent_skeletons: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedQuery:
    normalized: str
    native_query_clean: str
    query_has_diacritics: bool
    terms: tuple[str, ...]
    prepared_terms: tuple[_PreparedTerm, ...]
    comparison: bool
    snack_slang: bool
    requires_baby_audience: bool
    accented_variant_sets: tuple[frozenset[str], ...]
    wants_small: bool
    wants_large: bool
    wants_pack: bool
    core_indices: tuple[int, ...]
    numeric_indices: tuple[int, ...]
    explicit_packaging_indices: tuple[int, ...]
    required_terms_count: int


@lru_cache(maxsize=2048)
def _prepare_query(query: str) -> _PreparedQuery:
    normalized = normalize_search_text(query).strip().casefold()
    native_query_clean = unicodedata.normalize("NFC", query.strip().casefold())
    query_has_diacritics = _has_vietnamese_diacritic(native_query_clean)
    terms, comparison, snack_slang = _query_terms(query)
    requires_baby_audience = bool(_BABY_QUERY_RE.search(normalized)) if terms else False
    accented_map: dict[str, set[str]] = defaultdict(set)
    native_query_words: set[str] = set()
    for native_word in _native_tokens(query):
        native_query_words.add(native_word)
        if _has_vietnamese_diacritic(native_word):
            folded = _tokens(native_word)
            if folded:
                accented_map[folded[0]].add(native_word)
    prepared_terms: list[_PreparedTerm] = []
    for token in terms:
        variants = _token_lookup_variants(token)
        measure_match = _MEASURE_TOKEN_RE.fullmatch(token)
        if measure_match:
            digits, unit = measure_match.groups()
            unit_aliases: tuple[str, ...] = ("l", "lit") if unit in {"l", "lit"} else (unit,)
        else:
            digits = None
            unit_aliases = ()
        acc_set = frozenset(accented_map.get(token, ()))
        acc_skels = tuple(_vowel_skeleton(v) for v in acc_set)
        prepared_terms.append(
            _PreparedTerm(
                token=token,
                variants=variants,
                measure_digits=digits,
                measure_unit_aliases=unit_aliases,
                short_alpha_prefix=token.isalpha() and len(token) <= 3,
                is_digit=token.isdigit(),
                accent_variants=acc_set,
                accent_skeletons=acc_skels,
            )
        )
    has_packaging_followed_by_lon = any(
        left in (_PACKAGING_WORDS - {"lon"}) and right == "lon"
        for left, right in zip(terms, terms[1:], strict=False)
    )
    wants_small = any(word in _SIZE_PREFERENCE_WORDS for word in terms)
    wants_large = (
        "lớn" in native_query_words
        or has_packaging_followed_by_lon
        or any(word in _LARGE_SIZE_PREFERENCE_WORDS for word in terms)
    )
    wants_pack = any(word in _PACK_WORDS for word in terms)
    lon_as_large = (
        {"lon"} if ("lớn" in native_query_words or has_packaging_followed_by_lon) else set()
    )
    large_size_tokens = _LARGE_SIZE_PREFERENCE_WORDS | lon_as_large
    effective_packaging = _PACKAGING_WORDS - lon_as_large
    core_indices = tuple(
        index
        for index, token in enumerate(terms)
        if len(token) >= 3
        and not token[0].isdigit()
        and token not in effective_packaging
        and token not in _SIZE_PREFERENCE_WORDS
        and token not in large_size_tokens
    )
    numeric_indices = tuple(
        index for index, token in enumerate(terms) if token[0].isdigit()
    )
    explicit_packaging_indices = tuple(
        index for index, token in enumerate(terms) if token in effective_packaging
    )
    required_terms_count = (
        sum(
            1
            for token in terms
            if token not in _SIZE_PREFERENCE_WORDS and token not in large_size_tokens
        )
        or len(terms)
    )
    return _PreparedQuery(
        normalized=normalized,
        native_query_clean=native_query_clean,
        query_has_diacritics=query_has_diacritics,
        terms=terms,
        prepared_terms=tuple(prepared_terms),
        comparison=comparison,
        snack_slang=snack_slang,
        requires_baby_audience=requires_baby_audience,
        accented_variant_sets=tuple(frozenset(v) for v in accented_map.values()),
        wants_small=wants_small,
        wants_large=wants_large,
        wants_pack=wants_pack,
        core_indices=core_indices,
        numeric_indices=numeric_indices,
        explicit_packaging_indices=explicit_packaging_indices,
        required_terms_count=required_terms_count,
    )


def _prepared_word_matches(pterm: _PreparedTerm, words: frozenset[str]) -> bool:
    if pterm.token in words:
        return True
    if len(pterm.variants) > 1 and not words.isdisjoint(pterm.variants):
        return True
    if pterm.measure_digits is not None:
        if pterm.measure_digits in words and any(
            alias in words for alias in pterm.measure_unit_aliases
        ):
            return True
    if pterm.short_alpha_prefix:
        prefix = pterm.token
        plen = len(prefix)
        return any(word.startswith(prefix) and word[plen:].isdigit() for word in words)
    if pterm.is_digit:
        prefix = pterm.token
        plen = len(prefix)
        return any(word.startswith(prefix) and word[plen:].isalpha() for word in words)
    return False


def _word_matches(token: str, words: frozenset[str]) -> bool:
    if token in words:
        return True
    variants = _token_lookup_variants(token)
    if len(variants) > 1 and not words.isdisjoint(variants):
        return True
    measure_match = _MEASURE_TOKEN_RE.fullmatch(token)
    if measure_match:
        digits, unit = measure_match.groups()
        unit_aliases = ("l", "lit") if unit in {"l", "lit"} else (unit,)
        if digits in words and any(alias in words for alias in unit_aliases):
            return True
    if token.isalpha() and len(token) <= 3:
        return any(word.startswith(token) and word[len(token) :].isdigit() for word in words)
    if token.isdigit():
        return any(word.startswith(token) and word[len(token) :].isalpha() for word in words)
    return False


def _score_prepared(
    pq: _PreparedQuery,
    candidate: _Candidate,
    known_words: frozenset[str],
    known_native_words: frozenset[str] = frozenset(),
) -> float:
    normalized = pq.normalized
    if normalized in candidate.identifiers:
        return 1000.0
    native_title_clean = candidate.native_title_clean or unicodedata.normalize(
        "NFC", candidate.product.name.strip().casefold()
    )
    if normalized and normalized == candidate.title and (
        not pq.query_has_diacritics or pq.native_query_clean == native_title_clean
    ):
        return 900.0
    if normalized and candidate.title.startswith(normalized) and (
        not pq.query_has_diacritics or native_title_clean.startswith(pq.native_query_clean)
    ):
        return 800.0

    terms = pq.terms
    if not terms:
        return 0.0
    if pq.requires_baby_audience and not candidate.baby_audience:
        return 0.0

    title_words = candidate.title_words
    brand_words = candidate.brand_words
    category_words = candidate.category_words
    uom_words = candidate.uom_words
    cand_native_words = candidate.native_words
    folded_to_native = candidate.folded_to_native

    term_scores: list[float] = []
    title_hits = 0
    for pterm in pq.prepared_terms:
        accent_collision = False
        if pterm.accent_variants and cand_native_words.isdisjoint(pterm.accent_variants):
            candidate_same_folded = folded_to_native.get(pterm.token)
            if candidate_same_folded is None and not folded_to_native:
                candidate_same_folded = tuple(
                    nw
                    for nw in cand_native_words
                    for f in (_tokens(nw),)
                    if f and f[0] == pterm.token
                )
            if candidate_same_folded:
                accent_collision = True
                for variant, v_skel in zip(
                    pterm.accent_variants, pterm.accent_skeletons, strict=True
                ):
                    unknown_variant = bool(known_native_words) and variant not in known_native_words
                    for cand_native in candidate_same_folded:
                        if v_skel == _vowel_skeleton(cand_native) and (
                            unknown_variant or cand_native in candidate.brand_native_words
                        ):
                            accent_collision = False
                            break
                    if not accent_collision:
                        break
        if accent_collision:
            term_scores.append(0.0)
            continue
        if _prepared_word_matches(pterm, title_words):
            title_hits += 1
            term_scores.append(3.0)
        elif _prepared_word_matches(pterm, brand_words):
            term_scores.append(2.4)
        elif _prepared_word_matches(pterm, uom_words):
            term_scores.append(1.8)
        elif _prepared_word_matches(pterm, category_words):
            term_scores.append(1.3)
        else:
            term_scores.append(0.0)

    exact_core_matches = sum(1 for index in pq.core_indices if term_scores[index] > 0)
    if exact_core_matches >= 1:
        field_words_3 = (title_words, brand_words, category_words)
        field_weights_3 = (3.0, 2.4, 1.3)
        for index, pterm in enumerate(pq.prepared_terms):
            token = pterm.token
            if term_scores[index] or len(token) < 4 or pterm.is_digit:
                continue
            if token in known_words:
                continue
            fuzzy_score = max(
                (
                    SequenceMatcher(None, token, word).ratio() * weight
                    for words, weight in zip(field_words_3, field_weights_3, strict=True)
                    for word in words
                    if len(word) >= 4
                ),
                default=0.0,
            )
            if fuzzy_score >= 2.16:
                term_scores[index] = fuzzy_score

    if pq.numeric_indices:
        if pq.comparison and len(pq.numeric_indices) > 1:
            if max((term_scores[i] for i in pq.numeric_indices), default=0.0) == 0:
                return 0.0
        elif any(term_scores[i] == 0 for i in pq.numeric_indices):
            return 0.0

    if pq.explicit_packaging_indices and not pq.comparison:
        if any(term_scores[i] == 0 for i in pq.explicit_packaging_indices):
            return 0.0
    if any(term_scores[i] == 0 for i in pq.core_indices):
        return 0.0

    matched = sum(1 for s in term_scores if s > 0)
    if matched == 0 or matched / pq.required_terms_count < 0.5:
        return 0.0
    score = sum(term_scores) - 1.5 * (len(terms) - matched)
    if pq.accented_variant_sets:
        native_exact_hits = sum(
            1
            for variants in pq.accented_variant_sets
            if not cand_native_words.isdisjoint(variants)
        )
        score += 1.5 * native_exact_hits
    if title_hits >= 2:
        score += 2.0
    # Vietnamese head-noun vs flavor/scent modifier disambiguation:
    # A product whose leading head noun matches the query ("Cam Sành", "Chanh có hạt")
    # must outrank products where the term only appears after a flavor/scent/part marker
    # ("Dưa lưới ruột cam", "Bánh cracker vị rau", "Sữa trái cây hương nho").
    if terms and candidate.lead_words and not pq.snack_slang:
        if any(t in candidate.lead_words for t in terms):
            score += 1.8
        elif (
            candidate.modifier_only_words
            and all(
                t in candidate.modifier_only_words
                for t in terms
                if t in title_words
            )
            and any(t in candidate.modifier_only_words for t in terms)
            and not any(t in candidate.head_words for t in terms)
        ):
            score -= 2.5
    if len(terms) >= 2 and candidate.title_space_text:
        leaf_cat = candidate.category_segment_texts[-1] if candidate.category_segment_texts else ""
        non_phrase_words = (
            _PACKAGING_WORDS | _SIZE_PREFERENCE_WORDS | _LARGE_SIZE_PREFERENCE_WORDS
        )
        prefix_boosted = False
        cat_boosted = False
        for idx in range(len(terms) - 1):
            t1, t2 = terms[idx], terms[idx + 1]
            if (
                t1.isdigit()
                or t2.isdigit()
                or t1 in non_phrase_words
                or t2 in non_phrase_words
            ):
                continue
            bigram = f"{t1} {t2}"
            if not prefix_boosted and candidate.title_space_text.startswith(bigram):
                score += 0.5
                prefix_boosted = True
            if not cat_boosted and leaf_cat and (
                leaf_cat == bigram or f" {bigram} " in f" {leaf_cat} "
            ):
                score += 0.6
                cat_boosted = True
    if pq.snack_slang and "snack" in category_words:
        score += 5.0
        if "bim" in title_words:
            score += 2.0
    if "banh" in terms and "bot" not in terms:
        cat_joined = " ".join(candidate.category_segment_texts)
        if "bot" in title_words and (
            candidate.title_space_text.startswith("bot ")
            or "bot nau an" in cat_joined
        ):
            score -= 6.0
        if ("mi" in terms or "my" in terms) and (
            "banh mi" in candidate.title_space_text
            or "banh my" in candidate.title_space_text
            or "sandwich" in candidate.title_space_text
        ):
            score += 4.5

    if pq.wants_small:
        preferred_measure = (
            candidate.measure if candidate.measure is not None else _measure(candidate.title)
        )
        if preferred_measure is not None:
            amount, _dimension = preferred_measure
            score += max(-3.0, min(3.0, 3.0 * (1000.0 - amount) / 1000.0))
            if candidate.has_pack_title_or_uom or not (title_words | uom_words).isdisjoint(
                _PACK_WORDS
            ):
                score -= 3.0
    elif pq.wants_large:
        preferred_measure = (
            candidate.measure if candidate.measure is not None else _measure(candidate.title)
        )
        if preferred_measure is not None:
            amount, _dimension = preferred_measure
            score += max(-2.0, min(3.0, 3.0 * (amount - 500.0) / 3000.0))
    elif not pq.wants_pack:
        if candidate.has_pack_uom or not uom_words.isdisjoint(_PACK_WORDS):
            score -= 5.0
    return max(score, 0.1)


def _score(
    query: str,
    candidate: _Candidate,
    known_words: frozenset[str],
    known_native_words: frozenset[str] = frozenset(),
) -> float:
    return _score_prepared(_prepare_query(query), candidate, known_words, known_native_words)


def _measure(title: str) -> tuple[float, str] | None:
    match = _MEASURE_PATTERN.search(title)
    if match is None:
        return None
    amount = float(match.group(1).replace(",", "."))
    unit = match.group(2)
    if unit in {"l", "lit", "liter", "litre"}:
        return amount * 1000, "volume"
    if unit in {"ml", "milliliter", "millilitre"}:
        return amount, "volume"
    if unit == "kg":
        return amount * 1000, "mass"
    return amount, "mass"


@dataclass(frozen=True)
class _PreparedCategoryFilter:
    mode: str  # "none" | "baby" | "exact_taxonomy" | "phrases"
    taxonomy_key: str = ""
    is_oral_care: bool = False
    phrase_needles: tuple[str, ...] = ()


def _prepare_category_filter(
    category: str | None,
    known_category_keys: frozenset[str],
) -> _PreparedCategoryFilter:
    if not category:
        return _PreparedCategoryFilter("none")
    normalized = _category_key(category)
    if normalized == "baby":
        return _PreparedCategoryFilter("baby")
    alias_key = _category_alias_key(normalized)
    taxonomy_key = normalize_category_text(category)
    if taxonomy_key in known_category_keys and alias_key not in _CATEGORY_PHRASE_ALIASES:
        return _PreparedCategoryFilter("exact_taxonomy", taxonomy_key=taxonomy_key)
    phrase_needles = tuple(
        f" {' '.join(phrase)} " for phrase in _category_phrase_tokens(normalized)
    )
    return _PreparedCategoryFilter(
        "phrases",
        is_oral_care=(normalized == "oral_care"),
        phrase_needles=phrase_needles,
    )


def _prepared_category_matches(
    cat_filter: _PreparedCategoryFilter,
    candidate: _Candidate,
) -> bool:
    mode = cat_filter.mode
    if mode == "none":
        return True
    if mode == "exact_taxonomy":
        return cat_filter.taxonomy_key in candidate.category_segment_texts
    if mode == "baby":
        return candidate.baby_audience or candidate.is_baby_dept_parent
    segments = candidate.category_segment_texts
    if cat_filter.is_oral_care and candidate.is_baby_dept_parent:
        fallback_title = candidate.title_space_text or " ".join(_tokens(candidate.product.name))
        segments = (*segments, fallback_title)
    for segment in segments:
        padded = f" {segment} "
        for needle in cat_filter.phrase_needles:
            if needle in padded:
                return True
    return False


def _category_matches(
    category: str | None,
    candidate: _Candidate,
    known_category_keys: frozenset[str] = frozenset(),
) -> bool:
    return _prepared_category_matches(
        _prepare_category_filter(category, known_category_keys), candidate
    )


class SnapshotProductRepository:
    """Read-only product catalog sourced from a validated local JSON snapshot.

    Product prices, recorded stock, and recommended shelf locations remain
    snapshots. This repository never connects to a live transaction system.
    """

    source_name = "thanh_lam_json_snapshot"
    supports_current_stock = False
    supports_spicy_filter = False
    supports_price_sort = True

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        rows, quarantined, report = analyze_dataset(self.path)
        if report.get("valid") is not True:
            raise ValueError("The local product snapshot failed schema validation")
        quarantined_ids = {item["product_id"] for item in quarantined}
        dataset_version = str(report["dataset_version"])
        products: list[ProductSnapshot] = []
        for row in rows:
            if row.product_id in quarantined_ids:
                continue
            timestamp = datetime.fromisoformat(row.scraped_at.replace("Z", "+00:00"))
            shelf = recommended_shelf(row.product_id, dataset_version)
            effective_price = min(row.price_vnd, row.sale_price_vnd)
            promotion = row.discount_promotion if row.sale_price_vnd < row.price_vnd else None
            products.append(
                ProductSnapshot(
                    product_id=row.product_id,
                    name=row.name,
                    category=row.category_name or row.sub_department or row.department or "",
                    price_vnd=effective_price,
                    stock=row.stock,
                    stock_is_current=False,
                    shelf=shelf,
                    shelf_source="RECOMMENDED_PLANOGRAM" if shelf else "UNKNOWN",
                    promotion=promotion,
                    spicy=None,
                    ingredients=None,
                    expiry_at=None,
                    data_timestamp=timestamp,
                    brand=row.brand,
                    item_no=row.item_no,
                    barcode=row.barcode,
                    category_code=row.category_code,
                    department=row.department,
                    sub_department=row.sub_department,
                    category_hierarchy=row.category_hierarchy,
                    uom=row.uom,
                    image_url=row.image_url,
                    description=row.description,
                    seo_url=row.seo_url,
                    list_price_vnd=row.price_vnd,
                    sale_price_vnd=row.sale_price_vnd,
                    price_quality_status=(
                        "SALE" if row.sale_price_vnd < row.price_vnd else "NORMAL"
                    ),
                    catalog_version=dataset_version,
                )
            )
        self.products = tuple(products)
        self._speech_brand_names = catalog_brand_names(self.products, limit=1000)
        self._category_hierarchies = collect_category_hierarchies(self.products)
        self._known_category_keys = frozenset(
            normalize_category_text(segment)
            for path in self._category_hierarchies
            for segment in path
            if normalize_category_text(segment)
        )
        self.dataset_sha256 = dataset_version
        self.rows_quarantined = len(quarantined)
        self._by_id = {product.product_id.casefold(): product for product in products}
        self._candidates = tuple(self._candidate(product) for product in products)
        self._by_candidate_id = {
            candidate.product.product_id: candidate for candidate in self._candidates
        }
        self._known_words = frozenset(
            word for candidate in self._candidates for word in candidate.words
        )
        self._known_native_words = frozenset(
            word for candidate in self._candidates for word in candidate.native_words
        )
        word_freq: Counter[str] = Counter()
        brand_words: set[str] = set()
        compound_targets: set[str] = set()
        catalog_bigrams: set[tuple[str, str]] = set()
        for candidate in self._candidates:
            for word in candidate.words:
                word_freq[word] += 1
            for bw in candidate.brand_words:
                if len(bw) >= 3 and bw.isalpha():
                    brand_words.add(bw)
                    compound_targets.add(bw)
            for tw in candidate.title_words:
                if len(tw) >= 5 and tw.isalpha() and tw not in _PROTECTED_QUERY_WORDS:
                    # Include de-hyphenated compound tokens such as chocopie, cocacola
                    if tw not in _tokens(candidate.title):
                        compound_targets.add(tw)
            title_seq = _tokens(candidate.title)
            for idx in range(len(title_seq) - 1):
                catalog_bigrams.add((title_seq[idx], title_seq[idx + 1]))
            for seg_text in candidate.category_segment_texts:
                seg_seq = _tokens(seg_text)
                for idx in range(len(seg_seq) - 1):
                    catalog_bigrams.add((seg_seq[idx], seg_seq[idx + 1]))
            for nw in candidate.native_words:
                folded = _tokens(nw)
                if folded and len(folded[0]) >= 4 and folded[0].isalpha() and nw == folded[0]:
                    compound_targets.add(folded[0])
        self._brand_words = frozenset(brand_words)
        self._compound_targets = frozenset(compound_targets)
        self._catalog_bigrams = frozenset(catalog_bigrams)
        phonetic_index: dict[str, str] = {}
        sorted_targets = sorted(
            compound_targets,
            key=lambda w: (-(w in brand_words), -word_freq[w], w),
        )
        for target in sorted_targets:
            for pv in _phonetic_variants(target):
                phonetic_index.setdefault(pv, target)
        self._phonetic_index = phonetic_index
        self._token_index: dict[str, set[str]] = defaultdict(set)
        self._title_index: dict[str, set[str]] = defaultdict(set)
        self._identifier_index: dict[str, str] = {}
        for candidate in self._candidates:
            for word in candidate.words:
                self._token_index[word].add(candidate.product.product_id)
            self._title_index[candidate.title].add(candidate.product.product_id)
            for identifier in candidate.identifiers:
                self._identifier_index[identifier] = candidate.product.product_id
        # Each repository owns one immutable catalog version. Keep a bounded
        # page cache with that instance so a reload cannot reuse old products.
        self._search_page = lru_cache(maxsize=1024)(self._search)
        self._normalize_noisy_cached = lru_cache(maxsize=2048)(self._normalize_noisy_query)

    def normalize_noisy_query(self, query: str) -> str:
        if not query or len(self.products) < 50:
            return query
        return self._normalize_noisy_cached(query)

    def _normalize_noisy_query(self, text: str) -> str:
        text = _translate_cjk_retail_terms(text, strip_leftover_cjk=False)
        matches = list(_WORD_RE.finditer(text))
        if not matches:
            return text
        native_toks = [unicodedata.normalize("NFC", m.group().casefold()) for m in matches]
        plain_toks = [_tokens(nt)[0] if _tokens(nt) else nt for nt in native_toks]
        n = len(matches)
        edits: list[tuple[int, int, str]] = []
        i = 0
        while i < n:
            matched_compound: str | None = None
            matched_span = 1
            preceded_by_apostrophe = (
                matches[i].start() > 0 and text[matches[i].start() - 1] in ("'", "\u2019")
            )
            for span in () if preceded_by_apostrophe else (5, 4, 3, 2):
                if i + span > n:
                    continue
                between = text[matches[i].end() : matches[i + span - 1].start()]
                if re.search(r"[.!?,;:\d/]", between):
                    continue
                span_native = native_toks[i : i + span]
                span_plain = plain_toks[i : i + span]
                if any(not p.isalpha() or len(p) > 4 for p in span_plain):
                    continue
                if any(nt in _NATIVE_NO_COMPOUND_WORDS for nt in span_native):
                    continue
                if all(
                    _has_vietnamese_diacritic(nt) and nt in self._known_native_words
                    for nt in span_native
                ):
                    continue
                if span == 2 and (span_plain[0], span_plain[1]) in self._catalog_bigrams:
                    continue
                if span == 2 and span_native[0] in {"co", "có"} and span_plain[1] == "xi":
                    continue
                joined = "".join(span_plain)
                if len(joined) < 3:
                    continue
                if joined in self._compound_targets:
                    matched_compound = joined
                    matched_span = span
                    break
                if all(p in self._known_words for p in span_plain) and any(
                    len(p) >= 4 for p in span_plain
                ):
                    continue
                if len(joined) >= 4:
                    p_fold = _phonetic_fold(joined)
                    if p_fold in self._phonetic_index:
                        matched_compound = self._phonetic_index[p_fold]
                        matched_span = span
                        break
                    if len(p_fold) >= 6:
                        best_target = None
                        best_ratio = 0.0
                        for pv, target in self._phonetic_index.items():
                            if (
                                pv[0] != p_fold[0]
                                or pv[-1] != p_fold[-1]
                                or abs(len(pv) - len(p_fold)) > 1
                            ):
                                continue
                            ratio = SequenceMatcher(None, p_fold, pv).ratio()
                            if ratio >= 0.85 and ratio > best_ratio:
                                best_ratio = ratio
                                best_target = target
                        if best_target:
                            matched_compound = best_target
                            matched_span = span
                            break
            if matched_compound is not None:
                edits.append(
                    (matches[i].start(), matches[i + matched_span - 1].end(), matched_compound)
                )
                i += matched_span
                continue

            nt = native_toks[i]
            pt = plain_toks[i]
            if (
                nt not in self._known_native_words
                and pt in self._known_words
                and re.search(
                    r"(?:a[oiuy]|e[ou]|o[ai]|u[aiy])[\u0300\u0301\u0303\u0309\u0323]",
                    unicodedata.normalize("NFD", nt),
                )
            ):
                edits.append((matches[i].start(), matches[i].end(), pt))
                i += 1
                continue
            if (
                pt in self._known_words
                or pt in _PROTECTED_QUERY_WORDS
                or not pt.isalpha()
                or len(pt) < 4
                or _has_vietnamese_diacritic(nt)
            ):
                i += 1
                continue

            p_fold = _phonetic_fold(pt)
            if p_fold in self._phonetic_index:
                edits.append((matches[i].start(), matches[i].end(), self._phonetic_index[p_fold]))
                i += 1
                continue

            best_word = None
            best_score = 0.0
            for candidate_word in self._compound_targets:
                if len(candidate_word) < 4:
                    continue
                if candidate_word[0] != pt[0] or abs(len(candidate_word) - len(pt)) > 2:
                    continue
                ratio = SequenceMatcher(None, pt, candidate_word).ratio()
                p_ratio = SequenceMatcher(None, p_fold, _phonetic_fold(candidate_word)).ratio()
                eff_ratio = max(ratio, p_ratio)
                if eff_ratio >= 0.80:
                    score = eff_ratio + (0.03 if candidate_word in self._brand_words else 0.0)
                    if score > best_score:
                        best_score = score
                        best_word = candidate_word
            if best_word:
                edits.append((matches[i].start(), matches[i].end(), best_word))
            i += 1

        if not edits:
            return text
        result = text
        for start, end, replacement in reversed(edits):
            result = f"{result[:start]}{replacement}{result[end:]}"
        return result

    @staticmethod
    def _candidate(product: ProductSnapshot) -> _Candidate:
        title = normalize_search_text(product.name)
        brand = normalize_search_text(product.brand)
        dehyp_title_tokens = _dehyphenated_tokens(product.name)
        dehyp_brand_tokens = _dehyphenated_tokens(product.brand)
        raw_hierarchy = product.category_hierarchy or ""
        category = normalize_search_text(
            " ".join(
                filter(
                    None,
                    (
                        product.category,
                        product.category_hierarchy,
                        product.department,
                        product.sub_department,
                    ),
                )
            )
        )
        uom = normalize_search_text(product.uom)
        category_segment_words = tuple(
            frozenset(_tokens(part))
            for part in raw_hierarchy.split(">")
            if part.strip()
        )
        category_segment_texts = tuple(
            " ".join(_tokens(part))
            for part in raw_hierarchy.split(">")
            if part.strip()
        )
        if not category_segment_words:
            category_segment_words = (
                frozenset(_tokens(f"{category} {product.category}")),
            )
            category_segment_texts = (
                " ".join(_tokens(f"{category} {product.category}")),
            )
        audience_evidence = product_audience_evidence(product)
        baby_audience = audience_evidence in {"child", "newborn"}
        title_tokens = _tokens(title)
        brand_tokens = _tokens(brand)
        title_compact_brand_tokens: tuple[str, ...] = ()
        if (
            dehyp_brand_tokens
            and len(brand_tokens) == 2
            and all(tok.isalpha() and len(tok) >= 2 for tok in brand_tokens)
            and len(title_tokens) >= 2
        ):
            joined_brand = "".join(brand_tokens)
            if joined_brand in dehyp_brand_tokens and any(
                title_tokens[i : i + 2] == brand_tokens for i in range(len(title_tokens) - 1)
            ):
                title_compact_brand_tokens = (joined_brand,)
        category_tokens = _tokens(category)
        uom_tokens = _tokens(uom)
        words = tuple(
            dict.fromkeys(
                (
                    *title_tokens,
                    *dehyp_title_tokens,
                    *title_compact_brand_tokens,
                    *brand_tokens,
                    *dehyp_brand_tokens,
                    *category_tokens,
                    *uom_tokens,
                )
            )
        )
        identifiers = frozenset(
            value.casefold()
            for value in (product.product_id, product.item_no, product.barcode)
            if value
        )
        title_words = frozenset((*title_tokens, *dehyp_title_tokens, *title_compact_brand_tokens))
        uom_words = frozenset(uom_tokens)
        native_words = frozenset(
            _native_tokens(
                " ".join(
                    filter(
                        None,
                        (
                            product.name,
                            product.brand,
                            product.category,
                            product.category_hierarchy,
                            product.department,
                            product.sub_department,
                            product.uom,
                        ),
                    )
                )
            )
        )
        folded_buckets: dict[str, list[str]] = defaultdict(list)
        for nw in native_words:
            f = _tokens(nw)
            if f:
                folded_buckets[f[0]].append(nw)
        folded_to_native = {k: tuple(v) for k, v in folded_buckets.items()}
        is_baby_dept_parent = any(
            segment == _BABY_DEPARTMENT_SEGMENT for segment in category_segment_words[:-1]
        )
        mod_parts = _MODIFIER_PREPOSITION_RE.split(product.name, maxsplit=1)
        head_toks = _tokens(normalize_search_text(mod_parts[0])) if mod_parts else title_tokens
        tail_toks = (
            _tokens(normalize_search_text(mod_parts[1])) if len(mod_parts) > 1 else ()
        )
        head_words = frozenset(head_toks)
        non_pack_head = [t for t in head_toks if t not in _PACK_WORDS and t not in _PACKAGING_WORDS]
        lead_words = frozenset(non_pack_head[:2])
        modifier_only_words = frozenset(t for t in tail_toks if t not in head_words)
        return _Candidate(
            product=product,
            title=title,
            brand=brand,
            category=category,
            uom=uom,
            identifiers=identifiers,
            words=words,
            title_words=title_words,
            brand_words=frozenset((*brand_tokens, *dehyp_brand_tokens)),
            category_words=frozenset(category_tokens),
            category_segment_words=category_segment_words,
            category_segment_texts=category_segment_texts,
            uom_words=uom_words,
            native_words=native_words,
            brand_native_words=frozenset(_native_tokens(product.brand)),
            baby_audience=baby_audience,
            audience_evidence=audience_evidence,
            native_title_clean=unicodedata.normalize("NFC", product.name.strip().casefold()),
            folded_to_native=folded_to_native,
            measure=_measure(title),
            has_pack_title_or_uom=not (title_words | uom_words).isdisjoint(_PACK_WORDS),
            has_pack_uom=not uom_words.isdisjoint(_PACK_WORDS),
            is_baby_dept_parent=is_baby_dept_parent,
            title_space_text=" ".join(_tokens(product.name)),
            head_words=head_words,
            lead_words=lead_words,
            modifier_only_words=modifier_only_words,
        )

    def search(
        self,
        query: str = "",
        category: str | None = None,
        max_price_vnd: int | None = None,
        spicy: bool | None = None,
        in_stock: bool = False,
        limit: int = 10,
        brand: str | None = None,
        price_operator: PriceOperator | None = None,
        price_value_vnd: int | None = None,
        diversify_by_subcategory: bool = False,
        offset: int = 0,
        sort_by: str | None = None,
    ) -> list[ProductSnapshot]:
        # Return a fresh list: callers may reorder/filter it without changing
        # the cached page. ProductSnapshot values themselves are immutable.
        return list(
            self._search_page(
                query=query,
                category=category,
                max_price_vnd=max_price_vnd,
                spicy=spicy,
                in_stock=in_stock,
                limit=limit,
                brand=brand,
                price_operator=price_operator,
                price_value_vnd=price_value_vnd,
                diversify_by_subcategory=diversify_by_subcategory,
                offset=offset,
                sort_by=sort_by,
            )
        )

    def _search(
        self,
        query: str,
        category: str | None,
        max_price_vnd: int | None,
        spicy: bool | None,
        in_stock: bool,
        limit: int,
        brand: str | None,
        price_operator: PriceOperator | None,
        price_value_vnd: int | None,
        diversify_by_subcategory: bool,
        offset: int,
        sort_by: str | None,
    ) -> tuple[ProductSnapshot, ...]:
        normalized = normalize_search_text(query).strip().casefold()
        exact_id = self._identifier_index.get(normalized)
        exact_title_ids = self._title_index.get(normalized)
        effective_raw_query = (
            query if exact_id or exact_title_ids else self.normalize_noisy_query(query)
        )
        raw_plain_set = set(_tokens(effective_raw_query))
        has_explicit_brand = bool(raw_plain_set & self._brand_words)
        aliased_query = (
            effective_raw_query
            if exact_id or exact_title_ids
            else catalog_query_with_aliases(effective_raw_query)
        )
        # Never let a category alias inject "tre em" into an explicit brand lookup
        # (e.g. "ta dan sunmate" is adult diapers, not "ta dan tre em").
        if (
            has_explicit_brand
            and "tre em" in normalize_search_text(aliased_query)
            and "tre em" not in normalize_search_text(effective_raw_query)
        ):
            retrieval_query = effective_raw_query
        else:
            retrieval_query = aliased_query
        terms, comparison, _snack_slang = _query_terms(retrieval_query)
        normalized = normalize_search_text(retrieval_query).strip().casefold()

        cat_filter = _prepare_category_filter(category, self._known_category_keys)
        requested_audience = explicit_product_audience(query)
        normalized_brand = _tokens(brand)

        def _collect_matches(
            active_query: str, active_terms: tuple[str, ...]
        ) -> list[tuple[float, ProductSnapshot]]:
            if exact_id:
                c_ids = {exact_id}
            elif exact_title_ids:
                c_ids = set(exact_title_ids)
            else:
                c_ids = (
                    set().union(
                        *(
                            self._token_index.get(eq_token, set())
                            for token in active_terms
                            for eq_token in _token_lookup_variants(token)
                        )
                    )
                    if active_terms
                    else {item.product.product_id for item in self._candidates}
                )
            pq = _prepare_query(active_query)
            by_cid = self._by_candidate_id
            known_words = self._known_words
            known_native_words = self._known_native_words
            matched_rows: list[tuple[float, ProductSnapshot]] = []
            for product_id in c_ids:
                candidate = by_cid[product_id]
                if not _prepared_category_matches(cat_filter, candidate):
                    continue
                if requested_audience is not None:
                    ev = candidate.audience_evidence
                    if requested_audience == "newborn":
                        if ev != "newborn":
                            continue
                    elif requested_audience == "adult":
                        if ev in {"child", "newborn"}:
                            continue
                    elif ev not in {"child", "newborn"}:
                        continue
                if normalized_brand and not all(
                    token in candidate.brand_words for token in normalized_brand
                ):
                    continue
                product = candidate.product
                if max_price_vnd is not None and product.price_vnd > max_price_vnd:
                    continue
                if price_operator is not None and price_value_vnd is not None:
                    if not matches_price_constraint(
                        product.price_vnd, price_operator, price_value_vnd
                    ):
                        continue
                if spicy is not None and product.spicy is not spicy:
                    continue
                if in_stock and product.sellable_stock <= 0:
                    continue
                score = _score_prepared(
                    pq,
                    candidate,
                    known_words,
                    known_native_words,
                )
                if active_terms and score <= 0:
                    continue
                matched_rows.append((score, product))
            return matched_rows

        results = _collect_matches(retrieval_query, terms)
        # If a synonym rewrite yielded 0 results, retry once with the raw query.
        if not results and retrieval_query != effective_raw_query:
            raw_terms, _, _ = _query_terms(effective_raw_query)
            if raw_terms:
                results = _collect_matches(effective_raw_query, raw_terms)
                if results:
                    terms = raw_terms
        # If an explicit catalog brand is named (e.g. Bioré, Kikkoman, Tresemmé)
        # and extra regional/origin/modifier words prevented an exact match,
        # retry once with those safe non-identity modifiers stripped.
        if not results and has_explicit_brand:
            relaxed_query = re.sub(
                r"\b(?:xa\s+bong|xa\s+phong)\s+tam\b",
                "sua tam",
                normalize_search_text(effective_raw_query),
            )
            relaxed_tokens = [
                tok
                for tok in _tokens(relaxed_query)
                if tok not in _RELAXABLE_BRAND_MODIFIERS
            ]
            relaxed_str = " ".join(relaxed_tokens)
            if relaxed_str and relaxed_str != normalize_search_text(retrieval_query):
                relaxed_terms, _, _ = _query_terms(relaxed_str)
                if relaxed_terms and (set(relaxed_terms) & self._brand_words):
                    results = _collect_matches(relaxed_str, relaxed_terms)
                    if results:
                        terms = relaxed_terms

        if sort_by == "price_ascending":
            results.sort(
                key=lambda pair: (
                    pair[1].price_vnd,
                    pair[1].name.casefold(),
                    pair[1].product_id,
                )
            )
        elif sort_by == "price_descending":
            results.sort(
                key=lambda pair: (
                    -pair[1].price_vnd,
                    pair[1].name.casefold(),
                    pair[1].product_id,
                )
            )
        elif sort_by is not None:
            raise ValueError(f"Unsupported product sort: {sort_by}")
        elif not terms:
            results.sort(
                key=lambda pair: (
                    pair[1].price_vnd,
                    pair[1].name.casefold(),
                    pair[1].product_id,
                )
            )
        else:
            results.sort(
                key=lambda pair: (-pair[0], pair[1].name.casefold(), pair[1].product_id)
            )
        products = [product for _score_value, product in results]
        bounded_limit = max(1, min(limit, 20))
        bounded_offset = max(0, min(int(offset), 100_000))
        if diversify_by_subcategory:
            products = diversify_by_category_leaf(products, bounded_offset + bounded_limit)
        return tuple(products[bounded_offset : bounded_offset + bounded_limit])

    def catalog_terms_evidenced(
        self,
        terms: tuple[str, ...] | list[str],
        category: str,
    ) -> frozenset[str]:
        """Find exact name/brand/description terms in one bounded catalog pass."""
        requested = frozenset(
            token
            for term in terms
            for token in normalize_category_text(term).split()
            if token
        )
        if not requested:
            return frozenset()

        supported: set[str] = set()
        for candidate in self._candidates:
            if not _category_matches(category, candidate, self._known_category_keys):
                continue
            product = candidate.product
            evidence = frozenset(
                (
                    *candidate.words,
                    *normalize_category_text(
                        " ".join(
                            filter(None, (product.name, product.brand, product.description))
                        )
                    ).split(),
                )
            )
            supported.update(requested & evidence)
            for req_token in requested - supported:
                variants = _token_lookup_variants(req_token)
                if len(variants) > 1 and evidence.intersection(variants):
                    supported.add(req_token)
            if supported == requested:
                break
        return frozenset(supported)

    def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
        return self._category_hierarchies

    def list_speech_terms(self, limit: int = 32) -> tuple[str, ...]:
        return catalog_speech_terms(self.products, limit=limit)

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
        return self._speech_brand_names[: max(1, min(int(limit), len(self._speech_brand_names)))]

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return self._by_id.get(product_id.strip().casefold())

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]:
        products = [
            product
            for product in self.products
            if product.promotion
            and _category_matches(
                category,
                self._by_candidate_id[product.product_id],
                self._known_category_keys,
            )
        ]
        products.sort(key=lambda product: (product.price_vnd, product.name.casefold()))
        return products[: max(1, min(limit, 10))]

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions:
        return CatalogAdditions(comparable=False)
