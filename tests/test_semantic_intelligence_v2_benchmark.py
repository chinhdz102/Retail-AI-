from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.products.snapshot_repository import SnapshotProductRepository
from evaluation import build_semantic_intelligence_v2_frozen as dataset_builder
from evaluation import semantic_intelligence_v2_benchmark as benchmark
from evaluation.export_vietnamese_qrels_review import export_review_queues
from evaluation.semantic_intelligence_v2_benchmark import (
    _asks_for_alternative,
    _recall_at_k,
    _run_conversation_evaluation,
)


def test_recall_at_k_uses_all_relevant_products_as_denominator() -> None:
    assert _recall_at_k(["p1", "p2", "p3", "p4", "p5"], {"p1", "p6", "p7", "p8"}, 5) == 0.25


def test_recall_at_k_uses_the_actual_top_k_window() -> None:
    assert _recall_at_k(
        [
            "irrelevant-1",
            "relevant-1",
            "irrelevant-2",
            "irrelevant-3",
            "irrelevant-4",
            "relevant-2",
        ],
        {"relevant-1", "relevant-2"},
        5,
    ) == 0.5


def test_recall_at_k_marks_empty_gold_as_not_evaluable() -> None:
    assert _recall_at_k(["p1", "p2"], set(), 5) is None


def test_hard_constraint_recall_uses_full_gold_and_raw_top_five(monkeypatch) -> None:
    eligible = [
        ProductSnapshot(
            product_id=f"eligible-{index}",
            name=f"Eligible product {index}",
            category="Meat",
            category_hierarchy="Food > Meat",
            price_vnd=50,
            stock=1,
            brand="Allowed",
        )
        for index in range(1, 9)
    ]
    excluded = [
        ProductSnapshot(
            product_id=f"excluded-{index}",
            name=f"Excluded product {index}",
            category="Meat",
            category_hierarchy="Food > Meat",
            price_vnd=50,
            stock=1,
            brand="Excluded",
        )
        for index in range(1, 5)
    ]
    actual_results = [eligible[0], *excluded, eligible[1], eligible[2]]
    monkeypatch.setattr(
        benchmark,
        "understand_catalog_query",
        lambda *_args, **_kwargs: SimpleNamespace(
            product_type="Meat", budget_vnd=100, negative_constraints=[]
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "execute_catalog_tool",
        lambda *_args, **_kwargs: SimpleNamespace(status="OK", products=actual_results),
    )
    repository = SimpleNamespace(
        products=eligible + excluded,
        list_category_hierarchies=lambda: [],
    )

    result = benchmark._run_deterministic(
        {
            "cases": [
                {
                    "case_id": "multi-gold-hard-constraint",
                    "dimension": "hard_constraint",
                    "query": "find meat below 100",
                    "expected": {
                        "kind": "hard_constraint",
                        "category": "Meat",
                        "excluded_brand": "Excluded",
                        "max_price_vnd": 100,
                    },
                }
            ]
        },
        repository,
    )

    row = result["case_results"][0]
    aggregate = result["aggregate"]["hard_constraint"]
    assert row["valid_results_at_5"] == 1
    assert row["violations"] == 4
    assert row["candidate_recall_at_5"] == 0.125
    assert aggregate["candidate_recall_at_5"] == 0.125
    assert aggregate["candidate_recall_at_5_evaluable_count"] == 1


def test_hard_constraint_recall_is_not_scored_when_no_products_are_eligible(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        benchmark,
        "understand_catalog_query",
        lambda *_args, **_kwargs: SimpleNamespace(
            product_type="No match", budget_vnd=None, negative_constraints=[]
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "execute_catalog_tool",
        lambda *_args, **_kwargs: SimpleNamespace(status="OK", products=[]),
    )
    repository = SimpleNamespace(products=[], list_category_hierarchies=lambda: [])

    result = benchmark._run_deterministic(
        {
            "cases": [
                {
                    "case_id": "no-eligible-products",
                    "dimension": "hard_constraint",
                    "query": "find nonexistent category",
                    "expected": {
                        "kind": "hard_constraint",
                        "category": "No match",
                        "excluded_brand": "Excluded",
                        "max_price_vnd": 100,
                    },
                }
            ]
        },
        repository,
    )

    aggregate = result["aggregate"]["hard_constraint"]
    assert result["case_results"][0]["correct_abstention"] is True
    assert result["case_results"][0]["candidate_recall_at_5"] is None
    assert aggregate["candidate_recall_at_5"] is None
    assert aggregate["candidate_recall_at_5_evaluable_count"] == 0


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


def test_review_queue_pools_retrievers_and_independently_blinds_reviewers(tmp_path) -> None:
    repository = SnapshotProductRepository(dataset_builder.CATALOG)
    products = repository.products[:4]
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
                    },
                    {
                        "case_id": "case-without-retrieval-result",
                        "query": "This query was not run by a retrieval system.",
                        "expected": {"gold_product_id": "ALSO_DO_NOT_SHOW"},
                    },
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
                        {
                            "case_id": "secret-case-1",
                            "product_ids": [products[0].product_id, products[1].product_id],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    alternate_results = tmp_path / "retrieval-variants.json"
    alternate_results.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "secret-case-1",
                        "bm25_top5": [products[1].product_id, products[2].product_id],
                        "dense_top5": [products[3].product_id, products[2].product_id],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exported = export_review_queues(
        dataset,
        [results, alternate_results],
        tmp_path / "queue",
        catalog_path=dataset_builder.CATALOG,
    )
    annotator_a_path = Path(str(exported["annotator_a"]))
    annotator_b_path = Path(str(exported["annotator_b"]))
    annotator_a = annotator_a_path.read_text(encoding="utf-8-sig")
    annotator_b = annotator_b_path.read_text(encoding="utf-8-sig")
    rows_a = list(csv.DictReader(io.StringIO(annotator_a)))
    rows_b = list(csv.DictReader(io.StringIO(annotator_b)))
    assert "DO_NOT_SHOW" not in annotator_a
    assert "rank" not in rows_a[0]
    assert "bm25" not in annotator_a.casefold()
    assert "dense" not in annotator_a.casefold()
    assert "UNREVIEWED" in annotator_a
    assert {row["product_id"] for row in rows_a} == {product.product_id for product in products}
    assert {row["candidate_key"] for row in rows_a} == {
        row["candidate_key"] for row in rows_b
    }
    assert {
        "use_case_fit",
        "audience_fit",
        "variant_fit",
        "quantity_unit_fit",
        "budget_fit",
    } <= set(rows_a[0])
    order_a = [row["candidate_key"] for row in rows_a]
    order_b = [row["candidate_key"] for row in rows_b]
    assert order_a != order_b
    manifest = json.loads(Path(str(exported["evaluator_manifest"])).read_text(encoding="utf-8"))
    assert manifest["candidate_count_by_query"] == {rows_a[0]["query_id"]: 4}
    assert manifest["split_authorization"]["effective_split"] == "development"
    assert manifest["split_authorization"]["eligible_for_blind_claim"] is False
    assert manifest["reviewer_queues"]["source_and_rank_hidden"] is True
    assert exported["skipped_dataset_cases_without_retrieval_results"] == 1

    repeated = export_review_queues(
        dataset,
        [results, alternate_results],
        tmp_path / "queue-repeat",
        catalog_path=dataset_builder.CATALOG,
    )
    assert Path(str(repeated["annotator_a"])).read_bytes() == annotator_a_path.read_bytes()
    assert Path(str(repeated["annotator_b"])).read_bytes() == annotator_b_path.read_bytes()

    capped = export_review_queues(
        dataset,
        [results, alternate_results],
        tmp_path / "queue-capped",
        catalog_path=dataset_builder.CATALOG,
        max_candidates_per_query=3,
    )
    capped_rows = list(
        csv.DictReader(
            io.StringIO(Path(str(capped["annotator_a"])).read_text(encoding="utf-8-sig"))
        )
    )
    assert len(capped_rows) == 3
    assert {row["product_id"] for row in capped_rows} == {
        products[0].product_id,
        products[1].product_id,
        products[3].product_id,
    }


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
