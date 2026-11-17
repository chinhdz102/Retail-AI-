"""Vietnamese retail number aliases for Azure TTS, with escaped visible text.

Only unambiguous currency/measurement tokens are expanded. IDs, URLs and unknown
formats remain literal. This is a bounded text-normalization layer, not a model
of SKU pronunciation or an assessment of perceived speech quality.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape, quoteattr

_DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES = ("", "nghìn", "triệu", "tỷ", "nghìn tỷ")
_TOKEN = re.compile(
    r"(?<![\w.,/:+\-])(?P<number>[0-9][0-9.,]*)[ \t]*"
    r"(?P<unit>vnd|đ|₫|kg|gr|g|ml|lít|lit|l)(?!\w)", re.I,
)
_INTEGER = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)")
_DECIMAL = re.compile(r"[0-9]+(?:[,.][0-9]{1,6})?")
_UNITS = {"ml": "mi li lít", "l": "lít", "lít": "lít", "lit": "lít",
          "kg": "ki lô gam", "g": "gam", "gr": "gam"}


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


def _spoken_alias(number: str, unit: str) -> str | None:
    # Preserve leading-zero identifiers, unsupported magnitudes and ambiguity.
    digits = number.replace(".", "").replace(",", "")
    if len(digits) > 15 or (len(number) > 1 and number[0] == "0" and number[1].isdigit()):
        return None
    unit = unit.casefold()
    if _INTEGER.fullmatch(number):
        words = vietnamese_integer(int(number.replace(".", "")))
    elif unit not in {"đ", "₫", "vnd"} and _DECIMAL.fullmatch(number):
        integer, fraction = re.split(r"[,.]", number)
        words = f"{vietnamese_integer(int(integer))} phẩy " + " ".join(
            _DIGITS[int(digit)] for digit in fraction
        )
    else:
        return None
    return f"{words} đồng" if unit in {"đ", "₫", "vnd"} else f"{words} {_UNITS[unit]}"


def retail_ssml_text(text: str, language: str) -> str:
    """Preserve written text and XML-escape exactly once; user markup is inert."""
    if language != "vi":
        return escape(text)
    output = []
    cursor = 0
    for match in _TOKEN.finditer(text):
        output.append(escape(text[cursor:match.start()]))
        original = escape(match.group(0))
        alias = _spoken_alias(match.group("number"), match.group("unit"))
        output.append(f"<sub alias={quoteattr(alias)}>{original}</sub>" if alias else original)
        cursor = match.end()
    output.append(escape(text[cursor:]))
    return "".join(output)
