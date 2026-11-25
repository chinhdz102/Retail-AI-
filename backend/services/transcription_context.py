"""Prior conversation supplies vocabulary, never a guessed replacement transcript."""

from __future__ import annotations

import re
import unicodedata


def session_speech_terms(catalog, record, limit: int = 8) -> tuple[str, ...]:
    limit = max(0, min(32, limit))
    if not limit:
        return ()
    fallback = getattr(catalog, "list_speech_terms", lambda _: ())(limit)
    if record is None:
        return tuple(fallback)
    messages = [
        m.get("content", "")
        for m in record.messages[-6:]
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ][-2:]
    customer = unicodedata.normalize("NFC", " ".join(messages).casefold())
    explicit = []
    brands = getattr(catalog, "list_brand_names", lambda **_: ())(limit=300)
    for brand in brands:
        name = unicodedata.normalize("NFC", brand.casefold())
        if len(name) > 1 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", customer):
            explicit.append(brand)
    shopping = record.shopping
    ids = list(
        dict.fromkeys(
            [
                shopping.active_product_id,
                *shopping.selected_product_ids,
                *shopping.comparison_product_ids,
            ]
        )
    )
    get_product = getattr(catalog, "get_product_snapshot", lambda _: None)
    products = [p for pid in ids[:4] if pid and (p := get_product(pid)) is not None]
    terms = [
        *explicit,
        *(p.brand for p in products if p.brand),
        *(p.category for p in products),
        *fallback,
    ]
    result = []
    seen = set()
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        key = unicodedata.normalize("NFC", term.casefold().strip())
        if key not in seen:
            result.append(term)
            seen.add(key)
        if len(result) == limit:
            break
    return tuple(result)
