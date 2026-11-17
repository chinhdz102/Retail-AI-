"""Compare known WAV, provider text, and actual HTTP result without rewriting speech."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx

from evaluation.vietnamese_tone_metrics import syllables


async def main(args):
    sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    folder = root / ".tmp/tone-research-20261110/cases"
    cases = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["cases"]
    selected = [row for row in cases if row["id"] in {
        "vi-VN-HoaiMyNeural-00", "vi-VN-NamMinhNeural-00",
        "vi-VN-HoaiMyNeural-00-noise10db", "vi-VN-NamMinhNeural-00-noise10db",
        "vi-VN-HoaiMyNeural-01", "vi-VN-NamMinhNeural-01",
    }]
    report = {"physical_microphone": False, "rows": []}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=40) as client:
        session = (await client.post("/sessions", json={})).json()["session_id"]
        try:
            for case in selected:
                content = (folder / case["file"]).read_bytes()
                response = await client.post("/audio/transcribe", data={"session_id": session},
                                             files={"file": ("voice.wav", content, "audio/wav")})
                response.raise_for_status()
                result = response.json()
                trace_file = root / ".tmp/stt-diagnostics" / result["request_id"] / "trace.json"
                trace = json.loads(trace_file.read_text(encoding="utf-8"))
                provider = trace["provider_response"]
                if isinstance(provider.get("text"), str):
                    raw_text = provider["text"]
                elif provider.get("RecognitionStatus") == "Success" and provider.get("NBest"):
                    raw_text = provider["NBest"][0]["Display"]
                else:
                    raise ValueError("Trace has no successful decoder transcript")
                row = {"id": case["id"], "reference": case["reference"], "result": result,
                       "provider_text": raw_text,
                       "uploaded_hash_matches": trace["uploaded_sha256"] == hashlib.sha256(
                           content).hexdigest(),
                       "provider_to_api_same_tones": syllables(raw_text) == syllables(
                           result["text"]),
                       "exact_reference": syllables(result["text"]) == syllables(
                           case["reference"])}
                report["rows"].append(row)
        finally:
            await client.post(f"/sessions/{session}/close")
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
