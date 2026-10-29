"""Compare legacy lexical search with the grounded query compiler and tool path."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.query_understanding import understand_catalog_query
from backend.services.retail_tools import execute_catalog_tool

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "winmart_products.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _in_category(product: Any, expected_category: str) -> bool:
    expected = normalize_category_text(expected_category)
    segments = [
        normalize_category_text(segment)
        for segment in (product.category_hierarchy or "").split(">")
    ]
    if not segments:
        segments = [normalize_category_text(product.category)]
    return expected in segments


def _run_query_cases(
    cases: list[dict[str, Any]], repository: SnapshotProductRepository
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    base_latencies: list[float] = []
    new_latencies: list[float] = []
    product_cases = [row for row in cases if row["kind"] == "product_resolution"]
    category_cases = [row for row in cases if row["kind"] != "product_resolution"]
    product_top1_base = product_top5_base = product_top1_new = product_top5_new = 0
    category_precision_base = category_precision_new = 0.0
    category_errors_base = category_errors_new = 0
    use_case_marked = use_case_total = 0

    for case in cases:
        started = time.perf_counter_ns()
        baseline = repository.search(case["query"], limit=10)
        base_ms = (time.perf_counter_ns() - started) / 1_000_000
        base_latencies.append(base_ms)

        started = time.perf_counter_ns()
        observation = execute_catalog_tool(
            "search_products",
            json.dumps({"query": case["query"], "limit": 10}, ensure_ascii=False),
            repository=repository,
        )
        new_ms = (time.perf_counter_ns() - started) / 1_000_000
        new_latencies.append(new_ms)
        actual = list(observation.products)
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "kind": case["kind"],
            "query": case["query"],
            "baseline_status": "SUCCESS" if baseline else "NOT_FOUND",
            "new_status": observation.status,
            "baseline_top5": [item.product_id for item in baseline[:5]],
            "new_top5": [item.product_id for item in actual[:5]],
            "baseline_latency_ms": round(base_ms, 4),
            "new_latency_ms": round(new_ms, 4),
        }

        if case["kind"] == "product_resolution":
            gold = set(case["gold_product_ids"])
            base_rank = next(
                (index for index, item in enumerate(baseline, 1) if item.product_id in gold), None
            )
            new_rank = next(
                (index for index, item in enumerate(actual, 1) if item.product_id in gold), None
            )
            product_top1_base += int(base_rank == 1)
            product_top5_base += int(base_rank is not None and base_rank <= 5)
            product_top1_new += int(new_rank == 1)
            product_top5_new += int(new_rank is not None and new_rank <= 5)
            row.update(
                {
                    "gold_product_ids": sorted(gold),
                    "baseline_relevant_rank": base_rank,
                    "new_relevant_rank": new_rank,
                }
            )
        else:
            expected_category = case["expected_category"]
            base_relevant = [item for item in baseline[:5] if _in_category(item, expected_category)]
            new_relevant = [item for item in actual[:5] if _in_category(item, expected_category)]
            category_precision_base += len(base_relevant) / max(1, min(5, len(baseline)))
            category_precision_new += len(new_relevant) / max(1, min(5, len(actual)))
            category_errors_base += len(baseline[:5]) - len(base_relevant)
            category_errors_new += len(actual[:5]) - len(new_relevant)
            row.update(
                {
                    "expected_category": expected_category,
                    "baseline_category_precision_at5": len(base_relevant)
                    / max(1, min(5, len(baseline))),
                    "new_category_precision_at5": len(new_relevant) / max(1, min(5, len(actual))),
                    "query_understanding": observation.content.get("query_understanding"),
                }
            )
            if case["kind"] == "use_case_category":
                use_case_total += 1
                query_understanding = observation.content.get("query_understanding") or {}
                parsed_use_case = query_understanding.get("use_case")
                use_case_marked += int(
                    bool(parsed_use_case)
                    and parsed_use_case in query_understanding.get("unverified_constraints", [])
                )
                row["expected_use_case"] = case["expected_use_case"]

        rows.append(row)

    product_count = len(product_cases)
    category_count = len(category_cases)
    metrics = {
        "query_count": len(cases),
        "product_resolution_count": product_count,
        "category_query_count": category_count,
        "product_recall_at_1": {
            "baseline": round(product_top1_base / max(1, product_count), 4),
            "query_compiler": round(product_top1_new / max(1, product_count), 4),
        },
        "product_recall_at_5": {
            "baseline": round(product_top5_base / max(1, product_count), 4),
            "query_compiler": round(product_top5_new / max(1, product_count), 4),
        },
        "category_precision_at_5": {
            "baseline": round(category_precision_base / max(1, category_count), 4),
            "query_compiler": round(category_precision_new / max(1, category_count), 4),
        },
        "category_product_type_error_rate": {
            "baseline": round(
                category_errors_base
                / max(
                    1,
                    sum(
                        min(5, len(repository.search(row["query"], limit=10)))
                        for row in category_cases
                    ),
                ),
                4,
            ),
            "query_compiler": round(
                category_errors_new
                / max(
                    1,
                    sum(
                        min(5, len(row["new_top5"]))
                        for row in rows
                        if row["kind"] != "product_resolution"
                    ),
                ),
                4,
            ),
        },
        "use_case_context_marked_unverified_rate": round(
            use_case_marked / max(1, use_case_total), 4
        ),
        "latency_ms": {
            "baseline_p50": round(statistics.median(base_latencies), 4) if base_latencies else None,
            "baseline_p95": _percentile(base_latencies, 0.95),
            "query_compiler_p50": round(statistics.median(new_latencies), 4)
            if new_latencies
            else None,
            "query_compiler_p95": _percentile(new_latencies, 0.95),
        },
    }
    return metrics, rows


def _run_conversations(
    path: Path, repository: SnapshotProductRepository
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    conversations = json.loads(path.read_text(encoding="utf-8"))
    details = []
    scope_retained = price_ranked = alternative_retained = 0
    for conversation in conversations:
        prior_queries: list[str] = []
        turns = []
        expected = conversation["expected_category"]
        turn_categories = []
        turn2_prices: list[int] = []
        for turn in conversation["turns"]:
            query = turn["user"]
            parsed = understand_catalog_query(
                query,
                repository.list_category_hierarchies(),
                previous_queries=tuple(prior_queries),
            )
            observation = execute_catalog_tool(
                "search_products",
                json.dumps({"query": query, "limit": 5}, ensure_ascii=False),
                repository=repository,
                previous_queries=tuple(prior_queries),
            )
            turn_categories.append(parsed.product_type)
            if len(turns) == 1:
                turn2_prices = [product.price_vnd for product in observation.products]
            turns.append(
                {
                    "query": query,
                    "intent": parsed.intent,
                    "product_type": parsed.product_type,
                    "ranking_criterion": parsed.ranking_criterion,
                    "status": observation.status,
                    "prices": [product.price_vnd for product in observation.products],
                    "category_precision_at5": (
                        round(
                            sum(_in_category(product, expected) for product in observation.products)
                            / len(observation.products),
                            4,
                        )
                        if observation.products
                        else 0.0
                    ),
                }
            )
            prior_queries.append(query)
        retained = all(category is not None for category in turn_categories)
        sorted_prices = turn2_prices == sorted(turn2_prices)
        alternative_kept = (
            len(turn_categories) >= 3
            and turn_categories[2] == turn_categories[0]
            and turn_categories[2] is not None
        )
        scope_retained += int(retained)
        price_ranked += int(sorted_prices)
        alternative_retained += int(alternative_kept)
        details.append(
            {
                "conversation_id": conversation["conversation_id"],
                "expected_category": expected,
                "scope_retained_all_turns": retained,
                "lowest_price_turn_sorted": sorted_prices,
                "alternative_follow_up_retained_scope": alternative_kept,
                "turns": turns,
            }
        )
    count = len(conversations)
    return (
        {
            "conversation_count": count,
            "scope_retention_all_turns": round(scope_retained / max(1, count), 4),
            "lowest_price_follow_up_sorted": round(price_ranked / max(1, count), 4),
            "alternative_follow_up_scope_retention": round(alternative_retained / max(1, count), 4),
        },
        details,
    )


def _category_inventory(
    repository: SnapshotProductRepository,
    category: str,
) -> tuple[Any, ...]:
    """Read all catalog pages in a category for price and alternative checks."""
    products_by_id: dict[str, Any] = {}
    offset = 0
    page_size = 20
    while offset < 20_000:
        page = repository.search(query="", category=category, limit=page_size, offset=offset)
        if not page:
            break
        for product in page:
            if _in_category(product, category):
                products_by_id[product.product_id] = product
        offset += len(page)
        if len(page) < page_size:
            break
    return tuple(products_by_id.values())


def _response_explicitly_reports_no_alternative(text: str) -> bool:
    normalized = normalize_category_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "khong con",
            "chua tim thay them",
            "khong tim thay them",
            "het lua chon",
            "no more",
            "no other",
        )
    )


async def _run_stateful_api_conversations(
    path: Path,
    repository: SnapshotProductRepository,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate real API/session/tool transitions without calling an LLM provider."""
    import httpx

    import backend.api.main as api_main
    from backend.services import retail_tools

    class NoProviderCalls:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, *_args: Any, **_kwargs: Any) -> str:
            self.calls += 1
            raise AssertionError("The deterministic API conversation unexpectedly called Groq")

    conversations = json.loads(path.read_text(encoding="utf-8"))
    provider = NoProviderCalls()
    original_api_repository = api_main.get_product_repository
    original_tool_repository = retail_tools.get_product_repository
    original_overrides = dict(api_main.app.dependency_overrides)
    api_main.get_product_repository = lambda *_args, **_kwargs: repository
    retail_tools.get_product_repository = lambda *_args, **_kwargs: repository
    api_main.app.dependency_overrides[api_main.get_groq_service] = lambda: provider

    details: list[dict[str, Any]] = []
    category_relevant = category_visible = 0
    id_card_consistent = 0
    id_card_checks = 0
    state_retained = 0
    state_checks = 0
    lowest_price_correct = 0
    lowest_price_checks = 0
    alternatives_novel = 0
    alternative_checks = 0
    alternative_available_returned = 0
    alternative_available_checks = 0
    provider_free_conversations = 0
    category_inventories = {
        conversation["expected_category"]: _category_inventory(
            repository,
            conversation["expected_category"],
        )
        for conversation in conversations
    }

    try:
        transport = httpx.ASGITransport(app=api_main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://retail-evaluation",
            timeout=90,
        ) as client:
            for conversation in conversations:
                provider_calls_before = provider.calls
                created = await client.post(
                    "/sessions",
                    json={"kiosk_id": f"consultation-eval-{conversation['conversation_id']}"},
                )
                failures: list[str] = []
                if created.status_code != 201:
                    details.append(
                        {
                            "conversation_id": conversation["conversation_id"],
                            "failures": [f"session_create_http_{created.status_code}"],
                            "turns": [],
                        }
                    )
                    continue

                session_id = created.json()["session_id"]
                expected_category = conversation["expected_category"]
                category_products = category_inventories[expected_category]
                category_ids = {product.product_id for product in category_products}
                all_visible_ids: list[str] = []
                turn_details: list[dict[str, Any]] = []

                for turn_index, turn in enumerate(conversation["turns"]):
                    response = await client.post(
                        "/agent/respond",
                        json={"session_id": session_id, "text": turn["user"]},
                    )
                    turn_row: dict[str, Any] = {
                        "query": turn["user"],
                        "expected_action": turn.get("intent"),
                        "http_status": response.status_code,
                        "failures": [],
                    }
                    if response.status_code != 200:
                        turn_row["failures"].append(f"respond_http_{response.status_code}")
                        failures.extend(turn_row["failures"])
                        turn_details.append(turn_row)
                        continue

                    payload = response.json()
                    ids = list(payload.get("grounded_product_ids") or [])
                    cards = list(payload.get("products") or [])
                    card_ids = [card.get("product_id") for card in cards]
                    id_card_checks += 1
                    card_ids_match = ids == card_ids
                    id_card_consistent += int(card_ids_match)
                    if not card_ids_match:
                        turn_row["failures"].append("grounded_ids_do_not_match_visible_cards")

                    product_rows = [
                        repository.get_product_snapshot(product_id) for product_id in ids
                    ]
                    unknown_ids = [
                        product_id
                        for product_id, product in zip(ids, product_rows)
                        if product is None
                    ]
                    wrong_category_ids = [
                        product_id
                        for product_id, product in zip(ids, product_rows)
                        if product is not None and not _in_category(product, expected_category)
                    ]
                    category_visible += sum(product is not None for product in product_rows)
                    category_relevant += sum(
                        product is not None and _in_category(product, expected_category)
                        for product in product_rows
                    )
                    if unknown_ids:
                        turn_row["failures"].append(f"unknown_grounded_ids:{unknown_ids}")
                    if wrong_category_ids:
                        turn_row["failures"].append(f"wrong_category_ids:{wrong_category_ids}")

                    prior_visible_ids = set(all_visible_ids)
                    all_visible_ids.extend(ids)
                    session_state = api_main.session_store.get(api_main.UUID(session_id)).shopping
                    state_checks += 1
                    retained_product_ids = set(session_state.selected_product_ids)
                    retained_product_ids.update(session_state.seen_product_ids)
                    retained_product_ids.update(session_state.original_displayed_product_ids)
                    retained_product_ids.update(
                        product_id
                        for page in session_state.displayed_product_pages
                        for product_id in page
                    )
                    state_contains_visible = set(all_visible_ids).issubset(retained_product_ids)
                    state_retained += int(state_contains_visible)
                    if not state_contains_visible:
                        turn_row["failures"].append("session_did_not_remember_visible_products")

                    if turn_index == 1:
                        lowest_price_checks += 1
                        displayed_before_price_followup = [
                            product
                            for product_id in prior_visible_ids
                            if (product := repository.get_product_snapshot(product_id)) is not None
                        ]
                        expected_min = min(
                            (product.price_vnd for product in displayed_before_price_followup),
                            default=None,
                        )
                        actual_min = min((card["price_vnd"] for card in cards), default=None)
                        correct_min = expected_min is not None and actual_min == expected_min
                        lowest_price_correct += int(correct_min)
                        turn_row["expected_min_price_vnd"] = expected_min
                        turn_row["returned_min_price_vnd"] = actual_min
                        if not correct_min:
                            turn_row["failures"].append(
                                "lowest_price_does_not_match_prior_visible_page"
                            )

                    is_alternative = turn.get("intent") == "alternative"
                    if is_alternative:
                        prior_ids = prior_visible_ids
                        alternative_checks += 1
                        novel = not (prior_ids & set(ids))
                        alternatives_novel += int(novel)
                        if not novel:
                            turn_row["failures"].append("alternative_repeated_visible_product")

                        unseen_category_ids = category_ids - prior_ids
                        if unseen_category_ids:
                            alternative_available_checks += 1
                            returned_available = bool(set(ids) & unseen_category_ids)
                            alternative_available_returned += int(returned_available)
                            if not returned_available:
                                turn_row["failures"].append(
                                    "catalog_alternative_available_but_none_returned"
                                )
                        elif not ids and not _response_explicitly_reports_no_alternative(
                            payload.get("text", "")
                        ):
                            turn_row["failures"].append(
                                "catalog_exhausted_without_clear_no_alternative_response"
                            )

                    turn_row.update(
                        {
                            "grounded_product_ids": ids,
                            "visible_product_ids": card_ids,
                            "tools_used": payload.get("tools_used", []),
                            "model": payload.get("model"),
                            "answer": payload.get("text"),
                            "seen_product_ids_after_turn": list(session_state.seen_product_ids),
                        }
                    )
                    failures.extend(turn_row["failures"])
                    turn_details.append(turn_row)

                provider_free = provider.calls == provider_calls_before
                provider_free_conversations += int(provider_free)
                if not provider_free:
                    failures.append("unexpected_provider_call")
                details.append(
                    {
                        "conversation_id": conversation["conversation_id"],
                        "expected_category": expected_category,
                        "catalog_category_product_count": len(category_products),
                        "previous_visible_ids_are_tracked": all(
                            "session_did_not_remember_visible_products"
                            not in turn["failures"]
                            for turn in turn_details
                        ),
                        "provider_free": provider_free,
                        "failures": list(dict.fromkeys(failures)),
                        "turns": turn_details,
                    }
                )
                await client.post(f"/sessions/{session_id}/close")
    finally:
        api_main.get_product_repository = original_api_repository
        retail_tools.get_product_repository = original_tool_repository
        api_main.app.dependency_overrides.clear()
        api_main.app.dependency_overrides.update(original_overrides)

    conversation_count = len(conversations)
    turn_count = sum(len(item["turns"]) for item in details)
    metrics = {
        "mode": "in_process_agent_api_and_snapshot_catalog",
        "conversation_count": conversation_count,
        "turn_count": turn_count,
        "id_card_consistency_rate": round(id_card_consistent / max(1, id_card_checks), 4),
        "catalog_category_precision": round(category_relevant / max(1, category_visible), 4),
        "session_visible_product_retention_rate_across_state_fields": round(
            state_retained / max(1, state_checks),
            4,
        ),
        "lowest_price_matches_prior_visible_page_rate": round(
            lowest_price_correct / max(1, lowest_price_checks), 4
        ),
        "alternative_non_repetition_rate": round(
            alternatives_novel / max(1, alternative_checks),
            4,
        ),
        "alternative_return_rate_when_unseen_catalog_item_exists": round(
            alternative_available_returned / max(1, alternative_available_checks),
            4,
        ),
        "provider_free_conversation_rate": round(
            provider_free_conversations / max(1, conversation_count),
            4,
        ),
        "conversation_failures": sum(bool(item["failures"]) for item in details),
    }
    return metrics, details


def run(cases_path: Path, conversations_path: Path, source_path: Path) -> dict[str, Any]:
    repository = SnapshotProductRepository(source_path)
    cases = _read_jsonl(cases_path)
    metrics, outcomes = _run_query_cases(cases, repository)
    conversation_metrics, conversation_outcomes = _run_conversations(conversations_path, repository)
    api_conversation_metrics, api_conversation_outcomes = asyncio.run(
        _run_stateful_api_conversations(conversations_path, repository)
    )
    return {
        "kind": "generalized_catalog_consultation_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "offline_snapshot_and_deterministic_catalog_tool",
        "source_file": str(source_path),
        "dataset_sha256": repository.dataset_sha256,
        "case_file": str(cases_path),
        "conversation_file": str(conversations_path),
        "metrics": metrics,
        "parser_tool_conversation_diagnostic_metrics": conversation_metrics,
        "stateful_api_conversation_metrics": api_conversation_metrics,
        "limitations": [
            "No LLM generation/provider latency measured; metrics cover parser and catalog tools.",
            "Snapshot has no attributes to verify use-case suitability.",
            "Catalog-name variants test retrieval, not human preference relevance.",
            "Taxonomy labels come from source data; case generation is model-independent.",
            "Baseline uses direct snapshot search; compiler uses parser and production tools.",
        ],
        "cases": outcomes,
        "parser_tool_conversation_diagnostics": conversation_outcomes,
        "stateful_api_conversations": api_conversation_outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--conversations", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.cases, args.conversations, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "metrics": result["metrics"],
                "parser_tool_conversation_diagnostic_metrics": result[
                    "parser_tool_conversation_diagnostic_metrics"
                ],
                "stateful_api_conversation_metrics": result[
                    "stateful_api_conversation_metrics"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
