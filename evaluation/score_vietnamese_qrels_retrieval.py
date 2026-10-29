"""Score ranked retrieval runs against resolved human product-qrels.

Unknown, unreviewed, and unresolved pairs are never converted to irrelevant labels.
Metrics are reported only for query/cutoff pairs whose returned top-k is fully judged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from statistics import mean, median
from typing import Any

from backend.data.catalog_import import canonical_dataset_sha256
from evaluation.score_vietnamese_qrels_review import FIT_FIELDS, FIT_LABELS
from evaluation.split_policy import SplitPolicyError, authorize_dataset_split

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "data" / "winmart_products.json"
ROOT = Path(__file__).resolve().parents[1]
RELEVANCE_GRADE = {"EXACT": 3, "SUBSTITUTE": 2, "COMPLEMENT": 1, "IRRELEVANT": 0}
BINARY_RELEVANT_MIN_GRADE = 2
DEFAULT_KS = (1, 5, 10)
RANK_FIELD = re.compile(r"^(?P<system>[A-Za-z0-9][A-Za-z0-9_.-]*)_top(?P<depth>[1-9][0-9]*)$")


class RetrievalQrelsScoringError(ValueError):
    """Raised when run metadata, judged qrels, or candidate rankings do not align."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetrievalQrelsScoringError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RetrievalQrelsScoringError(f"{label} must be a JSON object: {path}")
    return value


def _dataset_cases(path: Path) -> tuple[str, list[dict[str, str]]]:
    try:
        raw = path.read_bytes()
        if path.suffix.casefold() == ".jsonl":
            cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            dataset_id = path.stem
        else:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
                raise RetrievalQrelsScoringError("Dataset JSON must contain a cases array")
            cases = payload["cases"]
            dataset_id = str(payload.get("dataset_id") or path.stem)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalQrelsScoringError(f"Cannot read evaluation dataset {path}: {exc}") from exc

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(cases, start=1):
        if not isinstance(row, dict):
            raise RetrievalQrelsScoringError(f"Dataset row {index} must be an object")
        case_id = str(row.get("case_id") or "").strip()
        query = str(row.get("query") or "").strip()
        if not case_id or not query:
            raise RetrievalQrelsScoringError(f"Dataset row {index} needs case_id and query")
        if case_id in seen:
            raise RetrievalQrelsScoringError(f"Dataset contains duplicate case_id {case_id!r}")
        seen.add(case_id)
        normalized.append({"case_id": case_id, "query": query})
    if not normalized:
        raise RetrievalQrelsScoringError("Evaluation dataset has no cases")
    return dataset_id, normalized


def _query_id(dataset_sha256: str, case_id: str) -> str:
    return hashlib.sha256(f"{dataset_sha256}:{case_id}".encode()).hexdigest()[:16]


def _load_qrels(
    path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, dict[str, Any]]],
    dict[str, int],
    dict[str, str],
]:
    report = _read_json(path, "qrels agreement report")
    if report.get("schema_version") != "vietnamese_product_qrels_agreement_v1":
        raise RetrievalQrelsScoringError("Unsupported qrels agreement report schema")
    dataset = report.get("dataset")
    items = report.get("items")
    if not isinstance(dataset, dict) or not isinstance(items, list):
        raise RetrievalQrelsScoringError("Qrels agreement report lacks dataset metadata or items")
    for field in ("dataset_id", "dataset_sha256", "catalog_sha256", "split"):
        if not str(dataset.get(field) or "").strip():
            raise RetrievalQrelsScoringError(f"Qrels agreement report lacks dataset.{field}")

    stats = {
        "total_qrel_rows": 0,
        "reviewed_relevance_pairs": 0,
        "unjudged_pairs": 0,
        "unresolved_pairs": 0,
    }
    judgments: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    query_texts: dict[str, str] = {}
    seen_pairs: set[tuple[str, str]] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise RetrievalQrelsScoringError(f"Qrels item {index} must be an object")
        for field in ("dataset_id", "dataset_sha256", "catalog_sha256", "split"):
            if str(item.get(field) or "") != str(dataset[field]):
                raise RetrievalQrelsScoringError(
                    f"Qrels item {index} has inconsistent frozen metadata: {field}"
                )
        query_id = str(item.get("query_id") or "").strip()
        query_text = str(item.get("query_text") or "").strip()
        product_id = str(item.get("product_id") or "").strip()
        if not query_id or not query_text:
            raise RetrievalQrelsScoringError(f"Qrels item {index} lacks query_id or query_text")
        existing_query_text = query_texts.setdefault(query_id, query_text)
        if existing_query_text != query_text:
            raise RetrievalQrelsScoringError(
                f"Qrels query {query_id} mixes query text across candidate rows"
            )
        stats["total_qrel_rows"] += 1
        if not product_id:
            # Exported NO_CANDIDATE rows describe an empty result, not a product pair.
            continue
        pair_key = (query_id, product_id)
        if pair_key in seen_pairs:
            raise RetrievalQrelsScoringError(
                f"Duplicate qrel product {product_id!r} for query {query_id}"
            )
        seen_pairs.add(pair_key)
        resolution = item.get("resolution_status")
        resolved = item.get("resolved_judgments")
        if resolution not in {"AGREED", "ADJUDICATED"} or not isinstance(resolved, dict):
            stats["unresolved_pairs"] += 1
            continue
        relevance = str(resolved.get("relevance") or "").strip().upper()
        if relevance == "UNJUDGED":
            stats["unjudged_pairs"] += 1
            continue
        if relevance not in RELEVANCE_GRADE:
            raise RetrievalQrelsScoringError(
                f"Qrels item {index} has unsupported resolved relevance {relevance!r}"
            )
        fits = {field: str(resolved.get(field) or "").strip().upper() for field in FIT_FIELDS}
        invalid_fits = [field for field, value in fits.items() if value not in FIT_LABELS]
        if invalid_fits:
            raise RetrievalQrelsScoringError(
                f"Qrels item {index} has invalid fit judgments: {', '.join(invalid_fits)}"
            )
        judgments[query_id][product_id] = {
            "grade": RELEVANCE_GRADE[relevance],
            "label": relevance,
            "fits": fits,
        }
        stats["reviewed_relevance_pairs"] += 1
    return dataset, dict(judgments), stats, query_texts


def _load_runs(
    paths: list[Path],
    dataset_sha256: str,
    catalog_sha256: str,
    cases_by_id: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    if not paths:
        raise RetrievalQrelsScoringError("At least one ranked retrieval artifact is required")
    systems: dict[str, dict[str, Any]] = {}
    artifact_hashes: list[dict[str, str]] = []
    for path in paths:
        artifact = _read_json(path, "retrieval artifact")
        if artifact.get("kind") != "generalized_retrieval_variants":
            raise RetrievalQrelsScoringError(f"Unsupported retrieval artifact kind in {path}")
        query_set = artifact.get("query_set")
        catalog = artifact.get("catalog")
        if not isinstance(query_set, dict) or not isinstance(catalog, dict):
            raise RetrievalQrelsScoringError(
                f"Retrieval artifact {path} lacks query-set or catalog provenance"
            )
        if query_set.get("case_file_canonical_sha256") != dataset_sha256:
            raise RetrievalQrelsScoringError(
                f"Dataset SHA-256 mismatch in retrieval artifact {path}"
            )
        if catalog.get("canonical_content_sha256") != catalog_sha256:
            raise RetrievalQrelsScoringError(
                f"Catalog SHA-256 mismatch in retrieval artifact {path}"
            )
        rows = artifact.get("cases")
        if not isinstance(rows, list):
            raise RetrievalQrelsScoringError(f"Retrieval artifact {path} lacks cases")
        artifact_hashes.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "canonical_sha256": canonical_dataset_sha256(path.read_bytes()),
            }
        )
        seen_cases: set[str] = set()
        found_systems: set[str] = set()
        aliases = artifact.get("system_names", {})
        if not isinstance(aliases, dict):
            raise RetrievalQrelsScoringError(
                f"Retrieval artifact {path} system_names must be an object"
            )
        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise RetrievalQrelsScoringError(f"{path}: case row {row_number} must be an object")
            case_id = str(row.get("case_id") or "").strip()
            if case_id not in cases_by_id:
                raise RetrievalQrelsScoringError(f"{path}: unknown case_id {case_id!r}")
            if case_id in seen_cases:
                raise RetrievalQrelsScoringError(f"{path}: duplicate case_id {case_id!r}")
            seen_cases.add(case_id)
            if str(row.get("query") or "").strip() != cases_by_id[case_id]:
                raise RetrievalQrelsScoringError(f"{path}: query text mismatch for {case_id}")
            query_id = _query_id(dataset_sha256, case_id)
            for field, values in row.items():
                match = RANK_FIELD.fullmatch(str(field))
                if match is None:
                    continue
                system_name = str(aliases.get(field) or match.group("system")).strip()
                if not system_name:
                    raise RetrievalQrelsScoringError(
                        f"{path}: system name for ranking field {field} is empty"
                    )
                depth = int(match.group("depth"))
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    raise RetrievalQrelsScoringError(
                        f"{path}: ranking {field} for {case_id} must be a list of product IDs"
                    )
                ranked_ids = [value.strip() for value in values]
                if len(ranked_ids) > depth:
                    raise RetrievalQrelsScoringError(
                        f"{path}: {field} returned {len(ranked_ids)} IDs beyond "
                        f"declared depth {depth}"
                    )
                if len(ranked_ids) != len(set(ranked_ids)):
                    raise RetrievalQrelsScoringError(
                        f"{path}: ranking {field} contains duplicate product IDs for {case_id}"
                    )
                if system_name not in systems:
                    systems[system_name] = {
                        "depth": depth,
                        "rankings": {},
                        "source_path": str(path),
                        "ranking_field": field,
                    }
                system = systems[system_name]
                if system["source_path"] != str(path):
                    raise RetrievalQrelsScoringError(
                        f"System name {system_name!r} is present in multiple artifacts; "
                        "assign a unique system_names alias in each artifact."
                    )
                if system["depth"] != depth or system["ranking_field"] != field:
                    raise RetrievalQrelsScoringError(
                        f"System {system_name!r} has inconsistent ranking fields/depths"
                    )
                if query_id in system["rankings"]:
                    raise RetrievalQrelsScoringError(
                        f"System {system_name!r} contains duplicate query {query_id}"
                    )
                system["rankings"][query_id] = ranked_ids
                found_systems.add(system_name)
        if not found_systems:
            raise RetrievalQrelsScoringError(f"No ranked _topK lists found in {path}")
    return systems, artifact_hashes


def _validate_evaluator_manifest(
    path: Path,
    *,
    dataset: dict[str, Any],
    run_paths: list[Path],
) -> dict[str, str]:
    manifest = _read_json(path, "evaluator-only manifest")
    for field in ("dataset_id", "dataset_sha256", "catalog_sha256", "split"):
        if str(manifest.get(field) or "") != str(dataset[field]):
            raise RetrievalQrelsScoringError(
                f"Evaluator-only manifest does not match frozen qrels metadata: {field}"
            )
    recorded_hashes = manifest.get("candidate_artifact_sha256")
    if not isinstance(recorded_hashes, list) or any(
        not isinstance(value, str) for value in recorded_hashes
    ):
        raise RetrievalQrelsScoringError(
            "Evaluator-only manifest lacks candidate_artifact_sha256 list"
        )
    current_hashes = [canonical_dataset_sha256(run_path.read_bytes()) for run_path in run_paths]
    if recorded_hashes != current_hashes:
        raise RetrievalQrelsScoringError(
            "Ranked artifacts/order do not match the frozen qrels candidate-pool manifest"
        )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def _query_metrics(
    ranked_ids: list[str],
    qrels: dict[str, dict[str, Any]],
    k: int,
) -> dict[str, float | int | None]:
    top_ids = ranked_ids[:k]
    top_grades = [qrels[product_id]["grade"] for product_id in top_ids]
    ideal_grades = sorted((item["grade"] for item in qrels.values()), reverse=True)[:k]
    ideal_dcg = _dcg(ideal_grades)
    ndcg = _dcg(top_grades) / ideal_dcg if ideal_dcg else 0.0
    relevant_total = sum(item["grade"] >= BINARY_RELEVANT_MIN_GRADE for item in qrels.values())
    relevant_positions = [
        index + 1 for index, grade in enumerate(top_grades) if grade >= BINARY_RELEVANT_MIN_GRADE
    ]
    return {
        "ndcg": ndcg,
        "precision": len(relevant_positions) / k,
        "recall": len(relevant_positions) / relevant_total if relevant_total else None,
        "mrr": 1.0 / relevant_positions[0] if relevant_positions else 0.0,
        "relevant_total_in_judged_pool": relevant_total,
        "returned_count": len(top_ids),
    }


def _fit_failure_summary(
    ranked_ids: list[str], qrels: dict[str, dict[str, Any]], k: int
) -> dict[str, dict[str, int | float | None]]:
    summary: dict[str, dict[str, int | float | None]] = {}
    for field in FIT_FIELDS:
        passed = failed = 0
        for product_id in ranked_ids[:k]:
            judgment = qrels[product_id]["fits"].get(field)
            passed += judgment == "PASS"
            failed += judgment == "FAIL"
        denominator = passed + failed
        summary[field] = {
            "pass": passed,
            "fail": failed,
            "applicable_and_known": denominator,
            "failure_rate": failed / denominator if denominator else None,
        }
    return summary


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _paired_comparisons(
    systems: dict[str, dict[str, Any]],
    *,
    ks: tuple[int, ...],
    seed: int,
    bootstrap_resamples: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for reference, candidate in combinations(sorted(systems), 2):
        by_k: dict[str, Any] = {}
        for k in ks:
            ref_rows = systems[reference]["per_k"][str(k)]["per_query"]
            cand_rows = systems[candidate]["per_k"][str(k)]["per_query"]
            shared = sorted(
                query_id
                for query_id in set(ref_rows) & set(cand_rows)
                if ref_rows[query_id].get("status") == "SCORED"
                and cand_rows[query_id].get("status") == "SCORED"
            )
            deltas = [
                cand_rows[query_id]["metrics"]["ndcg"] - ref_rows[query_id]["metrics"]["ndcg"]
                for query_id in shared
            ]
            item: dict[str, Any] = {
                "reference": reference,
                "candidate": candidate,
                "k": k,
                "paired_query_count": len(shared),
                "mean_ndcg_delta": round(mean(deltas), 6) if deltas else None,
                "median_ndcg_delta": round(median(deltas), 6) if deltas else None,
                "improved_queries": sum(delta > 1e-12 for delta in deltas),
                "degraded_queries": sum(delta < -1e-12 for delta in deltas),
                "unchanged_queries": sum(abs(delta) <= 1e-12 for delta in deltas),
                "bootstrap_95_percentile_ci": None,
                "ci_note": None,
                "resampling_unit": "query",
                "seed": seed,
                "resamples": bootstrap_resamples,
            }
            if len(deltas) < 30:
                item["ci_note"] = "not computed: fewer than 30 paired, fully judged queries"
            elif bootstrap_resamples < 100:
                item["ci_note"] = "not computed: bootstrap requires at least 100 resamples"
            else:
                token = hashlib.sha256(f"{seed}:{reference}:{candidate}:{k}".encode()).hexdigest()
                rng = random.Random(int(token[:16], 16))
                sampled_means = [
                    mean(rng.choice(deltas) for _ in deltas) for _ in range(bootstrap_resamples)
                ]
                item["bootstrap_95_percentile_ci"] = [
                    round(_percentile(sampled_means, 0.025), 6),
                    round(_percentile(sampled_means, 0.975), 6),
                ]
            by_k[str(k)] = item
        output[f"{reference}_vs_{candidate}"] = by_k
    return output


def score_ranked_runs_against_qrels(
    agreement_report_path: Path,
    dataset_path: Path,
    evaluator_manifest_path: Path,
    run_paths: list[Path],
    output_path: Path,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    ks: tuple[int, ...] = DEFAULT_KS,
    seed: int = 20261029,
    bootstrap_resamples: int = 2000,
) -> dict[str, Any]:
    if not ks or any(k < 1 for k in ks) or tuple(sorted(set(ks))) != ks:
        raise ValueError("ks must be unique positive integers in increasing order")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if bootstrap_resamples < 0:
        raise ValueError("bootstrap_resamples must be non-negative")

    qrels_dataset, judged_qrels, qrel_stats, qrel_query_texts = _load_qrels(agreement_report_path)
    dataset_id, cases = _dataset_cases(dataset_path)
    dataset_sha256 = canonical_dataset_sha256(dataset_path.read_bytes())
    if dataset_id != str(qrels_dataset["dataset_id"]):
        raise RetrievalQrelsScoringError("Dataset ID does not match the qrels agreement report")
    if dataset_sha256 != str(qrels_dataset["dataset_sha256"]):
        raise RetrievalQrelsScoringError(
            "Dataset SHA-256 does not match the qrels agreement report"
        )
    catalog_sha256 = canonical_dataset_sha256(catalog_path.read_bytes())
    if catalog_sha256 != str(qrels_dataset["catalog_sha256"]):
        raise RetrievalQrelsScoringError(
            "Catalog SHA-256 does not match the qrels agreement report"
        )
    split = str(qrels_dataset["split"])
    try:
        split_authorization = authorize_dataset_split(dataset_path, split)
    except SplitPolicyError as exc:
        raise RetrievalQrelsScoringError(f"Dataset split is not authorized: {exc}") from exc
    evaluator_manifest = _validate_evaluator_manifest(
        evaluator_manifest_path,
        dataset=qrels_dataset,
        run_paths=run_paths,
    )

    cases_by_id = {case["case_id"]: case["query"] for case in cases}
    query_ids_by_case = {case_id: _query_id(dataset_sha256, case_id) for case_id in cases_by_id}
    case_by_query_id = {query_id: case_id for case_id, query_id in query_ids_by_case.items()}
    unknown_qrels_queries = sorted(set(qrel_query_texts) - set(case_by_query_id))
    if unknown_qrels_queries:
        raise RetrievalQrelsScoringError(
            f"Qrels contain query IDs absent from the frozen dataset: {unknown_qrels_queries[:5]}"
        )
    for query_id, query_text in qrel_query_texts.items():
        case_id = case_by_query_id[query_id]
        if query_text != cases_by_id[case_id]:
            raise RetrievalQrelsScoringError(
                f"Qrels query text differs from frozen dataset for {query_id}"
            )
    qrel_query_ids = sorted(qrel_query_texts)
    if not qrel_query_ids:
        raise RetrievalQrelsScoringError("Qrels agreement report contains no query rows")

    systems, artifact_hashes = _load_runs(run_paths, dataset_sha256, catalog_sha256, cases_by_id)
    for system_name, system in systems.items():
        system["per_k"] = {}
        for k in ks:
            per_query: dict[str, Any] = {}
            scored_metrics: list[dict[str, float | int | None]] = []
            failure_rows: list[dict[str, dict[str, int | float | None]]] = []
            for query_id in qrel_query_ids:
                ranked = system["rankings"].get(query_id)
                if ranked is None:
                    per_query[query_id] = {"status": "MISSING_FROM_RUN", "metrics": None}
                    continue
                if k > system["depth"]:
                    per_query[query_id] = {
                        "status": "INSUFFICIENT_RUN_DEPTH",
                        "declared_depth": system["depth"],
                        "metrics": None,
                    }
                    continue
                query_qrels = judged_qrels.get(query_id, {})
                top_ids = ranked[:k]
                if not query_qrels:
                    per_query[query_id] = {
                        "status": "NO_RESOLVED_QUERY_JUDGMENTS",
                        "unjudged_product_ids": top_ids,
                        "metrics": None,
                    }
                    continue
                unjudged = [product_id for product_id in top_ids if product_id not in query_qrels]
                if unjudged:
                    per_query[query_id] = {
                        "status": "INCOMPLETE_UNJUDGED_TOP_K",
                        "unjudged_product_ids": unjudged,
                        "metrics": None,
                    }
                    continue
                metrics = _query_metrics(ranked, query_qrels, k)
                per_query[query_id] = {"status": "SCORED", "metrics": metrics}
                scored_metrics.append(metrics)
                failure_rows.append(_fit_failure_summary(ranked, query_qrels, k))

            if qrel_stats["reviewed_relevance_pairs"] == 0:
                status = "NOT_AVAILABLE_NO_RESOLVED_RELEVANCE_JUDGMENTS"
            elif k > system["depth"]:
                status = "INSUFFICIENT_RUN_DEPTH"
            elif len(scored_metrics) == len(qrel_query_ids):
                status = "SCORED"
            elif scored_metrics:
                status = "PARTIAL_JUDGMENT_OR_RUN_COVERAGE"
            else:
                status = "NO_COMPLETE_TOP_K_JUDGMENTS"

            metric_names = ("ndcg", "precision", "recall", "mrr")
            aggregate = {
                name: (
                    round(
                        mean(float(row[name]) for row in scored_metrics if row[name] is not None),
                        6,
                    )
                    if any(row[name] is not None for row in scored_metrics)
                    else None
                )
                for name in metric_names
            }
            fit_aggregate: dict[str, dict[str, int | float | None]] = {}
            for field in FIT_FIELDS:
                passed = sum(row[field]["pass"] for row in failure_rows)
                failed = sum(row[field]["fail"] for row in failure_rows)
                denominator = passed + failed
                fit_aggregate[field] = {
                    "pass": passed,
                    "fail": failed,
                    "applicable_and_known": denominator,
                    "failure_rate": round(failed / denominator, 6) if denominator else None,
                }
            system["per_k"][str(k)] = {
                "status": status,
                "coverage": {
                    "qrels_query_count": len(qrel_query_ids),
                    "scored_query_count": len(scored_metrics),
                    "scored_query_rate": round(len(scored_metrics) / len(qrel_query_ids), 6),
                },
                "metrics_macro_average": aggregate,
                "reviewer_flagged_fit_failures": fit_aggregate,
                "per_query": per_query,
            }

    output = {
        "schema_version": "vietnamese_qrels_ranked_retrieval_metrics_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "run_metadata": _run_metadata(),
        "qrels_agreement_report": {
            "path": str(agreement_report_path),
            "sha256": hashlib.sha256(agreement_report_path.read_bytes()).hexdigest(),
        },
        "dataset": {
            **qrels_dataset,
            "case_file": str(dataset_path),
            "catalog_file": str(catalog_path),
            "split_authorization": split_authorization,
        },
        "evaluator_only_manifest": evaluator_manifest,
        "label_provenance": {
            "source": "resolved independent-review agreement/adjudication report",
            "grade_mapping": RELEVANCE_GRADE,
            "binary_relevant_labels": [
                label
                for label, grade in RELEVANCE_GRADE.items()
                if grade >= BINARY_RELEVANT_MIN_GRADE
            ],
            "ndcg_gain": "2^grade - 1",
            "recall_scope": (
                "judged Exact/Substitute items in the pooled candidate set, not the full catalog"
            ),
            "unjudged_and_unresolved": (
                "excluded from qrels; a returned top-k item without a resolved label "
                "makes that query incomplete"
            ),
            "human_suitability_claim": False,
        },
        "qrels_coverage": qrel_stats,
        "retrieval_artifacts": artifact_hashes,
        "evaluation_config": {
            "ks": list(ks),
            "seed": seed,
            "bootstrap_resamples": bootstrap_resamples,
            "paired_ci_minimum_queries": 30,
        },
        "systems": systems,
        "paired_ndcg_comparisons": _paired_comparisons(
            systems,
            ks=ks,
            seed=seed,
            bootstrap_resamples=bootstrap_resamples,
        ),
        "limitations": [
            "A scored query is usable only when every returned top-k product has a resolved qrel.",
            "Recall is conditional on the judged pooled candidates and is not catalog-wide recall.",
            (
                "Complement receives graded nDCG gain but is not a substitute for binary "
                "precision, recall, or MRR."
            ),
            "Paired bootstrap resamples queries; shared products can make query errors dependent.",
            (
                "Metrics do not certify data independence, split eligibility, freshness, "
                "or production suitability."
            ),
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output


def _run_metadata() -> dict[str, Any]:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    status = git("status", "--porcelain", "--untracked-files=all")
    tracked_status = git("status", "--porcelain", "--untracked-files=no")
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return {
        "git_commit_sha": git("rev-parse", "HEAD") or None,
        "working_tree": {
            "dirty_tracked_files": bool(tracked_status),
            "untracked_files_present": any(line.startswith("??") for line in status.splitlines()),
            "tracked_diff_sha256": hashlib.sha256(diff.stdout).hexdigest(),
            "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        },
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agreement-report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--evaluator-manifest", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ks", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    args = parser.parse_args()
    try:
        result = score_ranked_runs_against_qrels(
            args.agreement_report,
            args.dataset,
            args.evaluator_manifest,
            args.run,
            args.output,
            catalog_path=args.catalog,
            ks=tuple(args.ks),
            seed=args.seed,
            bootstrap_resamples=args.bootstrap_resamples,
        )
    except (RetrievalQrelsScoringError, SplitPolicyError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result["systems"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
