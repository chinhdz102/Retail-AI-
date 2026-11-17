"""Evaluate one-decoder Vietnamese Azure STT with production acoustic checks."""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from backend.services.azure_stt import AzureVietnameseSTT
from backend.services.speech_activity import _decode_audio
from backend.services.whisper_stt import _wav
from evaluation.vietnamese_tone_metrics import score


async def run(args):
    source = json.loads(args.manifest.read_text(encoding="utf8"))
    report = {"model": "azure-conversation-vi-VN", "physical_microphone": False,
              "input_manifest": str(args.manifest), "rows": [], "complete": False}
    service = AzureVietnameseSTT()
    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf8"))
        if previous.get("input_manifest") != str(args.manifest):
            raise ValueError("Output belongs to another manifest")
        report["rows"] = previous["rows"]
    completed = {(row["id"], row["level"]) for row in report["rows"]}
    for item in source["cases"]:
        path = Path(item["file"])
        path = path if path.is_absolute() else args.manifest.parent / path
        original = path.read_bytes()
        for level in args.levels.split(","):
            if (item["id"], float(level)) in completed:
                continue
            content = original
            if float(level) != 1:
                content = _wav(_decode_audio(original, time.perf_counter() + 5) * float(level))
            row = {**item, "level": float(level), "sha256": hashlib.sha256(content).hexdigest()}
            start = time.monotonic()
            try:
                result = await service.transcribe(content)
                row.update(text=result.text, accepted=result.speech_detected,
                           rejection_reason=result.rejection_reason, quality=result.quality,
                           scores=score(item["reference"], result.text))
            except Exception as error:
                row["error_type"] = type(error).__name__
            row["seconds"] = round(time.monotonic() - start, 3)
            report["rows"].append(row)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            persist(args.output, report)
            print(json.dumps({k: row.get(k) for k in ["id", "level", "text", "rejection_reason",
                                                    "error_type"]}, ensure_ascii=False), flush=True)
    report["complete"] = True
    persist(args.output, report)


def persist(path, report):
    data = json.dumps(report, ensure_ascii=False, indent=2)
    for attempt in range(3):
        try:
            path.write_text(data, encoding="utf8")
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(.1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--levels", default="1")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
