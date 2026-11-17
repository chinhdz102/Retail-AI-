"""Bounded local API latency probe; no LLM calls or physical microphone claims."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import wave
from pathlib import Path

import httpx


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/reports/artifacts/voice_latency_20261115.json")
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    output = root / args.output
    report = {"physical_microphone": False, "stt": [], "tts": []}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=35) as client:
        report["config"] = (await client.get("/audio/config")).json()
        session = await client.post("/sessions", json={"kiosk_id": "latency-audit"})
        session.raise_for_status()
        session_id = session.json()["session_id"]
        try:
            if args.prepare:
                started = time.perf_counter()
                ready = await client.post("/audio/prepare", json={"session_id": session_id})
                report["prepare"] = {"status": ready.status_code,
                    "ms": round((time.perf_counter() - started) * 1000)}
            for name in ["vi-VN-NamMinhNeural-00-raw.wav", "vi-VN-NamMinhNeural-01-raw.wav"]:
                path = root / "docs/reports/artifacts/stt-input-captures" / name
                audio = path.read_bytes()
                with wave.open(io.BytesIO(audio)) as wav:
                    duration = wav.getnframes() / wav.getframerate()
                started = time.perf_counter()
                response = await client.post("/audio/transcribe",
                    data={"session_id": session_id, "language": "vi"},
                    files={"file": ("voice.wav", audio, "audio/wav")})
                row = {"fixture": name, "synthetic_voice": True,
                       "audio_seconds": round(duration, 3), "status": response.status_code,
                       "api_ms": round((time.perf_counter() - started) * 1000)}
                if response.is_success:
                    payload = response.json()
                    row.update(text=payload["text"], speech_detected=payload["speech_detected"])
                    row["stage_ms"] = {
                        key: round(value, 1) for key, value in payload.get("quality", {}).items()
                        if key.startswith("stt_") and key.endswith("_ms")}
                report["stt"].append(row)
            for text in ["Mình đang nghe, bạn cứ nói tự nhiên nhé.",
                         "Bạn có thể chọn thịt bò thái mỏng hoặc thịt ba chỉ để nhúng lẩu. "
                         "Bạn thích thịt bò hay thịt heo hơn để mình gợi ý tiếp nhé?"]:
                started = time.perf_counter()
                ticket = await client.post("/audio/synthesize/tickets", json={
                    "session_id": session_id, "text": text, "language": "vi"})
                ticket.raise_for_status()
                ticket_ms = (time.perf_counter() - started) * 1000
                first_ms, count, size = None, 0, 0
                async with client.stream("GET", ticket.json()["audio_url"]) as response:
                    status = response.status_code
                    headers = {key: value for key, value in response.headers.items()
                               if key in {"content-type", "content-length", "transfer-encoding",
                                          "x-tts-provider"}}
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        if first_ms is None:
                            first_ms = round((time.perf_counter() - started) * 1000)
                        count += 1
                        size += len(chunk)
                report["tts"].append({"characters": len(text), "status": status,
                    "ticket_ms": round(ticket_ms), "first_byte_ms": first_ms,
                    "complete_ms": round((time.perf_counter() - started) * 1000),
                    "network_chunks": count, "bytes": size, "headers": headers})
        finally:
            await client.post(f"/sessions/{session_id}/close")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
