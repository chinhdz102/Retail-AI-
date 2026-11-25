"""Verify localhost VAD assets by bytes, not an HTTP 200 SPA fallback."""

import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
public = ROOT / "apps/kiosk-airi/public"
rows = []
with httpx.Client(base_url="http://127.0.0.1:5173", timeout=30) as client:
    for file in (public / "vad").rglob("*"):
        if not file.is_file():
            continue
        relative = file.relative_to(public).as_posix()
        response = client.get("/" + relative)
        rows.append(
            {
                "path": relative,
                "status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "bytes": len(response.content),
                "matches_local": response.content == file.read_bytes(),
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
        )
Path("docs/reports/artifacts/voice_asset_delivery_20261101.json").write_text(
    json.dumps(rows, indent=2), encoding="utf-8"
)
print(json.dumps({"assets": len(rows), "all_match": all(row["matches_local"] for row in rows)}))
