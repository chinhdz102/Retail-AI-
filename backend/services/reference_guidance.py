"""Small, auditable reference retrieval, separate from SKU facts and dialogue."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _entries():
    path = Path(__file__).resolve().parents[2] / "data" / "retail_reference_guidance.json"
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def relevant_guidance(*texts):
    text = unicodedata.normalize("NFC", " ".join(texts)).casefold()

    def contains(phrases):
        return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text)
                   for phrase in phrases)

    return [{key: entry[key] for key in ("id", "principles", "sources")}
            for entry in _entries() if contains(entry["triggers"])
            and (not entry.get("context") or contains(entry["context"]))]
