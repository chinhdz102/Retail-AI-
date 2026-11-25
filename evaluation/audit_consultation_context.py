"""Offline byte footprint replay; never claims new provider token measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.services.customer_consultation import _compact_reference_context


def audit(source: Path) -> dict:
    content = source.read_bytes()
    document = json.loads(content)
    rows = []
    for turn in document["rows"]:
        for call in turn.get("calls", []):
            messages = call.get("context", [])
            if len(messages) < 3:
                continue
            prefix, context_text = messages[1]["content"].split("\n", 1)
            context = json.loads(context_text)
            compact = _compact_reference_context(context)
            updated = [dict(message) for message in messages]
            updated[1]["content"] = prefix + "\n" + json.dumps(
                compact, ensure_ascii=False, separators=(",", ":"),
            )
            before = sum(len(message["content"].encode("utf-8")) for message in messages)
            after = sum(len(message["content"].encode("utf-8")) for message in updated)
            rows.append({
                "stage": call["stage"], "before_message_utf8_bytes": before,
                "after_message_utf8_bytes": after, "saved_bytes": before - after,
                "recorded_provider_prompt_tokens_before": (
                    call.get("usage", {}).get("prompt_tokens")
                ),
                "provider_prompt_tokens_after": None,
                "roles_unchanged": [m["role"] for m in messages] == [m["role"] for m in updated],
                "system_and_customer_unchanged": (
                    messages[0] == updated[0] and messages[-1] == updated[-1]
                ),
            })
    return {
        "kind": "offline lossless reference compaction replay",
        "source": str(source), "source_sha256": hashlib.sha256(content).hexdigest(),
        "limitation": (
            "UTF-8 message bytes only; schema/provider framing excluded. "
            "No tokenizer or provider calls; after token counts are unknown."
        ),
        "rows": rows,
        "before_message_utf8_bytes": sum(row["before_message_utf8_bytes"] for row in rows),
        "after_message_utf8_bytes": sum(row["after_message_utf8_bytes"] for row in rows),
        "saved_bytes": sum(row["saved_bytes"] for row in rows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.source)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
