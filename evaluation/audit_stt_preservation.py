"""Matched Vietnamese STT comparisons with development/held-out cases and hashes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import numpy as np

from backend.config import get_settings
from backend.services.speech_activity import analyze_speech_audio
from backend.services.whisper_stt import WhisperSTT, _prepare_decode_audio, _wav, evaluate_transcript
from evaluation.vietnamese_tone_metrics import score

PRIMERS = {
    "plain": "",
    "generic": "Cuộc trò chuyện bằng tiếng Việt ở cửa hàng.",
    "shopping": (
        "Cửa hàng bán thực phẩm, thịt, cá, rau, sữa, nước uống, bánh, gạo, "
        "nước giặt, nước lau sàn, dầu gội, sữa tắm, băng vệ sinh và đồ dùng gia đình. "
        "Tư vấn giá tiền, số lượng và dung tích."
    ),
}
PHRASES = [
    ("dev", "Mình cần nước lau sàn, không phải nước giặt."),
    ("dev", "Cho mình thịt bò để nhúng lẩu."),
    ("dev", "Không lấy sữa có đường, đổi sang loại không đường."),
    ("dev", "Mình lấy hai gói, chứ không phải cả thùng."),
    ("holdout", "Chai lớn một lít, không lấy hai chai nhỏ."),
    ("holdout", "Cho mình nửa cân thịt ba chỉ heo."),
    ("holdout", "Bỏ bánh bao ra, mình muốn mua thịt tươi."),
    ("holdout", "Có loại dầu gội nào cho tóc dầu dưới một trăm nghìn không?"),
    ("holdout", "Mình cần băng vệ sinh ban đêm dài ba mươi lăm xen ti mét."),
    ("holdout", "Nhà có gạo rồi, mua đồ ăn cho hai người thôi."),
    ("holdout", "Hủy món lúc nãy, mình chỉ lấy hai hộp sữa."),
    ("holdout", "Có loại trà không đường, không có ga không?"),
]


def save(path, data):
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def prepare(args):
    settings = get_settings()
    folder = args.manifest.parent
    folder.mkdir(exist_ok=True, parents=True)
    rows = []
    async with httpx.AsyncClient(timeout=30) as client:
        for voice in ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"):
            for index, (split, text) in enumerate(PHRASES):
                path = folder / f"{voice}-{index:02}.wav"
                if not path.exists():
                    response = await client.post(
                        f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/"
                        "cognitiveservices/v1",
                        headers={
                            "Ocp-Apim-Subscription-Key": settings.azure_speech_key.get_secret_value(),
                            "Content-Type": "application/ssml+xml",
                            "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
                        },
                        content=(
                            '<speak version="1.0" xml:lang="vi-VN" '
                            'xmlns="http://www.w3.org/2001/10/synthesis">'
                            f'<voice name="{voice}">{escape(text)}</voice></speak>'
                        ).encode(),
                    )
                    if response.status_code != 200:
                        raise RuntimeError(f"Synthesis HTTP {response.status_code}")
                    path.write_bytes(response.content)
                rows.append({"id": path.stem, "split": split, "file": str(path.resolve()),
                             "reference": text, "kind": "synthetic-clean", "voice": voice})
                if split == "holdout" and index in (4, 6, 9):
                    analyzed = analyze_speech_audio(path.read_bytes())
                    samples = analyzed.samples
                    ticks = np.arange(len(samples)) / 16000
                    noise = np.random.default_rng(731).normal(0, .6, len(samples))
                    noise += np.sin(ticks * 2 * np.pi * 100)
                    noise += .3 * np.sin(ticks * 2 * np.pi * 200)
                    noise *= np.sqrt(np.mean(samples * samples)) / max(
                        1e-6, np.sqrt(np.mean(noise * noise))) * 10 ** (-5 / 20)
                    noisy = folder / (path.stem + "-fan5db.wav")
                    noisy.write_bytes(_wav((samples + noise).astype(np.float32)))
                    rows.append({**rows[-1], "id": noisy.stem, "file": str(noisy.resolve()),
                                 "kind": "synthetic-fan5db"})
        old = Path(".tmp/tone-research-20261110/cases/manifest.json")
        for item in json.loads(old.read_text(encoding="utf-8"))["cases"]:
            if item["kind"] in {"human-fleurs", "nonspeech"}:
                path = Path(item["file"])
                path = path if path.is_absolute() else old.parent / path
                rows.append({**item, "file": str(path.resolve()), "split": item["kind"]})
    for row in rows:
        row["sha256"] = hashlib.sha256(Path(row["file"]).read_bytes()).hexdigest()
    save(args.manifest, {"physical_microphone": False, "cases": rows,
                         "noise": "Reproducible mechanical proxy, not a room recording"})
    print(json.dumps({"prepared": len(rows)}, ensure_ascii=False))


async def compare(args):
    settings, service = get_settings(), WhisperSTT()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = {"physical_microphone": False, "primers": PRIMERS, "rows": [], "complete": False}
    cases = [r for r in manifest["cases"] if r["split"] in args.splits.split(",")]
    async with httpx.AsyncClient(timeout=30) as client:
        for item in cases:
            content = Path(item["file"]).read_bytes()
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            analyzed = analyze_speech_audio(content)
            wav, gain = _prepare_decode_audio(analyzed.samples)
            for variant in args.variants.split(","):
                row = {**item, "variant": variant, "provider_calls": 0}
                start = time.monotonic()
                if not analyzed.activity.speech_detected:
                    row.update(text="", accepted=False, rejection_reason="no_speech")
                else:
                    data = {"model": "whisper-large-v3", "language": "vi", "temperature": "0",
                            "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
                    if PRIMERS[variant]:
                        data["prompt"] = PRIMERS[variant]
                    for key in service._keys():
                        row["provider_calls"] += 1
                        try:
                            response = await client.post(
                                settings.groq_base_url.rstrip("/") + "/audio/transcriptions",
                                headers={"Authorization": "Bearer " + key},
                                files={"file": ("voice.wav", wav, "audio/wav")}, data=data)
                        except httpx.HTTPError as error:
                            row["error_type"] = type(error).__name__
                            break
                        row["http_status"] = response.status_code
                        if response.status_code in {401, 403, 429}:
                            continue
                        if response.status_code == 200:
                            result = evaluate_transcript(response.json(), {
                                **analyzed.activity.quality, "whisper_input_gain": gain})
                            row.update(text=result.text, accepted=result.speech_detected,
                                       rejection_reason=result.rejection_reason, quality=result.quality)
                        break
                row["seconds"] = round(time.monotonic() - start, 3)
                if "text" in row:
                    row["scores"] = score(item["reference"], row["text"])
                report["rows"].append(row)
                save(args.output, report)
                print(json.dumps({k:row.get(k) for k in ["id", "variant", "text", "http_status",
                                                       "rejection_reason"]}, ensure_ascii=False), flush=True)
    report["complete"] = len(report["rows"]) == len(cases) * len(args.variants.split(","))
    save(args.output, report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "compare"])
    parser.add_argument("--manifest", type=Path, default=Path(
        ".tmp/voice-preservation-20261110/asr-manifest.json"))
    parser.add_argument("--splits", default="dev")
    parser.add_argument("--variants", default="plain,generic,shopping")
    parser.add_argument("--output", type=Path, default=Path(
        "docs/reports/artifacts/stt_preservation_development_20261110.json"))
    args = parser.parse_args()
    asyncio.run(prepare(args) if args.mode == "prepare" else compare(args))
