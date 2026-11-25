"""Real provider/API diagnostics. Exposed read speech and synthetic retail audio.

No room/microphone or subjective TTS quality claim is implied by decoding audio.
Run with --section stt, tts or e2e; each writes a separate auditable artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import random
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs/reports/artifacts"


def wav(samples):
    import array

    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(
            array.array("h", [int(max(-1, min(1, v)) * 32767) for v in samples]).tobytes()
        )
    return output.getvalue()


def decode(content):
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
            "pipe:1",
        ],
        input=content,
        capture_output=True,
        timeout=20,
        check=True,
    )
    with wave.open(io.BytesIO(result.stdout)) as audio:
        # ffmpeg WAV on a pipe can declare an unknown length; count decoded frames.
        frames = audio.readframes(16000 * 180)
        duration = len(frames) / (
            audio.getframerate() * audio.getnchannels() * audio.getsampwidth()
        )
    return result.stdout, duration


async def run(
    section, *, output: Path | None = None, base_url="http://127.0.0.1:8000",
    sample_manifest: Path | None = None,
):
    run_tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "section": section,
        "limitations": [
            "Exposed diagnostic samples, not a blind evaluation.",
            "API/audio replay does not test room microphone, speaker echo, "
            "or perceived voice quality.",
        ],
        "source_sha256": {
            file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
            for file in [
                "backend/services/groq.py",
                "backend/services/speech_activity.py",
                "backend/assets/silero/silero_vad_v6.onnx",
                "backend/services/speech_text.py",
                "backend/services/azure_speech.py",
                "backend/services/elevenlabs.py",
                "backend/services/speech_stream.py",
                "backend/services/speech_tickets.py",
                "backend/services/customer_consultation.py",
                "backend/api/main.py",
                "apps/kiosk-airi/src/App.vue",
                "apps/kiosk-airi/src/api.ts",
                "apps/kiosk-airi/src/voice-neural.ts",
                "apps/kiosk-airi/src/voice-controller.ts",
            ]
        },
        "rows": [],
    }
    output = (output or ARTIFACTS / f"voice_pipeline_{section}_{run_tag}.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    def save(row):
        report["rows"].append(row)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"response", "transcript", "reference", "reply"}
                },
                ensure_ascii=True,
            ),
            flush=True,
        )

    async with httpx.AsyncClient(base_url=base_url, timeout=65) as client:
        session = await client.post("/sessions", json={"kiosk_id": f"voice-audit-{section}"})
        session.raise_for_status()
        sid = session.json()["session_id"]

        async def transcribe(content, name="sample.wav", mime="audio/wav"):
            start = perf_counter()
            response = await client.post(
                "/audio/transcribe",
                data={"session_id": sid, "language": "vi"},
                files={"file": (name, content, mime)},
            )
            return {
                "http_status": response.status_code,
                "latency_ms": round((perf_counter() - start) * 1000),
                "response": response.json(),
            }

        async def speak(text, language, label):
            started = perf_counter()
            ticket = await client.post(
                "/audio/synthesize/tickets",
                json={"session_id": sid, "text": text, "language": language},
            )
            ticket.raise_for_status()
            url = ticket.json()["audio_url"]
            chunks = []
            first = None
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        if first is None:
                            first = round((perf_counter() - started) * 1000)
                        chunks.append(chunk)
                metadata = {
                    "provider": response.headers.get("x-tts-provider"),
                    "voice": response.headers.get("x-voice-id"),
                    "mime": response.headers.get("content-type"),
                }
            audio = b"".join(chunks)
            completed_ms = round((perf_counter() - started) * 1000)
            audio_path = output.parent / f"voice_audit_{label}_{run_tag}.mp3"
            audio_path.write_bytes(audio)
            decoded, duration = decode(audio)
            reuse = await client.get(url)
            return decoded, {
                **metadata,
                "audio_bytes": len(audio),
                "duration_seconds": round(duration, 3),
                "first_audio_ms_including_ticket": first,
                "complete_ms": completed_ms,
                "reuse_status": reuse.status_code,
                "audio_sha256": hashlib.sha256(audio).hexdigest(),
                "audio_path": str(audio_path.relative_to(ROOT)),
            }

        try:
            if section == "stt":
                manifest = json.loads(
                    (ROOT / ".tmp/fleurs_vi_test_samples/manifest.json").read_text(encoding="utf-8")
                )
                samples = [
                    (
                        f"fleurs:{s['filename']}",
                        ROOT / ".tmp/fleurs_vi_test_samples" / s["filename"],
                        s["reference"],
                        "google/fleurs vi_vn test; CC-BY-4.0",
                    )
                    for s in manifest["samples"]
                ]
                regional = json.loads(
                    (
                        ROOT / "evaluation/results/live_groq_stt_vivoice34_current_20261026.json"
                    ).read_text(encoding="utf-8")
                )
                samples.extend(
                    (
                        f"vivoice:{s['province']}",
                        ROOT / ".tmp/vivoice34_samples" / s["file"],
                        s["reference"],
                        "ViVoice34 train preview; CC-BY-4.0; province is not a verified accent",
                    )
                    for s in regional["results"]
                )
                extra_metadata = {}
                if sample_manifest:
                    content = sample_manifest.read_bytes()
                    extra = json.loads(content)
                    report["sample_manifest_sha256"] = hashlib.sha256(content).hexdigest()
                    report["sample_selection"] = extra["selection"]
                    report["limitations"].extend(extra["limitations"])
                    samples = [
                        (s["id"], ROOT / s["path"], s["reference"],
                         f"{s['source']}; {s['license']}; {s['split']}")
                        for s in extra["samples"]
                    ]
                    extra_metadata = {s["id"]: s for s in extra["samples"]}
                for label, file, reference, source in samples:
                    content = file.read_bytes()
                    metadata = extra_metadata.get(label, {})
                    row = {
                        "case": label,
                        "kind": metadata.get("kind", "human-speech"),
                        "source": source,
                        "reference": reference,
                        "duration_seconds": metadata.get("duration_seconds"),
                        "audio_sha256": hashlib.sha256(content).hexdigest(),
                        **await transcribe(content),
                    }
                    body = row["response"]
                    row.update(
                        {
                            "transcript": body.get("text", ""),
                            "model": body.get("model"),
                            "speech_detected": body.get("speech_detected"),
                        }
                    )
                    if row["kind"] == "human-speech":
                        row.update(accuracy(reference, body.get("text", "")))
                    save(row)
                rng = random.Random(42)
                for label, content in [
                    ("silence", wav([0.0] * 48000)),
                    (
                        "tone",
                        wav([0.15 * math.sin(i * 2 * math.pi * 440 / 16000) for i in range(48000)]),
                    ),
                    ("white-noise", wav([rng.uniform(-0.05, 0.05) for _ in range(48000)])),
                    ("empty", b""),
                    ("malformed", b"this is not audio"),
                ]:
                    save({"case": label, "kind": "negative-control", **await transcribe(content)})
                save({"case": "wrong-mime", **await transcribe(b"x", mime="text/plain")})
                save({"case": "oversized", **await transcribe(b"x" * (10 * 1024**2 + 1))})
            elif section == "tts":
                for language, text in [
                    (
                        "vi",
                        "Gói 300g giá 45.000đ, chai 1,5 lít giá 32.500đ. "
                        "A & B, nhỏ hơn < 5 và lớn hơn > 2.",
                    ),
                    (
                        "en",
                        "This bottle is 500 milliliters. Please check the price at the counter.",
                    ),
                    ("zh", "这瓶饮料是五百毫升，请在收银台确认价格。"),
                ]:
                    try:
                        _, row = await speak(text, language, language)
                        save({"case": language, "kind": "real-ticket-stream-decode", **row})
                    except Exception as exc:
                        save({"case": language, "error_type": type(exc).__name__})
                too_long = await client.post(
                    "/audio/synthesize/tickets",
                    json={
                        "session_id": sid,
                        "text": "Đọc thử một đoạn dài. " * 150,
                        "language": "vi",
                    },
                )
                save(
                    {
                        "case": "overlong-text",
                        "http_status": too_long.status_code,
                        "expected_status": 422,
                    }
                )
                ticket = await client.post(
                    "/audio/synthesize/tickets",
                    json={
                        "session_id": sid,
                        "text": "Đọc thử một đoạn dài. " * 60,
                        "language": "vi",
                    },
                )
                ticket.raise_for_status()
                async with client.stream("GET", ticket.json()["audio_url"]) as response:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            save(
                                {
                                    "case": "client-cancel-after-first-chunk",
                                    "http_status": response.status_code,
                                    "first_chunk_bytes": len(chunk),
                                    "upstream_cleanup": "See backend unit tests/logs; "
                                    "client close alone cannot prove release.",
                                }
                            )
                            break
            else:
                for index, question in enumerate(
                    [
                        "Mình muốn mua thịt để nhúng lẩu, khoảng hai trăm nghìn.",
                        "Có nước uống không đường nào dưới ba mươi nghìn không?",
                        "Mình cần pin cho điều khiển, chưa biết dùng cỡ nào.",
                        "Mình muốn mua sữa tắm cho da nhạy cảm.",
                    ]
                ):
                    try:
                        audio, synthesis = await speak(question, "vi", f"e2e_input_{index}")
                        stt = await transcribe(audio)
                        transcript = stt["response"].get("text", "")
                        if not stt["response"].get("speech_detected"):
                            save(
                                {
                                    "case": index,
                                    "kind": "synthetic-e2e",
                                    "reference": question,
                                    "stt": stt,
                                    "error": "speech_rejected",
                                }
                            )
                            continue
                        started = perf_counter()
                        response = await client.post(
                            "/agent/respond", json={"session_id": sid, "text": transcript}
                        )
                        response.raise_for_status()
                        body = response.json()
                        agent_ms = round((perf_counter() - started) * 1000)
                        _, tts = await speak(body["text"], "vi", f"e2e_reply_{index}")
                        save(
                            {
                                "case": index,
                                "kind": "synthetic-e2e",
                                "reference": question,
                                "transcript": transcript,
                                "stt_ms": stt["latency_ms"],
                                "agent_ms": agent_ms,
                                "reply": body,
                                "tts": tts,
                                **accuracy(question, transcript),
                            }
                        )
                        await asyncio.sleep(4)
                    except Exception as exc:
                        save({"case": index, "error_type": type(exc).__name__})
        finally:
            await client.post(f"/sessions/{sid}/close")
        report["complete"] = True
        report["source_sha256_at_end"] = {
            file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
            for file in report["source_sha256"]
        }
        report["source_stable_during_run"] = (
            report["source_sha256"] == report["source_sha256_at_end"]
        )
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=["stt", "tts", "e2e"], required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--sample-manifest", type=Path, help="STT only: replace default sample set")
    args = parser.parse_args()
    asyncio.run(run(
        args.section, output=args.output, base_url=args.base_url,
        sample_manifest=args.sample_manifest,
    ))
