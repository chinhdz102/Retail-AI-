"""Vietnamese retail number aliases for Azure TTS, with escaped visible text.

Only unambiguous currency/measurement/retail tokens are expanded via SSML
``<sub alias="...">`` so visible text remains identical. IDs, URLs and unknown
formats remain literal. This is a bounded text-normalization layer, not a model
of SKU pronunciation or an assessment of perceived speech quality.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape, quoteattr

_DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES = ("", "nghìn", "triệu", "tỷ", "nghìn tỷ")
_INTEGER = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)")
_DECIMAL = re.compile(r"[0-9]+(?:[,.][0-9]{1,6})?")

_UNITS = {
    "ml": "mi li lít",
    "l": "lít",
    "lít": "lít",
    "lit": "lít",
    "kg": "ki lô gam",
    "g": "gam",
    "gr": "gam",
    "mg": "mi li gam",
    "kcal": "ki lô ca lo",
    "ppm": "pê pê em",
}

_SALE_UNIT_WORDS = {
    "chai": "chai",
    "hộp": "hộp",
    "gói": "gói",
    "túi": "túi",
    "lon": "lon",
    "thùng": "thùng",
    "lốc": "lốc",
    "tuýp": "tuýp",
    "can": "can",
    "bộ": "bộ",
    "miếng": "miếng",
    "cuộn": "cuộn",
    "bịch": "bịch",
    "lọ": "lọ",
    "hũ": "hũ",
    "khay": "khay",
    "vỉ": "vỉ",
    "cây": "cây",
    "quả": "quả",
    "cái": "cái",
}

_STATIC_ALIASES = {
    "co₂": "xê ô hai",
    "p/s": "pê ét",
    "3in1": "ba trong một",
    "2in1": "hai trong một",
    "nrc": "nước rửa chén",
    "gvs": "giấy vệ sinh",
    "kdr": "kem đánh răng",
    "nxv": "nước xả vải",
}

# Order matters: compound unit-prices (e.g. "20.957đ/100g", "15.900đ/Chai")
# and time ranges match before standalone numbers/units.
_RETAIL_TOKEN = re.compile(
    r"(?P<unit_price>"
    r"(?<![\w.,/:+\-])(?P<up_num>[0-9][0-9.,]*)[ \t]*(?P<up_cur>vnd|đ|₫)[ \t]*/[ \t]*"
    r"(?:(?P<up_dnum>[0-9][0-9.,]*)[ \t]*(?P<up_dunit>kg|gr|g|ml|lít|lit|l)|"
    r"(?P<up_munit>kg|gr|g|ml|lít|lit|l|chai|hộp|gói|túi|lon|thùng|lốc|tuýp|can|bộ|miếng|"
    r"cuộn|bịch|lọ|hũ|khay|vỉ|cây|quả|cái))(?!\w))"
    r"|(?P<time_range>"
    r"(?<![\w.,/:+\-])(?P<tr_a>[0-9]{1,3})[ \t]*[-–][ \t]*(?P<tr_b>[0-9]{1,3})[ \t]*"
    r"(?P<tr_unit>phút|giây|giờ|ngày|lần)(?!\w))"
    r"|(?P<percent>"
    r"(?<![\w.,/:+\-])(?P<pct_num>[0-9][0-9.,]*)[ \t]*%)"
    r"|(?P<amount>"
    r"(?<![\w.,/:+\-])(?P<number>[0-9][0-9.,]*)[ \t]*"
    r"(?P<unit>vnd|đ|₫|kcal|ppm|kg|gr|mg|g|ml|lít|lit|l)(?!\w))"
    r"|(?P<static>"
    r"(?<![\w/-])(?:CO₂|P/S|3in1|2in1)(?![\w/-]))"
    r"|(?P<abbr>"
    r"(?<![\w/-])(?:NRC|GVS|KDR|NXV)(?![\w/-]))"
    r"|(?P<omo_ng>(?<=OMO )NG(?![\w/-]))",
    re.I,
)


def _read_three_digits(value: int, *, force_hundreds: bool) -> str:
    hundreds, remainder = divmod(value, 100)
    words = [f"{_DIGITS[hundreds]} trăm"] if hundreds or force_hundreds else []
    if not remainder:
        return " ".join(words)
    tens, units = divmod(remainder, 10)
    if tens:
        words.append("mười" if tens == 1 else f"{_DIGITS[tens]} mươi")
        if units:
            words.append("lăm" if units == 5 else "mốt" if units == 1 and tens > 1
                         else "tư" if units == 4 and tens > 1 else _DIGITS[units])
    else:
        if hundreds or force_hundreds:
            words.append("lẻ")
        words.append(_DIGITS[units])
    return " ".join(words)


def vietnamese_integer(value: int) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 10**15:
        raise ValueError("Integer is outside supported retail range")
    if value == 0:
        return _DIGITS[0]
    groups = []
    while value:
        value, group = divmod(value, 1000)
        groups.append(group)
    words = []
    for index in range(len(groups) - 1, -1, -1):
        if not groups[index]:
            continue
        words.append(_read_three_digits(groups[index], force_hundreds=index < len(groups) - 1))
        if index:
            words.append(_SCALES[index])
    return " ".join(words)


def _spoken_number(number: str, *, allow_decimal: bool) -> str | None:
    digits = number.replace(".", "").replace(",", "")
    if len(digits) > 15 or (len(number) > 1 and number[0] == "0" and number[1].isdigit()):
        return None
    if _INTEGER.fullmatch(number):
        return vietnamese_integer(int(number.replace(".", "")))
    if allow_decimal and _DECIMAL.fullmatch(number):
        integer, fraction = re.split(r"[,.]", number)
        return f"{vietnamese_integer(int(integer))} phẩy " + " ".join(
            _DIGITS[int(digit)] for digit in fraction
        )
    return None


def _spoken_alias(number: str, unit: str) -> str | None:
    # Preserve leading-zero identifiers, unsupported magnitudes and ambiguity.
    unit_key = unit.casefold()
    is_currency = unit_key in {"đ", "₫", "vnd"}
    words = _spoken_number(number, allow_decimal=not is_currency)
    if words is None:
        return None
    return f"{words} đồng" if is_currency else f"{words} {_UNITS[unit_key]}"


def _match_alias(match: re.Match[str]) -> str | None:
    if match.group("unit_price"):
        price_words = _spoken_number(match.group("up_num"), allow_decimal=False)
        if price_words is None:
            return None
        dnum = match.group("up_dnum")
        dunit = match.group("up_dunit")
        if dnum and dunit:
            denom_words = _spoken_alias(dnum, dunit)
            if denom_words is None:
                return None
            return f"{price_words} đồng trên {denom_words}"
        munit = (match.group("up_munit") or "").casefold()
        spoken_unit = _UNITS.get(munit) or _SALE_UNIT_WORDS.get(munit)
        if not spoken_unit:
            return None
        return f"{price_words} đồng mỗi {spoken_unit}"
    if match.group("time_range"):
        a = _spoken_number(match.group("tr_a"), allow_decimal=False)
        b = _spoken_number(match.group("tr_b"), allow_decimal=False)
        if a is None or b is None:
            return None
        return f"{a} đến {b} {match.group('tr_unit').casefold()}"
    if match.group("percent"):
        pct_words = _spoken_number(match.group("pct_num"), allow_decimal=True)
        return f"{pct_words} phần trăm" if pct_words is not None else None
    if match.group("amount"):
        return _spoken_alias(match.group("number"), match.group("unit"))
    if match.group("static"):
        return _STATIC_ALIASES.get(match.group("static").casefold())
    if match.group("abbr"):
        raw_abbr = match.group("abbr")
        # Only expand uppercase Thanh Lam Mart abbreviations (avoid lowercase words).
        return _STATIC_ALIASES.get(raw_abbr.casefold()) if raw_abbr.isupper() else None
    if match.group("omo_ng"):
        return "nước giặt" if match.group("omo_ng") == "NG" else None
    return None


def retail_ssml_text(text: str, language: str) -> str:
    """Preserve written text and XML-escape exactly once; user markup is inert."""
    if language != "vi":
        return escape(text)
    # Soften prose semicolons to commas in spoken SSML so Azure vi-VN Neural
    # voice uses a natural brief breath pause instead of a long sentence stop.
    spoken_source = re.sub(r"\s*;\s*", ", ", text)
    output = []
    cursor = 0
    for match in _RETAIL_TOKEN.finditer(spoken_source):
        output.append(escape(spoken_source[cursor:match.start()]))
        original = escape(match.group(0))
        alias = _match_alias(match)
        output.append(f"<sub alias={quoteattr(alias)}>{original}</sub>" if alias else original)
        cursor = match.end()
    output.append(escape(spoken_source[cursor:]))
    return "".join(output)
