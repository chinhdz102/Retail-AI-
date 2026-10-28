from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

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
        "khac",
        "other",
        "others",
    }
)
_CATEGORY_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    # Common shopper wording that differs lexically from this catalog's labels.
    # Each target is resolved against the active taxonomy at runtime.
    "mi goi": ("mi an lien",),
    "mi tom": ("mi an lien",),
    "dau goi": ("dau goi",),
    "do an": ("thuc pham",),
    "do uong": ("do uong thuoc la",),
    "xa phong tam": ("xa bong tam",),
    "sua hop": ("sua tuoi tiet trung",),
    "gao thuong": ("gao te",),
    "gao nau com thuong": ("gao te",),
    "lau nha": ("nuoc lau san nha",),
    "lau san": ("nuoc lau san nha",),
    "rua bat": ("nuoc rua chen bat",),
    "rua chen": ("nuoc rua chen bat",),
    "nuoc rua bat": ("nuoc rua chen bat",),
    "nuoc rua chen": ("nuoc rua chen bat",),
    "nuoc rua chen dia": ("nuoc rua chen bat",),
    "khan giay bo tui": ("khan giay goi",),
    "giay an bo tui": ("khan giay goi",),
    "khan an bo tui": ("khan giay goi",),
    "ta bim so sinh": ("ta dan tre em",),
    "ta dan": ("ta dan tre em",),
    # Keep the reduplicated snack wording separate from diapers. A bare
    # ``bỉm`` is ambiguous without a diaper form (dán/quần) or audience cue.
    "bim bim": ("snack",),
    "bim bim khoai tay": ("snack kh tay chien",),
    "bim bim lat khoai tay": ("snack kh tay chien",),
    "bim dan": ("ta dan tre em",),
    "bim quan nguoi lon": ("ta quan ta dan nlon",),
    "bim nguoi lon": ("ta quan ta dan nlon",),
    "bim quan": ("ta quan tre em",),
    "ta cho be so sinh": ("ta dan tre em",),
    "ta bim cho be so sinh": ("ta dan tre em",),
    "ta cho tre so sinh": ("ta dan tre em",),
    "bim cho tre so sinh": ("ta dan tre em",),
    "thuoc xit muoi": ("thuoc diet con trung",),
    "kem chong nang cho be": ("kem chong nangtre em",),
    "kem chong nang cho tre em": ("kem chong nangtre em",),
    "kem chong nang tre em": ("kem chong nangtre em",),
    "giay uot em be": ("giay uot tre em",),
    "giay uot cho be": ("giay uot tre em",),
    "hop dung com": ("do dung thuc an",),
    "hop com": ("do dung thuc an",),
    "binh giu nhiet": ("ly va do dung u nuoc",),
    "gao thom": ("gao nong san kho",),
    "do co rua bep": ("dung cu co rua bep",),
    "coc giay dung mot lan": ("do dung mot lan",),
    "dung mot lan": ("do dung mot lan",),
    "do choi be trai": ("do choi be trai",),
    "do choi hop cho be trai": ("do choi be trai",),
    "goi om": ("gối",),
    "nuoc ngot": ("nuoc ngot co ga",),
    "nuoc suoi": ("nuoc khoang",),
    "yaourt": ("sua chua an",),
    "cai ngot": ("rau cai",),
    "cai thia": ("rau cai",),
    "cai xanh": ("rau cai",),
    "ca dong": ("ca nuoc ngot tuoi",),
    "giay cuon": ("giay ve sinh",),
    "giay an mini": ("khan giay goi",),
    "chong nang": ("kem chong nang",),
    "thuoc danh rang": ("kem danh rang",),
    "ca do": ("ca chua",),
    # The source snapshot abbreviates ``khoai`` as ``kh.`` in this leaf.
    "snack khoai tay": ("snack kh tay chien",),
    "banh quy": ("banh biscuit",),
    "giay ve sinh": ("giay ve sinh",),
    "giay an": ("khan giay goi",),
    "vo hoc sinh": ("vo",),
    "vo cho hoc sinh": ("vo",),
    "giay nu": ("giay dep nu",),
    "dau xa": ("dau xa duong toc",),
    "newborn diapers": ("ta dan tre em",),
    # Resolve everyday napkin wording through the active catalog rather than
    # selecting a fabric-towel category on a literal "khăn lau" overlap.
    "khan lau mieng": ("khan giay", "giay an", "giay lua", "khan rut"),
    "khan an": ("khan giay", "giay an", "giay lua", "khan rut"),
    "napkin": ("khan giay", "giay an", "giay lua", "khan rut"),
    "paper napkin": ("khan giay", "giay an", "giay lua", "khan rut"),
    "tissue": ("khan giay",),
    # Everyday "baby wipes" wording belongs to the catalog's child-wipes
    # category, even when the planner labels it as generic baby-care goods.
    "khan giay cho be": ("khan giay", "giay an", "giay lua", "khan rut"),
    "giay an tre em": ("khan giay", "giay an", "giay lua", "khan rut"),
    "giay dung khi di cung be": ("khan giay", "giay an", "giay lua", "khan rut"),
    "khan uot cho be": ("khan uot tre em", "giay uot tre em"),
    "khan uot em be": ("khan uot tre em", "giay uot tre em"),
    "khan uot tre em": ("khan uot tre em", "giay uot tre em"),
    "baby wipes": ("khan uot tre em", "giay uot tre em"),
    "nuoc rua mat": ("sua rua mat",),
    "sua rua mat": ("sua rua mat",),
    # Keep exact product-type wording anchored to the corresponding catalog
    # leaf even when the model proposes a nearby personal-care category.
    "bong tay trang": ("bong tay trang",),
    "mieng bong tay trang": ("bong tay trang",),
    "cotton pad": ("bong tay trang",),
    "cotton pads": ("bong tay trang",),
}


def catalog_category_query_aliases(query: str) -> tuple[str, ...]:
    """Return catalog-backed product-type synonyms found in a spoken query."""
    normalized_query = normalize_category_text(query)
    aliases = _CATEGORY_QUERY_SYNONYMS.get(normalized_query)
    if aliases is not None:
        return aliases
    embedded_phrases = [
        phrase
        for phrase in _CATEGORY_QUERY_SYNONYMS
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized_query)
    ]
    if not embedded_phrases:
        return ()
    longest = max(len(phrase.split()) for phrase in embedded_phrases)
    return tuple(
        dict.fromkeys(
            alias
            for phrase in embedded_phrases
            if len(phrase.split()) == longest
            for alias in _CATEGORY_QUERY_SYNONYMS[phrase]
        )
    )


def catalog_category_alias_source_phrase(query: str) -> str | None:
    """Return the longest spoken synonym phrase present in a query."""
    normalized_query = normalize_category_text(query)
    if normalized_query in _CATEGORY_QUERY_SYNONYMS:
        return normalized_query
    embedded_phrases = [
        phrase
        for phrase in _CATEGORY_QUERY_SYNONYMS
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized_query)
    ]
    if not embedded_phrases:
        return None
    return max(embedded_phrases, key=lambda phrase: (len(phrase.split()), len(phrase)))


def catalog_query_with_aliases(query: str) -> str:
    """Rewrite the longest known shopper synonym to its taxonomy wording."""
    normalized_query = normalize_category_text(query)
    # Keep the reduplicated everyday word intact for lexical scoring: the
    # catalog contains a product title with this phrase, while the parser's
    # separate category route already understands it as a snack request.
    if normalized_query == "bim bim":
        return query
    matches = [
        (phrase, aliases)
        for phrase, aliases in _CATEGORY_QUERY_SYNONYMS.items()
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized_query)
    ]
    if not matches:
        return query
    phrase, aliases = max(matches, key=lambda item: (len(item[0].split()), len(item[0])))
    rewritten = re.sub(
        rf"(?<!\w){re.escape(phrase)}(?!\w)",
        aliases[0],
        normalized_query,
        count=1,
    )
    return " ".join(rewritten.split())


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
    paths = tuple(hierarchies)
    for alias in catalog_category_query_aliases(query):
        match = resolve_catalog_category(alias, paths)
        if match is not None:
            direct_match = resolve_catalog_category(query, paths)
            source_phrase = catalog_category_alias_source_phrase(query)
            source_tokens = set(normalize_category_text(source_phrase).split())
            if (
                direct_match is not None
                and direct_match.exact_phrase
                and source_tokens
                and source_tokens <= direct_match.matched_tokens
            ):
                # Prefer a longer phrase that is itself a catalog category
                # over an alias found only inside it (e.g. ``cá nước ngọt``
                # must not collapse to the embedded ``nước ngọt`` alias).
                return direct_match
            return match
    return None


@lru_cache(maxsize=16384)
def normalize_category_text(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", unaccented))


def category_alias_refines_explicit_audience(
    query: str,
    direct_category: str,
    alias_category: str,
) -> bool:
    """Prefer a taxonomy alias when it adds an audience the shopper stated."""
    audience_cues = (
        "tre em",
        "em be",
        "so sinh",
        "baby",
        "child",
        "children",
        "kids",
        "nam",
        "nu",
        "dan ong",
        "phu nu",
        "nguoi lon",
        "adult",
        "men",
        "women",
    )
    normalized_query = f" {normalize_category_text(query)} "
    normalized_direct = normalize_category_text(direct_category)
    normalized_alias = normalize_category_text(alias_category)
    query_names_audience = any(f" {cue} " in normalized_query for cue in audience_cues) or (
        " be " in normalized_query
    )
    alias_names_audience = any(cue in normalized_alias for cue in audience_cues)
    direct_names_audience = any(cue in normalized_direct for cue in audience_cues)
    return query_names_audience and alias_names_audience and not direct_names_audience


def resolve_catalog_category_with_explicit_audience(
    query: str,
    hierarchies: Iterable[tuple[str, ...]],
    base_match: CatalogCategoryMatch | None,
) -> CatalogCategoryMatch | None:
    """Refine a broad product phrase only when the shopper names an audience.

    For example, ``dầu gội dành cho nữ`` contains a broad alias for shampoo
    plus an explicit audience. This resolves against actual catalog leaves and
    stays conservative when no unique leaf represents that combination.
    """
    if base_match is None or not base_match.matched_tokens:
        return None

    normalized_query = f" {normalize_category_text(query)} "
    audience_forms: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("nu", ("nu", "phu nu", "chi em", "dan ba", "women", "female")),
        ("nam", ("nam", "dan ong", "men", "male")),
        ("tre em", ("tre em", "em be", "be", "be nho", "kids", "children")),
        ("nguoi lon", ("nguoi lon", "adult", "adults")),
    )
    stated_audience = next(
        (
            canonical
            for canonical, forms in audience_forms
            if any(f" {normalize_category_text(form)} " in normalized_query for form in forms)
        ),
        None,
    )
    if stated_audience is None:
        return None

    base_tokens = set(base_match.matched_tokens)
    audience_tokens = set(stated_audience.split())
    candidates: dict[str, tuple[str, ...]] = {}
    for path in tuple(hierarchies):
        if not path:
            continue
        label = path[-1]
        label_tokens = set(normalize_category_text(label).split())
        if base_tokens <= label_tokens and audience_tokens <= label_tokens:
            candidates[normalize_category_text(label)] = path
    if len(candidates) != 1:
        return None

    label_key, path = next(iter(candidates.items()))
    label = path[-1]
    return CatalogCategoryMatch(
        category=label,
        matched_tokens=frozenset(base_tokens | audience_tokens),
        exact_phrase=False,
    )


def _native_category_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", value.casefold())
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def _category_query_words(
    normalized_query: str,
    stop_words: frozenset[str],
    native_query: str | None = None,
) -> list[str]:
    words = normalized_query.split()
    native_words = _native_category_words(native_query or "")
    return [
        word
        for index, word in enumerate(words)
        if word not in stop_words
        or (word == "goi" and index > 0 and words[index - 1] == "dau")
        or (word == "an" and index < len(native_words) and native_words[index] == "ăn")
        or (index < len(native_words) and native_words[index] == "gối")
    ]


def _has_category_diacritic(value: str) -> bool:
    return "đ" in value or any(
        unicodedata.category(character) == "Mn" for character in unicodedata.normalize("NFD", value)
    )


def _resolve_native_exact_single_word_category(
    query: str,
    paths: tuple[tuple[str, ...], ...],
) -> CatalogCategoryMatch | None:
    """Keep accent-bearing one-word category labels distinct from folded fillers."""
    native_words = _native_category_words(query)
    request_fillers = _CATEGORY_QUERY_STOP_WORDS | _SINGLE_WORD_CATEGORY_CONTEXT_WORDS | {"xem"}
    matches: dict[str, str] = {}
    native_query_words = {word.casefold() for word in native_words}
    for path in paths:
        for label in path:
            label_words = _native_category_words(label)
            normalized_label = normalize_category_text(label)
            other_content = [
                normalize_category_text(word)
                for word in native_words
                if normalize_category_text(word) != normalized_label
                and normalize_category_text(word) not in request_fillers
            ]
            if (
                len(normalized_label.split()) == 1
                and len(label_words) == 1
                and _has_category_diacritic(label_words[0])
                and label_words[0].casefold() in native_query_words
                and not other_content
            ):
                matches.setdefault(normalized_label, label)
    if len(matches) != 1:
        return None
    normalized_label, label = next(iter(matches.items()))
    return CatalogCategoryMatch(
        category=label,
        matched_tokens=frozenset({normalized_label}),
        exact_phrase=True,
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
    paths = tuple(hierarchies)
    native_match = _resolve_native_exact_single_word_category(query, paths)
    if native_match is not None:
        return native_match
    match = _resolve_catalog_category_cached(query, paths)
    if match is None or not category_is_parent(match.category, paths):
        return match

    # A broad parent can match more words than a specific item explicitly
    # named earlier in a location or comparison question (e.g. ``cà chua ở
    # khu rau ăn quả``). Refine only when exactly one descendant leaf is fully
    # represented by the utterance; otherwise retain the broad parent.
    query_tokens = set(normalize_category_text(query).split())
    parent_key = normalize_category_text(match.category)
    leaf_candidates: dict[str, str] = {}
    for path in paths:
        if len(path) < 2 or not any(
            normalize_category_text(segment) == parent_key for segment in path[:-1]
        ):
            continue
        leaf = path[-1]
        leaf_tokens = set(normalize_category_text(leaf).split())
        if len(leaf_tokens) >= 2 and leaf_tokens <= query_tokens:
            leaf_candidates[normalize_category_text(leaf)] = leaf
    if len(leaf_candidates) == 1:
        leaf = next(iter(leaf_candidates.values()))
        return CatalogCategoryMatch(
            category=leaf,
            matched_tokens=frozenset(normalize_category_text(leaf).split()),
            exact_phrase=False,
        )
    return match


@lru_cache(maxsize=512)
def _resolve_catalog_category_cached(
    query: str,
    hierarchies: tuple[tuple[str, ...], ...],
) -> CatalogCategoryMatch | None:
    """Match a generic product-type phrase to the active catalog taxonomy.

    Exact taxonomy phrases win. Shorter spoken forms such as “rửa mặt” can match
    a unique catalog leaf like “Sữa rửa mặt nữ”; ambiguous variants resolve to
    their nearest shared parent and keep the phrase available to product search.
    """
    normalized_query = normalize_category_text(query)
    query_words = _category_query_words(normalized_query, _CATEGORY_QUERY_STOP_WORDS, query)
    query_tokens = frozenset(query_words)
    category_query_words = _category_query_words(
        re.sub(r"\bgoi\s+y\b", " ", normalized_query),
        _SINGLE_WORD_CATEGORY_CONTEXT_WORDS,
        query,
    )
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
        phrase_in_query = f" {key} " in f" {normalized_query} "
        if len(label_words) == 1:
            native_label_words = _native_category_words(label)
            native_label_is_stated = bool(
                native_label_words
                and _has_category_diacritic(native_label_words[0])
                and native_label_words[0] in _native_category_words(query)
            )
            if label_words[0] in {"khac", "other", "others"} or (
                label_words[0] in _SINGLE_WORD_CATEGORY_CONTEXT_WORDS and not native_label_is_stated
            ):
                continue
            # Accent folding makes Vietnamese homonyms collide inside longer
            # phrases (for example, "dầu gội" contains the folded token
            # "dau", which is also the folded spelling of "đậu"). A single
            # word taxonomy label is exact only when it is the full request
            # after conversational stop words are removed.
            exact_single_word = category_query_words == label_words
            native_label_words = _native_category_words(label)
            if (
                native_label_words
                and native_label_words[0] in _native_category_words(query)
                and category_query_words
                and category_query_words[0] == label_words[0]
            ):
                # Preserve accents for a named one-word type when it is the
                # leading product phrase ("gạo", "muối", "nồi"). Do not turn
                # a trailing material/accessory mention ("cốc giấy", "đèn pin")
                # into the requested product category.
                exact_single_word = True
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
            path for item in best for path in labels[normalize_category_text(item[3])][1]
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


def catalog_leaf_query_phrase(query: str, hierarchies: Iterable[tuple[str, ...]]) -> str:
    return _catalog_leaf_query_phrase_cached(query, tuple(hierarchies))


@lru_cache(maxsize=512)
def _catalog_leaf_query_phrase_cached(query: str, hierarchies: tuple[tuple[str, ...], ...]) -> str:
    """Find the longest literal product-type prefix in the active taxonomy."""
    words = normalize_category_text(query).split()
    native_words = _native_category_words(query)
    paths = tuple(hierarchies)
    parents = {normalize_category_text(label) for path in paths for label in path[:-1]}
    stated_parents = [
        parent.split() for parent in parents if f" {parent} " in f" {' '.join(words)} "
    ]
    matches: list[tuple[int, int, str]] = []
    for path in paths:
        if len(path) < 4 or normalize_category_text(path[-1]) in parents:
            continue
        leaf_words = normalize_category_text(path[-1]).split()
        if len(leaf_words) == 1:
            native_label = _native_category_words(path[-1])[0]
            if leaf_words[0] in {"khac", "other", "others"} or (
                leaf_words[0] in _SINGLE_WORD_CATEGORY_CONTEXT_WORDS
                and not (_has_category_diacritic(native_label) and native_label in native_words)
            ):
                continue
        if len(leaf_words) == 1 and _native_category_words(path[-1])[0] in native_words:
            matches.append((1, -words.index(leaf_words[0]), leaf_words[0]))
        for size in range(min(len(words), len(leaf_words)), 1, -1):
            prefix = leaf_words[:size]
            if " ".join(prefix) in parents or any(
                len(parent) >= size and parent[:size] == prefix for parent in stated_parents
            ):
                continue
            for start in range(len(words) - size + 1):
                if words[start : start + size] == prefix:
                    matches.append((size, -start, " ".join(prefix)))
    return max(matches, default=(0, 0, ""))[2]


def query_names_catalog_leaf(query: str, hierarchies: Iterable[tuple[str, ...]]) -> bool:
    """Return whether a phrase in a query names or prefixes a catalog leaf.

    This lets discovery keep product-type words such as “dầu gội” while
    dropping broad needs such as “da khô” from the actual product-title filter.
    The match is derived from the active catalog taxonomy, not a hand-maintained
    list of product categories.
    """
    return bool(catalog_leaf_query_phrase(query, hierarchies))


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
