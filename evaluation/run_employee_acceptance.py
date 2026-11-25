"""Capture paced real HTTP turns for the predeclared employee acceptance suite.

This records evidence; content remains pending agent manual review. It does not
call a judge model, infer human-level quality from HTTP200, or retry away failures.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "evaluation/cases/retail_employee_acceptance_20261103.json"
SOURCE_FILES = (
    "backend/api/main.py",
    "backend/api/schemas.py",
    "backend/config.py",
    "backend/services/customer_consultation.py",
    "backend/services/consultation_policy.py",
    "backend/services/consultation_repair.py",
    "backend/services/consultation_verification.py",
    "backend/services/consultation_text.py",
    "backend/services/product_form.py",
    "backend/services/product_constraints.py",
    "backend/services/groq.py",
    "backend/services/retail_agent.py",
    "backend/services/product_comparison.py",
    "backend/services/query_understanding.py",
    "backend/services/shopping_guidance.py",
    "backend/services/shopping_language.py",
    "backend/products/category_taxonomy.py",
    "backend/products/snapshot_repository.py",
    "data/shopping_guidance.json",
    "data/thanh_lam_products.json",
    "evaluation/run_employee_acceptance.py",
)


def timestamp():
    return datetime.now(UTC).isoformat()


def source_hashes(suite_path: Path) -> dict[str, str]:
    result = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    result["acceptance_suite"] = hashlib.sha256(suite_path.read_bytes()).hexdigest()
    return result


def load_schedule(suite: dict, selectors: list[str] | None, max_turns: int | None) -> list[dict]:
    chosen = {value for item in selectors or [] for value in item.split(",") if value}
    ids = {journey["id"] for journey in suite["journeys"]}
    unknown = chosen - ids - {"original35", "new12"}
    if unknown:
        raise ValueError("Unknown journey selector(s): " + ", ".join(sorted(unknown)))
    schedule = []
    for journey in suite["journeys"]:
        selected = (
            not chosen
            or journey["id"] in chosen
            or "original35" in chosen
            and journey["group"] == "original35"
            or "new12" in chosen
            and journey["group"] == "cross_category_generalization"
        )
        if not selected:
            continue
        for index, rubric in enumerate(journey["turns"], 1):
            schedule.append(
                {
                    "journey_id": journey["id"],
                    "journey_name": journey["name"],
                    "group": journey["group"],
                    "session_mode": journey["session_mode"],
                    "turn_index": index,
                    "rubric": rubric,
                }
            )
    return schedule[:max_turns] if max_turns is not None else schedule


def summarize(rows: list[dict]) -> dict:
    counts = {
        label: sum(row["result_class"] == label for row in rows)
        for label in ("not_run", "pending_agent_manual_review", "service_failure")
    }
    return {
        "scheduled_turns_denominator": len(rows),
        "attempted_http_turns": sum(row.get("respond_attempted", False) for row in rows),
        "service_failures_in_denominator": counts["service_failure"],
        "not_run_in_denominator": counts["not_run"],
        "pending_agent_manual_review": counts["pending_agent_manual_review"],
        "original_prompt_turns": sum("original_index" in row["rubric"] for row in rows),
        "setup_turns": sum(row["rubric"]["role"] == "setup" for row in rows),
        "new_journey_turns": sum(row["group"] == "cross_category_generalization" for row in rows),
        "semantic_pass_count": None,
        "semantic_acceptance_rate": None,
        "review_notice": "Content requires agent manual review against saved rubrics. "
        "This is not independent human evaluation; HTTP success is not acceptance.",
    }


def capture_response(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return {
        "http_status": response.status_code,
        "response_json": payload,
        "response_text": response.text,
    }


def is_service_failure(capture: dict) -> bool:
    payload = capture.get("response_json")
    if capture.get("http_status") != 200 or not isinstance(payload, dict):
        return True
    model = str(payload.get("model", "")).casefold()
    return (
        not isinstance(payload.get("text"), str)
        or not payload["text"].strip()
        or "consultation-fallback" in model
    )


def write_report(output: Path, report: dict):
    report["updated_at"] = timestamp()
    report["summary"] = summarize(report["rows"])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


async def run_capture(args, suite: dict, schedule: list[dict]):
    initial_hashes = source_hashes(args.suite)
    report = {
        "suite_id": suite["suite_id"],
        "declared_full_suite_journeys": len(suite["journeys"]),
        "declared_full_suite_turns": sum(len(journey["turns"]) for journey in suite["journeys"]),
        "created_at": timestamp(),
        "completed": False,
        "evaluation_policy": suite["evaluation_policy"],
        "provenance": suite["provenance"],
        "base_url": args.base_url,
        "delay_seconds_after_response": args.delay_seconds,
        "timeout_seconds": args.timeout_seconds,
        "source_sha256_at_start": initial_hashes,
        "source_hash_notice": "Hashes describe files on disk; the operator must verify the "
        "HTTP service was restarted with these sources. No process-version claim is inferred.",
        "source_unchanged": True,
        "source_changed_at_turn": None,
        "selected_journeys": list(dict.fromkeys(item["journey_id"] for item in schedule)),
        "max_turns_global": args.max_turns,
        "sessions": {},
        "rows": [
            {
                **item,
                "result_class": "not_run",
                "respond_attempted": False,
                "manual_review": {
                    "reviewer": "agent_manual_review_pending",
                    "rating": None,
                    "rationale": None,
                },
            }
            for item in schedule
        ],
    }
    write_report(args.output, report)
    next_allowed = 0.0
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout_seconds) as client:
        for row_number, row in enumerate(report["rows"], 1):
            if source_hashes(args.suite) != initial_hashes:
                report["source_unchanged"] = False
                report["source_changed_at_turn"] = row_number
                report["stop_reason"] = "source_changed; remaining turns retained as not_run"
                break
            journey_id = row["journey_id"]
            if journey_id not in report["sessions"]:
                session = {"created_at": timestamp()}
                try:
                    response = await client.post(
                        "/sessions",
                        json={
                            "kiosk_id": "employee-acceptance",
                        },
                    )
                    session.update(capture_response(response))
                    payload = session["response_json"]
                    if response.status_code in {200, 201} and isinstance(payload, dict):
                        session["session_id"] = payload.get("session_id")
                except httpx.HTTPError as exc:
                    session["error_type"] = type(exc).__name__
                report["sessions"][journey_id] = session
            session = report["sessions"][journey_id]
            if not session.get("session_id"):
                row.update(result_class="service_failure", failure_stage="session_creation")
                write_report(args.output, report)
                continue
            # Pace all respond requests, including between separate journeys.
            delay = max(0.0, next_allowed - perf_counter())
            if delay:
                await asyncio.sleep(delay)
            if source_hashes(args.suite) != initial_hashes:
                report["source_unchanged"] = False
                report["source_changed_at_turn"] = row_number
                report["stop_reason"] = "source_changed; remaining turns retained as not_run"
                break
            row["request"] = {
                "session_id": session["session_id"],
                "text": row["rubric"]["text"],
                "language": "vi",
                "client_turn_id": str(uuid4()),
            }
            row["respond_attempted"] = True
            row["started_at"] = timestamp()
            start = perf_counter()
            try:
                response = await client.post("/agent/respond", json=row["request"])
                row.update(capture_response(response))
                row["result_class"] = (
                    "service_failure" if is_service_failure(row) else "pending_agent_manual_review"
                )
            except httpx.HTTPError as exc:
                row.update(
                    result_class="service_failure",
                    failure_stage="respond_transport",
                    error_type=type(exc).__name__,
                )
            row["elapsed_seconds"] = round(perf_counter() - start, 3)
            row["finished_at"] = timestamp()
            next_allowed = perf_counter() + args.delay_seconds
            write_report(args.output, report)
            print(
                json.dumps(
                    {
                        "journey": journey_id,
                        "turn": row["turn_index"],
                        "result_class": row["result_class"],
                        "elapsed_seconds": row["elapsed_seconds"],
                    }
                ),
                flush=True,
            )
    report["source_sha256_at_end"] = source_hashes(args.suite)
    report["source_unchanged"] &= report["source_sha256_at_end"] == initial_hashes
    report["completed"] = all(row["result_class"] != "not_run" for row in report["rows"])
    write_report(args.output, report)
    print(json.dumps(report["summary"], ensure_ascii=True, indent=2))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--journeys", nargs="+", help="IDs, comma-separated IDs, original35 or new12"
    )
    parser.add_argument("--max-turns", type=int, help="Global cap, including setup turns")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=45.0,
        help="Pause after each model response; minimum45 seconds",
    )
    parser.add_argument("--timeout-seconds", type=float, default=100.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--plan-only", action="store_true", help="Validate and print schedule offline"
    )
    args = parser.parse_args()
    if args.delay_seconds < 45 or not args.delay_seconds < float("inf"):
        parser.error("--delay-seconds must be finite and at least45")
    if args.max_turns is not None and args.max_turns < 1:
        parser.error("--max-turns must be positive")
    if not 0 < args.timeout_seconds < float("inf"):
        parser.error("--timeout-seconds must be finite and positive")
    if not args.plan_only and args.output is None:
        parser.error("--output is required for a capture")
    if args.output and args.output.exists():
        parser.error("Output already exists; use a new filename to preserve previous evidence")
    return args


def main():
    args = parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    schedule = load_schedule(suite, args.journeys, args.max_turns)
    if not schedule:
        raise ValueError("No turns selected")
    if args.plan_only:
        print(
            json.dumps(
                {
                    "suite": suite["suite_id"],
                    "scheduled_turns": len(schedule),
                    "journeys": list(dict.fromkeys(row["journey_id"] for row in schedule)),
                    "original_prompt_turns": sum(
                        "original_index" in row["rubric"] for row in schedule
                    ),
                    "delay_seconds": args.delay_seconds,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return
    asyncio.run(run_capture(args, suite, schedule))


if __name__ == "__main__":
    main()
