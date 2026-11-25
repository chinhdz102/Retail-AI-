"""Four frozen real-draft checks of the fact-reference verifier, not end-to-end advice.

Cases are fixed from prior captures before execution. Expected labels never enter
the model request. This exposed diagnostic cannot establish employee quality.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.consultation_text import decode_bound_text
from backend.services.customer_consultation import (
    REVIEW,
    ConsultationDecision,
    ConsultationReview,
    GroundedSelection,
    _render_selection,
    _review_context,
    _review_issue,
    _structured,
)
from backend.services.groq import GroqService

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs/reports/artifacts"


def frozen_case(repository, label, filename, ordinal, expected, *, first_pick_only=False):
    path = ARTIFACTS / filename
    document = json.loads(path.read_text(encoding="utf-8"))
    row = document["rows"][0]
    calls = [c for c in row["calls"] if c["stage"] == "GroundedSelection" and c.get("decision")]
    call = calls[ordinal]
    data = json.loads(call["context"][1]["content"].split("\n", 1)[1])
    data["customer"] = row["query"]
    data["selection_model"] = call["model"]
    for fact in data["candidates"]:
        # These formatting references are omitted from native model context;
        # restore only the deterministic protocol fields for renderer replay.
        fact["name_ref"] = f"{{name:{fact['candidate']}}}"
        fact["price_ref"] = f"{{price:{fact['candidate']}}}"
    wire = json.loads(json.dumps(call["decision"]))
    if first_pick_only:
        index = wire["picks"][0]["candidate"]
        wire["picks"] = wire["picks"][:1]
        wire["text"] = [p for p in wire["text"] if p["candidate"] == index]
    candidates = [repository.get_product_snapshot(p["id"]) for p in data["candidates"]]
    if any(p is None for p in candidates):
        raise ValueError("Frozen product is absent from the active catalog")
    indexes = list(range(len(candidates)))
    raw = decode_bound_text(json.dumps(wire), "text", indexes, basket=False)
    selection = GroundedSelection.model_validate_json(raw)
    plan = ConsultationDecision.model_validate(data["decision"])
    sources = {p["id"]: p.get("search_indexes", []) for p in data["candidates"]}
    answer, _ = _render_selection(selection, candidates, plan, sources)
    return {
        "label": label, "source": filename,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "expected_acceptable": expected, "first_pick_only": first_pick_only,
        "rendered_text": answer,
        "context": _review_context(data, selection, answer),
    }, selection, candidates


async def run(args):
    if args.output.exists():
        raise ValueError("Preserve existing evidence; choose a new output")
    settings = Settings()
    repository = get_product_repository(settings)
    service = GroqService(settings)
    cases = [
        frozen_case(repository, "corrected-ordinary-preparation",
                    "intent_advice_boundaries_trace_20261106.json", 1, True),
        frozen_case(repository, "unsupported-popularity-and-unlimited-use",
                    "intent_advice_boundaries_trace_20261106.json", 0, False),
        frozen_case(repository, "ordinary-ingredient-preparation",
                    "intent_meat_function_trace_20261106.json", 1, True, first_pick_only=True),
        frozen_case(repository, "general-care-treatment-boundary",
                    "intent_treatment_nominal_trace_20261106.json", 1, True),
    ]
    source_files = (
        "backend/services/consultation_verification.py",
        "backend/services/customer_consultation.py",
        "backend/services/consultation_text.py",
        "evaluation/probe_fact_references.py",
    )

    def hashes():
        return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in source_files}

    report = {
        "created_at": datetime.now(UTC).isoformat(), "complete": False,
        "scope": (
            "Fixed real drafts; verifier calls only, not end-to-end advice or blind evaluation."
        ),
        "expected_labels_sent_to_model": False, "source_hashes_before": hashes(),
        "rows": [dict(row, status="not_run") for row, _, _ in cases],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    save()
    original = service.structured_chat
    active_calls = []

    async def capture(messages, **kwargs):
        result = await original(messages, **kwargs)
        active_calls.append({"decision": json.loads(str(result)), "model": result.model,
                             "usage": result.usage, "trace": result.fallback_trace})
        return result

    service.structured_chat = capture
    for number, (frozen, selection, candidates) in enumerate(cases):
        if number:
            await asyncio.sleep(45)
        active_calls = []
        row = report["rows"][number]
        started = perf_counter()
        try:
            review, model = await _structured(
                service, ConsultationReview, REVIEW, frozen["context"],
            )
            issue = _review_issue(review, selection, candidates)
            row.update(status="reviewed", acceptable=not bool(issue), issue=issue,
                       review=review.model_dump(), model=model)
            row["matches_expected"] = row["acceptable"] == frozen["expected_acceptable"]
        except Exception as exc:
            row.update(status="service_failure", error_type=type(exc).__name__)
        row.update(seconds=round(perf_counter() - started, 3), calls=active_calls)
        save()
        print(json.dumps({k: v for k, v in row.items() if k not in {"context", "calls"}},
                         ensure_ascii=True), flush=True)
    report["source_hashes_after"] = hashes()
    report["source_unchanged"] = report["source_hashes_before"] == report["source_hashes_after"]
    report["complete"] = True
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
