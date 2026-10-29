from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation import score_vietnamese_answer_quality_review as scoring_module
from evaluation.export_vietnamese_answer_quality_review import (
    ANNOTATION_FIELDS,
    CSV_FIELDS,
    FLAG_FIELDS,
    FROZEN_REVIEW_FIELDS,
    RATING_FIELDS,
    _frozen_review_packet_sha256,
    _opaque_id,
    _prompt_records_sha256,
    _turn_records,
)
from evaluation.score_vietnamese_answer_quality_review import (
    AnswerQualityScoringError,
    score_vietnamese_answer_quality_review,
)


def _read_queue_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _fixture_rows(
    *, based_on: tuple[Path, Path] | None = None
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows_a: list[dict[str, str]] = []
    rows_b: list[dict[str, str]] = []
    need_a = [1, 2, 3, 4, 5]
    need_b = [1, 3, 3, 4, 4]
    critical_a = ["true", "true", "false", "false", ""]
    critical_b = ["true", "false", "false", "true", ""]
    for index in range(5):
        base = {
            "schema_version": "1.0",
            "review_item_id": f"review-{index}",
            "conversation_id": "conversation-1",
            "turn_index": str(index + 1),
            "dialogue_context": "",
            "user_message": f"Câu hỏi {index + 1}?",
            "assistant_response": f"Câu trả lời {index + 1}.",
            "visible_recommendations": "",
        }
        annotations_a = {field: str(need_a[index]) for field in RATING_FIELDS} | {
            field: "false" for field in FLAG_FIELDS
        }
        annotations_b = {field: str(need_b[index]) for field in RATING_FIELDS} | {
            field: "false" for field in FLAG_FIELDS
        }
        annotations_a["critical_wrong_product_type"] = critical_a[index]
        annotations_b["critical_wrong_product_type"] = critical_b[index]
        annotations_a.update(
            {
                "reviewer_id": "reviewer-a",
                "reviewed_at": "2026-10-29T10:00:00+07:00",
                "confidence_1_5": "4",
                "rationale": "Có bằng chứng trong câu trả lời.",
                "annotation_status": "INDEPENDENT_A",
            }
        )
        annotations_b.update(
            {
                "reviewer_id": "reviewer-b",
                "reviewed_at": "2026-10-29T11:00:00+07:00",
                "confidence_1_5": "3",
                "rationale": "Đánh giá độc lập dựa trên câu trả lời.",
                "annotation_status": "INDEPENDENT_B",
            }
        )
        rows_a.append({**base, **annotations_a})
        rows_b.append({**base, **annotations_b})
    if based_on is not None:
        stored_a = _read_queue_rows(based_on[0])
        stored_b = _read_queue_rows(based_on[1])
        identity_fields = (
            "review_item_id",
            "conversation_id",
            "turn_index",
            "dialogue_context",
            "user_message",
            "assistant_response",
            "visible_recommendations",
        )
        for rows, stored_rows in ((rows_a, stored_a), (rows_b, stored_b)):
            for row, stored in zip(rows, stored_rows, strict=True):
                for field in identity_fields:
                    row[field] = stored[field]
    return rows_a, rows_b


def _write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(
    path: Path,
    rows: list[dict[str, str]],
    *,
    source_path: Path,
    source_payload: dict[str, object],
    source_label: str,
    seed: int,
) -> None:
    artifact_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    turns = _turn_records(source_payload)
    source_label = str(source_label)
    frozen_rows = [
        {field: row[field] for field in FROZEN_REVIEW_FIELDS}
        | {"turn_index": int(row["turn_index"])}
        for row in rows
    ]
    packet_hash = _frozen_review_packet_sha256(frozen_rows)
    payload = {
        "artifact_id": "vietnamese_answer_quality_review_packet",
        "schema_version": "1.0",
        "seed": seed,
        "split": "development",
        "source_artifacts": [
            {
                "source_label": source_label,
                "mode": None,
                "artifact_name": source_path.name,
                "artifact_path": str(source_path.resolve()),
                "artifact_sha256": artifact_sha256,
                "artifact_id": source_payload.get("kind"),
                "dataset_id": source_payload.get("dataset_id"),
                "dataset_sha256": source_payload.get("dataset_sha256"),
                "dataset_authoring_method": None,
                "split": "development",
                "evaluation_dataset": None,
                "split_authorization": None,
                "prompt_set_sha256": _prompt_records_sha256(turns),
                "catalog_sha256": None,
                "provider": None,
                "model_primary": None,
                "commit_sha": None,
                "generated_at_utc": None,
                "turn_count": len(turns),
            }
        ],
        "assignments": [
            {
                "review_item_id": row["review_item_id"],
                "conversation_id": row["conversation_id"],
                "turn_index": int(row["turn_index"]),
                "source_label": source_label,
                "artifact_sha256": artifact_sha256,
                "original_case_id": "fixture-case",
            }
            for row in rows
        ],
        "frozen_review_packet": {
            "row_count": len(rows),
            "visible_content_sha256": packet_hash,
            "hashed_fields": list(FROZEN_REVIEW_FIELDS),
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    rows_a, rows_b = _fixture_rows()
    source_label = "fixture-source"
    seed = 29
    source_payload: dict[str, object] = {
        "kind": "answer_quality_test_fixture",
        "dataset_id": "answer_quality_fixture_v1",
        "dataset_sha256": "a" * 64,
        "split": "development",
        "results": [
            {
                "case_id": "fixture-case",
                "turn": int(row["turn_index"]),
                "prompt": row["user_message"],
                "response": row["assistant_response"],
            }
            for row in rows_a
        ],
    }
    source_path = tmp_path / "result_artifact.json"
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")
    artifact_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    conversation_id = _opaque_id("conv", seed, artifact_sha256, source_label, "fixture-case")
    for rows in (rows_a, rows_b):
        occurrence_by_turn: dict[int, int] = {}
        for row in rows:
            turn_index = int(row["turn_index"])
            occurrence_by_turn[turn_index] = occurrence_by_turn.get(turn_index, 0) + 1
            row["conversation_id"] = conversation_id
            row["review_item_id"] = _opaque_id(
                "review",
                seed,
                artifact_sha256,
                source_label,
                "fixture-case",
                str(turn_index),
                str(occurrence_by_turn[turn_index]),
            )
    path_a = tmp_path / "annotator_a.csv"
    path_b = tmp_path / "annotator_b.csv"
    manifest_path = tmp_path / "manifest.json"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    _write_manifest(
        manifest_path,
        rows_a,
        source_path=source_path,
        source_payload=source_payload,
        source_label=source_label,
        seed=seed,
    )
    return path_a, path_b, manifest_path


def _adjudication_rows(*, based_on: tuple[Path, Path] | None = None) -> list[dict[str, str]]:
    rows_a, rows_b = _fixture_rows(based_on=based_on)
    rows: list[dict[str, str]] = []
    for row_a, row_b in zip(rows_a, rows_b, strict=True):
        disputed = [
            field for field in (*RATING_FIELDS, *FLAG_FIELDS) if row_a[field] != row_b[field]
        ]
        if not disputed:
            continue
        adjudicated = dict(row_a)
        for field in ANNOTATION_FIELDS:
            adjudicated[field] = ""
        for field in disputed:
            adjudicated[field] = row_a[field]
        adjudicated.update(
            {
                "reviewer_id": "reviewer-c",
                "reviewed_at": "2026-10-29T12:00:00+07:00",
                "confidence_1_5": "5",
                "rationale": "Phân xử độc lập theo bằng chứng hiển thị.",
                "adjudication_decision": "KEEP_A",
                "annotation_status": "ADJUDICATED",
            }
        )
        rows.append(adjudicated)
    return rows


def test_reports_per_dimension_ordinal_agreement_and_flag_agreement(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    output = tmp_path / "agreement.json"

    report = score_vietnamese_answer_quality_review(path_a, path_b, manifest_path, output)

    need = report["agreement"]["user_need_understanding_1_5"]
    assert need["coverage"]["rated_by_both"] == 5
    assert need["exact_agreement_count"] == 3
    assert need["within_one_point_count"] == 5
    assert need["quadratic_weighted_cohen_kappa"] == pytest.approx(0.875)
    assert need["mean_a"] == 3
    assert need["mean_b"] == 3

    flag = report["agreement"]["critical_wrong_product_type"]
    assert flag["coverage"]["rated_by_both"] == 4
    assert flag["positive_agreement_rate"] == pytest.approx(0.5)
    assert flag["negative_agreement_rate"] == pytest.approx(0.5)
    assert report["summary"]["eligible_for_validation_or_blind_claim"] is False
    assert report["summary"]["single_composite_answer_quality_score"] is None
    assert report["summary"]["paired_disagreement_field_count"] == 14
    assert report["summary"]["unresolved_disagreement_field_count"] == 14
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == (
        "vietnamese_answer_quality_agreement_v1"
    )


def test_distinct_adjudicator_resolves_only_independent_disagreements(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    adjudication_path = tmp_path / "adjudication.csv"
    _write_queue(adjudication_path, _adjudication_rows(based_on=(path_a, path_b)))

    report = score_vietnamese_answer_quality_review(
        path_a,
        path_b,
        manifest_path,
        tmp_path / "agreement.json",
        adjudication_path,
    )

    assert report["reviewers"]["adjudicator"] == "reviewer-c"
    assert report["summary"]["adjudication_performed"] is True
    assert report["summary"]["adjudicated_disagreement_field_count"] == 14
    assert report["summary"]["unresolved_disagreement_field_count"] == 0
    assert report["summary"]["adjudication_coverage_of_paired_disagreements"] == 1
    assert (
        report["resolved_assessments"]["user_need_understanding_1_5"]["resolved_rating_mean"] == 3
    )
    assert (
        report["resolved_assessments"]["critical_wrong_product_type"]["resolved_true_rate"] == 0.5
    )
    assert all(
        item["adjudication_status"] == "ADJUDICATED"
        for item in report["items"]
        if item["disagreement_fields"]
    )


def test_adjudication_requires_a_distinct_reviewer_and_exact_dispute_fields(
    tmp_path: Path,
) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows = _adjudication_rows(based_on=(path_a, path_b))
    adjudication_path = tmp_path / "adjudication.csv"

    for row in rows:
        row["reviewer_id"] = "reviewer-a"
    _write_queue(adjudication_path, rows)
    with pytest.raises(AnswerQualityScoringError, match="distinct pseudonymous ID"):
        score_vietnamese_answer_quality_review(
            path_a,
            path_b,
            manifest_path,
            tmp_path / "agreement.json",
            adjudication_path,
        )


def test_keep_b_adjudication_must_match_reviewer_b_labels(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows = _adjudication_rows(based_on=(path_a, path_b))
    by_item_b = {row["review_item_id"]: row for row in rows_b}
    for row in rows:
        row["adjudication_decision"] = "KEEP_B"
        source_b = by_item_b[row["review_item_id"]]
        for field in (*RATING_FIELDS, *FLAG_FIELDS):
            if row[field]:
                row[field] = source_b[field]
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    adjudication_path = tmp_path / "adjudication.csv"
    _write_queue(adjudication_path, rows)

    report = score_vietnamese_answer_quality_review(
        path_a,
        path_b,
        manifest_path,
        tmp_path / "agreement.json",
        adjudication_path,
    )
    assert report["summary"]["adjudication_decisions"] == {"KEEP_B": 3}

    rows[0]["user_need_understanding_1_5"] = "1"
    _write_queue(adjudication_path, rows)
    with pytest.raises(AnswerQualityScoringError, match="does not preserve"):
        score_vietnamese_answer_quality_review(
            path_a,
            path_b,
            manifest_path,
            tmp_path / "agreement.json",
            adjudication_path,
        )


def test_new_judgment_records_a_third_reviewers_resolved_label(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows = _adjudication_rows(based_on=(path_a, path_b))
    rows[0]["adjudication_decision"] = "NEW_JUDGMENT"
    rows[0]["user_need_understanding_1_5"] = "5"
    adjudication_path = tmp_path / "adjudication.csv"
    _write_queue(adjudication_path, rows)

    report = score_vietnamese_answer_quality_review(
        path_a,
        path_b,
        manifest_path,
        tmp_path / "agreement.json",
        adjudication_path,
    )

    resolved = report["resolved_assessments"]["user_need_understanding_1_5"]
    assert report["summary"]["adjudication_decisions"]["NEW_JUDGMENT"] == 1
    assert resolved["resolved_value_counts"]["5"] == 2

    rows = _adjudication_rows(based_on=(path_a, path_b))
    rows[0]["user_need_understanding_1_5"] = ""
    _write_queue(adjudication_path, rows)
    with pytest.raises(AnswerQualityScoringError, match="must resolve exactly"):
        score_vietnamese_answer_quality_review(
            path_a,
            path_b,
            manifest_path,
            tmp_path / "agreement.json",
            adjudication_path,
        )

    rows = _adjudication_rows(based_on=(path_a, path_b))
    rows[0]["critical_other"] = "false"
    _write_queue(adjudication_path, rows)
    with pytest.raises(AnswerQualityScoringError, match="must resolve exactly"):
        score_vietnamese_answer_quality_review(
            path_a,
            path_b,
            manifest_path,
            tmp_path / "agreement.json",
            adjudication_path,
        )


def test_missing_and_not_applicable_ratings_are_excluded_and_counted(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_a[0]["naturalness_1_5"] = ""
    rows_b[1]["naturalness_1_5"] = ""
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)

    report = score_vietnamese_answer_quality_review(
        path_a, path_b, manifest_path, tmp_path / "agreement.json"
    )

    coverage = report["agreement"]["naturalness_1_5"]["coverage"]
    assert coverage == {
        "items": 5,
        "rated_by_a": 4,
        "rated_by_b": 4,
        "rated_by_both": 3,
        "rated_only_by_a": 1,
        "rated_only_by_b": 1,
        "blank_by_both": 0,
    }


def test_unreviewed_or_entirely_blank_queue_is_rejected(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, _ = _fixture_rows(based_on=(path_a, path_b))
    rows_a[0]["annotation_status"] = "UNREVIEWED"
    _write_queue(path_a, rows_a)
    with pytest.raises(AnswerQualityScoringError, match="expected 'INDEPENDENT_A'"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    for row in rows_a:
        for field in (*RATING_FIELDS, *FLAG_FIELDS):
            row[field] = ""
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="no human judgments"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )


def test_visible_content_hash_detects_identical_packet_tampering(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_a[0]["assistant_response"] = "Đã sửa nội dung sau khi xuất hàng chờ."
    rows_b[0]["assistant_response"] = rows_a[0]["assistant_response"]
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)

    with pytest.raises(AnswerQualityScoringError, match="packet hash mismatch"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )


def test_reviewer_queues_must_match_manifest_and_each_other(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_b.pop()
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="item sets differ"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_b[0]["user_message"] = "Nội dung lệch giữa hai người chấm?"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="differ in frozen"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )


def test_manifest_must_have_new_frozen_packet_provenance(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    del payload["frozen_review_packet"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AnswerQualityScoringError, match="regenerate queues"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )


def test_invalid_ratings_flags_reviewers_and_timestamps_fail_closed(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_a[0]["product_choice_1_5"] = "6"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="from 1 to 5"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_a[0]["critical_other"] = "maybe"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="true, false, or blank"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_a[0]["critical_other"] = "yes"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="true, false, or blank"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows(based_on=(path_a, path_b))
    rows_b[0]["reviewer_id"] = "reviewer-a"
    for row in rows_b:
        row["reviewer_id"] = "reviewer-a"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="distinct pseudonymous IDs"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )


def test_cli_writes_machine_readable_agreement_report(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    output = tmp_path / "cli-agreement.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.score_vietnamese_answer_quality_review",
            "--annotator-a",
            str(path_a),
            "--annotator-b",
            str(path_b),
            "--manifest",
            str(manifest_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["review_packet"]["item_count"] == 5


def test_answer_quality_scorer_rejects_unverified_blind_manifest(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["split"] = "blind_test"
    payload["source_artifacts"] = [{"split": "blind_test"}]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "should-not-write.json"

    with pytest.raises(AnswerQualityScoringError, match="lacks authorized dataset provenance"):
        score_vietnamese_answer_quality_review(path_a, path_b, manifest_path, output)

    assert not output.exists()


def test_answer_quality_scorer_rejects_blind_manifest_without_sources(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["split"] = "blind_test"
    payload["source_artifacts"] = []
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "should-not-write.json"

    with pytest.raises(
        AnswerQualityScoringError,
        match="must contain at least one authorized source",
    ):
        score_vietnamese_answer_quality_review(path_a, path_b, manifest_path, output)

    assert not output.exists()


def test_answer_quality_scorer_rejects_protected_hash_relabelled_as_development(
    tmp_path: Path, monkeypatch
) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_artifacts"][0]["dataset_sha256"] = "c" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def reject_registered_protected_hash(dataset_sha256: str, split: str) -> None:
        if dataset_sha256 == "c" * 64 and split == "development":
            from evaluation.split_policy import SplitPolicyError

            raise SplitPolicyError("registered protected dataset cannot be downgraded")

    monkeypatch.setattr(
        scoring_module,
        "reject_protected_dataset_hash_downgrade",
        reject_registered_protected_hash,
    )

    with pytest.raises(AnswerQualityScoringError, match="cannot be downgraded"):
        score_vietnamese_answer_quality_review(
            path_a,
            path_b,
            manifest_path,
            tmp_path / "blocked.json",
        )
