from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation.generalized_retrieval_variants import (
    SOURCE,
    _load_runtime_product_docs,
    _paired_bootstrap_comparison,
    _production_tool_rank,
    _query_metric_values,
    _relevance_cluster_ids,
    _validated_product_cases,
)
from evaluation.generalized_retrieval_variants import (
    run as run_retrieval_variants,
)
from evaluation.production_tool_search_benchmark import run as run_production_tool_benchmark

ROOT = Path(__file__).resolve().parents[1]


def test_retrieval_ablation_uses_only_validated_runtime_products() -> None:
    documents, repository, _ = _load_runtime_product_docs(SOURCE)

    assert len(documents) == 3081
    assert repository.rows_quarantined == 18
    assert {item["product_id"] for item in documents} == {
        item.product_id for item in repository.products
    }


def test_retrieval_ablation_includes_deterministic_production_search_tool() -> None:
    repository = SnapshotProductRepository(SOURCE)

    product_ids, status = _production_tool_rank(
        "Mình muốn tìm Bộ đồ chơi robot biến hình khủng long BKK-8285",
        repository,
    )

    assert status == "SUCCESS"
    assert product_ids[0] == "WIN_10330593"
    assert len(product_ids) <= 5


def test_production_tool_benchmark_writes_reproducible_case_and_metrics_artifact(
    tmp_path: Path,
) -> None:
    case_path = tmp_path / "one_case.jsonl"
    case_path.write_text(
        '{"case_id":"tool-benchmark-smoke","kind":"product_resolution",'
        '"variant":"conversational_exact",'
        '"query":"Mình muốn tìm Bộ đồ chơi robot biến hình khủng long BKK-8285",'
        '"gold_product_ids":["WIN_10330593"]}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "benchmark.json"

    result = run_production_tool_benchmark(SOURCE, case_path, output_path)

    assert output_path.is_file()
    assert result["system"]["external_llm_or_provider"] is False
    assert result["query_set"]["query_count"] == 1
    assert result["metrics"]["overall"]["recall_at_1"] == 1.0
    assert result["cases"][0]["retrieved_product_ids_top5"][0] == "WIN_10330593"


def test_retrieval_ablation_reports_production_baseline_without_downloading_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVector:
        def __init__(self, value: float) -> None:
            self.value = value

        def __matmul__(self, other: FakeVector) -> float:
            return self.value * other.value

    class FakeTextEmbedding:
        @staticmethod
        def add_custom_model(**_: object) -> None:
            return None

        def __init__(self, *, model_name: str) -> None:
            assert model_name

        def embed(self, texts: list[str]) -> list[FakeVector]:
            return [FakeVector(float(len(text))) for text in texts]

        def query_embed(self, texts: list[str]):
            return iter(FakeVector(float(len(text))) for text in texts)

    class FakeModelSource:
        def __init__(self, **_: object) -> None:
            return None

    fastembed = types.ModuleType("fastembed")
    fastembed.TextEmbedding = FakeTextEmbedding
    common = types.ModuleType("fastembed.common")
    common.__path__ = []
    model_description = types.ModuleType("fastembed.common.model_description")
    model_description.ModelSource = FakeModelSource
    model_description.PoolingType = types.SimpleNamespace(MEAN="mean")
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)
    monkeypatch.setitem(sys.modules, "fastembed.common", common)
    monkeypatch.setitem(sys.modules, "fastembed.common.model_description", model_description)

    case_path = tmp_path / "one_case.jsonl"
    case_path.write_text(
        '{"case_id":"tool-ablation-smoke","kind":"product_resolution",'
        '"variant":"conversational_exact",'
        '"query":"Mình muốn tìm Bộ đồ chơi robot biến hình khủng long BKK-8285",'
        '"gold_product_ids":["WIN_10330593"]}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "retrieval_variants.json"

    result = run_retrieval_variants(SOURCE, case_path, output_path)

    assert output_path.is_file()
    assert "production_snapshot_search_tool" in result["metrics"]
    assert "production_snapshot_search_tool_vs_bm25" in result["paired_comparisons"][
        "candidate_minus_reference"
    ]


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
