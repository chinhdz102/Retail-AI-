"""Fixed-score catalog hotword diagnostic; no reference-text words enter decoding.

Run with: uv run --extra local-asr --with sentencepiece python -m
evaluation.compare_zipformer_hotwords --output <new-artifact.json>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

import sentencepiece as spm

from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services import vietnamese_asr as vi
from backend.services.groq import GroqService
from evaluation.live_groq_stt_benchmark import accuracy

ROOT = Path(__file__).resolve().parents[1]


async def run(output):
    if output.exists():
        raise ValueError("Preserve previous evidence; choose a new output")
    paired = json.loads(
        (ROOT / "docs/reports/artifacts/vietnamese_asr_paired_20261105.json").read_text(
            encoding="utf-8"
        )
    )
    directory = ROOT / paired["model_dir"]
    catalog = get_product_repository(Settings())
    counts = Counter(p.brand for p in catalog.products if p.brand and p.brand != "NOBRAND")
    terms = [b for b, _ in counts.most_common(48)]
    tokenizer = spm.SentencePieceProcessor(model_file=str(directory / "bpe.model"))
    hotwords = directory.parent / "catalog48_hotwords_bpe.txt"
    symbols = {
        line.rsplit(" ", 1)[0]
        for line in (directory / "tokens.txt").read_text(encoding="utf-8").splitlines()
    }
    accepted = [
        t
        for t in terms
        if all(p in symbols and p != "<unk>" for p in tokenizer.encode(t.upper(), out_type=str))
    ]
    # The native bpe tokenizer expects raw phrases and its exported vocabulary.
    # Passing pre-tokenized pieces to cjkchar incorrectly splits ▁THIÊN.
    hotwords.write_text("\n".join(t.upper() for t in accepted) + "\n", encoding="utf-8")
    vocabulary = directory.parent / "bpe.vocab"
    vocabulary.write_text(
        "\n".join(
            f"{tokenizer.id_to_piece(i)}\t{tokenizer.get_score(i)}"
            for i in range(tokenizer.get_piece_size())
        )
        + "\n",
        encoding="utf-8",
    )
    # Also initialize the explicit Windows DLL directories used by the app.
    vi._recognizer(str(directory))
    import sherpa_onnx

    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(directory / "encoder.int8.onnx"),
        decoder=str(directory / "decoder.onnx"),
        joiner=str(directory / "joiner.int8.onnx"),
        tokens=str(directory / "tokens.txt"),
        num_threads=2,
        decoding_method="modified_beam_search",
        max_active_paths=4,
        hotwords_file=str(hotwords),
        hotwords_score=1.5,
        modeling_unit="bpe",
        bpe_vocab=str(vocabulary),
    )
    original = vi._recognizer
    vi._recognizer = lambda *_: recognizer
    settings = Settings(vietnamese_asr_backend="zipformer", vietnamese_asr_model_dir=str(directory))
    service = GroqService(settings)
    report = {
        "complete": False,
        "terms": terms,
        "encoded_terms": accepted,
        "skipped_terms": [t for t in terms if t not in accepted],
        "fixed_score": 1.5,
        "active_paths": 4,
        "hotwords_sha256": hashlib.sha256(hotwords.read_bytes()).hexdigest(),
        "policy": "Top48 catalog brands by frequency, fixed before decoding; no per-clip "
        "or reference-derived vocabulary. Exposed file diagnostic, not microphone QA.",
        "rows": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for sample in paired["rows"]:
            path = ROOT / sample["path"]
            start = perf_counter()
            row = {"id": sample["id"], "reference": sample["reference"], "path": sample["path"]}
            content = path.read_bytes()
            row["sha256"] = hashlib.sha256(content).hexdigest()
            try:
                text = await service.transcribe(
                    path.name, content, "audio/mpeg" if path.suffix == ".mp3" else "audio/wav", "vi"
                )
                row.update(text=str(text), speech_detected=text.speech_detected)
                if sample["reference"]:
                    row.update(accuracy(sample["reference"], str(text)))
            except Exception as exc:
                row["error_type"] = type(exc).__name__
            row["seconds"] = round(perf_counter() - start, 3)
            report["rows"].append(row)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "id": row["id"],
                        "seconds": row["seconds"],
                        "wer": row.get("wer"),
                        "error": row.get("error_type"),
                    }
                ),
                flush=True,
            )
    finally:
        vi._recognizer = original
    report["complete"] = True
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
