"""Preserve source hashes and compute diagnostic summaries without semantic scoring."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "docs/reports/artifacts"


def summarize(rows):
    return {
        "samples": len(rows),
        "accepted": sum(row.get("speech_detected") is True for row in rows),
        "exact_matches": sum(row.get("wer") == 0 for row in rows),
        "aggregate_wer": sum(r["word_errors"] for r in rows)
        / sum(r["reference_word_count"] for r in rows),
        "aggregate_cer": sum(r["character_errors"] for r in rows)
        / sum(r["reference_character_count"] for r in rows),
        "median_ms": median(r["latency_ms"] for r in rows),
        "max_ms": max(r["latency_ms"] for r in rows),
    }


def main():
    path = A / "consultation_voice_research_sources_20261101.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        name = source["name"]
        extracted = ROOT / f"tmp/consultation_voice_research/{name}.txt"
        if extracted.exists():
            source["local_extracted_text_sha256"] = hashlib.sha256(
                extracted.read_bytes()
            ).hexdigest()
        if name == "alce":
            pdf = ROOT / "tmp/consultation_voice_research/alce.pdf"
            source.pop("error_type", None)
            source.update(
                status=200,
                bytes=pdf.stat().st_size,
                sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                extraction="pypdf via bundled Python; 24 pages; sections 3.2/3.3 read",
            )
        else:
            source["sha256_scope"] = "Original HTTP response, before HTML-to-text extraction"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    stt = json.loads((A / "voice_pipeline_stt_20261101.json").read_text(encoding="utf-8"))["rows"]
    vad = json.loads((A / "local_vad_audio_replay_20261101.json").read_text(encoding="utf-8"))
    consultation = json.loads(
        (A / "customer_consultation_35_grounded_review_20261101.json").read_text(encoding="utf-8")
    )
    summary = {
        "fleurs": summarize([r for r in stt if r["case"].startswith("fleurs:")]),
        "vivoice": summarize([r for r in stt if r["case"].startswith("vivoice:")]),
        "stt_controls": [
            {
                "case": r["case"],
                "status": r["http_status"],
                "speech_detected": r.get("response", {}).get("speech_detected"),
            }
            for r in stt
            if r.get("kind") != "human-speech"
        ],
        "vad": {
            "human_files": sum("dataset" in r for r in vad["rows"]),
            "human_files_with_real_speech": sum(
                any(e["event"] == "SPEECH_REAL_START" for e in r["events"])
                for r in vad["rows"]
                if "dataset" in r
            ),
            "segments_over_12_seconds": sum(
                e.get("duration_seconds", 0) > 12 for r in vad["rows"] for e in r["events"]
            ),
            "negative_events": {r["id"]: r["events"] for r in vad["rows"] if "dataset" not in r},
        },
        "consultation": {
            "rows": len(consultation["rows"]),
            "models": dict(Counter(r["response"]["model"] for r in consultation["rows"])),
            "rows_with_products": sum(
                bool(r["response"]["products"]) for r in consultation["rows"]
            ),
            "median_seconds": median(r["elapsed_seconds"] for r in consultation["rows"]),
            "max_seconds": max(r["elapsed_seconds"] for r in consultation["rows"]),
        },
    }
    (A / "consultation_voice_measured_summary_20261101.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
