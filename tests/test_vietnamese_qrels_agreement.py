from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from backend.data.catalog_import import canonical_dataset_sha256
from evaluation import export_vietnamese_qrels_review as qrels_exporter
from evaluation import score_vietnamese_qrels_review as qrels_scoring
from evaluation.export_vietnamese_qrels_review import (
    REVIEW_FIELDS,
    REVIEW_METADATA_FIELDS,
    _frozen_assignment_sha256,
    export_review_queues,
)
from evaluation.score_vietnamese_qrels_review import (
    REQUIRED_FIELDS,
    ReviewScoringError,
    score_product_qrels_review,
)
from evaluation.split_policy import SplitPolicyError


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


def _development_fixture(
    tmp_path: Path,
    rows_a: list[dict[str, str]],
    rows_b: list[dict[str, str]],
    adjudication_rows: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, Path, Path, Path | None]:
    dataset = tmp_path / "development.json"
    cases = [
        {"case_id": f"case-{index}", "query": row["query_text"]}
        for index, row in enumerate(rows_a, start=1)
    ]
    dataset.write_text(
        json.dumps(
            {"dataset_id": "development-fixture-v1", "cases": cases},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    catalog_sha = rows_a[0]["catalog_sha256"]
    assignments = []
    shared_fields = (
        "query_text",
        "product_id",
        "product_name",
        "product_brand",
        "product_category",
        "product_price_vnd",
        "evidence_json",
        "catalog_sha256",
    )
    for index, (row_a, row_b) in enumerate(zip(rows_a, rows_b, strict=True), start=1):
        case_id = f"case-{index}"
        query_id = hashlib.sha256(f"{dataset_sha}:{case_id}".encode()).hexdigest()[:16]
        product_id = row_a["product_id"]
        candidate_key = hashlib.sha256(
            f"{query_id}:{product_id or 'NO_CANDIDATE'}".encode()
        ).hexdigest()[:20]
        for row in (row_a, row_b):
            row["query_id"] = query_id
            row["candidate_key"] = candidate_key
            row["dataset_id"] = "development-fixture-v1"
            row["dataset_sha256"] = dataset_sha
            row["split"] = "development"
        for field in shared_fields:
            row_b[field] = row_a[field]
        assignment = {
            "query_id": query_id,
            "candidate_key": candidate_key,
            "case_id": case_id,
            "product_id": product_id,
            "query_text": row_a["query_text"],
            "frozen_row_sha256": _frozen_assignment_sha256(row_a),
        }
        assignments.append(assignment)
        if adjudication_rows is not None:
            adjudication_row = adjudication_rows[index - 1]
            for field in ("query_id", "candidate_key", "dataset_id", "dataset_sha256", "split"):
                adjudication_row[field] = row_a[field]
            for field in shared_fields:
                adjudication_row[field] = row_a[field]

    manifest_path = tmp_path / "development_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "artifact_id": "vietnamese_product_qrels_review_packet",
                "schema_version": "1.0",
                "dataset_id": "development-fixture-v1",
                "dataset_sha256": dataset_sha,
                "catalog_sha256": catalog_sha,
                "split": "development",
                "candidate_assignments": assignments,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    queue_a_path = _write_queue(tmp_path / "a.csv", rows_a)
    queue_b_path = _write_queue(tmp_path / "b.csv", rows_b)
    adjudication_path = (
        _write_queue(tmp_path / "adjudication.csv", adjudication_rows)
        if adjudication_rows is not None
        else None
    )
    return dataset, manifest_path, queue_a_path, queue_b_path, adjudication_path


def _protected_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, str], dict[str, str], Path]:
    dataset = tmp_path / "blind.json"
    dataset.write_text(
        json.dumps(
            {
                "dataset_id": "blind-v1",
                "cases": [
                    {"case_id": "case-1", "query": "Mình cần một sản phẩm phù hợp."},
                    {"case_id": "case-2", "query": "Mình cần một loại khác."},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    query_id = hashlib.sha256(f"{dataset_sha}:case-1".encode()).hexdigest()[:16]
    candidate_key = hashlib.sha256(f"{query_id}:SKU-1".encode()).hexdigest()[:20]
    row_a = _row(status="INDEPENDENT_A", reviewer_id="reviewer-a", candidate_key=candidate_key)
    row_b = _row(status="INDEPENDENT_B", reviewer_id="reviewer-b", candidate_key=candidate_key)
    second_query_id = hashlib.sha256(f"{dataset_sha}:case-2".encode()).hexdigest()[:16]
    second_candidate_key = hashlib.sha256(f"{second_query_id}:SKU-2".encode()).hexdigest()[:20]
    row_a_second = _row(
        status="INDEPENDENT_A", reviewer_id="reviewer-a", candidate_key=second_candidate_key
    )
    row_b_second = _row(
        status="INDEPENDENT_B", reviewer_id="reviewer-b", candidate_key=second_candidate_key
    )
    for row in (row_a, row_b):
        row["query_id"] = query_id
        row["dataset_id"] = "blind-v1"
        row["dataset_sha256"] = dataset_sha
        row["split"] = "blind_test"
    for row in (row_a_second, row_b_second):
        row["query_id"] = second_query_id
        row["candidate_key"] = second_candidate_key
        row["query_text"] = "Mình cần một loại khác."
        row["product_id"] = "SKU-2"
        row["product_name"] = "Sản phẩm mẫu thứ hai"
        row["dataset_id"] = "blind-v1"
        row["dataset_sha256"] = dataset_sha
        row["split"] = "blind_test"
        row["evidence_json"] = '{"name":"Sản phẩm mẫu thứ hai","price_vnd":25000}'
    queue_a = _write_queue(tmp_path / "a.csv", [row_a, row_a_second])
    queue_b = _write_queue(tmp_path / "b.csv", [row_b, row_b_second])
    manifest = {
        "artifact_id": "vietnamese_product_qrels_review_packet",
        "schema_version": "1.0",
        "dataset_id": "blind-v1",
        "dataset_sha256": dataset_sha,
        "catalog_sha256": "b" * 64,
        "split": "blind_test",
        "candidate_assignments": [
            {
                "query_id": query_id,
                "candidate_key": candidate_key,
                "case_id": "case-1",
                "product_id": "SKU-1",
                "query_text": row_a["query_text"],
                "frozen_row_sha256": _frozen_assignment_sha256(row_a),
            },
            {
                "query_id": second_query_id,
                "candidate_key": second_candidate_key,
                "case_id": "case-2",
                "product_id": "SKU-2",
                "query_text": row_a_second["query_text"],
                "frozen_row_sha256": _frozen_assignment_sha256(row_a_second),
            },
        ],
    }
    manifest_path = tmp_path / "evaluator_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return dataset, manifest_path, queue_a, row_a, row_b, queue_b


def test_complete_agreement_preserves_reviews_and_reports_nominal_kappa_undefined(
    tmp_path: Path,
) -> None:
    dataset, manifest, reviewer_a, reviewer_b, _ = _development_fixture(
        tmp_path,
        [_row(status="INDEPENDENT_A", reviewer_id="reviewer-a")],
        [_row(status="INDEPENDENT_B", reviewer_id="reviewer-b")],
    )

    output = tmp_path / "agreement.json"
    report = score_product_qrels_review(
        reviewer_a,
        reviewer_b,
        output,
        dataset_path=dataset,
        evaluator_manifest_path=manifest,
    )
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
    dataset, manifest, reviewer_a, reviewer_b, adjudication = _development_fixture(
        tmp_path,
        [_row(status="INDEPENDENT_A", reviewer_id="reviewer-a")],
        [
            _row(
                status="INDEPENDENT_B",
                reviewer_id="reviewer-b",
                relevance="SUBSTITUTE",
                price_fit="UNKNOWN",
            )
        ],
        [
            _row(
                status="ADJUDICATED",
                reviewer_id="reviewer-c",
                relevance="SUBSTITUTE",
                price_fit="PASS",
            )
        ],
    )

    unresolved = score_product_qrels_review(
        reviewer_a,
        reviewer_b,
        tmp_path / "unresolved.json",
        dataset_path=dataset,
        evaluator_manifest_path=manifest,
    )
    assert unresolved["items"][0]["resolution_status"] == "UNRESOLVED"
    assert unresolved["items"][0]["resolved_judgments"] is None
    assert "relevance" in unresolved["items"][0]["disagreement_fields"]
    assert "price_fit" in unresolved["items"][0]["disagreement_fields"]

    adjudicated = score_product_qrels_review(
        reviewer_a,
        reviewer_b,
        tmp_path / "adjudicated.json",
        adjudication,
        dataset,
        manifest,
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


@pytest.mark.parametrize(
    ("metadata_field", "other_value"),
    [
        ("dataset_id", "different-dataset"),
        ("dataset_sha256", "c" * 64),
        ("catalog_sha256", "d" * 64),
        ("split", "blind_test"),
    ],
)
def test_reviewer_queue_must_not_mix_datasets_or_splits(
    tmp_path: Path,
    metadata_field: str,
    other_value: str,
) -> None:
    row_a_first = _row(status="INDEPENDENT_A", reviewer_id="reviewer-a")
    row_a_second = _row(
        status="INDEPENDENT_A", reviewer_id="reviewer-a", candidate_key="candidate-2"
    )
    row_a_second[metadata_field] = other_value
    row_b_first = _row(status="INDEPENDENT_B", reviewer_id="reviewer-b")
    row_b_second = _row(
        status="INDEPENDENT_B", reviewer_id="reviewer-b", candidate_key="candidate-2"
    )
    row_b_second[metadata_field] = other_value

    with pytest.raises(ReviewScoringError, match="mixes frozen dataset/catalog metadata"):
        score_product_qrels_review(
            _write_queue(tmp_path / "a.csv", [row_a_first, row_a_second]),
            _write_queue(tmp_path / "b.csv", [row_b_first, row_b_second]),
            tmp_path / "must-not-exist.json",
        )


def test_qrels_scorer_checks_protected_hash_even_when_reviewer_split_says_development(
    tmp_path: Path, monkeypatch
) -> None:
    row_a = _row(status="INDEPENDENT_A", reviewer_id="reviewer-a")
    row_b = _row(status="INDEPENDENT_B", reviewer_id="reviewer-b")
    row_a["dataset_sha256"] = row_b["dataset_sha256"] = "c" * 64

    def reject_registered_protected_hash(dataset_sha256: str, split: str) -> None:
        if dataset_sha256 == "c" * 64 and split == "development":
            raise SplitPolicyError("registered protected dataset cannot be downgraded")

    monkeypatch.setattr(
        qrels_scoring,
        "reject_protected_dataset_hash_downgrade",
        reject_registered_protected_hash,
    )

    with pytest.raises(ReviewScoringError, match="cannot be downgraded"):
        score_product_qrels_review(
            _write_queue(tmp_path / "a.csv", [row_a]),
            _write_queue(tmp_path / "b.csv", [row_b]),
            tmp_path / "agreement.json",
        )


def test_adjudication_queue_must_not_mix_frozen_metadata(tmp_path: Path) -> None:
    row_a_first = _row(status="INDEPENDENT_A", reviewer_id="reviewer-a")
    row_a_second = _row(
        status="INDEPENDENT_A", reviewer_id="reviewer-a", candidate_key="candidate-2"
    )
    row_b_first = _row(status="INDEPENDENT_B", reviewer_id="reviewer-b")
    row_b_second = _row(
        status="INDEPENDENT_B", reviewer_id="reviewer-b", candidate_key="candidate-2"
    )
    adjudication_first = _row(status="ADJUDICATED", reviewer_id="reviewer-c")
    adjudication_second = _row(
        status="ADJUDICATED", reviewer_id="reviewer-c", candidate_key="candidate-2"
    )
    adjudication_second["split"] = "blind_test"

    with pytest.raises(ReviewScoringError, match="mixes frozen dataset/catalog metadata"):
        score_product_qrels_review(
            _write_queue(tmp_path / "a.csv", [row_a_first, row_a_second]),
            _write_queue(tmp_path / "b.csv", [row_b_first, row_b_second]),
            tmp_path / "must-not-exist.json",
            _write_queue(
                tmp_path / "adjudication.csv", [adjudication_first, adjudication_second]
            ),
        )


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


def test_protected_qrels_scoring_requires_source_dataset_and_external_output(
    tmp_path: Path, monkeypatch
) -> None:
    dataset, manifest, reviewer_a, _row_a, _row_b, reviewer_b = _protected_fixture(tmp_path)
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        qrels_scoring,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    output = Path(__file__).resolve().parents[1] / "evaluation" / "review" / "blocked.json"

    with pytest.raises(ReviewScoringError, match="must stay outside the repository"):
        score_product_qrels_review(
            reviewer_a,
            reviewer_b,
            output,
            dataset_path=dataset,
            evaluator_manifest_path=manifest,
        )

    assert not output.exists()


def test_protected_qrels_rejects_symmetric_queue_tampering(tmp_path: Path, monkeypatch) -> None:
    dataset, manifest, reviewer_a, _row_a, _row_b, reviewer_b = _protected_fixture(tmp_path)
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        qrels_scoring,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    with reviewer_a.open(encoding="utf-8-sig", newline="") as handle:
        rows_a = list(csv.DictReader(handle))
    with reviewer_b.open(encoding="utf-8-sig", newline="") as handle:
        rows_b = list(csv.DictReader(handle))
    rows_a[0]["product_name"] = "Một món khác"
    rows_b[0]["product_name"] = "Một món khác"
    _write_queue(reviewer_a, rows_a)
    _write_queue(reviewer_b, rows_b)

    with pytest.raises(ReviewScoringError, match="frozen candidate content"):
        score_product_qrels_review(
            reviewer_a,
            reviewer_b,
            tmp_path / "agreement.json",
            dataset_path=dataset,
            evaluator_manifest_path=manifest,
        )


def test_protected_qrels_rejects_matching_rows_removed_from_both_queues(
    tmp_path: Path, monkeypatch
) -> None:
    dataset, manifest, reviewer_a, _row_a, _row_b, reviewer_b = _protected_fixture(tmp_path)
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        qrels_scoring,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    with reviewer_a.open(encoding="utf-8-sig", newline="") as handle:
        retained_a = list(csv.DictReader(handle))[:1]
    with reviewer_b.open(encoding="utf-8-sig", newline="") as handle:
        retained_b = list(csv.DictReader(handle))[:1]
    _write_queue(reviewer_a, retained_a)
    _write_queue(reviewer_b, retained_b)

    with pytest.raises(ReviewScoringError, match="item set does not match"):
        score_product_qrels_review(
            reviewer_a,
            reviewer_b,
            tmp_path / "agreement.json",
            dataset_path=dataset,
            evaluator_manifest_path=manifest,
        )


def test_protected_qrels_rejects_manifest_missing_a_dataset_case(
    tmp_path: Path, monkeypatch
) -> None:
    dataset, manifest, reviewer_a, _row_a, _row_b, reviewer_b = _protected_fixture(tmp_path)
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        qrels_scoring,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["candidate_assignments"] = manifest_data["candidate_assignments"][:1]
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ReviewScoringError, match="do not cover the complete protected dataset"):
        score_product_qrels_review(
            reviewer_a,
            reviewer_b,
            tmp_path / "agreement.json",
            dataset_path=dataset,
            evaluator_manifest_path=manifest,
        )


def test_protected_qrels_export_manifest_round_trips_through_scorer(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "blind.json"
    dataset.write_text(
        json.dumps(
            {
                "dataset_id": "blind-v1",
                "cases": [{"case_id": "case-1", "query": "Mình cần sản phẩm phù hợp."}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        qrels_exporter,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps({"cases": [{"case_id": "case-1", "product_ids": []}]}),
        encoding="utf-8",
    )
    exported = export_review_queues(
        dataset,
        candidates,
        tmp_path / "protected-review",
        split="blind_test",
    )
    queue_a = Path(str(exported["annotator_a"]))
    queue_b = Path(str(exported["annotator_b"]))
    for path, reviewer, status in (
        (queue_a, "reviewer-a", "INDEPENDENT_A"),
        (queue_b, "reviewer-b", "INDEPENDENT_B"),
    ):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["reviewer_id"] = reviewer
            row["reviewer_status"] = status
            row["reviewed_at"] = "2026-10-29T10:00:00+07:00"
            row["relevance"] = "UNJUDGED"
            for field in (
                "category_fit",
                "identity_fit",
                "brand_fit",
                "price_fit",
                "negation_fit",
                "availability_fit",
                "use_case_fit",
                "audience_fit",
                "variant_fit",
                "quantity_unit_fit",
                "budget_fit",
            ):
                row[field] = "NOT_APPLICABLE"
            row["clarification_expected"] = "false"
            row["abstention_expected"] = "false"
            row["confidence_1_to_5"] = "3"
            row["evidence_fields"] = "none"
            row["rationale"] = "Chưa có cặp sản phẩm để đánh giá."
        _write_queue(path, rows)

    monkeypatch.setattr(
        qrels_scoring,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    report = score_product_qrels_review(
        queue_a,
        queue_b,
        tmp_path / "agreement.json",
        dataset_path=dataset,
        evaluator_manifest_path=Path(str(exported["evaluator_manifest"])),
    )

    assert report["summary"]["item_count"] == 1
    assert report["items"][0]["product_id"] == ""


def test_protected_qrels_export_rejects_candidate_runs_missing_dataset_cases(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = tmp_path / "blind.json"
    dataset.write_text(
        json.dumps(
            {
                "dataset_id": "blind-v1",
                "cases": [
                    {"case_id": "case-1", "query": "Mình cần đồ uống."},
                    {"case_id": "case-2", "query": "Mình cần đồ ăn."},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dataset_sha = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        qrels_exporter,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha},
    )
    candidates = tmp_path / "partial-candidates.json"
    candidates.write_text(
        json.dumps({"cases": [{"case_id": "case-1", "product_ids": []}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="omit 1 registered dataset cases"):
        export_review_queues(
            dataset,
            candidates,
            tmp_path / "partial-review",
            split="blind_test",
        )

    assert not (tmp_path / "partial-review_annotator_a.csv").exists()
