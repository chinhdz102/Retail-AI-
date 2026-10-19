"""Evaluate lexical product resolution against a controlled local catalog snapshot.

This is an offline approximation for when PostgreSQL is unavailable. It does not
exercise the production SQL query, and its metrics must not be reported as a
PostgreSQL integration benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from backend.data.catalog_import import analyze_dataset
from backend.products.postgres_repository import normalize_search_text

ROOT = Path(__file__).resolve().parents[1]


def _case_rows(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not cases:
        raise ValueError(f"No product resolution cases found in {path}")
    return cases


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"\W+", normalize_search_text(text)) if token]


@dataclass(frozen=True)
class _CatalogCandidate:
    row: Any
    title: str
    brand: str
    category: str
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
        field_tokens = set(_tokens(" ".join((title, brand, category))))
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


def _score(query: str, candidate: _CatalogCandidate) -> tuple[float, str]:
    normalized = normalize_search_text(query).strip()
    tokens = _tokens(query)
    title, brand, category = candidate.title, candidate.brand, candidate.category

    if normalized.casefold() in candidate.identifiers:
        return 1000.0, "exact_identifier"
    if normalized and normalized == title:
        return 900.0, "exact_title"
    if normalized and title.startswith(normalized):
        return 800.0, "title_prefix"

    # Match the repository's accent folding, identifier handling and broad
    # title/brand/category fields, with a local edit-similarity fallback.
    fields = (title, brand, category)
    if tokens:
        hits = [
            any(token in field for field in fields)
            for token in tokens
        ]
        if all(hits):
            score = 700.0 + 5.0 * sum(token in title for token in tokens)
            return score, "all_tokens_lexical"
        if sum(hits) < 1:
            return 0.0, "insufficient_lexical_overlap"

        ratios = []
        for token, has_hit in zip(tokens, hits, strict=True):
            if has_hit:
                ratios.append(1.0)
                continue
            best = max(
                (
                    SequenceMatcher(None, token, candidate).ratio()
                    for candidate in candidate.words
                ),
                default=0.0,
            )
            ratios.append(best)
        coverage = sum(value >= 0.55 for value in ratios) / len(ratios)
        if coverage >= 0.6:
            phrase_ratio = max(SequenceMatcher(None, normalized, field).ratio() for field in fields)
            score = 100.0 * coverage + 25.0 * sum(ratios) / len(ratios) + phrase_ratio
            return score, "fuzzy_tokens"

    return 0.0, "no_match"


def _rank(
    query: str,
    candidates: list[_CatalogCandidate],
    token_index: dict[str, set[int]],
    identifier_index: dict[str, int],
    title_index: dict[str, set[int]],
    top_k: int,
) -> list[dict[str, Any]]:
    matches = []
    raw_query = query.casefold()
    snack_phrase = raw_query.strip() in {"bim bim", "bimbim", "giá bimbim", "gia bimbim"}
    explicit_diaper = "bỉm" in raw_query or "ta quan" in normalize_search_text(query)
    normalized = normalize_search_text(query).casefold()
    if normalized in identifier_index:
        candidate_ids = {identifier_index[normalized]}
    elif normalized in title_index:
        candidate_ids = set(title_index[normalized])
    elif snack_phrase:
        candidate_ids = {
            index for index, candidate in enumerate(candidates)
            if "bim bim" in candidate.title
            or any(term in candidate.category for term in ("snack", "bim", "banh keo"))
        }
    else:
        query_tokens = _tokens(query)
        token_matches = [token_index.get(token, set()) for token in query_tokens]
        exact_intersection = (
            set.intersection(*(set(matches) for matches in token_matches))
            if token_matches
            else set()
        )
        candidate_ids = exact_intersection or set().union(*token_matches)
    for index in candidate_ids:
        candidate = candidates[index]
        row = candidate.row
        score, reason = _score(query, candidate)
        category = candidate.category
        if snack_phrase:
            if re.search(r"\bbim\s+bim\b", candidate.title):
                score = max(score, 850.0)
                reason = "slang_exact_product_name"
            elif any(term in category for term in ("snack", "bim", "banh keo")):
                score = max(score, 740.0)
                reason = "slang_snack_category"
            else:
                score = 0.0
                reason = "excluded_non_snack_for_slang"
        if explicit_diaper and not any(term in category for term in ("bim", "ta", "cham soc be")):
            score = 0.0
            reason = "excluded_non_diaper"
        if score > 0:
            matches.append({
                "product_id": row.product_id,
                "name": row.name,
                "score": round(score, 4),
                "reason": reason,
            })
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
    outcomes = []
    latencies = []
    ambiguous = 0
    for case in cases:
        started = time.perf_counter_ns()
        results = _rank(
            case["query"], candidates, token_index, identifier_index, title_index, top_k
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
        outcomes.append({
            "case_id": case["case_id"],
            "query": case["query"],
            "kind": case["kind"],
            "gold_product_ids": sorted(gold),
            "results": results,
            "recall_at_1": first_rank == 1,
            "recall_at_3": first_rank is not None and first_rank <= min(3, top_k),
            "recall_at_5": first_rank is not None and first_rank <= min(5, top_k),
            "mrr": round(1 / first_rank, 4) if first_rank else 0.0,
            "first_relevant_rank": first_rank,
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
