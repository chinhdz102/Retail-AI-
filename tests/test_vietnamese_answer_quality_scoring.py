from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.export_vietnamese_answer_quality_review import (
    CSV_FIELDS,
    FLAG_FIELDS,
    FROZEN_REVIEW_FIELDS,
    RATING_FIELDS,
    _frozen_review_packet_sha256,
)
from evaluation.score_vietnamese_answer_quality_review import (
    AnswerQualityScoringError,
    score_vietnamese_answer_quality_review,
)


def _fixture_rows() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
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
    return rows_a, rows_b


def _write_queue(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    frozen_rows = [
        {field: row[field] for field in FROZEN_REVIEW_FIELDS}
        | {"turn_index": int(row["turn_index"])}
        for row in rows
    ]
    packet_hash = _frozen_review_packet_sha256(frozen_rows)
    payload = {
        "artifact_id": "vietnamese_answer_quality_review_packet",
        "schema_version": "1.0",
        "source_artifacts": [{"split": "development"}],
        "assignments": [
            {
                "review_item_id": row["review_item_id"],
                "conversation_id": row["conversation_id"],
                "turn_index": int(row["turn_index"]),
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
    path_a = tmp_path / "annotator_a.csv"
    path_b = tmp_path / "annotator_b.csv"
    manifest_path = tmp_path / "manifest.json"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    _write_manifest(manifest_path, rows_a)
    return path_a, path_b, manifest_path


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
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == (
        "vietnamese_answer_quality_agreement_v1"
    )


def test_missing_and_not_applicable_ratings_are_excluded_and_counted(tmp_path: Path) -> None:
    path_a, path_b, manifest_path = _write_fixture(tmp_path)
    rows_a, rows_b = _fixture_rows()
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
    rows_a, _ = _fixture_rows()
    rows_a[0]["annotation_status"] = "UNREVIEWED"
    _write_queue(path_a, rows_a)
    with pytest.raises(AnswerQualityScoringError, match="expected 'INDEPENDENT_A'"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows()
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
    rows_a, rows_b = _fixture_rows()
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
    rows_a, rows_b = _fixture_rows()
    rows_b.pop()
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="item sets differ"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows()
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
    rows_a, rows_b = _fixture_rows()
    rows_a[0]["product_choice_1_5"] = "6"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="from 1 to 5"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows()
    rows_a[0]["critical_other"] = "maybe"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="true, false, or blank"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows()
    rows_a[0]["critical_other"] = "yes"
    _write_queue(path_a, rows_a)
    _write_queue(path_b, rows_b)
    with pytest.raises(AnswerQualityScoringError, match="true, false, or blank"):
        score_vietnamese_answer_quality_review(
            path_a, path_b, manifest_path, tmp_path / "agreement.json"
        )

    rows_a, rows_b = _fixture_rows()
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
