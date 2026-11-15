"""Read explicit retail sizes from text without modifying that text.

The advice planner may express a customer's spoken size as a numeric constraint.
This parser verifies its quoted source; it is not an ASR correction dictionary.
Ambiguous/unsupported number syntax deliberately yields no measurement.
"""
from __future__ import annotations

import re
import unicodedata


_DIGITS = {"không": 0, "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4,
           "tư": 4, "năm": 5, "lăm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9}
_WORD = r"(?:không|một|mốt|hai|ba|bốn|tư|năm|lăm|sáu|bảy|tám|chín|mười|mươi|trăm|nghìn|ngàn|triệu|lẻ|linh|phẩy|nửa)"
_NUMBER = rf"(?:\d+(?:[.,]\d+)*|{_WORD}(?:\s+{_WORD})*)"
_UNITS = {
    "ml": ("volume_ml", 1), "mi li lít": ("volume_ml", 1),
    "mi li lit": ("volume_ml", 1), "mililit": ("volume_ml", 1),
    "l": ("volume_ml", 1000), "lit": ("volume_ml", 1000), "lít": ("volume_ml", 1000),
    "g": ("mass_g", 1), "gr": ("mass_g", 1), "gam": ("mass_g", 1), "gram": ("mass_g", 1),
    "kg": ("mass_g", 1000), "cân": ("mass_g", 1000), "ký": ("mass_g", 1000),
    "kí": ("mass_g", 1000), "ki lô gam": ("mass_g", 1000),
    "cm": ("length_cm", 1), "xen ti mét": ("length_cm", 1),
    "xăng ti mét": ("length_cm", 1), "centimet": ("length_cm", 1),
    "mm": ("length_cm", .1), "mi li mét": ("length_cm", .1),
}
_MEASUREMENT = re.compile(
    rf"(?<![\w.,])(?P<number>{_NUMBER})\s*(?P<unit>"
    + "|".join(re.escape(unit) for unit in sorted(_UNITS, key=len, reverse=True))
    + r")(?!\w)(?P<half>\s+rưỡi\b)?",
)


def _small_number(tokens: list[str]) -> int | None:
    hundreds = 0
    has_hundreds = len(tokens) >= 2 and tokens[0] in _DIGITS and tokens[1] == "trăm"
    if has_hundreds:
        hundreds = _DIGITS[tokens[0]] * 100
        tokens = tokens[2:]
    if not tokens:
        return hundreds if has_hundreds else None
    if has_hundreds and tokens[0] in {"lẻ", "linh"}:
        tokens = tokens[1:]
        return hundreds + _DIGITS[tokens[0]] if len(tokens) == 1 and tokens[0] in _DIGITS else None
    if len(tokens) == 1:
        return hundreds + (10 if tokens[0] == "mười" else _DIGITS[tokens[0]]) \
            if tokens[0] == "mười" or tokens[0] in _DIGITS else None
    if tokens[0] == "mười" and len(tokens) == 2 and tokens[1] in _DIGITS:
        return hundreds + 10 + _DIGITS[tokens[1]]
    if (len(tokens) in {2, 3} and _DIGITS.get(tokens[0], 0) >= 2
        and tokens[1] == "mươi" and (len(tokens) == 2 or tokens[2] in _DIGITS)):
        return hundreds + _DIGITS[tokens[0]] * 10 + (_DIGITS[tokens[2]] if len(tokens) == 3 else 0)
    return None


def _integer_words(tokens: list[str]) -> int | None:
    total = 0
    for scale_words, multiplier in (({"triệu"}, 1_000_000), ({"nghìn", "ngàn"}, 1000)):
        positions = [i for i, word in enumerate(tokens) if word in scale_words]
        if not positions:
            continue
        if len(positions) != 1:
            return None
        index = positions[0]
        group = _small_number(tokens[:index])
        if group is None or group == 0:
            return None
        total += group * multiplier
        tokens = tokens[index + 1:]
    remainder = _small_number(tokens) if tokens else 0
    return total + remainder if remainder is not None else None


def number_value(text: str) -> float | None:
    text = unicodedata.normalize("NFC", text.casefold()).strip()
    # Vietnamese labels use dots for groups of three and commas for decimals.
    # Keep ordinary decimal dots too (1.5L, 0.500L); never partially parse a
    # malformed grouped number as its trailing digits.
    if re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?", text):
        return float(text.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return float(text.replace(",", "."))
    if text == "nửa":
        return .5
    parts = text.split(" phẩy ")
    if len(parts) > 2:
        return None
    integer = _integer_words(parts[0].split())
    if integer is None:
        return None
    if len(parts) == 1:
        return float(integer)
    fraction = parts[1].split()
    if not fraction or any(token not in _DIGITS for token in fraction):
        return None
    return integer + int("".join(str(_DIGITS[token]) for token in fraction)) / 10 ** len(fraction)


def package_values(text: str, dimension: str) -> set[float]:
    found = set()
    text = unicodedata.normalize("NFC", text.casefold())
    # Separate a numeric pack count from its per-package size (2x500ml).
    # Do not split an alphanumeric SKU such as ABx500ml or sum the two bottles.
    text = re.sub(r"(?<![\w.,])(\d+)\s*[x×]\s*(?=\d)", r"\1 × ", text)
    for match in _MEASUREMENT.finditer(text):
        unit_dimension, multiplier = _UNITS[match["unit"]]
        value = number_value(match["number"])
        if dimension == unit_dimension and value is not None and value > 0:
            found.add((value + (.5 if match["half"] else 0)) * multiplier)
    return found
