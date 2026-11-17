"""Catalog price lists grounded in product taxonomy, without an LLM call."""

import asyncio
from collections import Counter, defaultdict, deque

from backend.products.repository import ProductRepository
from backend.services.retail_tools import ToolObservation
from backend.services.vietnamese_intent import normalize_vietnamese


def _words(value: str) -> tuple[str, ...]:
    return tuple(normalize_vietnamese(value).split())


async def lookup_price_products(
    repository: ProductRepository, query: str, *, limit: int = 5,
) -> ToolObservation:
    return await lookup_catalog_products(repository, query, ("price_vnd",), limit=limit)


async def lookup_catalog_products(
    repository: ProductRepository, query: str, fields: tuple[str, ...], *, limit: int = 5,
) -> ToolObservation:
    """Prefer the requested product type over products containing its name.

    Primary category labels come from the active catalog. Parent hierarchy
    matches alone must not make a bun a piece of meat. Retrieve a bounded pool
    before filtering so early lexical hits cannot crowd out the requested type.
    """
    try:
        pool_limit = max(100, len(getattr(repository, "products", ())))
        products = await asyncio.wait_for(
            asyncio.to_thread(repository.search, query=query, in_stock=False, limit=pool_limit),
            timeout=2.0,
        )
    except Exception:
        return ToolObservation(
            content={"count": 0},
            tool_name="search_products",
            status="TEMPORARY_FAILURE",
            source=repository.source_name,
            terminal=True,
        )
    query_words = _words(query)
    prefixes = {
        _words(p.category)
        for p in products
        if _words(p.category) and query_words[: len(_words(p.category))] == _words(p.category)
    }
    primary = max(prefixes, key=len, default=())
    if primary:
        products = [p for p in products if _words(p.category) == primary]
    else:
        typed = (
            [p for p in products if _words(p.category)[:1] == query_words]
            if len(query_words) == 1
            else []
        )
        # Product-type phrases must stay together. A bag of strawberry candy
        # may contain both words "dâu" and "gói", but is not "dầu gội".
        phrase = " " + " ".join(query_words) + " "
        literal = [p for p in products if phrase in " " + " ".join(_words(p.name)) + " "]
        if literal and not typed:
            products = literal
        # A broad noun can be the prefix of several real category labels,
        # e.g. milk types or leafy vegetables. Prefer those labels over a
        # candy/snack whose ingredients happen to mention the requested noun.
        if typed:
            roots = Counter((p.category_hierarchy or "").split(">")[0].strip() for p in typed)
            root = roots.most_common(1)[0][0]
            products = [
                p for p in typed if (p.category_hierarchy or "").split(">")[0].strip() == root
            ]
    # Interleave taxonomy children so broad questions do not show five
    # examples from just the first subcategory in the source file.
    groups = defaultdict(deque)
    for product in products:
        path = [part.strip() for part in (product.category_hierarchy or "").split(">")]
        child = next(
            (path[i + 1] for i, part in enumerate(path[:-1]) if _words(part) == primary),
            product.category,
        )
        groups[child].append(product)
    selected = []
    while groups and len(selected) < max(1, limit):
        for key in list(groups):
            if len(selected) == max(1, limit):
                break
            selected.append(groups[key].popleft())
            if not groups[key]:
                del groups[key]
    return ToolObservation(
        content={"count": len(selected), "category_match": bool(primary)},
        products=tuple(selected),
        tool_name="search_products",
        source=repository.source_name,
        requested_fields=fields,
        response_mode="DETERMINISTIC_FACT",
        terminal=True,
    )
