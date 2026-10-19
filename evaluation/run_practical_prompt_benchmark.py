"""Run direct-text real-world conversations against the configured Groq LLM.

The benchmark calls the retail agent API directly and never invokes STT. It
stores provider traces and machine-checkable grades after every user turn so a
long live run can be inspected or resumed safely.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from backend.api.main import _system_prompt, app, get_groq_service
from backend.config import Settings
from evaluation.live_groq_smoke import RecordingGroq, RequestPacer, summarize_results

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = Path(__file__).parent / "cases" / "practical_prompt_realworld_v1.json"

ACKNOWLEDGEMENT_MARKERS = (
    "cảm ơn",
    "không có chi",
    "rất vui",
    "sẵn sàng giúp",
    "mình luôn sẵn sàng",
    "mình luôn ở đây",
    "you are welcome",
    "happy to help",
)
UNAVAILABLE_MARKERS = (
    "chưa có",
    "chưa được cung cấp",
    "chưa xác minh",
    "chưa thể xác minh",
    "không có thông tin",
    "không thể xác nhận",
    "không đủ dữ liệu",
    "không có dữ liệu",
    "not available",
    "can't verify",
    "cannot verify",
)
RESERVATION_UNAVAILABLE_MARKERS = (
    "chưa hỗ trợ",
    "không hỗ trợ",
    "không thể giữ",
    "chưa thể giữ",
    "không nhận giữ",
    "nhân viên",
)
NO_OTHER_MATCH_MARKERS = (
    "không có sản phẩm nào khác",
    "không còn sản phẩm nào khác",
    "không có món nào khác",
    "không còn món nào khác",
    "không tìm thấy sản phẩm khác",
    "không có lựa chọn nào khác",
    "không còn lựa chọn nào khác",
    "chỉ có",
    "only one",
    "no other",
)
ACTION_CLAIM_PATTERNS = {
    "reserved": (
        r"\bđã giữ\b",
        r"\bgiữ thành công\b",
        r"\bđã đặt (?:giữ|trước)\b",
        r"\breserved successfully\b",
    ),
    "reported": (
        r"\bđã báo (?:cửa hàng|nhân viên|lỗi)\b",
        r"\bđã gửi báo cáo\b",
        r"\bđã ghi nhận (?:báo lỗi|khiếu nại)\b",
        r"\breport has been submitted\b",
    ),
    "price_changed": (
        r"\bđã (?:sửa|đổi|cập nhật) giá\b",
        r"\bgiá (?:đã được )?(?:sửa|đổi|cập nhật)\b",
        r"\bprice (?:has been )?changed\b",
    ),
    "paid": (
        r"\bđã xác nhận thanh toán\b",
        r"\bđơn (?:hàng )?đã thanh toán\b",
        r"\bđã đánh dấu (?:là )?(?:đã )?thanh toán\b",
        r"\bmarked (?:as )?paid\b",
    ),
}
WEATHER_ASSERTION_PATTERNS = (
    r"(?:hôm nay|bây giờ|hiện tại|ngoài trời).{0,20}(?:đang )?(?:mưa|nắng|có mưa)",
    r"(?:trời|thời tiết).{0,20}(?:đang )?(?:mưa|nắng|âm u)",
    r"\b(?:it is|it's) (?:raining|sunny)\b",
)
NUTRITION_VALUE_PATTERNS = (
    r"\b\d+(?:[.,]\d+)?\s*(?:kcal|calo|calories?)\b",
    r"\b(?:kcal|calo|calories?)\s*(?:là|:)?\s*\d+(?:[.,]\d+)?\b",
)
ALLOWED_TOOLS = {"search_products", "recommend_products", "get_product_snapshot"}


def _tool_calls(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for llm_call in trace:
        for call in llm_call.get("tool_calls", []):
            try:
                arguments = json.loads(call.get("arguments", "{}"))
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            result.append({"name": call.get("name", ""), "arguments": arguments})
    return result


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in markers)


def _contains_assertion(text: str, patterns: tuple[str, ...]) -> bool:
    normalized = text.casefold()
    return any(re.search(pattern, normalized) for pattern in patterns)


def grade_turn(
    checks: dict[str, Any],
    *,
    status_code: int | None,
    response: dict[str, Any],
    trace: list[dict[str, Any]],
    previous_turn: dict[str, Any] | None,
) -> dict[str, Any]:
    text = str(response.get("text", ""))
    normalized = text.casefold()
    product_ids = list(response.get("grounded_product_ids", []))
    calls = _tool_calls(trace)
    grades: dict[str, dict[str, Any]] = {}

    def add(name: str, passed: bool, expected: Any, observed: Any) -> None:
        grades[name] = {
            "status": "PASS" if passed else "FAIL",
            "expected": expected,
            "observed": observed,
        }

    add("http_200", status_code == 200, 200, status_code)
    if status_code != 200:
        grades["answer_quality"] = {
            "status": "NOT_SCORED",
            "expected": "an LLM response to evaluate",
            "observed": response.get("detail", response.get("error_type", "no response")),
        }
        return {
            "status": "NOT_SCORED",
            "passed_checks": 0,
            "total_checks": 1,
            "checks": grades,
        }

    if "product_id" in checks:
        expected_id = checks["product_id"]
        add("grounded_product", expected_id in product_ids, expected_id, product_ids)
    if "contains" in checks:
        missing = [term for term in checks["contains"] if term.casefold() not in normalized]
        add(
            "required_answer_content",
            not missing,
            checks["contains"],
            {"text": text, "missing": missing},
        )
    if "contains_any" in checks:
        found = [term for term in checks["contains_any"] if term.casefold() in normalized]
        add(
            "one_answer_variant",
            bool(found),
            checks["contains_any"],
            {"text": text, "found": found},
        )
    if "tool_fields" in checks:
        target_id = checks.get("product_id")
        expected_fields = set(checks["tool_fields"])
        snapshots = [
            call["arguments"]
            for call in calls
            if call["name"] == "get_product_snapshot"
            and call["arguments"].get("product_id") == target_id
        ]
        exact = any(
            set(snapshot.get("fields", [])) == expected_fields
            and len(snapshot.get("fields", [])) == len(expected_fields)
            for snapshot in snapshots
        )
        add(
            "requested_exact_catalog_fields",
            exact,
            {"product_id": target_id, "fields": sorted(expected_fields)},
            snapshots,
        )
    if checks.get("no_product_match"):
        search_calls = [
            call for call in calls if call["name"] in {"search_products", "recommend_products"}
        ]
        add(
            "abstains_on_unknown_product",
            not product_ids and bool(search_calls),
            "no grounded product; searched catalog",
            {"product_ids": product_ids, "calls": calls, "text": text},
        )
    if checks.get("no_sellable_match"):
        recommendation_calls = [call for call in calls if call["name"] == "recommend_products"]
        clear_no_match = any(
            marker in normalized
            for marker in ("không tìm thấy", "không có sản phẩm", "không có món", "no matching")
        )
        add(
            "no_match_respected",
            not product_ids and bool(recommendation_calls) and clear_no_match,
            "empty sellable result explained",
            {
                "product_ids": product_ids,
                "recommendation_calls": recommendation_calls,
                "text": text,
            },
        )
    if "price_operator" in checks:
        recommendation_calls = [call for call in calls if call["name"] == "recommend_products"]
        observed = [call["arguments"].get("price_constraint") for call in recommendation_calls]
        expected = {"operator": checks["price_operator"], "value_vnd": checks["price_value_vnd"]}
        passed = any(item == expected for item in observed)
        add("price_operator_boundary", passed, expected, observed)
    if "price_lt" in checks or "price_lte" in checks:
        recommendation_calls = [call for call in calls if call["name"] == "recommend_products"]
        constraints = [call["arguments"].get("price_constraint") for call in recommendation_calls]
        operator = "LT" if "price_lt" in checks else "LTE"
        value = checks.get("price_lt", checks.get("price_lte"))
        expected = {"operator": operator, "value_vnd": value}
        passed = any(item == expected for item in constraints)
        add("price_constraint", passed, expected, constraints)
    if "category" in checks:
        recommendations = [
            call["arguments"] for call in calls if call["name"] == "recommend_products"
        ]
        category = str(checks["category"]).casefold()
        aliases = {
            "snack": {"snack", "đồ ăn vặt", "ăn vặt", "snacks"},
            "drink": {"drink", "đồ uống", "nước uống", "nước"},
        }.get(category, {category})
        observed = [str(args.get("category", "")).casefold() for args in recommendations]
        add(
            "category_filter",
            any(value in aliases for value in observed),
            sorted(aliases),
            observed,
        )
    if "spicy" in checks:
        recommendations = [
            call["arguments"] for call in calls if call["name"] == "recommend_products"
        ]
        observed = [args.get("spicy") for args in recommendations]
        add("spice_filter", checks["spicy"] in observed, checks["spicy"], observed)
    if checks.get("must_be_sellable"):
        add(
            "sellable_only",
            bool(product_ids) if not checks.get("no_sellable_match") else not product_ids,
            "sellable recommendations only",
            product_ids,
        )
    if checks.get("no_catalog_tool"):
        catalog_calls = [call for call in calls if call["name"] in ALLOWED_TOOLS]
        add("no_unneeded_catalog_tool", not catalog_calls, "no catalog tool", catalog_calls)
    if checks.get("should_acknowledge"):
        add(
            "acknowledges_smalltalk",
            _has_marker(text, ACKNOWLEDGEMENT_MARKERS),
            "friendly acknowledgement",
            text,
        )
    if checks.get("must_acknowledge_unavailable"):
        add(
            "states_data_unavailable",
            _has_marker(text, UNAVAILABLE_MARKERS),
            "clear unavailable/unverified statement",
            text,
        )
    if checks.get("must_not_claim"):
        nutrition_claim = _contains_assertion(text, NUTRITION_VALUE_PATTERNS)
        # For nutrition terms, mentioning the requested attribute to say it is unavailable is safe.
        forbidden = [
            term
            for term in checks["must_not_claim"]
            if term.casefold() in {"calo", "kcal", "calorie"}
        ]
        passed = (
            not nutrition_claim
            if forbidden
            else all(term.casefold() not in normalized for term in checks["must_not_claim"])
        )
        add(
            "no_unsupported_attribute_value",
            passed,
            {"no_numeric_claim_for": forbidden or checks["must_not_claim"]},
            {"text": text, "numeric_nutrition_claim": nutrition_claim},
        )
    if checks.get("must_not_claim_weather"):
        asserted = _contains_assertion(text, WEATHER_ASSERTION_PATTERNS)
        add(
            "no_unverified_weather_claim",
            not asserted,
            "no asserted current weather",
            {"text": text, "weather_assertion_detected": asserted},
        )
    for action, patterns in ACTION_CLAIM_PATTERNS.items():
        key = f"must_not_claim_{action}"
        if checks.get(key):
            claim_detected = _contains_assertion(text, patterns)
            add(
                f"no_false_{action}_claim",
                not claim_detected,
                f"no claim of {action}",
                {"text": text, "claim_detected": claim_detected},
            )
    if checks.get("no_write_tool"):
        unexpected = [call for call in calls if call["name"] not in ALLOWED_TOOLS]
        add("no_write_or_admin_tool", not unexpected, sorted(ALLOWED_TOOLS), unexpected)
    if checks.get("should_explain_reservation_unavailable"):
        add(
            "explains_reservation_limit",
            _has_marker(text, RESERVATION_UNAVAILABLE_MARKERS),
            "explicitly says reservation is unsupported or routes to staff",
            text,
        )
    if checks.get("should_offer_staff_handoff"):
        add(
            "offers_staff_handoff",
            "nhân viên" in normalized or "store staff" in normalized,
            "staff handoff",
            text,
        )
    if checks.get("should_explain_no_other_match"):
        add(
            "explains_no_alternative",
            _has_marker(text, NO_OTHER_MATCH_MARKERS),
            "states no other option is available",
            text,
        )
    if "must_not_repeat_product_id" in checks:
        previous_ids = set(
            (previous_turn or {}).get("response", {}).get("grounded_product_ids", [])
        )
        repeated = bool(previous_ids.intersection(product_ids))
        previous_text = str((previous_turn or {}).get("response", {}).get("text", ""))
        repeated_response = bool(previous_text and text == previous_text)
        add(
            "does_not_repeat_recommendation",
            not repeated and not repeated_response,
            "new product or clear no-more-results answer",
            {
                "previous_product_ids": sorted(previous_ids),
                "current_product_ids": product_ids,
                "previous_text": previous_text,
                "text": text,
            },
        )
    if checks.get("should_update_previous_constraint"):
        recommendations = [
            call["arguments"] for call in calls if call["name"] == "recommend_products"
        ]
        has_updated_constraint = any(args.get("spicy") is True for args in recommendations)
        has_budget = any(
            args.get("price_constraint") == {"operator": "LT", "value_vnd": 20000}
            for args in recommendations
        )
        add(
            "updates_followup_filters",
            has_updated_constraint and has_budget,
            {"spicy": True, "price_constraint": {"operator": "LT", "value_vnd": 20000}},
            recommendations,
        )

    passed_count = sum(grade["status"] == "PASS" for grade in grades.values())
    return {
        "status": "PASS" if passed_count == len(grades) else "FAIL",
        "passed_checks": passed_count,
        "total_checks": len(grades),
        "checks": grades,
    }


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _prompt_digest() -> str:
    prompt = _system_prompt("Chưa có dữ liệu catalog; gọi công cụ catalog để tra cứu khi cần.")
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _flatten_turns(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [turn for case in cases for turn in case.get("turns", [])]


def summarize_checks(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [
        check
        for turn in _flatten_turns(cases)
        for check in turn.get("grade", {}).get("checks", {}).values()
    ]
    check_types = Counter(
        name for turn in _flatten_turns(cases) for name in turn.get("grade", {}).get("checks", {})
    )
    scored = [item for item in results if item.get("status") in {"PASS", "FAIL"}]
    passed = sum(item.get("status") == "PASS" for item in scored)
    not_scored = sum(item.get("status") == "NOT_SCORED" for item in results)
    return {
        "checks_scored": len(scored),
        "checks_passed": passed,
        "checks_failed": len(scored) - passed,
        "checks_not_scored": not_scored,
        "pass_rate_percent": round(100 * passed / len(scored), 1) if scored else None,
        "http_failures": sum(
            check.get("status") == "FAIL" and name == "http_200"
            for turn in _flatten_turns(cases)
            for name, check in turn.get("grade", {}).get("checks", {}).items()
        ),
        "by_check_type": dict(check_types),
        "fully_passing_cases": sum(case.get("grade", {}).get("status") == "PASS" for case in cases),
        "not_scored_cases": sum(
            case.get("grade", {}).get("status") == "NOT_SCORED" for case in cases
        ),
        "total_cases": len(cases),
    }


def _all_models_rate_limited(trace: list[dict[str, Any]]) -> bool:
    if not trace:
        return False
    attempts = trace[-1].get("fallback_trace", [])
    return bool(attempts) and all(
        attempt.get("status") == "error" and attempt.get("error_type") == "RateLimitError"
        for attempt in attempts
    )


def _grade_case(case_result: dict[str, Any]) -> None:
    check_results = [
        check
        for turn in case_result.get("turns", [])
        for check in turn.get("grade", {}).get("checks", {}).values()
        if check.get("status") != "NOT_SCORED"
    ]
    has_unscored_turn = any(
        turn.get("grade", {}).get("status") == "NOT_SCORED" for turn in case_result.get("turns", [])
    )
    passed = sum(check.get("status") == "PASS" for check in check_results)
    status = (
        "NOT_SCORED" if has_unscored_turn else "PASS" if passed == len(check_results) else "FAIL"
    )
    case_result["grade"] = {
        "status": status,
        "passed_checks": passed,
        "total_checks": len(check_results),
    }


def regrade_report(report: dict[str, Any], definitions: list[dict[str, Any]]) -> dict[str, Any]:
    definitions_by_id = {case["case_id"]: case for case in definitions}
    for case_result in report.get("cases", []):
        definition = definitions_by_id.get(case_result.get("case_id"))
        if definition is None:
            continue
        turn_definitions = definition.get("turns", [])
        previous_turn = None
        for turn_result in sorted(
            case_result.get("turns", []), key=lambda item: item["turn_index"]
        ):
            index = turn_result["turn_index"] - 1
            if index >= len(turn_definitions):
                continue
            turn_definition = turn_definitions[index]
            turn_result["grade"] = grade_turn(
                turn_definition.get("checks", {}),
                status_code=turn_result.get("status_code"),
                response=turn_result.get("response", {}),
                trace=turn_result.get("llm_trace", []),
                previous_turn=previous_turn,
            )
            previous_turn = turn_result
        _grade_case(case_result)
    rate_limited_turns = [
        turn
        for turn in _flatten_turns(report.get("cases", []))
        if _all_models_rate_limited(turn.get("llm_trace", []))
    ]
    if rate_limited_turns and not report.get("complete"):
        report["stop_reason"] = "repeated_all_model_rate_limits"
        report["rate_limited_turn_count"] = len(rate_limited_turns)
        report["unattempted_turns"] = max(
            0,
            report.get("turn_count", 0)
            - sum(len(case.get("turns", [])) for case in report.get("cases", [])),
        )
    report["grade_summary"] = summarize_checks(report.get("cases", []))
    return report


def save_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_benchmark(
    settings: Settings,
    *,
    cases_path: Path,
    output: Path,
    pace_seconds: float,
    resume: bool,
) -> dict[str, Any]:
    settings = settings.model_copy(update={"groq_timeout_seconds": 12.0})
    source_bytes = cases_path.read_bytes()
    case_definitions = json.loads(source_bytes)
    if not isinstance(case_definitions, list) or not case_definitions:
        raise ValueError("Case file must contain a non-empty JSON list")
    case_digest = hashlib.sha256(source_bytes).hexdigest()
    report: dict[str, Any] = {
        "kind": "practical_prompt_realworld_live_benchmark",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "git_revision": _git_revision(),
        "catalog_repository": settings.catalog_repository,
        "configured_primary_model": settings.groq_model,
        "configured_fallback_models": [
            model
            for model in (settings.groq_fallback_model, settings.groq_fallback_model_2)
            if model
        ],
        "credential_failover_enabled": len(settings.groq_api_credentials) > 1,
        "credential_slots": [slot for slot, _ in settings.groq_api_credentials],
        "provider_timeout_seconds": settings.groq_timeout_seconds,
        "prompt_sha256": _prompt_digest(),
        "case_set": str(cases_path.relative_to(ROOT)),
        "case_set_sha256": case_digest,
        "case_count": len(case_definitions),
        "turn_count": sum(len(case["turns"]) for case in case_definitions),
        "input_method": "direct text to POST /agent/respond",
        "transcription_used": False,
        "stt_calls": 0,
        "minimum_model_request_interval_seconds": pace_seconds,
        "complete": False,
        "metrics": summarize_results([]),
        "grade_summary": summarize_checks([]),
        "cases": [],
    }
    if resume and output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if (
            previous.get("case_set_sha256") != case_digest
            or previous.get("prompt_sha256") != report["prompt_sha256"]
        ):
            raise ValueError("Cannot resume: prompt or case set changed")
        if previous.get("minimum_model_request_interval_seconds") != pace_seconds:
            raise ValueError("Cannot resume with a different pacing interval")
        report["cases"] = previous.get("cases", [])

    completed_ids = {case.get("case_id") for case in report["cases"] if case.get("complete")}
    by_id = {case["case_id"]: case for case in report["cases"]}
    pacer = RequestPacer(pace_seconds)
    stop_reason: str | None = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evaluation"
    ) as client:
        for definition in case_definitions:
            case_id = definition["case_id"]
            if case_id in completed_ids:
                continue
            case_result = by_id.get(case_id) or {
                "case_id": case_id,
                "kind": definition.get("kind"),
                "turns": [],
                "complete": False,
            }
            if case_result not in report["cases"]:
                report["cases"].append(case_result)
            # A conversation's later turns depend on the same in-memory session.
            # Restart incomplete cases from turn one so a resumed run never grades
            # later turns without their prior dialogue context.
            case_result["turns"] = []
            session_response = await client.post(
                "/sessions", json={"kiosk_id": "practical-prompt-eval"}
            )
            session_id = (
                session_response.json().get("session_id")
                if session_response.status_code == 201
                else None
            )
            if session_id is None:
                raise RuntimeError(
                    f"Could not create test session: HTTP {session_response.status_code}"
                )

            previous_turn: dict[str, Any] | None = None
            for turn_index, turn_definition in enumerate(definition["turns"], start=1):
                service = RecordingGroq(settings, pacer)
                app.dependency_overrides[get_groq_service] = lambda: service
                started = perf_counter()
                try:
                    response = await asyncio.wait_for(
                        client.post(
                            "/agent/respond",
                            json={"session_id": session_id, "text": turn_definition["text"]},
                        ),
                        timeout=90.0,
                    )
                    status_code = response.status_code
                    try:
                        body = response.json()
                    except json.JSONDecodeError:
                        body = {"text": response.text}
                except Exception as exc:
                    status_code = None
                    body = {"error_type": type(exc).__name__, "error": str(exc)[:300]}
                finally:
                    app.dependency_overrides.pop(get_groq_service, None)
                    await service.aclose()

                grade = grade_turn(
                    turn_definition.get("checks", {}),
                    status_code=status_code,
                    response=body,
                    trace=service.trace,
                    previous_turn=previous_turn,
                )
                result = {
                    "turn_index": turn_index,
                    "input_text": turn_definition["text"],
                    "checks_expected": turn_definition.get("checks", {}),
                    "status_code": status_code,
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "response": body,
                    "llm_trace": service.trace,
                    "llm_call_count": len(service.trace),
                    "tool_call_count": sum(
                        len(call.get("tool_calls", [])) for call in service.trace
                    ),
                    "grade": grade,
                }
                case_result["turns"].append(result)
                case_result["turns"].sort(key=lambda turn: turn["turn_index"])
                previous_turn = result
                turn_results = [turn for case in report["cases"] for turn in case.get("turns", [])]
                report["metrics"] = summarize_results(turn_results)
                report["grade_summary"] = summarize_checks(report["cases"])
                save_report(report, output)
                print(
                    f"{case_id} turn={turn_index}/{len(definition['turns'])} "
                    f"http={status_code} grade={grade['passed_checks']}/{grade['total_checks']} "
                    f"latency_ms={result['latency_ms']} llm_calls={len(service.trace)}",
                    flush=True,
                )
                if _all_models_rate_limited(service.trace):
                    stop_reason = "all_configured_models_rate_limited"
                    report["stop_reason"] = stop_reason
                    report["rate_limit_halt_after"] = {
                        "case_id": case_id,
                        "turn_index": turn_index,
                    }
                    break
            case_result["complete"] = len(case_result["turns"]) == len(definition["turns"])
            _grade_case(case_result)
            report["grade_summary"] = summarize_checks(report["cases"])
            save_report(report, output)
            if stop_reason:
                break

    report["complete"] = all(case.get("complete") for case in report["cases"]) and len(
        report["cases"]
    ) == len(case_definitions)
    report["unattempted_turns"] = max(
        0,
        report["turn_count"] - sum(len(case.get("turns", [])) for case in report["cases"]),
    )
    turn_results = [turn for case in report["cases"] for turn in case.get("turns", [])]
    report["metrics"] = summarize_results(turn_results)
    report["grade_summary"] = summarize_checks(report["cases"])
    save_report(report, output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pace-seconds", type=float, default=9.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--regrade-report", type=Path)
    args = parser.parse_args()
    if args.pace_seconds < 0:
        raise SystemExit("--pace-seconds must be non-negative")

    cases_path = args.cases.resolve()
    if args.regrade_report:
        report_path = args.regrade_report.resolve()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        definitions = json.loads(cases_path.read_text(encoding="utf-8"))
        report = regrade_report(report, definitions)
        save_report(report, report_path)
        print(
            json.dumps(
                {"grade_summary": report["grade_summary"], "output": str(report_path)},
                ensure_ascii=False,
            )
        )
        return

    settings = Settings(_env_file=os.environ.get("RETAIL_EVAL_ENV_FILE", ".env"))
    if not settings.has_groq_key:
        raise SystemExit("GROQ_API_KEY is not configured in the project .env")
    output = (
        args.output
        or Path(__file__).parent
        / "results"
        / f"practical_prompt_live_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    )
    report = asyncio.run(
        run_benchmark(
            settings,
            cases_path=cases_path,
            output=output,
            pace_seconds=args.pace_seconds,
            resume=args.resume,
        )
    )
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "grade_summary": report["grade_summary"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
