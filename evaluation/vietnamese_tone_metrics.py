"""Accent-sensitive syllable alignment for ASR diagnostics, never text repair."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence

TONES = {"\u0300": "huyền", "\u0301": "sắc", "\u0303": "ngã", "\u0309": "hỏi", "\u0323": "nặng"}


def syllables(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text.casefold()))


def strip_tone(text: str) -> str:
    return unicodedata.normalize(
        "NFC", "".join(char for char in unicodedata.normalize("NFD", text) if char not in TONES)
    )


def tone(text: str) -> str:
    return next((TONES[c] for c in unicodedata.normalize("NFD", text) if c in TONES), "ngang")


def strip_diacritics(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).replace("đ", "d")


def align(reference: Sequence[str], hypothesis: Sequence[str]):
    table = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for i in range(len(reference) + 1):
        table[i][0] = i
    for j in range(len(hypothesis) + 1):
        table[0][j] = j
    for i, expected in enumerate(reference, start=1):
        for j, actual in enumerate(hypothesis, start=1):
            table[i][j] = min(
                table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + (expected != actual)
            )
    pairs = []
    i, j = len(reference), len(hypothesis)
    while i or j:
        if i and j and table[i][j] == table[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            pairs.append((reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif i and table[i][j] == table[i - 1][j] + 1:
            pairs.append((reference[i - 1], None))
            i -= 1
        else:
            pairs.append((None, hypothesis[j - 1]))
            j -= 1
    return table[-1][-1], list(reversed(pairs))


def score(reference: str, hypothesis: str) -> dict:
    expected, actual = syllables(reference), syllables(hypothesis)
    errors, pairs = align(expected, actual)
    toneless_errors, _ = align([strip_tone(w) for w in expected], [strip_tone(w) for w in actual])
    reference_chars = " ".join(expected)
    char_errors, _ = align(reference_chars, " ".join(actual))
    tone_pairs = [(a, b) for a, b in pairs if a and b and a != b and strip_tone(a) == strip_tone(b)]
    shape_pairs = [
        (a, b)
        for a, b in pairs
        if a
        and b
        and a != b
        and strip_tone(a) != strip_tone(b)
        and strip_diacritics(a) == strip_diacritics(b)
    ]
    return {
        "reference_syllables": len(expected),
        "syllable_errors": errors,
        "syllable_error_rate": errors / max(1, len(expected)),
        "tone_stripped_syllable_errors": toneless_errors,
        "character_errors": char_errors,
        "reference_characters": len(reference_chars),
        "tone_only_substitutions": len(tone_pairs),
        "vowel_shape_substitutions": len(shape_pairs),
        "tone_confusions": dict(Counter(f"{tone(a)} → {tone(b)}" for a, b in tone_pairs)),
        "tone_examples": [{"reference": a, "hypothesis": b} for a, b in tone_pairs],
        "exact": expected == actual,
    }
