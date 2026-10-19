"""Evaluate lexical product resolution against a controlled local catalog snapshot.

This is an offline approximation for when PostgreSQL is unavailable. It does not
exercise the production SQL query, and its metrics must not be reported as a
PostgreSQL integration benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from backend.data.catalog_import import analyze_dataset
from backend.products.catalog import QUERY_STOP_WORDS
from backend.products.postgres_repository import normalize_search_text

ROOT = Path(__file__).resolve().parents[1]


def _case_rows(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not cases:
        raise ValueError(f"No product resolution cases found in {path}")
    return cases


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"(?<=\w)['’](?=\w)", "", normalize_search_text(text))
    return [token for token in re.split(r"\W+", normalized) if token]


@dataclass(frozen=True)
class _CatalogCandidate:
    row: Any
    title: str
    brand: str
    category: str
    uom: str
    identifiers: frozenset[str]
    field_tokens: frozenset[str]
    words: tuple[str, ...]


def _prepare_catalog(rows: list[Any]):
    candidates = []
    token_index: dict[str, set[int]] = {}
    identifier_index: dict[str, int] = {}
    title_index: dict[str, set[int]] = {}
    for row in rows:
        title = normalize_search_text(row.name)
        brand = normalize_search_text(row.brand)
        category = normalize_search_text(
            " ".join(filter(None, (row.category_name, row.department, row.sub_department)))
        )
        uom = normalize_search_text(row.uom)
        field_tokens = set(_tokens(" ".join((title, brand, category, uom))))
        identifiers = frozenset(
            value.casefold()
            for value in (row.product_id, row.item_no, row.barcode)
            if value
        )
        entry = _CatalogCandidate(
            row=row,
            title=title,
            brand=brand,
            category=category,
            uom=uom,
            identifiers=identifiers,
            field_tokens=frozenset(field_tokens),
            words=tuple(_tokens(" ".join((title, brand, category)))),
        )
        index = len(candidates)
        candidates.append(entry)
        for token in field_tokens:
            token_index.setdefault(token, set()).add(index)
        for identifier in identifiers:
            identifier_index[identifier] = index
        title_index.setdefault(title, set()).add(index)
    return candidates, token_index, identifier_index, title_index


_QUERY_FILLER_WORDS = QUERY_STOP_WORDS | frozenset(
    {
        "loai",
        "cai",
        "mon",
        "gi",
        "nha",
        "nhe",
        "vay",
        "khac",
        "nhau",
        "size",
        "kich",
        "co",
        "may",
    }
)
_PACKAGE_AND_SIZE_TERMS = frozenset(
    {
        "chai",
        "lon",
        "hop",
        "goi",
        "tui",
        "thung",
        "loc",
        "kg",
        "ml",
        "lit",
        "nho",
        "small",
        "smaller",
        "mini",
        "large",
        "bigger",
    }
)
_COMPARISON_CUES = frozenset({"khac", "nhau", "vs", "versus", "compare", "comparison"})


def _query_terms(query: str) -> tuple[list[str], bool, bool]:
    """Remove conversational noise and normalize a few common retail synonyms.

    ``bim bim`` is snack slang while accented ``bỉm`` means diaper. Keeping the
    distinction here avoids letting accent folding turn a snack query into a
    diaper category search. The aliases are concepts, not product-specific IDs.
    """
    normalized = normalize_search_text(query).strip()
    explicit_diaper = bool(re.search(r"\bbỉm\b|\btã\s+quần\b", query.casefold()))
    comparison = bool(_COMPARISON_CUES.intersection(_tokens(normalized))) or bool(
        re.search(r"\b(?:khac\s+gi|khac\s+nhau|so\s+san|vs|versus)\b", normalized)
    )
    normalized = re.sub(r"\bkhong\s+duong\b", "zero", normalized)
    if explicit_diaper:
        normalized = re.sub(r"\bbim\b", "ta", normalized)
    else:
        normalized = re.sub(r"\bbim\s+bim\b|\bbimbim\b", "snack", normalized)
    tokens = [
        token
        for token in _tokens(normalized)
        if token not in _QUERY_FILLER_WORDS
    ]
    contains_snack_slang = bool(
        re.search(r"\bbim\s+bim\b|\bbimbim\b", normalize_search_text(query))
    )
    return tokens, comparison, not explicit_diaper and contains_snack_slang


def _word_matches(token: str, words: tuple[str, ...] | frozenset[str]) -> bool:
    if token in words:
        return True
    # Size labels are commonly joined to a pack count (L38, XL48) and measures
    # to units (330ml, 2.9kg); accept those only as exact prefixes.
    if token.isalpha() and len(token) <= 3:
        return any(word.startswith(token) and word[len(token):].isdigit() for word in words)
    if token.isdigit():
        return any(word.startswith(token) and word[len(token):].isalpha() for word in words)
    return False


def _measure(candidate: _CatalogCandidate) -> tuple[float, str] | None:
    """Return the product's first normalized mass/volume measure, if present."""
    pattern = re.compile(
        r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(ml|milliliter|millilitre|lit|liter|litre|l|kg|g)\b"
    )
    match = pattern.search(candidate.title)
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


def _score(
    query: str,
    candidate: _CatalogCandidate,
    known_words: frozenset[str],
) -> tuple[float, str]:
    normalized = normalize_search_text(query).strip()
    if normalized.casefold() in candidate.identifiers:
        return 1000.0, "exact_identifier"
    if normalized and normalized == candidate.title:
        return 900.0, "exact_title"
    if normalized and candidate.title.startswith(normalized):
        return 800.0, "title_prefix"

    terms, is_comparison, has_snack_slang = _query_terms(query)
    if not terms:
        return 0.0, "no_meaningful_query_terms"

    field_words = (
        frozenset(_tokens(candidate.title)),
        frozenset(_tokens(candidate.brand)),
        frozenset(_tokens(candidate.category)),
        frozenset(_tokens(candidate.uom)),
    )
    field_weights = (3.0, 2.4, 1.3, 1.8)
    term_scores: list[float] = []
    for token in terms:
        exact_weight = max(
            (
                weight
                for words, weight in zip(field_words, field_weights, strict=True)
                if _word_matches(token, words)
            ),
            default=0.0,
        )
        if exact_weight:
            term_scores.append(exact_weight)
        else:
            term_scores.append(0.0)

    core_terms = [
        token
        for token in terms
        if len(token) >= 3
        and not token[0].isdigit()
        and token not in _PACKAGE_AND_SIZE_TERMS
    ]
    exact_core_matches = sum(
        1 for token in core_terms if term_scores[terms.index(token)] > 0
    )
    used_fuzzy_match = False
    for index, token in enumerate(terms):
        if term_scores[index] > 0:
            continue
        # Fuzzy matching handles a typo only when the token is absent from the
        # catalog vocabulary and the candidate already has an exact product
        # anchor. A real catalog word should not drift to a different word in
        # another product through edit distance.
        fuzzy_is_plausible = exact_core_matches >= 1 and token not in known_words
        if len(token) >= 4 and not token.isdigit() and fuzzy_is_plausible:
            fuzzy = max(
                (
                    SequenceMatcher(None, token, word).ratio() * weight
                    for words, weight in zip(field_words[:3], field_weights[:3], strict=True)
                    for word in words
                    if len(word) >= 4
                ),
                default=0.0,
            )
            fuzzy_score = fuzzy if fuzzy >= 2.16 else 0.0
            term_scores[index] = fuzzy_score
            used_fuzzy_match = used_fuzzy_match or bool(fuzzy_score)

    numeric_terms = [token for token in terms if token[0].isdigit()]
    numeric_scores = [
        score for token, score in zip(terms, term_scores, strict=True) if token in numeric_terms
    ]
    if numeric_terms:
        if is_comparison and len(numeric_terms) > 1:
            # A comparison query deliberately names alternatives. Each result
            # should match at least one requested size, not every size at once.
            if max(numeric_scores, default=0.0) == 0:
                return 0.0, "requested_size_not_matched"
        elif any(score == 0 for score in numeric_scores):
            return 0.0, "requested_size_not_matched"

    explicit_packaging = [
        token for token in terms if token in {"chai", "lon", "hop", "goi", "tui", "thung", "loc"}
    ]
    if explicit_packaging and not is_comparison and any(
        term_scores[terms.index(token)] == 0 for token in explicit_packaging
    ):
        return 0.0, "requested_packaging_not_matched"

    # Brand/product descriptors should agree. A candidate that matches only
    # "Coca" and a volume must not outrank a Coca Zero variant when "Zero" was
    # explicitly requested. Package words and size adjectives remain useful
    # ranking hints but are not required to be present in a product title.
    if any(term_scores[terms.index(token)] == 0 for token in core_terms):
        return 0.0, "explicit_product_term_not_matched"

    matched_content = sum(score > 0 for score in term_scores)
    if matched_content == 0:
        return 0.0, "insufficient_lexical_overlap"
    coverage = matched_content / len(terms)
    if coverage < 0.5:
        return 0.0, "insufficient_query_coverage"

    # Product-name and brand hits carry more weight than department/category
    # hits; missing conversational descriptors lower the score without hiding
    # valid candidates for underspecified questions.
    score = sum(term_scores) - 1.5 * (len(terms) - matched_content)
    title_hits = sum(
        1 for token in terms if _word_matches(token, field_words[0])
    )
    if title_hits >= 2:
        score += 2.0
    if has_snack_slang and "bim bim" in candidate.title:
        score += 5.0
    reason = "all_terms_matched" if matched_content == len(terms) else "partial_terms_matched"
    if used_fuzzy_match:
        reason += "_with_typo_tolerance"
    return max(score, 0.0), reason


def _rank(
    query: str,
    candidates: list[_CatalogCandidate],
    token_index: dict[str, set[int]],
    identifier_index: dict[str, int],
    title_index: dict[str, set[int]],
    top_k: int,
    known_words: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    matches = []
    if known_words is None:
        known_words = frozenset(word for candidate in candidates for word in candidate.words)
    normalized = normalize_search_text(query).casefold()
    if normalized in identifier_index:
        candidate_ids = {identifier_index[normalized]}
    elif normalized in title_index:
        candidate_ids = set(title_index[normalized])
    else:
        query_tokens, _comparison, _snack_slang = _query_terms(query)
        token_matches = [token_index.get(token, set()) for token in query_tokens]
        candidate_ids = set().union(*token_matches) if token_matches else set()
    for index in candidate_ids:
        candidate = candidates[index]
        row = candidate.row
        score, reason = _score(query, candidate, known_words)
        if score > 0:
            matches.append({
                "product_id": row.product_id,
                "name": row.name,
                "score": round(score, 4),
                "reason": reason,
            })
    normalized_query = normalize_search_text(query)
    has_small_size_preference = bool(
        re.search(r"\b(?:nho|mini|small|smaller)\b", normalized_query)
    )
    if has_small_size_preference and len(matches) > 1:
        # Candidate IDs in the result rows are product IDs, so use the prepared
        # candidate list to resolve them without depending on data file order.
        candidate_by_id = {entry.row.product_id: entry for entry in candidates}
        measured = {
            item["product_id"]: _measure(candidate_by_id[item["product_id"]])
            for item in matches
        }
        dimensions = [value[1] for value in measured.values() if value is not None]
        if dimensions:
            preferred_dimension = max(set(dimensions), key=dimensions.count)
            preferred = [
                value[0]
                for value in measured.values()
                if value is not None and value[1] == preferred_dimension
            ]
            if len(preferred) > 1 and max(preferred) > min(preferred):
                low, high = min(preferred), max(preferred)
                span = math.log2(high / low)
                for item in matches:
                    value = measured[item["product_id"]]
                    if value is not None and value[1] == preferred_dimension:
                        relative_size = math.log2(value[0] / low) / span
                        item["score"] = round(item["score"] + 2.0 * (1.0 - relative_size), 4)
    return sorted(matches, key=lambda item: (-item["score"], item["name"]))[:top_k]


def _percentile(values: list[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def run(cases_path: Path, source_path: Path, top_k: int) -> dict[str, Any]:
    rows, quarantined, dataset = analyze_dataset(source_path)
    if not dataset.get("valid"):
        raise ValueError("The local catalog snapshot failed schema validation")
    cases = _case_rows(cases_path)
    candidates, token_index, identifier_index, title_index = _prepare_catalog(rows)
    known_words = frozenset(word for candidate in candidates for word in candidate.words)
    outcomes = []
    latencies = []
    ambiguous = 0
    for case in cases:
        started = time.perf_counter_ns()
        results = _rank(
            case["query"], candidates, token_index, identifier_index, title_index, top_k,
            known_words,
        )
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        latencies.append(elapsed)
        gold = set(case["gold_product_ids"])
        ranks = [index for index, item in enumerate(results, 1) if item["product_id"] in gold]
        first_rank = min(ranks) if ranks else None
        is_ambiguous = bool(
            len(results) > 1
            and results[1]["score"] >= results[0]["score"] * 0.95
        )
        ambiguous += int(is_ambiguous)
        gold_coverage_at_3 = round(
            len(gold.intersection(item["product_id"] for item in results[:3])) / len(gold), 4
        )
        gold_coverage_at_5 = round(
            len(gold.intersection(item["product_id"] for item in results[:5])) / len(gold), 4
        )
        outcomes.append({
            "case_id": case["case_id"],
            "query": case["query"],
            "kind": case["kind"],
            "gold_product_ids": sorted(gold),
            "expected_resolution_behavior": case.get(
                "expected_resolution_behavior", "single_or_browse"
            ),
            "results": results,
            "recall_at_1": first_rank == 1,
            "recall_at_3": first_rank is not None and first_rank <= min(3, top_k),
            "recall_at_5": first_rank is not None and first_rank <= min(5, top_k),
            "mrr": round(1 / first_rank, 4) if first_rank else 0.0,
            "first_relevant_rank": first_rank,
            "gold_set_recall_at_3": gold_coverage_at_3,
            "gold_set_recall_at_5": gold_coverage_at_5,
            "ambiguous_top_result": is_ambiguous,
            "latency_ms": round(elapsed, 4),
        })

    count = len(outcomes)
    return {
        "kind": "offline_product_resolution_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "offline_snapshot_lexical_approximation",
        "postgresql_integration": "NOT_RUN: PostgreSQL connection timed out",
        "source_file": str(source_path),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "dataset_validation": {
            "valid": dataset["valid"],
            "rows_total": dataset["rows_total"],
            "rows_accepted": dataset["rows_accepted"],
            "rows_quarantined": len(quarantined),
            "dataset_sha256": dataset["dataset_version"],
        },
        "case_file": str(cases_path),
        "case_count": count,
        "top_k": top_k,
        "metrics": {
            "recall_at_1": round(sum(item["recall_at_1"] for item in outcomes) / count, 4),
            "recall_at_3": round(sum(item["recall_at_3"] for item in outcomes) / count, 4),
            "recall_at_5": round(sum(item["recall_at_5"] for item in outcomes) / count, 4),
            "gold_set_recall_at_3": round(
                sum(item["gold_set_recall_at_3"] for item in outcomes) / count, 4
            ),
            "gold_set_recall_at_5": round(
                sum(item["gold_set_recall_at_5"] for item in outcomes) / count, 4
            ),
            "mrr": round(sum(item["mrr"] for item in outcomes) / count, 4),
            "ambiguity_rate": round(ambiguous / count, 4),
            "median_latency_ms": round(statistics.median(latencies), 4),
            "p95_latency_ms": _percentile(latencies, 0.95),
        },
        "failures": [item for item in outcomes if not item["recall_at_1"]],
        "cases": outcomes,
        "limitations": [
            (
                "This evaluator ranks the controlled JSON snapshot with local "
                "lexical/edit-distance scoring."
            ),
            (
                "It is not the production PostgreSQL query and does not verify the database "
                "import, indexes, SQL fuzzy matching, or store inventory."
            ),
            "No checkout, reservation, or transaction may use this web snapshot.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evaluation/cases/product_resolution_v3_offline.jsonl",
    )
    parser.add_argument("--source", type=Path, default=ROOT / "data/winmart_products.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation/results/offline_product_resolution_benchmark.json",
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if not 5 <= args.top_k <= 20:
        parser.error("--top-k must be between 5 and 20")
    report = run(args.cases, args.source, args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "metrics": report["metrics"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
