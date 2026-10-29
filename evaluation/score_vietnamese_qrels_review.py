"""Validate two blinded product-qrels queues and report agreement without inventing gold labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.export_vietnamese_qrels_review import (
    REVIEW_FIELDS,
    REVIEW_METADATA_FIELDS,
)

RELEVANCE_LABELS = ("EXACT", "SUBSTITUTE", "COMPLEMENT", "IRRELEVANT", "UNJUDGED")
FIT_FIELDS = (
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
)
FIT_LABELS = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")
BOOLEAN_FIELDS = ("clarification_expected", "abstention_expected")
AGREEMENT_FIELDS = ("relevance", *FIT_FIELDS, *BOOLEAN_FIELDS)
IDENTITY_FIELDS = ("query_id", "candidate_key")
SHARED_METADATA_FIELDS = (
    "query_text",
    "product_id",
    "product_name",
    "product_brand",
    "product_category",
    "product_price_vnd",
    "evidence_json",
    "dataset_id",
    "dataset_sha256",
    "catalog_sha256",
    "split",
)
QUEUE_METADATA_FIELDS = (
    "dataset_id",
    "dataset_sha256",
    "catalog_sha256",
    "split",
)
REQUIRED_FIELDS = (
    *IDENTITY_FIELDS,
    *SHARED_METADATA_FIELDS,
    "reviewer_status",
    *REVIEW_METADATA_FIELDS,
    *REVIEW_FIELDS,
)
REVIEW_STATUSES = {
    "annotator_a": "INDEPENDENT_A",
    "annotator_b": "INDEPENDENT_B",
    "adjudication": "ADJUDICATED",
}


class ReviewScoringError(ValueError):
    """Raised when reviewer files are incomplete, mismatched, or internally inconsistent."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_boolean(value: str, field: str, row_number: int) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ReviewScoringError(
        f"Row {row_number}: {field} must be true or false; got {value!r}"
    )


def _parse_reviewed_at(value: str, row_number: int) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewScoringError(
            f"Row {row_number}: reviewed_at must be an ISO-8601 timestamp with timezone"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewScoringError(
            f"Row {row_number}: reviewed_at must include a timezone"
        )
    return parsed.isoformat()


def _read_queue(path: Path, role: str) -> dict[tuple[str, str], dict[str, Any]]:
    expected_status = REVIEW_STATUSES[role]
    try:
        handle = path.open(encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ReviewScoringError(f"Cannot read {role} queue {path}: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ReviewScoringError(f"{role} queue is missing a CSV header: {path}")
        missing_columns = sorted(set(REQUIRED_FIELDS) - set(reader.fieldnames))
        if missing_columns:
            raise ReviewScoringError(
                f"{role} queue lacks required annotation columns: {', '.join(missing_columns)}"
            )

        rows: dict[tuple[str, str], dict[str, Any]] = {}
        reviewer_ids: set[str] = set()
        queue_metadata: tuple[str, ...] | None = None
        for row_number, raw in enumerate(reader, start=2):
            key = tuple((raw.get(field) or "").strip() for field in IDENTITY_FIELDS)
            if not all(key):
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: query_id and candidate_key are required"
                )
            if key in rows:
                raise ReviewScoringError(
                    f"{role} queue contains duplicate item {key[0]}/{key[1]}"
                )

            status = (raw.get("reviewer_status") or "").strip()
            if status != expected_status:
                raise ReviewScoringError(
                    f"{role} queue row {row_number} has reviewer_status={status!r}; "
                    f"expected {expected_status!r}"
                )
            reviewer_id = (raw.get("reviewer_id") or "").strip()
            if not reviewer_id:
                raise ReviewScoringError(f"{role} queue row {row_number}: reviewer_id is required")
            reviewer_ids.add(reviewer_id)
            reviewed_at = _parse_reviewed_at(raw.get("reviewed_at") or "", row_number)

            judgments: dict[str, Any] = {}
            relevance = (raw.get("relevance") or "").strip().upper()
            if relevance not in RELEVANCE_LABELS:
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: relevance must be one of "
                    f"{', '.join(RELEVANCE_LABELS)}"
                )
            judgments["relevance"] = relevance

            for field in FIT_FIELDS:
                value = (raw.get(field) or "").strip().upper()
                if value not in FIT_LABELS:
                    raise ReviewScoringError(
                        f"{role} queue row {row_number}: {field} must be one of "
                        f"{', '.join(FIT_LABELS)}"
                    )
                judgments[field] = value

            for field in BOOLEAN_FIELDS:
                judgments[field] = _parse_boolean(raw.get(field) or "", field, row_number)

            confidence_text = (raw.get("confidence_1_to_5") or "").strip()
            try:
                confidence = int(confidence_text)
            except ValueError as exc:
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: confidence_1_to_5 must be an integer 1–5"
                ) from exc
            if not 1 <= confidence <= 5:
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: confidence_1_to_5 must be between 1 and 5"
                )

            evidence_fields = (raw.get("evidence_fields") or "").strip()
            rationale = (raw.get("rationale") or "").strip()
            if not evidence_fields:
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: evidence_fields is required; use "
                    "'none' and explain missing evidence when the pair is UNJUDGED"
                )
            if not rationale:
                raise ReviewScoringError(f"{role} queue row {row_number}: rationale is required")
            judgments["confidence_1_to_5"] = confidence
            judgments["evidence_fields"] = evidence_fields
            judgments["rationale"] = rationale

            metadata = {field: (raw.get(field) or "").strip() for field in SHARED_METADATA_FIELDS}
            if not metadata["query_text"] or not metadata["dataset_id"]:
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: query_text and dataset_id are required"
                )
            current_queue_metadata = tuple(metadata[field] for field in QUEUE_METADATA_FIELDS)
            if queue_metadata is None:
                queue_metadata = current_queue_metadata
            elif current_queue_metadata != queue_metadata:
                differing_fields = [
                    field
                    for field, expected, actual in zip(
                        QUEUE_METADATA_FIELDS,
                        queue_metadata,
                        current_queue_metadata,
                        strict=True,
                    )
                    if expected != actual
                ]
                raise ReviewScoringError(
                    f"{role} queue mixes frozen dataset/catalog metadata at row {row_number}: "
                    f"{', '.join(differing_fields)} must match every other row"
                )
            for field in ("dataset_sha256", "catalog_sha256"):
                if not re.fullmatch(r"[a-fA-F0-9]{64}", metadata[field]):
                    raise ReviewScoringError(
                        f"{role} queue row {row_number}: {field} must be a 64-digit SHA-256"
                    )
            if metadata["split"] not in {"development", "validation", "blind_test"}:
                raise ReviewScoringError(
                    f"{role} queue row {row_number}: unsupported split {metadata['split']!r}"
                )

            rows[key] = {
                **metadata,
                **dict(zip(IDENTITY_FIELDS, key, strict=True)),
                "reviewer_status": status,
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "judgments": judgments,
            }

        if not rows:
            raise ReviewScoringError(f"{role} queue is empty")
        if len(reviewer_ids) != 1:
            raise ReviewScoringError(
                f"{role} queue must use one pseudonymous reviewer_id; found {len(reviewer_ids)}"
            )
        return rows


def _cohen_kappa(
    pairs: list[tuple[str, str]], categories: tuple[str, ...]
) -> tuple[float | None, float, float]:
    if not pairs:
        return None, 0.0, 0.0
    total = len(pairs)
    observed_count = sum(left == right for left, right in pairs)
    left_counts = Counter(left for left, _right in pairs)
    right_counts = Counter(right for _left, right in pairs)
    observed = observed_count / total
    expected = sum(left_counts[label] * right_counts[label] for label in categories) / total**2
    if expected >= 1.0:
        return None, observed, expected
    return (observed - expected) / (1.0 - expected), observed, expected


def _field_agreement(
    left_rows: dict[tuple[str, str], dict[str, Any]],
    right_rows: dict[tuple[str, str], dict[str, Any]],
    field: str,
) -> dict[str, Any]:
    pairs = [
        (left_rows[key]["judgments"][field], right_rows[key]["judgments"][field])
        for key in sorted(left_rows)
    ]
    categories = (
        RELEVANCE_LABELS
        if field == "relevance"
        else FIT_LABELS
        if field in FIT_FIELDS
        else ("false", "true")
    )
    string_pairs = [(str(left).lower(), str(right).lower()) for left, right in pairs]
    normalized_categories = tuple(label.lower() for label in categories)
    kappa, observed, expected = _cohen_kappa(string_pairs, normalized_categories)
    matrix = {
        label: {other: 0 for other in normalized_categories}
        for label in normalized_categories
    }
    for left, right in pairs:
        matrix[str(left).lower()][str(right).lower()] += 1
    return {
        "n": len(pairs),
        "exact_agreement": sum(left == right for left, right in pairs),
        "agreement_rate": round(observed, 6),
        "cohen_kappa_nominal": round(kappa, 6) if kappa is not None else None,
        "kappa_undefined_reason": (
            "expected_chance_agreement_is_1" if kappa is None and pairs else None
        ),
        "expected_chance_agreement": round(expected, 6),
        "confusion_matrix": matrix,
    }


def score_product_qrels_review(
    annotator_a_path: Path,
    annotator_b_path: Path,
    output_path: Path,
    adjudication_path: Path | None = None,
) -> dict[str, Any]:
    """Score blinded queues, retain both judgments, and resolve only agreement/adjudication."""
    reviewer_a = _read_queue(annotator_a_path, "annotator_a")
    reviewer_b = _read_queue(annotator_b_path, "annotator_b")
    keys_a, keys_b = set(reviewer_a), set(reviewer_b)
    if keys_a != keys_b:
        missing_b = sorted(keys_a - keys_b)[:5]
        missing_a = sorted(keys_b - keys_a)[:5]
        raise ReviewScoringError(
            f"Reviewer item sets differ (missing from B: {missing_b}; missing from A: {missing_a})"
        )

    reviewer_a_id = next(iter(reviewer_a.values()))["reviewer_id"]
    reviewer_b_id = next(iter(reviewer_b.values()))["reviewer_id"]
    if reviewer_a_id == reviewer_b_id:
        raise ReviewScoringError("Independent reviewers must use different pseudonymous IDs")
    for key in sorted(keys_a):
        for field in SHARED_METADATA_FIELDS:
            if reviewer_a[key][field] != reviewer_b[key][field]:
                raise ReviewScoringError(
                    f"Reviewer packets disagree on frozen {field} for {key[0]}/{key[1]}"
                )

    adjudication: dict[tuple[str, str], dict[str, Any]] = {}
    if adjudication_path is not None:
        adjudication = _read_queue(adjudication_path, "adjudication")
        if not set(adjudication) <= keys_a:
            raise ReviewScoringError("Adjudication file contains items outside the reviewer queues")
        adjudicator_id = next(iter(adjudication.values()))["reviewer_id"]
        if adjudicator_id in {reviewer_a_id, reviewer_b_id}:
            raise ReviewScoringError(
                "Adjudicator must use a distinct pseudonymous reviewer IDs from annotators"
            )
        for key, row in adjudication.items():
            if any(row[field] != reviewer_a[key][field] for field in SHARED_METADATA_FIELDS):
                raise ReviewScoringError(
                    f"Adjudication packet disagrees on frozen metadata for {key[0]}/{key[1]}"
                )

    scored_items = []
    disagreement_count = 0
    agreed_count = 0
    adjudicated_count = 0
    unresolved_count = 0
    for key in sorted(keys_a):
        row_a = reviewer_a[key]
        row_b = reviewer_b[key]
        disagreements = [
            field
            for field in AGREEMENT_FIELDS
            if row_a["judgments"][field] != row_b["judgments"][field]
        ]
        adjudicated_row = adjudication.get(key)
        if not disagreements and adjudicated_row is not None:
            raise ReviewScoringError(
                f"Adjudication is supplied for an item with no disagreement: {key[0]}/{key[1]}"
            )
        if disagreements:
            disagreement_count += 1
        if not disagreements:
            agreed_count += 1
            resolution_status = "AGREED"
            resolved = row_a["judgments"]
        elif adjudicated_row is not None:
            adjudicated_count += 1
            resolution_status = "ADJUDICATED"
            resolved = adjudicated_row["judgments"]
        else:
            unresolved_count += 1
            resolution_status = "UNRESOLVED"
            resolved = None

        scored_items.append(
            {
                **{field: row_a[field] for field in (*IDENTITY_FIELDS, *SHARED_METADATA_FIELDS)},
                "reviewer_a": {
                    "reviewer_id": row_a["reviewer_id"],
                    "reviewed_at": row_a["reviewed_at"],
                    "judgments": row_a["judgments"],
                },
                "reviewer_b": {
                    "reviewer_id": row_b["reviewer_id"],
                    "reviewed_at": row_b["reviewed_at"],
                    "judgments": row_b["judgments"],
                },
                "disagreement_fields": disagreements,
                "adjudication": (
                    {
                        "reviewer_id": adjudicated_row["reviewer_id"],
                        "reviewed_at": adjudicated_row["reviewed_at"],
                        "judgments": adjudicated_row["judgments"],
                    }
                    if adjudicated_row
                    else None
                ),
                "resolution_status": resolution_status,
                "resolved_judgments": resolved,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "vietnamese_product_qrels_agreement_v1",
        "input_files": {
            "annotator_a_sha256": _sha256(annotator_a_path),
            "annotator_b_sha256": _sha256(annotator_b_path),
            "adjudication_sha256": _sha256(adjudication_path)
            if adjudication_path is not None
            else None,
        },
        "dataset": {
            "dataset_id": next(iter(reviewer_a.values()))["dataset_id"],
            "dataset_sha256": next(iter(reviewer_a.values()))["dataset_sha256"],
            "catalog_sha256": next(iter(reviewer_a.values()))["catalog_sha256"],
            "split": next(iter(reviewer_a.values()))["split"],
        },
        "reviewers": {
            "annotator_a": reviewer_a_id,
            "annotator_b": reviewer_b_id,
            "adjudicator": (
                next(iter(adjudication.values()))["reviewer_id"] if adjudication else None
            ),
        },
        "summary": {
            "item_count": len(scored_items),
            "items_agreeing_on_all_scored_fields": agreed_count,
            "items_with_any_independent_disagreement": disagreement_count,
            "items_adjudicated": adjudicated_count,
            "items_unresolved": unresolved_count,
            "adjudication_coverage_of_disagreements": (
                round(adjudicated_count / disagreement_count, 6)
                if disagreement_count
                else None
            ),
            "resolved_label_rate": round(
                (agreed_count + adjudicated_count) / len(scored_items), 6
            ),
            "eligible_for_validation_or_blind_claim": False,
            "promotion_note": (
                "Scoring annotations does not establish independent authoring, blinded access, "
                "or split eligibility; the dataset split registry remains authoritative."
            ),
        },
        "agreement": {
            field: _field_agreement(reviewer_a, reviewer_b, field) for field in AGREEMENT_FIELDS
        },
        "items": scored_items,
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
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score_product_qrels_review(
        args.annotator_a,
        args.annotator_b,
        args.output,
        args.adjudication,
    )
    print(
        json.dumps(
            {"summary": report["summary"], "output": str(args.output)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
