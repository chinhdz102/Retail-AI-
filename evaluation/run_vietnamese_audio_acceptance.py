"""Replay Vietnamese files through the actual endpoint, including no-speech controls.

This is an exposed file replay, not a physical microphone or speaker test.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "backend/api/main.py",
    "backend/services/groq.py",
    "backend/services/vietnamese_asr.py",
    "backend/services/asr_disagreement.py",
    "backend/services/catalog_transcription.py",
    "backend/services/speech_activity.py",
    "backend/services/speech_upload.py",
    "backend/config.py",
    "scripts/start_demo.ps1",
)


def hashes():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in SOURCES}


async def run(args):
    if args.output.exists():
        raise ValueError("Preserve existing evidence; choose a new output")
    paired = json.loads(
        (ROOT / "docs/reports/artifacts/vietnamese_asr_paired_20261105.json").read_text(
            encoding="utf-8"
        )
    )
    human_ids = {"fleurs:10057024225045006889.wav", "vivoice:Nghệ An", "vimd:11_0309.wav"}
    samples = [
        r
        for r in paired["rows"]
        if r["id"] in human_ids or r.get("kind") in {"synthetic-retail", "environmental-control"}
    ]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "selection": "Six predeclared retail TTS sentences, all five ESC-50 controls, "
        "one existing FLEURS, Nghệ An and first ViMD clip. Exposed development replay.",
        "limitations": "No physical mic, room, browser VAD or loudspeaker is exercised. "
        "WER formatting is imperfect; inspect brand, negation and amount separately.",
        "scheduled": len(samples),
        "base_url": args.base_url,
        "source_hashes_before": hashes(),
        "rows": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    save()
    async with httpx.AsyncClient(base_url=args.base_url, timeout=35) as client:
        response = await client.post("/sessions", json={"kiosk_id": "vietnamese-audio-replay"})
        response.raise_for_status()
        session = response.json()["session_id"]
        for sample in samples:
            path = ROOT / sample["path"]
            content = path.read_bytes()
            row = {k: sample[k] for k in ("id", "reference", "source", "license", "path")}
            row["sha256"] = hashlib.sha256(content).hexdigest()
            start = perf_counter()
            try:
                response = await client.post(
                    "/audio/transcribe",
                    data={
                        "session_id": session,
                        "language": "vi",
                        "include_diagnostics": "true",
                    },
                    files={
                        "file": (
                            path.name,
                            content,
                            "audio/mpeg" if path.suffix == ".mp3" else "audio/wav",
                        )
                    },
                )
                row["http_status"] = response.status_code
                row["response"] = response.json()
                if response.status_code == 200 and sample["reference"]:
                    row.update(accuracy(sample["reference"], row["response"]["text"]))
            except (httpx.HTTPError, ValueError) as exc:
                row["error_type"] = type(exc).__name__
            row["seconds"] = round(perf_counter() - start, 3)
            report["rows"].append(row)
            save()
            print(
                json.dumps(
                    {"id": row["id"], "status": row.get("http_status"), "seconds": row["seconds"]},
                    ensure_ascii=True,
                ),
                flush=True,
            )
            await asyncio.sleep(1)
        start = perf_counter()
        response = await client.post(
            "/audio/synthesize",
            json={
                "session_id": session,
                "language": "vi",
                "text": "Mình chọn loại không đường. Chai một lít giá hai mươi nghìn đồng.",
            },
        )
        report["tts"] = {
            "http_status": response.status_code,
            "bytes": len(response.content),
            "seconds": round(perf_counter() - start, 3),
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k.startswith("x-") or k == "content-type"
            },
        }
        if response.status_code == 200:
            target = args.output.resolve().with_name(args.output.stem + "_tts.mp3")
            with target.open("xb") as audio_file:
                audio_file.write(response.content)
            report["tts"]["path"] = str(target.relative_to(ROOT))
            report["tts"]["sha256"] = hashlib.sha256(response.content).hexdigest()
        else:
            report["tts"]["error"] = response.text
    report["complete"] = len(report["rows"]) == len(samples)
    report["source_hashes_after"] = hashes()
    report["source_unchanged"] = report["source_hashes_before"] == report["source_hashes_after"]
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
