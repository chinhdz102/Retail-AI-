from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_CATEGORY_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "ban",
        "cai",
        "can",
        "cho",
        "co",
        "cua",
        "giup",
        "hang",
        "khong",
        "la",
        "loai",
        "minh",
        "mon",
        "mot",
        "muon",
        "mua",
        "nao",
        "nhe",
        "nhom",
        "san",
        "tim",
        "toi",
        "tu",
        "van",
        "voi",
        "duoc",
        "y",
        "i",
        "um",
        "uhm",
        "neu",
        "thi",
        "goi",
        "so",
        "vai",
        "phim",
        "nhanh",
        "bet",
        "need",
        "want",
        "looking",
        "for",
        "product",
        "products",
        "category",
        "type",
        "some",
        "any",
    }
)
_SINGLE_WORD_CATEGORY_CONTEXT_WORDS = frozenset(
    {
        "a",
        "ban",
        "can",
        "co",
        "cho",
        "cua",
        "duoc",
        "dung",
        "gi",
        "giup",
        "hang",
        "khong",
        "la",
        "loai",
        "minh",
        "mon",
        "mot",
        "mua",
        "muon",
        "nao",
        "nhe",
        "nhi",
        "nha",
        "need",
        "neu",
        "some",
        "so",
        "san",
        "pham",
        "phu",
        "hop",
        "toi",
        "tim",
        "type",
        "van",
        "voi",
        "want",
        "looking",
        "for",
        "any",
    }
)
_CATEGORY_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    # Resolve everyday napkin wording through the active catalog rather than
    # selecting a fabric-towel category on a literal "khăn lau" overlap.
    "khan lau mieng": ("khan giay",),
    "khan an": ("khan giay",),
    "napkin": ("paper napkin", "khan giay"),
    "paper napkin": ("khan giay",),
    "tissue": ("khan giay",),
}


@dataclass(frozen=True)
class CatalogCategoryMatch:
    category: str
    matched_tokens: frozenset[str]
    exact_phrase: bool


def resolve_catalog_category_alias(
    query: str,
    hierarchies: Iterable[tuple[str, ...]],
) -> CatalogCategoryMatch | None:
    """Resolve a known product-type synonym to a category in this catalog."""
    aliases = _CATEGORY_QUERY_SYNONYMS.get(normalize_category_text(query), ())
    paths = tuple(hierarchies)
    for alias in aliases:
        match = resolve_catalog_category(alias, paths)
        if match is not None:
            return match
    return None


def normalize_category_text(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", unaccented))


def _native_category_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", value.casefold())
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def _has_category_diacritic(value: str) -> bool:
    return "đ" in value or any(
        unicodedata.category(character) == "Mn"
        for character in unicodedata.normalize("NFD", value)
    )


def split_category_hierarchy(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(">") if part.strip())


def collect_category_hierarchies(products: Iterable[object]) -> tuple[tuple[str, ...], ...]:
    paths: set[tuple[str, ...]] = set()
    for product in products:
        hierarchy = getattr(product, "category_hierarchy", None)
        path = split_category_hierarchy(hierarchy)
        if not path:
            category = str(getattr(product, "category", "") or "").strip()
            path = (category,) if category else ()
        if path:
            paths.add(path)
    return tuple(sorted(paths, key=lambda path: tuple(part.casefold() for part in path)))


def _common_path(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not paths:
        return ()
    shared: list[str] = []
    for parts in zip(*paths, strict=False):
        normalized = {normalize_category_text(part) for part in parts}
        if len(normalized) != 1:
            break
        shared.append(parts[0])
    return tuple(shared)


def resolve_catalog_category(
    query: str,
    hierarchies: Iterable[tuple[str, ...]],
) -> CatalogCategoryMatch | None:
    """Match a generic product-type phrase to the active catalog taxonomy.

    Exact taxonomy phrases win. Shorter spoken forms such as “rửa mặt” can match
    a unique catalog leaf like “Sữa rửa mặt nữ”; ambiguous variants resolve to
    their nearest shared parent and keep the phrase available to product search.
    """
    normalized_query = normalize_category_text(query)
    query_tokens = frozenset(
        token for token in normalized_query.split() if token not in _CATEGORY_QUERY_STOP_WORDS
    )
    query_words = [
        token for token in normalized_query.split() if token not in _CATEGORY_QUERY_STOP_WORDS
    ]
    category_query_words = [
        word
        for word in re.sub(r"\bgoi\s+y\b", " ", normalized_query).split()
        if word not in _SINGLE_WORD_CATEGORY_CONTEXT_WORDS
    ]
    native_category_query_words = [
        word
        for word in _native_category_words(query)
        if normalize_category_text(word) not in _SINGLE_WORD_CATEGORY_CONTEXT_WORDS
    ]
    if not query_tokens:
        return None

    labels: dict[str, tuple[str, list[tuple[str, ...]]]] = {}
    paths = tuple(hierarchies)
    for path in paths:
        for label in path:
            key = normalize_category_text(label)
            if not key:
                continue
            entry = labels.get(key)
            if entry is None:
                labels[key] = (label, [path])
            elif path not in entry[1]:
                entry[1].append(path)

    exact: list[tuple[int, int, int, str, frozenset[str]]] = []
    for key, (label, label_paths) in labels.items():
        label_words = key.split()
        label_tokens = frozenset(label_words)
        if not label_words:
            continue
        # Prefer a full taxonomy phrase stated in the request. Also accept a
        # two-or-more-word prefix such as “mỹ phẩm” for “Mỹ phẩm, chăm sóc cá
        # nhân”. A one-word segment is accepted only when the user says that
        # exact catalog label; it is never inferred from a one-word prefix.
        phrase_in_query = bool(
            re.search(rf"(?<!\w){re.escape(key)}(?!\w)", normalized_query)
        )
        if len(label_words) == 1:
            # Accent folding makes Vietnamese homonyms collide inside longer
            # phrases (for example, "dầu gội" contains the folded token
            # "dau", which is also the folded spelling of "đậu"). A single
            # word taxonomy label is exact only when it is the full request
            # after conversational stop words are removed.
            exact_single_word = category_query_words == label_words
            if exact_single_word and len(native_category_query_words) == 1:
                native_label_words = _native_category_words(label)
                native_query_word = native_category_query_words[0]
                if (
                    _has_category_diacritic(native_query_word)
                    and native_label_words
                    and native_query_word != native_label_words[0]
                ):
                    exact_single_word = False
            phrase_in_query = phrase_in_query and exact_single_word
        query_is_label_prefix = (
            len(category_query_words) >= 2
            and len(category_query_words) <= len(label_words)
            and label_words[: len(category_query_words)] == category_query_words
        )
        if not phrase_in_query and not query_is_label_prefix:
            continue
        matched = label_tokens if phrase_in_query else query_tokens
        exact.append(
            (
                len(matched),
                int(phrase_in_query),
                max(len(path) for path in label_paths),
                label,
                matched,
            )
        )
    if exact:
        best_score = max((item[0], item[2]) for item in exact)
        best = [item for item in exact if (item[0], item[2]) == best_score]
        matched = frozenset().union(*(item[4] for item in best))
        if len(best) == 1:
            _length, phrase, _depth, label, _matched = best[0]
            return CatalogCategoryMatch(label, matched, exact_phrase=bool(phrase))
        if query_tokens <= matched and len(query_tokens) >= 2:
            return CatalogCategoryMatch(
                " ".join(query_words),
                matched,
                exact_phrase=all(bool(item[1]) for item in best),
            )
        matched_paths = [
            path
            for item in best
            for path in labels[normalize_category_text(item[3])][1]
        ]
        common = _common_path(matched_paths)
        if common:
            return CatalogCategoryMatch(
                common[-1],
                matched,
                exact_phrase=all(bool(item[1]) for item in best),
            )

    partial: list[tuple[int, int, int, str, frozenset[str], list[tuple[str, ...]]]] = []
    for key, (label, label_paths) in labels.items():
        label_words = key.split()
        label_tokens = frozenset(label_words)
        overlap = query_tokens & label_tokens
        if len(overlap) < 2:
            continue
        ordered_overlap = [word for word in query_words if word in label_tokens]
        label_positions = [label_words.index(word) for word in ordered_overlap]
        if label_positions != sorted(label_positions):
            continue
        query_coverage = len(overlap) / len(query_tokens)
        label_coverage = len(overlap) / len(label_tokens)
        if query_coverage < 0.67 or label_coverage < 0.5:
            continue
        partial.append(
            (
                len(overlap),
                round(query_coverage * 100),
                round(label_coverage * 100),
                label,
                overlap,
                label_paths,
            )
        )
    if not partial:
        return None

    best_score = max(item[:3] for item in partial)
    best = [item for item in partial if item[:3] == best_score]
    matched_tokens = frozenset().union(*(item[4] for item in best))
    if len(best) == 1:
        return CatalogCategoryMatch(best[0][3], matched_tokens, exact_phrase=False)

    if query_tokens <= matched_tokens and len(query_tokens) >= 2:
        # The same precise product type can live under multiple branches (for
        # example fresh and frozen beef). Keep the user's full type as a
        # segment query instead of widening it to a broad ancestor such as all
        # food. Repositories match these tokens against one category segment.
        return CatalogCategoryMatch(" ".join(query_words), matched_tokens, exact_phrase=False)

    matched_paths = [path for item in best for path in item[5]]
    common = _common_path(matched_paths)
    if common:
        return CatalogCategoryMatch(common[-1], matched_tokens, exact_phrase=False)
    return None


def category_leaf(product: object) -> str:
    path = split_category_hierarchy(getattr(product, "category_hierarchy", None))
    if path:
        return path[-1]
    return str(getattr(product, "category", "") or "Khác")


def category_is_parent(
    category: str,
    hierarchies: Iterable[tuple[str, ...]],
) -> bool:
    normalized = normalize_category_text(category)
    return any(
        len(path) > index + 1
        for path in hierarchies
        for index, label in enumerate(path)
        if normalize_category_text(label) == normalized
    )


def query_names_catalog_leaf(query: str, hierarchies: Iterable[tuple[str, ...]]) -> bool:
    """Return whether a phrase in a query names or prefixes a catalog leaf.

    This lets discovery keep product-type words such as “dầu gội” while
    dropping broad needs such as “da khô” from the actual product-title filter.
    The match is derived from the active catalog taxonomy, not a hand-maintained
    list of product categories.
    """
    words = normalize_category_text(query).split()
    if len(words) < 2:
        return False

    paths = tuple(hierarchies)
    parent_keys = {
        normalize_category_text(label)
        for path in paths
        for label in path[:-1]
    }
    leaves = {
        normalize_category_text(path[-1])
        for path in paths
        if path
        and len(path) >= 4
        and normalize_category_text(path[-1]) not in parent_keys
    }
    for leaf in leaves:
        leaf_words = leaf.split()
        max_prefix = min(len(words), len(leaf_words))
        for size in range(max_prefix, 1, -1):
            prefix = leaf_words[:size]
            if any(
                words[start : start + size] == prefix
                for start in range(len(words) - size + 1)
            ):
                return True
    return False


def diversify_by_category_leaf(products: Iterable[object], limit: int) -> list:
    """Interleave results so a broad browse shows distinct catalog subcategories."""
    rows = list(products)
    by_leaf: dict[str, list] = {}
    for item in rows:
        leaf = normalize_category_text(category_leaf(item)) or "khac"
        by_leaf.setdefault(leaf, []).append(item)

    # Input rows are already ranked by the repository. Return at most one
    # representative per leaf so a broad browse doesn't repeat one branch.
    selected: list = []
    for leaf_items in by_leaf.values():
        selected.append(leaf_items[0])
        if len(selected) >= max(1, limit):
            return selected
    return selected
