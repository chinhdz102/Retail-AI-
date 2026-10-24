"""Conservatively correct Whisper product words using the active product catalog."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Protocol

from backend.products.catalog import QUERY_STOP_WORDS, ProductSnapshot


class SearchableCatalog(Protocol):
    def search(self, query: str = "", limit: int = 10) -> list[ProductSnapshot]: ...


@dataclass(frozen=True)
class CatalogWordCorrection:
    source: str
    replacement: str


_WORD_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_MISHEARD_BOTTLE_SIZE = re.compile(
    r"\btrai\b(?=\s+\d+(?:[.,]\d+)?\s*(?:ml|l[ií]t|l|kg|g)\b)",
    flags=re.IGNORECASE,
)
_NEVER_CORRECT = QUERY_STOP_WORDS | frozenset(
    {
        "minh",
        "mua",
        "voi",
        "nhung",
        "dang",
        "nay",
        "kia",
        "day",
        "di",
        "nhe",
        "a",
        "ha",
        "ve",
        "thi",
        "neu",
        "roi",
        "nua",
        "gi",
        "cai",
        "loai",
        "danh",
        "khu",
        "vi",
        "cay",
        "sua",
        "com",
        "dien",
        "and",
        "nhan",
        "mot",
        "hai",
        "ba",
        "bon",
        "nam",
        "sau",
        "bay",
        "tam",
        "chin",
        "muoi",
    }
)
_RETAIL_BRAND_CUE_PHRASES = (
    ("mua",),
    ("gia", "ban"),
    ("gia", "bao", "nhieu"),
    ("bao", "nhieu", "tien"),
    ("con", "hang"),
    ("het", "hang"),
    ("mat", "hang"),
    ("cua", "hang"),
    ("hang", "hoa"),
    ("hang", "ton"),
    ("ton", "kho"),
    ("khuyen", "mai"),
    ("san", "pham"),
    ("mua", "hang"),
    ("tim", "mua"),
)


def _normalize_word(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D").casefold()
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


_VIETNAMESE_LETTER_NAMES: dict[str, tuple[str, ...]] = {
    "b": ("be",),
    "c": ("xe",),
    "d": ("de",),
    "f": ("ep",),
    "g": ("gie",),
    "h": ("hat",),
    "k": ("ka",),
    "l": ("el",),
    "m": ("em",),
    "n": ("en",),
    "p": ("pe",),
    "q": ("quy",),
    "r": ("er", "ero"),
    "s": ("et",),
    "t": ("te",),
    "v": ("ve",),
    "w": ("dupliu",),
    "x": ("ich",),
    "y": ("idai",),
}


def _words(value: str | None) -> tuple[str, ...]:
    return tuple(_WORD_PATTERN.findall(value or ""))


def _retail_cue_positions(
    matches: list[re.Match[str]], normalized_words: list[str]
) -> list[int]:
    """Find commerce cues without confusing Vietnamese homophones such as kế/kệ."""
    cue_positions: set[int] = set()
    for start in range(len(normalized_words)):
        for phrase in _RETAIL_BRAND_CUE_PHRASES:
            end = start + len(phrase)
            if tuple(normalized_words[start:end]) == phrase:
                cue_positions.add(start)

        # Keep the accented standalone price/location words. The de-accented
        # forms are ambiguous in ordinary Vietnamese (e.g. gia = family, ke = kế).
        surface = matches[start].group().casefold()
        if surface == "giá":
            cue_positions.add(start)
        if surface == "kệ":
            previous = normalized_words[start - 1] if start > 0 else ""
            following = normalized_words[start + 1] if start + 1 < len(normalized_words) else ""
            if previous in {"o", "nam", "tren", "duoi", "gan", "quay", "dau"} or (
                following in {"so", "nao", "gi", "nay", "kia"}
                or following.isdigit()
                or (len(following) > 1 and following[0].isalpha() and following[1:].isdigit())
            ):
                cue_positions.add(start)
    return sorted(cue_positions)


def _product_text(product: ProductSnapshot) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            product.name,
            product.brand,
            product.category,
            product.category_hierarchy,
            product.department,
            product.sub_department,
            product.uom,
        )
        if value
    )


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _max_correction_distance(source: str) -> int:
    if len(source) <= 2:
        return 0
    if len(source) <= 5:
        return 2
    return 2


def _restore_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _correct_retail_packaging_classifier(
    text: str,
) -> tuple[str, list[CatalogWordCorrection]]:
    """Fix the common ASR confusion "trai 600ml" -> "chai 600ml".

    The correction only applies when the following token is a measured package
    size, where "trai" is not a valid Vietnamese product classifier.
    """
    corrections: list[CatalogWordCorrection] = []

    def replace(match: re.Match[str]) -> str:
        source = match.group()
        replacement = _restore_case(source, "chai")
        corrections.append(CatalogWordCorrection(source=source, replacement=replacement))
        return replacement

    return _MISHEARD_BOTTLE_SIZE.sub(replace, text), corrections


def _catalog_brand_surface(brand: str) -> str:
    return brand.title() if brand.isupper() else brand


def _short_acronym_pronunciation_score(
    spoken_tokens: tuple[str, ...], acronym_tokens: tuple[str, ...]
) -> float | None:
    """Compare a two-token transcript with a two-letter brand's Vietnamese name."""
    if len(spoken_tokens) != 2 or len(acronym_tokens) != 2:
        return None
    if any(len(token) != 1 for token in acronym_tokens):
        return None

    spoken = tuple(_normalize_word(token) for token in spoken_tokens)
    acronym = tuple(_normalize_word(token) for token in acronym_tokens)
    # A spoken first letter is a strong anchor. The second letter may be rendered
    # as its Vietnamese letter name (e.g. "ét" -> S, "ép" -> F).
    if spoken[0] != acronym[0]:
        return None
    second = spoken[1]
    if second == acronym[1]:
        return 0.0

    spoken_aliases = _VIETNAMESE_LETTER_NAMES.get(second, ()) if len(second) == 1 else ()
    spoken_forms = spoken_aliases or (second,)
    target_forms = _VIETNAMESE_LETTER_NAMES.get(acronym[1], ())
    if not target_forms:
        return None
    ratios = [
        _edit_distance(spoken_form, target_form) / max(len(spoken_form), len(target_form))
        for spoken_form in spoken_forms
        for target_form in target_forms
    ]
    score = min(ratios, default=1.0)
    return score if score <= 0.5 else None


def _correct_fuzzy_brand(
    text: str,
    catalog: SearchableCatalog,
) -> tuple[str, list[CatalogWordCorrection]]:
    list_brand_names = getattr(catalog, "list_brand_names", None)
    if not callable(list_brand_names):
        return text, []
    try:
        raw_brands = list_brand_names(300)
    except Exception:
        return text, []
    matches = list(_WORD_PATTERN.finditer(text))
    normalized_words = [_normalize_word(match.group()) for match in matches]
    cue_positions = _retail_cue_positions(matches, normalized_words)
    category_phrases: list[tuple[tuple[str, ...], str]] = []
    category_words: set[str] = set()
    list_category_hierarchies = getattr(catalog, "list_category_hierarchies", None)
    if callable(list_category_hierarchies):
        try:
            for path in list_category_hierarchies():
                for category in path:
                    tokens = tuple(_normalize_word(word) for word in _words(category))
                    tokens = tuple(token for token in tokens if token)
                    if tokens:
                        category_phrases.append((tokens, category))
                        category_words.update(tokens)
        except Exception:
            category_phrases = []
            category_words = set()

    # Index short category n-grams once. The old path compared every nearby text
    # window against every taxonomy entry for each possible brand correction;
    # long transcripts multiplied that nested scan into tens of seconds.
    category_phrase_index: dict[tuple[str, ...], set[str]] = defaultdict(set)
    category_word_buckets: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for category_tokens, category in category_phrases:
        for width in range(2, min(4, len(category_tokens)) + 1):
            for start in range(len(category_tokens) - width + 1):
                category_phrase_index[category_tokens[start : start + width]].add(category)
        for word in category_tokens:
            if len(word) >= 5:
                category_word_buckets[len(word)].add((word, category))

    # An exact catalog category is strong evidence of a product query, even when
    # the customer does not say a price/stock cue (for example, "Có kem đánh
    # răng P, F không?").
    for start in range(len(normalized_words)):
        for width in range(2, min(4, len(normalized_words) - start) + 1):
            if tuple(normalized_words[start : start + width]) in category_phrase_index:
                cue_positions.append(start)
                break
    if not cue_positions:
        return text, []

    category_brand_cache: dict[str, set[str]] = {}

    def brands_near_span(start_index: int, end_index: int) -> tuple[set[str], bool]:
        exact_categories: set[str] = set()
        # Catalog categories often have longer labels than people say (for example,
        # "Tã quần" is stored as "Tã quần trẻ em"). Accept a spoken two-word prefix.
        for context_start in range(len(normalized_words)):
            for context_length in range(2, min(4, len(normalized_words) - context_start) + 1):
                context_end = context_start + context_length
                if context_start < end_index and start_index < context_end:
                    continue
                gap = (
                    start_index - context_end
                    if context_end <= start_index
                    else context_start - end_index
                )
                if gap > 5:
                    continue
                between_start = min(matches[context_start].start(), matches[start_index].start())
                between_end = max(
                    matches[context_end - 1].end(), matches[end_index - 1].end()
                )
                if re.search(r"[.!?;]", text[between_start:between_end]):
                    continue
                exact_categories.update(
                    category_phrase_index.get(
                        tuple(normalized_words[context_start:context_end]), ()
                    )
                )

        matched_categories = set(exact_categories)
        if not exact_categories:
            # A clear, nearby ASR miss such as "nắc" for "snack" can narrow the
            # brand candidates. Short generic words never act as category evidence.
            for token_index, word in enumerate(normalized_words):
                if start_index <= token_index < end_index:
                    continue
                if word in _NEVER_CORRECT or len(word) < 3:
                    continue
                if min(abs(token_index - start_index), abs(token_index - end_index)) > 5:
                    continue
                for category_length in range(max(5, len(word) - 2), len(word) + 3):
                    for category_word, category in category_word_buckets.get(category_length, ()):
                        distance = _edit_distance(word, category_word)
                        if distance <= 2 and distance / max(len(word), len(category_word)) <= 0.4:
                            matched_categories.add(category)

        if not matched_categories:
            return set(raw_brands), False

        brands_for_categories: set[str] = set()
        for category in sorted(matched_categories):
            if category not in category_brand_cache:
                try:
                    products = catalog.search(query="", category=category, limit=100)
                except Exception:
                    products = []
                category_brand_cache[category] = {
                    product.brand.strip()
                    for product in products
                    if product.brand and product.brand.strip()
                }
            brands_for_categories.update(category_brand_cache[category])
        return brands_for_categories, bool(exact_categories)

    if not raw_brands:
        return text, []
    known_brand_keys = {
        "".join(_normalize_word(word) for word in _words(brand))
        for brand in raw_brands
        if brand and brand.strip()
    }
    known_brand_sequences = tuple(
        tuple(_normalize_word(word) for word in _words(brand))
        for brand in raw_brands
        if brand and brand.strip()
    )
    known_brand_tokens = {token for brand_tokens in known_brand_sequences for token in brand_tokens}

    possibilities: list[tuple[float, int, int, int, str, str]] = []
    for start_index in range(len(matches)):
        for span_length in range(1, 4):
            end_index = start_index + span_length
            if end_index > len(matches):
                break
            if any(
                re.search(r"[.!?;]", text[matches[index].end() : matches[index + 1].start()])
                for index in range(start_index, end_index - 1)
            ):
                break
            source_tokens = normalized_words[start_index:end_index]
            possible_spoken_acronym = (
                len(source_tokens) == 2
                and len(source_tokens[0]) == 1
                and source_tokens[0].isalpha()
                and 1 <= len(source_tokens[1]) <= 4
            )
            exact_short_brand_transcription = (
                len(source_tokens) == 2
                and all(len(token) == 1 for token in source_tokens)
                and any(
                    tuple(source_tokens) == brand_tokens
                    and len(brand_tokens) == 2
                    and all(len(token) == 1 for token in brand_tokens)
                    for brand_tokens in known_brand_sequences
                )
            )
            if not possible_spoken_acronym and any(
                word in _NEVER_CORRECT for word in source_tokens
            ):
                continue
            if any(word in category_words and len(word) >= 4 for word in source_tokens):
                continue
            if len(source_tokens) == 1 and source_tokens[0] in known_brand_tokens:
                continue
            source = "".join(source_tokens)
            if (len(source) < 4 and not possible_spoken_acronym) or (
                source in known_brand_keys and not exact_short_brand_transcription
            ):
                continue
            if any(
                any(
                    tuple(source_tokens[offset : offset + len(brand_tokens)]) == brand_tokens
                    for offset in range(len(source_tokens) - len(brand_tokens) + 1)
                )
                for brand_tokens in known_brand_sequences
            ) and not exact_short_brand_transcription:
                continue
            if not any(
                start_index - 5 <= cue_index < start_index
                or end_index <= cue_index <= end_index + 4
                for cue_index in cue_positions
            ):
                continue

            candidate_brand_surfaces, has_exact_category = brands_near_span(start_index, end_index)
            brands = {
                "".join(_normalize_word(word) for word in _words(brand)): _catalog_brand_surface(
                    brand.strip()
                )
                for brand in candidate_brand_surfaces
                if brand and brand.strip()
            }
            brands.pop("", None)
            if not brands:
                continue
            distances = [
                (_edit_distance(source, brand), brand, surface)
                for brand, surface in brands.items()
                if abs(len(source) - len(brand)) <= 3
            ]
            if not distances:
                continue
            distances.sort(
                key=lambda item: (item[0] / max(len(source), len(item[1])), item[0], item[1])
            )
            best_distance, best_brand, best_surface = distances[0]
            ratio = best_distance / max(len(source), len(best_brand))
            standard_match = best_distance <= 3 and ratio <= 0.45
            exact_category_match = (
                has_exact_category and len(brands) <= 3 and best_distance <= 4 and ratio <= 0.6
            )
            short_brand_candidates = [
                (
                    "".join(_normalize_word(word) for word in _words(brand)),
                    _catalog_brand_surface(brand.strip()),
                )
                for brand in candidate_brand_surfaces
                if brand
                and brand.strip()
                and len(_words(brand)) == 2
                and all(len(_normalize_word(word)) == 1 for word in _words(brand))
            ]
            phonetic_acronym_matches: list[tuple[float, str, str]] = []
            if has_exact_category and len(source_tokens) == 2:
                for brand_key, brand_surface in short_brand_candidates:
                    brand_tokens = tuple(
                        _normalize_word(word) for word in _words(brand_surface)
                    )
                    score = _short_acronym_pronunciation_score(source_tokens, brand_tokens)
                    if score is not None:
                        phonetic_acronym_matches.append((score, brand_key, brand_surface))
            if phonetic_acronym_matches:
                phonetic_acronym_matches.sort(key=lambda item: (item[0], item[1]))
                best_score, best_brand_key, best_brand_surface = phonetic_acronym_matches[0]
                unique_best = len(phonetic_acronym_matches) == 1 or (
                    phonetic_acronym_matches[1][0] - best_score >= 0.2
                )
                if unique_best:
                    possibilities.append(
                        (
                            best_score,
                            round(best_score * 100),
                            start_index,
                            end_index,
                            best_brand_key,
                            best_brand_surface,
                        )
                    )
                    continue
            if possible_spoken_acronym and has_exact_category:
                # This span resembles a spelled-out short brand. If catalog evidence
                # cannot resolve it uniquely, do not run the broader fuzzy matcher.
                continue
            if best_distance == 0 or not (standard_match or exact_category_match):
                continue
            if len(distances) > 1:
                second_distance, second_brand, _ = distances[1]
                second_ratio = second_distance / max(len(source), len(second_brand))
                if second_distance == best_distance and abs(second_ratio - ratio) < 0.08:
                    continue
                if exact_category_match and second_ratio - ratio < 0.12:
                    continue
            possibilities.append(
                (ratio, best_distance, start_index, end_index, best_brand, best_surface)
            )

    if not possibilities:
        return text, []
    possibilities.sort(key=lambda item: (item[0], item[1], -(item[3] - item[2])))
    best = possibilities[0]
    if len(possibilities) > 1:
        second = possibilities[1]
        overlaps = best[2] < second[3] and second[2] < best[3]
        if not overlaps and abs(second[0] - best[0]) < 0.08:
            return text, []

    start = matches[best[2]].start()
    end = matches[best[3] - 1].end()
    replacement = best[5]
    corrected = f"{text[:start]}{replacement}{text[end:]}"
    return corrected, [
        CatalogWordCorrection(
            source=text[start:end],
            replacement=replacement,
        )
    ]


def correct_catalog_words(
    text: str,
    catalog: SearchableCatalog,
    *,
    language: str = "vi",
    max_anchor_searches: int = 5,
) -> tuple[str, list[CatalogWordCorrection]]:
    """Apply catalog-grounded corrections to likely Vietnamese product terms.

    A fuzzy brand can be corrected only near a retail cue, with an explicit
    matching category or a clearly unique catalog candidate. Product variants
    need an exact brand anchor. Ordinary conversation stays as Whisper returned it.
    """
    if language != "vi" or not text.strip():
        return text, []

    text, packaging_corrections = _correct_retail_packaging_classifier(text)
    text, fuzzy_brand_corrections = _correct_fuzzy_brand(text, catalog)
    existing_corrections = [*packaging_corrections, *fuzzy_brand_corrections]

    matches = list(_WORD_PATTERN.finditer(text))
    if not matches:
        return text, existing_corrections

    anchored_products: list[tuple[int, ProductSnapshot]] = []
    searched: set[str] = set()
    for token_index, match in enumerate(matches):
        surface = match.group()
        normalized = _normalize_word(surface)
        if len(normalized) < 4 or not surface[:1].isupper() or normalized in searched:
            continue
        searched.add(normalized)
        try:
            candidates = catalog.search(query=surface, limit=30)
        except Exception:
            # STT must still work if an optional catalog lookup is unavailable.
            continue
        for product in candidates:
            brand_words = {_normalize_word(word) for word in _words(product.brand)}
            if normalized in brand_words:
                anchored_products.append((token_index, product))
        if anchored_products and len(searched) >= max_anchor_searches:
            break

    if not anchored_products:
        return text, existing_corrections

    vocabulary_by_token: dict[int, dict[str, Counter[str]]] = {}
    category_vocabulary_by_token: dict[int, dict[str, Counter[str]]] = {}
    for anchor_index, product in anchored_products:
        vocabulary = vocabulary_by_token.setdefault(anchor_index, {})
        product_words: dict[str, set[str]] = {}
        for field in _product_text(product):
            for surface in _words(field):
                normalized = _normalize_word(surface)
                if normalized:
                    product_words.setdefault(normalized, set()).add(surface)
        for normalized, variants in product_words.items():
            for surface in variants:
                vocabulary.setdefault(normalized, Counter())[surface] += 1
        category_vocabulary = category_vocabulary_by_token.setdefault(anchor_index, {})
        for field in (
            product.category,
            product.category_hierarchy,
            product.department,
            product.sub_department,
        ):
            for surface in _words(field):
                normalized = _normalize_word(surface)
                if normalized:
                    category_vocabulary.setdefault(normalized, Counter())[surface] += 1
    corrections: list[CatalogWordCorrection] = []
    edits: list[tuple[int, int, str]] = []
    for token_index, match in enumerate(matches):
        surface = match.group()
        normalized = _normalize_word(surface)
        if len(normalized) < 2:
            continue

        nearby_vocabularies = [
            vocabulary
            for anchor_index, vocabulary in vocabulary_by_token.items()
            if 0 < abs(token_index - anchor_index) <= 2
        ]
        if not nearby_vocabularies:
            continue

        candidates: dict[str, Counter[str]] = {}
        for vocabulary in nearby_vocabularies:
            for normalized_candidate, variants in vocabulary.items():
                candidates.setdefault(normalized_candidate, Counter()).update(variants)
        if normalized in _NEVER_CORRECT:
            category_variants: Counter[str] = Counter()
            for anchor_index, vocabulary in category_vocabulary_by_token.items():
                if 0 < abs(token_index - anchor_index) <= 2:
                    category_variants.update(vocabulary.get(normalized, Counter()))
            if not category_variants:
                continue
            candidates[normalized] = category_variants
        if normalized in candidates:
            variants = candidates[normalized]
            canonical = max(variants, key=lambda value: (variants[value], value.isupper(), value))
            if surface.casefold() != canonical.casefold() and _normalize_word(
                surface
            ) == _normalize_word(canonical):
                corrected = _restore_case(surface, canonical)
                corrections.append(CatalogWordCorrection(source=surface, replacement=corrected))
                edits.append((match.start(), match.end(), corrected))
            continue
        max_distance = _max_correction_distance(normalized)
        distances = {
            candidate: _edit_distance(normalized, candidate)
            for candidate in candidates
            if len(candidate) >= 4 and abs(len(candidate) - len(normalized)) <= max_distance
        }
        if not distances:
            continue
        best_distance = min(distances.values())
        best = [candidate for candidate, distance in distances.items() if distance == best_distance]
        if best_distance > max_distance:
            continue

        best.sort(
            key=lambda candidate: (sum(candidates[candidate].values()), candidate), reverse=True
        )
        best_support = sum(candidates[best[0]].values())
        second_support = sum(candidates[best[1]].values()) if len(best) > 1 else 0
        if best_support < 3 or (second_support and best_support < second_support * 2):
            continue

        replacement_options = candidates[best[0]]
        replacement = max(
            replacement_options,
            key=lambda candidate: (replacement_options[candidate], candidate.isupper(), candidate),
        )
        corrected = _restore_case(surface, replacement)
        corrections.append(CatalogWordCorrection(source=surface, replacement=corrected))
        edits.append((match.start(), match.end(), corrected))

    if not edits:
        return text, existing_corrections
    corrected_text = text
    for start, end, replacement in reversed(edits):
        corrected_text = f"{corrected_text[:start]}{replacement}{corrected_text[end:]}"
    return corrected_text, [*existing_corrections, *corrections]
