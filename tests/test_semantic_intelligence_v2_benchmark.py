from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation import build_semantic_intelligence_v2_frozen as dataset_builder
from evaluation.export_vietnamese_qrels_review import export_review_queues
from evaluation.semantic_intelligence_v2_benchmark import (
    _asks_for_alternative,
    _run_conversation_evaluation,
)


@pytest.mark.parametrize(
    ("query", "expected_category", "expected"),
    [
        (
            "Mình đang cần Các loại bột khác, cho mình xem vài loại.",
            "Các loại bột khác",
            False,
        ),
        (
            "Còn lựa chọn nào khác cùng nhóm không?",
            "Các loại bột khác",
            True,
        ),
        (
            "Cho mình xem loại khác trong Các loại bột khác.",
            "Các loại bột khác",
            True,
        ),
        (
            "Hai loại này khác nhau thế nào?",
            "Các loại bột khác",
            False,
        ),
        (
            "Do you have other options in Các loại bột khác?",
            "Các loại bột khác",
            True,
        ),
    ],
)
def test_alternative_grader_ignores_taxonomy_label_wording(
    query: str,
    expected_category: str,
    expected: bool,
) -> None:
    assert _asks_for_alternative(query, expected_category) is expected


def test_revised_dev_dataset_build_is_deterministic_and_has_feasible_price_labels(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "semantic-dev.json"
    monkeypatch.setattr(dataset_builder, "OUTPUT", output)
    first_summary = dataset_builder.build()
    first_bytes = output.read_bytes()
    second_summary = dataset_builder.build()
    second_bytes = output.read_bytes()

    assert first_bytes == second_bytes
    assert first_summary["dataset_sha256"] == second_summary["dataset_sha256"]
    payload = json.loads(first_bytes)
    assert "created_at" not in payload
    assert payload["human_review_required"] is True
    assert payload["source_catalog_sha256"]

    repository = SnapshotProductRepository(dataset_builder.CATALOG)
    for case in payload["cases"]:
        expected = case["expected"]
        if expected["kind"] != "hard_constraint":
            continue
        eligible = [
            product
            for product in repository.products
            if normalize_category_text(
                (product.category_hierarchy or product.category or "").split(">")[-1]
            )
            == normalize_category_text(expected["category"])
            and product.price_vnd <= expected["max_price_vnd"]
            and normalize_category_text(product.brand or "")
            != normalize_category_text(expected["excluded_brand"])
        ]
        assert eligible, case["case_id"]


def test_review_queue_exports_separate_blinded_csvs_without_gold_or_rank(tmp_path) -> None:
    repository = SnapshotProductRepository(dataset_builder.CATALOG)
    product = repository.products[0]
    dataset = tmp_path / "dev.json"
    dataset.write_text(
        json.dumps(
            {
                "dataset_id": "dev-fixture",
                "cases": [
                    {
                        "case_id": "secret-case-1",
                        "query": "Tìm giúp mình một món cùng nhóm.",
                        "expected": {"gold_product_id": "DO_NOT_SHOW"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "deterministic_parser_and_catalog": {
                    "case_results": [
                        {"case_id": "secret-case-1", "product_ids": [product.product_id]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    exported = export_review_queues(
        dataset,
        results,
        tmp_path / "queue",
        catalog_path=dataset_builder.CATALOG,
    )
    annotator_a = Path(str(exported["annotator_a"])).read_text(encoding="utf-8-sig")
    annotator_b = Path(str(exported["annotator_b"])).read_text(encoding="utf-8-sig")
    assert annotator_a == annotator_b
    assert "DO_NOT_SHOW" not in annotator_a
    assert "rank" not in annotator_a.casefold()
    assert "UNREVIEWED" in annotator_a
    assert product.product_id in annotator_a


def test_parser_only_conversation_harness_does_not_claim_alternative_novelty() -> None:
    repository = SnapshotProductRepository(dataset_builder.CATALOG)
    category = "Khăn mặt"
    result = _run_conversation_evaluation(
        {
            "conversations": [
                {
                    "conversation_id": "test-alt-harness",
                    "expected_category": category,
                    "turns": [
                        {"user": f"Mình muốn xem vài loại {category}."},
                        {"user": "Còn lựa chọn nào khác cùng nhóm không?"},
                    ],
                }
            ]
        },
        repository,
    )

    assert result["alternative_intent_recognition"]["candidate_novelty_evaluated_cases"] == 0
    assert (
        result["alternative_intent_recognition"]["candidate_novelty_status"]
        == "NOT_EVALUATED_REQUIRES_STATEFUL_API_SESSION"
    )
    assert (
        result["conversation_results"][0]["turn_results"][1]["alternative_novelty_status"]
        == "not_evaluated_without_session_visible_candidate_history"
    )
