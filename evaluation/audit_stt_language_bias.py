"""Measure UI-language forcing and prompt bias on the same public VI/EN audio.

This diagnostic deliberately includes a wrong-language condition. Its text is
recorded before the existing quality gate, along with the gate's decision.
"""

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
from backend.services.groq import (
    GROQ_STT_CATALOG_CONTEXT_TERMS,
    GroqService,
    _transcription_prompt,
    _transcription_quality,
)
from evaluation.audit_stt_regression import english_samples, vietnamese_samples
from evaluation.live_groq_stt_benchmark import accuracy

MODES = ("selected_language", "auto_no_prompt", "auto_ui_prompt", "force_vi")


async def run(output: Path, modes: list[str]) -> None:
    settings = Settings()
    catalog = get_product_repository(settings)
    vocabulary = catalog.list_speech_terms(GROQ_STT_CATALOG_CONTEXT_TERMS)
    service = GroqService(settings)
    vietnamese = vietnamese_samples()
    samples = (vietnamese[:4] + [s for s in vietnamese if s["id"].startswith("vimd:")]
               + await asyncio.to_thread(english_samples))
    report = {
        "created_at": datetime.now(UTC).isoformat(), "model": settings.groq_stt_model,
        "selection": "First 4 existing FLEURS + 3 ViMD + first 4 LibriSpeech rows.",
        "limitations": [
            "Public exposed speech; no physical mic, mixed-language speech or blind evaluation.",
            "Auto-no-prompt changes both language forcing and prompt; auto-ui-prompt keeps "
            "the Vietnamese UI prompt while omitting the language parameter.",
            "Decoder metadata is not calibrated word accuracy; raw and accepted scores differ.",
        ],
        "source_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                          for name in ("backend/services/groq.py",
                                       "evaluation/audit_stt_language_bias.py")},
        "rows": [], "complete": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        for sample in samples:
            for mode in modes:
                if mode == "force_vi" and sample["language"] != "en":
                    continue
                file = Path(sample["path"])
                content = file.read_bytes()
                params = dict(
                    file=(file.name, content, "audio/wav"), model=settings.groq_stt_model,
                    response_format="verbose_json", timestamp_granularities=["segment"],
                    temperature=0,
                )
                if mode == "auto_ui_prompt":
                    params["prompt"] = _transcription_prompt("vi", vocabulary)
                elif mode != "auto_no_prompt":
                    language = "vi" if mode == "force_vi" else sample["language"]
                    params.update(language=language,
                                  prompt=_transcription_prompt(language, vocabulary))
                row = {
                    "case": sample["id"], "source": sample["source"],
                    "license": sample["license"], "reference": sample["reference"],
                    "language": sample["language"],
                    "audio_sha256": hashlib.sha256(content).hexdigest(),
                    "mode": mode, "attempts": [],
                }
                started = perf_counter()
                for slot, key in settings.groq_api_credentials:
                    client = service._client_for_credential(slot, key)
                    try:
                        result = await client.audio.transcriptions.create(**params)
                        accepted, quality, rejection = _transcription_quality(result)
                        raw = result.text or ""
                        row.update({
                            "raw_text": raw, "detected_language": result.language,
                            "accepted": accepted, "quality": quality, "rejection": rejection,
                            "raw_scores": accuracy(sample["reference"], raw),
                            "accepted_scores": accuracy(sample["reference"],
                                                        raw if accepted else ""),
                        })
                        row["attempts"].append({"credential_slot": slot, "status": "success"})
                        break
                    except Exception as error:
                        row["attempts"].append({"credential_slot": slot, "status": "error",
                                                "error_type": type(error).__name__})
                row["latency_ms"] = round((perf_counter() - started) * 1000)
                report["rows"].append(row)
                save()
                print(json.dumps({
                    "case": sample["id"], "mode": mode, "accepted": row.get("accepted"),
                    "wer": row.get("raw_scores", {}).get("wer"),
                }), flush=True)
        report["complete"] = True
        save()
    finally:
        for client in service._clients_by_credential.values():
            await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    asyncio.run(run(args.output, args.modes))
