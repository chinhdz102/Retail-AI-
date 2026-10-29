"""Create blinded human-review queues from API/conversation result artifacts.

Only user-visible turns are exported to annotators. Run provenance, model labels,
case IDs, grader expectations, ranks, and system traces stay in the evaluator-only
manifest and must not be distributed with the reviewer CSVs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.split_policy import (
    PROTECTED_SPLITS,
    SplitPolicyError,
    authorize_dataset_split,
    authorize_evaluation_artifact_path,
    reject_protected_dataset_hash_downgrade,
)

ROOT = Path(__file__).resolve().parents[1]
CSV_FIELDS = [
    "schema_version",
    "review_item_id",
    "conversation_id",
    "turn_index",
    "dialogue_context",
    "user_message",
    "assistant_response",
    "visible_recommendations",
    "user_need_understanding_1_5",
    "product_choice_1_5",
    "explanation_quality_1_5",
    "evidence_grounding_1_5",
    "naturalness_1_5",
    "helpfulness_1_5",
    "critical_wrong_product_type",
    "critical_hard_constraint_violation",
    "critical_price_or_unit_error",
    "critical_invented_use_or_benefit",
    "critical_false_stock_claim",
    "critical_unintended_purchase_action",
    "critical_session_data_leakage",
    "critical_other",
    "critical_other_note",
    "reviewer_id",
    "reviewed_at",
    "confidence_1_5",
    "rationale",
    "adjudication_decision",
    "annotation_status",
]
RATING_FIELDS = CSV_FIELDS[8:14]
FLAG_FIELDS = CSV_FIELDS[14:22]
ANNOTATION_FIELDS = [
    *RATING_FIELDS,
    *FLAG_FIELDS,
    "critical_other_note",
    "reviewer_id",
    "reviewed_at",
    "confidence_1_5",
    "rationale",
    "adjudication_decision",
    "annotation_status",
]
VISIBLE_ITEM_KEYS = (
    "name",
    "title",
    "brand",
    "category",
    "need_label",
    "label",
    "quantity",
    "unit_price_vnd",
    "price_vnd",
    "sale_price_vnd",
    "line_total_vnd",
    "total_vnd",
    "uom",
    "shelf",
    "promotion",
    "rationale",
)
USER_KEYS = ("user_message", "user_text", "prompt", "user", "query", "text")
ANSWER_KEYS = ("assistant_response", "assistant", "response", "answer", "output")
FROZEN_REVIEW_FIELDS = (
    "schema_version",
    "review_item_id",
    "conversation_id",
    "turn_index",
    "dialogue_context",
    "user_message",
    "assistant_response",
    "visible_recommendations",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frozen_review_packet_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    packet = [
        {field: row.get(field, "") for field in FROZEN_REVIEW_FIELDS}
        for row in sorted(rows, key=lambda item: str(item.get("review_item_id") or ""))
    ]
    canonical = json.dumps(
        packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("text", "content", "message", "answer"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        if isinstance(value.get("content"), list):
            fragments = [
                _text(item) for item in value["content"] if isinstance(item, (str, Mapping))
            ]
            return "\n".join(fragment for fragment in fragments if fragment)
    if isinstance(value, list):
        # Some API artifacts store chat messages in the prompt field. Ignore
        # system/developer content and retain only the latest user utterance.
        user_messages = [
            item.get("content") or item.get("text")
            for item in value
            if isinstance(item, Mapping) and item.get("role") == "user"
        ]
        if user_messages:
            return _text(user_messages[-1])
    return ""


def _visible_fields(item: Mapping[str, Any]) -> str:
    fields = [
        f"{key.replace('_', ' ')}: {item[key]}"
        for key in VISIBLE_ITEM_KEYS
        if item.get(key) is not None and item.get(key) != ""
    ]
    return " | ".join(fields)


def _visible_recommendations(response: Any) -> str:
    if not isinstance(response, Mapping):
        return ""
    lines: list[str] = []
    products = response.get("products") or response.get("product_cards") or []
    if isinstance(products, Sequence) and not isinstance(products, (str, bytes)):
        for product in products:
            if isinstance(product, Mapping):
                rendered = _visible_fields(product)
                if rendered:
                    lines.append(f"Sản phẩm: {rendered}")

    plan = response.get("shopping_plan")
    options = plan.get("options", []) if isinstance(plan, Mapping) else []
    if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        for option in options:
            if not isinstance(option, Mapping):
                continue
            label = _text(option.get("label"))
            total = option.get("total_vnd")
            heading = "Phương án"
            if label:
                heading += f": {label}"
            if total is not None:
                heading += f" | total vnd: {total}"
            lines.append(heading)
            items = option.get("items", [])
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                for item in items:
                    if isinstance(item, Mapping):
                        rendered = _visible_fields(item)
                        if rendered:
                            lines.append(f"- {rendered}")
    # Preserve the user-visible product/plan details without copying product
    # IDs, source metadata, model IDs, tools, or rank fields.
    return "\n".join(lines)


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, Mapping):
        return _text(response)
    return _text(response)


def _first_value(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _turn_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize common flat and nested API result formats, excluding grader data."""
    if isinstance(payload, list):
        sources = payload
    elif isinstance(payload, Mapping):
        sources = None
        for key in ("results", "cases", "case_results"):
            value = payload.get(key)
            if isinstance(value, list):
                sources = value
                break
        if sources is None:
            raise ValueError("result artifact must contain a results/cases list")
    else:
        raise ValueError("result artifact root must be a JSON object or list")

    turns: list[dict[str, Any]] = []
    for source_index, source in enumerate(sources, start=1):
        if not isinstance(source, Mapping):
            continue
        case_id = str(
            source.get("case_id") or source.get("conversation_id") or f"conversation-{source_index}"
        )
        nested_turns = source.get("turns")
        if isinstance(nested_turns, list):
            for turn_index, turn in enumerate(nested_turns, start=1):
                if not isinstance(turn, Mapping):
                    continue
                user = _text(_first_value(turn, USER_KEYS))
                answer = _first_value(turn, ANSWER_KEYS)
                turns.append(
                    {
                        "case_id": case_id,
                        "turn_index": _positive_int(
                            turn.get("turn_index", turn.get("turn")), turn_index
                        ),
                        "user_message": user,
                        "assistant_text": _response_text(answer),
                        "visible_recommendations": _visible_recommendations(answer),
                        "source_model": _response_model(answer, turn),
                    }
                )
            continue

        user = _text(_first_value(source, USER_KEYS))
        answer = _first_value(source, ANSWER_KEYS)
        if answer is None:
            continue
        turns.append(
            {
                "case_id": case_id,
                "turn_index": _positive_int(
                    source.get("turn_index", source.get("turn")), source_index
                ),
                "user_message": user,
                "assistant_text": _response_text(answer),
                "visible_recommendations": _visible_recommendations(answer),
                "source_model": _response_model(answer, source),
            }
        )

    turns = [row for row in turns if row["user_message"]]
    if not turns:
        raise ValueError(
            "no reviewable conversation turns found; expected user text and an API response"
        )
    return turns


def _response_model(response: Any, row: Mapping[str, Any]) -> str | None:
    if isinstance(response, Mapping) and response.get("model"):
        return str(response["model"])
    if row.get("model"):
        return str(row["model"])
    trace = row.get("llm_trace")
    if isinstance(trace, list):
        for attempt in trace:
            if isinstance(attempt, Mapping) and attempt.get("model"):
                return str(attempt["model"])
    return None


def _catalog_hash(payload: Mapping[str, Any]) -> str | None:
    for key in ("catalog_sha256", "catalog_snapshot_sha256"):
        if payload.get(key):
            return str(payload[key])
    scope = payload.get("catalog_scope")
    if isinstance(scope, Mapping):
        for key in ("catalog_sha256", "catalog_snapshot_sha256", "winmart_dataset_sha256"):
            if scope.get(key):
                return str(scope[key])
    return None


def _source_units(payload: Any, source_label: str) -> list[tuple[str, str | None, Any]]:
    """Split comparison-mode result bundles into independently blinded sources."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("mode_results"), list):
        return [(source_label, None, payload)]
    units: list[tuple[str, str | None, Any]] = []
    for index, mode_result in enumerate(payload["mode_results"], start=1):
        if not isinstance(mode_result, Mapping):
            continue
        mode = str(mode_result.get("mode") or f"mode_{index}")
        unit_payload = dict(payload)
        unit_payload.pop("mode_results", None)
        for key in ("dataset_id", "dataset_sha256", "split", "evaluation_dataset"):
            mode_value = mode_result.get(key)
            outer_value = payload.get(key)
            if mode_value is not None and outer_value is not None and mode_value != outer_value:
                raise ValueError(
                    f"Mode {mode!r} has {key} provenance that differs from its parent artifact"
                )
            if mode_value is not None:
                unit_payload[key] = mode_value
        for key in ("cases", "results", "case_results"):
            if isinstance(mode_result.get(key), list):
                unit_payload[key] = mode_result[key]
                break
        units.append((f"{source_label}:{mode}", mode, unit_payload))
    return units or [(source_label, None, payload)]


def _prompt_records_sha256(turns: Sequence[Mapping[str, Any]]) -> str:
    prompt_records = [
        {
            "case_id": str(turn["case_id"]),
            "turn_index": int(turn["turn_index"]),
            "user_message": str(turn["user_message"]),
        }
        for turn in turns
    ]
    prompt_records.sort(
        key=lambda item: (item["case_id"], item["turn_index"], item["user_message"])
    )
    canonical = json.dumps(
        prompt_records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _dataset_prompt_set(dataset_path: Path) -> set[tuple[str, int, str]]:
    if dataset_path.suffix.casefold() == ".jsonl":
        payload: Any = [
            json.loads(line)
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases = payload
    elif isinstance(payload, Mapping):
        cases = payload.get("cases", [])
    else:
        raise ValueError("Evaluation dataset must contain a cases list")
    if not isinstance(cases, list):
        raise ValueError("Evaluation dataset must contain a cases list")
    prompts: set[tuple[str, int, str]] = set()
    case_ids: set[str] = set()
    prompt_identities: set[tuple[str, int]] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("Evaluation dataset cases must be objects")
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in case_ids:
            raise ValueError("Evaluation dataset cases need unique, non-empty case_id values")
        case_ids.add(case_id)
        nested_turns = case.get("turns")
        if isinstance(nested_turns, list):
            if not nested_turns:
                raise ValueError(f"Evaluation dataset case {case_id!r} has no turns")
            for index, turn in enumerate(nested_turns, start=1):
                if not isinstance(turn, Mapping):
                    raise ValueError(f"Evaluation dataset case {case_id!r} has an invalid turn")
                user_message = _text(_first_value(turn, USER_KEYS))
                turn_index = _positive_int(turn.get("turn_index", turn.get("turn")), index)
                if not user_message:
                    raise ValueError(
                        f"Evaluation dataset case {case_id!r} turn {turn_index} has no user text"
                    )
                identity = (case_id, turn_index)
                if identity in prompt_identities:
                    raise ValueError(
                        f"Evaluation dataset has duplicate turn identity {case_id!r}/{turn_index}"
                    )
                prompt_identities.add(identity)
                prompts.add((case_id, turn_index, user_message))
        else:
            query = _text(_first_value(case, USER_KEYS))
            if not query:
                query = str(case.get("query") or "").strip()
            if not query:
                raise ValueError(f"Evaluation dataset case {case_id!r} has no prompt text")
            prompts.add((case_id, 1, query))
    if not prompts:
        raise ValueError("Evaluation dataset has no prompt records")
    return prompts


def _validate_turn_prompts_against_dataset(
    turns: Sequence[Mapping[str, Any]],
    dataset_path: Path,
    *,
    require_complete: bool = False,
) -> None:
    expected = _dataset_prompt_set(dataset_path)
    observed_keys: set[tuple[str, int]] = set()
    unexpected = sorted(
        (
            str(turn["case_id"]),
            int(turn["turn_index"]),
            str(turn["user_message"]),
        )
        for turn in turns
        if (
            str(turn["case_id"]),
            int(turn["turn_index"]),
            str(turn["user_message"]),
        )
        not in expected
    )
    for turn in turns:
        identity = (str(turn["case_id"]), int(turn["turn_index"]))
        if identity in observed_keys:
            raise ValueError(
                "Result artifact contains a duplicate case/turn identity absent from a "
                "one-to-one evaluation record"
            )
        observed_keys.add(identity)
    if unexpected:
        raise ValueError(
            "Result artifact contains case IDs or user prompts absent from its registered "
            f"evaluation dataset: {unexpected[:3]}"
        )
    if require_complete:
        observed = {
            (str(turn["case_id"]), int(turn["turn_index"]), str(turn["user_message"]))
            for turn in turns
        }
        missing = sorted(expected - observed)
        if missing:
            raise ValueError(
                "Protected answer-quality source omits registered case/turn prompts; "
                f"missing {len(missing)} records, including {missing[:3]}"
            )


def _opaque_id(prefix: str, seed: int, *parts: str) -> str:
    raw = "\x1f".join((str(seed), *parts)).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:16]}"


def _blank_row(
    *,
    item_id: str,
    conversation_id: str,
    turn_index: int,
    dialogue_context: str,
    user_message: str,
    assistant_response: str,
    visible_recommendations: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": "1.0",
        "review_item_id": item_id,
        "conversation_id": conversation_id,
        "turn_index": turn_index,
        "dialogue_context": dialogue_context,
        "user_message": user_message,
        "assistant_response": assistant_response
        or "[Không ghi nhận được câu trả lời trong artifact.]",
        "visible_recommendations": visible_recommendations,
        "annotation_status": "UNREVIEWED",
    }
    row.update({field: "" for field in ANNOTATION_FIELDS if field != "annotation_status"})
    return row


def export_review_queues(
    results_paths: Sequence[Path],
    source_labels: Sequence[str],
    output_prefix: Path,
    *,
    seed: int = 20261029,
    split: str | None = None,
) -> dict[str, Any]:
    if not results_paths:
        raise ValueError("at least one --results artifact is required")
    if len(results_paths) != len(source_labels):
        raise ValueError("provide exactly one --source-label for each --results artifact")
    if any(not label.strip() for label in source_labels):
        raise ValueError("source labels must be non-empty (they are hidden from reviewers)")
    if len(set(source_labels)) != len(source_labels):
        raise ValueError("source labels must be unique within one review packet")

    review_rows: list[dict[str, Any]] = []
    provenance_sources: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    effective_splits: set[str] = set()
    for result_path, source_label in zip(results_paths, source_labels, strict=True):
        artifact_path = Path(result_path)
        raw = artifact_path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
        artifact_hash = _sha256_bytes(raw)
        for unit_label, mode, unit_payload in _source_units(payload, source_label):
            metadata = unit_payload if isinstance(unit_payload, Mapping) else {}
            evaluation_dataset = metadata.get("evaluation_dataset")
            if not isinstance(evaluation_dataset, Mapping):
                evaluation_dataset = None
            source_split_value = (
                evaluation_dataset.get("split")
                if evaluation_dataset is not None
                else None
            ) or metadata.get("split")
            source_split = str(source_split_value or "development")
            source_dataset_sha256 = str(
                metadata.get("dataset_sha256")
                or (
                    evaluation_dataset.get("dataset_sha256")
                    if evaluation_dataset is not None
                    else ""
                )
                or ""
            )
            if source_dataset_sha256:
                try:
                    reject_protected_dataset_hash_downgrade(
                        source_dataset_sha256, source_split
                    )
                except SplitPolicyError as exc:
                    raise ValueError(
                        f"Source {unit_label!r} uses an unauthorized dataset split: {exc}"
                    ) from exc
            if split is not None and source_split != split:
                raise ValueError(
                    f"Requested split {split!r} does not match source {unit_label!r} "
                    f"split {source_split!r}"
                )
            effective_splits.add(source_split)
            split_authorization: dict[str, Any] | None = None
            if evaluation_dataset is not None and evaluation_dataset.get("path"):
                try:
                    split_authorization = authorize_dataset_split(
                        Path(str(evaluation_dataset["path"])), source_split
                    )
                except SplitPolicyError as exc:
                    raise ValueError(
                        f"Source dataset for {unit_label!r} is not authorized: {exc}"
                    ) from exc
                if evaluation_dataset.get("dataset_sha256") != split_authorization[
                    "dataset_sha256"
                ]:
                    raise ValueError(
                        f"Source dataset hash for {unit_label!r} differs from the split registry"
                    )
                expected_dataset_id = str(
                    evaluation_dataset.get("dataset_id") or metadata.get("dataset_id") or ""
                )
                if str(metadata.get("dataset_id") or "") != expected_dataset_id:
                    raise ValueError(
                        f"Source dataset ID for {unit_label!r} does not match its artifact"
                    )
                if str(metadata.get("dataset_sha256") or "") != str(
                    split_authorization["dataset_sha256"]
                ):
                    raise ValueError(
                        f"Source result dataset hash for {unit_label!r} does not match the "
                        "registered evaluation dataset"
                    )
            elif source_split in PROTECTED_SPLITS:
                raise ValueError(
                    f"Protected {source_split} answer-quality review requires an "
                    "evaluation_dataset path and exact registered hash in the source artifact"
                )
            elif evaluation_dataset is not None and source_split in PROTECTED_SPLITS:
                raise ValueError(
                    f"Source {unit_label!r} declares an evaluation_dataset without a path"
                )

            if source_split in PROTECTED_SPLITS:
                authorize_evaluation_artifact_path(
                    artifact_path,
                    source_split,
                    artifact_role="answer-quality result input",
                )
            normalized_turns = _turn_records(unit_payload)
            if evaluation_dataset is not None and evaluation_dataset.get("path"):
                try:
                    _validate_turn_prompts_against_dataset(
                        normalized_turns,
                        Path(str(evaluation_dataset["path"])),
                        require_complete=source_split in PROTECTED_SPLITS,
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Cannot validate source prompts for {unit_label!r}: {exc}"
                    ) from exc
            prompt_set_sha256 = _prompt_records_sha256(normalized_turns)
            provenance_sources.append(
                {
                    "source_label": unit_label,
                    "mode": mode,
                    "artifact_name": artifact_path.name,
                    "artifact_path": str(artifact_path.resolve()),
                    "artifact_sha256": artifact_hash,
                    "artifact_id": metadata.get("artifact_id") or metadata.get("kind"),
                    "dataset_id": metadata.get("dataset_id"),
                    "dataset_sha256": metadata.get("dataset_sha256"),
                    "dataset_authoring_method": metadata.get("authoring_method"),
                    "split": source_split,
                    "evaluation_dataset": dict(evaluation_dataset)
                    if evaluation_dataset is not None
                    else None,
                    "split_authorization": split_authorization,
                    "prompt_set_sha256": prompt_set_sha256,
                    "catalog_sha256": _catalog_hash(metadata),
                    "provider": metadata.get("provider"),
                    "model_primary": metadata.get("model_primary") or metadata.get("model"),
                    "commit_sha": metadata.get("commit_sha"),
                    "generated_at_utc": metadata.get("generated_at_utc")
                    or metadata.get("created_at_utc"),
                    "turn_count": len(normalized_turns),
                }
            )
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for turn in normalized_turns:
                grouped[turn["case_id"]].append(turn)
            for case_id, turns in sorted(grouped.items()):
                turns.sort(key=lambda item: item["turn_index"])
                conversation_id = _opaque_id("conv", seed, artifact_hash, unit_label, case_id)
                history: list[str] = []
                occurrence_by_turn: dict[int, int] = defaultdict(int)
                for turn in turns:
                    turn_index = turn["turn_index"]
                    occurrence_by_turn[turn_index] += 1
                    item_id = _opaque_id(
                        "review",
                        seed,
                        artifact_hash,
                        unit_label,
                        case_id,
                        str(turn_index),
                        str(occurrence_by_turn[turn_index]),
                    )
                    context = "\n\n".join(history)
                    row = _blank_row(
                        item_id=item_id,
                        conversation_id=conversation_id,
                        turn_index=turn_index,
                        dialogue_context=context,
                        user_message=turn["user_message"],
                        assistant_response=turn["assistant_text"],
                        visible_recommendations=turn["visible_recommendations"],
                    )
                    review_rows.append(row)
                    assignments.append(
                        {
                            "review_item_id": item_id,
                            "conversation_id": conversation_id,
                            "source_label": unit_label,
                            "artifact_sha256": artifact_hash,
                            "original_case_id": case_id,
                            "turn_index": turn_index,
                            "model": turn.get("source_model"),
                        }
                    )
                    history.append(f"Khách: {turn['user_message']}")
                    assistant = turn["assistant_text"]
                    recommendations = turn["visible_recommendations"]
                    if recommendations:
                        assistant = (
                            f"{assistant}\n{recommendations}" if assistant else recommendations
                        )
                    history.append(f"Trợ lý: {assistant}")

    if not review_rows:
        raise ValueError("no reviewable turns found")
    if len(effective_splits) != 1:
        raise ValueError("One answer-quality review packet cannot mix evaluation splits")
    if len(provenance_sources) > 1:
        comparison_fields = (
            "dataset_id",
            "dataset_sha256",
            "catalog_sha256",
            "prompt_set_sha256",
        )
        for field in comparison_fields:
            observed = {source.get(field) for source in provenance_sources}
            if len(observed) != 1:
                raise ValueError(
                    "Paired answer-quality comparisons require identical source "
                    f"{field}; got {sorted(str(value) for value in observed)}"
                )
    effective_split = next(iter(effective_splits))
    output_prefix = Path(output_prefix)
    authorize_evaluation_artifact_path(
        output_prefix,
        effective_split,
        artifact_role="answer-quality review output prefix",
    )
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    written: dict[str, Any] = {"row_count": len(review_rows)}
    queues: dict[str, list[dict[str, Any]]] = {}
    for reviewer, reviewer_seed in (("annotator_a", seed), ("annotator_b", seed + 1)):
        rows = list(review_rows)
        random.Random(reviewer_seed).shuffle(rows)
        queues[reviewer] = rows
    if len(review_rows) > 1:
        order_a = [row["review_item_id"] for row in queues["annotator_a"]]
        order_b = [row["review_item_id"] for row in queues["annotator_b"]]
        if order_a == order_b:
            queues["annotator_b"] = queues["annotator_b"][1:] + queues["annotator_b"][:1]

    for reviewer in ("annotator_a", "annotator_b"):
        output_path = output_prefix.with_name(f"{output_prefix.name}_{reviewer}.csv")
        authorize_evaluation_artifact_path(
            output_path,
            effective_split,
            artifact_role=f"answer-quality {reviewer} queue",
        )
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="raise")
            writer.writeheader()
            writer.writerows(queues[reviewer])
        written[reviewer] = str(output_path)

    manifest_path = output_prefix.with_name(f"{output_prefix.name}_evaluator_only_manifest.json")
    authorize_evaluation_artifact_path(
        manifest_path,
        effective_split,
        artifact_role="answer-quality evaluator-only manifest",
    )
    manifest = {
        "artifact_id": "vietnamese_answer_quality_review_packet",
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "seed": seed,
        "split": effective_split,
        "split_authorization": [
            source["split_authorization"]
            for source in provenance_sources
            if source.get("split_authorization") is not None
        ],
        "source_artifacts": provenance_sources,
        "assignments": assignments,
        "frozen_review_packet": {
            "row_count": len(review_rows),
            "visible_content_sha256": _frozen_review_packet_sha256(review_rows),
            "hashed_fields": list(FROZEN_REVIEW_FIELDS),
        },
        "reviewer_queues": {
            "model_provider_and_source_hidden": True,
            "case_ids_and_grader_expectations_hidden": True,
            "response_rank_and_system_metadata_hidden": True,
            "conversation_context_preserved": True,
            "rows_randomized_independently": True,
            "do_not_distribute_manifest_to_annotators": True,
        },
        "annotation_status": "UNREVIEWED; no human labels are present",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written["evaluator_manifest"] = str(manifest_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        action="append",
        required=True,
        help="API/conversation result JSON; repeat with --source-label for blind comparison.",
    )
    parser.add_argument(
        "--source-label",
        action="append",
        required=True,
        help="Evaluator-only source label in the same order as --results.",
    )
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--split", choices=("development", "validation", "blind_test"))
    args = parser.parse_args()
    result = export_review_queues(
        args.results,
        args.source_label,
        args.output_prefix,
        seed=args.seed,
        split=args.split,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
