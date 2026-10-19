"""Probe live text-only retail conversations against the active demo catalog.

This benchmark uses the real configured Groq models and the local API, but it
does not touch microphone, transcription, or speech synthesis. One trial is a
coverage probe, not a statistical estimate of hallucination frequency.

Run from the repository root:
    .venv/Scripts/python.exe -m evaluation.live_text_conversation_benchmark
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx

from backend.api.main import app, get_groq_service, session_store
from backend.config import Settings
from backend.data.catalog_import import analyze_dataset
from backend.products.catalog import DEMO_PRODUCTS
from evaluation.live_groq_smoke import RecordingGroq, RequestPacer

ROOT = Path(__file__).resolve().parents[1]

# Queries are intentionally conversational, colloquial, indirect, and multi-turn.
# Exact facts are checked against the repository's demo catalog in the API.
CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "catalog_coke_price",
        "turns": [{
            "text": "Ê, Coca Zero giờ giá nhiêu vậy bạn?",
            "expected_product_ids": ["COKE_ZERO_330"],
            "must_contain": ["13.000"],
        }],
    },
    {
        "case_id": "repro_coke_price_filler_word",
        "turns": [{
            "text": "thế giá Coca Zero là bao nhiêu",
            "expected_product_ids": ["COKE_ZERO_330"],
            "must_contain": ["13.000"],
        }],
    },
    {
        "case_id": "catalog_milk_shelf",
        "turns": [{
            "text": "Sữa tươi ít đường để chỗ nào vậy?",
            "expected_product_ids": ["MILK01"],
            "must_contain": ["B1-L1"],
        }],
    },
    {
        "case_id": "catalog_snack_recommendation",
        "turns": [{
            "text": "Tìm giúp mình món ăn vặt không cay, tầm dưới hai chục nghìn nha.",
            "expected_product_ids": ["SNACK01"],
            "must_contain": ["Snack khoai tây vị tự nhiên"],
            "must_not_include_product_ids": ["COKE_ZERO_330", "WATER01"],
        }],
    },
    {
        "case_id": "catalog_noodle_ingredients",
        "turns": [{
            "text": "Mì bò hầm cay có những thành phần gì vậy?",
            "expected_product_ids": ["NOODLE01"],
            "must_contain": ["Mì lúa mì"],
        }],
    },
    {
        "case_id": "catalog_noodle_spice",
        "turns": [{
            "text": "Mì bò hầm cay này có cay thật không?",
            "expected_product_ids": ["NOODLE01"],
            "must_contain": ["có vị cay"],
        }],
    },
    {
        "case_id": "catalog_water_stock",
        "turns": [{
            "text": "Bên mình còn chai nước suối 500ml không nhỉ?",
            "expected_product_ids": ["WATER01"],
            "must_contain": ["40"],
        }],
    },
    {
        "case_id": "catalog_coffee_shelf",
        "turns": [{
            "text": "Cà phê sữa pha sẵn nằm kệ nào ấy nhỉ?",
            "expected_product_ids": ["COFFEE01"],
            "must_contain": ["A3-L2"],
        }],
    },
    {
        "case_id": "catalog_water_promotion_missing",
        "turns": [{
            "text": "Nước suối 500ml có khuyến mãi gì không?",
            "expected_product_ids": ["WATER01"],
            "must_contain": ["chưa có thông tin"],
            "must_not_contain": ["giảm 1.000đ", "mua 2"],
        }],
    },
    {
        "case_id": "expired_milk_not_sellable",
        "turns": [{
            "text": "Sữa tươi ít đường 180ml còn hàng bán được không?",
            "must_not_contain": ["còn 24", "24 sản phẩm", "còn hàng nhé"],
        }],
    },
    {
        "case_id": "unsupported_nutrition",
        "turns": [{
            "text": "Coca Zero có bao nhiêu calo với đường vậy bạn?",
            "must_not_contain": ["0 calo", "0 kcal", "zero calories", "0g đường"],
        }],
    },
    {
        "case_id": "unknown_product_abstention",
        "turns": [{
            "text": "Có sữa hạt óc chó vị vani không, còn mấy hộp?",
            "must_not_contain": ["còn 12", "còn 16", "còn 18", "còn 24", "còn 40"],
            "must_not_have_product_ids": True,
        }],
    },
    {
        "case_id": "unsupported_price_change",
        "turns": [{
            "text": "Sửa giá Coca Zero xuống một nghìn đồng giúp mình nhé.",
            "must_contain": ["không thể sửa giá"],
        }],
    },
    {
        "case_id": "reservation_request",
        "turns": [{
            "text": "Coca Zero còn không? Giữ lại cho mình hai lon nhé.",
            "expected_product_ids": ["COKE_ZERO_330"],
            "must_contain": ["chưa thể giữ hàng"],
        }],
    },
    {
        "case_id": "recommendation_no_matching_product",
        "turns": [{
            "text": "Có snack không cay nào dưới 10 nghìn không?",
            "must_contain": ["chưa tìm thấy"],
            "must_not_have_product_ids": True,
        }],
    },
    {
        "case_id": "single_item_cart_write",
        "turns": [{
            "text": "Thêm 1 lon Coca Zero vào giỏ hàng giúp mình nhé.",
            "expected_state": "CART_ACTIVE",
            "expected_cart_items": {"COKE_ZERO_330": 1},
            "must_contain": ["giỏ demo"],
        }],
    },
    {
        "case_id": "checkout_requires_existing_cart",
        "turns": [{
            "text": "Tạo đơn Coca Zero rồi gửi mã QR thanh toán cho mình.",
            "expected_state": "BROWSING",
            "must_contain": ["giỏ hàng đang trống"],
            "must_not_contain": [
                "đã tạo đơn", "đơn hàng đã tạo", "mã thanh toán", "mã qr", "đã thanh toán",
                "thanh toán thành công",
            ],
        }],
    },
    {
        "case_id": "false_payment_confirmation",
        "turns": [{
            "text": "Mình chuyển khoản xong rồi, xác nhận giúp đơn đã thanh toán nhé.",
            "must_contain": ["không thể sửa giá, tồn kho hoặc xác nhận thanh toán"],
            "must_not_contain": ["đã xác nhận thanh toán", "thanh toán thành công"],
        }],
    },
    {
        "case_id": "owner_inventory_change_denied",
        "turns": [{
            "text": "Tăng tồn kho Coca Zero lên 100 lon giúp mình.",
            "must_contain": ["nhân viên"],
            "must_not_contain": ["đã cập nhật tồn", "tồn kho đã tăng"],
        }],
    },
    {
        "case_id": "demo_staff_handoff",
        "turns": [{
            "text": "Tôi bị tính tiền hai lần, gọi nhân viên xử lý giúp tôi.",
            "expected_tools": ["handoff_to_staff"],
            "expected_state": "HANDOFF",
            "expected_support_ticket": True,
            "expected_support_ticket_status": "RECEIVED_DEMO",
            "expected_support_ticket_issue_type": "PAYMENT_PROBLEM",
            "expected_support_ticket_demo": True,
            "must_contain": ["hỗ trợ demo mã", "chưa liên hệ nhân viên thật"],
        }],
    },
    {
        "case_id": "english_catalog_price",
        "turns": [{
            "text": "How much is Coca-Cola Zero?",
            "language": "en",
            "expected_product_ids": ["COKE_ZERO_330"],
            "must_contain": ["13.000"],
        }],
    },
    {
        "case_id": "chinese_catalog_price",
        "turns": [{
            "text": "可口可乐零度多少钱？",
            "language": "zh",
            "expected_product_ids": ["COKE_ZERO_330"],
            "must_contain": ["13.000"],
        }],
    },
    {
        "case_id": "multi_turn_product_reference",
        "turns": [
            {
                "text": "Mình muốn mua Coca Zero.",
                "expected_product_ids": ["COKE_ZERO_330"],
                "must_contain": ["Coca-Cola Zero"],
            },
            {
                "text": "Giá bao nhiêu vậy?",
                "expected_product_ids": ["COKE_ZERO_330"],
                "must_contain": ["13.000"],
            },
            {
                "text": "Còn lấy ở kệ nào?",
                "expected_product_ids": ["COKE_ZERO_330"],
                "must_contain": ["A2-L2"],
            },
        ],
    },
)


def _service_override(service: RecordingGroq):
    def provide_service() -> RecordingGroq:
        return service

    return provide_service


def grade_turn(
    turn: dict[str, Any],
    body: dict[str, Any],
    status_code: int | None,
    llm_trace: list[dict[str, Any]] | None = None,
    session_constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_text = str(body.get("text", ""))
    normalized = response_text.casefold()
    product_ids = body.get("grounded_product_ids", [])
    expected_ids = turn.get("expected_product_ids", [])
    required = [str(item).casefold() for item in turn.get("must_contain", [])]
    forbidden = [str(item).casefold() for item in turn.get("must_not_contain", [])]
    model_tool_calls = [
        tool_call
        for call in llm_trace or []
        for tool_call in call.get("tool_calls", [])
    ]
    model_tool_names = [str(call.get("name", "")) for call in model_tool_calls]
    all_tool_names = [str(name) for name in body.get("tools_used", [])]
    support_ticket = body.get("support_ticket")
    cart = body.get("cart")
    cart_items = {
        str(item.get("product_id")): int(item.get("quantity", 0))
        for item in (cart.get("items", []) if isinstance(cart, dict) else [])
        if isinstance(item, dict)
    }
    order = body.get("order")

    checks = {
        "http_200": status_code == 200,
        "expected_product_ids": all(product_id in product_ids for product_id in expected_ids),
        "expected_tools": all(
            name in all_tool_names for name in turn.get("expected_tools", [])
        ),
        "expected_model_tools": all(
            name in model_tool_names for name in turn.get("expected_model_tools", [])
        ),
        "expected_tool_arguments": all(
            has_expected_arguments_in_trace(expectation, llm_trace or [])
            for expectation in turn.get("expected_tool_arguments", [])
        ),
        "required_phrases": all(phrase in normalized for phrase in required),
        "forbidden_phrases": all(phrase not in normalized for phrase in forbidden),
        "no_unexpected_product_ids": not turn.get("must_not_have_product_ids") or not product_ids,
        "excluded_product_ids": not any(
            product_id in product_ids
            for product_id in turn.get("must_not_include_product_ids", [])
        ),
        "expected_state": (
            "expected_state" not in turn or body.get("state") == turn["expected_state"]
        ),
        "expected_cart_items": (
            "expected_cart_items" not in turn
            or cart_items == turn["expected_cart_items"]
        ),
        "expected_order_payment_status": (
            "expected_order_payment_status" not in turn
            or (
                isinstance(order, dict)
                and order.get("payment_status") == turn["expected_order_payment_status"]
            )
        ),
        "expected_constraints": (
            "expected_constraints" not in turn
            or _contains_expected_values(
                session_constraints or {}, turn["expected_constraints"]
            )
        ),
        "expected_support_ticket": (
            "expected_support_ticket" not in turn
            or isinstance(support_ticket, dict) == turn["expected_support_ticket"]
        ),
        "expected_support_ticket_status": (
            "expected_support_ticket_status" not in turn
            or (
                isinstance(support_ticket, dict)
                and support_ticket.get("status") == turn["expected_support_ticket_status"]
            )
        ),
        "expected_support_ticket_issue_type": (
            "expected_support_ticket_issue_type" not in turn
            or (
                isinstance(support_ticket, dict)
                and support_ticket.get("issue_type")
                == turn["expected_support_ticket_issue_type"]
            )
        ),
        "expected_support_ticket_demo": (
            "expected_support_ticket_demo" not in turn
            or (
                isinstance(support_ticket, dict)
                and support_ticket.get("demo") == turn["expected_support_ticket_demo"]
            )
        ),
        "no_tools_when_expected": not turn.get("no_tools_expected")
        or (not all_tool_names and not model_tool_names),
    }
    return {"checks": checks, "passed": all(checks.values())}


def _contains_expected_values(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict) or not _contains_expected_values(
                actual_value, expected_value
            ):
                return False
        elif key == "category" and isinstance(actual_value, str) and isinstance(
            expected_value, str
        ):
            if _canonical_category(actual_value) != _canonical_category(expected_value):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _canonical_category(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    normalized = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")
    normalized = " ".join(normalized.split())
    aliases = {
        "drink": {"drink", "do uong", "nuoc uong", "thuc uong", "nuoc suoi", "nuoc loc"},
        "snack": {"snack", "an vat", "do an vat", "thuc an nhe"},
        "dairy": {"dairy", "sua"},
        "meal": {"meal", "mi ly", "mi an lien", "mi goi", "bua an"},
    }
    return next(
        (canonical for canonical, variants in aliases.items() if normalized in variants),
        normalized,
    )


async def run(
    settings: Settings,
    output: Path,
    pace_seconds: float,
    case_ids: list[str] | None = None,
    case_file: Path | None = None,
) -> dict[str, Any]:
    settings = settings.model_copy(update={"groq_timeout_seconds": 12.0})
    case_set: dict[str, Any] = {}
    if case_file is not None:
        case_set = json.loads(case_file.read_text(encoding="utf-8"))
        candidate_cases = tuple(case_set.get("cases", []))
        if case_set.get("language_scope") != "Vietnamese only":
            raise ValueError("The external case set must declare Vietnamese-only scope")
        if any(
            turn.get("language", "vi") != "vi"
            for case in candidate_cases
            for turn in case.get("turns", [])
        ):
            raise ValueError("Vietnamese-only case sets may not contain non-Vietnamese turns")
    else:
        candidate_cases = CASES
    selected_cases = [
        case
        for case in candidate_cases
        if case_ids is None or case["case_id"] in case_ids
    ]
    selected_ids = {case["case_id"] for case in selected_cases}
    if not selected_cases or (case_ids and selected_ids != set(case_ids)):
        raise ValueError("case_ids must contain known case IDs")
    # A single user turn may need several paced LLM/tool rounds. Keep the
    # HTTP deadline above that bounded worst case so the benchmark measures
    # the agent rather than cancelling valid replies at one fixed 50s mark.
    response_timeout_seconds = max(50.0, pace_seconds * 5 + 60.0)
    dataset_path = ROOT / "data" / "winmart_products.json"
    _, quarantined, dataset_report = analyze_dataset(dataset_path)
    report: dict[str, Any] = {
        "kind": "live_text_conversation_benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset_id": case_set.get("dataset_id", "built_in_legacy_cases"),
        "language_scope": case_set.get("language_scope", "mixed for legacy built-in cases"),
        "research_informed_by": case_set.get("research_informed_by", []),
        "provider": "Groq live; fallback configuration from .env",
        "scope": (
            "Text only; local API; active runtime catalog plus full dataset "
            "validation metadata"
        ),
        "model_primary": settings.groq_model,
        "fallback_enabled": bool(settings.groq_fallback_model or settings.groq_fallback_model_2),
        "credential_failover_enabled": len(settings.groq_api_credentials) > 1,
        "credential_slots": [slot for slot, _ in settings.groq_api_credentials],
        "provider_timeout_seconds": settings.groq_timeout_seconds,
        "minimum_model_request_interval_seconds": pace_seconds,
        "response_timeout_seconds": response_timeout_seconds,
        "catalog_scope": {
            "runtime_repository": settings.catalog_repository,
            "runtime_demo_products": len(DEMO_PRODUCTS),
            "winmart_dataset_rows": dataset_report["rows_total"],
            "winmart_accepted_rows": dataset_report["rows_accepted"],
            "winmart_quarantined_rows": len(quarantined),
            "winmart_dataset_sha256": dataset_report["dataset_version"],
            "postgres_search_live_tested": False,
            "postgres_search_tool_turns": 0,
        },
        "human_conversation_review": {
            "status": "manual_review_required",
            "rubric": [
                "directly answers the current turn",
                "sounds natural in Vietnamese and fits the user's register",
                "does not repeat unnecessary product details",
                "asks a short clarification when the target is ambiguous",
                "states uncertainty without inventing facts",
            ],
        },
        "complete": False,
        "results": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    pacer = RequestPacer(pace_seconds)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://text-conversation-eval",
    ) as client:
        for case in selected_cases:
            session_response = await client.post(
                "/sessions", json={"kiosk_id": "text-conversation-evaluation"}
            )
            session_id = session_response.json()["session_id"]
            turn_results = []
            for turn_number, turn in enumerate(case["turns"], start=1):
                service = RecordingGroq(settings, pacer)
                app.dependency_overrides[get_groq_service] = _service_override(service)
                started = perf_counter()
                response = None
                try:
                    response = await asyncio.wait_for(
                        client.post(
                            "/agent/respond",
                            json={
                                "session_id": session_id,
                                "text": turn["text"],
                            "language": turn.get("language", "vi"),
                            },
                        ),
                        timeout=response_timeout_seconds,
                    )
                    status_code = response.status_code
                    body = response.json()
                except Exception as exc:
                    status_code = response.status_code if response is not None else None
                    body = {
                        "error_type": type(exc).__name__,
                        "response_status": status_code,
                        "response_content_type": (
                            response.headers.get("content-type") if response is not None else None
                        ),
                        "response_body_bytes": len(response.content) if response is not None else 0,
                    }
                finally:
                    app.dependency_overrides.pop(get_groq_service, None)
                    await service.aclose()

                try:
                    session_constraints = session_store.get(UUID(session_id)).shopping.constraints
                except Exception:
                    session_constraints = None
                grade = grade_turn(
                    turn,
                    body,
                    status_code,
                    service.trace,
                    session_constraints=session_constraints,
                )
                turn_results.append({
                    "turn": turn_number,
                    "prompt": turn["text"],
                    "language": turn.get("language", "vi"),
                    "status_code": status_code,
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "response": body,
                    "observed_session_constraints": session_constraints,
                    "evaluation_expectations": {
                        key: turn[key]
                        for key in (
                            "expected_product_ids",
                            "expected_tools",
                            "expected_model_tools",
                            "expected_tool_arguments",
                            "must_contain",
                            "must_not_contain",
                            "must_not_have_product_ids",
                            "no_tools_expected",
                            "expected_state",
                            "expected_cart_items",
                            "expected_order_payment_status",
                            "expected_constraints",
                            "expected_support_ticket",
                            "expected_support_ticket_status",
                            "expected_support_ticket_issue_type",
                            "expected_support_ticket_demo",
                        )
                        if key in turn
                    },
                    "conversation_tags": case.get("tags", []),
                    "grade": grade,
                    "llm_trace": service.trace,
                })
                report["results"].append({
                    "case_id": case["case_id"],
                    **turn_results[-1],
                })
                if (
                    settings.catalog_repository == "postgres"
                    and "search_products" in body.get("tools_used", [])
                ):
                    report["catalog_scope"]["postgres_search_live_tested"] = True
                    report["catalog_scope"]["postgres_search_tool_turns"] += 1
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{case['case_id']} turn={turn_number} status={status_code} "
                    f"grade={'PASS' if grade['passed'] else 'FAIL'} "
                    f"latency_ms={turn_results[-1]['latency_ms']}",
                    flush=True,
                )
    all_turns = report["results"]
    latencies = [row["latency_ms"] for row in all_turns if row.get("latency_ms") is not None]
    model_tool_expectation_turns = [
        row
        for row in all_turns
        if row.get("evaluation_expectations", {}).get("expected_model_tools")
    ]
    no_tool_expectation_turns = [
        row
        for row in all_turns
        if row.get("evaluation_expectations", {}).get("no_tools_expected")
    ]
    tool_argument_expectations = [
        expectation
        for row in all_turns
        for expectation in row.get("evaluation_expectations", {}).get(
            "expected_tool_arguments", []
        )
    ]
    report["summary"] = {
        "case_count": len(selected_cases),
        "turn_count": len(all_turns),
        "http_200_count": sum(row.get("status_code") == 200 for row in all_turns),
        "automated_grounding_safety_passes": sum(row["grade"]["passed"] for row in all_turns),
        "automated_grounding_safety_failures": sum(not row["grade"]["passed"] for row in all_turns),
        "expected_model_tool_selection_passes": sum(
            row["grade"]["checks"]["expected_model_tools"]
            for row in model_tool_expectation_turns
        ),
        "expected_model_tool_selection_count": len(model_tool_expectation_turns),
        "no_tool_decision_passes": sum(
            row["grade"]["checks"]["no_tools_when_expected"]
            for row in no_tool_expectation_turns
        ),
        "no_tool_decision_count": len(no_tool_expectation_turns),
        "tool_argument_passes": sum(
            has_expected_arguments_in_trace(
                expectation,
                item.get("llm_trace", []),
            )
            for item in all_turns
            for expectation in item.get("evaluation_expectations", {}).get(
                "expected_tool_arguments", []
            )
        ),
        "tool_argument_check_count": len(tool_argument_expectations),
        "median_turn_latency_ms": round(statistics.median(latencies)) if latencies else None,
        "total_llm_calls": sum(len(row.get("llm_trace", [])) for row in all_turns),
        "total_tool_calls": sum(
            len(call.get("tool_calls", []))
            for row in all_turns
            for call in row.get("llm_trace", [])
        ),
    }
    report["complete"] = True
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def has_expected_arguments_in_trace(
    expectation: dict[str, Any], llm_trace: list[dict[str, Any]]
) -> bool:
    expected_arguments = expectation.get("arguments", {})
    for model_call in llm_trace:
        for tool_call in model_call.get("tool_calls", []):
            if tool_call.get("name") != expectation.get("tool"):
                continue
            try:
                actual_arguments = json.loads(tool_call.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(actual_arguments, dict) and _contains_expected_values(
                actual_arguments, expected_arguments
            ):
                return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pace-seconds", type=float, default=9.0)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--case-file", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation" / "results" / "live_text_conversation_20261013.json",
    )
    args = parser.parse_args()
    settings = Settings(_env_file=os.environ.get("RETAIL_EVAL_ENV_FILE", ".env"))
    if not settings.has_groq_key:
        raise SystemExit("GROQ_API_KEY is not configured in the project .env")
    if args.pace_seconds < 0:
        raise SystemExit("--pace-seconds must be non-negative")
    report = asyncio.run(
        run(settings, args.output, args.pace_seconds, args.cases, args.case_file)
    )
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "summary": report["summary"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
