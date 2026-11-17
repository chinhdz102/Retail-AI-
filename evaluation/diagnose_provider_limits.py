"""Classify provider limits without recording credentials or raw error bodies."""

import argparse
import asyncio
import json
import re
from pathlib import Path

from backend.services.consultation import Plan, _response_format
from backend.services.groq import GroqService, _rate_limit_headers


async def main(args):
    service = GroqService()
    clients = service._clients(service._client_or_raise())
    rows = []
    options = {"messages": [{"role": "user", "content": "Xin chào"}]}
    if args.probe:
        probe = json.loads(args.probe.read_text(encoding="utf-8"))[0]
        options["messages"] = probe["calls"][0]["messages"]
        options["response_format"] = _response_format(Plan)
    models = list(dict.fromkeys(args.models or [service.settings.groq_model]))
    try:
        for slot, client in enumerate(clients):
            for model in models:
                row = {"credential_slot": slot, "model": model}
                try:
                    model_options = ({"reasoning_effort": "none"} if model.startswith("qwen/")
                        else {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss-") else {})
                    response = await client.chat.completions.with_raw_response.create(
                        model=model, **options, **model_options,
                        max_tokens=1200 if args.probe else args.max_tokens,
                    )
                    row.update(status=200, rate_limit_headers=_rate_limit_headers(response.headers))
                except Exception as error:
                    row.update(status=getattr(error, "status_code", None),
                               error_type=type(error).__name__)
                    # Only emit known rate-limit labels, never the surrounding text.
                    labels = re.findall(r"\b(?:TPD|TPM|RPD|RPM)\b", str(error))
                    row["limit_dimensions"] = sorted(set(labels))
                    row["rate_limit_headers"] = _rate_limit_headers(
                        getattr(getattr(error, "response", None), "headers", {}))
                rows.append(row)
                print(json.dumps(row), flush=True)
    finally:
        for client in clients:
            await client.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--output", type=Path, default=Path(
        "docs/reports/artifacts/provider_limit_dimensions_20261110.json"))
    asyncio.run(main(parser.parse_args()))
