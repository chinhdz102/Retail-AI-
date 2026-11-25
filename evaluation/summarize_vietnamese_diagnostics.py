"""Aggregate preserved Vietnamese decoder evidence; never infer microphone quality."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from backend.services.asr_disagreement import needs_confirmation

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs/reports/artifacts"


def aggregate():
    paired = json.loads(
        (ARTIFACTS / "vietnamese_asr_paired_20261105.json").read_text(encoding="utf-8")
    )
    hotwords = json.loads(
        (ARTIFACTS / "zipformer_hotwords_bpe_20261105.json").read_text(encoding="utf-8")
    )
    bias = {r["id"]: r for r in hotwords["rows"]}
    groups = defaultdict(
        lambda: defaultdict(
            lambda: {
                "scheduled": 0,
                "scored": 0,
                "word_errors": 0,
                "reference_words": 0,
                "input_or_decode_failures": [],
            }
        )
    )
    confirmed = []
    controls = []
    for row in paired["rows"]:
        variants = {**row["variants"], "zipformer_hotwords": bias.get(row["id"], {})}
        group = row["id"].split(":")[0]
        for name, result in variants.items():
            totals = groups[group][name]
            totals["scheduled"] += 1
            if "word_errors" in result:
                totals["scored"] += 1
                totals["word_errors"] += result["word_errors"]
                totals["reference_words"] += result["reference_word_count"]
            elif row["reference"]:
                totals["input_or_decode_failures"].append(row["id"])
        left, right = variants.get("baseline", {}), variants.get("zipformer", {})
        if row["reference"] and "text" in left and "text" in right:
            confirm, distance = needs_confirmation(
                left["text"], right["text"], left.get("quality", {})
            )
            confirmed.append({"id": row["id"], "confirm": confirm, "distance": distance})
        if not row["reference"]:
            controls.append(
                {
                    "id": row["id"],
                    "decoders": {
                        name: {
                            "text": value.get("text"),
                            "speech_detected": value.get("speech_detected"),
                            "error_type": value.get("error_type"),
                        }
                        for name, value in variants.items()
                    },
                }
            )
    for variants in groups.values():
        for value in variants.values():
            value["weighted_whitespace_wer"] = (
                value["word_errors"] / value["reference_words"]
                if value["reference_words"]
                else None
            )
    return {
        "limitations": "Exposed cached files, tiny samples, possible training overlap; "
        "synthetic retail is not natural human speech. Whitespace WER is "
        "format-sensitive. Decoder agreement does not establish correctness.",
        "scheduled_audio_files": len(paired["rows"]),
        "groups": dict(groups),
        "confirmation_burden": {
            "scored_speech": len(confirmed),
            "flagged": sum(r["confirm"] for r in confirmed),
            "rows": confirmed,
        },
        "controls": controls,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(aggregate(), target, ensure_ascii=False, indent=2)
        target.write("\n")
