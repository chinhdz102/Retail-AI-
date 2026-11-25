"""Small, sourced reference corpus; general guidance is never SKU evidence."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from backend.services.shopping_language import resolve_possessive_size

GUIDANCE_PATH = Path(__file__).resolve().parents[2] / "data" / "shopping_guidance.json"


@lru_cache(maxsize=1)
def _entries() -> tuple[dict, ...]:
    return tuple(json.loads(GUIDANCE_PATH.read_text(encoding="utf-8"))["entries"])


_NON_FOOD_BABY_CARE_RE = re.compile(
    r"\b(?:sữa\s+tắm|tắm\s+gội|dầu\s+gội|tã\b|bỉm|khăn\s+ướt|bàn\s+chải|kem\s+đánh\s+răng|"
    r"nước\s+giặt|nước\s+xả)\b",
    re.IGNORECASE,
)
_FOOD_OR_DRINK_CUE_RE = re.compile(
    r"\b(?:ăn|uống|sữa\s+tươi|sữa\s+bột|sữa\s+chua|váng\s+sữa|bánh|kẹo|snack|cháo|ngũ\s+cốc)\b",
    re.IGNORECASE,
)


def shopping_guidance(text: str, *, limit: int = 3) -> list[dict]:
    """Match complete phrases, retaining accents (``chó`` is not ``cho``)."""
    def normalize(value: str) -> str:
        return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()

    resolved = resolve_possessive_size(text)
    normalized = " " + normalize(resolved) + " "
    is_non_food_baby_care = bool(
        _NON_FOOD_BABY_CARE_RE.search(resolved) and not _FOOD_OR_DRINK_CUE_RE.search(resolved)
    )
    matches = []
    for entry in _entries():
        if entry.get("id") == "cdc-child-food-texture" and is_non_food_baby_care:
            continue
        phrases = {normalize(term) for term in entry["match_any"]}
        score = sum(1 for phrase in phrases if f" {phrase} " in normalized)
        if score:
            matches.append((score, entry))
    matches.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [
        {key: value for key, value in entry.items() if key != "match_any"}
        for _, entry in matches[:max(0, limit)]
    ]
