"""One frozen native stage, no model fallbacks, preserving known Retry-After deadlines."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from jsonschema import Draft202012Validator

from backend.config import Settings
from backend.services.groq import GroqService
from evaluation.diagnose_live_advice_validation import provider_schema_diagnostic
from evaluation.trace_consultation_probe import cooldown_snapshot, safe_error


async def run(args):
    source = json.loads(args.source.read_text(encoding="utf-8"))
    frozen = source["rows"][args.row]["calls"][args.call]
    settings = Settings().model_copy(update={
        "groq_model": args.model,
        "groq_fallback_model": "", "groq_fallback_model_2": "",
        "groq_structured_reasoning_effort": args.effort,
    })
    service = GroqService(settings)
    for document in [source, *(json.loads(p.read_text(encoding="utf-8"))
                               for p in args.respect_cooldowns)]:
        for entry in document.get("cooldowns", []):
            until = datetime.fromisoformat(entry["conservative_not_before_utc"])
            if until.tzinfo is None:
                raise ValueError("Cooldown must include timezone")
            remaining = (until - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                key = (entry["model"], entry["slot"])
                service._chat_cooldowns[key] = max(
                    service._chat_cooldowns.get(key, 0), perf_counter() + remaining,
                )
    report = {
        "created_at": datetime.now(UTC).isoformat(), "kind": "frozen native stage diagnostic",
        "source": str(args.source), "row": args.row, "call": args.call,
        "model_requested": args.model, "reasoning_effort": args.effort,
        "context_sha256": hashlib.sha256(json.dumps(
            frozen["context"], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps(
            frozen["schema"], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest(),
        "max_tokens": frozen["max_tokens"], "cooldowns_before": cooldown_snapshot(service),
        "limitations": "Single exposed stage; schema validity is not advice quality "
                        "or a journey pass.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write("{}")
    original_client = service._client_for_credential
    wrapped_slots = set()
    report["provider_schema_failures"] = []

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
                        report["provider_schema_failures"].append(diagnostic)
                    raise

            endpoint.create = diagnostic_create
            wrapped_slots.add(slot)
        return client

    service._client_for_credential = diagnostic_client
    started = perf_counter()
    try:
        result = await service.structured_chat(
            frozen["context"], name=frozen["stage"], schema=frozen["schema"],
            max_tokens=frozen["max_tokens"],
        )
        payload = json.loads(str(result))
        errors = list(Draft202012Validator(frozen["schema"]).iter_errors(payload))
        report.update(model=result.model, result=payload, usage=result.usage,
                      finish_reason=result.finish_reason, trace=result.fallback_trace,
                      schema_valid=not errors,
                      schema_error_paths=[list(error.path) for error in errors])
    except Exception as exc:
        report.update(safe_error(exc))
    report.update(seconds=round(perf_counter() - started, 3),
                  cooldowns=cooldown_snapshot(service))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--row", type=int, default=0)
    parser.add_argument("--call", type=int, default=0)
    parser.add_argument("--model", default="qwen/qwen3.8-27b")
    parser.add_argument("--effort", choices=("none", "low"), required=True)
    parser.add_argument("--respect-cooldowns", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
