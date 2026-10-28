"""Compare BM25, multilingual E5 and RRF on catalog-derived product queries.

This optional experiment is deliberately isolated from production dependencies.
Run it with FastEmbed in a temporary environment; it does not download models
or build dense vectors during application requests.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "winmart_products.json"
DEV_CASES = ROOT / "evaluation" / "cases" / "generalized_catalog_consultation_dev_v1.jsonl"
MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_FILE = "onnx/model_O4.onnx"

_QUERY_FILLER = frozenset(
    "minh toi em anh chi ban muon can tim cho xem giup hang loai nhom san pham "
    "mot vai nao nhe nha please find show looking for product products category type".split()
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("đ", "d")))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _metrics(ranked: list[list[str]], gold_ids: list[set[str]]) -> dict[str, float]:
    recalls_1 = []
    recalls_5 = []
    reciprocal_ranks = []
    ndcgs = []
    for prediction, relevant in zip(ranked, gold_ids, strict=True):
        recalls_1.append(float(bool(prediction[:1] and prediction[0] in relevant)))
        recalls_5.append(float(bool(set(prediction[:5]) & relevant)))
        ranks = [index + 1 for index, item_id in enumerate(prediction[:5]) if item_id in relevant]
        reciprocal_ranks.append(1.0 / min(ranks) if ranks else 0.0)
        dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
        ideal_count = min(len(relevant), 5)
        idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        ndcgs.append(dcg / idcg if idcg else 0.0)
    return {
        "recall_at_1": round(statistics.mean(recalls_1), 4),
        "recall_at_5": round(statistics.mean(recalls_5), 4),
        "mrr_at_5": round(statistics.mean(reciprocal_ranks), 4),
        "ndcg_at_5": round(statistics.mean(ndcgs), 4),
    }


def _bm25_rank(
    query: str,
    documents: list[dict[str, Any]],
    tokenized_documents: list[list[str]],
    document_frequency: dict[str, int],
    average_length: float,
    limit: int = 100,
) -> list[str]:
    tokens = [token for token in _normalize(query).split() if token not in _QUERY_FILLER]
    if not tokens:
        return []
    total = len(documents)
    scores = []
    for document, terms in zip(documents, tokenized_documents, strict=True):
        frequencies: dict[str, int] = {}
        for term in terms:
            frequencies[term] = frequencies.get(term, 0) + 1
        score = 0.0
        length_norm = 1.5 * (1.0 - 0.75 + 0.75 * len(terms) / max(average_length, 1.0))
        for token in set(tokens):
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            idf = math.log(
                1.0
                + (total - document_frequency.get(token, 0) + 0.5)
                / (document_frequency.get(token, 0) + 0.5)
            )
            score += idf * frequency * 2.5 / (frequency + length_norm)
        if score:
            scores.append((score, document["product_id"]))
    scores.sort(key=lambda row: (-row[0], row[1]))
    return [product_id for _, product_id in scores[:limit]]


def _rrf_rank(first: list[str], second: list[str], limit: int = 100) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in (first, second):
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (60 + rank)
    return [
        item_id for item_id, _ in sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:limit]
    ]


def run(source: Path, cases_path: Path, output_path: Path) -> dict[str, Any]:
    try:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
    except ImportError as error:
        raise RuntimeError(
            "Install fastembed in a temporary environment to run dense variants"
        ) from error

    documents = json.loads(source.read_text(encoding="utf-8"))
    product_docs = [
        {
            "product_id": row["product_id"],
            "name": row.get("name") or "",
            "brand": row.get("brand") or "",
            "category_hierarchy": row.get("category_hierarchy") or row.get("category_name") or "",
        }
        for row in documents
        if row.get("product_id") and row.get("name")
    ]
    corpus = [
        f"{row['name']}. Brand: {row['brand']}. Category: {row['category_hierarchy']}"
        for row in product_docs
    ]
    tokenized_documents = [_normalize(text).split() for text in corpus]
    document_frequency: dict[str, int] = {}
    for terms in tokenized_documents:
        for term in set(terms):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    average_length = statistics.mean(map(len, tokenized_documents))
    cases = [row for row in _read_jsonl(cases_path) if row["kind"] == "product_resolution"]

    TextEmbedding.add_custom_model(
        model=MODEL_NAME,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=MODEL_NAME),
        dim=384,
        model_file=MODEL_FILE,
    )
    cold_started = time.perf_counter_ns()
    model = TextEmbedding(model_name=MODEL_NAME)
    embeddings = list(model.embed([f"passage: {text}" for text in corpus]))
    index_build_ms = (time.perf_counter_ns() - cold_started) / 1_000_000

    bm25_ranked = []
    dense_ranked = []
    hybrid_ranked = []
    dense_latencies = []
    gold_ids = []
    case_rows = []
    for case in cases:
        query = case["query"]
        bm25_ids = _bm25_rank(
            query, product_docs, tokenized_documents, document_frequency, average_length
        )
        query_started = time.perf_counter_ns()
        query_vector = next(model.query_embed([f"query: {query}"]))
        dense_ms = (time.perf_counter_ns() - query_started) / 1_000_000
        dense_latencies.append(dense_ms)
        dense_scores = [
            (float(vector @ query_vector), product_docs[index]["product_id"])
            for index, vector in enumerate(embeddings)
        ]
        dense_scores.sort(key=lambda row: (-row[0], row[1]))
        dense_ids = [product_id for _, product_id in dense_scores[:100]]
        bm25_ranked.append(bm25_ids)
        dense_ranked.append(dense_ids)
        hybrid_ranked.append(_rrf_rank(bm25_ids, dense_ids))
        relevant = set(case["gold_product_ids"])
        gold_ids.append(relevant)
        case_rows.append(
            {
                "case_id": case["case_id"],
                "query": query,
                "gold_product_ids": sorted(relevant),
                "bm25_top5": bm25_ids[:5],
                "dense_top5": dense_ids[:5],
                "rrf_top5": _rrf_rank(bm25_ids, dense_ids, limit=5),
            }
        )

    result = {
        "kind": "generalized_retrieval_variants",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "offline_catalog_embedding_trial",
        "source_file": str(source),
        "case_file": str(cases_path),
        "catalog_size": len(product_docs),
        "product_query_count": len(cases),
        "model": {
            "name": MODEL_NAME,
            "onnx_file": MODEL_FILE,
            "dimensions": 384,
            "license": "MIT (upstream model card)",
            "backend": "FastEmbed ONNX Runtime CPU",
        },
        "index_build_ms_including_model_load": round(index_build_ms, 3),
        "vector_payload_fp32_bytes": len(embeddings) * 384 * 4,
        "dense_query_latency_ms": {
            "p50": round(statistics.median(dense_latencies), 3),
            "p95": round(
                sorted(dense_latencies)[max(0, math.ceil(len(dense_latencies) * 0.95) - 1)], 3
            ),
        },
        "metrics": {
            "bm25": _metrics(bm25_ranked, gold_ids),
            "dense_e5_small": _metrics(dense_ranked, gold_ids),
            "rrf_bm25_dense": _metrics(hybrid_ranked, gold_ids),
        },
        "limitations": [
            "Catalog-derived name/package split; not a human-authored semantic-fit evaluation.",
            "Dense vectors retrieve candidates; claims still need verified catalog evidence.",
            "Cold build includes initialization, any cache-miss download, and vectorization.",
            "Download time, memory peak, and end-to-end API latency were not isolated.",
        ],
        "cases": case_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--cases", type=Path, default=DEV_CASES)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/reports/artifacts/generalized_retrieval_variants_dev_20261027.json",
    )
    args = parser.parse_args()
    result = run(args.source, args.cases, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "metrics": result["metrics"],
                "index_build_ms": result["index_build_ms_including_model_load"],
                "query_latency_ms": result["dense_query_latency_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
