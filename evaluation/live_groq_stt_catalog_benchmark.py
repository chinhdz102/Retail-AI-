"""End-to-end Vietnamese retail STT benchmark using exact-reference Azure speech audio."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.azure_speech import AzureSpeechService
from backend.services.catalog_transcription import correct_catalog_words
from backend.services.groq import GroqService
from evaluation.live_groq_stt_benchmark import accuracy

CASES = (
    ("greeting", "Chào bạn, mình cần tìm vài món đồ."),
    ("brand_price", "Cho mình hỏi Coca Zero giá bao nhiêu?"),
    ("dairy", "Sữa Vinamilk không đường có loại một lít không?"),
    ("instant_noodles", "Mì Hảo Hảo tôm chua cay nằm ở kệ nào?"),
    ("snack", "Snack Oishi vị cay còn hàng không?"),
    ("personal_care", "Có dầu gội dành cho tóc dầu không?"),
    ("follow_up", "Còn bao nhiêu sản phẩm vậy?"),
    ("colloquial", "Có Coca Zero hông, giá nhiêu vậy?"),
    ("tissue", "Cửa hàng có khăn giấy ướt không mùi không?"),
    ("dish_soap", "Nước rửa chén Sunlight loại một lít giá bao nhiêu?"),
    ("toothpaste", "Có kem đánh răng P/S cho răng nhạy cảm không?"),
    ("baby_diaper", "Tã quần Huggies cỡ M còn hàng không?"),
    ("household", "Nồi cơm điện Lock and Lock nằm ở khu nào?"),
    ("low_budget_meal", "Có mì ly ăn liền dưới hai mươi nghìn không?"),
    ("bottled_water", "Cho mình chai nước suối nhỏ nhé."),
    ("promotion", "Dầu gội Sunsilk đang có khuyến mãi không?"),
)


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, int(len(ordered) * fraction + 0.999999) - 1)
    return ordered[index]


async def close_clients(service: GroqService, speech: AzureSpeechService) -> None:
    clients = list(service._clients_by_credential.values())
    if speech.client is not None:
        clients.append(speech.client)
    await asyncio.gather(
        *(client.aclose() for client in clients if hasattr(client, "aclose")),
        return_exceptions=True,
    )


async def run(settings: Settings, output: Path) -> dict[str, Any]:
    speech = AzureSpeechService(settings)
    stt = GroqService(settings)
    catalog = get_product_repository(settings)
    report: dict[str, Any] = {
        "kind": "live_groq_stt_exact_reference_retail_dialogue",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "audio_source": "Azure-generated Vietnamese speech; exact source text is the reference",
        "language": "vi",
        "stt_primary_model": settings.groq_stt_model,
        "stt_fallback_model": settings.groq_stt_fallback_model,
        "trial_count": len(CASES),
        "results": [],
        "complete": False,
    }

    try:
        for case_id, reference in CASES:
            item: dict[str, Any] = {"case_id": case_id, "reference": reference}
            started = perf_counter()
            try:
                synth_started = perf_counter()
                audio = await speech.synthesize(reference, "vi")
                item["tts_ms"] = round((perf_counter() - synth_started) * 1000)
                stt_started = perf_counter()
                transcription = await stt.transcribe(
                    f"{case_id}.mp3",
                    audio.content,
                    audio.content_type,
                    language="vi",
                    vocabulary=catalog.list_speech_terms(32),
                )
                raw_text = str(transcription)
                text, corrections = correct_catalog_words(
                    raw_text,
                    catalog,
                    language="vi",
                )
                item.update(
                    {
                        "status": "success" if transcription.speech_detected else "rejected",
                        "speech_detected": transcription.speech_detected,
                        "stt_ms": round((perf_counter() - stt_started) * 1000),
                        "total_ms": round((perf_counter() - started) * 1000),
                        "model": transcription.model,
                        "fallback_trace": transcription.fallback_trace,
                        "quality": transcription.quality,
                        "raw_transcription": raw_text,
                        "transcription": text,
                        "catalog_corrections": [
                            {"from": correction.source, "to": correction.replacement}
                            for correction in corrections
                        ],
                    }
                )
                if transcription.speech_detected:
                    item.update(accuracy(reference, text))
                    item["raw_wer"] = accuracy(reference, raw_text)["wer"]
            except Exception as exc:
                item.update(
                    {
                        "status": "error",
                        "total_ms": round((perf_counter() - started) * 1000),
                        "error_type": type(exc).__name__,
                    }
                )
            report["results"].append(item)
            print(
                f"STT case={case_id} status={item['status']} "
                f"stt_ms={item.get('stt_ms')} wer={item.get('wer')}",
                flush=True,
            )
    finally:
        await close_clients(stt, speech)

    accepted = [row for row in report["results"] if row["status"] == "success"]
    latencies = [row["stt_ms"] for row in accepted if row.get("stt_ms") is not None]
    report["summary"] = {
        "case_count": len(report["results"]),
        "accepted_count": len(accepted),
        "rejected_count": sum(row["status"] == "rejected" for row in report["results"]),
        "error_count": sum(row["status"] == "error" for row in report["results"]),
        "exact_match_count": sum(row.get("wer") == 0 for row in accepted),
        "mean_wer": sum(row["wer"] for row in accepted if "wer" in row) / max(1, len(accepted)),
        "mean_cer": sum(row["cer"] for row in accepted if "cer" in row) / max(1, len(accepted)),
        "raw_mean_wer": sum(row["raw_wer"] for row in accepted if "raw_wer" in row)
        / max(1, len(accepted)),
        "raw_exact_match_count": sum(row.get("raw_wer") == 0 for row in accepted),
        "median_stt_ms": round(median(latencies)) if latencies else None,
        "p95_stt_ms": percentile(latencies, 0.95),
    }
    report["complete"] = len(report["results"]) == len(CASES)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/live_groq_stt_catalog_benchmark.json"),
    )
    args = parser.parse_args()
    settings = Settings(_env_file=os.environ.get("RETAIL_EVAL_ENV_FILE", ".env"))
    if not settings.has_groq_key:
        raise SystemExit("GROQ_API_KEY is not configured in the project .env")
    if not settings.has_azure_speech:
        raise SystemExit("AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are not configured")
    report = asyncio.run(run(settings, args.output))
    print(f"Saved {args.output}; complete={report['complete']}")


if __name__ == "__main__":
    main()
