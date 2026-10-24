from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from backend.data.catalog_import import analyze_dataset
from backend.products.catalog import (
    QUERY_STOP_WORDS,
    PriceOperator,
    ProductSnapshot,
    matches_price_constraint,
)
from backend.products.category_taxonomy import (
    collect_category_hierarchies,
    diversify_by_category_leaf,
    normalize_category_text,
)
from backend.products.planogram import recommended_shelf
from backend.products.postgres_repository import normalize_search_text
from backend.products.repository import (
    CatalogAdditions,
    catalog_brand_names,
    catalog_speech_terms,
)

_FILLER_WORDS = QUERY_STOP_WORDS | frozenset(
    {
        "loai",
        "cai",
        "mon",
        "gi",
        "nha",
        "nhe",
        "nhi",
        "vay",
        "size",
        "kich",
        "co",
        "may",
        "khac",
        "nhau",
        "vs",
        "versus",
        "container",
        "package",
        "packaging",
    }
)
_PACKAGING_WORDS = frozenset({"chai", "lon", "hop", "goi", "tui", "thung", "loc"})
_SIZE_PREFERENCE_WORDS = frozenset({"nho", "mini", "small", "smaller"})
_COMPARISON_PATTERN = re.compile(r"\b(?:khac\s+gi|khac\s+nhau|so\s+sanh|compare|comparison)\b")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_MEASURE_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(ml|milliliter|millilitre|lit|liter|litre|l|kg|g)\b"
)


@dataclass(frozen=True)
class _Candidate:
    product: ProductSnapshot
    title: str
    brand: str
    category: str
    uom: str
    identifiers: frozenset[str]
    words: tuple[str, ...]
    title_words: frozenset[str]
    brand_words: frozenset[str]
    category_words: frozenset[str]
    category_segment_words: tuple[frozenset[str], ...]
    category_segment_texts: tuple[str, ...]
    uom_words: frozenset[str]
    native_words: frozenset[str]
    baby_audience: bool


def _tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = normalize_search_text(value)
    normalized = re.sub(r"(?<=[a-z])['’](?=[a-z])", "", normalized)
    return tuple(_TOKEN_PATTERN.findall(normalized))


def _native_tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = unicodedata.normalize("NFC", value.casefold())
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _has_vietnamese_diacritic(value: str) -> bool:
    return "đ" in value or any(
        unicodedata.category(character) == "Mn"
        for character in unicodedata.normalize("NFD", value)
    )


@lru_cache(maxsize=128)
def _category_key(value: str) -> str:
    return normalize_search_text(value).strip()


_CATEGORY_PHRASE_ALIASES = {
    "drink": ("nuoc uong", "nuoc ngot", "nuoc suoi", "ca phe", "tra"),
    "snack": ("banh snack", "do an vat", "snack"),
    "dairy": ("sua", "sua chua", "pho mai"),
    "meal": ("thuc pham an lien", "thuc an san", "mi", "do an nong"),
    "skincare": ("cham soc da",),
    "haircare": (
        "cham soc toc",
        "dau goi",
        "dau xa",
        "dau goi tre em",
        "dau xa tre em",
    ),
    "oral_care": (
        "cham soc rang mieng",
        "kem danh rang",
        "ban chai danh rang",
        "nuoc suc mieng",
        "chi nha khoa",
    ),
    "home_cleaning": ("ve sinh nha",),
    "cosmetic": (
        "cham soc da",
        "cham soc toc",
        "cham soc rang mieng",
        "cham soc ca nhan",
        "cham soc suc khoe",
        "my pham",
        "my pham cham soc ca nhan",
        "oral care",
        "personal care",
    ),
    "diaper": ("ta bim", "ta quan", "giay quan"),
    "laundry": ("bot giat", "nuoc giat", "giat xa", "chat giat tay"),
}


def _category_alias_key(normalized: str) -> str:
    if normalized in _CATEGORY_PHRASE_ALIASES:
        return normalized
    underscored = normalized.replace(" ", "_")
    return underscored if underscored in _CATEGORY_PHRASE_ALIASES else normalized


@lru_cache(maxsize=128)
def _category_phrase_tokens(normalized: str) -> tuple[tuple[str, ...], ...]:
    alias_key = _category_alias_key(normalized)
    return tuple(
        _tokens(phrase)
        for phrase in _CATEGORY_PHRASE_ALIASES.get(alias_key, (normalized,))
        if _tokens(phrase)
    )


def _query_terms(query: str) -> tuple[list[str], bool, bool]:
    normalized = normalize_search_text(query).strip()
    explicit_diaper = bool(re.search(r"\b(?:bỉm|tã\s+quần)\b", query.casefold()))
    comparison = bool(_COMPARISON_PATTERN.search(normalized)) or bool(
        re.search(r"\b(?:vs|versus)\b", normalized)
    )
    normalized = re.sub(r"\bkhong\s+duong\b", "zero", normalized)
    normalized = re.sub(
        r"\b(?:sugar[\s-]*free|no\s+sugar|without\s+sugar)\b",
        "zero",
        normalized,
    )
    normalized = re.sub(r"\b(?:kids?|children?)\b", "be", normalized)
    snack_slang = not explicit_diaper and bool(
        re.search(r"\bbim\s+bim\b|\bbimbim\b", normalized)
    )
    if explicit_diaper:
        normalized = re.sub(r"\bbim\b", "ta", normalized)
    else:
        normalized = re.sub(r"\bbim\s+bim\b|\bbimbim\b", "snack", normalized)
    terms = [token for token in _tokens(normalized) if token not in _FILLER_WORDS]
    return terms, comparison, snack_slang


def _word_matches(token: str, words: frozenset[str]) -> bool:
    if token in words:
        return True
    if token.isalpha() and len(token) <= 3:
        return any(word.startswith(token) and word[len(token) :].isdigit() for word in words)
    if token.isdigit():
        return any(word.startswith(token) and word[len(token) :].isalpha() for word in words)
    return False


def _score(
    query: str,
    candidate: _Candidate,
    known_words: frozenset[str],
) -> float:
    normalized = normalize_search_text(query).strip().casefold()
    if normalized in candidate.identifiers:
        return 1000.0
    if normalized and normalized == candidate.title:
        return 900.0
    if normalized and candidate.title.startswith(normalized):
        return 800.0

    terms, comparison, snack_slang = _query_terms(query)
    if not terms:
        return 0.0

    field_words = (
        candidate.title_words,
        candidate.brand_words,
        candidate.category_words,
        candidate.uom_words,
    )
    field_weights = (3.0, 2.4, 1.3, 1.8)
    accented_query_words: dict[str, set[str]] = defaultdict(set)
    for native_word in _native_tokens(query):
        if not _has_vietnamese_diacritic(native_word):
            continue
        folded = _tokens(native_word)
        if folded:
            accented_query_words[folded[0]].add(native_word)

    def has_accent_collision(token: str) -> bool:
        variants = accented_query_words.get(token)
        if not variants or candidate.native_words.intersection(variants):
            return False
        return any(
            folded and folded[0] == token
            for native_word in candidate.native_words
            for folded in (_tokens(native_word),)
        )

    term_scores = [
        0.0
        if has_accent_collision(token)
        else max(
            (
                weight
                for words, weight in zip(field_words, field_weights, strict=True)
                if _word_matches(token, words)
            ),
            default=0.0,
        )
        for token in terms
    ]
    core_terms = [
        (index, token)
        for index, token in enumerate(terms)
        if len(token) >= 3
        and not token[0].isdigit()
        and token not in _PACKAGING_WORDS
        and token not in _SIZE_PREFERENCE_WORDS
    ]
    exact_core_matches = sum(1 for index, _token in core_terms if term_scores[index] > 0)
    for index, token in enumerate(terms):
        if term_scores[index] or len(token) < 4 or token.isdigit():
            continue
        if exact_core_matches < 1 or token in known_words:
            continue
        fuzzy_score = max(
            (
                SequenceMatcher(None, token, word).ratio() * weight
                for words, weight in zip(field_words[:3], field_weights[:3], strict=True)
                for word in words
                if len(word) >= 4
            ),
            default=0.0,
        )
        if fuzzy_score >= 2.16:
            term_scores[index] = fuzzy_score

    numeric_terms = [token for token in terms if token[0].isdigit()]
    numeric_scores = [
        score for token, score in zip(terms, term_scores, strict=True) if token in numeric_terms
    ]
    if numeric_terms:
        if comparison and len(numeric_terms) > 1:
            if max(numeric_scores, default=0.0) == 0:
                return 0.0
        elif any(score == 0 for score in numeric_scores):
            return 0.0

    explicit_packaging = [token for token in terms if token in _PACKAGING_WORDS]
    if explicit_packaging and not comparison and any(
        term_scores[terms.index(token)] == 0 for token in explicit_packaging
    ):
        return 0.0
    if any(term_scores[index] == 0 for index, _token in core_terms):
        return 0.0

    matched = sum(score > 0 for score in term_scores)
    if matched == 0 or matched / len(terms) < 0.5:
        return 0.0
    score = sum(term_scores) - 1.5 * (len(terms) - matched)
    if sum(_word_matches(token, candidate.title_words) for token in terms) >= 2:
        score += 2.0
    if snack_slang and "snack" in candidate.category_words:
        score += 5.0
        if "bim" in candidate.title_words:
            score += 2.0

    if any(word in _SIZE_PREFERENCE_WORDS for word in terms):
        preferred_measure = _measure(candidate.title)
        if preferred_measure is not None:
            amount, _dimension = preferred_measure
            score += max(-3.0, min(3.0, 3.0 * (1000.0 - amount) / 1000.0))
            if (candidate.title_words | candidate.uom_words).intersection(
                {"thung", "loc", "combo"}
            ):
                score -= 3.0
    elif not any(word in {"thung", "loc", "combo"} for word in terms):
        if candidate.uom_words.intersection({"thung", "loc", "combo"}):
            score -= 5.0
    return max(score, 0.0)


def _measure(title: str) -> tuple[float, str] | None:
    match = _MEASURE_PATTERN.search(title)
    if match is None:
        return None
    amount = float(match.group(1).replace(",", "."))
    unit = match.group(2)
    if unit in {"l", "lit", "liter", "litre"}:
        return amount * 1000, "volume"
    if unit in {"ml", "milliliter", "millilitre"}:
        return amount, "volume"
    if unit == "kg":
        return amount * 1000, "mass"
    return amount, "mass"


def _category_matches(
    category: str | None,
    candidate: _Candidate,
    known_category_keys: frozenset[str] = frozenset(),
) -> bool:
    if not category:
        return True
    normalized = _category_key(category)
    if normalized == "baby":
        if candidate.baby_audience:
            return True
        # This parent identifies an intentional baby and maternity department.
        # Match it as one taxonomy segment, rather than by concatenating every
        # segment in the path (which previously leaked unrelated categories).
        baby_department = frozenset({"do", "dung", "cham", "soc", "me", "va", "be"})
        return any(
            segment == baby_department
            for segment in candidate.category_segment_words[:-1]
        )

    alias_key = _category_alias_key(normalized)
    taxonomy_key = normalize_category_text(category)
    if (
        taxonomy_key in known_category_keys
        and alias_key not in _CATEGORY_PHRASE_ALIASES
    ):
        # Catalog labels are authoritative. Match their actual hierarchy
        # segment so homonyms and partial token overlaps cannot widen a search.
        return taxonomy_key in candidate.category_segment_texts

    category_segments = candidate.category_segment_texts
    if normalized == "oral_care" and any(
        segment == frozenset({"do", "dung", "cham", "soc", "me", "va", "be"})
        for segment in candidate.category_segment_words[:-1]
    ):
        category_segments += (" ".join(_tokens(candidate.product.name)),)

    return any(
        f" {' '.join(phrase)} " in f" {segment} "
        for phrase in _category_phrase_tokens(normalized)
        for segment in category_segments
    )


class SnapshotProductRepository:
    """Read-only product catalog sourced from a validated local JSON snapshot.

    Product prices, recorded stock, and recommended shelf locations remain
    snapshots. This repository never connects to a live transaction system.
    """

    source_name = "winmart_json_snapshot"
    supports_current_stock = False
    supports_spicy_filter = False

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        rows, quarantined, report = analyze_dataset(self.path)
        if report.get("valid") is not True:
            raise ValueError("The local product snapshot failed schema validation")
        quarantined_ids = {item["product_id"] for item in quarantined}
        dataset_version = str(report["dataset_version"])
        products: list[ProductSnapshot] = []
        for row in rows:
            if row.product_id in quarantined_ids:
                continue
            timestamp = datetime.fromisoformat(row.scraped_at.replace("Z", "+00:00"))
            shelf = recommended_shelf(row.product_id, dataset_version)
            effective_price = min(row.price_vnd, row.sale_price_vnd)
            promotion = row.discount_promotion if row.sale_price_vnd < row.price_vnd else None
            products.append(
                ProductSnapshot(
                    product_id=row.product_id,
                    name=row.name,
                    category=row.category_name or row.sub_department or row.department or "",
                    price_vnd=effective_price,
                    stock=row.stock,
                    stock_is_current=False,
                    shelf=shelf,
                    shelf_source="RECOMMENDED_PLANOGRAM" if shelf else "UNKNOWN",
                    promotion=promotion,
                    spicy=None,
                    ingredients=None,
                    expiry_at=None,
                    data_timestamp=timestamp,
                    brand=row.brand,
                    item_no=row.item_no,
                    barcode=row.barcode,
                    category_code=row.category_code,
                    department=row.department,
                    sub_department=row.sub_department,
                    category_hierarchy=row.category_hierarchy,
                    uom=row.uom,
                    image_url=row.image_url,
                    description=row.description,
                    seo_url=row.seo_url,
                    list_price_vnd=row.price_vnd,
                    sale_price_vnd=row.sale_price_vnd,
                    price_quality_status=(
                        "SALE" if row.sale_price_vnd < row.price_vnd else "NORMAL"
                    ),
                )
            )
        self.products = tuple(products)
        self._speech_brand_names = catalog_brand_names(self.products, limit=1000)
        self._category_hierarchies = collect_category_hierarchies(self.products)
        self._known_category_keys = frozenset(
            normalize_category_text(segment)
            for path in self._category_hierarchies
            for segment in path
            if normalize_category_text(segment)
        )
        self.dataset_sha256 = dataset_version
        self.rows_quarantined = len(quarantined)
        self._by_id = {product.product_id.casefold(): product for product in products}
        self._candidates = tuple(self._candidate(product) for product in products)
        self._by_candidate_id = {
            candidate.product.product_id: candidate for candidate in self._candidates
        }
        self._known_words = frozenset(
            word for candidate in self._candidates for word in candidate.words
        )
        self._token_index: dict[str, set[str]] = defaultdict(set)
        self._title_index: dict[str, set[str]] = defaultdict(set)
        self._identifier_index: dict[str, str] = {}
        for candidate in self._candidates:
            for word in candidate.words:
                self._token_index[word].add(candidate.product.product_id)
            self._title_index[candidate.title].add(candidate.product.product_id)
            for identifier in candidate.identifiers:
                self._identifier_index[identifier] = candidate.product.product_id

    @staticmethod
    def _candidate(product: ProductSnapshot) -> _Candidate:
        title = normalize_search_text(product.name)
        brand = normalize_search_text(product.brand)
        raw_hierarchy = product.category_hierarchy or ""
        category = normalize_search_text(
            " ".join(
                filter(
                    None,
                    (
                        product.category,
                        product.category_hierarchy,
                        product.department,
                        product.sub_department,
                    ),
                )
            )
        )
        uom = normalize_search_text(product.uom)
        category_segment_words = tuple(
            frozenset(_tokens(part))
            for part in raw_hierarchy.split(">")
            if part.strip()
        )
        category_segment_texts = tuple(
            " ".join(_tokens(part))
            for part in raw_hierarchy.split(">")
            if part.strip()
        )
        if not category_segment_words:
            category_segment_words = (
                frozenset(_tokens(f"{category} {product.category}")),
            )
            category_segment_texts = (
                " ".join(_tokens(f"{category} {product.category}")),
            )
        leaf = raw_hierarchy.split(">")[-1] if raw_hierarchy else product.category
        baby_audience = bool(
            re.search(
                r"(?<!\w)(?:bé|trẻ em|bỉm|tã|t\.em)(?!\w)",
                f"{leaf} {product.name}",
                re.IGNORECASE,
            )
        )
        words = tuple(
            dict.fromkeys(
                (
                    *_tokens(title),
                    *_tokens(brand),
                    *_tokens(category),
                    *_tokens(uom),
                )
            )
        )
        identifiers = frozenset(
            value.casefold()
            for value in (product.product_id, product.item_no, product.barcode)
            if value
        )
        return _Candidate(
            product=product,
            title=title,
            brand=brand,
            category=category,
            uom=uom,
            identifiers=identifiers,
            words=words,
            title_words=frozenset(_tokens(title)),
            brand_words=frozenset(_tokens(brand)),
            category_words=frozenset(_tokens(category)),
            category_segment_words=category_segment_words,
            category_segment_texts=category_segment_texts,
            uom_words=frozenset(_tokens(uom)),
            native_words=frozenset(
                _native_tokens(
                    " ".join(
                        filter(
                            None,
                            (
                                product.name,
                                product.brand,
                                product.category,
                                product.category_hierarchy,
                                product.department,
                                product.sub_department,
                                product.uom,
                            ),
                        )
                    )
                )
            ),
            baby_audience=baby_audience,
        )

    def search(
        self,
        query: str = "",
        category: str | None = None,
        max_price_vnd: int | None = None,
        spicy: bool | None = None,
        in_stock: bool = False,
        limit: int = 10,
        brand: str | None = None,
        price_operator: PriceOperator | None = None,
        price_value_vnd: int | None = None,
        diversify_by_subcategory: bool = False,
    ) -> list[ProductSnapshot]:
        terms, comparison, _snack_slang = _query_terms(query)
        normalized = normalize_search_text(query).strip().casefold()
        exact_id = self._identifier_index.get(normalized)
        exact_title_ids = self._title_index.get(normalized)
        if exact_id:
            candidate_ids = {exact_id}
        elif exact_title_ids:
            candidate_ids = set(exact_title_ids)
        else:
            candidate_ids = set().union(
                *(self._token_index.get(token, set()) for token in terms)
            ) if terms else {item.product.product_id for item in self._candidates}

        normalized_brand = _tokens(brand)
        results: list[tuple[float, ProductSnapshot]] = []
        for product_id in candidate_ids:
            candidate = self._by_candidate_id[product_id]
            product = candidate.product
            if not _category_matches(
                category, candidate, self._known_category_keys
            ):
                continue
            if normalized_brand and not all(
                token in candidate.brand_words for token in normalized_brand
            ):
                continue
            if max_price_vnd is not None and product.price_vnd > max_price_vnd:
                continue
            if not matches_price_constraint(product.price_vnd, price_operator, price_value_vnd):
                continue
            if spicy is not None and product.spicy is not spicy:
                continue
            if in_stock and product.sellable_stock <= 0:
                continue
            score = _score(query, candidate, self._known_words)
            if terms and score <= 0:
                continue
            results.append((score, product))

        if not terms:
            results.sort(key=lambda pair: (pair[1].price_vnd, pair[1].name.casefold()))
        else:
            results.sort(key=lambda pair: (-pair[0], pair[1].name.casefold()))
        products = [product for _score_value, product in results]
        bounded_limit = max(1, min(limit, 20))
        if diversify_by_subcategory:
            return diversify_by_category_leaf(products, bounded_limit)
        return products[:bounded_limit]

    def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
        return self._category_hierarchies

    def list_speech_terms(self, limit: int = 32) -> tuple[str, ...]:
        return catalog_speech_terms(self.products, limit=limit)

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
        return self._speech_brand_names[: max(1, min(int(limit), len(self._speech_brand_names)))]

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return self._by_id.get(product_id.strip().casefold())

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]:
        products = [
            product
            for product in self.products
            if product.promotion
            and _category_matches(
                category,
                self._by_candidate_id[product.product_id],
                self._known_category_keys,
            )
        ]
        products.sort(key=lambda product: (product.price_vnd, product.name.casefold()))
        return products[: max(1, min(limit, 10))]

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions:
        return CatalogAdditions(comparable=False)
