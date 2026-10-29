"""Compare BM25, multilingual E5 and RRF on catalog-derived product queries.

This optional experiment is deliberately isolated from production dependencies.
Run it with FastEmbed in a temporary environment; it does not download models
or build dense vectors during application requests.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.data.catalog_import import canonical_dataset_sha256
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.retail_tools import execute_catalog_tool

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


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _latency_summary(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"p50": None, "p95": None, "p99": None, "mean": None, "max": None}
    ordered = sorted(samples)

    def percentile(fraction: float) -> float:
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]

    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(percentile(0.95), 3),
        "p99": round(percentile(0.99), 3),
        "mean": round(statistics.mean(ordered), 3),
        "max": round(ordered[-1], 3),
    }


def _load_runtime_product_docs(
    source: Path,
) -> tuple[list[dict[str, str]], SnapshotProductRepository, float]:
    started = time.perf_counter_ns()
    repository = SnapshotProductRepository(source)
    load_ms = (time.perf_counter_ns() - started) / 1_000_000
    product_docs = [
        {
            "product_id": product.product_id,
            "name": product.name,
            "brand": product.brand or "",
            "category_hierarchy": product.category_hierarchy or product.category or "",
        }
        for product in repository.products
        if product.product_id and product.name
    ]
    return product_docs, repository, load_ms


def _validated_product_cases(
    cases_path: Path, accepted_product_ids: set[str]
) -> list[dict[str, Any]]:
    cases = [
        row for row in _read_jsonl(cases_path) if row.get("kind") == "product_resolution"
    ]
    if not cases:
        raise ValueError("Case file has no product_resolution queries")
    seen_case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("case_id")
        gold_ids = case.get("gold_product_ids")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_case_ids:
            raise ValueError("Product-resolution case IDs must be nonempty and unique")
        seen_case_ids.add(case_id)
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError(f"Case {case_id} has no query text")
        if (
            not isinstance(gold_ids, list)
            or not gold_ids
            or any(not isinstance(product_id, str) for product_id in gold_ids)
        ):
            raise ValueError(f"Case {case_id} has no valid gold product IDs")
        unknown_ids = sorted(set(gold_ids) - accepted_product_ids)
        if unknown_ids:
            raise ValueError(
                f"Case {case_id} has gold IDs outside the accepted runtime catalog: "
                f"{unknown_ids}"
            )
    return cases


def _production_tool_rank(
    query: str,
    repository: SnapshotProductRepository,
    *,
    limit: int = 5,
) -> tuple[list[str], str]:
    """Run the deterministic customer search tool against the runtime snapshot.

    This includes production query parsing, filters, repository ranking, and
    tool observation construction. It is measured separately from the BM25/E5 trial.
    """
    observation = execute_catalog_tool(
        "search_products",
        json.dumps({"query": query, "limit": limit}, ensure_ascii=False),
        repository=repository,
        current_user_text=query,
    )
    return [product.product_id for product in observation.products], observation.status


_QUERY_METRIC_NAMES = ("recall_at_1", "recall_at_5", "mrr_at_5", "ndcg_at_5")


def _query_metric_values(prediction: list[str], relevant: set[str]) -> dict[str, float]:
    """Return standard binary-relevance metrics for one query.

    Recall is the fraction of all relevant product IDs retrieved, not merely a
    hit indicator. The distinction matters when a query has multiple acceptable
    identities or variants in its relevance set.
    """
    if not relevant:
        raise ValueError("Each retrieval query must have at least one relevant product")
    retrieved_at_1 = set(prediction[:1]) & relevant
    retrieved_at_5 = set(prediction[:5]) & relevant
    ranks = [index + 1 for index, item_id in enumerate(prediction[:5]) if item_id in relevant]
    dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
    ideal_count = min(len(relevant), 5)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return {
        "recall_at_1": len(retrieved_at_1) / len(relevant),
        "recall_at_5": len(retrieved_at_5) / len(relevant),
        "mrr_at_5": 1.0 / min(ranks) if ranks else 0.0,
        "ndcg_at_5": dcg / idcg if idcg else 0.0,
    }


def _metrics(ranked: list[list[str]], gold_ids: list[set[str]]) -> dict[str, float]:
    query_metrics = [
        _query_metric_values(prediction, relevant)
        for prediction, relevant in zip(ranked, gold_ids, strict=True)
    ]
    if not query_metrics:
        raise ValueError("Cannot compute retrieval metrics for an empty query set")
    return {
        metric: round(statistics.mean(row[metric] for row in query_metrics), 4)
        for metric in _QUERY_METRIC_NAMES
    }


def _relevance_cluster_ids(gold_ids: list[set[str]]) -> list[str]:
    """Group paraphrase queries connected to the same relevant product IDs."""
    if not gold_ids or any(not product_ids for product_ids in gold_ids):
        raise ValueError("Each query must have at least one relevant product ID")
    parents = list(range(len(gold_ids)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    first_query_by_product: dict[str, int] = {}
    for query_index, relevant_ids in enumerate(gold_ids):
        for product_id in relevant_ids:
            if product_id in first_query_by_product:
                union(query_index, first_query_by_product[product_id])
            else:
                first_query_by_product[product_id] = query_index
    return [f"product_cluster_{find(index)}" for index in range(len(gold_ids))]


def _paired_bootstrap_comparison(
    reference: list[dict[str, float]],
    candidate: list[dict[str, float]],
    *,
    cluster_ids: list[str] | None = None,
    seed: int = 20261029,
    resamples: int = 5000,
) -> dict[str, Any]:
    """Compare systems with a paired percentile bootstrap over query clusters."""
    if not reference or len(reference) != len(candidate):
        raise ValueError("Paired comparisons require equally sized, nonempty query sets")
    if resamples < 100:
        raise ValueError("At least 100 bootstrap resamples are required")
    if cluster_ids is not None and len(cluster_ids) != len(reference):
        raise ValueError("Cluster IDs must align one-to-one with paired queries")
    rng = random.Random(seed)
    n_queries = len(reference)
    if cluster_ids is None:
        cluster_ids = [f"query_{index}" for index in range(n_queries)]
    cluster_members: dict[str, list[int]] = {}
    for index, cluster_id in enumerate(cluster_ids):
        cluster_members.setdefault(cluster_id, []).append(index)
    clusters = list(cluster_members.values())
    n_clusters = len(clusters)
    metrics: dict[str, Any] = {}
    for metric in _QUERY_METRIC_NAMES:
        deltas = [
            candidate_row[metric] - reference_row[metric]
            for reference_row, candidate_row in zip(reference, candidate, strict=True)
        ]
        bootstrap_means = []
        for _ in range(resamples):
            sampled_clusters = [clusters[rng.randrange(n_clusters)] for _ in range(n_clusters)]
            sampled_query_count = sum(map(len, sampled_clusters))
            sampled_delta_total = sum(
                deltas[query_index]
                for cluster in sampled_clusters
                for query_index in cluster
            )
            bootstrap_means.append(sampled_delta_total / sampled_query_count)
        bootstrap_means.sort()
        lower_index = math.floor((resamples - 1) * 0.025)
        upper_index = math.ceil((resamples - 1) * 0.975)
        metrics[metric] = {
            "mean_delta_candidate_minus_reference": round(statistics.mean(deltas), 6),
            "candidate_wins": sum(delta > 1e-12 for delta in deltas),
            "reference_wins": sum(delta < -1e-12 for delta in deltas),
            "ties": sum(abs(delta) <= 1e-12 for delta in deltas),
            "bootstrap_95_percentile_ci": [
                round(bootstrap_means[lower_index], 6),
                round(bootstrap_means[upper_index], 6),
            ],
        }
    return {"n_queries": n_queries, "n_clusters": n_clusters, "metrics": metrics}


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
    source = source.resolve()
    cases_path = cases_path.resolve()
    output_path = output_path.resolve()
    product_docs, repository, catalog_load_ms = _load_runtime_product_docs(source)
    accepted_product_ids = {row["product_id"] for row in product_docs}
    cases = _validated_product_cases(cases_path, accepted_product_ids)

    try:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
    except ImportError as error:
        raise RuntimeError(
            "Install fastembed in a temporary environment to run dense variants"
        ) from error

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
    TextEmbedding.add_custom_model(
        model=MODEL_NAME,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=MODEL_NAME),
        dim=384,
        model_file=MODEL_FILE,
    )
    cpu_started = time.process_time()
    cold_started = time.perf_counter_ns()
    model = TextEmbedding(model_name=MODEL_NAME)
    embeddings = list(model.embed([f"passage: {text}" for text in corpus]))
    index_build_ms = (time.perf_counter_ns() - cold_started) / 1_000_000

    production_tool_ranked = []
    bm25_ranked = []
    dense_ranked = []
    hybrid_ranked = []
    production_tool_latencies = []
    production_tool_status_counts: dict[str, int] = {}
    bm25_latencies = []
    dense_latencies = []
    dense_embedding_latencies = []
    hybrid_latencies = []
    production_tool_query_metrics = []
    bm25_query_metrics = []
    dense_query_metrics = []
    hybrid_query_metrics = []
    gold_ids = []
    case_rows = []
    for case in cases:
        query = case["query"]
        production_started = time.perf_counter_ns()
        production_ids, production_status = _production_tool_rank(query, repository)
        production_tool_latencies.append(
            (time.perf_counter_ns() - production_started) / 1_000_000
        )
        production_tool_status_counts[production_status] = (
            production_tool_status_counts.get(production_status, 0) + 1
        )
        bm25_started = time.perf_counter_ns()
        bm25_ids = _bm25_rank(
            query, product_docs, tokenized_documents, document_frequency, average_length
        )
        bm25_latencies.append((time.perf_counter_ns() - bm25_started) / 1_000_000)
        query_started = time.perf_counter_ns()
        embedding_started = time.perf_counter_ns()
        query_vector = next(model.query_embed([f"query: {query}"]))
        dense_embedding_latencies.append(
            (time.perf_counter_ns() - embedding_started) / 1_000_000
        )
        dense_scores = [
            (float(vector @ query_vector), product_docs[index]["product_id"])
            for index, vector in enumerate(embeddings)
        ]
        dense_scores.sort(key=lambda row: (-row[0], row[1]))
        dense_ids = [product_id for _, product_id in dense_scores[:100]]
        dense_latencies.append((time.perf_counter_ns() - query_started) / 1_000_000)
        hybrid_started = time.perf_counter_ns()
        hybrid_ids = _rrf_rank(bm25_ids, dense_ids)
        hybrid_latencies.append((time.perf_counter_ns() - hybrid_started) / 1_000_000)
        production_tool_ranked.append(production_ids)
        bm25_ranked.append(bm25_ids)
        dense_ranked.append(dense_ids)
        hybrid_ranked.append(hybrid_ids)
        relevant = set(case["gold_product_ids"])
        gold_ids.append(relevant)
        query_scores = {
            "production_snapshot_search_tool": _query_metric_values(
                production_ids, relevant
            ),
            "bm25": _query_metric_values(bm25_ids, relevant),
            "dense_e5_small": _query_metric_values(dense_ids, relevant),
            "rrf_bm25_dense": _query_metric_values(hybrid_ids, relevant),
        }
        production_tool_query_metrics.append(
            query_scores["production_snapshot_search_tool"]
        )
        bm25_query_metrics.append(query_scores["bm25"])
        dense_query_metrics.append(query_scores["dense_e5_small"])
        hybrid_query_metrics.append(query_scores["rrf_bm25_dense"])
        case_rows.append(
            {
                "case_id": case["case_id"],
                "query": query,
                "gold_product_ids": sorted(relevant),
                "production_snapshot_search_tool_top5": production_ids[:5],
                "production_tool_status": production_status,
                "bm25_top5": bm25_ids[:5],
                "dense_top5": dense_ids[:5],
                "rrf_top5": hybrid_ids[:5],
                "query_metrics": query_scores,
            }
        )

    process_cpu_ms = (time.process_time() - cpu_started) * 1000
    relevance_clusters = _relevance_cluster_ids(gold_ids)

    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    ).stdout
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    catalog_bytes = source.read_bytes()
    case_bytes = cases_path.read_bytes()
    query_manifest = [
        {
            "case_id": row["case_id"],
            "query": row["query"],
            "gold_product_ids": row["gold_product_ids"],
        }
        for row in cases
    ]
    query_set_sha256 = hashlib.sha256(
        json.dumps(
            query_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    def percentile_samples(samples: list[float]) -> dict[str, float | None]:
        return _latency_summary(samples)

    package_versions = {
        name: _package_version(name)
        for name in ("fastembed", "onnxruntime", "numpy")
    }

    result = {
        "kind": "generalized_retrieval_variants",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "offline_catalog_embedding_trial",
        "source_file": _relative_path(source),
        "case_file": _relative_path(cases_path),
        "commit_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
        "working_tree": {
            "dirty_tracked_files": bool(status.strip()),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        },
        "catalog": {
            "raw_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "canonical_content_sha256": canonical_dataset_sha256(catalog_bytes),
            "runtime_accepted_products": len(repository.products),
            "quarantined_products": repository.rows_quarantined,
            "searchable_product_documents": len(product_docs),
            "repository_load_ms": round(catalog_load_ms, 3),
        },
        "query_set": {
            "kind": "product_resolution",
            "query_count": len(cases),
            "case_file_canonical_sha256": canonical_dataset_sha256(case_bytes),
            "evaluated_case_manifest_sha256": query_set_sha256,
            "all_gold_ids_in_accepted_runtime_catalog": True,
            "labels": "catalog-derived product identity; not human relevance judgments",
        },
        "catalog_size": len(product_docs),
        "product_query_count": len(cases),
        "model": {
            "name": MODEL_NAME,
            "onnx_file": MODEL_FILE,
            "dimensions": 384,
            "license": "MIT (upstream model card)",
            "backend": "FastEmbed ONNX Runtime CPU",
            "fastembed_version": package_versions["fastembed"],
            "onnxruntime_version": package_versions["onnxruntime"],
        },
        "index_build_ms_including_model_load": round(index_build_ms, 3),
        "index_build_scope": (
            "model initialization (including any cache-miss download) and embedding "
            "all accepted catalog products"
        ),
        "vector_payload_fp32_bytes": len(embeddings) * 384 * 4,
        "query_latency_ms": {
            "production_snapshot_search_tool_end_to_end": percentile_samples(
                production_tool_latencies
            ),
            "bm25_full_rank": percentile_samples(bm25_latencies),
            "dense_embedding_only": percentile_samples(dense_embedding_latencies),
            "dense_embedding_scoring_and_sort": percentile_samples(dense_latencies),
            "rrf_fusion": percentile_samples(hybrid_latencies),
        },
        "production_tool_status_counts": production_tool_status_counts,
        "process_cpu_ms": round(process_cpu_ms, 3),
        "memory_measurement": "not_measured",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or None,
            "logical_cpu_count": os.cpu_count(),
            "dependencies": package_versions,
        },
        "methodology": {
            "corpus": "validated SnapshotProductRepository products only",
            "query_kind": "product-resolution exact-title variants",
            "candidates_per_system": 100,
            "dense_query_prefix": "query: ",
            "dense_document_prefix": "passage: ",
            "rrf_constant": 60,
            "same_queries_same_catalog": True,
            "gold_ids_validated_before_retrieval": True,
            "production_baseline": (
                "execute_catalog_tool(search_products) against the runtime snapshot with the "
                "query text and five-result limit; includes deterministic query parsing, "
                "hard filters, repository ranking, and production tool response construction"
            ),
        },
        "metrics": {
            "production_snapshot_search_tool": _metrics(production_tool_ranked, gold_ids),
            "bm25": _metrics(bm25_ranked, gold_ids),
            "dense_e5_small": _metrics(dense_ranked, gold_ids),
            "rrf_bm25_dense": _metrics(hybrid_ranked, gold_ids),
        },
        "paired_comparisons": {
            "method": "paired product-cluster bootstrap percentile interval for mean metric delta",
            "confidence_level": 0.95,
            "resamples": 5000,
            "seed": 20261029,
            "resampling_unit": "connected component of queries sharing any gold product ID",
            "cluster_count": len(set(relevance_clusters)),
            "metric_note": (
                "Recall@k is the fraction of all query-relevant product IDs retrieved; "
                "MRR@5 and binary nDCG@5 use the same validated relevance set."
            ),
            "candidate_minus_reference": {
                "production_snapshot_search_tool_vs_bm25": _paired_bootstrap_comparison(
                    bm25_query_metrics,
                    production_tool_query_metrics,
                    cluster_ids=relevance_clusters,
                    seed=20261032,
                ),
                "production_snapshot_search_tool_vs_rrf_bm25_dense": (
                    _paired_bootstrap_comparison(
                        hybrid_query_metrics,
                        production_tool_query_metrics,
                        cluster_ids=relevance_clusters,
                        seed=20261033,
                    )
                ),
                "dense_e5_small_vs_bm25": _paired_bootstrap_comparison(
                    bm25_query_metrics,
                    dense_query_metrics,
                    cluster_ids=relevance_clusters,
                    seed=20261029,
                ),
                "rrf_bm25_dense_vs_bm25": _paired_bootstrap_comparison(
                    bm25_query_metrics,
                    hybrid_query_metrics,
                    cluster_ids=relevance_clusters,
                    seed=20261030,
                ),
                "rrf_bm25_dense_vs_dense_e5_small": _paired_bootstrap_comparison(
                    dense_query_metrics,
                    hybrid_query_metrics,
                    cluster_ids=relevance_clusters,
                    seed=20261031,
                ),
            },
        },
        "limitations": [
            (
                "The case files are catalog-derived title variants, not human-authored "
                "semantic-fit queries."
            ),
            (
                "The prior heldout SKU split has been inspected and is no longer a blind "
                "heldout result."
            ),
            "Dense vectors retrieve candidates; claims still need verified catalog evidence.",
            (
                "The production-tool baseline is measured only on catalog-derived product-identity "
                "queries; this does not establish relevance for independently authored "
                "natural-need advice."
            ),
            (
                "Cold build includes initialization, any cache-miss download, and vectorization; "
                "these parts are not separately timed."
            ),
            "Memory peak/process RSS and model download time were not measured.",
            (
                "RRF uses synthetic title-resolution labels and does not establish semantic "
                "recommendation suitability."
            ),
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
        default=(
            ROOT
            / "docs/reports/artifacts/generalized_retrieval_variants_runtime_dev_20261029.json"
        ),
    )
    args = parser.parse_args()
    result = run(args.source, args.cases, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "metrics": result["metrics"],
                "index_build_ms": result["index_build_ms_including_model_load"],
                "query_latency_ms": result["query_latency_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
