"""Bounded matched-context model probe; no production configuration mutation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.customer_consultation import (
    ConsultationDecision,
    GroundedSelection,
    _accept_default_only_json,
    _render_selection,
    _tool_schema,
)
from backend.services.groq import GroqProviderError, GroqService

ROOT = Path(__file__).resolve().parents[1]


async def main():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / f"docs/reports/artifacts/frozen_selection_probe_{stamp}.json"
    previous = ROOT / "docs/reports/artifacts/frozen_selection_probe_20261103T124703Z.json"
    report = (
        json.loads(previous.read_text(encoding="utf-8"))
        if previous.exists()
        else {
            "kind": "four-call frozen selection development ablation",
            "rows": [],
        }
    )
    report.pop("stop_reason", None)
    report["note"] = (
        "Qwen none and 120B low are distinct configured arms; "
        "old traces are not matched low controls."
    )
    repository = get_product_repository(Settings())
    cases = [
        ("hotpot-final", "live_hotpot_direct_trace_final_20261103.json"),
        ("breakfast-old", "live_advice_direct_trace_20261103.json"),
    ]
    for label, filename in cases:
        source = ROOT / "docs/reports/artifacts" / filename
        document = json.loads(source.read_text(encoding="utf-8"))
        calls = [
            c
            for row in document["rows"]
            for c in row.get("calls", [])
            if c.get("stage") == "GroundedSelection" and c.get("context")
        ]
        call = calls[-1] if label == "hotpot-final" else calls[0]
        messages = call["context"]
        context = json.loads(messages[1]["content"].split("\n", 1)[1])
        plan = ConsultationDecision.model_validate(context["decision"])
        candidates = [repository.get_product_snapshot(p["id"]) for p in context["candidates"]]
        if any(p is None for p in candidates):
            raise RuntimeError("Frozen candidate missing from actual repository")
        sources = {p["id"]: p["search_indexes"] for p in context["candidates"]}
        for model, reasoning in [("qwen/qwen3.8-27b", "none"), ("openai/gpt-oss-120b", "low")]:
            if any(r["case"] == label and r["model_requested"] == model for r in report["rows"]):
                continue
            if report["rows"]:
                await asyncio.sleep(45)
            settings = Settings(
                groq_model=model,
                groq_fallback_model="",
                groq_fallback_model_2="",
                groq_structured_reasoning_effort=reasoning,
            )
            service = GroqService(settings)
            row = {
                "case": label,
                "model_requested": model,
                "reasoning": reasoning,
                "source": filename,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "context_sha256": hashlib.sha256(
                    json.dumps(messages, ensure_ascii=False).encode()
                ).hexdigest(),
                "schema_sha256": hashlib.sha256(
                    json.dumps(
                        _tool_schema(GroundedSelection, strict=True), sort_keys=True
                    ).encode()
                ).hexdigest(),
            }
            start = perf_counter()
            try:
                result = await service.structured_chat(
                    messages,
                    name="GroundedSelection",
                    schema=_tool_schema(GroundedSelection, strict=True),
                    max_tokens=1400,
                    local_json_validator=lambda text: _accept_default_only_json(
                        GroundedSelection, text
                    ),
                )
                row.update(model=result.model, usage=result.usage, trace=result.fallback_trace)
                selection = GroundedSelection.model_validate_json(str(result), strict=True)
                row["decision"] = selection.model_dump(mode="json")
                try:
                    answer, products = _render_selection(selection, candidates, plan, sources)
                    row.update(
                        grounding_valid=True,
                        rendered_text=answer,
                        products=[
                            {"id": p.product_id, "name": p.name, "price_vnd": p.price_vnd}
                            for p in products
                        ],
                    )
                except ValueError as exc:
                    row.update(grounding_valid=False, validation_issue=str(exc))
            except GroqProviderError as exc:
                row.update(error_type=type(exc).__name__, trace=exc.fallback_trace)
                body = getattr(exc.__cause__, "body", {})
                row["error_flags"] = {
                    flag: flag in str(body).casefold()
                    for flag in (
                        "json_validate_failed",
                        "max_tokens",
                        "reasoning",
                        "maximum",
                        "maxLength",
                    )
                }
                row["latency_s"] = round(perf_counter() - start, 3)
                report["rows"].append(row)
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(str(output), flush=True)
                if any(t.get("status_code") == 429 for t in exc.fallback_trace):
                    report["stop_reason"] = "429 observed; no additional requests."
                    output.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    return
                continue
            row["latency_s"] = round(perf_counter() - start, 3)
            report["rows"].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "case": label,
                        "model": model,
                        "usage": row.get("usage"),
                        "grounding_valid": row.get("grounding_valid"),
                    }
                ),
                flush=True,
            )
            # A credential rotation after 429 is still evidence of capacity pressure;
            # stop rather than turning this experiment into further quota probing.
            if any(t.get("status_code") == 429 for t in row.get("trace", [])):
                report["stop_reason"] = (
                    "429 observed before successful credential rotation; stopped."
                )
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                break
        if report.get("stop_reason"):
            break
    print(str(output), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
