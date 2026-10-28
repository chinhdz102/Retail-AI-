"""Rank conversational needs against catalog titles; never infer product benefits."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any

from backend.products.catalog import QUERY_STOP_WORDS, ProductSnapshot
from backend.products.category_taxonomy import diversify_by_category_leaf, normalize_category_text
from backend.products.repository import ProductRepository
from backend.services.vietnamese_intent import recommendation_content

_CONVERSATIONAL_WORDS = QUERY_STOP_WORDS | frozenset(
    "dao nay tao may m t tui minh ban giup gioi thieu de xuat mot vai loai nhung cac "
    "san pham nao gi mua can muon thu tim chon dum ho voi nhe nha nho nhieu qua "
    "hoi bi dang rat hay nhanh lam de dung duoc phu hop cho nha dau tien truoc sau chua biet "
    "nen the nao nhu the sao co khong thich lay bo tranh hang hieu nhan uu tien "
    "ngan sach gia tam duoi toi da khong qua dong nghin ngan chuc tram k vnd "
    "chac chan dam bao chua khoi vinh vien ngon "
    "can could please recommend suggest some any my me i you for with without "
    "want need looking have help would should use buy choose avoid except".split()
)
_NEGATED_BRAND = re.compile(
    r"\b(?:khong(?:\s+(?:phai|thich|lay|mua|dung|chon))?|tranh|tru|bo\s+qua|bo|loai bo|"
    r"dung\s+(?:lay|chon|mua)|no|avoid|without|except)\s+"
    r"(?:(?:san pham|thuong hieu|nhan hang|nhan|hang|loai|brand|cua)\s+)*$"
)
_NEGATED_BRAND_SUFFIX = re.compile(
    r"^\s*(?:thi\s+)?(?:bo\s+qua|loai\s+bo|loai\s+tru|khong\s+(?:lay|chon|mua|dung)|"
    r"dung\s+(?:lay|chon|mua)|avoid|except|without)\b"
)


def _words(text: str) -> set[str]:
    return set(normalize_category_text(text).split())


def preference_terms(text: str, query: str) -> tuple[str, ...]:
    """Keep need words separate from the hard product identity query."""
    excluded = _CONVERSATIONAL_WORDS | _words(query)
    return tuple(
        dict.fromkeys(
            word
            for word in recommendation_content(text).split()
            if word not in excluded and len(word) > 1 and word.isalpha()
        )
    )


_PREPARATION_NAMES = {
    "lau": "lẩu",
    "nuong": "nướng",
    "chien": "chiên",
    "xao": "xào",
    "hap": "hấp",
    "ham": "hầm",
    "luoc": "luộc",
    "ran": "rán",
}
_PREPARATION_PREFIXES = {
    "nhung": "nhúng",
    "an": "ăn",
    "nau": "nấu",
    "lam": "làm",
    "cho": "cho",
    "de": "để",
}


def preparation_terms(text: str) -> frozenset[str]:
    """Identify preparation wording while preserving Vietnamese accent distinctions."""
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text.casefold()))
    matches: set[str] = set()
    unaccented_input = text.isascii()
    for prefix, method in zip(words, words[1:]):
        prefix_key = normalize_category_text(prefix)
        method_key = normalize_category_text(method)
        if (
            prefix_key in _PREPARATION_PREFIXES
            and prefix in {prefix_key, _PREPARATION_PREFIXES[prefix_key]}
            and method_key in _PREPARATION_NAMES
            and (
                method == _PREPARATION_NAMES[method_key]
                or (unaccented_input and method == method_key)
            )
        ):
            matches.add(method_key)
    return frozenset(matches)


def title_mentions_preparation(title: str, methods: frozenset[str]) -> bool:
    """Use literal catalog title wording as evidence, with accents preserved."""
    native_words = set(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", title.casefold())))
    return any(_PREPARATION_NAMES[method] in native_words for method in methods)


def excluded_catalog_brands(text: str, brands: list[str]) -> frozenset[str]:
    normalized = normalize_category_text(text)
    excluded = set()
    for brand in brands:
        key = normalize_category_text(brand)
        if not key or f" {key} " not in f" {normalized} ":
            continue
        for match in re.finditer(rf"(?<!\w){re.escape(key)}(?!\w)", normalized):
            prefix = normalized[max(0, match.start() - 100) : match.start()]
            suffix = normalized[match.end() : match.end() + 48]
            if _NEGATED_BRAND.search(prefix) or _NEGATED_BRAND_SUFFIX.search(suffix):
                excluded.add(key)
    return frozenset(excluded)


def remove_excluded_brand_phrases(text: str, brands: list[str]) -> str:
    """Remove catalog-grounded negative brand clauses from positive retrieval text."""
    normalized = normalize_category_text(text)
    excluded = excluded_catalog_brands(text, brands)
    for brand in sorted(excluded, key=len, reverse=True):
        key = re.escape(brand)
        prefix_clause = re.compile(
            r"\b(?:khong(?:\s+(?:phai|thich|lay|mua|dung|chon))?|tranh|tru|"
            r"bo(?:\s+qua)?|loai\s+(?:bo|tru)|dung\s+(?:lay|chon|mua)|"
            r"no|avoid|without|except)\s+"
            r"(?:(?:san pham|thuong hieu|nhan hang|nhan|hang|loai|brand|cua)\s+)*"
            rf"{key}\b"
        )
        suffix_clause = re.compile(
            r"\b(?:(?:san pham|thuong hieu|nhan hang|nhan|hang|loai|brand)\s+)*"
            rf"{key}\s+(?:thi\s+)?"
            r"(?:bo\s+qua|loai\s+(?:bo|tru)|khong\s+(?:lay|chon|mua|dung)|"
            r"dung\s+(?:lay|chon|mua)|avoid|except|without)\b"
        )
        normalized = prefix_clause.sub(" ", normalized)
        normalized = suffix_clause.sub(" ", normalized)
    return " ".join(normalized.split())


def search_with_preferences(
    repository: ProductRepository,
    criteria: dict[str, Any],
    text: str,
) -> tuple[tuple[ProductSnapshot, ...], dict[str, list[str]]]:
    """Rerank with at most three extra searches, preserving every hard filter.

    Need words are soft retrieval signals. Evidence is a literal title match,
    not verified efficacy, safety, or suitability. Additional searches recover
    matching products outside the first page without broadening the category.
    """
    limit = int(criteria.get("limit", 5))
    terms = preference_terms(text, str(criteria.get("query") or ""))
    if not text or not terms:
        return tuple(repository.search(**criteria)), {}

    pool_criteria = {**criteria, "limit": 20}
    initial = repository.search(**pool_criteria)
    pool = {product.product_id: product for product in initial}
    taxonomy_words = set().union(
        *(_words(product.category_hierarchy or product.category) for product in initial)
    )
    terms = tuple(term for term in terms if term not in taxonomy_words)
    vocabulary_products = getattr(repository, "products", None) or initial
    title_counts: Counter[str] = Counter()
    for product in vocabulary_products:
        title_counts.update(_words(product.name))
    probes = sorted(
        (term for term in terms if title_counts[term]),
        key=lambda term: (title_counts[term], -len(term), term),
    )[:3]
    for term in probes:
        probe = {**pool_criteria, "query": f"{criteria.get('query') or ''} {term}".strip()}
        pool.update((product.product_id, product) for product in repository.search(**probe))

    brands = list({product.brand for product in vocabulary_products if product.brand})
    excluded = excluded_catalog_brands(text, brands)
    candidates = [
        product
        for product in pool.values()
        if normalize_category_text(product.brand) not in excluded
    ]
    positive_terms = set(terms) - set(" ".join(excluded).split())
    evidence = {
        product.product_id: sorted(positive_terms & _words(product.name)) for product in candidates
    }
    frequencies = Counter(term for matches in evidence.values() for term in matches)

    def score(product: ProductSnapshot) -> float:
        return sum(
            math.log1p(len(candidates) / frequencies[term]) for term in evidence[product.product_id]
        )

    ranked = sorted(candidates, key=score, reverse=True)
    matched = [product for product in ranked if evidence[product.product_id]]
    selected = tuple(
        diversify_by_category_leaf(matched or ranked, limit)
        if criteria.get("diversify_by_subcategory")
        else (matched or ranked)[:limit]
    )
    return selected, {
        product.product_id: evidence[product.product_id]
        for product in selected
        if evidence[product.product_id]
    }
