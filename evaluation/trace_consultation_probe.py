"""Read-only consultation trace using catalog/settings semantics; no secret output."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services import customer_consultation as consultation
from backend.services.groq import GroqService
from backend.services.sessions import InMemorySessionStore
from evaluation.diagnose_live_advice_validation import (
    SOURCE,
    known_messages,
    provider_schema_diagnostic,
    safe_failure,
)

ROOT = Path(__file__).resolve().parents[1]

COMPACT_SELECTION_POLICY = """Bạn là nhân viên siêu thị nói tiếng Việt, xưng mình/bạn.
Khách nhờ chọn thì chọn giúp và nói thẳng lý do trong 2-3 câu. Giữ đúng mục đích,
ngân sách, đồ đã có, điều đã loại trừ và thay đổi ở lượt hiện tại. Đừng kể quá
trình tìm kiếm hay thông tin nội bộ. Đừng hỏi lại điều đã rõ hoặc xin phép tư vấn.

Chọn từ candidates có đúng chức năng khách cần. So sánh thay thế thì mỗi món
một đơn vị; giỏ mua cùng phải đúng vai trò, số lượng và tổng tiền. purchase_budget
là phép tính của ứng dụng, không chứng minh khẩu phần hay công dụng. Chỉ chọn
phần đáp ứng được nếu thiếu món, chỉ rõ phần thiếu; đừng gọi một phần là đủ cả giỏ.
Thiếu mốc so sánh hoặc thông tin thiết yếu thì đưa một hướng hữu ích và hỏi một câu.

Lý do dựa trên dạng hàng, quy cách, giá hoặc evidence thật. Dùng kiến thức đời
sống để đề xuất khách chuẩn bị/phối hợp đồ thông thường; đừng nói SKU đã có đặc
tính không được ghi nhận. Không hứa cảm giác, thành phần, sức khỏe, an toàn,
hiệu năng hơn hàng khác, dùng được bao lâu hay đủ ăn/dùng cho bao nhiêu người.
Hướng dẫn đặc biệt phải có general_guidance_sources; nguồn chung không bảo chứng
SKU. Không chế thêm bước, thời gian hay cách pha/nấu để hợp thiết bị khách có.
Một chai khác lốc nhiều chai; khối lượng không chứng minh kích thước bên ngoài.
Món bị bác ở nháp chưa phải món khách đã xem. Nếu sửa, giải quyết lỗi đã chỉ rõ,
giữ phần đúng và đừng thêm lời hứa khác. Tuân selection_contract và schema.
"""


def cooldown_snapshot(service: GroqService) -> list[dict]:
    """Carry Retry-After deadlines into later probes without storing credentials."""
    monotonic_now = perf_counter()
    wall_now = datetime.now(UTC)
    return [
        {"model": model, "slot": slot,
         "conservative_not_before_utc": (
             wall_now + timedelta(seconds=deadline - monotonic_now)
         ).isoformat()}
        for (model, slot), deadline in sorted(service._chat_cooldowns.items())
        if deadline > monotonic_now
    ]


def source_hashes() -> dict:
    hashes = {}
    for filename in (
        "backend/services/customer_consultation.py",
        "backend/services/consultation_repair.py",
        "backend/services/consultation_verification.py",
        "backend/services/groq.py",
        "backend/config.py",
        "backend/services/shopping_guidance.py",
        "data/shopping_guidance.json",
        "backend/services/product_form.py",
        "backend/services/product_packaging.py",
        "backend/services/purchase_budget.py",
        "backend/services/advice_claims.py",
        "backend/services/speech_upload.py",
        "backend/services/product_constraints.py",
        "backend/services/consultation_policy.py",
        "backend/services/consultation_text.py",
        "backend/api/main.py",
        "backend/products/knowledge.py",
        "data/product_knowledge_enrichment_pilot.json",
        "data/thanh_lam_products.json",
        "scripts/start_demo.ps1",
    ):
        hashes[filename] = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
    for name in ("POLICY", "SELECTION_POLICY", "REVIEW", "FORM_EVIDENCE_POLICY"):
        value = getattr(consultation, name, None)
        if isinstance(value, str):
            hashes[name] = hashlib.sha256(value.encode()).hexdigest()
    return hashes


def safe_error(exc: Exception) -> dict:
    cause = exc.__cause__ or exc
    body = getattr(cause, "body", {}) or {}
    return {
        "error_type": type(exc).__name__,
        "trace": getattr(exc, "fallback_trace", []),
        "provider_error_flags": {
            flag: flag in str(body).casefold()
            for flag in (
                "json_validate_failed",
                "context_length",
                "reasoning",
                "max_tokens",
                "daily",
                "tokens per minute",
                "tokens per day",
                "requests per day",
                "response_format",
                "schema",
            )
        },
    }


def remember_published_turn(record, row):
    """Match API retention: a no-product reply does not erase the visible page."""
    if "text" not in row or "memory" not in row:
        return
    record.shopping.consultation = row["memory"]
    if row.get("products"):
        record.shopping.selected_product_ids = [p["id"] for p in row["products"]]
    record.messages.extend([
        {"role": "user", "content": row["query"]},
        {"role": "assistant", "content": row["text"]},
    ])


def probe_state(record):
    return {"messages": record.messages, "consultation": record.shopping.consultation,
            "selected_product_ids": record.shopping.selected_product_ids}


def restore_probe_state(record, document):
    state = document.get("final_state")
    if state is not None:
        record.messages = state["messages"]
        record.shopping.consultation = state["consultation"]
        record.shopping.selected_product_ids = state["selected_product_ids"]
    else:
        for prior in document.get("rows", []):
            remember_published_turn(record, prior)


async def run(args):
    resumed = json.loads(args.resume_from.read_text(encoding="utf-8")) \
        if getattr(args, "resume_from", None) else None
    overrides = (
        {}
        if args.config == "settings"
        else {
            "app_env": "development",
            "demo_sales_enabled": True,
            "customer_consultation_enabled": True,
            "customer_consultation_review_enabled": True,
            "customer_consultation_local_fallback_enabled": True,
        }
    )
    settings = Settings(**overrides)
    if getattr(args, "model", None):
        settings = settings.model_copy(update={
            "groq_model": args.model,
            "groq_fallback_model": "qwen/qwen3.8-27b",
        })
    if args.reasoning_effort is not None:
        settings = settings.model_copy(
            update={
                "groq_structured_reasoning_effort": args.reasoning_effort,
            }
        )
    repository = get_product_repository(settings)
    service = GroqService(settings)
    if getattr(args, "compact_selection_policy", False):
        consultation.SELECTION_POLICY = COMPACT_SELECTION_POLICY
    seeded_cooldowns = []
    cooldown_documents = ([resumed] if resumed else [])
    if args.respect_cooldowns:
        cooldown_documents.append(json.loads(args.respect_cooldowns.read_text(encoding="utf-8")))
    for cooldown_document in cooldown_documents:
        now = datetime.now(UTC)
        for entry in cooldown_document.get("cooldowns", []):
            until = datetime.fromisoformat(entry["conservative_not_before_utc"])
            if until.tzinfo is None:
                raise ValueError("Cooldown not-before timestamp must include a UTC offset")
            remaining = (until - now).total_seconds()
            if remaining > 0:
                key = (entry["model"], entry["slot"])
                service._chat_cooldowns[key] = max(
                    service._chat_cooldowns.get(key, 0),
                    perf_counter() + remaining,
                )
                seeded_cooldowns.append(
                    {"model": key[0], "slot": key[1], "not_before": until.isoformat()}
                )
    report = {
        "kind": "read-only consultation development trace",
        "created_at": datetime.now(UTC).isoformat(),
        "config": {
            "mode": args.config,
            "review_selection": settings.customer_consultation_review_enabled,
            "local_fallback": settings.customer_consultation_local_fallback_enabled,
            "models": [
                settings.groq_model,
                settings.groq_fallback_model,
                settings.groq_fallback_model_2,
            ],
            "reasoning": settings.groq_structured_reasoning_effort,
            "selection_policy": "compact-experiment"
            if getattr(args, "compact_selection_policy", False) else "production",
        },
        "source_hashes_before": source_hashes(),
        "seeded_cooldowns": seeded_cooldowns,
        "resumed_from": str(args.resume_from) if resumed else None,
        "pause_seconds": args.pause_seconds,
        "python_exception_tracing": getattr(args, "trace_local_errors", False),
        "rows": [],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    label = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.label)[:70]
    output = args.output or ROOT / f"docs/reports/artifacts/consultation_trace_{label}_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the requested path before any provider call; never replace a baseline.
    with output.open("x", encoding="utf-8") as artifact:
        artifact.write("{}")
    record = InMemorySessionStore().create("trace")
    if resumed:
        restore_probe_state(record, resumed)
        report["resumed_turn_count"] = len(record.messages) // 2
    report["state_replay_version"] = 2
    report["initial_state"] = json.loads(json.dumps(probe_state(record), ensure_ascii=False))
    active_calls = []
    active_events = []
    known = known_messages()
    original_client = service._client_for_credential
    wrapped_slots = set()
    active_rate_limits = []
    traced_filename = str(SOURCE.resolve())

    def diagnostic_client(slot, api_key):
        client = original_client(slot, api_key)
        if slot not in wrapped_slots:
            endpoint = client.chat.completions.with_raw_response
            create = endpoint.create

            async def diagnostic_create(**request):
                try:
                    return await create(**request)
                except Exception as exc:
                    diagnostic = provider_schema_diagnostic(exc, request, slot)
                    if diagnostic is not None:
                        report.setdefault("provider_schema_failures", []).append(diagnostic)
                    status = getattr(exc, "status_code", None)
                    if status == 429:
                        # Keep this even if a later request is cancelled by the
                        # consultation deadline and loses its fallback trace.
                        active_rate_limits.append({"model": request.get("model"), "slot": slot})
                    raise

            endpoint.create = diagnostic_create
            wrapped_slots.add(slot)
        return client

    service._client_for_credential = diagnostic_client

    def exception_trace(frame, event, arg):
        # Only trace lines/exceptions in the application file. Resolving paths
        # for every SDK/asyncio exception materially distorts the deadline probe.
        if event == "call":
            return exception_trace if frame.f_code.co_filename == traced_filename else None
        if event == "exception":
            _, exc, _ = arg
            if isinstance(exc, ValueError) and len(active_events) < 200:
                detail = safe_failure(exc, known)
                detail.update(
                    function=frame.f_code.co_name,
                    line=frame.f_lineno,
                    stage_calls_so_far=len(active_calls),
                )
                active_events.append(detail)
        return exception_trace

    original = service.structured_chat

    # Capture caught application-boundary failures without tracing every Python
    # frame in the SDK, JSON parser and event loop. Keep actual stage outputs in
    # the existing authored-probe capture; exception metadata stays bounded.
    boundary_originals = {}
    for function_name in ("_structured", "_render_selection", "_render"):
        function = getattr(consultation, function_name)
        boundary_originals[function_name] = function
        if asyncio.iscoroutinefunction(function):
            def async_boundary(wrapped, name):
                async def capture(*values, **options):
                    try:
                        return await wrapped(*values, **options)
                    except ValueError as exc:
                        detail = safe_failure(exc, known)
                        detail.update(function=name, stage_calls_so_far=len(active_calls))
                        active_events.append(detail)
                        raise
                return capture
            setattr(consultation, function_name, async_boundary(function, function_name))
        else:
            def sync_boundary(wrapped, name):
                def capture(*values, **options):
                    try:
                        return wrapped(*values, **options)
                    except ValueError as exc:
                        detail = safe_failure(exc, known)
                        detail.update(function=name, stage_calls_so_far=len(active_calls))
                        active_events.append(detail)
                        raise
                return capture
            setattr(consultation, function_name, sync_boundary(function, function_name))

    async def trace(messages, **kwargs):
        row = {
            "stage": kwargs.get("name"),
            "context": messages,
            "schema": kwargs.get("schema"),
            "max_tokens": kwargs.get("max_tokens"),
        }
        started = perf_counter()
        try:
            result = await original(messages, **kwargs)
            row.update(
                model=result.model,
                raw_result=str(result),
                usage=result.usage,
                finish_reason=result.finish_reason,
                trace=result.fallback_trace,
            )
            try:
                row["decision"] = json.loads(str(result))
            except ValueError:
                row["result_is_json"] = False
            return result
        except Exception as exc:
            row.update(safe_error(exc))
            raise
        finally:
            row["latency_seconds"] = round(perf_counter() - started, 3)
            active_calls.append(row)

    service.structured_chat = trace
    queries = args.query or [
        "Tối nay ăn lẩu, mình muốn mua thịt để nhúng.",
        "Ưu tiên bò thái lát, khoảng hai trăm nghìn thôi.",
    ]
    for index, query in enumerate(queries):
        if index:
            await asyncio.sleep(args.pause_seconds)
        active_calls = []
        active_events = []
        active_rate_limits = []
        row = {"query": query, "calls": active_calls, "local_validation_events": active_events}
        row["provider_rate_limits"] = active_rate_limits
        started = perf_counter()
        previous_trace = sys.gettrace()
        try:
            # Python-wide tracing changes SDK/JSON/asyncio timings substantially.
            # Enable only for diagnosing a local caught error, never latency runs.
            if getattr(args, "trace_local_errors", False):
                sys.settrace(exception_trace)
            result = await consultation.safe_consult_customer(
                text=query,
                record=record,
                repository=repository,
                groq=service,
                review_selection=settings.customer_consultation_review_enabled,
                turn_id=f"trace-{index}",
            )
            if result:
                row.update(
                    text=result.text,
                    model=result.model,
                    memory=result.memory,
                    products=[
                        {"id": p.product_id, "name": p.name, "price_vnd": p.price_vnd}
                        for p in result.products
                    ],
                )
                remember_published_turn(record, row)
            else:
                row["delegated"] = True
        except Exception as exc:
            row.update(safe_error(exc))
        finally:
            sys.settrace(previous_trace)
        row["latency_seconds"] = round(perf_counter() - started, 3)
        report["rows"].append(row)
        report["final_state"] = probe_state(record)
        report["cooldowns"] = cooldown_snapshot(service)
        report["source_hashes_after"] = source_hashes()
        report["source_stable"] = report["source_hashes_before"] == report["source_hashes_after"]
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {key: value for key, value in row.items() if key not in {"calls", "memory"}},
                ensure_ascii=True,
            ),
            flush=True,
        )
        if active_rate_limits or any(
            t.get("status_code") == 429 for call in active_calls for t in call.get("trace", [])
        ):
            report["stop_reason"] = (
                "429 observed; no further turn sent. Honor recorded retry headers."
            )
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            break
    print(str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="legacy")
    parser.add_argument("--query", action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pause-seconds", type=float, default=45)
    parser.add_argument("--config", choices=("demo", "settings"), default="demo")
    parser.add_argument("--reasoning-effort", choices=("none", "low", "medium", "high"))
    parser.add_argument("--model", help="Explicit model for a controlled diagnostic")
    parser.add_argument("--respect-cooldowns", type=Path)
    parser.add_argument("--resume-from", type=Path,
                        help="Resume a captured journey's published turns and known cooldowns.")
    parser.add_argument("--trace-local-errors", action="store_true")
    parser.add_argument("--compact-selection-policy", action="store_true")
    args = parser.parse_args()
    if args.pause_seconds < 45 or not args.pause_seconds < float("inf"):
        parser.error("--pause-seconds must be finite and at least45")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
