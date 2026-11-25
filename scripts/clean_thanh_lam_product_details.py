"""SOTA 7-stage data cleaning, validation & quality scoring pipeline for Thanh Lam Mart product details.

Implements proven patterns from:
  - Hugging Face DataTrove / FineWeb (Unicode normalization, boilerplate & marketing-fluff
    removal, Gopher/C4 non-substantive filtering, terminal punctuation & dedup metrics)
  - OpenFoodFacts parser & quality facets (ingredient substance verification, balanced
    parentheses & clean clause trimming, cross-field bleed separation, allergen-claim
    quarantine, and quantitative quality scorecard).

Run:
    .venv\\Scripts\\python.exe scripts/clean_thanh_lam_product_details.py
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DETAILS_PATH = ROOT / "data" / "thanh_lam_product_details.json"

# ---------------------------------------------------------------------------
# Stage 1: Unicode, whitespace, and typographic normalization
# ---------------------------------------------------------------------------
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\xad]")
_HTML_TAG_RE = re.compile(r"<[^>]{1,120}>")
_MULTI_SPACE_RE = re.compile(r"[ \t\r\n\xa0]+")
_MISSING_SPACE_AFTER_PUNCT_RE = re.compile(
    r"([,;:])([^\s\d\)\]\.\,\"\'\-/:])"
)
_PCT_PAREN_RE = re.compile(r"(\d\s*%)\(")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;:.!?])")
_BROKEN_DOTS_RE = re.compile(r"(?:,\s*\.+|\.{2,})")

# ---------------------------------------------------------------------------
# Stage 2: Cross-field bleed splitters & merged-bullet sentence boundary repair
# ---------------------------------------------------------------------------
_CROSS_FIELD_SPLIT_RE = re.compile(
    r"(?i)\b(?:hướng dẫn sử dụng(?:\s+và\s+bảo quản)?|hướng dẫn bảo quản|"
    r"cách(?:\s+sử)?\s+dụng|cách dùng|bảo quản(?:\s+nơi|\s+lạnh|\s*:)|"
    r"ngày sản xuất|hạn sử dụng|năng lượng\s*:|số lượng\s*:|quy cách\s*:|"
    r"khối lượng\s*:|dung tích\s*:|xuất xứ\s*:|thương hiệu\s*:|lưu ý\s*:|"
    r"cảnh báo\s*:|đối tượng sử dụng\s*:)\s*[:：]?"
)

# Action verbs that frequently start a new HTML <li> bullet in Vietnamese usage/storage guides.
_BULLET_START_VERBS = (
    "Bước", "Cho", "Đổ", "Rót", "Đun", "Nấu", "Hấp", "Chiên", "Xào", "Nướng",
    "Pha", "Khuấy", "Trộn", "Lắc", "Mở", "Bật", "Cắt", "Thoa", "Bôi", "Xịt",
    "Làm ướt", "Rửa", "Tắm", "Gội", "Xả", "Massage", "Chải", "Lau", "Đặt",
    "Đậy", "Đóng", "Giữ", "Để", "Tránh", "Bảo quản", "Không sử dụng",
    "Không dùng", "Dùng", "Sử dụng", "Thưởng thức", "Ăn", "Uống", "Ngâm",
    "Nên", "Lưu ý", "Chú ý", "Sau khi",
)
_MERGED_BULLET_RE = re.compile(
    r"([a-zà-ỹ0-9%\)])(?<!\bSữa)(?<!\bNước)(?<!\bKem)(?<!\bGel)(?<!\bDầu)\s+(?=(?:"
    + "|".join(re.escape(v) for v in _BULLET_START_VERBS)
    + r")\b)"
)

# Leading labels/noise to strip cleanly from field starts.
_LEADING_LABEL_RE = re.compile(
    r"^(?:[\d\.\-\*\•]+\s*)?(?:"
    r"thông tin sản phẩm|mô tả sản phẩm|đánh giá|chi tiết sản phẩm|"
    r"thành phần(?:\s+chính|\s+dinh dưỡng|\s+và\s+công dụng)?|"
    r"nguyên liệu(?:\s+chính)?|chất liệu(?:\s+chính)?|"
    r"hướng dẫn sử dụng(?:\s+và\s+bảo quản)?|hướng dẫn bảo quản|"
    r"cách sử dụng|cách dùng|bảo quản|công dụng(?:\s+sản phẩm)?|"
    r"đặc điểm nổi bật|gồm có|bao gồm|gồm"
    r")\s*[:：\-]?\s*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Stage 3: Placeholder, packaging, safety-hazard & marketing-fluff filters
# ---------------------------------------------------------------------------
_PLACEHOLDER_ING_RE = re.compile(
    r"^(?:xem\s+trên\s+bao\s+bì|in\s+trên\s+bao\s+bì|chi\s+tiết\s+xem\s+trên\s+bao\s+bì|"
    r"theo\s+công\s+bố|đang\s+cập\s+nhật|chưa\s+có\s+thông\s+tin|"
    r"không\s+có\s+thông\s+tin)\b",
    re.IGNORECASE,
)
_PACKAGING_ONLY_RE = re.compile(
    r"^(?:vỏ\s+đựng|bao\s+bì|hộp\s+đựng|chai\s+đựng|túi\s+đựng|lon\s+đựng|nắp\s+đậy)\b",
    re.IGNORECASE,
)
_UNVERIFIED_ALLERGEN_SAFETY_RE = re.compile(
    r"(?:safe\s+for\s+.*allerg|no\s+milk\.\s*safe|100%\s*không\s+gây\s+dị\s+ứng|"
    r"an\s+toàn\s+tuyệt\s+đối\s+cho\s+người\s+dị\s+ứng)",
    re.IGNORECASE,
)

# Sentences that are pure marketing copy (never actual ingredient facts).
_PURE_MARKETING_SENTENCE_RE = re.compile(
    r"(?:dây\s+chuyền\s+(?:sản\s+xuất\s+)?hiện\s+đại|công\s+nghệ\s+(?:sản\s+xuất\s+)?(?:hiện\s+đại|tiên\s+tiến)|"
    r"đạt\s+tiêu\s+chuẩn\s+(?:vệ\s+sinh|an\s+toàn)|tiêu\s+chuẩn\s+vệ\s+sinh\s+an\s+toàn|"
    r"kích\s+thích\s+(?:bạn\s+)?ăn\s+ngon\s+miệng|cho\s+màu\s+sắc\s+đẹp\s+mắt|"
    r"chất\s+lượng\s+hoàn\s+hảo|an\s+toàn\s+cho\s+sức\s+khỏe\s+(?:người\s+tiêu\s+dùng|cả\s+gia\s+đình|người\s+dùng)|"
    r"được\s+chọn\s+lọc\s+dựa\s+trên\s+rất\s+nhiều\s+tiêu\s+chí|"
    r"được\s+kiểm\s+duyệt\s+chặt\s+chẽ|đảm\s+bảo\s+an\s+toàn\s+thực\s+phẩm|"
    r"là\s+một\s+trong\s+những\s+(?:loại|món|sản\s+phẩm)|"
    r"được\s+sử\s+dụng\s+rất\s+phổ\s+biến|mang\s+lại\s+sự\s+tin\s+tưởng|"
    r"đóng\s+gói\s+tiện\s+dụng|tiện\s+lợi\s+khi\s+sử\s+dụng|"
    r"dùng\s+chấm\s+trực\s+tiếp\s+hoặc\s+tẩm\s+ướp|"
    r"vỏ\s+đựng\s+sản\s+phẩm\s+làm\s+từ|"
    r"thiết\s+kế\s+chuyên\s+biệt\s+cho\s+từng\s+giai\s+đoạn|"
    r"hương\s+vị\s+cay\s+ngon\s+vô\s+cùng\s+hấp\s+dẫn)",
    re.IGNORECASE,
)

_EXOTIC_SCRIPT_RE = re.compile(r"[\u0400-\u1dff]")
_TRAILING_NON_SECTION_RE = re.compile(
    r"\b(?:thông tin thương hiệu|đôi nét về thương hiệu|mô tả thương hiệu|"
    r"thông tin cảnh báo|chỉ tiêu chất lượng|ngày sản xuất|hạn sử dụng)\b.*$",
    re.IGNORECASE,
)
_VALID_INCI_RE = re.compile(
    r"\b(?:sorbitol|hydrated silica|sodium lauryl sulfate|sodium fluoride|"
    r"glycerin|niacinamide|titanium dioxide|stearic acid|peg-\d+|disodium edta|"
    r"zinc citrate|cellulose gum|panthenol|tocopherol|selenium sulfide|"
    r"dipotassium glycyrrhizate|ketoconazole|piroctone olamine|zinc pyrithione|"
    r"polyquaternium|ammonium laureth|dimethicone|cocamidopropyl betaine|"
    r"menthol|salicylic acid|allantoin|cetyl alcohol|stearyl alcohol|"
    r"phenoxyethanol|tocopheryl acetate|sodium chloride|citric acid)\b",
    re.IGNORECASE,
)
_REAL_HTML_JUNK_RE = re.compile(r"</?[a-zA-Z][^>]*>|&(?:amp|nbsp|lt|gt|quot);")

# Real ingredient/material substance detector (OpenFoodFacts-inspired domain lexicon).
_REAL_SUBSTANCE_RE = re.compile(
    r"\b(?:(?<!món\s)(?<!đất\s)nước(?:\s+sạch|\s+tinh\s+khiết|\s+mắm|\s+tương|\s+cốt|\s+bão\s+hòa|\s+ép|\s+muối)?|"
    r"đường|muối|bột\s+\w+|dầu\s+\w+|mỡ|sữa|trứng|thịt|cá|tôm|mực|cua|gà|bò|heo|lợn|"
    r"gạo|nếp|đậu|đỗ|ớt|tỏi|tiêu(?!\s*(?:chí|chuẩn|dùng|thụ|hóa|hủy))|hành|gừng|nghệ|sả|quế|hồi|mật\s+ong|mật|"
    r"trà|chè|cà\s+phê|ca\s+cao|cacao|socola|sô\s+cô\s+la|phô\s+mai|bơ|kem|váng\s+sữa|"
    r"vitamin|canxi|calcium|sắt|kẽm|magiê|kali|natri|sodium|bicarbonate|flouride|fluoride|"
    r"i-ốt|iod|acid|axit|glycerin|aqua|water|extract|chiết\s+xuất|tinh\s+dầu|tinh\s+chất|"
    r"polyester|cotton|bông|vải|nhựa|pp|pet|pe|abs|inox|thép|nhôm|giấy|bột\s+giấy|gỗ|tre|"
    r"cao\s+su|silicone|polyurethane|sợi|nylon|than\s+hoạt\s+tính|đất\s+sét|baking\s+soda|"
    r"chất\s+điều\s+vị|chất\s+bảo\s+quản|chất\s+ổn\s+định|chất\s+nhũ\s+hóa|chất\s+tạo|"
    r"chất\s+làm|chất\s+chống|chất\s+điều\s+chỉnh|hương\s+liệu|phẩm\s+màu|màu\s+thực\s+phẩm|"
    r"tinh\s+bột|maltodextrin|men|nấm|rau|củ|quả|trái\s+cây|tảo\s+biển|rong\s+biển|"
    r"chanh|cam|dâu|xoài|táo|nho|dừa|mè|vừng|lạc|hạnh\s+nhân|óc\s+chó|yến\s+mạch|lúa\s+mì|"
    r"lúa\s+mạch|ngô|bắp|khoai|sắn|cà\s+chua|xì\s+dầu|giấm|mạch\s+nha|glucose|fructose|"
    r"sucrose|maltose|lactose|whey|casein|protein|đạm|chất\s+béo|shortening|margarine|"
    r"collagen|niacinamide|panthenol|tocopherol|ceramide|hyaluronic|salicylic|retinol|"
    r"laureth|sulfate|paraben|citric|benzoate|sorbate|lecithin|pectin|gelatin|agar|xanthan|"
    r"ins\s*\d{3}|e\d{3}|wheat\s+flour|sugar|palm\s+oil|milk|cocoa|rice|soy|bean|starch|"
    r"lô\s+hội|nha\s+đam|bạc\s+hà|oải\s+hương|hoa\s+hồng|hoa\s+cúc|hương\s+thảo|tơ\s+tằm|"
    r"ngọc\s+trai|yến\s+sào|nhân\s+sâm|linh\s+chi|cốm|hạt\s+sen|hạt\s+chia|hạt\s+điều|macca|"
    r"hoạt\s+chất|enzyme|enzim|surfactant|alcohol|cồn|ethanol|chlorine|clo|javel|"
    r"selenium|sulfide|glycyrrhizate|ketoconazole|piroctone|pyrithione|polyquaternium|"
    r"dimethicone|betaine|menthol|allantoin|phenoxyethanol|chlorhexidine|"
    r"caffeine|taurine|inositol|ginseng|probiotic|lợi\s+khuẩn|prebiotic|fos|inulin|dha|epa)\b",
    re.IGNORECASE,
)

# Detect broken sentence fragments and negative-only marketing claims masquerading as ingredients.
_BROKEN_FRAGMENT_START_RE = re.compile(
    r"^(?:có\s+trong\s+(?:bánh|kẹo|sản\s+phẩm|thực\s+phẩm)\b|"
    r"của\s+sản\s+phẩm\b|"
    r"(?:sản\s+phẩm\s+)?không\s+(?:có|chứa)\s+chất\s+phụ\s+gia\b|"
    r"sản\s+phẩm\s+là\s+sự\s+kết\s+hợp\b)",
    re.IGNORECASE,
)
_MADE_FROM_SPLIT_RE = re.compile(
    r"\b(?:được\s+làm|làm|được\s+chế\s+biến|chế\s+biến)\s+(?:từ|bằng)\b",
    re.IGNORECASE,
)
_GENERIC_SELECTED_ING_RE = re.compile(
    r"(?:nguyên\s+liệu|thành\s+phần)\s+(?:tự\s+nhiên\s+)?(?:hảo\s+hạng\s+)?(?:sạch\s+)?"
    r"(?:được\s+)?(?:tuyển\s+chọn|chọn\s+lọc)",
    re.IGNORECASE,
)
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*\)")

# Detect pure-English OpenFoodFacts strings (no Vietnamese diacritics & >= 3 English food words).
_VI_DIACRITIC_RE = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
_EN_FOOD_WORDS_RE = re.compile(
    r"\b(?:water|sugar|salt|wheat|flour|palm|vegetable|oil|starch|powder|flavor|flavour|"
    r"colour|color|emulsifier|thickener|raising|preservative|contains|milk|butter|soy|rice)\b",
    re.IGNORECASE,
)


def normalize_typography(text: str) -> str:
    if not text:
        return ""
    s = html.unescape(text)
    s = unicodedata.normalize("NFC", s)
    s = _ZERO_WIDTH_RE.sub("", s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _EMPTY_PARENS_RE.sub("", s)
    s = re.sub(r"\bfruvihse\b", "fructose", s, flags=re.IGNORECASE)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    # Preserve decimal numbers like 5,76% or 13.41% while fixing missing space after punctuation.
    s = _PCT_PAREN_RE.sub(r"\1 (", s)
    s = _MISSING_SPACE_AFTER_PUNCT_RE.sub(r"\1 \2", s)
    s = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = _BROKEN_DOTS_RE.sub(".", s)
    # Remove stray leading/trailing punctuation artifacts.
    s = s.strip(" \t\r\n-–—•*:")
    s = re.sub(r"\s{2,}", " ", s)
    return s


def strip_title_echo(text: str, product_name: str) -> str:
    """Strip repeated product title / section prefix echoed at the start of Thanh Lam Mart copy."""
    s = text.strip()
    if not s:
        return ""
    norm_title = re.sub(r"\s+", " ", product_name).strip()
    for _ in range(3):
        prev = s
        s = _LEADING_LABEL_RE.sub("", s).strip()
        if norm_title and len(norm_title) >= 8:
            # Strip exact case-insensitive prefix match of product title.
            if s.casefold().startswith(norm_title.casefold()):
                rest = s[len(norm_title):].lstrip(" .:-–—")
                if len(rest) >= 15:
                    s = rest
            # Also strip shortened title prefix without pack size (e.g. "Bánh quế vị kem dâu Cosy").
            short_title = re.sub(
                r"\s+(?:gói|hộp|chai|lon|túi|bịch|tuýp|can|lốc|thùng|vỉ|khay|hũ|lọ)\b.*$",
                "",
                norm_title,
                flags=re.IGNORECASE,
            ).strip()
            if short_title and len(short_title) >= 12 and s.casefold().startswith(short_title.casefold() + " "):
                after = s[len(short_title):].strip()
                # Only strip if immediately followed by another title repetition or "Thông tin sản phẩm".
                if after.casefold().startswith(("thông tin", "mô tả", short_title.casefold())):
                    s = after
        if s == prev:
            break
    return s


def repair_merged_bullets(text: str) -> str:
    """Insert clean '. ' boundaries where HTML <li> bullets were joined with bare spaces."""
    if not text:
        return ""
    # Guard compound Vietnamese product nouns ("Sữa Rửa Mặt", "Nước Rửa Chén", "Nước Lau Sàn")
    # before inserting sentence dots ahead of capitalized action verbs.
    s = re.sub(r"\b(Sữa|Nước|Kem|Gel|Dầu)\.\s+(Rửa|Tắm|Gội|Lau|Xả)\b", r"\1 \2", text)
    repaired = _MERGED_BULLET_RE.sub(r"\1. ", s)
    repaired = re.sub(r"\b(Sữa|Nước|Kem|Gel|Dầu)\.\s+(Rửa|Tắm|Gội|Lau|Xả)\b", r"\1 \2", repaired)
    repaired = re.sub(r"\.\s*\.", ".", repaired)
    # Collapse echoed title headers like "Kẹo gừng Gingerbon gói 125g Kẹo gừng Gingerbon..."
    repaired = re.sub(r"^([A-ZÀ-Ỹ][^\.\n]{8,55}?)\s+(?:\([^)]+\)\s*)?(?=\1\b)", "", repaired)
    return repaired.strip()


def balance_parens(text: str) -> str:
    """Remove unmatched '(' or ')' cleanly without corrupting valid parentheticals."""
    # First drop stray unmatched ')' scanning left-to-right.
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            out.append(ch)
        elif ch == ")":
            if depth > 0:
                depth -= 1
                out.append(ch)
        else:
            out.append(ch)
    s = "".join(out)
    # Then trim any remaining unclosed '(' from the right.
    while s.count("(") > s.count(")"):
        idx = s.rfind("(")
        if idx <= 0:
            s = s.replace("(", "", 1)
            break
        s = s[:idx].rstrip(" ,;:-–—")
    return s


def trim_clean_sentence(text: str, max_len: int) -> str:
    """Truncate strictly at a clean sentence/clause boundary with balanced parentheses."""
    s = balance_parens(text.strip())
    if not s:
        return ""

    # Drop dangling truncated words after pre-existing ellipsis cutoffs.
    if len(s) > max_len:
        cut = s[:max_len]
        # Prefer last sentence end ('. ', '; ') beyond 55% of budget.
        best_sent = max(cut.rfind(". "), cut.rfind("; "))
        if best_sent >= int(max_len * 0.50):
            s = cut[: best_sent + 1].strip()
        else:
            # Fall back to last comma outside open parentheses.
            last_comma = cut.rfind(", ")
            if last_comma >= int(max_len * 0.50):
                s = cut[:last_comma].strip()
            else:
                s = cut.rsplit(" ", 1)[0].strip()

    s = balance_parens(s).rstrip(" ,;:-–—")
    if s and s[-1] not in ".!?":
        s += "."
    return s


def _capitalize_first(s: str) -> str:
    if not s:
        return ""
    return s[0].upper() + s[1:]


def clean_ingredients(
    raw_ing: str,
    product_name: str,
    *,
    source: str | None = None,
) -> tuple[str | None, dict[str, str]]:
    """Clean and validate an ingredient string; return (clean_ing_or_None, extracted_bleed)."""
    bleed: dict[str, str] = {}
    if not raw_ing:
        return None, bleed

    s = normalize_typography(raw_ing)
    # Quarantine exotic scripts / OpenFoodFacts OCR gibberish (HF FineWeb-2 script filter).
    if _EXOTIC_SCRIPT_RE.search(s):
        return None, bleed
    # If an explicit "Thành phần:" label appears after noisy OCR/title prefix, slice cleanly from it.
    m_tp = re.search(r"\bthành\s+phần\s*[:：]\s*", s, re.IGNORECASE)
    if m_tp and m_tp.start() > 0:
        s = s[m_tp.end():].strip()
    # Quarantine garbled English OCR strings (random case/digit artifacts like "180ua. OnlseuslinA").
    if not _VI_DIACRITIC_RE.search(s) and not _VALID_INCI_RE.search(s):
        if re.search(r"\b\d+[a-z]{2,}\b|[a-z][A-Z]{2,}|\baos\b", s):
            return None, bleed
    # Quarantine dangerous unverified allergen/safety assertions.
    if _UNVERIFIED_ALLERGEN_SAFETY_RE.search(s):
        return None, bleed

    # Strip stray leading "Tự nhiên " fragment before product title (e.g. "Tự nhiên Kẹo dẻo...").
    s = re.sub(r"^Tự\s+nhiên\s+(?=[A-ZÀ-Ỹ])", "", s)
    # Strip trailing promotional clauses occasionally appended to retailer ingredient fields.
    s = re.sub(r"\.\s*Sản\s+phẩm\s+lành\s+tính\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\.\s*Đóng\s+gói\s+với\s+khối\s+lượng\b.*$", "", s, flags=re.IGNORECASE)
    # Strip trailing ", và các thành phần khác" filler or dangling ellipsis inside list.
    s = re.sub(r",\s*và\s+các\s+thành\s+phần\s+khác\.?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(?:,\s*)?\.{3,}\s*$", "", s)

    # Split out cross-field bleed (e.g. "Hướng dẫn sử dụng: ...", "Bảo quản: ...").
    split_match = _CROSS_FIELD_SPLIT_RE.search(s)
    if split_match:
        head = s[: split_match.start()].strip(" .,:;-–—")
        tail = s[split_match.start():].strip()
        tail_low = tail.casefold()
        if any(k in tail_low[:30] for k in ("hướng dẫn sử dụng", "cách dùng", "cách sử dụng")):
            bleed["usage"] = tail
        elif "bảo quản" in tail_low[:30]:
            bleed["storage"] = tail
        s = head

    s = strip_title_echo(s, product_name)
    s = _LEADING_LABEL_RE.sub("", s).strip()
    if not s or len(s) < 10:
        return None, bleed

    # Reject placeholder / packaging-only / broken-fragment entries.
    if (
        _PLACEHOLDER_ING_RE.search(s)
        or _PACKAGING_ONLY_RE.search(s)
        or _BROKEN_FRAGMENT_START_RE.search(s)
    ):
        return None, bleed

    # Reject non-Vietnamese OpenFoodFacts English/foreign strings (keep clean INCI cosmetics).
    if not _VI_DIACRITIC_RE.search(s) and not _VALID_INCI_RE.search(s):
        if source == "OpenFoodFacts" and len(_EN_FOOD_WORDS_RE.findall(s)) >= 2:
            return None, bleed
        if len(_EN_FOOD_WORDS_RE.findall(s)) >= 2:
            return None, bleed

    # Reject pure usage slogans masquerading as ingredients (e.g. "Đượm vị cay... Dùng để chấm").
    if re.search(r"\bdùng\s+để\s+chấm\b", s, re.IGNORECASE) and not re.search(
        r"\b(?:ớt|tỏi|đường|muối|cà chua|bột|tinh bột)\b", s, re.IGNORECASE
    ):
        return None, bleed

    # Sentence-level marketing fluff removal (HF FineWeb / DataTrove sentence filter).
    # Split on ". " (preserving decimal dots like 5.76% and abbreviations).
    sentences = [seg.strip() for seg in re.split(r"(?<!\d)\.\s+", s) if seg.strip()]
    kept: list[str] = []
    for sent in sentences:
        if _PLACEHOLDER_ING_RE.search(sent) or _PACKAGING_ONLY_RE.search(sent):
            continue
        if _UNVERIFIED_ALLERGEN_SAFETY_RE.search(sent) or _BROKEN_FRAGMENT_START_RE.search(sent):
            continue
        # Reject title-echo pure-fluff sentences ("X vị kem dâu được làm từ những nguyên liệu
        # tự nhiên được chọn lọc kỹ lưỡng") where NO concrete substance appears AFTER "làm từ".
        m_made = _MADE_FROM_SPLIT_RE.search(sent)
        if m_made and _GENERIC_SELECTED_ING_RE.search(sent[m_made.end():]):
            predicate = sent[m_made.end():]
            if not _REAL_SUBSTANCE_RE.search(predicate):
                continue
        # Drop pure marketing sentences that contain NO real ingredient/material substance.
        if _PURE_MARKETING_SENTENCE_RE.search(sent) and not _REAL_SUBSTANCE_RE.search(sent):
            continue
        # Drop standalone usage/promotional sentences mixed into "Thành phần và công dụng".
        if re.match(
            r"^(?:sản phẩm dùng|dùng để|dùng với|màu đỏ tự nhiên mịn màng|"
            r"chất lượng|đạt tiêu chuẩn|sản phẩm được sản xuất|quy trình chế biến)\b",
            sent,
            re.IGNORECASE,
        ) and not _REAL_SUBSTANCE_RE.search(sent):
            continue
        # Also if a sentence contains marketing fluff about "dùng chấm / chất lượng hoàn hảo"
        # alongside another sentence that ALREADY has substances, drop the non-substance sentence.
        if re.search(
            r"(?:dùng chấm trực tiếp|kích thích bạn ăn ngon|màu sắc đẹp mắt|"
            r"vị cay ngon vô cùng|đạt tiêu chuẩn vệ sinh)",
            sent,
            re.IGNORECASE,
        ) and not re.search(
            r"(?:làm từ|làm bằng|thành phần|nguyên liệu|ớt tươi|bột|đường|muối|dầu|nước|sữa|thịt|cá)",
            sent,
            re.IGNORECASE,
        ):
            continue
        kept.append(sent)

    if not kept:
        return None, bleed

    joined = ". ".join(kept)
    # Require at least ONE genuine substance/material token (eliminates 100% pure-fluff ingredients!).
    if not _REAL_SUBSTANCE_RE.search(joined):
        return None, bleed

    # Trim trailing marketing clause inside the surviving ingredient sentence if present.
    joined = re.sub(
        r",\s*(?:cho màu sắc đẹp mắt|chất lượng hoàn hảo|mang đến hương vị thơm ngon|"
        r"đảm bảo an toàn cho sức khỏe)[^.]*",
        "",
        joined,
        flags=re.IGNORECASE,
    )
    joined = repair_merged_bullets(joined)
    joined = _capitalize_first(normalize_typography(joined))
    joined = trim_clean_sentence(joined, max_len=460)
    if len(joined) < 12 or not _REAL_SUBSTANCE_RE.search(joined):
        return None, bleed
    if (
        not _VI_DIACRITIC_RE.search(joined)
        and not _VALID_INCI_RE.search(joined)
        and len(_EN_FOOD_WORDS_RE.findall(joined)) >= 2
    ):
        return None, bleed
    return joined, bleed


_STORAGE_ONLY_PREFIX_RE = re.compile(
    r"^(?:hướng\s+dẫn\s+bảo\s+quản|bảo\s+quản|nơi\s+khô\s+ráo|khô\s+ráo|"
    r"ở\s+nhiệt\s+độ\s+phòng|ngăn\s+mát|ngăn\s+đông|tránh\s+ánh\s+nắng)\b",
    re.IGNORECASE,
)
_REAL_USAGE_VERB_RE = re.compile(
    r"\b(?:dùng\s+trực\s+tiếp|ăn\s+trực\s+tiếp|ăn\s+ngay|dùng\s+ngay|uống\s+trực\s+tiếp|"
    r"lắc\s+(?:đều|kỹ)|thoa|gội|xả|tắm|rửa|chải|đánh\s+răng|súc\s+miệng|giặt|lau|"
    r"xịt|bôi|đắp|pha|nấu|hấp|chiên|xào|luộc|nướng|chấm|ướp|trộn|bóc\s+vỏ|mở\s+gói|mở\s+nắp)\b",
    re.IGNORECASE,
)


def clean_usage(raw_usage: str, product_name: str) -> tuple[str | None, str | None]:
    """Clean usage instructions; split out trailing storage clause if mixed."""
    if not raw_usage:
        return None, None
    s = normalize_typography(raw_usage)
    s = strip_title_echo(s, product_name)
    s = _LEADING_LABEL_RE.sub("", s).strip()
    if not s or len(s) < 8 or _PLACEHOLDER_ING_RE.search(s):
        return None, None

    # Migrate misclassified storage-only text out of usage into storage.
    if _STORAGE_ONLY_PREFIX_RE.search(s) and not _REAL_USAGE_VERB_RE.search(s):
        return None, s

    extracted_storage: str | None = None
    m_sto = re.search(
        r"\b(?:hướng dẫn bảo quản|bảo quản\s+(?:nơi|lạnh|trong|ở|:))",
        s,
        re.IGNORECASE,
    )
    if m_sto and m_sto.start() >= 15:
        extracted_storage = s[m_sto.start():].strip()
        s = s[: m_sto.start()].strip(" .,:;-–—")

    # Strip trailing brand/warning/quality/expiry metadata.
    s = _TRAILING_NON_SECTION_RE.sub("", s).strip(" .,:;-–—")
    s = repair_merged_bullets(s)
    s = _capitalize_first(normalize_typography(s))
    s = trim_clean_sentence(s, max_len=380)
    if len(s) < 10:
        return None, extracted_storage
    return s, extracted_storage


_VALID_STORAGE_CUE_RE = re.compile(
    r"(?:(?<!chất\s)bảo\s+quản|khô\s+ráo|thoáng\s+mát|nhiệt\s+độ|tủ\s+lạnh|ngăn\s+mát|"
    r"ngăn\s+đông|ánh\s+nắng|ánh\s+sáng|đậy\s+kín|đóng\s+kín|tránh\s+nhiệt|tránh\s+ẩm|"
    r"để\s+nơi|\d+\s*°?\s*c\b|lạnh\s+từ|cấp\s+đông|trữ\s+đông|nơi\s+mát|độ\s+ẩm)",
    re.IGNORECASE,
)


def clean_storage(raw_storage: str, product_name: str) -> str | None:
    if not raw_storage:
        return None
    s = normalize_typography(raw_storage)
    s = strip_title_echo(s, product_name)
    s = _LEADING_LABEL_RE.sub("", s).strip()
    if not s or len(s) < 6 or _PLACEHOLDER_ING_RE.search(s):
        return None
    if not _VALID_STORAGE_CUE_RE.search(s):
        return None

    # Restore clipped "Bảo quản " prefix when section splitter consumed the verb.
    low = s.casefold()
    if low.startswith(("nơi khô", "khô ráo", "lạnh ", "ở nhiệt độ", "trong tủ", "tránh ánh", "ngăn mát", "ngăn đông")):
        s = "Bảo quản " + s

    s = _TRAILING_NON_SECTION_RE.sub("", s).strip(" .,:;-–—")
    s = repair_merged_bullets(s)
    s = _capitalize_first(normalize_typography(s))
    s = trim_clean_sentence(s, max_len=280)
    return s if len(s) >= 10 and _VALID_STORAGE_CUE_RE.search(s) else None


def clean_summary(raw_summary: str, product_name: str) -> str | None:
    if not raw_summary:
        return None
    s = normalize_typography(raw_summary)
    s = strip_title_echo(s, product_name)
    if not s or len(s) < 20:
        return None
    s = repair_merged_bullets(s)
    s = _capitalize_first(normalize_typography(s))
    s = trim_clean_sentence(s, max_len=420)
    return s if len(s) >= 20 else None


def audit_dataset(records: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Compute Hugging Face DataTrove + OpenFoodFacts 10-dimension defect metrics."""
    bad_punct_re = re.compile(
        r"(?:[,;:][^\s\d\)\]\.\,\"\']|\s{2,}|,\.\.|\.\.(?!\.)|[\xa0\u200b\u200c\u200d\ufeff])"
    )
    bleed_re = re.compile(
        r"(?:hướng dẫn sử dụng|hướng dẫn bảo quản|bảo quản nơi|ngày sản xuất\s*:)",
        re.IGNORECASE,
    )
    metrics: Counter[str] = Counter()
    for rec in records.values():
        ing = str(rec.get("ingredients") or "")
        use = str(rec.get("usage") or "")
        sto = str(rec.get("storage") or "")
        summ = str(rec.get("summary") or "")

        if ing:
            metrics["count_ingredients"] += 1
            if _PLACEHOLDER_ING_RE.search(ing) or _PACKAGING_ONLY_RE.search(ing):
                metrics["defect_placeholder_or_packaging_ing"] += 1
            if _UNVERIFIED_ALLERGEN_SAFETY_RE.search(ing):
                metrics["defect_unsafe_allergen_claim"] += 1
            if not _REAL_SUBSTANCE_RE.search(ing):
                metrics["defect_fluff_only_ing"] += 1
            if bleed_re.search(ing):
                metrics["defect_cross_field_bleed"] += 1
            if (
                not _VI_DIACRITIC_RE.search(ing)
                and not _VALID_INCI_RE.search(ing)
                and len(_EN_FOOD_WORDS_RE.findall(ing)) >= 2
            ):
                metrics["defect_non_vietnamese_ing"] += 1
            if _EXOTIC_SCRIPT_RE.search(ing):
                metrics["defect_exotic_ocr_gibberish"] += 1
        if use:
            metrics["count_usage"] += 1
        if sto:
            metrics["count_storage"] += 1
            if not _VALID_STORAGE_CUE_RE.search(sto):
                metrics["defect_non_storage_in_storage"] += 1
        if summ:
            metrics["count_summary"] += 1

        for val in (ing, use, sto, summ):
            if not val:
                continue
            if bad_punct_re.search(val):
                metrics["defect_bad_punct_or_unicode"] += 1
            if val.count("(") != val.count(")"):
                metrics["defect_unbalanced_parens"] += 1
            if val.endswith("...") or val.endswith("…"):
                metrics["defect_truncated_ellipsis"] += 1
            if _MERGED_BULLET_RE.search(val):
                metrics["defect_merged_bullets"] += 1
            if _REAL_HTML_JUNK_RE.search(val):
                metrics["defect_html_junk"] += 1

    return dict(sorted(metrics.items()))


def main() -> None:
    payload = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    raw_records: dict[str, dict[str, Any]] = payload["records"]

    before_metrics = audit_dataset(raw_records)
    print("=== BEFORE CLEANING (10-DIMENSION AUDIT) ===")
    for k, v in before_metrics.items():
        print(f"  {k:38s}: {v}")

    cleaned_records: dict[str, dict[str, Any]] = {}
    stats = Counter()
    dropped_ing_reasons = Counter()

    for pid, rec in raw_records.items():
        pname = str(rec.get("product_name") or "")
        ing_source = rec.get("ingredients_source")
        ing_clean, bleed = clean_ingredients(
            str(rec.get("ingredients") or ""),
            pname,
            source=ing_source,
        )
        if rec.get("ingredients") and not ing_clean:
            dropped_ing_reasons["filtered_noise_or_fluff"] += 1

        raw_usage = str(rec.get("usage") or "") or bleed.get("usage", "")
        usage_clean, sto_from_usage = clean_usage(raw_usage, pname)

        raw_storage = str(rec.get("storage") or "") or bleed.get("storage", "") or (sto_from_usage or "")
        storage_clean = clean_storage(raw_storage, pname)

        summary_clean = clean_summary(str(rec.get("summary") or ""), pname)

        origin_clean = None
        if rec.get("origin"):
            o = trim_clean_sentence(_capitalize_first(normalize_typography(str(rec["origin"]))), 120)
            if len(o) >= 4 and not _PLACEHOLDER_ING_RE.search(o):
                origin_clean = o

        entry: dict[str, Any] = {
            "product_name": pname,
            "brand": rec.get("brand", ""),
            "barcode": rec.get("barcode", ""),
            "source_url": rec.get("source_url", ""),
        }
        if ing_clean:
            entry["ingredients"] = ing_clean
            stats["ingredients"] += 1
            if ing_source:
                entry["ingredients_source"] = ing_source
            if rec.get("ingredients_source_url"):
                entry["ingredients_source_url"] = rec["ingredients_source_url"]
            if rec.get("ingredients_match_method"):
                entry["ingredients_match_method"] = rec["ingredients_match_method"]
        if usage_clean:
            entry["usage"] = usage_clean
            stats["usage"] += 1
            if rec.get("usage_source"):
                entry["usage_source"] = rec["usage_source"]
            if rec.get("usage_source_url"):
                entry["usage_source_url"] = rec["usage_source_url"]
        if storage_clean:
            entry["storage"] = storage_clean
            stats["storage"] += 1
            if rec.get("storage_source"):
                entry["storage_source"] = rec["storage_source"]
            if rec.get("storage_source_url"):
                entry["storage_source_url"] = rec["storage_source_url"]
        if origin_clean:
            entry["origin"] = origin_clean
            stats["origin"] += 1
            if rec.get("origin_source"):
                entry["origin_source"] = rec["origin_source"]
            if rec.get("origin_source_url"):
                entry["origin_source_url"] = rec["origin_source_url"]
        if summary_clean:
            entry["summary"] = summary_clean
            stats["summary"] += 1

        if any(k in entry for k in ("ingredients", "usage", "storage", "origin", "summary")):
            cleaned_records[pid] = entry

    after_metrics = audit_dataset(cleaned_records)
    print("\n=== AFTER SOTA 7-STAGE CLEANING ===")
    for k, v in after_metrics.items():
        print(f"  {k:38s}: {v}")
    print(f"\n  Filtered noisy/fluff ingredients removed: {dropped_ing_reasons['filtered_noise_or_fluff']}")

    new_payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "cleaned_pipeline": "hf-datatrove-openfoodfacts-sota-v1",
        "source": payload.get("source", "Thanh Lam Mart Official Product Listing"),
        "source_catalog_sha256": payload["source_catalog_sha256"],
        "total_catalog_products": payload["total_catalog_products"],
        "enriched_products": len(cleaned_records),
        "field_counts": dict(stats),
        "quality_audit_after": after_metrics,
        "records": cleaned_records,
    }
    DETAILS_PATH.write_text(
        json.dumps(new_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = DETAILS_PATH.stat().st_size / (1024 * 1024)
    print(
        f"\nSAVED clean {DETAILS_PATH.name} ({size_mb:.2f} MB) | "
        f"enriched={len(cleaned_records)}/{payload['total_catalog_products']} | fields={dict(stats)}"
    )

    # Print sample before/after verification on key products
    for pid in ("TLM_10141747", "TLM_10017865", "TLM_10005885", "TLM_10014578", "TLM_10601154", "TLM_10194215"):
        r = cleaned_records.get(pid)
        print(f"\n  [{pid}] {r['product_name'] if r else 'DROPPED'}")
        if r:
            for k in ("ingredients", "usage", "storage"):
                if r.get(k):
                    print(f"    {k:11s}: {r[k][:220]}")


if __name__ == "__main__":
    main()
