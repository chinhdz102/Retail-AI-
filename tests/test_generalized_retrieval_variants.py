from __future__ import annotations

from pathlib import Path

import pytest

from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation.generalized_retrieval_variants import (
    SOURCE,
    _load_runtime_product_docs,
    _paired_bootstrap_comparison,
    _query_metric_values,
    _relevance_cluster_ids,
    _validated_product_cases,
)

ROOT = Path(__file__).resolve().parents[1]


def test_retrieval_ablation_uses_only_validated_runtime_products() -> None:
    documents, repository, _ = _load_runtime_product_docs(SOURCE)

    assert len(documents) == 3081
    assert repository.rows_quarantined == 18
    assert {item["product_id"] for item in documents} == {
        item.product_id for item in repository.products
    }


@pytest.mark.parametrize(
    ("case_file", "expected_count"),
    [
        ("generalized_catalog_consultation_dev_v1.jsonl", 150),
        ("generalized_catalog_consultation_heldout_v1.jsonl", 360),
    ],
)
def test_retrieval_ablation_validates_gold_ids_against_runtime_catalog(
    case_file: str, expected_count: int
) -> None:
    repository = SnapshotProductRepository(SOURCE)
    accepted_ids = {item.product_id for item in repository.products}

    cases = _validated_product_cases(
        ROOT / "evaluation" / "cases" / case_file,
        accepted_ids,
    )

    assert len(cases) == expected_count
    assert all(set(case["gold_product_ids"]) <= accepted_ids for case in cases)


def test_retrieval_ablation_rejects_gold_id_outside_runtime_catalog(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "invalid_gold.jsonl"
    cases_path.write_text(
        '{"case_id":"bad-gold","kind":"product_resolution",'
        '"query":"find a product","gold_product_ids":["NOT_IN_CATALOG"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside the accepted runtime catalog"):
        _validated_product_cases(cases_path, {"A_VALID_SKU"})


def test_recall_at_k_counts_fraction_of_all_relevant_ids() -> None:
    scores = _query_metric_values(
        ["SKU_A", "SKU_X", "SKU_Y"],
        {"SKU_A", "SKU_B"},
    )

    assert scores["recall_at_1"] == 0.5
    assert scores["recall_at_5"] == 0.5
    assert scores["mrr_at_5"] == 1.0


def test_paired_bootstrap_reports_query_wins_losses_ties_and_is_reproducible() -> None:
    reference = [
        {"recall_at_1": value, "recall_at_5": value, "mrr_at_5": value, "ndcg_at_5": value}
        for value in (1.0, 0.5, 0.0, 0.0)
    ]
    candidate = [
        {"recall_at_1": value, "recall_at_5": value, "mrr_at_5": value, "ndcg_at_5": value}
        for value in (0.0, 1.0, 0.0, 0.5)
    ]

    clusters = ["sku-a", "sku-a", "sku-b", "sku-b"]
    first = _paired_bootstrap_comparison(
        reference, candidate, cluster_ids=clusters, seed=17, resamples=500
    )
    second = _paired_bootstrap_comparison(
        reference, candidate, cluster_ids=clusters, seed=17, resamples=500
    )

    assert first == second
    assert first["n_queries"] == 4
    assert first["n_clusters"] == 2
    assert first["metrics"]["recall_at_1"]["mean_delta_candidate_minus_reference"] == 0.0
    assert first["metrics"]["recall_at_1"]["candidate_wins"] == 2
    assert first["metrics"]["recall_at_1"]["reference_wins"] == 1
    assert first["metrics"]["recall_at_1"]["ties"] == 1
    assert len(first["metrics"]["recall_at_1"]["bootstrap_95_percentile_ci"]) == 2


def test_relevance_clusters_merge_queries_sharing_a_relevant_sku() -> None:
    clusters = _relevance_cluster_ids(
        [{"SKU_A"}, {"SKU_A"}, {"SKU_B"}, {"SKU_A", "SKU_B"}, {"SKU_C"}]
    )

    assert clusters[0] == clusters[1] == clusters[2] == clusters[3]
    assert clusters[4] != clusters[0]


def test_paired_bootstrap_rejects_unpaired_or_too_small_samples() -> None:
    scores = [{"recall_at_1": 1.0, "recall_at_5": 1.0, "mrr_at_5": 1.0, "ndcg_at_5": 1.0}]

    with pytest.raises(ValueError, match="equally sized"):
        _paired_bootstrap_comparison(scores, [])
    with pytest.raises(ValueError, match="At least 100"):
        _paired_bootstrap_comparison(scores, scores, resamples=99)
    with pytest.raises(ValueError, match="align one-to-one"):
        _paired_bootstrap_comparison(scores, scores, cluster_ids=[])
