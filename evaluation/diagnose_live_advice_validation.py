"""Diagnose authored consultation prompts; preserve artifacts and sanitize failures.

No provider call is made by --help. Each --query starts a fresh session.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReview,
    GroundedSelection,
    consult_customer,
)
from backend.services.groq import GroqService
from backend.services.sessions import InMemorySessionStore

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'backend/services/customer_consultation.py'


def parser():
    value = argparse.ArgumentParser(__doc__)
    value.add_argument(
        '--query',
        action='append',
        required=True,
        help='Authored diagnostic prompt; may repeat for fresh sessions.',
    )
    value.add_argument('--output', type=Path, required=True,
                       help='New JSON artifact path. Existing files are never overwritten.')
    return value


def known_messages():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    return {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == 'ValueError' and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def safe_failure(exc, known):
    data = {'error_type': type(exc).__name__}
    if type(exc).__name__ == 'ValidationError' and callable(getattr(exc, 'errors', None)):
        data['validation_errors'] = [
            {'loc': error.get('loc'), 'type': error.get('type')}
            for error in exc.errors()
        ]
    elif isinstance(exc, ValueError):
        data['validation_message'] = (
            str(exc) if str(exc) in known else 'Unrecognized validation message omitted'
        )
    data['stack'] = [
        {'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
        for frame in traceback.extract_tb(exc.__traceback__)
        if Path(frame.filename).resolve() == SOURCE.resolve()
    ]
    return data


def safe_trace(trace):
    keys = {'model', 'status', 'error_type', 'error_code', 'status_code',
            'credential_slot', 'rate_limit_headers', 'elapsed_seconds', 'recovery'}
    return [{key: value for key, value in row.items() if key in keys}
            for row in trace if isinstance(row, dict)]


def limited(trace):
    return any(row.get('status_code') == 429
               or row.get('error_code') == 'rate_limit_exceeded'
               or row.get('status') == 'cooldown' for row in trace)


def schema_constraints(value, schema, path=()):
    """Constraint-only diagnostic: no values or provider messages in failures."""
    issues = []
    if isinstance(value, str):
        for keyword, invalid in (("minLength", len(value) < schema.get("minLength", 0)),
                                 ("maxLength", len(value) > schema.get("maxLength", float("inf")))):
            if invalid:
                issues.append({"loc": list(path), "type": keyword,
                               "limit": schema[keyword], "actual_length": len(value)})
    if isinstance(value, list):
        for keyword, invalid in (("minItems", len(value) < schema.get("minItems", 0)),
                                 ("maxItems", len(value) > schema.get("maxItems", float("inf")))):
            if invalid:
                issues.append({"loc": list(path), "type": keyword,
                               "limit": schema[keyword], "actual_length": len(value)})
        for index, item in enumerate(value):
            issues.extend(schema_constraints(item, schema.get("items", {}), (*path, index)))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                issues.append({"loc": list((*path, key)), "type": "required"})
        for key, item in value.items():
            if key in schema.get("properties", {}):
                issues.extend(schema_constraints(item, schema["properties"][key], (*path, key)))
            elif schema.get("additionalProperties") is False:
                issues.append({"loc": list(path), "type": "additionalProperties"})
    if "enum" in schema and value not in schema["enum"]:
        issues.append({"loc": list(path), "type": "enum"})
    return issues


def provider_schema_diagnostic(exc, request, slot):
    # Only fixed metadata and JSON generated for our authored probe are retained.
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error", body)
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if code not in {"json_validate_failed", "tool_use_failed", "invalid_request_error"}:
        return None
    spec = (request.get("response_format") or {}).get("json_schema", {})
    row = {"error_code": code, "model": request.get("model"),
           "credential_slot": slot, "stage": spec.get("name"),
           "status_code": getattr(exc, "status_code", None)}
    generation = error.get("failed_generation")
    if not isinstance(generation, (str, dict, list)):
        row["failed_generation_available"] = False
        return row
    try:
        parsed = json.loads(generation) if isinstance(generation, str) else generation
    except (ValueError, TypeError) as parse_error:
        row["failed_generation_parseable_json"] = False
        if isinstance(generation, str):
            stripped = generation.strip()
            lowered = stripped.casefold()
            row["failed_generation_structure"] = {
                "length": len(generation),
                "has_json_fence": "```json" in lowered,
                "starts_lt_think": lowered.startswith("<think>"),
                "ends_think": lowered.endswith("</think>"),
                "contains_tooltag": any(tag in lowered for tag in (
                    "<tool_call>", "</tool_call>", "<function=", "<tool=",
                )),
                "looks_json_object": stripped.startswith("{"),
                "first_nonspace_codepoint": ord(stripped[0]) if stripped else None,
            }
            if isinstance(parse_error, json.JSONDecodeError):
                # JSONDecodeError.msg is copied only through a closed enum. Never
                # retain its document, repr, arbitrary provider text or snippets.
                known = {
                    "Expecting value", "Extra data",
                    "Expecting property name enclosed in double quotes",
                    "Expecting ':' delimiter", "Expecting ',' delimiter",
                    "Unterminated string starting at", "Invalid control character at",
                    "Invalid \\escape", "Invalid \\uXXXX escape",
                }
                row["failed_generation_structure"]["json_error"] = {
                    "pos": parse_error.pos, "lineno": parse_error.lineno,
                    "message": parse_error.msg if parse_error.msg in known else "other",
                }
        return row
    row["failed_generation_decision"] = parsed
    row["schema_constraints"] = schema_constraints(parsed, spec.get("schema", {}))
    klass = {item.__name__: item for item in
             (ConsultationDecision, GroundedSelection, ConsultationReview)}.get(spec.get("name"))
    if klass:
        try:
            klass.model_validate(parsed)
        except Exception as validation:
            if type(validation).__name__ == "ValidationError":
                row["pydantic_errors"] = [{"loc": item.get("loc"), "type": item.get("type")}
                                          for item in validation.errors()]
    return row


async def main():
    args = parser().parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve atomically before creating a provider client. Never alter baseline files.
    with output.open('x', encoding='utf-8') as artifact:
        artifact.write('{}')
    report = {
        'kind': 'direct consultation authored development diagnostic',
        'created_at': datetime.now(UTC).isoformat(),
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'rows': [],
    }
    known = known_messages()
    settings = Settings(catalog_repository='snapshot')
    repository = get_product_repository(settings)
    service = GroqService(settings)
    original_client = service._client_for_credential
    wrapped_slots = set()

    def diagnostic_client(slot, api_key):
        client = original_client(slot, api_key)
        if slot not in wrapped_slots:
            raw = client.chat.completions.with_raw_response
            original_create = raw.create

            async def diagnostic_create(**request):
                try:
                    return await original_create(**request)
                except Exception as exc:
                    diagnostic = provider_schema_diagnostic(exc, request, slot)
                    if diagnostic is not None:
                        report.setdefault("provider_schema_failures", []).append(diagnostic)
                        save()
                    raise

            raw.create = diagnostic_create
            wrapped_slots.add(slot)
        return client

    service._client_for_credential = diagnostic_client
    original = service.chat
    calls = []
    rate_limited = False
    active_events = []
    source_path = str(SOURCE.resolve()).casefold()

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def exception_trace(frame, event, arg):
        # Record caught local failures too: the public error can hide an earlier render issue.
        if event == 'exception' and frame.f_code.co_filename.casefold() == source_path:
            _, exc, _ = arg
            if isinstance(exc, ValueError):
                data = safe_failure(exc, known)
                data.update(function=frame.f_code.co_name, line=frame.f_lineno,
                            stage_calls_so_far=len(calls))
                if len(active_events) < 200:
                    active_events.append(data)
        return exception_trace

    async def traced(messages, **kwargs):
        nonlocal rate_limited
        response_format = kwargs.get('response_format') or {}
        row = {
            'stage': response_format.get('json_schema', {}).get('name', 'chat'),
            'context': messages, 'max_tokens': kwargs.get('max_tokens'),
        }
        calls.append(row)
        started = time.perf_counter()
        try:
            result = await original(messages, **kwargs)
        except Exception as exc:
            trace = safe_trace(getattr(exc, 'fallback_trace', []))
            row.update(error_type=type(exc).__name__, fallback_trace=trace)
            rate_limited = rate_limited or limited(trace)
            raise
        else:
            trace = safe_trace(result.fallback_trace)
            row.update(model=result.model, usage=result.usage, fallback_trace=trace)
            rate_limited = rate_limited or limited(trace)
            try:
                row['decision'] = json.loads(str(result))
            except ValueError:
                # Only successful generated output is captured; provider errors are never copied.
                row['unparsed_synthetic_response'] = str(result)
            return result
        finally:
            row['elapsed_s'] = round(time.perf_counter() - started, 3)
            save()

    service.chat = traced
    previous_trace = sys.gettrace()
    try:
        for prompt in args.query:
            calls = []
            active_events = []
            row = {'prompt': prompt, 'calls': calls, 'local_validation_events': active_events}
            report['rows'].append(row)
            record = InMemorySessionStore().create('direct-synthetic-diagnostic')
            started = time.perf_counter()
            sys.settrace(exception_trace)
            try:
                result = await asyncio.wait_for(
                    consult_customer(text=prompt, record=record, repository=repository,
                                     groq=service, turn_id='direct-trace', review_selection=True),
                    timeout=38,
                )
                row['result'] = None if result is None else {
                    'text': result.text, 'model': result.model,
                    'products': [{'id': product.product_id, 'name': product.name}
                                 for product in result.products],
                }
            except Exception as exc:
                row.update(safe_failure(exc, known))
                row['fallback_trace'] = safe_trace(getattr(exc, 'fallback_trace', []))
            finally:
                sys.settrace(previous_trace)
            row['elapsed_s'] = round(time.perf_counter() - started, 3)
            save()
            summary_row = {
                key: value
                for key, value in row.items()
                if key not in {'calls', 'local_validation_events'}
            }
            print(json.dumps(summary_row, ensure_ascii=False), flush=True)
            if rate_limited:
                report['stop_reason'] = 'Rate limit/cooldown observed; no next prompt attempted.'
                break
        save()
    finally:
        sys.settrace(previous_trace)
        for client in service._clients_by_credential.values():
            await client.close()


if __name__ == '__main__':
    asyncio.run(main())

