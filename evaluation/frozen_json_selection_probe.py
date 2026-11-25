"""One Qwen JSON-object control, with strict local parsing and grounded rendering."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter

from evaluation.frozen_selection_probe import (
    ROOT,
    ConsultationDecision,
    GroqProviderError,
    GroqService,
    GroundedSelection,
    Settings,
    _render_selection,
    _tool_schema,
    get_product_repository,
)


async def main():
    await asyncio.sleep(45)
    source = ROOT / "docs/reports/artifacts/live_hotpot_direct_trace_final_20261103.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    call = [
        c
        for row in document["rows"]
        for c in row.get("calls", [])
        if c.get("stage") == "GroundedSelection" and c.get("context")
    ][-1]
    messages = [dict(m) for m in call["context"]]
    context = json.loads(messages[1]["content"].split("\n", 1)[1])
    schema = _tool_schema(GroundedSelection, strict=True)
    messages[0]["content"] += (
        "\nReturn one JSON object conforming exactly to this schema:\n"
        + json.dumps(
            schema,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    settings = Settings(
        groq_model="qwen/qwen3.8-27b",
        groq_fallback_model="",
        groq_fallback_model_2="",
        groq_structured_reasoning_effort="none",
    )
    service = GroqService(settings)
    report = {
        "kind": "one frozen JSON-object control; not production",
        "reasoning": "none",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "context_sha256": hashlib.sha256(
            json.dumps(call["context"], ensure_ascii=False).encode()
        ).hexdigest(),
        "extra_system_schema_bytes": len(messages[0]["content"].encode())
        - len(call["context"][0]["content"].encode()),
    }
    start = perf_counter()
    try:
        result = await service.chat(
            messages, response_format={"type": "json_object"}, max_tokens=1400
        )
        report.update(model=result.model, usage=result.usage, trace=result.fallback_trace)
        try:
            selection = GroundedSelection.model_validate_json(str(result), strict=True)
            report["decision"] = selection.model_dump(mode="json")
            repository = get_product_repository(settings)
            products = [repository.get_product_snapshot(p["id"]) for p in context["candidates"]]
            plan = ConsultationDecision.model_validate(context["decision"])
            sources = {p["id"]: p["search_indexes"] for p in context["candidates"]}
            answer, picked = _render_selection(selection, products, plan, sources)
            report.update(
                grounding_valid=True,
                rendered_text=answer,
                products=[
                    {"id": p.product_id, "name": p.name, "price_vnd": p.price_vnd} for p in picked
                ],
            )
        except ValueError as exc:
            report.update(grounding_valid=False, validation_issue=type(exc).__name__)
    except GroqProviderError as exc:
        report.update(error_type=type(exc).__name__, trace=exc.fallback_trace)
    report["latency_s"] = round(perf_counter() - start, 3)
    output = ROOT / (
        "docs/reports/artifacts/frozen_json_selection_probe_"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + ".json"
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
