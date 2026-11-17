"""Controlled tone-error research; does not change the live recognizer."""

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

from evaluation.vietnamese_tone_metrics import score

PHRASES = [
    "Em mua thịt về ăn lẩu.",
    "Em cần nước lau sàn.",
    "Em muốn mua sữa ít đường.",
    "Nhờ anh sửa đơn giúp em.",
    "Cho em một ít thịt bò.",
    "Bỏ món đó ra giúp em.",
    "Em muốn mua cá tươi.",
    "Em muốn mua cà chua.",
    "Em lấy hai túi gạo.",
    "Em muốn đổi sang loại khác.",
    "Một lít.",
    "Không đường.",
]
CONTEXT = (
    "Thịt bò, thịt heo, lẩu, sữa, nước giặt, dầu gội, nước lau sàn, cá, cà chua, "
    "gạo, khăn giấy, mì, bánh, ít đường, không đường, một lít."
)


def save(report: dict, path: Path):
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def audio_path(manifest: Path, case: dict) -> Path:
    path = Path(case["file"])
    return path if path.is_absolute() else manifest.parent / path


async def prepare(args):
    from backend.config import get_settings
    from backend.services.speech_activity import _decode_audio
    from backend.services.whisper_stt import _wav

    settings = get_settings()
    folder = args.manifest.parent
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    async with httpx.AsyncClient(timeout=25) as client:
        for voice in ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"):
            for index, reference in enumerate(PHRASES):
                name = f"{voice}-{index:02}"
                target = folder / f"{name}.wav"
                if not target.exists():
                    response = await client.post(
                        f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/"
                        "cognitiveservices/v1",
                        headers={
                            "Ocp-Apim-Subscription-Key": (
                                settings.azure_speech_key.get_secret_value()
                            ),
                            "Content-Type": "application/ssml+xml",
                            "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
                        },
                        content=(
                            '<speak version="1.0" xml:lang="vi-VN" '
                            'xmlns="http://www.w3.org/2001/10/synthesis">'
                            f'<voice name="{voice}">{escape(reference)}</voice></speak>'
                        ).encode(),
                    )
                    if response.status_code != 200:
                        raise RuntimeError(f"Synthesis HTTP {response.status_code}")
                    target.write_bytes(response.content)
                rows.append(
                    {
                        "id": name,
                        "kind": "synthetic-clean",
                        "file": target.name,
                        "reference": reference,
                        "speaker": voice,
                    }
                )
                if index in (0, 2, 4):
                    samples = _decode_audio(target.read_bytes(), time.perf_counter() + 5)
                    # Reproducible mechanical-noise proxy; not a room recording.
                    ticks = np.arange(len(samples)) / 16000
                    noise = (
                        np.random.default_rng(113).normal(0, 1, len(samples)) * 0.6
                        + np.sin(ticks * np.pi * 2 * 100)
                        + np.sin(ticks * np.pi * 2 * 200) * 0.3
                    )
                    noise *= np.sqrt(np.mean(samples**2)) / max(1e-6, np.sqrt(np.mean(noise**2)))
                    noisy = folder / f"{name}-noise10db.wav"
                    noisy.write_bytes(_wav(samples + noise / np.sqrt(10)))
                    rows.append(
                        {
                            "id": name + "-noise10db",
                            "kind": "synthetic-noise10db",
                            "file": noisy.name,
                            "reference": reference,
                            "speaker": voice,
                        }
                    )
    source = args.public_root / ".tmp/fleurs_vi_test_samples"
    for sample in json.loads((source / "manifest.json").read_text(encoding="utf-8"))["samples"]:
        rows.append(
            {
                "id": "fleurs-" + sample["filename"],
                "kind": "human-fleurs",
                "file": str((source / sample["filename"]).resolve()),
                "reference": sample["reference"],
                "speaker": sample.get("speaker_gender"),
            }
        )
    for index in (0, 1):
        file = args.noise_root / ".tmp/browser-qa/captures" / f"{index}-0.wav"
        if file.exists():
            rows.append(
                {
                    "id": "captured-nonspeech-" + str(index),
                    "kind": "nonspeech",
                    "file": str(file.resolve()),
                    "reference": "",
                }
            )
    silence = folder / "silence.wav"
    silence.write_bytes(_wav(np.zeros(32000, dtype=np.float32)))
    rows.append({"id": "silence", "kind": "nonspeech", "file": silence.name, "reference": ""})
    for row in rows:
        path = audio_path(args.manifest, row)
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(
        {
            "limitations": [
                "Synthetic cases are diagnostics, not proof of human speech accuracy.",
                "FLEURS is public read speech, not spontaneous retail speech.",
                "10 dB noise is an artificial proxy, not physical fan/microphone capture.",
            ],
            "cases": rows,
        },
        args.manifest,
    )
    print(json.dumps({"prepared": len(rows), "manifest": str(args.manifest)}))


async def remote(args):
    from backend.config import get_settings
    from backend.services.whisper_stt import WhisperSTT

    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    service, settings = WhisperSTT(), get_settings()
    output = {"mode": "raw-provider-comparison", "context": CONTEXT, "rows": []}
    async with httpx.AsyncClient(timeout=35) as client:
        for case in source["cases"]:
            content = audio_path(args.manifest, case).read_bytes()
            for model, prompted in (
                ("whisper-large-v3", False),
                ("whisper-large-v3", True),
                ("whisper-large-v3-turbo", False),
            ):
                variant = model + ("-context" if prompted else "-plain")
                data = {
                    "model": model,
                    "language": "vi",
                    "temperature": "0",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                }
                if prompted:
                    data["prompt"] = CONTEXT
                started = time.monotonic()
                row = {**case, "variant": variant}
                for key in service._keys():
                    try:
                        response = await client.post(
                            settings.groq_base_url.rstrip("/") + "/audio/transcriptions",
                            headers={"Authorization": "Bearer " + key},
                            files={"file": ("sample.wav", content, "audio/wav")},
                            data=data,
                        )
                    except httpx.HTTPError as error:
                        row["error_type"] = type(error).__name__
                        break
                    row["http_status"] = response.status_code
                    if response.status_code in (401, 403, 429):
                        continue
                    if response.status_code == 200:
                        payload = response.json()
                        row.update(
                            text=payload.get("text", ""), segments=payload.get("segments", [])
                        )
                        row["scores"] = score(case["reference"], row["text"])
                    break
                row["seconds"] = round(time.monotonic() - started, 3)
                output["rows"].append(row)
                save(output, args.output)
                print(
                    json.dumps(
                        {
                            "id": case["id"],
                            "variant": variant,
                            "text": row.get("text"),
                            "error": row.get("error_type"),
                            "http": row.get("http_status"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )


def local(args):
    from faster_whisper import WhisperModel

    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    model = WhisperModel(str(args.model_dir), device="cpu", compute_type="int8", cpu_threads=4)
    report = {"mode": "local-int8-comparison", "model": args.model_name, "rows": []}
    for case in source["cases"]:
        started = time.monotonic()
        segments, _ = model.transcribe(
            str(audio_path(args.manifest, case)),
            language="vi",
            task="transcribe",
            beam_size=5,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        row = {
            **case,
            "variant": args.model_name,
            "text": text,
            "scores": score(case["reference"], text),
            "seconds": round(time.monotonic() - started, 3),
        }
        report["rows"].append(row)
        save(report, args.output)
        print(
            json.dumps(
                {"id": case["id"], "variant": args.model_name, "text": text}, ensure_ascii=False
            ),
            flush=True,
        )


def official_phoasr(args):
    import torch
    from faster_whisper.audio import decode_audio
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    torch.set_num_threads(4)
    model_id = "Qualcomm-AI-Research/PhoASR-whisper-small"
    revision = "da2d9ae0ac0a6a6faba20add61c2e980ccd85037"
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        revision=revision,
        attn_implementation="eager",
    ).eval()
    processor = AutoProcessor.from_pretrained(model_id, revision=revision)
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = {
        "mode": "official-model-inference",
        "model": model_id,
        "revision": revision,
        "runtime": "transformers-4.57.6-cpu-float32",
        "rows": [],
    }
    for case in source["cases"]:
        started = time.monotonic()
        samples = decode_audio(str(audio_path(args.manifest, case)), sampling_rate=16000)
        inputs = processor(
            samples, sampling_rate=16000, return_tensors="pt", return_attention_mask=True
        )
        with torch.inference_mode():
            output = model.generate(
                input_features=inputs.input_features,
                attention_mask=inputs.attention_mask,
                num_frames=int(inputs.attention_mask.sum().item()),
                language="vi",
                task="transcribe",
                return_timestamps="word",
                return_token_timestamps=True,
                return_dict_in_generate=True,
            )
        text = processor.batch_decode(output["sequences"], skip_special_tokens=True)[0].strip()
        row = {
            **case,
            "variant": "PhoASR-whisper-small-official",
            "text": text,
            "scores": score(case["reference"], text),
            "seconds": round(time.monotonic() - started, 3),
        }
        report["rows"].append(row)
        save(report, args.output)
        print(
            json.dumps(
                {"id": case["id"], "text": text, "seconds": row["seconds"]}, ensure_ascii=False
            ),
            flush=True,
        )


async def pipeline(args):
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = {"mode": "running-http-api", "rows": []}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=35) as client:
        report["configuration"] = (await client.get("/audio/config")).json()
        session = (await client.post("/sessions", json={"kiosk_id": "tone-research"})).json()
        try:
            for case in source["cases"]:
                started = time.monotonic()
                response = await client.post(
                    "/audio/transcribe",
                    data={"session_id": session["session_id"], "language": "vi"},
                    files={
                        "file": (
                            "sample.wav",
                            audio_path(args.manifest, case).read_bytes(),
                            "audio/wav",
                        )
                    },
                )
                row = {
                    **case,
                    "http_status": response.status_code,
                    "seconds": round(time.monotonic() - started, 3),
                }
                if response.status_code == 200:
                    row["response"] = response.json()
                    row["text"] = row["response"].get("text", "")
                    row["scores"] = score(case["reference"], row["text"])
                report["rows"].append(row)
                save(report, args.output)
                print(
                    json.dumps(
                        {"id": case["id"], "status": response.status_code, "text": row.get("text")},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            await client.post(f"/sessions/{session['session_id']}/close")


async def azure(args):
    """Matched, research-only REST comparison; never change live ASR settings."""
    from backend.config import get_settings
    from backend.services.speech_activity import _decode_audio
    from backend.services.whisper_stt import _wav

    settings = get_settings()
    if not settings.has_azure_speech:
        raise RuntimeError("Azure speech is not configured")
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = {
        "mode": "raw-provider-comparison",
        "model": "azure-conversation-vi-VN",
        "limitations": [
            "Provider does not expose an immutable model revision.",
            "Synthetic voices come from Azure TTS; compare human rows separately.",
        ],
        "rows": [],
    }
    async with httpx.AsyncClient(timeout=35) as client:
        for case in source["cases"]:
            content = audio_path(args.manifest, case).read_bytes()
            samples = _decode_audio(content, time.perf_counter() + 5)
            started = time.monotonic()
            row = {**case, "variant": report["model"]}
            try:
                response = await client.post(
                    f"https://{settings.azure_speech_region}.stt.speech.microsoft.com/"
                    "speech/recognition/conversation/cognitiveservices/v1",
                    params={"language": "vi-VN", "format": "detailed"},
                    headers={
                        "Ocp-Apim-Subscription-Key": settings.azure_speech_key.get_secret_value(),
                        "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                    },
                    content=_wav(samples),
                )
                row["http_status"] = response.status_code
                if response.status_code == 200:
                    payload = response.json()
                    row["recognition_status"] = payload.get("RecognitionStatus")
                    candidates = payload.get("NBest", [])
                    best = candidates[0] if candidates else {}
                    row["text"] = best.get("Display", payload.get("DisplayText", ""))
                    row["provider_confidence"] = best.get("Confidence")
                    row["scores"] = score(case["reference"], row["text"])
            except httpx.HTTPError as error:
                row["error_type"] = type(error).__name__
            row["seconds"] = round(time.monotonic() - started, 3)
            report["rows"].append(row)
            save(report, args.output)
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "http": row.get("http_status"),
                        "status": row.get("recognition_status"),
                        "text": row.get("text"),
                        "error": row.get("error_type"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def prepare_browser(args):
    from backend.services.speech_activity import _decode_audio
    from backend.services.whisper_stt import _wav

    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    selected = {
        "vi-VN-HoaiMyNeural-00",
        "vi-VN-HoaiMyNeural-01",
        "vi-VN-HoaiMyNeural-02",
        "vi-VN-NamMinhNeural-00",
        "vi-VN-NamMinhNeural-01",
        "vi-VN-NamMinhNeural-03",
    }
    rows = []
    for case in source["cases"]:
        if case["id"] not in selected:
            continue
        samples = _decode_audio(
            audio_path(args.manifest, case).read_bytes(), time.perf_counter() + 5
        )
        padded = np.concatenate((np.zeros(48000), samples, np.zeros(32000)))
        path = args.output.parent / (case["id"] + "-fake-mic.wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _wav(padded)
        path.write_bytes(content)
        rows.append(
            {
                **case,
                "file": str(path.resolve()),
                "original_file": str(audio_path(args.manifest, case).resolve()),
                "original_sha256": case["sha256"],
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    save({"physical_microphone": False, "cases": rows}, args.output)
    print(json.dumps({"prepared": len(rows)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=["prepare", "remote", "local", "pipeline", "phoasr", "azure", "prepare-browser"],
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--public-root", type=Path)
    parser.add_argument("--noise-root", type=Path, default=Path.cwd())
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--model-name")
    arguments = parser.parse_args()
    if arguments.stage == "prepare":
        asyncio.run(prepare(arguments))
    elif arguments.stage == "remote":
        asyncio.run(remote(arguments))
    elif arguments.stage == "pipeline":
        asyncio.run(pipeline(arguments))
    elif arguments.stage == "phoasr":
        official_phoasr(arguments)
    elif arguments.stage == "azure":
        asyncio.run(azure(arguments))
    elif arguments.stage == "prepare-browser":
        prepare_browser(arguments)
    else:
        local(arguments)
