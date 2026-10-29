from __future__ import annotations

import csv
from pathlib import Path

import pytest

from evaluation.export_vietnamese_qrels_review import (
    REVIEW_FIELDS,
    REVIEW_METADATA_FIELDS,
)
from evaluation.score_vietnamese_qrels_review import (
    REQUIRED_FIELDS,
    ReviewScoringError,
    score_product_qrels_review,
)


def _row(
    *,
    status: str,
    reviewer_id: str,
    candidate_key: str = "candidate-1",
    relevance: str = "EXACT",
    price_fit: str = "PASS",
) -> dict[str, str]:
    row = {
        "query_id": "query-1",
        "candidate_key": candidate_key,
        "query_text": "Mình cần một sản phẩm phù hợp.",
        "product_id": "SKU-1",
        "product_name": "Sản phẩm mẫu",
        "product_brand": "Nhãn mẫu",
        "product_category": "Nhóm hàng mẫu",
        "product_price_vnd": "25000",
        "evidence_json": '{"name":"Sản phẩm mẫu","price_vnd":25000}',
        "dataset_id": "dev-v1",
        "dataset_sha256": "a" * 64,
        "catalog_sha256": "b" * 64,
        "split": "development",
        "reviewer_status": status,
        "reviewer_id": reviewer_id,
        "reviewed_at": "2026-10-29T10:00:00+07:00",
        "relevance": relevance,
        "category_fit": "PASS",
        "identity_fit": "PASS",
        "brand_fit": "NOT_APPLICABLE",
        "price_fit": price_fit,
        "negation_fit": "NOT_APPLICABLE",
        "availability_fit": "UNKNOWN",
        "use_case_fit": "UNKNOWN",
        "audience_fit": "NOT_APPLICABLE",
        "variant_fit": "PASS",
        "quantity_unit_fit": "PASS",
        "budget_fit": "NOT_APPLICABLE",
        "clarification_expected": "false",
        "abstention_expected": "false",
        "confidence_1_to_5": "4",
        "evidence_fields": "name,price_vnd",
        "rationale": "Tên và giá khớp với thông tin snapshot được cung cấp.",
    }
    assert set(row) == set(REQUIRED_FIELDS)
    assert set(REVIEW_FIELDS) <= set(row)
    assert set(REVIEW_METADATA_FIELDS) <= set(row)
    return row


def _write_queue(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_complete_agreement_preserves_reviews_and_reports_nominal_kappa_undefined(
    tmp_path: Path,
) -> None:
    reviewer_a = _write_queue(
        tmp_path / "a.csv", [_row(status="INDEPENDENT_A", reviewer_id="reviewer-a")]
    )
    reviewer_b = _write_queue(
        tmp_path / "b.csv", [_row(status="INDEPENDENT_B", reviewer_id="reviewer-b")]
    )

    output = tmp_path / "agreement.json"
    report = score_product_qrels_review(reviewer_a, reviewer_b, output)
    item = report["items"][0]

    assert item["resolution_status"] == "AGREED"
    assert item["reviewer_a"]["judgments"]["relevance"] == "EXACT"
    assert item["reviewer_b"]["judgments"]["relevance"] == "EXACT"
    assert item["resolved_judgments"]["relevance"] == "EXACT"
    assert report["agreement"]["relevance"]["agreement_rate"] == 1.0
    assert report["agreement"]["relevance"]["cohen_kappa_nominal"] is None
    assert report["summary"]["eligible_for_validation_or_blind_claim"] is False
    assert output.is_file()


def test_disagreement_stays_unresolved_until_a_distinct_adjudicator_reviews_it(
    tmp_path: Path,
) -> None:
    reviewer_a = _write_queue(
        tmp_path / "a.csv", [_row(status="INDEPENDENT_A", reviewer_id="reviewer-a")]
    )
    reviewer_b = _write_queue(
        tmp_path / "b.csv",
        [
            _row(
                status="INDEPENDENT_B",
                reviewer_id="reviewer-b",
                relevance="SUBSTITUTE",
                price_fit="UNKNOWN",
            )
        ],
    )
    adjudication = _write_queue(
        tmp_path / "adjudication.csv",
        [
            _row(
                status="ADJUDICATED",
                reviewer_id="reviewer-c",
                relevance="SUBSTITUTE",
                price_fit="PASS",
            )
        ],
    )

    unresolved = score_product_qrels_review(reviewer_a, reviewer_b, tmp_path / "unresolved.json")
    assert unresolved["items"][0]["resolution_status"] == "UNRESOLVED"
    assert unresolved["items"][0]["resolved_judgments"] is None
    assert "relevance" in unresolved["items"][0]["disagreement_fields"]
    assert "price_fit" in unresolved["items"][0]["disagreement_fields"]

    adjudicated = score_product_qrels_review(
        reviewer_a,
        reviewer_b,
        tmp_path / "adjudicated.json",
        adjudication,
    )
    item = adjudicated["items"][0]
    assert item["resolution_status"] == "ADJUDICATED"
    assert item["reviewer_a"]["judgments"]["relevance"] == "EXACT"
    assert item["reviewer_b"]["judgments"]["relevance"] == "SUBSTITUTE"
    assert item["adjudication"]["reviewer_id"] == "reviewer-c"
    assert item["resolved_judgments"]["relevance"] == "SUBSTITUTE"
    assert adjudicated["summary"]["items_adjudicated"] == 1


def test_blank_export_queue_is_rejected_instead_of_emitting_fake_agreement(tmp_path: Path) -> None:
    reviewer_a = _write_queue(
        tmp_path / "a.csv", [_row(status="UNREVIEWED", reviewer_id="reviewer-a")]
    )
    reviewer_b = _write_queue(
        tmp_path / "b.csv", [_row(status="UNREVIEWED", reviewer_id="reviewer-b")]
    )

    with pytest.raises(ReviewScoringError, match="reviewer_status"):
        score_product_qrels_review(reviewer_a, reviewer_b, tmp_path / "must-not-exist.json")


def test_missing_annotation_fields_are_rejected_with_row_and_field(tmp_path: Path) -> None:
    incomplete = _row(status="INDEPENDENT_A", reviewer_id="reviewer-a")
    incomplete["use_case_fit"] = ""
    reviewer_a = _write_queue(tmp_path / "a.csv", [incomplete])

    with pytest.raises(ReviewScoringError, match="use_case_fit"):
        score_product_qrels_review(
            reviewer_a,
            _write_queue(
                tmp_path / "b.csv", [_row(status="INDEPENDENT_B", reviewer_id="reviewer-b")]
            ),
            tmp_path / "must-not-exist.json",
        )


def test_mismatched_review_packets_fail_closed(tmp_path: Path) -> None:
    reviewer_a = _write_queue(
        tmp_path / "a.csv", [_row(status="INDEPENDENT_A", reviewer_id="reviewer-a")]
    )
    reviewer_b = _write_queue(
        tmp_path / "b.csv",
        [
            _row(
                status="INDEPENDENT_B",
                reviewer_id="reviewer-b",
                candidate_key="different-candidate",
            )
        ],
    )

    with pytest.raises(ReviewScoringError, match="item sets differ"):
        score_product_qrels_review(reviewer_a, reviewer_b, tmp_path / "must-not-exist.json")


def test_adjudicator_cannot_self_adjudicate_or_resolve_unannotated_items(tmp_path: Path) -> None:
    reviewer_a = _write_queue(
        tmp_path / "a.csv", [_row(status="INDEPENDENT_A", reviewer_id="reviewer-a")]
    )
    reviewer_b = _write_queue(
        tmp_path / "b.csv",
        [
            _row(
                status="INDEPENDENT_B",
                reviewer_id="reviewer-b",
                relevance="SUBSTITUTE",
            )
        ],
    )
    self_adjudication = _write_queue(
        tmp_path / "self.csv",
        [
            _row(
                status="ADJUDICATED",
                reviewer_id="reviewer-a",
                relevance="EXACT",
            )
        ],
    )

    with pytest.raises(ReviewScoringError, match="distinct pseudonymous reviewer IDs"):
        score_product_qrels_review(
            reviewer_a,
            reviewer_b,
            tmp_path / "must-not-exist.json",
            self_adjudication,
        )
