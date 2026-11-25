"""Paired exposed Vietnamese diagnostic; no English or accuracy marketing claim."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services import groq as groq_module
from backend.services.azure_speech import AzureSpeechService
from backend.services.groq import GroqService
from evaluation.audit_stt_regression import vietnamese_samples
from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]
COMPACT_PROMPT = "Hội thoại tiếng Việt tại cửa hàng. {catalog_terms}"
RETAIL = [
    "Em muốn mua thịt bò để nhúng lẩu, đừng lấy thịt heo nhé.",
    "Cho mình sữa không đường, chai một lít, chứ không phải một trăm mi li lít.",
    "Nước giặt OMO với Ariel loại nào đáng tiền hơn?",
    "Em chỉ có một trăm năm mươi nghìn, cần dầu gội chứ không phải sữa tắm.",
    "Da em nhạy cảm, em không cần loại có mùi nồng.",
    "Anh tư vấn giúp em với, em chưa biết chọn loại nào.",
]


async def run(args):
    if args.output.exists():
        raise ValueError("Preserve previous evidence; choose a new output")
    settings = Settings()
    catalog = get_product_repository(settings)
    vocabulary = catalog.list_speech_terms(8)
    cloud = GroqService(settings.model_copy(update={"groq_stt_fallback_model": ""}))
    local = GroqService(
        settings.model_copy(
            update={
                "vietnamese_asr_backend": "zipformer",
                "vietnamese_asr_model_dir": str(args.model_dir),
            }
        )
    )
    samples = vietnamese_samples()
    cache = ROOT / ".tmp/vi_asr_20261105/retail_synthetic"
    cache.mkdir(parents=True, exist_ok=True)
    azure = AzureSpeechService(settings)
    for i, text in enumerate(RETAIL):
        file = cache / f"retail_{i}.mp3"
        if not file.exists():
            audio = await azure.synthesize(text, "vi")
            file.write_bytes(audio.content)
        samples.append(
            {
                "id": f"retail-synthetic:{i}",
                "path": str(file),
                "reference": text,
                "kind": "synthetic-retail",
                "source": "Azure vi-VN-HoaiMyNeural TTS",
                "license": "locally generated diagnostic",
            }
        )
    controls = json.loads(
        (ROOT / ".tmp/voice_diagnostics_20261103/manifest.json").read_text(encoding="utf-8")
    )["samples"]
    samples += [s for s in controls if s["kind"] == "environmental-control"]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "selection": "All 22 cached Vietnamese human clips + six predeclared TTS retail "
        "sentences + all five cached ESC-50 controls; no selection by ASR output.",
        "limitations": [
            "Exposed diagnostic, not held out or a microphone field test.",
            "FLEURS/ViMD may overlap Zipformer training.",
            "Synthetic speech is not evidence of customer accent robustness.",
            "Whitespace WER uses the repository's existing number normalization; "
            "inspect negation, units, money and brand errors separately.",
        ],
        "model_dir": str(args.model_dir),
        "rows": [],
        "source_sha256": {
            f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
            for f in (
                "backend/services/groq.py",
                "backend/services/vietnamese_asr.py",
                "backend/services/speech_activity.py",
                "evaluation/compare_vietnamese_asr.py",
            )
        },
        "prompts": {"baseline": groq_module.RETAIL_STT_PROMPTS["vi"], "compact": COMPACT_PROMPT},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("{}", encoding="utf-8")
    baseline = groq_module.RETAIL_STT_PROMPTS["vi"]
    try:
        for sample in samples:
            content = (ROOT / sample["path"]).read_bytes()
            row = {**sample, "sha256": hashlib.sha256(content).hexdigest(), "variants": {}}
            # Alternate cloud order to reduce a simple order/provider-time bias.
            variants = ["baseline", "compact"]
            if len(report["rows"]) % 2:
                variants.reverse()
            for name in ["zipformer", *variants]:
                start = perf_counter()
                service = local if name == "zipformer" else cloud
                groq_module.RETAIL_STT_PROMPTS["vi"] = (
                    COMPACT_PROMPT if name == "compact" else baseline
                )
                try:
                    value = await asyncio.wait_for(
                        service.transcribe(
                            "sample.wav",
                            content,
                            "audio/mpeg" if sample["path"].endswith(".mp3") else "audio/wav",
                            "vi",
                            vocabulary=vocabulary,
                        ),
                        timeout=25,
                    )
                    outcome = {
                        "text": str(value),
                        "model": value.model,
                        "speech_detected": value.speech_detected,
                        "quality": value.quality,
                        "trace": value.fallback_trace,
                    }
                    if sample["reference"]:
                        outcome.update(accuracy(sample["reference"], str(value)))
                except Exception as exc:
                    outcome = {"error_type": type(exc).__name__}
                outcome["seconds"] = round(perf_counter() - start, 3)
                row["variants"][name] = outcome
                if name != "zipformer":
                    await asyncio.sleep(1)
            report["rows"].append(row)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "id": sample["id"],
                        "variants": {
                            name: {
                                k: v
                                for k, v in outcome.items()
                                if k in ("wer", "seconds", "error_type")
                            }
                            for name, outcome in row["variants"].items()
                        },
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
    finally:
        groq_module.RETAIL_STT_PROMPTS["vi"] = baseline
    report["complete"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
