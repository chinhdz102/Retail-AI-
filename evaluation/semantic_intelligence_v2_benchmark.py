"""Reproducible aggregate benchmark for the frozen Retail-AI V2 corpus.

Rows are saved for audit. Synthetic labels stay provisional until human review.
The optional live LLM arm is a structured-extraction ablation, not a judge.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.config import get_settings
from backend.products.category_taxonomy import normalize_category_text, resolve_catalog_category
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.groq import GroqProviderError, GroqService
from backend.services.query_understanding import understand_catalog_query
from backend.services.retail_tools import execute_catalog_tool
from backend.services.vietnamese_intent import normalize_vietnamese

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = (
    ROOT / "evaluation" / "cases" / "retail_ai_semantic_intelligence_v2_frozen_20261028.json"
)
DEFAULT_OUTPUT = (
    ROOT / "docs" / "reports" / "artifacts" / "retail_ai_semantic_intelligence_v2_benchmark.json"
)
DEFAULT_CATALOG = ROOT / "data" / "winmart_products.json"


class LLMExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: str = Field(min_length=1, max_length=32)
    product_type: str | None = Field(default=None, max_length=160)
    product_reference: str = Field(default="", max_length=160)
    use_case: str | None = Field(default=None, max_length=240)
    budget_vnd: int | None = Field(default=None, ge=0, le=100_000_000)
    budget_evidence: str = Field(default="", max_length=120)
    negative_constraints: list[str] = Field(default_factory=list, max_length=8)
    negative_evidence: list[str] = Field(default_factory=list, max_length=8)
    ranking_criterion: str = Field(default="relevance", max_length=32)
    clarification_needed: bool = False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _category_match(product: Any, expected: str) -> bool:
    expected_key = normalize_category_text(expected)
    path = [normalize_category_text(part) for part in (product.category_hierarchy or "").split(">")]
    return expected_key in path or expected_key == normalize_category_text(product.category)


def _budget_is_satisfied(price_vnd: int, query: str, maximum: int) -> bool:
    """Honor strict wording such as "dưới" separately from inclusive ceilings."""
    normalized = normalize_vietnamese(query)
    strict_upper_bound = bool(re.search(r"\b(?:duoi|under|below|less than)\b", normalized))
    return price_vnd < maximum if strict_upper_bound else price_vnd <= maximum


def _recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float | None:
    """Compute recall using every relevant ID and the actual top-k result window.

    Return ``None`` when a query has no relevant items; an empty gold set is not
    a recall score of zero and is measured separately as a correct abstention.
    """
    if k < 1:
        raise ValueError("k must be positive")
    if not relevant_ids:
        return None
    return len(set(retrieved_ids[:k]) & relevant_ids) / len(relevant_ids)


def _asks_for_alternative(query: str, expected_category: str) -> bool:
    """Detect a conversational alternative request, not ``khác`` in a label.

    The gold taxonomy scope is used only to mask its verbatim occurrence before
    the phrase check. This avoids marking labels such as ``Các loại bột khác``
    as an alternative request while keeping the grader independent of parser
    output.
    """
    normalized = normalize_vietnamese(query)
    normalized_category = normalize_vietnamese(expected_category).strip()
    if normalized_category:
        category_phrase = re.escape(normalized_category).replace(r"\ ", r"\s+")
        normalized = re.sub(
            rf"(?<!\w){category_phrase}(?!\w)",
            " ",
            normalized,
        )
    return bool(
        re.search(
            r"\b(?:loai nao khac|cai nao khac|mon nao khac|san pham nao khac|"
            r"lua chon nao khac|phuong an nao khac|loai khac|cai khac|mon khac|"
            r"san pham khac|lua chon khac|phuong an khac|other options?|"
            r"another (?:item|product|option)|different options?|different one)\b",
            normalized,
        )
    )


def _metrics_percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _run_deterministic(
    dataset: dict[str, Any], repository: SnapshotProductRepository
) -> dict[str, Any]:
    hierarchies = repository.list_category_hierarchies()
    rows: list[dict[str, Any]] = []
    latency_ms: list[float] = []
    parser_latency_ms: list[float] = []
    tool_latency_ms: list[float] = []
    metrics: dict[str, dict[str, int | float]] = defaultdict(lambda: defaultdict(float))

    for case in dataset["cases"]:
        expected = case["expected"]
        kind = expected["kind"]
        started = time.perf_counter_ns()
        parser_error = None
        try:
            parsed = understand_catalog_query(case["query"], hierarchies)
        except (ValidationError, ValueError) as exc:
            parsed = None
            parser_error = type(exc).__name__
        parser_elapsed = (time.perf_counter_ns() - started) / 1_000_000
        parser_latency_ms.append(parser_elapsed)
        tool_started = time.perf_counter_ns()
        try:
            observation = execute_catalog_tool(
                "search_products",
                json.dumps({"query": case["query"], "limit": 10}, ensure_ascii=False),
                repository=repository,
                current_user_text=case["query"],
            )
        except (ValidationError, ValueError) as exc:
            observation = None
            parser_error = parser_error or type(exc).__name__
        tool_elapsed = (time.perf_counter_ns() - tool_started) / 1_000_000
        tool_latency_ms.append(tool_elapsed)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        latency_ms.append(elapsed)
        products = list(observation.products) if observation is not None else []
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "dimension": case["dimension"],
            "kind": kind,
            "status": observation.status if observation is not None else "PARSER_ERROR",
            "product_ids": [item.product_id for item in products],
            "latency_ms": round(elapsed, 3),
            "parser_latency_ms": round(parser_elapsed, 3),
            "tool_latency_ms": round(tool_elapsed, 3),
            "parser_error_type": parser_error,
        }

        metric = metrics[kind]
        metric["count"] += 1
        if kind == "product_resolution":
            relevant = set(expected["product_ids"])
            rank = next(
                (i for i, product in enumerate(products, 1) if product.product_id in relevant), None
            )
            row.update(
                {
                    "gold_rank": rank,
                    "pass_at_1": rank == 1,
                    "pass_at_5": rank is not None and rank <= 5,
                }
            )
            metric["recall_at_1"] += int(rank == 1)
            metric["recall_at_5"] += int(rank is not None and rank <= 5)
            metric["mrr"] += 1 / rank if rank else 0
        elif kind == "category_browse":
            relevant = [
                product
                for product in products[:5]
                if _category_match(product, expected["category"])
            ]
            precision = len(relevant) / max(1, min(5, len(products)))
            parsed_category = parsed.product_type if parsed else None
            row.update(
                {"category_precision_at_5": round(precision, 4), "parsed_category": parsed_category}
            )
            metric["precision_at_5"] += precision
            metric["parser_category_accuracy"] += int(
                bool(parsed_category)
                and normalize_category_text(parsed_category)
                == normalize_category_text(expected["category"])
            )
            metric["parser_error_count"] += int(parser_error is not None)
        elif kind == "hard_constraint":
            expected_category = expected["category"]
            excluded_key = normalize_category_text(expected["excluded_brand"])
            eligible = [
                product
                for product in repository.products
                if _category_match(product, expected_category)
                and _budget_is_satisfied(
                    product.price_vnd,
                    case["query"],
                    int(expected["max_price_vnd"]),
                )
                and normalize_category_text(product.brand) != excluded_key
            ]
            all_valid_results = [
                product
                for product in products
                if _category_match(product, expected_category)
                and _budget_is_satisfied(
                    product.price_vnd,
                    case["query"],
                    int(expected["max_price_vnd"]),
                )
                and normalize_category_text(product.brand) != excluded_key
            ]
            eligible_ids = {product.product_id for product in eligible}
            top5_ids = [product.product_id for product in products[:5]]
            top10_ids = [product.product_id for product in products[:10]]
            valid_top5 = set(top5_ids) & eligible_ids
            valid_top10 = set(top10_ids) & eligible_ids
            violations = len(products) - len(all_valid_results)
            recall_at_5 = _recall_at_k(top5_ids, eligible_ids, 5)
            recall_at_10 = _recall_at_k(top10_ids, eligible_ids, 10)
            capacity_at_5 = min(5, len(eligible))
            capacity_at_10 = min(10, len(eligible))
            recall_ceiling_at_5 = (
                capacity_at_5 / len(eligible) if eligible else None
            )
            recall_ceiling_at_10 = (
                capacity_at_10 / len(eligible) if eligible else None
            )
            normalized_recall_at_5 = (
                len(valid_top5) / capacity_at_5 if capacity_at_5 else None
            )
            normalized_recall_at_10 = (
                len(valid_top10) / capacity_at_10 if capacity_at_10 else None
            )
            top10_underfilled = (
                len(valid_top10) < capacity_at_10 if eligible else None
            )
            row.update(
                {
                    "hard_constraint_pass": (bool(products) and violations == 0)
                    or (not eligible and not products),
                    "violations": violations,
                    "eligible_product_count": len(eligible),
                    "returned_product_count": len(products),
                    "valid_results_at_5": len(valid_top5),
                    "top_5_capacity": capacity_at_5,
                    "correct_abstention": not eligible and not products,
                    "candidate_recall_at_5": (
                        round(recall_at_5, 4) if recall_at_5 is not None else None
                    ),
                    "candidate_recall_at_5_capacity_ceiling": (
                        round(recall_ceiling_at_5, 4)
                        if recall_ceiling_at_5 is not None
                        else None
                    ),
                    "capacity_normalized_recall_at_5": (
                        round(normalized_recall_at_5, 4)
                        if normalized_recall_at_5 is not None
                        else None
                    ),
                    "valid_results_at_10": len(valid_top10),
                    "top_10_capacity": capacity_at_10,
                    "candidate_recall_at_10": (
                        round(recall_at_10, 4) if recall_at_10 is not None else None
                    ),
                    "candidate_recall_at_10_capacity_ceiling": (
                        round(recall_ceiling_at_10, 4)
                        if recall_ceiling_at_10 is not None
                        else None
                    ),
                    "capacity_normalized_recall_at_10": (
                        round(normalized_recall_at_10, 4)
                        if normalized_recall_at_10 is not None
                        else None
                    ),
                    "top_10_underfilled": top10_underfilled,
                    "parsed_category": parsed.product_type if parsed else None,
                    "parsed_budget_vnd": parsed.budget_vnd if parsed else None,
                    "parsed_negative_constraints": (
                        list(parsed.negative_constraints) if parsed else []
                    ),
                }
            )
            metric["constraint_case_pass"] += int(
                (bool(products) and violations == 0) or (not eligible and not products)
            )
            metric["constraint_violations"] += violations
            metric.setdefault("candidate_recall_at_5", 0)
            metric.setdefault("candidate_recall_at_5_evaluable_count", 0)
            metric.setdefault("candidate_recall_at_5_capacity_ceiling", 0)
            metric.setdefault("capacity_normalized_recall_at_5", 0)
            metric.setdefault("candidate_recall_at_10", 0)
            metric.setdefault("candidate_recall_at_10_evaluable_count", 0)
            metric.setdefault("candidate_recall_at_10_capacity_ceiling", 0)
            metric.setdefault("capacity_normalized_recall_at_10", 0)
            metric.setdefault("top_10_underfilled_case_count", 0)
            metric.setdefault("top_10_underfilled_evaluable_count", 0)
            if recall_at_5 is not None and recall_at_10 is not None:
                metric["candidate_recall_at_5"] += recall_at_5
                metric["candidate_recall_at_5_capacity_ceiling"] += recall_ceiling_at_5 or 0
                metric["capacity_normalized_recall_at_5"] += normalized_recall_at_5 or 0
                metric["candidate_recall_at_5_evaluable_count"] += 1
                metric["candidate_recall_at_10"] += recall_at_10
                metric["candidate_recall_at_10_capacity_ceiling"] += recall_ceiling_at_10 or 0
                metric["capacity_normalized_recall_at_10"] += normalized_recall_at_10 or 0
                metric["candidate_recall_at_10_evaluable_count"] += 1
                metric["top_10_underfilled_case_count"] += int(bool(top10_underfilled))
                metric["top_10_underfilled_evaluable_count"] += 1
            metric["nonempty_result"] += int(bool(products))
            metric["parser_error_count"] += int(parser_error is not None)
        rows.append(row)

    summarized: dict[str, Any] = {}
    for kind, values in metrics.items():
        count = int(values["count"])
        summarized[kind] = {"count": count}
        for key, value in values.items():
            if key == "count":
                continue
            if key == "candidate_recall_at_5_evaluable_count":
                summarized[kind][key] = int(value)
                continue
            if key in {
                "candidate_recall_at_10_evaluable_count",
                "top_10_underfilled_case_count",
                "top_10_underfilled_evaluable_count",
            }:
                summarized[kind][key] = int(value)
                continue
            if key in {
                "candidate_recall_at_5",
                "candidate_recall_at_5_capacity_ceiling",
                "capacity_normalized_recall_at_5",
            }:
                denominator = int(values["candidate_recall_at_5_evaluable_count"])
                summarized[kind][key] = round(value / denominator, 4) if denominator else None
                continue
            if key in {
                "candidate_recall_at_10",
                "candidate_recall_at_10_capacity_ceiling",
                "capacity_normalized_recall_at_10",
            }:
                denominator = int(values["candidate_recall_at_10_evaluable_count"])
                summarized[kind][key] = round(value / denominator, 4) if denominator else None
                continue
            summarized[kind][key] = (
                round(value / max(1, count), 4)
                if key
                in {
                    "recall_at_1",
                    "recall_at_5",
                    "mrr",
                    "precision_at_5",
                    "parser_category_accuracy",
                    "constraint_case_pass",
                    "candidate_recall_at_5",
                    "candidate_recall_at_5_capacity_ceiling",
                    "capacity_normalized_recall_at_5",
                    "candidate_recall_at_10",
                    "candidate_recall_at_10_capacity_ceiling",
                    "capacity_normalized_recall_at_10",
                    "nonempty_result",
                }
                else int(value)
            )
        if "top_10_underfilled_evaluable_count" in summarized[kind]:
            top10_evaluable = summarized[kind]["top_10_underfilled_evaluable_count"]
            summarized[kind]["top_10_underfilled_rate"] = (
                round(summarized[kind]["top_10_underfilled_case_count"] / top10_evaluable, 4)
                if top10_evaluable
                else None
            )
    return {
        "aggregate": summarized,
        "latency_ms": {
            "p50": _metrics_percentile(latency_ms, 0.50),
            "p95": _metrics_percentile(latency_ms, 0.95),
            "mean": round(statistics.mean(latency_ms), 3) if latency_ms else None,
        },
        "parser_latency_ms": {
            "p50": _metrics_percentile(parser_latency_ms, 0.50),
            "p95": _metrics_percentile(parser_latency_ms, 0.95),
            "mean": round(statistics.mean(parser_latency_ms), 3)
            if parser_latency_ms
            else None,
        },
        "production_tool_latency_ms": {
            "p50": _metrics_percentile(tool_latency_ms, 0.50),
            "p95": _metrics_percentile(tool_latency_ms, 0.95),
            "mean": round(statistics.mean(tool_latency_ms), 3) if tool_latency_ms else None,
        },
        "case_results": rows,
    }


def _run_conversation_evaluation(
    dataset: dict[str, Any], repository: SnapshotProductRepository
) -> dict[str, Any]:
    """Exercise scope, ranking and references through the real catalog tool."""
    hierarchies = repository.list_category_hierarchies()
    conversations: list[dict[str, Any]] = []
    totals: dict[str, int] = defaultdict(int)

    for conversation in dataset.get("conversations", []):
        history: list[str] = []
        expected_category = conversation["expected_category"]
        turns: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(conversation["turns"]):
            query = str(turn["user"])
            parsed = understand_catalog_query(
                query,
                hierarchies,
                previous_query=history[-1] if history else None,
                previous_queries=history,
            )
            asks_for_first_item = bool(
                re.search(r"(?:đầu tiên|first (?:item|product|one))", query, re.IGNORECASE)
            )
            if asks_for_first_item:
                # This harness has no assistant-visible ordering or selected SKU state.
                observation_status = "requires_stateful_api_validation"
                products = []
                category_in_scope = None
                parser_scope_exact = None
            else:
                observation = execute_catalog_tool(
                    "search_products",
                    json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
                    repository=repository,
                    current_user_text=query,
                    previous_query=history[-1] if history else None,
                    previous_queries=history,
                )
                observation_status = observation.status
                products = list(observation.products)
                category_in_scope = bool(products) and all(
                    _category_match(product, expected_category) for product in products
                )
                parser_scope_exact = normalize_category_text(
                    parsed.product_type or ""
                ) == normalize_category_text(expected_category)
            # Evaluate retained conversational scope by the products returned,
            # not by requiring one exact parser string for every valid alias.
            context_retained = category_in_scope
            prices = [product.price_vnd for product in products]
            price_ordered = (
                parsed.ranking_criterion == "price_ascending"
                and bool(products)
                and prices == sorted(prices)
            )
            asks_for_alternative = _asks_for_alternative(query, expected_category)
            alternative_understood = (
                parsed.intent == "alternative" or "alternative" in parsed.secondary_intents
            )
            first_item_reference_understood = not asks_for_first_item or bool(
                parsed.conversation_reference
            )
            turn_result = {
                "turn_index": turn_index,
                "query": query,
                "intent": parsed.intent,
                "secondary_intents": list(parsed.secondary_intents),
                "parsed_product_type": parsed.product_type,
                "ranking_criterion": parsed.ranking_criterion,
                "conversation_reference": parsed.conversation_reference,
                "tool_status": observation_status,
                "product_ids": [product.product_id for product in products],
                "category_scope_pass": category_in_scope,
                "context_retention_pass": context_retained,
                "parser_scope_exact_match": parser_scope_exact,
                "price_order_pass": price_ordered
                if parsed.ranking_criterion == "price_ascending"
                else None,
                "alternative_parser_intent_pass": (
                    alternative_understood if asks_for_alternative else None
                ),
                "alternative_novelty_status": (
                    "not_evaluated_without_session_visible_candidate_history"
                    if asks_for_alternative
                    else None
                ),
                "first_item_reference_pass": first_item_reference_understood
                if asks_for_first_item
                else None,
            }
            turns.append(turn_result)
            totals["turn_count"] += 1
            if category_in_scope is not None:
                totals["category_scope_cases"] += 1
                totals["category_scope_pass"] += int(category_in_scope)
            if turn_index and not asks_for_first_item:
                totals["context_retention_cases"] += 1
                totals["context_retention_pass"] += int(context_retained)
                totals["parser_scope_exact_cases"] += 1
                totals["parser_scope_exact_pass"] += int(parser_scope_exact)
            if parsed.ranking_criterion == "price_ascending":
                totals["price_order_cases"] += 1
                totals["price_order_pass"] += int(price_ordered)
            if asks_for_alternative:
                totals["alternative_cases"] += 1
                totals["alternative_parser_intent_pass"] += int(alternative_understood)
            if asks_for_first_item:
                totals["first_item_reference_cases"] += 1
                totals["first_item_reference_pass"] += int(first_item_reference_understood)
            history.append(query)

        conversations.append(
            {"conversation_id": conversation["conversation_id"], "turn_results": turns}
        )

    def rate(numerator: str, denominator: str) -> float | None:
        count = totals[denominator]
        return round(totals[numerator] / count, 4) if count else None

    return {
        "conversation_count": len(conversations),
        "turn_count": totals["turn_count"],
        "category_scope_cases": totals["category_scope_cases"],
        "category_scope_pass_rate": rate("category_scope_pass", "category_scope_cases"),
        "context_retention": {
            "cases": totals["context_retention_cases"],
            "pass_rate": rate("context_retention_pass", "context_retention_cases"),
        },
        "parser_scope_exact_match": {
            "cases": totals["parser_scope_exact_cases"],
            "pass_rate": rate("parser_scope_exact_pass", "parser_scope_exact_cases"),
        },
        "price_ordering": {
            "cases": totals["price_order_cases"],
            "pass_rate": rate("price_order_pass", "price_order_cases"),
        },
        "alternative_intent_recognition": {
            "cases": totals["alternative_cases"],
            "parser_pass_rate": rate("alternative_parser_intent_pass", "alternative_cases"),
            "candidate_novelty_evaluated_cases": 0,
            "candidate_novelty_status": "NOT_EVALUATED_REQUIRES_STATEFUL_API_SESSION",
        },
        "first_item_reference": {
            "cases": totals["first_item_reference_cases"],
            "pass_rate": rate("first_item_reference_pass", "first_item_reference_cases"),
        },
        "limitations": [
            "Runs the parser and catalog tool with prior user turns; it does not simulate "
            "assistant messages or selected-item state.",
            "Alternative and first-item-reference metrics check parsed intent/reference only; "
            "they do not prove that a new SKU or the selected first SKU was returned.",
            "Alternative metrics measure parser intent recognition, not unseen-product novelty.",
            (
                "First-item references are excluded from tool-scope metrics because this "
                "harness has no assistant-visible ordering; the live session API smoke "
                "checks the SKU and price."
            ),
            "Synthetic conversation labels require human review and do not measure response "
            "naturalness.",
        ],
        "conversation_results": conversations,
    }


def _validate_llm_extraction(
    raw: dict[str, Any], query: str, hierarchies: tuple[tuple[str, ...], ...]
) -> dict[str, Any]:
    parsed = LLMExtraction.model_validate(raw)
    category = None
    if parsed.product_type:
        match = resolve_catalog_category(parsed.product_type, hierarchies)
        category = match.category if match else None
    normalized_query = normalize_vietnamese(query)

    def evidence_is_verbatim(evidence: str) -> bool:
        return bool(evidence and normalize_vietnamese(evidence) in normalized_query)

    product_reference = parsed.product_reference.strip()
    if product_reference and normalize_vietnamese(product_reference) not in normalized_query:
        product_reference = ""
    budget = parsed.budget_vnd if evidence_is_verbatim(parsed.budget_evidence) else None
    negatives = [
        value.strip()
        for value, evidence in zip(
            parsed.negative_constraints, parsed.negative_evidence, strict=False
        )
        if value.strip() and evidence_is_verbatim(evidence)
    ]
    use_case = (
        parsed.use_case
        if parsed.use_case and normalize_vietnamese(parsed.use_case) in normalized_query
        else None
    )
    return {
        "intent": parsed.intent,
        "product_type": category,
        "product_reference": product_reference,
        "use_case": use_case,
        "budget_vnd": budget,
        "negative_constraints": negatives,
        "ranking_criterion": parsed.ranking_criterion,
        "clarification_needed": parsed.clarification_needed,
    }


def _llm_tool_definition() -> list[dict[str, Any]]:
    schema = LLMExtraction.model_json_schema()
    return [
        {
            "type": "function",
            "function": {
                "name": "extract_retail_query",
                "description": (
                    "Extract user-stated retail intent and cite exact constraint evidence."
                ),
                "parameters": schema,
            },
        }
    ]


async def _run_llm_ablation(
    dataset: dict[str, Any], repository: SnapshotProductRepository, sample_size: int
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_groq_key:
        return {"status": "skipped_no_credentials", "sample_size": 0, "aggregate": {}}
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in dataset["cases"]:
        by_kind[case["expected"]["kind"]].append(case)
    rng = __import__("random").Random(2026102802)
    sample = []
    for kind in ("category_browse", "hard_constraint", "product_resolution"):
        rows = list(by_kind[kind])
        rng.shuffle(rows)
        sample.extend(rows[: max(1, sample_size // 3)])
    sample = sample[:sample_size]
    categories = sorted(
        {segment for path in repository.list_category_hierarchies() for segment in path}
    )
    category_json = json.dumps(categories, ensure_ascii=False)
    service = GroqService(settings)
    semaphore = asyncio.Semaphore(2)
    outputs: list[dict[str, Any]] = []

    async def run_one(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            query = case["query"]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Extract retail query slots. Do not answer or recommend. Do not invent a "
                        "product, brand, price, stock, benefit, or constraint. Product type must "
                        "be one exact taxonomy segment when possible; otherwise null. Budget "
                        "and every negative constraint need exact user-quoted evidence. Preserve "
                        "the Vietnamese meaning and distinction between needs and requirements. "
                        f"Allowed catalog taxonomy segments: {category_json}"
                    ),
                },
                {"role": "user", "content": query},
            ]
            started = time.perf_counter()
            try:
                result = await service.chat(
                    messages,
                    tools=_llm_tool_definition(),
                    tool_choice={"type": "function", "function": {"name": "extract_retail_query"}},
                    max_tokens=320,
                )
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                call = next(
                    (
                        item
                        for item in result.tool_calls
                        if item["function"]["name"] == "extract_retail_query"
                    ),
                    None,
                )
                raw = json.loads(call["function"]["arguments"]) if call else {}
                validated = _validate_llm_extraction(
                    raw, query, repository.list_category_hierarchies()
                )
                expected = case["expected"]
                if expected["kind"] == "product_resolution":
                    ref_query = validated["product_reference"]
                    products = (
                        repository.search(ref_query, category=validated["product_type"], limit=10)
                        if ref_query
                        else []
                    )
                    gold = set(expected["product_ids"])
                    rank = next(
                        (i for i, p in enumerate(products, 1) if p.product_id in gold), None
                    )
                    passed = rank == 1
                else:
                    category = validated["product_type"]
                    products = (
                        repository.search(
                            validated["product_reference"] or category or "",
                            category=category,
                            limit=10,
                        )
                        if category
                        else []
                    )
                    passed = bool(category) and normalize_category_text(
                        category
                    ) == normalize_category_text(expected["category"])
                    if expected["kind"] == "hard_constraint":
                        passed = passed and validated["budget_vnd"] == expected["max_price_vnd"]
                        passed = passed and normalize_category_text(expected["excluded_brand"]) in {
                            normalize_category_text(value)
                            for value in validated["negative_constraints"]
                        }
                return {
                    "case_id": case["case_id"],
                    "kind": expected["kind"],
                    "pass": bool(passed),
                    "latency_ms": elapsed,
                    "model": getattr(result, "model", ""),
                    "fallback_attempt_count": len(getattr(result, "fallback_trace", [])),
                    "validated_extraction": validated,
                }
            except (
                GroqProviderError,
                json.JSONDecodeError,
                TypeError,
                ValidationError,
                ValueError,
            ) as exc:
                return {
                    "case_id": case["case_id"],
                    "kind": case["expected"]["kind"],
                    "pass": False,
                    "error_type": type(exc).__name__,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                }

    outputs = await asyncio.gather(*(run_one(case) for case in sample))
    by_kind_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    latencies: list[float] = []
    models: dict[str, int] = defaultdict(int)
    for row in outputs:
        by_kind_rows[row["kind"]].append(row)
        if row.get("latency_ms") is not None:
            latencies.append(float(row["latency_ms"]))
        if row.get("model"):
            models[row["model"]] += 1
    per_kind = {}
    for kind, rows in by_kind_rows.items():
        successful = [row for row in rows if row.get("error_type") is None]
        per_kind[kind] = {
            "sampled": len(rows),
            "successful": len(successful),
            "provider_errors": sum(row.get("error_type") == "GroqProviderError" for row in rows),
            "other_errors": sum(
                row.get("error_type") not in {None, "GroqProviderError"} for row in rows
            ),
            "accuracy_success_only": (
                round(sum(bool(row["pass"]) for row in successful) / len(successful), 4)
                if successful
                else None
            ),
        }
    provider_errors = sum(row.get("error_type") == "GroqProviderError" for row in outputs)
    non_provider_errors = sum(
        row.get("error_type") not in {None, "GroqProviderError"} for row in outputs
    )
    return {
        "status": "completed_with_provider_errors"
        if provider_errors
        else "completed_with_errors"
        if non_provider_errors
        else "completed",
        "sample_size": len(outputs),
        "provider_error_count": provider_errors,
        "provider_error_rate": round(provider_errors / max(1, len(outputs)), 4),
        "other_error_count": non_provider_errors,
        "per_kind": per_kind,
        "per_kind_accuracy": {
            kind: values["accuracy_success_only"] for kind, values in per_kind.items()
        },
        "latency_ms": {
            "p50": _metrics_percentile(latencies, 0.50),
            "p95": _metrics_percentile(latencies, 0.95),
        },
        "model_counts": dict(models),
        "case_results": outputs,
        "limitations": [
            "Provisional template-derived cases",
            "One LLM extraction per case",
            "LLM responses are not human-judged",
            "Evidence validation deliberately rejects unsupported claims",
        ],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    catalog_path = args.catalog.resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    repository = SnapshotProductRepository(catalog_path)
    git_status = (
        __import__("subprocess")
        .run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        .stdout
    )
    git_diff = (
        __import__("subprocess")
        .run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        .stdout
        or b""
    )
    result: dict[str, Any] = {
        "artifact_id": "retail_ai_semantic_intelligence_v2_benchmark",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "commit_sha": __import__("subprocess")
        .run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
        .stdout.strip(),
        "working_tree": {
            "dirty": bool(git_status.strip()),
            "status_sha256": hashlib.sha256(git_status.encode("utf-8")).hexdigest(),
            "tracked_diff_sha256": hashlib.sha256(git_diff).hexdigest(),
        },
        "dataset_id": dataset.get("dataset_id"),
        "dataset_authoring_method": dataset.get("authoring_method"),
        "dataset_sha256": _sha256(dataset_path),
        "catalog_sha256": _sha256(catalog_path),
        "query_count": len(dataset["cases"]),
        "conversation_count": len(dataset.get("conversations", [])),
        "evaluation_status": "provisional_requires_human_review",
        "deterministic_parser_and_catalog": _run_deterministic(dataset, repository),
        "conversation_parser_and_catalog": _run_conversation_evaluation(dataset, repository),
        "environment": {
            "python_runtime": "3.13+",
            "catalog_source": repository.source_name,
            "llm_credentials_configured": get_settings().has_groq_key,
            "voice_evaluated": False,
        },
    }
    if args.with_llm:
        result["llm_structured_extraction_ablation"] = await _run_llm_ablation(
            dataset, repository, args.llm_sample_size
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        key: result[key]
        for key in (
            "artifact_id",
            "commit_sha",
            "dataset_sha256",
            "query_count",
            "conversation_count",
            "evaluation_status",
        )
    }
    summary["deterministic_aggregate"] = result["deterministic_parser_and_catalog"]["aggregate"]
    summary["latency_ms"] = result["deterministic_parser_and_catalog"]["latency_ms"]
    summary["conversation_metrics"] = {
        key: value
        for key, value in result["conversation_parser_and_catalog"].items()
        if key != "conversation_results" and key != "limitations"
    }
    if "llm_structured_extraction_ablation" in result:
        llm = result["llm_structured_extraction_ablation"]
        summary["llm_ablation"] = {
            key: llm.get(key)
            for key in (
                "status",
                "sample_size",
                "provider_error_count",
                "provider_error_rate",
                "per_kind",
                "latency_ms",
                "model_counts",
            )
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--llm-sample-size", type=int, default=60)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
