"""Score agreement for two independently completed Vietnamese answer reviews.

This reports reviewer agreement and label coverage only. It does not turn
unreviewed/missing labels into negative judgments or claim system quality.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.export_vietnamese_answer_quality_review import (
    ANNOTATION_FIELDS,
    CSV_FIELDS,
    FLAG_FIELDS,
    FROZEN_REVIEW_FIELDS,
    RATING_FIELDS,
)

RATING_CATEGORIES = (1, 2, 3, 4, 5)
FLAG_CATEGORIES = (False, True)
REVIEW_STATUSES = {
    "annotator_a": "INDEPENDENT_A",
    "annotator_b": "INDEPENDENT_B",
}
REVIEW_METADATA_FIELDS = (
    "reviewer_id",
    "reviewed_at",
    "confidence_1_5",
    "rationale",
    "annotation_status",
)
ANNOTATION_VALUE_FIELDS = (*RATING_FIELDS, *FLAG_FIELDS)
CONTENT_FIELDS = tuple(
    field for field in CSV_FIELDS if field not in ANNOTATION_FIELDS and field != "annotation_status"
)


class AnswerQualityScoringError(ValueError):
    """Raised when queues are incomplete, altered, or not independently reviewed."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnswerQualityScoringError(f"Cannot read file {path}: {exc}") from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnswerQualityScoringError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnswerQualityScoringError(f"{label} must be a JSON object")
    return value


def _read_manifest(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(path, "evaluator-only manifest")
    if (
        manifest.get("artifact_id") != "vietnamese_answer_quality_review_packet"
        or manifest.get("schema_version") != "1.0"
    ):
        raise AnswerQualityScoringError("Unsupported answer-quality review manifest schema")
    packet = manifest.get("frozen_review_packet")
    if not isinstance(packet, dict):
        raise AnswerQualityScoringError(
            "Manifest lacks frozen_review_packet; regenerate queues with the current exporter"
        )
    if packet.get("hashed_fields") != list(FROZEN_REVIEW_FIELDS):
        raise AnswerQualityScoringError("Manifest frozen review fields do not match this scorer")
    digest = packet.get("visible_content_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise AnswerQualityScoringError("Manifest has no valid frozen packet SHA-256")

    raw_assignments = manifest.get("assignments")
    if not isinstance(raw_assignments, list) or not raw_assignments:
        raise AnswerQualityScoringError("Manifest assignments must be a non-empty array")
    assignments: dict[str, dict[str, Any]] = {}
    for index, assignment in enumerate(raw_assignments, start=1):
        if not isinstance(assignment, dict):
            raise AnswerQualityScoringError(f"Manifest assignment {index} must be an object")
        item_id = str(assignment.get("review_item_id") or "").strip()
        conversation_id = str(assignment.get("conversation_id") or "").strip()
        try:
            turn_index = int(assignment.get("turn_index"))
        except (TypeError, ValueError) as exc:
            raise AnswerQualityScoringError(
                f"Manifest assignment {index} has invalid turn_index"
            ) from exc
        if not item_id or not conversation_id or turn_index < 1:
            raise AnswerQualityScoringError(f"Manifest assignment {index} is incomplete")
        if item_id in assignments:
            raise AnswerQualityScoringError(f"Manifest has duplicate review item {item_id}")
        assignments[item_id] = {
            "conversation_id": conversation_id,
            "turn_index": turn_index,
        }
    if packet.get("row_count") != len(assignments):
        raise AnswerQualityScoringError("Manifest row_count does not match its assignments")
    return manifest, assignments


def _parse_timestamp(value: str, *, row_number: int, role: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnswerQualityScoringError(
            f"{role} row {row_number}: reviewed_at must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnswerQualityScoringError(
            f"{role} row {row_number}: reviewed_at must include a timezone"
        )
    return parsed.isoformat()


def _parse_rating(value: str, *, field: str, row_number: int, role: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        rating = int(normalized)
    except ValueError as exc:
        raise AnswerQualityScoringError(
            f"{role} row {row_number}: {field} must be an integer from 1 to 5 or blank"
        ) from exc
    if not 1 <= rating <= 5:
        raise AnswerQualityScoringError(
            f"{role} row {row_number}: {field} must be from 1 to 5 or blank"
        )
    return rating


def _parse_flag(value: str, *, field: str, row_number: int, role: str) -> bool | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise AnswerQualityScoringError(
        f"{role} row {row_number}: {field} must be true, false, or blank"
    )


def _read_queue(path: Path, role: str) -> tuple[dict[str, dict[str, Any]], str]:
    expected_status = REVIEW_STATUSES[role]
    try:
        handle = path.open(encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise AnswerQualityScoringError(f"Cannot read {role} queue {path}: {exc}") from exc

    rows: dict[str, dict[str, Any]] = {}
    reviewer_ids: set[str] = set()
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AnswerQualityScoringError(f"{role} queue has no CSV header")
        if len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(
            CSV_FIELDS
        ):
            raise AnswerQualityScoringError(
                f"{role} queue header does not match the current answer-quality CSV schema"
            )

        for row_number, raw in enumerate(reader, start=2):
            if None in raw or any(value is None for value in raw.values()):
                raise AnswerQualityScoringError(f"{role} row {row_number} has malformed CSV cells")
            item_id = (raw.get("review_item_id") or "").strip()
            if not item_id:
                raise AnswerQualityScoringError(f"{role} row {row_number} lacks review_item_id")
            if item_id in rows:
                raise AnswerQualityScoringError(f"{role} queue duplicates review item {item_id}")
            status = (raw.get("annotation_status") or "").strip()
            if status != expected_status:
                raise AnswerQualityScoringError(
                    f"{role} row {row_number} status is {status!r}; expected {expected_status!r}"
                )

            reviewer_id = (raw.get("reviewer_id") or "").strip()
            rationale = (raw.get("rationale") or "").strip()
            if not reviewer_id:
                raise AnswerQualityScoringError(f"{role} row {row_number} lacks reviewer_id")
            if not rationale:
                raise AnswerQualityScoringError(f"{role} row {row_number} lacks rationale")
            reviewer_ids.add(reviewer_id)
            reviewed_at = _parse_timestamp(
                raw.get("reviewed_at") or "", row_number=row_number, role=role
            )
            try:
                confidence = int((raw.get("confidence_1_5") or "").strip())
            except ValueError as exc:
                raise AnswerQualityScoringError(
                    f"{role} row {row_number}: confidence_1_5 must be an integer from 1 to 5"
                ) from exc
            if not 1 <= confidence <= 5:
                raise AnswerQualityScoringError(
                    f"{role} row {row_number}: confidence_1_5 must be from 1 to 5"
                )

            judgments = {
                field: _parse_rating(
                    raw.get(field) or "", field=field, row_number=row_number, role=role
                )
                for field in RATING_FIELDS
            }
            judgments.update(
                {
                    field: _parse_flag(
                        raw.get(field) or "", field=field, row_number=row_number, role=role
                    )
                    for field in FLAG_FIELDS
                }
            )
            if not any(value is not None for value in judgments.values()):
                raise AnswerQualityScoringError(
                    f"{role} row {row_number} has no human judgments; blank/N/A cannot be scored"
                )

            try:
                turn_index = int((raw.get("turn_index") or "").strip())
            except ValueError as exc:
                raise AnswerQualityScoringError(
                    f"{role} row {row_number}: turn_index must be a positive integer"
                ) from exc
            if turn_index < 1 or not (raw.get("conversation_id") or "").strip():
                raise AnswerQualityScoringError(
                    f"{role} row {row_number}: conversation_id and positive turn_index are required"
                )

            rows[item_id] = {
                "content": {field: raw.get(field, "") for field in CONTENT_FIELDS},
                "conversation_id": (raw.get("conversation_id") or "").strip(),
                "turn_index": turn_index,
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "confidence_1_5": confidence,
                "rationale": rationale,
                "judgments": judgments,
            }

    if not rows:
        raise AnswerQualityScoringError(f"{role} queue is empty")
    if len(reviewer_ids) != 1:
        raise AnswerQualityScoringError(
            f"{role} queue must use one pseudonymous reviewer_id; found {len(reviewer_ids)}"
        )
    return rows, next(iter(reviewer_ids))


def _frozen_packet_sha256(rows: dict[str, dict[str, Any]]) -> str:
    packet: list[dict[str, Any]] = []
    for item_id in sorted(rows):
        frozen_row = {field: rows[item_id]["content"][field] for field in FROZEN_REVIEW_FIELDS}
        frozen_row["turn_index"] = int(frozen_row["turn_index"])
        packet.append(frozen_row)
    canonical = json.dumps(
        packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _coverage(values_a: list[Any], values_b: list[Any]) -> dict[str, int]:
    return {
        "items": len(values_a),
        "rated_by_a": sum(value is not None for value in values_a),
        "rated_by_b": sum(value is not None for value in values_b),
        "rated_by_both": sum(a is not None and b is not None for a, b in zip(values_a, values_b)),
        "rated_only_by_a": sum(a is not None and b is None for a, b in zip(values_a, values_b)),
        "rated_only_by_b": sum(a is None and b is not None for a, b in zip(values_a, values_b)),
        "blank_by_both": sum(a is None and b is None for a, b in zip(values_a, values_b)),
    }


def _confusion_matrix(
    pairs: list[tuple[Any, Any]], categories: tuple[Any, ...]
) -> dict[str, dict[str, int]]:
    matrix = {
        str(left).lower(): {str(right).lower(): 0 for right in categories} for left in categories
    }
    for left, right in pairs:
        matrix[str(left).lower()][str(right).lower()] += 1
    return matrix


def _quadratic_weighted_kappa(pairs: list[tuple[int, int]]) -> tuple[float | None, str | None]:
    if not pairs:
        return None, "no_items_rated_by_both_reviewers"
    n = len(pairs)
    left = Counter(a for a, _b in pairs)
    right = Counter(b for _a, b in pairs)
    observed_disagreement = sum(((a - b) / 4) ** 2 for a, b in pairs) / n
    expected_disagreement = sum(
        (left[a] * right[b] / (n * n)) * ((a - b) / 4) ** 2
        for a in RATING_CATEGORIES
        for b in RATING_CATEGORIES
    )
    if expected_disagreement <= 1e-15:
        return None, "expected_weighted_disagreement_is_zero"
    return 1 - observed_disagreement / expected_disagreement, None


def _rating_agreement(values_a: list[int | None], values_b: list[int | None]) -> dict[str, Any]:
    coverage = _coverage(values_a, values_b)
    pairs = [(a, b) for a, b in zip(values_a, values_b) if a is not None and b is not None]
    exact = sum(a == b for a, b in pairs)
    within_one = sum(abs(a - b) <= 1 for a, b in pairs)
    kappa, reason = _quadratic_weighted_kappa(pairs)
    counts_a = Counter(value for value in values_a if value is not None)
    counts_b = Counter(value for value in values_b if value is not None)
    return {
        "coverage": coverage,
        "exact_agreement_count": exact,
        "exact_agreement_rate": round(exact / len(pairs), 6) if pairs else None,
        "within_one_point_count": within_one,
        "within_one_point_rate": round(within_one / len(pairs), 6) if pairs else None,
        "quadratic_weighted_cohen_kappa": round(kappa, 6) if kappa is not None else None,
        "kappa_undefined_reason": reason,
        "rating_distribution_a": {str(value): counts_a[value] for value in RATING_CATEGORIES},
        "rating_distribution_b": {str(value): counts_b[value] for value in RATING_CATEGORIES},
        "mean_a": round(
            sum(value for value in values_a if value is not None)
            / sum(value is not None for value in values_a),
            6,
        )
        if any(value is not None for value in values_a)
        else None,
        "mean_b": round(
            sum(value for value in values_b if value is not None)
            / sum(value is not None for value in values_b),
            6,
        )
        if any(value is not None for value in values_b)
        else None,
        "confusion_matrix": _confusion_matrix(pairs, RATING_CATEGORIES),
    }


def _cohen_kappa_nominal(pairs: list[tuple[bool, bool]]) -> tuple[float | None, str | None]:
    if not pairs:
        return None, "no_items_flagged_by_both_reviewers"
    n = len(pairs)
    left = Counter(a for a, _b in pairs)
    right = Counter(b for _a, b in pairs)
    observed = sum(a == b for a, b in pairs) / n
    expected = sum(left[value] * right[value] for value in FLAG_CATEGORIES) / (n * n)
    if expected >= 1 - 1e-15:
        return None, "expected_chance_agreement_is_one"
    return (observed - expected) / (1 - expected), None


def _flag_agreement(values_a: list[bool | None], values_b: list[bool | None]) -> dict[str, Any]:
    coverage = _coverage(values_a, values_b)
    pairs = [(a, b) for a, b in zip(values_a, values_b) if a is not None and b is not None]
    agreeing = sum(a == b for a, b in pairs)
    both_positive = sum(a and b for a, b in pairs)
    positive_total = sum(a for a, _b in pairs) + sum(b for _a, b in pairs)
    both_negative = sum(not a and not b for a, b in pairs)
    negative_total = sum(not a for a, _b in pairs) + sum(not b for _a, b in pairs)
    kappa, reason = _cohen_kappa_nominal(pairs)
    return {
        "coverage": coverage,
        "exact_agreement_count": agreeing,
        "exact_agreement_rate": round(agreeing / len(pairs), 6) if pairs else None,
        "cohen_kappa_nominal": round(kappa, 6) if kappa is not None else None,
        "kappa_undefined_reason": reason,
        "positive_agreement_rate": round(2 * both_positive / positive_total, 6)
        if positive_total
        else None,
        "negative_agreement_rate": round(2 * both_negative / negative_total, 6)
        if negative_total
        else None,
        "positive_count_a": sum(value is True for value in values_a),
        "positive_count_b": sum(value is True for value in values_b),
        "confusion_matrix": _confusion_matrix(pairs, FLAG_CATEGORIES),
    }


def score_vietnamese_answer_quality_review(
    annotator_a_path: Path,
    annotator_b_path: Path,
    manifest_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Validate completed independent queues and write a conservative agreement report."""
    manifest, assignments = _read_manifest(manifest_path)
    rows_a, reviewer_a_id = _read_queue(annotator_a_path, "annotator_a")
    rows_b, reviewer_b_id = _read_queue(annotator_b_path, "annotator_b")
    if reviewer_a_id == reviewer_b_id:
        raise AnswerQualityScoringError("Independent reviewers must use distinct pseudonymous IDs")
    ids_a, ids_b = set(rows_a), set(rows_b)
    if ids_a != ids_b:
        raise AnswerQualityScoringError(
            f"Reviewer item sets differ (missing from B: {sorted(ids_a - ids_b)[:5]}; "
            f"missing from A: {sorted(ids_b - ids_a)[:5]})"
        )
    if ids_a != set(assignments):
        raise AnswerQualityScoringError(
            "Reviewer item set does not match evaluator manifest assignments"
        )

    for item_id in sorted(ids_a):
        row_a, row_b = rows_a[item_id], rows_b[item_id]
        assignment = assignments[item_id]
        if (
            row_a["conversation_id"] != assignment["conversation_id"]
            or row_a["turn_index"] != assignment["turn_index"]
        ):
            raise AnswerQualityScoringError(f"Item {item_id} does not match manifest assignment")
        if row_a["content"] != row_b["content"]:
            raise AnswerQualityScoringError(
                f"Reviewer packets differ in frozen customer-visible content for {item_id}"
            )

    packet_hash_a = _frozen_packet_sha256(rows_a)
    packet_hash_b = _frozen_packet_sha256(rows_b)
    expected_packet_hash = manifest["frozen_review_packet"]["visible_content_sha256"]
    if packet_hash_a != expected_packet_hash or packet_hash_b != expected_packet_hash:
        raise AnswerQualityScoringError(
            "Frozen customer-visible packet hash mismatch; rows were altered or packet is stale"
        )

    item_agreement: list[dict[str, Any]] = []
    for item_id in sorted(ids_a):
        judgments_a = rows_a[item_id]["judgments"]
        judgments_b = rows_b[item_id]["judgments"]
        disagreements = [
            field
            for field in ANNOTATION_VALUE_FIELDS
            if judgments_a[field] is not None
            and judgments_b[field] is not None
            and judgments_a[field] != judgments_b[field]
        ]
        missing_a = [field for field in ANNOTATION_VALUE_FIELDS if judgments_a[field] is None]
        missing_b = [field for field in ANNOTATION_VALUE_FIELDS if judgments_b[field] is None]
        item_agreement.append(
            {
                "review_item_id": item_id,
                "disagreement_fields": disagreements,
                "missing_fields_a": missing_a,
                "missing_fields_b": missing_b,
            }
        )

    agreement: dict[str, Any] = {
        field: _rating_agreement(
            [rows_a[item_id]["judgments"][field] for item_id in sorted(ids_a)],
            [rows_b[item_id]["judgments"][field] for item_id in sorted(ids_a)],
        )
        for field in RATING_FIELDS
    }
    agreement.update(
        {
            field: _flag_agreement(
                [rows_a[item_id]["judgments"][field] for item_id in sorted(ids_a)],
                [rows_b[item_id]["judgments"][field] for item_id in sorted(ids_a)],
            )
            for field in FLAG_FIELDS
        }
    )

    fully_paired_disagreements = sum(bool(item["disagreement_fields"]) for item in item_agreement)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "vietnamese_answer_quality_agreement_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "input_files": {
            "annotator_a_sha256": _sha256(annotator_a_path),
            "annotator_b_sha256": _sha256(annotator_b_path),
            "evaluator_manifest_sha256": _sha256(manifest_path),
        },
        "review_packet": {
            "item_count": len(ids_a),
            "visible_content_sha256": expected_packet_hash,
            "source_artifact_count": len(manifest.get("source_artifacts", []))
            if isinstance(manifest.get("source_artifacts"), list)
            else None,
            "source_splits": sorted(
                {
                    str(source.get("split"))
                    for source in manifest.get("source_artifacts", [])
                    if isinstance(source, dict) and source.get("split") is not None
                }
            ),
        },
        "reviewers": {"annotator_a": reviewer_a_id, "annotator_b": reviewer_b_id},
        "summary": {
            "items_with_any_paired_disagreement": fully_paired_disagreements,
            "ratings_and_flags_are_reported_per_field": True,
            "single_composite_answer_quality_score": None,
            "adjudication_performed": False,
            "disagreements_resolved": False,
            "eligible_for_validation_or_blind_claim": False,
            "interpretation": (
                "This report measures independent reviewer agreement and label coverage. "
                "It does not establish answer correctness, customer acceptance, "
                "or release readiness. "
                "Missing/N/A labels are excluded per field; disagreements remain unresolved."
            ),
        },
        "agreement": agreement,
        "items": item_agreement,
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotator-a", type=Path, required=True)
    parser.add_argument("--annotator-b", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score_vietnamese_answer_quality_review(
        args.annotator_a, args.annotator_b, args.manifest, args.output
    )
    summary = report["summary"]
    print(
        json.dumps(
            {
                "review_item_count": report["review_packet"]["item_count"],
                "items_with_any_paired_disagreement": summary["items_with_any_paired_disagreement"],
                "eligible_for_validation_or_blind_claim": summary[
                    "eligible_for_validation_or_blind_claim"
                ],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
