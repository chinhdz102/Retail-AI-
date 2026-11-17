"""Replay public files and actual browser-captured WAVs through the running API.

This makes real provider calls. No physical microphone is opened. References
are evaluation labels only; they never enter an STT request or a prompt.
"""

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import httpx

from evaluation.audit_whisper_vi import cases
from evaluation.live_groq_stt_benchmark import accuracy


def inputs(root, capture_report):
    yield from cases(root)
    capture = json.loads(capture_report.read_text(encoding="utf-8"))
    for row in capture["rows"]:
        for item in row["captures"]:
            content = Path(item["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("Captured WAV changed since the browser test")
            yield (f"{row['id']} x{row['gain']}", "browser-" + row["kind"], content,
                   row["id"] if "short-vietnamese" in row["kind"] else None)
    for directory in ("fleurs_vi_test_samples", "vietsuperspeech_dev_samples"):
        folder = root / ".tmp" / directory
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["samples"]:
            yield item["filename"], "additional-" + directory, (
                folder / item["filename"]).read_bytes(), item["reference"]


async def run(args):
    report = {"physical_microphone": False, "rows": [], "complete": False}
    async with httpx.AsyncClient(base_url=args.api_base, timeout=40) as client:
        config = await client.get("/audio/config")
        config.raise_for_status()
        report["runtime"] = config.json()
        report["catalog_lookup"] = (await client.get(
            "/products/search", params={"q": "10005885", "limit": 1})).json()
        session = (await client.post("/sessions", json={"kiosk_id": "voice-followup-audit"}))
        session.raise_for_status()
        session_id = session.json()["session_id"]
        try:
            for name, kind, content, reference in inputs(args.source_root, args.capture_report):
                started = perf_counter()
                response = await client.post(
                    "/audio/transcribe", data={"session_id": session_id, "language": "vi"},
                    files={"file": ("voice.wav", content, "audio/wav")},
                )
                row = {"id": name, "kind": kind, "sha256": hashlib.sha256(content).hexdigest(),
                       "http_status": response.status_code, "result": response.json(),
                       "elapsed_ms": (perf_counter() - started) * 1000}
                if reference and response.is_success:
                    row["reference"] = reference
                    row["accuracy"] = accuracy(reference, row["result"]["text"])
                report["rows"].append(row)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                print(json.dumps({"id": name, "kind": kind, "status": response.status_code,
                                  "text": row["result"].get("text"),
                                  "reason": row["result"].get("rejection_reason")}), flush=True)
        finally:
            await client.post(f"/sessions/{session_id}/close")
    summary = defaultdict(lambda: {"total": 0, "accepted": 0, "empty": 0, "errors": 0})
    for row in report["rows"]:
        group = summary[row["kind"]]
        group["total"] += 1
        group["errors"] += row["http_status"] != 200
        group["accepted"] += bool(row["result"].get("text"))
        group["empty"] += row["http_status"] == 200 and not row["result"].get("text")
    report["summary"] = dict(summary)
    report["complete"] = all(row["http_status"] == 200 for row in report["rows"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--capture-report", type=Path, required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
