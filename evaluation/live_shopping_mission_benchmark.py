"""Run live Groq shopping-mission conversations against the configured catalog.

The ``legacy`` mode is a controlled ablation of the current checkout with the
mission route and tool hidden. It is not a historical Git revision.

Example::

    .venv/Scripts/python.exe -m evaluation.live_shopping_mission_benchmark \
        --case-ids SMH-001 SMH-021 --mode both
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import unicodedata
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx

from backend.api import main as api_main
from backend.api.main import app, get_groq_service, session_store, shopping_service
from backend.config import get_settings
from backend.services import retail_agent
from evaluation.live_groq_smoke import RecordingGroq, RequestPacer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_FILE = ROOT / "evaluation/cases/vietnamese_shopping_mission_heldout_v1_20261021.json"


def _normalize(value: Any) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold().replace("đ", "d"))
    unaccented = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(unaccented.split())


def _goal_term_is_present(actual: str, expected_term: str) -> bool:
    """Match a small Vietnamese phrasing variant without rewarding negated goals."""
    actual_tokens = re.findall(r"[a-z0-9]+", _normalize(actual))
    expected_tokens = re.findall(r"[a-z0-9]+", _normalize(expected_term))
    if not expected_tokens:
        return False

    variants = [expected_tokens]
    if expected_tokens == ["xem", "bong"]:
        variants.append(["xem", "tran", "bong"])

    for variant in variants:
        width = len(variant)
        for start in range(len(actual_tokens) - width + 1):
            if actual_tokens[start : start + width] != variant:
                continue
            prefix = actual_tokens[max(0, start - 2) : start]
            if prefix and prefix[-1] in {"khong", "chua"}:
                continue
            if prefix[-2:] == ["khong", "muon"]:
                continue
            return True
    return False


def _all_need_text(mission: dict[str, Any]) -> str:
    fragments: list[str] = []
    for need in mission.get("needs", []):
        if isinstance(need, dict):
            for key in (
                "need_id",
                "label",
                "category",
                "resolved_category",
                "product_query",
                "rationale",
            ):
                fragments.append(str(need.get(key) or ""))
    return _normalize(" ".join(fragments))


def _groups_covered(groups: list[list[str]], mission: dict[str, Any]) -> list[bool]:
    searchable = _all_need_text(mission)
    return [any(_normalize(term) in searchable for term in group) for group in groups]


def _plan_groups_covered(
    groups: list[list[str]], items: list[dict[str, Any]], needs: list[dict[str, Any]]
) -> list[bool]:
    needs_by_id = {str(need.get("need_id")): need for need in needs if need.get("need_id")}
    fragments: list[str] = []
    for item in items:
        for key in ("need_label", "category", "rationale", "name"):
            fragments.append(str(item.get(key) or ""))
        linked_need = needs_by_id.get(str(item.get("need_id")))
        if linked_need:
            for key in ("label", "category", "resolved_category", "product_query"):
                fragments.append(str(linked_need.get(key) or ""))
    searchable = _normalize(" ".join(fragments))
    return [any(_normalize(term) in searchable for term in group) for group in groups]


def _plan_arithmetic_is_valid(options: list[dict[str, Any]]) -> bool:
    for option in options:
        items = [item for item in option.get("items", []) if isinstance(item, dict)]
        if len(items) != len(option.get("items", [])):
            return False
        item_total = 0
        for item in items:
            try:
                quantity = int(item["quantity"])
                unit_price = int(item["unit_price_vnd"])
                line_total = int(item["line_total_vnd"])
            except (KeyError, TypeError, ValueError):
                return False
            if quantity <= 0 or unit_price < 0 or line_total != unit_price * quantity:
                return False
            item_total += line_total
        try:
            option_total = int(option["total_vnd"])
        except (KeyError, TypeError, ValueError):
            return False
        if option_total != item_total:
            return False
    return True


def _cart_signature(session_id: UUID) -> tuple[tuple[str, int], ...]:
    try:
        cart = shopping_service.get_cart(session_id)
    except Exception:
        return ()
    items = getattr(cart, "items", ())
    return tuple(
        sorted(
            (str(getattr(item, "product_id", "")), int(getattr(item, "quantity", 0)))
            for item in items
        )
    )


def grade_turn(
    expected: dict[str, Any],
    response: dict[str, Any],
    mission: dict[str, Any] | None,
    cart_unchanged: bool,
    previous_mission_id: str | None,
    product_ids_in_catalog: set[str],
    previous_mission_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mission = mission if isinstance(mission, dict) else {}
    plan = response.get("shopping_plan")
    plan = plan if isinstance(plan, dict) else {}
    needs = [need for need in mission.get("needs", []) if isinstance(need, dict)]
    plan_options = [option for option in plan.get("options", []) if isinstance(option, dict)]
    plan_items = [
        item
        for option in plan_options
        for item in option.get("items", [])
        if isinstance(item, dict)
    ]
    primary_plan_items = (
        [item for item in plan_options[0].get("items", []) if isinstance(item, dict)]
        if plan_options
        else []
    )
    seen_ids = {
        str(product_id) for product_id in response.get("grounded_product_ids", []) if product_id
    }
    seen_ids.update(str(item.get("product_id")) for item in plan_items if item.get("product_id"))
    state_text = _normalize(
        " ".join(str(mission.get(key) or "") for key in ("goal", "use_case", "occasion"))
    )
    goal_ok = not expected.get("goal_terms_any") or any(
        _goal_term_is_present(state_text, str(term)) for term in expected["goal_terms_any"]
    )
    group_coverage = _groups_covered(expected.get("need_category_groups", []), mission)
    required_coverage = _groups_covered(expected.get("required_category_groups", []), mission)
    primary_plan_group_coverage = _plan_groups_covered(
        expected.get("required_category_groups", []), primary_plan_items, needs
    )
    required_need_specs = [
        need
        for need in needs
        if str(need.get("source", "")).upper() == "EXPLICIT" and need.get("required") is True
    ]
    inferred_need_specs = [
        need for need in needs if str(need.get("source", "")).upper() == "INFERRED"
    ]
    budget = expected.get("budget_vnd")
    totals = [int(option.get("total_vnd", 0)) for option in plan_options]
    budget_ok = budget is None or not totals or all(total <= int(budget) for total in totals)
    party_size = expected.get("party_size")
    expected_existing = [_normalize(item) for item in expected.get("existing_items_any", [])]
    mission_existing = [_normalize(item) for item in mission.get("existing_items", [])]
    unverified_terms = [
        _normalize(term) for term in expected.get("unverified_constraint_terms_any", [])
    ]
    actual_unverified = _normalize(
        " ".join(str(value) for need in needs for value in need.get("unverified_constraints", []))
    )
    tools_used = [str(tool).casefold().replace("-", "_") for tool in response.get("tools_used", [])]
    cart_write_tool_prefixes = (
        "add_to_cart",
        "remove_from_cart",
        "update_cart",
        "clear_cart",
        "checkout",
        "place_order",
        "submit_order",
        "purchase",
        "buy_products",
    )
    cart_write_tool_called = any(tool.startswith(cart_write_tool_prefixes) for tool in tools_used)
    plan_provenance_valid = all(
        bool(item.get("product_id"))
        and str(item.get("product_id")) in product_ids_in_catalog
        and bool(item.get("data_source"))
        and bool(item.get("data_timestamp"))
        for item in plan_items
    )
    response_text = _normalize(response.get("text", ""))
    response_digits = re.sub(r"\D", "", str(response.get("text", "")))
    expected_product_ids = {
        str(product_id)
        for product_id in expected.get("grounded_product_ids_any", [])
        if product_id
    }
    expected_fact_price = expected.get("fact_price_vnd")
    mission_plan_required = bool(expected.get("mission_plan_required", True))
    rejected_product_ids = {
        str(product_id)
        for product_id in mission.get("rejected_product_ids", [])
        if product_id
    }
    planned_product_ids = {
        str(item.get("product_id")) for item in plan_items if item.get("product_id")
    }
    response_terms = [_normalize(term) for term in expected.get("response_terms_any", [])]
    clarification_terms = [
        _normalize(term) for term in expected.get("clarification_terms_any", [])
    ]
    response_term_groups = [
        [_normalize(term) for term in group]
        for group in expected.get("response_term_groups_all", [])
    ]
    missing_need_terms = [
        _normalize(term) for term in expected.get("unavailable_required_need_terms_any", [])
    ]
    plan_missing_value = plan.get("missing_required_needs", [])
    plan_missing_text = _normalize(
        " ".join(map(str, plan_missing_value))
        if isinstance(plan_missing_value, list)
        else plan_missing_value
    )
    missing_need_named_in_plan = any(term in plan_missing_text for term in missing_need_terms)
    missing_need_disclosed = any(term in response_text for term in missing_need_terms)

    checks = {
        "mission_plan_present": bool(plan) is mission_plan_required,
        "goal_identified": goal_ok,
        "expected_category_groups_covered": all(group_coverage),
        "expected_required_groups_covered": all(required_coverage),
        "required_groups_present_in_primary_plan": all(primary_plan_group_coverage),
        "explicit_needs_are_required": not expected.get("explicit_needs_required")
        or all(
            any(
                any(
                    _normalize(term)
                    in _normalize(
                        " ".join(
                            str(need.get(key) or "")
                            for key in (
                                "label",
                                "category",
                                "resolved_category",
                                "product_query",
                            )
                        )
                    )
                    for term in group
                )
                for need in required_need_specs
            )
            for group in expected.get("required_category_groups", [])
        ),
        "inferred_needs_stay_optional": not expected.get("inferred_needs_optional")
        or bool(inferred_need_specs)
        and all(need.get("required") is False for need in inferred_need_specs),
        "budget_preserved": budget is None
        or int(mission.get("total_budget_vnd") or -1) == int(budget),
        "budget_compliant": budget_ok,
        "party_size_preserved": party_size is None or mission.get("party_size") == party_size,
        "minimum_plan_items": len(primary_plan_items) >= int(expected.get("minimum_plan_items", 0)),
        "products_exist_in_runtime_catalog": seen_ids <= product_ids_in_catalog,
        "plan_totals_match_line_items": _plan_arithmetic_is_valid(plan_options),
        "plan_products_have_catalog_provenance": plan_provenance_valid,
        "cart_write_tools_not_called": not cart_write_tool_called,
        "cart_unchanged": cart_unchanged and plan.get("cart_mutated", False) is False,
        "mission_id_stable": previous_mission_id is None
        or mission.get("mission_id") == previous_mission_id,
        "mission_state_preserved": not expected.get("preserve_mission_state")
        or previous_mission_state is not None
        and mission == previous_mission_state,
        "expected_grounded_products_present": not expected_product_ids
        or expected_product_ids <= seen_ids,
        "fact_price_present": expected_fact_price is None
        or str(int(expected_fact_price)) in response_digits,
        "rejected_products_recorded": not expected.get("rejected_products_recorded")
        or bool(rejected_product_ids),
        "rejected_products_not_recommended": not expected.get("rejected_products_recorded")
        or rejected_product_ids.isdisjoint(planned_product_ids),
        "visible_response_terms_satisfied": not response_terms
        or any(term in response_text for term in response_terms),
        "useful_clarification_asked": not clarification_terms
        or any(term in response_text for term in clarification_terms),
        "no_ungrounded_items_before_clarification": not expected.get(
            "no_grounded_products"
        )
        or not seen_ids,
        "visible_response_term_groups_satisfied": all(
            any(term in response_text for term in group) for group in response_term_groups
        ),
        "unavailable_required_need_named_in_plan": not missing_need_terms
        or missing_need_named_in_plan,
        "unavailable_required_need_disclosed": not missing_need_terms or missing_need_disclosed,
        "existing_items_recorded": not expected_existing
        or any(term in item for term in expected_existing for item in mission_existing),
        "unverified_constraints_tracked": not unverified_terms
        or all(term in actual_unverified for term in unverified_terms),
        "unsupported_constraints_cleared": not (
            "unverified_constraint_terms_any" in expected
            and not expected["unverified_constraint_terms_any"]
        )
        or not actual_unverified,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "category_group_coverage": group_coverage,
        "required_group_coverage": required_coverage,
        "primary_plan_required_group_coverage": primary_plan_group_coverage,
        "plan_item_count": len(plan_items),
        "primary_plan_item_count": len(primary_plan_items),
        "plan_totals_vnd": totals,
        "mission_need_count": len(needs),
        "grounded_product_count": len(seen_ids),
        "missing_product_ids": sorted(seen_ids - product_ids_in_catalog),
    }


def _service_override(service: RecordingGroq):
    return lambda: service


class RotatingCredentialRecordingGroq(RecordingGroq):
    """Use configured keys round-robin in a live benchmark, preserving fallback order."""

    def __init__(self, settings, pacer: RequestPacer, credential_start_index: int) -> None:
        super().__init__(settings, pacer)
        self.credential_start_index = credential_start_index

    def _credentials_or_raise(self) -> tuple[tuple[str, str], ...]:
        credentials = super()._credentials_or_raise()
        offset = self.credential_start_index % len(credentials)
        self.credential_start_index = (offset + 1) % len(credentials)
        return credentials[offset:] + credentials[:offset]


def _header_seconds(value: Any) -> float | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
        if not match:
            return None
        amount = float(match.group(1))
        scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]
        return amount * scale


def _rate_limit_cooldown_seconds(trace: list[dict[str, Any]]) -> float:
    """Read provider retry/reset headers from failed Groq attempts without exposing keys."""
    waits: list[float] = []
    for call in trace:
        for attempt in call.get("fallback_trace", []):
            if attempt.get("error_type") != "RateLimitError":
                continue
            headers = attempt.get("rate_limit_headers", {})
            for name in ("retry-after", "x-ratelimit-reset-tokens"):
                seconds = _header_seconds(headers.get(name))
                if seconds is not None:
                    waits.append(seconds)
    return max(waits, default=0.0)


def _successful_credential_slot(trace: list[dict[str, Any]]) -> str | None:
    for call in reversed(trace):
        for attempt in reversed(call.get("fallback_trace", [])):
            if attempt.get("status") == "success" and attempt.get("credential_slot"):
                return str(attempt["credential_slot"])
    return None


def _credential_cursor_after_trace(
    credentials: tuple[tuple[str, str], ...],
    trace: list[dict[str, Any]],
    fallback_offset: int,
) -> int:
    slot = _successful_credential_slot(trace)
    slots = [name for name, _ in credentials]
    if slot in slots:
        return (slots.index(slot) + 1) % len(slots)
    return (fallback_offset + 1) % len(credentials) if credentials else 0


def _filter_mission_tool(original):
    def without_mission_tool(state):
        return [
            tool
            for tool in original(state)
            if (tool.get("function") or {}).get("name") != "plan_shopping_mission"
        ]

    return without_mission_tool


async def _run_one_mode(
    mode: str,
    cases: list[dict[str, Any]],
    output: Path,
    pace_seconds: float,
    outer_report: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings().model_copy(update={"groq_timeout_seconds": 18.0})
    repository = api_main.get_product_repository(settings)
    catalog_ids = {product.product_id for product in repository.search(limit=1000)}
    # Snapshot search caps page size, so direct ID checks use dataset rows for
    # this currently configured WinMart snapshot; demo IDs come from the repo.
    if settings.catalog_repository == "snapshot":
        snapshot_file = Path(settings.catalog_snapshot_path)
        if not snapshot_file.is_absolute():
            snapshot_file = ROOT / snapshot_file
        catalog_rows = json.loads(snapshot_file.read_text(encoding="utf-8"))
        catalog_ids.update(str(row.get("product_id")) for row in catalog_rows)

    original_route = api_main.should_route_to_shopping_mission
    original_tools_for_state = retail_agent.tools_for_state
    if mode == "legacy":
        api_main.should_route_to_shopping_mission = lambda *args, **kwargs: False
        retail_agent.tools_for_state = _filter_mission_tool(original_tools_for_state)

    result: dict[str, Any] = {
        "mode": mode,
        "mode_description": (
            "Current mission route and planner"
            if mode == "current"
            else (
                "Controlled ablation: mission route/tool hidden; same code, model, keys, "
                "catalog and prompts otherwise"
            )
        ),
        "model_primary": settings.groq_model,
        "fallback_models": [
            name for name in (settings.groq_fallback_model, settings.groq_fallback_model_2) if name
        ],
        "credential_slots": [slot for slot, _ in settings.groq_api_credentials],
        "credential_strategy": (
            "round-robin starting key; rotate to next key after a successful call"
        ),
        "catalog_repository": settings.catalog_repository,
        "catalog_product_ids_loaded": len(catalog_ids),
        "cases": [],
    }
    outer_report["mode_results"].append(result)
    pacer = RequestPacer(pace_seconds)
    credentials = settings.groq_api_credentials
    credential_cursor = 0
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://shopping-mission-eval",
            timeout=max(75.0, pace_seconds * 6 + 60.0),
        ) as client:
            for case in cases:
                session_response = await client.post(
                    "/sessions", json={"kiosk_id": f"mission-eval-{mode}"}
                )
                if session_response.status_code != 201:
                    raise RuntimeError(f"session creation failed: {session_response.status_code}")
                session_id_text = session_response.json()["session_id"]
                session_id = UUID(session_id_text)
                turn_results: list[dict[str, Any]] = []
                previous_mission_id = None
                for turn_number, turn in enumerate(case["turns"], start=1):
                    before_cart = _cart_signature(session_id)
                    try:
                        mission_before_turn = deepcopy(
                            session_store.get(session_id).shopping.shopping_mission
                        )
                    except Exception:
                        mission_before_turn = None
                    start_offset = credential_cursor
                    start_slot = credentials[start_offset][0] if credentials else None
                    service = RotatingCredentialRecordingGroq(settings, pacer, start_offset)
                    app.dependency_overrides[get_groq_service] = _service_override(service)
                    started = perf_counter()
                    response = None
                    try:
                        response = await client.post(
                            "/agent/respond",
                            json={
                                "session_id": session_id_text,
                                "text": turn["text"],
                                "language": "vi",
                            },
                        )
                        status_code = response.status_code
                        body = response.json()
                    except Exception as exc:
                        status_code = response.status_code if response is not None else None
                        body = {"error_type": type(exc).__name__, "text": ""}
                    finally:
                        app.dependency_overrides.pop(get_groq_service, None)
                        await service.aclose()

                    # A fallback can make the HTTP request succeed after an
                    # earlier model/key hit a provider rate limit. Respect the
                    # reset header in that case too; otherwise the benchmark
                    # keeps sending requests at full pace and burns every
                    # configured fallback against the same organization quota.
                    cooldown_seconds = _rate_limit_cooldown_seconds(service.trace)
                    if cooldown_seconds:
                        pacer.defer(cooldown_seconds)
                    credential_cursor = _credential_cursor_after_trace(
                        credentials,
                        service.trace,
                        service.credential_start_index,
                    )

                    try:
                        record = session_store.get(session_id)
                        mission = record.shopping.shopping_mission
                    except Exception:
                        mission = {}
                    after_cart = _cart_signature(session_id)
                    grade = grade_turn(
                        turn.get("expect", {}),
                        body,
                        mission,
                        before_cart == after_cart,
                        previous_mission_id,
                        catalog_ids,
                        previous_mission_state=mission_before_turn,
                    )
                    if mission.get("mission_id"):
                        previous_mission_id = mission["mission_id"]
                    turn_results.append(
                        {
                            "turn": turn_number,
                            "user_text": turn["text"],
                            "http_status": status_code,
                            "latency_ms": round((perf_counter() - started) * 1000),
                            "credential_start_slot": start_slot,
                            "credential_success_slot": _successful_credential_slot(service.trace),
                            "provider_rate_limit_cooldown_seconds": round(cooldown_seconds, 2),
                            "response": body,
                            "mission_state": mission,
                            "llm_trace": service.trace,
                            "grade": grade,
                        }
                    )
                    # Persist after each completed turn so quota or network errors
                    # still leave a usable partial report.
                    case_result = {
                        "case_id": case["case_id"],
                        "archetype": case.get("archetype"),
                        "tags": case.get("tags", []),
                        "turns": turn_results,
                    }
                    if turn_number == 1:
                        result["cases"].append(case_result)
                    else:
                        result["cases"][-1] = case_result
                    write_report(output, outer_report)

                result["cases"][-1] = case_result
                try:
                    await client.post(f"/sessions/{session_id_text}/close")
                except Exception:
                    pass
    finally:
        api_main.should_route_to_shopping_mission = original_route
        retail_agent.tools_for_state = original_tools_for_state

    result["summary"] = _summarize(result["cases"])
    return result


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [turn for case in cases for turn in case.get("turns", [])]
    checks = Counter(
        name
        for turn in turns
        for name, passed in turn.get("grade", {}).get("checks", {}).items()
        if passed
    )
    check_counts = Counter(
        name for turn in turns for name in turn.get("grade", {}).get("checks", {})
    )
    latencies = [int(turn["latency_ms"]) for turn in turns if turn.get("latency_ms") is not None]
    return {
        "scenario_count": len(cases),
        "turn_count": len(turns),
        "http_200_turns": sum(turn.get("http_status") == 200 for turn in turns),
        "turns_with_plan": sum(
            bool(turn.get("response", {}).get("shopping_plan")) for turn in turns
        ),
        "all_checks_passed_turns": sum(
            turn.get("grade", {}).get("passed", False) for turn in turns
        ),
        "check_pass_counts": {
            name: {"passed": checks[name], "total": count} for name, count in check_counts.items()
        },
        "median_api_latency_ms": round(statistics.median(latencies)) if latencies else None,
    }


def write_report(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _benchmark_complete(
    selected_case_count: int,
    dataset_case_count: int,
    selected_turn_count: int,
    mode_results: list[dict[str, Any]],
    expected_mode_count: int,
) -> bool:
    return (
        selected_case_count == dataset_case_count
        and len(mode_results) == expected_mode_count
        and all(
            result.get("summary", {}).get("turn_count") == selected_turn_count
            for result in mode_results
        )
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    case_file = Path(args.case_file).resolve()
    dataset = json.loads(case_file.read_text(encoding="utf-8"))
    cases = dataset.get("cases", [])
    if dataset.get("language_scope") != "Vietnamese only":
        raise ValueError("case file must declare Vietnamese-only scope")
    selected = [case for case in cases if args.case_ids is None or case["case_id"] in args.case_ids]
    if not selected or (
        args.case_ids and {case["case_id"] for case in selected} != set(args.case_ids)
    ):
        raise ValueError("case_ids must all exist in the selected case file")

    catalog_path = ROOT / "data/winmart_products.json"
    credential_count = len(get_settings().groq_api_credentials)
    between_mode_cooldown_seconds = 60 if args.mode == "both" and credential_count else 0
    base_report = {
        "kind": "live_shopping_mission_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset_id": dataset.get("dataset_id"),
        "dataset_sha256": hashlib.sha256(case_file.read_bytes()).hexdigest(),
        "dataset_case_count": len(cases),
        "selected_case_ids": [case["case_id"] for case in selected],
        "selected_turn_count": sum(len(case["turns"]) for case in selected),
        "catalog_snapshot_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "live_model_comparison": True,
        "human_quality_review": "required; automatic grades cover state/data/safety only",
        "pace_seconds": args.pace_seconds,
        "credential_rotation": (
            "round-robin across configured key slots; same start order in both modes"
        ),
        "credential_slot_count": credential_count,
        "between_mode_cooldown_seconds": between_mode_cooldown_seconds,
        "partial": True,
        "mode_results": [],
    }
    write_report(args.output, base_report)
    modes = ["current", "legacy"] if args.mode == "both" else [args.mode]
    for mode_index, mode in enumerate(modes):
        await _run_one_mode(
            mode,
            selected,
            args.output,
            args.pace_seconds,
            base_report,
        )
        base_report["complete"] = _benchmark_complete(
            len(selected),
            len(cases),
            sum(len(case["turns"]) for case in selected),
            base_report["mode_results"],
            len(modes),
        )
        base_report["partial"] = not base_report["complete"]
        write_report(args.output, base_report)
        if mode_index < len(modes) - 1 and between_mode_cooldown_seconds:
            await asyncio.sleep(between_mode_cooldown_seconds)
    base_report["complete"] = _benchmark_complete(
        len(selected),
        len(cases),
        sum(len(case["turns"]) for case in selected),
        base_report["mode_results"],
        len(modes),
    )
    base_report["partial"] = not base_report["complete"]
    write_report(args.output, base_report)
    return base_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-file", default=str(DEFAULT_CASE_FILE))
    parser.add_argument("--case-ids", nargs="*")
    parser.add_argument("--mode", choices=("current", "legacy", "both"), default="current")
    parser.add_argument("--pace-seconds", type=float, default=1.8)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation/results/live_shopping_mission_benchmark.json",
    )
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "complete": report.get("complete"),
                "mode_summaries": [
                    {"mode": item["mode"], **item["summary"]}
                    for item in report.get("mode_results", [])
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
