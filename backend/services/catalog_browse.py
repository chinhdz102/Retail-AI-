"""Conservative routing for shoppers listing more than one catalog category."""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.products.category_taxonomy import (
    normalize_category_text,
    resolve_catalog_category,
    resolve_catalog_category_alias,
)
from backend.services.query_understanding import understand_catalog_query
from backend.services.vietnamese_intent import normalize_vietnamese

_CATEGORY_LIST_SEPARATOR = re.compile(r"\b(?:va|voi|hay|hoac|and|or)\b")
_MAX_CATEGORY_GROUPS = 5
_BROWSE_CUE = re.compile(
    r"\b(?:co ban khong|con hang khong|dang ban|dang co|ban nhung gi|"
    r"co nhung loai|co cac loai|co loai nao|nhung loai nao|loai nao dang|"
    r"gom nhung|gom cac|nhan nao|available|what (?:types|kinds)|"
    r"which (?:types|kinds)|show me|list)\b"
)
_OUT_OF_SCOPE_CUE = re.compile(
    r"\b(?:so sanh|khac nhau|khac gi|ngon hon|ngon nhat|ban chay|"
    r"re nhat|gia|bao nhieu|o dau|ke nao|vi tri|cong dung|thanh phan|"
    r"tu van|recommend|suggest|best seller|most popular|cheaper|price|where)\b"
)


def explicit_catalog_category_list(
    text: str,
    category_hierarchies: Iterable[tuple[str, ...]],
) -> tuple[str, ...] | None:
    """Resolve a short list of distinct taxonomy categories for browse asks.

    This intentionally ignores product variants that share one category (for
    example beef and chicken flavors of instant noodles). Each side must resolve
    to an actual category in the active taxonomy, directly or through a known
    catalog synonym. It does not infer categories from arbitrary noun phrases.
    """
    normalized = " ".join(normalize_vietnamese(text).split())
    if not _BROWSE_CUE.search(normalized) or _OUT_OF_SCOPE_CUE.search(normalized):
        return None

    raw_chunks = re.split(r"[,;]", text)
    if any(not chunk.strip() for chunk in raw_chunks):
        return None

    segments: list[str] = []
    for raw_chunk in raw_chunks:
        chunk = " ".join(normalize_vietnamese(raw_chunk).split())
        separators = list(_CATEGORY_LIST_SEPARATOR.finditer(chunk))
        segment_start = 0
        for separator in separators:
            segments.append(chunk[segment_start : separator.start()].strip(" ,.!?;:"))
            segment_start = separator.end()
        segments.append(chunk[segment_start:].strip(" ,.!?;:"))
    if len(segments) > _MAX_CATEGORY_GROUPS or any(not segment for segment in segments):
        return None

    hierarchies = tuple(category_hierarchies)
    canonical_labels = {
        normalize_category_text(label): label
        for path in hierarchies
        for label in path
    }

    resolved: list[str] = []
    seen: set[str] = set()

    def add_category(label: str | None) -> bool:
        if label is None:
            return False
        canonical_key = normalize_category_text(label)
        if canonical_key not in seen:
            resolved.append(label)
            seen.add(canonical_key)
        return True

    for segment in segments:
        understanding = understand_catalog_query(segment, hierarchies)
        candidate = understanding.product_type
        match = resolve_catalog_category(candidate, hierarchies) if candidate else None
        if match is not None:
            label = canonical_labels.get(normalize_category_text(match.category))
            if add_category(label):
                continue

        direct = resolve_catalog_category(segment, hierarchies)
        if direct is not None and direct.exact_phrase:
            label = canonical_labels.get(normalize_category_text(direct.category))
            if add_category(label):
                continue

        alias = resolve_catalog_category_alias(segment, hierarchies)
        if alias is None:
            return None
        label = canonical_labels.get(normalize_category_text(alias.category))
        if not add_category(label):
            return None

    if len(resolved) < 2:
        return None
    return tuple(resolved)


def explicit_catalog_category_pair(
    text: str,
    category_hierarchies: Iterable[tuple[str, ...]],
) -> tuple[str, str] | None:
    """Compatibility wrapper for callers that need exactly two categories."""
    categories = explicit_catalog_category_list(text, category_hierarchies)
    if categories is None or len(categories) != 2:
        return None
    return categories[0], categories[1]
