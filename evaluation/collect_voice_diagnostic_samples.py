"""Cache a small, fixed, public audio diagnostic; never use it as blind evaluation.

Audio remains in .tmp and is not redistributed. ViMD: CC-BY-NC-ND-4.0;
ESC-50: CC-BY-NC-3.0. This is noncommercial research/test input only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import wave
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".tmp/voice_diagnostics_20261103"


def collect() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest_path = CACHE / "manifest.json"
    if manifest_path.exists():
        print("Using existing manifest", manifest_path, flush=True)
        return
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "selection": "First three 20-85-character transcripts from ViMD test preview rows 0-99; "
        "first fold-5 ESC-50 filename per listed category. No selection by ASR result.",
        "limitations": [
            "Small exposed diagnostic, not blind or representative of kiosk customers.",
            "ViMD preview here is North only; no claim to cover 63 dialects.",
            "ESC categories suggest non-speech; no independent listening annotation.",
            "Audio is cached locally only; preserve original dataset licenses and attribution.",
        ],
        "samples": [],
    }
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        response = client.get(
            "https://datasets-server.huggingface.co/rows",
            params={"dataset": "nguyendv02/ViMD_Dataset", "config": "default", "split": "test",
                    "offset": 0, "length": 100},
        )
        response.raise_for_status()
        preview = response.json()
        selected = [row["row"] for row in preview["rows"]
                    if 20 <= len(row["row"]["text"]) <= 85][:3]
        for row in selected:
            response = client.get(row["audio"][0]["src"])
            response.raise_for_status()
            name = f"vimd_{row['filename']}"
            (CACHE / name).write_bytes(response.content)
            with wave.open(io.BytesIO(response.content), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            manifest["samples"].append({
                "id": f"vimd:{row['filename']}", "kind": "human-speech",
                "path": str((CACHE / name).relative_to(ROOT)),
                "reference": row["text"], "region": row["region"],
                "speaker_id": row["speakerID"], "province": row["province_name"],
                "source": "https://huggingface.co/datasets/nguyendv02/ViMD_Dataset",
                "license": "CC-BY-NC-ND-4.0", "split": "test preview",
                "duration_seconds": duration,
                "sha256": hashlib.sha256(response.content).hexdigest(),
            })
        base = "https://raw.githubusercontent.com/karolpiczak/ESC-50/master"
        response = client.get(f"{base}/meta/esc50.csv")
        response.raise_for_status()
        metadata = list(csv.DictReader(io.StringIO(response.text)))
        for category in [
            "dog", "door_wood_knock", "keyboard_typing", "vacuum_cleaner", "clock_alarm",
        ]:
            row = next(r for r in metadata if r["category"] == category and r["fold"] == "5")
            name = row["filename"]
            response = client.get(f"{base}/audio/{name}")
            response.raise_for_status()
            (CACHE / name).write_bytes(response.content)
            manifest["samples"].append({
                "id": f"esc50:{category}:{name}", "kind": "environmental-control",
                "path": str((CACHE / name).relative_to(ROOT)), "reference": "",
                "category": category, "original_source_id": row["src_file"],
                "source": "https://github.com/karolpiczak/ESC-50",
                "license": "CC-BY-NC-3.0", "split": "fold 5", "duration_seconds": 5.0,
                "sha256": hashlib.sha256(response.content).hexdigest(),
            })
            print(category, name, flush=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Cached", len(manifest["samples"]), "samples", manifest_path, flush=True)


if __name__ == "__main__":
    collect()
