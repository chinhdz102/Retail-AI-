"""Compare Whisper prompts on the same province-labeled Vietnamese audio."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import backend.services.groq as groq_module
from backend.config import get_settings
from backend.products.repository import get_product_repository
from backend.services.groq import GroqService
from evaluation.live_groq_stt_benchmark import accuracy

DEFAULT_INPUT = Path("evaluation/results/live_groq_stt_vivoice34_20261021.json")
DEFAULT_OUTPUT = Path("evaluation/results/live_groq_stt_vivoice34_prompt_ablation_20261021.json")
DEFAULT_AUDIO_DIR = Path(".tmp/vivoice34_samples")
TARGET_PROVINCES = ("Hải Phòng", "Nghệ An", "Thừa Thiên Huế", "Quảng Trị")
PROMPTS = {
    "retail_prompt_no_dynamic_catalog": "retail",
    "retail_context_without_brand_examples": (
        "Tiếng Việt giao tiếp tự nhiên tại cửa hàng; có thể dùng giọng Bắc, Trung, Nam "
        "và khẩu ngữ. "
        "Chép sát lời, giữ dấu và tên riêng; không tự thêm, đoán, dịch hay tóm tắt. Có thể hỏi "
        "hàng hóa, giá, khuyến mãi, công dụng, vị trí, tồn kho, đồ ăn, đồ uống và nhu cầu mua sắm. "
        "Đơn vị thường gặp: chai, lon, hộp, gói, lốc, thùng, ml, lít. "
        "Nếu không nghe rõ lời, để trống."
    ),
    "brandfree_retail_with_dynamic_catalog": "retail_with_catalog",
    "short_verbatim_vi": "Tiếng Việt. Chép đúng lời đã nghe, giữ phương ngữ và tên riêng. "
    "Không tự hoàn thiện, đoán, dịch hay thêm từ.",
}


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return ordered[index]


def _fixed_prompt(value: str):
    def prompt_function(_language: str, _vocabulary: tuple[str, ...] | None = None) -> str:
        return value

    return prompt_function


async def run(
    input_path: Path,
    output_path: Path,
    audio_dir: Path,
    *,
    model: str = "whisper-large-v3",
    prompt_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    source = json.loads(input_path.read_text(encoding="utf-8"))
    base_results = source.get("results") or []
    selected = [
        item for province in TARGET_PROVINCES
        for item in base_results
        if item.get("province") == province
    ]
    if len(selected) != len(TARGET_PROVINCES):
        raise ValueError("The base report must contain one result for each target province")

    selected_prompts = prompt_names or tuple(PROMPTS)
    unknown_prompts = set(selected_prompts) - set(PROMPTS)
    if unknown_prompts:
        raise ValueError(f"Unknown prompt variants: {sorted(unknown_prompts)}")

    settings = get_settings().model_copy(
        update={"groq_stt_model": model, "groq_stt_fallback_model": ""}
    )
    catalog_vocabulary = get_product_repository(settings).list_speech_terms(32)
    service = GroqService(settings=settings)
    original_prompt = groq_module._transcription_prompt
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "kind": "live_groq_stt_vivoice34_prompt_ablation",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "dataset": source.get("dataset"),
        "dataset_source": source.get("dataset_source"),
        "license": source.get("license"),
        "models": [model],
        "comparison": (
            "The same four WAV files are sent directly through Groq's transcription API with "
            "the production retail prompt without dynamic catalog terms and with a short, "
            "brand-free Vietnamese verbatim prompt. The existing report supplies the full "
            "endpoint reference, which also includes catalog correction."
        ),
        "selected_provinces": list(TARGET_PROVINCES),
        "selected_prompts": list(selected_prompts),
        "results": [],
        "complete": False,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for sample in selected:
            audio_path = audio_dir / str(sample["file"])
            if not audio_path.is_file():
                raise FileNotFoundError(audio_path)
            audio = audio_path.read_bytes()
            item: dict[str, Any] = {
                "province": sample["province"],
                "row_idx": sample.get("row_idx"),
                "file": sample["file"],
                "reference": sample["reference"],
                "full_endpoint_current": {
                    "text": sample["transcription"],
                    "wer": sample["wer"],
                    "cer": sample["cer"],
                    "latency_ms": sample["latency_ms"],
                    "model": sample.get("model"),
                },
                "prompt_results": {},
            }
            report["results"].append(item)
            for prompt_name, prompt_value in PROMPTS.items():
                if prompt_name not in selected_prompts:
                    continue
                if prompt_value in {"retail", "retail_with_catalog"}:
                    groq_module._transcription_prompt = original_prompt
                    vocabulary = (
                        catalog_vocabulary if prompt_value == "retail_with_catalog" else ()
                    )
                else:
                    groq_module._transcription_prompt = _fixed_prompt(prompt_value)
                    vocabulary = ()
                started = perf_counter()
                try:
                    result = await service.transcribe(
                        audio_path.name,
                        audio,
                        "audio/wav",
                        language="vi",
                        vocabulary=vocabulary,
                    )
                    text = str(result)
                    scores = accuracy(str(sample["reference"]), text)
                    item["prompt_results"][prompt_name] = {
                        "text": text,
                        "speech_detected": result.speech_detected,
                        "model": result.model,
                        "latency_ms": round((perf_counter() - started) * 1000),
                        "quality": result.quality,
                        "fallback_trace": result.fallback_trace,
                        **scores,
                    }
                except Exception as error:
                    item["prompt_results"][prompt_name] = {
                        "error_type": type(error).__name__,
                        "latency_ms": round((perf_counter() - started) * 1000),
                    }
                output_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            print(f"sample={sample.get('row_idx')} province_sample_done", flush=True)
    finally:
        groq_module._transcription_prompt = original_prompt

    summary: dict[str, Any] = {}
    for prompt_name in selected_prompts:
        successful = [
            result["prompt_results"][prompt_name]
            for result in report["results"]
            if "word_errors" in result["prompt_results"].get(prompt_name, {})
        ]
        word_errors = sum(int(result["word_errors"]) for result in successful)
        reference_words = sum(int(result["reference_word_count"]) for result in successful)
        latencies = [int(result["latency_ms"]) for result in successful]
        summary[prompt_name] = {
            "successful_samples": len(successful),
            "aggregate_wer": word_errors / max(1, reference_words),
            "mean_utterance_wer": sum(float(result["wer"]) for result in successful)
            / max(1, len(successful)),
            "exact_matches": sum(result["wer"] == 0 for result in successful),
            "median_latency_ms": round(median(latencies)) if latencies else None,
            "p95_latency_ms": _percentile(latencies, 95),
        }
    report["summary"] = summary
    report["complete"] = all(
        summary[name]["successful_samples"] == len(TARGET_PROVINCES)
        for name in selected_prompts
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument(
        "--model",
        choices=("whisper-large-v3", "whisper-large-v3-turbo"),
        default="whisper-large-v3",
    )
    parser.add_argument("--prompts", nargs="+", choices=tuple(PROMPTS), default=tuple(PROMPTS))
    args = parser.parse_args()
    report = asyncio.run(
        run(
            args.input,
            args.output,
            args.audio_dir,
            model=args.model,
            prompt_names=tuple(args.prompts),
        )
    )
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "summary": report.get("summary"),
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
