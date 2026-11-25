"""Independent decoder disagreement is a reason to confirm, never to rewrite.

These are conservative development heuristics, not calibrated correctness
probabilities. Speaker/microphone validation is still required.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from backend.services.speech_text import vietnamese_integer

_DIGITS = {word: value for value, word in enumerate(
    ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
)} | {"mốt": 1, "tư": 4, "lăm": 5}
_SCALES = {"nghìn": 1000, "ngàn": 1000, "triệu": 1_000_000, "tỷ": 1_000_000_000}
_NUMBER_WORDS = {*_DIGITS, *_SCALES, "trăm", "mười", "mươi", "linh", "lẻ", "phẩy"}
_UNITS = {
    "lít": ("volume_ml", 1000), "mililít": ("volume_ml", 1),
    "kg": ("weight_g", 1000), "kilôgam": ("weight_g", 1000),
    "g": ("weight_g", 1), "gam": ("weight_g", 1), "gram": ("weight_g", 1),
    "đồng": ("money_vnd", 1),
}
# Whisper confidence can remain high on substantively different transcripts.
# This is a confirmation threshold, not an accuracy/confidence calibration.
SUBSTANTIVE_DISAGREEMENT_THRESHOLD = 0.20


def _number_value(words: list[str]) -> float | None:
    if not words:
        return None
    if "phẩy" in words:
        index = words.index("phẩy")
        integer, fraction = _number_value(words[:index]), words[index + 1:]
        if integer is None or not fraction or any(word not in _DIGITS for word in fraction):
            return None
        return integer + int("".join(str(_DIGITS[w]) for w in fraction)) / 10 ** len(fraction)
    total = group = digit = 0
    for word in words:
        if word in _DIGITS:
            digit += _DIGITS[word]
        elif word == "trăm":
            group += (digit or 1) * 100
            digit = 0
        elif word in {"mười", "mươi"}:
            group += (digit or 1) * 10
            digit = 0
        elif word in _SCALES:
            total += (group + digit) * _SCALES[word]
            group = digit = 0
    return float(total + group + digit)


def _quantity_spans(words: list[str]) -> list[tuple[int, int, str, float]]:
    """Compare amounts/units independently of the decoder's global confidence.

    This is an uncertainty cue, never a replacement transcript or an assertion
    that one decoder is right. Equivalent written/spoken numbers compare equal.
    """
    anchors = []
    index = 0
    while index < len(words):
        if words[index] not in _NUMBER_WORDS:
            index += 1
            continue
        start = index
        while index < len(words) and words[index] in _NUMBER_WORDS:
            index += 1
        number = _number_value(words[start:index])
        unit = words[index] if index < len(words) else ""
        if number is not None and unit in _UNITS:
            dimension, multiplier = _UNITS[unit]
            if index + 1 < len(words) and words[index + 1] == "rưỡi":
                number += 0.5
            end = index + 1 + int(index + 1 < len(words) and words[index + 1] == "rưỡi")
            anchors.append((start, end, dimension, round(number * multiplier, 6)))
        elif number is not None and words[index - 1] in _SCALES:
            anchors.append((start, index, "money_vnd", number))
    return anchors


def quantity_anchors(words: list[str]) -> list[tuple[str, float]]:
    return [(dimension, number) for _, _, dimension, number in _quantity_spans(words)]


def _canonical_quantity_words(words: list[str]) -> list[str]:
    """Equivalent spoken units must not become lexical disagreement at a lower threshold."""
    result = []
    cursor = 0
    for start, end, dimension, number in _quantity_spans(words):
        result.extend(words[cursor:start])
        result.append(f"{dimension}={number:g}")
        cursor = end
    result.extend(words[cursor:])
    return result


def comparison_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.casefold())

    def spoken(match):
        raw = match[0]
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
            raw = re.sub(r"[.,]", "", raw)
        if re.fullmatch(r"\d+", raw) and len(raw) <= 12:
            return " " + vietnamese_integer(int(raw)) + " "
        pieces = re.split(r"[.,]", raw, maxsplit=1)
        if len(pieces) == 2 and len(pieces[0]) <= 12:
            return (
                " "
                + vietnamese_integer(int(pieces[0]))
                + " phẩy "
                + " ".join(vietnamese_integer(int(c)) for c in pieces[1] if c.isdigit())
                + " "
            )
        return " " + raw + " "

    text = re.sub(r"\d+(?:[.,]\d+)*", spoken, text)
    text = re.sub(r"\b(?:mi\s+li\s+l[ií]t|ml)\b", "mililít", text)
    text = re.sub(r"\b(?:lit|l)\b", "lít", text)
    return re.findall(r"[^\W_]+", text)


def needs_confirmation(primary: str, secondary: str, quality: dict) -> tuple[bool, float]:
    left, right = comparison_words(primary), comparison_words(secondary)
    if not left or not right:
        return bool(left), 1.0
    distance = 1 - SequenceMatcher(
        None, _canonical_quantity_words(left), _canonical_quantity_words(right), autojunk=False,
    ).ratio()
    # Preserve a short negation that changes the buying constraint. Merely
    # formatting a number as digits must not manufacture disagreement.
    negations = {"không", "đừng", "chớ", "chưa"}
    negation_changed = any(left.count(word) != right.count(word) for word in negations)

    # A matching count can still reverse the requirement (không đường versus
    # không đá). Compare a bounded cue/scope as well; numeric formatting was
    # normalized above. Disagreement asks for confirmation, never a new word.
    def scopes(words):
        return [tuple(words[i : i + 6]) for i, word in enumerate(words) if word in negations]

    scope_changed = scopes(left) != scopes(right)
    quantity_changed = quantity_anchors(left) != quantity_anchors(right)
    return bool(
        negation_changed or scope_changed or quantity_changed
        or distance >= SUBSTANTIVE_DISAGREEMENT_THRESHOLD
    ), distance


def catalog_anchor_disagreement(primary: str, secondary: str, names) -> bool:
    """Flag exact catalog identities present in only one independent decode.

    Catalog names are used for comparison, never injected as a reference answer
    or used to replace a word. This catches a consequential short entity error
    that a sentence-wide edit distance can hide. Agreement is not correctness.
    """
    left = " " + " ".join(comparison_words(primary)) + " "
    right = " " + " ".join(comparison_words(secondary)) + " "
    for name in names:
        if not isinstance(name, str) or len(name) > 100:
            continue
        words = comparison_words(name)
        if len("".join(words)) < 3:
            continue  # Single letters and tiny acronyms are ambiguous in speech.
        phrase = " " + " ".join(words) + " "
        if (phrase in left) != (phrase in right):
            return True
    return False
