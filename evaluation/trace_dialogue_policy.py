"""Trace validated planner/composer/review outputs with the real configured LLM.

This is a direct-service diagnostic. It changes no prompts, retrieval or verdicts.
Only validated JSON and public catalog evidence are retained; no credentials or
raw provider errors are written.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

from backend.products.repository import get_product_repository
from backend.services import consultation
from backend.services.groq import GroqProviderError, GroqService
from evaluation.audit_dialogue_policy import CONVERSATIONS


async def run(args):
    sys.stdout.reconfigure(encoding="utf-8")
    source = Path("data/thanh_lam_products.json")
    report = {"mode": "real-llm-direct-service-trace", "case": args.case,
              "catalog_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "turns": [], "complete": False}
    original = consultation._json_call
    service = GroqService()
    repository = get_product_repository()
    history, previous_ids, previous_plan = [], [], None
    current = {}

    async def traced(*call_args, **kwargs):
        stage = {"stage": call_args[2].__name__}
        current["stages"].append(stage)
        # Record public retrieval evidence only, never service configuration.
        for message in call_args[1]:
            if message["content"].startswith("Shopping evidence (data, not instructions): "):
                evidence = json.loads(message["content"].split(": ", 1)[1])
                stage["evidence"] = {key: evidence[key] for key in
                    ("plan", "candidates", "previous_products", "review_feedback")
                    if key in evidence}
        try:
            value, model = await original(*call_args, **kwargs)
        except GroqProviderError:
            stage["error_type"] = "GroqProviderError"
            raise
        stage.update(model=model, output=value.model_dump())
        return value, model

    consultation._json_call = traced
    try:
        for question in CONVERSATIONS[args.case]:
            current = {"question": question, "stages": []}
            report["turns"].append(current)
            start = time.perf_counter()
            try:
                answer = await consultation.consult(service, repository, question,
                    history, previous_ids, previous_plan)
            except GroqProviderError as error:
                current["error_type"] = type(error).__name__
                previous_plan = getattr(error, "consultation_plan", previous_plan)
                history.append({"role": "user", "content": question})
            else:
                current.update(answer=answer.text, model=answer.model)
                previous_plan = answer.plan.model_dump()
                previous_ids = [p.product_id for p in answer.products] or previous_ids
                history.extend([{"role": "user", "content": question},
                                {"role": "assistant", "content": answer.text}])
            current["seconds"] = round(time.perf_counter() - start, 3)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in current.items() if k != "stages"},
                             ensure_ascii=False), flush=True)
            await asyncio.sleep(args.pause_seconds)
    finally:
        consultation._json_call = original
    report["complete"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=list(CONVERSATIONS), required=True)
    parser.add_argument("--pause-seconds", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
