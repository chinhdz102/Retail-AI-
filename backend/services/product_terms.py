"""Source-backed equivalent product terms, scoped to their store departments.

These are retrieval vocabulary, never answer templates or ASR corrections.
Exact brand, variant and measurement filters deliberately do not use this layer.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def _text(value):
    return " ".join(unicodedata.normalize("NFC", value.casefold()).split())


@lru_cache(maxsize=1)
def _groups():
    source = Path(__file__).resolve().parents[2] / "data" / "vietnamese_product_terms.json"
    return json.loads(source.read_text(encoding="utf-8"))["groups"]


def _applicable(category):
    return [group for group in _groups()
            if not category or any(_text(category) == _text(c) for c in group["categories"])]


def _pattern(term):
    return r"(?<!\w)" + re.escape(_text(term)) + r"(?!\w)"


def identity_terms(value, category=None):
    """Canonical accented identity, never a fuzzy/diacritic correction."""
    value = _text(value)
    for group in _applicable(category):
        for term in sorted(group["terms"], key=len, reverse=True):
            value = re.sub(_pattern(term), _text(group["terms"][0]), value)
    return value


def equivalent_queries(value, category=None):
    """Bounded equivalent searches, preserving every surrounding query word."""
    queries = [_text(value)]
    for group in _applicable(category):
        for query in queries[:]:
            for term in group["terms"]:
                if re.search(_pattern(term), query):
                    for alternative in group["terms"]:
                        candidate = re.sub(_pattern(term), _text(alternative), query)
                        if candidate not in queries:
                            queries.append(candidate)
        queries = queries[:8]
    return queries[1:]


def relevant_equivalences(*texts):
    """External vocabulary evidence for composition/review, not inferred facts."""
    return [group["terms"] for group in _groups()
            if any(re.search(_pattern(term), _text(text))
                   for text in texts for term in group["terms"])]


def term_categories(value):
    """Department scope supplied by verified vocabulary rather than a model."""
    return {category for group in _groups()
            if any(re.search(_pattern(term), _text(value)) for term in group["terms"])
            for category in group["categories"]}
