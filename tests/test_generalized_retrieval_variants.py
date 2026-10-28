from __future__ import annotations

from pathlib import Path

import pytest

from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation.generalized_retrieval_variants import (
    SOURCE,
    _load_runtime_product_docs,
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
