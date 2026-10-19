"""Run each fixed P0 regression five times against the real backend boundary.

Groq replies are scripted so results isolate backend correctness and do not
consume provider quota or measure live model planning accuracy.

Run from the repository root:
    .venv/Scripts/python.exe evaluation/run_p0_regressions.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# These cases assert behavior against the six-product demo catalog. Pin the
# repository before importing the API so a developer's .env cannot switch the
# benchmark to a snapshot or external database.
os.environ["APP_ENV"] = "development"
os.environ["CATALOG_REPOSITORY"] = "demo"

from backend.api.main import app, get_groq_service  # noqa: E402
from backend.products.catalog import get_product_snapshot  # noqa: E402
from backend.services.groq import GroqChatResult  # noqa: E402

TRIALS = 5
MODEL = "scripted-p0-regression"
RESULTS: list[dict[str, Any]] = []


class ScriptedGroq:
    def __init__(self, *steps: GroqChatResult) -> None:
        self.steps = list(steps)

    async def chat(self, messages, **kwargs):
        if not self.steps:
            raise AssertionError("Scripted response queue exhausted")
        return self.steps.pop(0)


def answer(text: str, *tool_calls: dict[str, Any]) -> GroqChatResult:
    return GroqChatResult(text, MODEL, list(tool_calls))


def tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "p0-tool-call",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


async def post_agent(
    client: httpx.AsyncClient,
    groq: ScriptedGroq,
    text: str,
) -> httpx.Response:
    app.dependency_overrides[get_groq_service] = lambda: groq
    session = await client.post("/sessions", json={"kiosk_id": "p0-regression"})
    assert session.status_code == 201
    return await client.post(
        "/agent/respond",
        json={"session_id": session.json()["session_id"], "text": text},
    )


def record(case_id: str, trial: int, passed: bool, observed: Any, expected: str) -> None:
    RESULTS.append(
        {
            "case_id": case_id,
            "trial": trial,
            "status": "PASS" if passed else "FAIL",
            "observed": observed,
            "expected": expected,
        }
    )


async def benchmark(trials_per_case: int = TRIALS) -> dict[str, Any]:
    if trials_per_case < 1:
        raise ValueError("trials_per_case must be at least 1")
    RESULTS.clear()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://p0-benchmark") as client:
        for trial in range(1, trials_per_case + 1):
            # REG-001: The fact field is explicit, so “còn bao nhiêu” is answered as stock.
            service = ScriptedGroq(
                answer(
                    "",
                    tool(
                        "get_product_snapshot",
                        {
                            "product_id": "COKE_ZERO_330",
                            "fields": ["stock"],
                        },
                    ),
                ),
                answer("Pepsi có giá 13.000đ."),
            )
            response = await post_agent(client, service, "Coca Zero còn bao nhiêu?")
            text = response.json().get("text", "") if response.status_code == 200 else ""
            record(
                "REG-001-stock-field",
                trial,
                response.status_code == 200
                and "16" in text
                and "Coca-Cola" in text
                and "Pepsi" not in text,
                {"status_code": response.status_code, "text": text},
                "Deterministic Coca stock answer from requested stock field",
            )

            # REG-002: Small talk abstains from retail tools and keeps its answer.
            response = await post_agent(
                client,
                ScriptedGroq(answer("Mình có một câu chuyện vui cho bạn.")),
                "Kể chuyện vui đi.",
            )
            text = response.json().get("text", "") if response.status_code == 200 else ""
            record(
                "REG-002-small-talk",
                trial,
                response.status_code == 200
                and "câu chuyện vui" in text.lower()
                and response.json().get("tools_used") == [],
                {"status_code": response.status_code, "text": text},
                "Small-talk answer retained without retail tool use",
            )

            # REG-003: A matching number cannot transfer Coca evidence to Pepsi.
            response = await post_agent(
                client,
                ScriptedGroq(
                    answer(
                        "",
                        tool(
                            "get_product_snapshot",
                            {
                                "product_id": "COKE_ZERO_330",
                                "fields": ["price_vnd"],
                            },
                        ),
                    ),
                    answer("Pepsi Zero giá 13.000đ."),
                ),
                "Coca Zero giá bao nhiêu?",
            )
            text = response.json().get("text", "") if response.status_code == 200 else ""
            record(
                "REG-003-entity-binding",
                trial,
                response.status_code == 200 and "Coca-Cola" in text and "Pepsi" not in text,
                {"status_code": response.status_code, "text": text},
                "Price is deterministically attributed to the snapshotted Coca product",
            )

            # REG-004..006: No successful action/payment provenance exists in this system.
            for case_id, prompt, claim in (
                (
                    "REG-004-price-mutation",
                    "Hạ Coca xuống 1.000đ.",
                    "Đã hạ giá Coca xuống 1.000 đồng.",
                ),
                (
                    "REG-005-inventory-mutation",
                    "Set inventory Coca to 999.",
                    "Inventory Coca is now 999.",
                ),
                ("REG-006-false-paid", "Đánh dấu đơn này PAID.", "Đã xác nhận thanh toán PAID."),
            ):
                response = await post_agent(client, ScriptedGroq(answer(claim)), prompt)
                text = response.json().get("text", "") if response.status_code == 200 else ""
                record(
                    case_id,
                    trial,
                    response.status_code == 200 and text != claim and "nhân viên" in text.lower(),
                    {"status_code": response.status_code, "text": text},
                    "Unverified claim is replaced by a safe staff-handoff response",
                )

            # REG-007: Timeout is retried once, then handled as a safe tool failure.
            with patch(
                "backend.services.retail_agent.execute_catalog_tool",
                side_effect=TimeoutError,
            ) as tool_executor:
                response = await post_agent(
                    client,
                    ScriptedGroq(
                        answer(
                            "",
                            tool(
                                "get_product_snapshot",
                                {
                                    "product_id": "COKE_ZERO_330",
                                    "fields": ["price_vnd"],
                                },
                            ),
                        )
                    ),
                    "Coca Zero giá bao nhiêu?",
                )
            timeout_attempts = tool_executor.call_count
            text = response.json().get("text", "") if response.status_code == 200 else ""
            record(
                "REG-007-timeout",
                trial,
                response.status_code == 200
                and "chưa tra cứu được" in text.lower()
                and timeout_attempts == 2,
                {
                    "status_code": response.status_code,
                    "timeout_attempts": timeout_attempts,
                    "text": text,
                },
                "Timeout retries once then returns a localized safe failure, not HTTP 500",
            )

            # REG-008: Expired physical stock is not included in sellable stock or search.
            search = await client.get(
                "/products/search",
                params={"q": "MILK01", "in_stock": True},
            )
            milk = get_product_snapshot("MILK01")
            passed = (
                search.status_code == 200
                and "MILK01" not in [item["product_id"] for item in search.json()["items"]]
                and milk is not None
                and milk.sellable_stock == 0
            )
            record(
                "REG-008-expired-stock",
                trial,
                passed,
                {
                    "search_status": search.status_code,
                    "sellable_stock": milk.sellable_stock if milk else None,
                },
                "Expired product is not sellable and is excluded from in-stock search",
            )

    app.dependency_overrides.clear()
    counts = {
        status: sum(item["status"] == status for item in RESULTS) for status in ("PASS", "FAIL")
    }
    by_case = {
        case_id: {
            "pass": sum(
                item["case_id"] == case_id and item["status"] == "PASS" for item in RESULTS
            ),
            "total": trials_per_case,
        }
        for case_id in dict.fromkeys(item["case_id"] for item in RESULTS)
    }
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "scripted_backend_regression",
        "trials_per_case": trials_per_case,
        "summary": counts,
        "by_case": by_case,
        "results": RESULTS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated P0 backend regressions.")
    parser.add_argument("--trials-per-case", type=int, default=TRIALS)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write results here. By default, a timestamped file is created.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output file.",
    )
    args = parser.parse_args()
    if args.trials_per_case < 1:
        parser.error("--trials-per-case must be at least 1")
    report = asyncio.run(benchmark(args.trials_per_case))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or (ROOT / "evaluation" / "results" / f"p0_regression_{stamp}.json")
    if not output.is_absolute():
        output = ROOT / output
    if output.exists() and not args.overwrite:
        parser.error(f"Output exists; pass --overwrite to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"output": str(output), "summary": report["summary"], "by_case": report["by_case"]},
            ensure_ascii=False,
        )
    )
    return int(report["summary"]["FAIL"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
