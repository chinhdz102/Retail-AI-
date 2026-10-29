from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.data.catalog_import import canonical_dataset_sha256
from evaluation.score_vietnamese_qrels_retrieval import (
    RetrievalQrelsScoringError,
    _paired_comparisons,
    score_ranked_runs_against_qrels,
)
from evaluation.score_vietnamese_qrels_review import FIT_FIELDS


def _write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _judgments(relevance: str, **overrides: str) -> dict[str, str]:
    values = {"relevance": relevance, **{field: "NOT_APPLICABLE" for field in FIT_FIELDS}}
    values.update(overrides)
    return values


def _build_inputs(
    tmp_path: Path,
    *,
    qrels: list[tuple[str, str, str, dict[str, str] | None]],
    rankings: dict[str, list[list[str]]] | None = None,
    run_catalog_sha: str | None = None,
) -> tuple[Path, Path, Path, Path, Path, dict[str, str]]:
    dataset_path = _write_json(
        tmp_path / "toy_dataset.json",
        {
            "dataset_id": "toy_dataset_v1",
            "cases": [
                {"case_id": "q1", "query": "Tìm sản phẩm phù hợp thứ nhất"},
                {"case_id": "q2", "query": "Tìm sản phẩm phù hợp thứ hai"},
            ],
        },
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("[]\n", encoding="utf-8")
    dataset_sha = canonical_dataset_sha256(dataset_path.read_bytes())
    catalog_sha = canonical_dataset_sha256(catalog_path.read_bytes())
    query_ids = {
        case_id: hashlib.sha256(f"{dataset_sha}:{case_id}".encode()).hexdigest()[:16]
        for case_id in ("q1", "q2")
    }
    items = []
    for case_id, product_id, resolution_status, resolved in qrels:
        items.append(
            {
                "query_id": query_ids[case_id],
                "candidate_key": f"{case_id}:{product_id}",
                "query_text": f"Tìm sản phẩm phù hợp thứ {'nhất' if case_id == 'q1' else 'hai'}",
                "product_id": product_id,
                "dataset_id": "toy_dataset_v1",
                "dataset_sha256": dataset_sha,
                "catalog_sha256": catalog_sha,
                "split": "development",
                "resolution_status": resolution_status,
                "resolved_judgments": resolved,
            }
        )
    agreement_path = _write_json(
        tmp_path / "agreement.json",
        {
            "schema_version": "vietnamese_product_qrels_agreement_v1",
            "dataset": {
                "dataset_id": "toy_dataset_v1",
                "dataset_sha256": dataset_sha,
                "catalog_sha256": catalog_sha,
                "split": "development",
            },
            "items": items,
        },
    )
    by_system = rankings or {
        "bm25": [["p3", "p2", "p1"], ["p5", "p6", "unknown-q2"]],
        "dense": [["p1", "p2", "p3"], ["p5", "p6", "unknown-q2"]],
    }
    run_cases = []
    for case_index, case_id in enumerate(("q1", "q2")):
        row: dict[str, Any] = {
            "case_id": case_id,
            "query": f"Tìm sản phẩm phù hợp thứ {'nhất' if case_id == 'q1' else 'hai'}",
        }
        for system_name, per_case in by_system.items():
            row[f"{system_name}_top5"] = per_case[case_index]
        run_cases.append(row)
    run_path = _write_json(
        tmp_path / "retrieval.json",
        {
            "kind": "generalized_retrieval_variants",
            "query_set": {"case_file_canonical_sha256": dataset_sha},
            "catalog": {"canonical_content_sha256": run_catalog_sha or catalog_sha},
            "cases": run_cases,
        },
    )
    manifest_path = _write_json(
        tmp_path / "evaluator_manifest.json",
        {
            "dataset_id": "toy_dataset_v1",
            "dataset_sha256": dataset_sha,
            "catalog_sha256": catalog_sha,
            "split": "development",
            "candidate_artifact_sha256": [canonical_dataset_sha256(run_path.read_bytes())],
        },
    )
    output_path = tmp_path / "metrics.json"
    return (
        agreement_path,
        dataset_path,
        catalog_path,
        manifest_path,
        run_path,
        {
            "output": str(output_path),
            **query_ids,
        },
    )


def _score(
    paths: tuple[Path, Path, Path, Path, Path, dict[str, str]],
    *,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    agreement, dataset, catalog, manifest, run, meta = paths
    return score_ranked_runs_against_qrels(
        agreement,
        dataset,
        manifest,
        [run],
        Path(meta["output"]),
        catalog_path=catalog,
        ks=ks,
        bootstrap_resamples=100,
    )


def _qrels() -> list[tuple[str, str, str, dict[str, str] | None]]:
    return [
        ("q1", "p1", "AGREED", _judgments("EXACT")),
        ("q1", "p2", "AGREED", _judgments("SUBSTITUTE")),
        ("q1", "p3", "AGREED", _judgments("COMPLEMENT")),
        (
            "q1",
            "p4",
            "AGREED",
            _judgments("IRRELEVANT", negation_fit="FAIL", price_fit="FAIL"),
        ),
        ("q1", "p9", "AGREED", _judgments("UNJUDGED")),
        ("q2", "p5", "AGREED", _judgments("EXACT")),
        ("q2", "p6", "AGREED", _judgments("IRRELEVANT")),
    ]


def test_graded_metrics_use_fixed_esci_grades_and_only_fully_judged_top_k(
    tmp_path: Path,
) -> None:
    result = _score(_build_inputs(tmp_path, qrels=_qrels()))
    bm25 = result["systems"]["bm25"]["per_k"]["3"]

    assert bm25["status"] == "PARTIAL_JUDGMENT_OR_RUN_COVERAGE"
    assert bm25["coverage"] == {
        "qrels_query_count": 2,
        "scored_query_count": 1,
        "scored_query_rate": 0.5,
    }
    scored = next(row for row in bm25["per_query"].values() if row["status"] == "SCORED")
    assert scored["metrics"]["precision"] == pytest.approx(2 / 3)
    expected_ndcg = (1 + 3 / math.log2(3) + 7 / math.log2(4)) / (
        7 + 3 / math.log2(3) + 1 / math.log2(4)
    )
    assert scored["metrics"]["ndcg"] == pytest.approx(expected_ndcg)
    assert scored["metrics"]["recall"] == 1.0
    assert scored["metrics"]["mrr"] == 0.5
    incomplete = next(
        row for row in bm25["per_query"].values() if row["status"] == "INCOMPLETE_UNJUDGED_TOP_K"
    )
    assert incomplete["unjudged_product_ids"] == ["unknown-q2"]
    assert result["label_provenance"]["human_suitability_claim"] is False


def test_reviewer_flagged_fit_failures_are_counted_per_field(tmp_path: Path) -> None:
    rankings = {"bm25": [["p3", "p2", "p1", "p4"], ["p5", "p6"]]}
    result = _score(_build_inputs(tmp_path, qrels=_qrels(), rankings=rankings), ks=(4,))
    metric = result["systems"]["bm25"]["per_k"]["4"]

    assert metric["reviewer_flagged_fit_failures"]["negation_fit"]["fail"] == 1
    assert metric["reviewer_flagged_fit_failures"]["price_fit"]["failure_rate"] == 1.0


def test_metrics_are_null_when_no_resolved_relevance_judgments_exist(tmp_path: Path) -> None:
    qrels = [("q1", "p1", "AGREED", _judgments("UNJUDGED"))]
    result = _score(_build_inputs(tmp_path, qrels=qrels))
    metric = result["systems"]["bm25"]["per_k"]["1"]

    assert metric["status"] == "NOT_AVAILABLE_NO_RESOLVED_RELEVANCE_JUDGMENTS"
    assert metric["metrics_macro_average"] == {
        "ndcg": None,
        "precision": None,
        "recall": None,
        "mrr": None,
    }
    assert result["qrels_coverage"]["reviewed_relevance_pairs"] == 0
    assert result["qrels_coverage"]["unjudged_pairs"] == 1


def test_unresolved_adjudication_does_not_become_an_irrelevant_label(tmp_path: Path) -> None:
    qrels = [("q1", "p1", "UNRESOLVED", None)]
    result = _score(_build_inputs(tmp_path, qrels=qrels))
    per_query = next(iter(result["systems"]["bm25"]["per_k"]["1"]["per_query"].values()))

    assert per_query["status"] == "NO_RESOLVED_QUERY_JUDGMENTS"
    assert per_query["metrics"] is None
    assert result["qrels_coverage"]["unresolved_pairs"] == 1


def test_qrels_query_text_must_match_the_frozen_dataset(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path, qrels=_qrels())
    agreement, dataset, catalog, manifest, run, meta = paths
    report = json.loads(agreement.read_text(encoding="utf-8"))
    report["items"][0]["query_text"] = "Tìm một thứ hoàn toàn khác"
    _write_json(agreement, report)

    with pytest.raises(RetrievalQrelsScoringError, match="Qrels query"):
        score_ranked_runs_against_qrels(
            agreement,
            dataset,
            manifest,
            [run],
            Path(meta["output"]),
            catalog_path=catalog,
            ks=(1,),
        )


def test_k_above_artifact_depth_is_not_reported_as_a_metric(tmp_path: Path) -> None:
    result = _score(_build_inputs(tmp_path, qrels=_qrels()), ks=(10,))
    metric = result["systems"]["bm25"]["per_k"]["10"]

    assert metric["status"] == "INSUFFICIENT_RUN_DEPTH"
    assert metric["metrics_macro_average"]["ndcg"] is None


def test_catalog_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path, qrels=_qrels(), run_catalog_sha="f" * 64)
    agreement, dataset, catalog, manifest, run, meta = paths

    with pytest.raises(RetrievalQrelsScoringError, match="Catalog SHA-256 mismatch"):
        score_ranked_runs_against_qrels(
            agreement,
            dataset,
            manifest,
            [run],
            Path(meta["output"]),
            catalog_path=catalog,
            ks=(1,),
        )


def test_run_artifacts_must_match_frozen_candidate_pool_manifest(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path, qrels=_qrels())
    agreement, dataset, catalog, manifest, run, meta = paths
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["candidate_artifact_sha256"] = ["0" * 64]
    _write_json(manifest, manifest_data)

    with pytest.raises(RetrievalQrelsScoringError, match="candidate-pool manifest"):
        score_ranked_runs_against_qrels(
            agreement,
            dataset,
            manifest,
            [run],
            Path(meta["output"]),
            catalog_path=catalog,
            ks=(1,),
        )


def test_duplicate_ranking_candidate_fails_closed(tmp_path: Path) -> None:
    rankings = {"bm25": [["p1", "p1"], ["p5"]]}
    with pytest.raises(RetrievalQrelsScoringError, match="duplicate product IDs"):
        _score(_build_inputs(tmp_path, qrels=_qrels(), rankings=rankings), ks=(1,))


def test_paired_bootstrap_is_reproducible_when_at_least_30_queries_are_scored() -> None:
    reference = {f"q{index}": {"status": "SCORED", "metrics": {"ndcg": 0.5}} for index in range(30)}
    candidate = {
        f"q{index}": {
            "status": "SCORED",
            "metrics": {"ndcg": 0.6 if index < 15 else 0.4},
        }
        for index in range(30)
    }
    systems = {
        "bm25": {"per_k": {"5": {"per_query": reference}}},
        "dense": {"per_k": {"5": {"per_query": candidate}}},
    }

    first = _paired_comparisons(systems, ks=(5,), seed=23, bootstrap_resamples=100)[
        "bm25_vs_dense"
    ]["5"]
    second = _paired_comparisons(systems, ks=(5,), seed=23, bootstrap_resamples=100)[
        "bm25_vs_dense"
    ]["5"]

    assert first["paired_query_count"] == 30
    assert first["improved_queries"] == 15
    assert first["degraded_queries"] == 15
    assert first["bootstrap_95_percentile_ci"] == second["bootstrap_95_percentile_ci"]
    assert first["bootstrap_95_percentile_ci"] is not None


def test_cli_writes_reproducible_metrics_artifact(tmp_path: Path) -> None:
    agreement, dataset, catalog, manifest, run, meta = _build_inputs(tmp_path, qrels=_qrels())
    output = Path(meta["output"])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.score_vietnamese_qrels_retrieval",
            "--agreement-report",
            str(agreement),
            "--dataset",
            str(dataset),
            "--evaluator-manifest",
            str(manifest),
            "--catalog",
            str(catalog),
            "--run",
            str(run),
            "--output",
            str(output),
            "--ks",
            "1",
            "3",
            "--bootstrap-resamples",
            "100",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "vietnamese_qrels_ranked_retrieval_metrics_v1"
    assert artifact["run_metadata"]["git_commit_sha"]
    assert artifact["evaluator_only_manifest"]["sha256"]
