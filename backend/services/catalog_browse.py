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

_CATEGORY_CONNECTOR = re.compile(r"\b(?:va|voi|hay|hoac|and|or)\b")
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


def explicit_catalog_category_pair(
    text: str,
    category_hierarchies: Iterable[tuple[str, ...]],
) -> tuple[str, str] | None:
    """Return two distinct taxonomy categories only for clear list/availability asks.

    This intentionally ignores product variants that share one category (for
    example beef and chicken flavors of instant noodles). Each side must resolve
    to an actual category in the active taxonomy, directly or through a known
    catalog synonym. It does not infer categories from arbitrary noun phrases.
    """
    normalized = " ".join(normalize_vietnamese(text).split())
    if not _BROWSE_CUE.search(normalized) or _OUT_OF_SCOPE_CUE.search(normalized):
        return None

    connectors = list(_CATEGORY_CONNECTOR.finditer(normalized))
    if len(connectors) != 1:
        return None

    connector = connectors[0]
    segments = (
        normalized[: connector.start()].strip(" ,.!?;:"),
        normalized[connector.end() :].strip(" ,.!?;:"),
    )
    hierarchies = tuple(category_hierarchies)
    canonical_labels = {
        normalize_category_text(label): label
        for path in hierarchies
        for label in path
    }

    resolved: list[str] = []
    for segment in segments:
        understanding = understand_catalog_query(segment, hierarchies)
        candidate = understanding.product_type
        match = resolve_catalog_category(candidate, hierarchies) if candidate else None
        if match is not None:
            label = canonical_labels.get(normalize_category_text(match.category))
            if label:
                resolved.append(label)
                continue

        direct = resolve_catalog_category(segment, hierarchies)
        if direct is not None and direct.exact_phrase:
            label = canonical_labels.get(normalize_category_text(direct.category))
            if label:
                resolved.append(label)
                continue

        alias = resolve_catalog_category_alias(segment, hierarchies)
        if alias is None:
            return None
        label = canonical_labels.get(normalize_category_text(alias.category))
        if not label:
            return None
        resolved.append(label)

    if len(resolved) != 2 or normalize_category_text(resolved[0]) == normalize_category_text(
        resolved[1]
    ):
        return None
    return resolved[0], resolved[1]
