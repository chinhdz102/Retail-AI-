from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from backend.data.catalog_import import canonical_dataset_sha256
from evaluation import export_vietnamese_answer_quality_review as exporter_module
from evaluation.export_vietnamese_answer_quality_review import (
    ANNOTATION_FIELDS,
    CSV_FIELDS,
    FROZEN_REVIEW_FIELDS,
    _frozen_review_packet_sha256,
    export_review_queues,
)
from evaluation.split_policy import SplitPolicyError

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _read_csv(path: str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_answer_quality_export_blinds_sources_and_preserves_provenance(tmp_path) -> None:
    first = _write_json(
        tmp_path / "first.json",
        {
            "kind": "live_text_conversation_benchmark",
            "dataset_id": "dataset-secret",
            "dataset_sha256": "a" * 64,
            "catalog_scope": {"winmart_dataset_sha256": "b" * 64},
            "provider": "secret-provider",
            "model_primary": "secret-model",
            "commit_sha": "deadbeef",
            "results": [
                {
                    "case_id": "secret-case-1",
                    "turn": 1,
                    "prompt": "Mình cần nước rửa chén dưới 70 nghìn.",
                    "response": {
                        "text": "Mình có thể tìm giúp bạn.",
                        "model": "secret-model",
                        "products": [
                            {
                                "product_id": "SECRET_PRODUCT_ID",
                                "name": "Nước rửa chén A",
                                "brand": "A",
                                "price_vnd": 50000,
                                "data_source": "private-catalog-source",
                            }
                        ],
                    },
                    "grade": {"expected": "GOLD_EXPECTATION_SECRET"},
                    "conversation_tags": ["secret-tag"],
                    "llm_trace": [{"model": "secret-model", "tool": "secret-tool"}],
                },
                {
                    "case_id": "secret-case-1",
                    "turn": 2,
                    "prompt": "Loại nào dịu nhẹ hơn?",
                    "response": {"text": "Mình chưa có dữ liệu để khẳng định."},
                    "grade": {"expected": "SECRET_GOLD_TWO"},
                },
            ],
        },
    )
    second = _write_json(
        tmp_path / "second.json",
        {
            "kind": "second-system",
            "dataset_id": "dataset-secret",
            "dataset_sha256": "a" * 64,
            "catalog_scope": {"winmart_dataset_sha256": "b" * 64},
            "model_primary": "other-secret-model",
            "results": [
                {
                    "case_id": "secret-case-1",
                    "turn": 1,
                    "prompt": "Mình cần nước rửa chén dưới 70 nghìn.",
                    "response": {"text": "Mình thấy một lựa chọn trong tầm giá."},
                    "evaluation_expectations": {"gold": "SECRET_EXPECTATION"},
                },
                {
                    "case_id": "secret-case-1",
                    "turn": 2,
                    "prompt": "Loại nào dịu nhẹ hơn?",
                    "response": {"text": "Mình chưa có thông tin xác minh."},
                },
            ],
        },
    )

    exported = export_review_queues(
        [first, second],
        ["baseline_source", "candidate_source"],
        tmp_path / "answer-review",
        seed=19,
    )

    rows_a = _read_csv(exported["annotator_a"])
    rows_b = _read_csv(exported["annotator_b"])
    assert len(rows_a) == len(rows_b) == 4
    assert set(rows_a[0]) == set(CSV_FIELDS)
    assert {row["review_item_id"] for row in rows_a} == {row["review_item_id"] for row in rows_b}
    assert [row["review_item_id"] for row in rows_a] != [row["review_item_id"] for row in rows_b]
    for row in rows_a + rows_b:
        assert row["annotation_status"] == "UNREVIEWED"
        assert all(row[field] == "" for field in ANNOTATION_FIELDS if field != "annotation_status")

    content = Path(exported["annotator_a"]).read_text(encoding="utf-8-sig")
    for hidden in (
        "secret-provider",
        "secret-model",
        "other-secret-model",
        "secret-case-1",
        "SECRET_PRODUCT_ID",
        "private-catalog-source",
        "GOLD_EXPECTATION_SECRET",
        "SECRET_GOLD_TWO",
        "secret-tool",
        "baseline_source",
        "candidate_source",
    ):
        assert hidden not in content
    assert "Nước rửa chén A" in content
    assert "price vnd: 50000" in content
    later_turn = next(row for row in rows_a if row["turn_index"] == "2")
    assert "Loại nào dịu nhẹ hơn?" in later_turn["user_message"]
    assert "Mình cần nước rửa chén dưới 70 nghìn." in later_turn["dialogue_context"]
    assert "Mình có thể tìm giúp bạn." in later_turn["dialogue_context"]

    manifest = json.loads(Path(exported["evaluator_manifest"]).read_text(encoding="utf-8"))
    assert manifest["annotation_status"] == "UNREVIEWED; no human labels are present"
    assert manifest["frozen_review_packet"]["row_count"] == 4
    assert len(manifest["frozen_review_packet"]["visible_content_sha256"]) == 64
    assert manifest["frozen_review_packet"]["hashed_fields"] == [
        "schema_version",
        "review_item_id",
        "conversation_id",
        "turn_index",
        "dialogue_context",
        "user_message",
        "assistant_response",
        "visible_recommendations",
    ]
    frozen_rows = [
        {field: row[field] for field in FROZEN_REVIEW_FIELDS}
        | {"turn_index": int(row["turn_index"])}
        for row in rows_a
    ]
    assert manifest["frozen_review_packet"]["visible_content_sha256"] == (
        _frozen_review_packet_sha256(frozen_rows)
    )
    assert manifest["reviewer_queues"]["do_not_distribute_manifest_to_annotators"] is True
    assert [item["source_label"] for item in manifest["source_artifacts"]] == [
        "baseline_source",
        "candidate_source",
    ]
    assert manifest["source_artifacts"][0]["dataset_sha256"] == "a" * 64
    assert manifest["source_artifacts"][0]["catalog_sha256"] == "b" * 64
    assert manifest["assignments"][0]["original_case_id"] == "secret-case-1"


def test_answer_quality_export_reads_nested_api_conversations(tmp_path) -> None:
    nested = _write_json(
        tmp_path / "nested.json",
        {
            "dataset_id": "nested-demo",
            "cases": [
                {
                    "case_id": "nested-case",
                    "turns": [
                        {
                            "user_text": "Tư vấn cho mình món ăn nhẹ nhé.",
                            "response": {
                                "text": "Đây là một phương án tham khảo.",
                                "shopping_plan": {
                                    "options": [
                                        {
                                            "label": "Gọn nhẹ",
                                            "total_vnd": 12000,
                                            "items": [
                                                {
                                                    "product_id": "HIDDEN_ID",
                                                    "name": "Snack khoai tây",
                                                    "quantity": 1,
                                                    "unit_price_vnd": 12000,
                                                    "data_source": "hidden-source",
                                                }
                                            ],
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                }
            ],
        },
    )
    exported = export_review_queues([nested], ["private-label"], tmp_path / "nested-review", seed=4)
    rows = _read_csv(exported["annotator_a"])

    assert len(rows) == 1
    assert rows[0]["user_message"] == "Tư vấn cho mình món ăn nhẹ nhé."
    assert rows[0]["assistant_response"] == "Đây là một phương án tham khảo."
    assert "Snack khoai tây" in rows[0]["visible_recommendations"]
    assert "total vnd: 12000" in rows[0]["visible_recommendations"]
    content = Path(exported["annotator_a"]).read_text(encoding="utf-8-sig")
    assert "HIDDEN_ID" not in content
    assert "hidden-source" not in content
    assert "private-label" not in content


def test_answer_quality_export_blinds_modes_in_api_comparison_artifact(tmp_path) -> None:
    comparison = _write_json(
        tmp_path / "comparison.json",
        {
            "kind": "live_shopping_mission_benchmark",
            "dataset_id": "comparison-dataset",
            "mode_results": [
                {
                    "mode": "current",
                    "cases": [
                        {
                            "case_id": "case-1",
                            "turns": [
                                {
                                    "turn": 1,
                                    "user_text": "Tìm nước uống giúp mình.",
                                    "response": {"text": "Đây là một lựa chọn."},
                                }
                            ],
                        }
                    ],
                },
                {
                    "mode": "legacy",
                    "cases": [
                        {
                            "case_id": "case-1",
                            "turns": [
                                {
                                    "turn": 1,
                                    "user_text": "Tìm nước uống giúp mình.",
                                    "response": {"text": "Mình tìm thấy một sản phẩm."},
                                }
                            ],
                        }
                    ],
                },
            ],
        },
    )

    exported = export_review_queues(
        [comparison], ["hidden_comparison"], tmp_path / "comparison-review", seed=8
    )
    rows = _read_csv(exported["annotator_a"])
    manifest = json.loads(Path(exported["evaluator_manifest"]).read_text(encoding="utf-8"))

    assert len(rows) == 2
    assert len({row["conversation_id"] for row in rows}) == 2
    assert "current" not in Path(exported["annotator_a"]).read_text(encoding="utf-8-sig")
    assert "legacy" not in Path(exported["annotator_a"]).read_text(encoding="utf-8-sig")
    assert [item["mode"] for item in manifest["source_artifacts"]] == ["current", "legacy"]
    assert {item["source_label"] for item in manifest["assignments"]} == {
        "hidden_comparison:current",
        "hidden_comparison:legacy",
    }


def test_answer_quality_schema_matches_blank_template_and_rubric_dimensions() -> None:
    schema_path = ROOT / "evaluation/schemas/vietnamese_answer_quality_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    template_path = ROOT / "evaluation/review/vietnamese_answer_quality_template_v1.csv"
    with template_path.open(encoding="utf-8-sig", newline="") as handle:
        template_rows = list(csv.reader(handle))

    assert template_rows == [CSV_FIELDS]
    assert schema["required"] == CSV_FIELDS
    assert set(schema["properties"]) >= set(CSV_FIELDS)
    assert all(
        schema["properties"][field]["$ref"] == "#/$defs/rating" for field in CSV_FIELDS[8:14]
    )
    assert all(
        schema["properties"][field]["$ref"] == "#/$defs/critical_flag"
        for field in CSV_FIELDS[14:22]
    )
    assert schema["properties"]["annotation_status"]["enum"] == [
        "UNREVIEWED",
        "INDEPENDENT_A",
        "INDEPENDENT_B",
        "ADJUDICATED",
    ]
    assert schema["properties"]["adjudication_decision"]["enum"] == [
        "",
        "KEEP_A",
        "KEEP_B",
        "NEW_JUDGMENT",
    ]
    assert "adjudication" in schema["properties"]


def test_answer_quality_export_rejects_unpaired_source_labels(tmp_path) -> None:
    result = _write_json(
        tmp_path / "empty.json",
        {"results": [{"case_id": "case-1", "prompt": "Hello", "response": "Hi"}]},
    )
    with pytest.raises(ValueError, match="one --source-label"):
        export_review_queues([result], [], tmp_path / "invalid")


def test_answer_quality_export_rejects_blind_metadata_without_registered_dataset(tmp_path) -> None:
    result = _write_json(
        tmp_path / "blind-result.json",
        {
            "kind": "live_text_conversation_benchmark",
            "dataset_id": "claimed-blind-dataset",
            "split": "blind_test",
            "results": [
                {
                    "case_id": "case-1",
                    "turn": 1,
                    "prompt": "Mình cần nước uống.",
                    "response": {"text": "Mình tìm giúp bạn."},
                }
            ],
        },
    )
    output_prefix = tmp_path / "should-not-write"

    with pytest.raises(ValueError, match="requires an evaluation_dataset path"):
        export_review_queues(
            [result], ["candidate"], output_prefix, split="blind_test"
        )

    assert not list(tmp_path.glob("should-not-write*"))


def test_answer_quality_export_keeps_protected_packets_outside_repository(
    tmp_path, monkeypatch
) -> None:
    dataset = tmp_path / "registered-blind.json"
    dataset.write_text(
        json.dumps(
            {
                "dataset_id": "blind-set",
                "cases": [
                    {
                        "case_id": "case-1",
                        "turns": [{"text": "Mình cần nước uống."}],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dataset_sha256 = hashlib.sha256(dataset.read_bytes()).hexdigest()
    monkeypatch.setattr(
        exporter_module,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": dataset_sha256},
    )
    result = _write_json(
        tmp_path / "protected-result.json",
        {
            "kind": "live_text_conversation_benchmark",
            "dataset_id": "blind-set",
            "dataset_sha256": dataset_sha256,
            "split": "blind_test",
            "evaluation_dataset": {
                "dataset_id": "blind-set",
                "split": "blind_test",
                "path": str(dataset),
                "dataset_sha256": dataset_sha256,
            },
            "results": [
                {
                    "case_id": "case-1",
                    "turn": 1,
                    "prompt": "Mình cần nước uống.",
                    "response": {"text": "Mình tìm giúp bạn."},
                }
            ],
        },
    )
    output_prefix = ROOT / "evaluation" / "review" / "should-not-leak-blind"

    with pytest.raises(ValueError, match="must stay outside the repository"):
        export_review_queues([result], ["candidate"], output_prefix, split="blind_test")

    assert not output_prefix.with_name(f"{output_prefix.name}_annotator_a.csv").exists()


def test_answer_quality_export_rejects_result_hash_not_bound_to_registered_dataset(
    tmp_path, monkeypatch
) -> None:
    dataset = _write_json(
        tmp_path / "blind.json",
        {
            "dataset_id": "blind-set",
            "cases": [
                {"case_id": "case-1", "turns": [{"text": "Mình cần nước uống."}]}
            ],
        },
    )
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    result = _write_json(
        tmp_path / "result.json",
        {
            "dataset_id": "blind-set",
            "dataset_sha256": "0" * 64,
            "split": "blind_test",
            "evaluation_dataset": {
                "dataset_id": "blind-set",
                "dataset_sha256": digest,
                "split": "blind_test",
                "path": str(dataset),
            },
            "results": [
                {
                    "case_id": "case-1",
                    "turn": 1,
                    "prompt": "Mình cần nước uống.",
                    "response": "Mình tìm giúp bạn.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        exporter_module,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": digest},
    )
    with pytest.raises(ValueError, match="result dataset hash"):
        export_review_queues([result], ["candidate"], tmp_path / "invalid", split="blind_test")


def test_answer_quality_export_rejects_protected_result_missing_dataset_turns(
    tmp_path, monkeypatch
) -> None:
    dataset = _write_json(
        tmp_path / "blind.json",
        {
            "dataset_id": "blind-set",
            "cases": [
                {"case_id": "case-1", "turns": [{"text": "Mình cần nước uống."}]},
                {"case_id": "case-2", "turns": [{"text": "Mình cần đồ ăn."}]},
            ],
        },
    )
    digest = canonical_dataset_sha256(dataset.read_bytes())
    monkeypatch.setattr(
        exporter_module,
        "authorize_dataset_split",
        lambda _path, _split: {"dataset_sha256": digest},
    )
    result = _write_json(
        tmp_path / "partial-result.json",
        {
            "dataset_id": "blind-set",
            "dataset_sha256": digest,
            "split": "blind_test",
            "evaluation_dataset": {
                "dataset_id": "blind-set",
                "dataset_sha256": digest,
                "split": "blind_test",
                "path": str(dataset),
            },
            "results": [
                {
                    "case_id": "case-1",
                    "turn": 1,
                    "prompt": "Mình cần nước uống.",
                    "response": "Mình tìm giúp bạn.",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="omits registered case/turn prompts"):
        export_review_queues([result], ["candidate"], tmp_path / "partial", split="blind_test")

    assert not list(tmp_path.glob("partial*annotator*.csv"))


def test_answer_quality_export_rejects_unpaired_prompt_sets(tmp_path) -> None:
    first = _write_json(
        tmp_path / "first.json",
        {
            "dataset_id": "same",
            "dataset_sha256": "a" * 64,
            "results": [{"case_id": "case-1", "prompt": "Tìm nước.", "response": "A"}],
        },
    )
    second = _write_json(
        tmp_path / "second.json",
        {
            "dataset_id": "same",
            "dataset_sha256": "a" * 64,
            "results": [{"case_id": "case-2", "prompt": "Tìm sữa.", "response": "B"}],
        },
    )

    with pytest.raises(ValueError, match="prompt_set_sha256"):
        export_review_queues(
            [first, second], ["baseline", "candidate"], tmp_path / "unpaired"
        )


def test_answer_quality_export_rejects_protected_hash_relabelled_as_development(
    tmp_path, monkeypatch
) -> None:
    def reject_registered_protected_hash(dataset_sha256: str, split: str) -> None:
        if dataset_sha256 == "c" * 64 and split == "development":
            raise SplitPolicyError("registered protected dataset cannot be downgraded")

    monkeypatch.setattr(
        exporter_module,
        "reject_protected_dataset_hash_downgrade",
        reject_registered_protected_hash,
    )
    result = _write_json(
        tmp_path / "relabelled-result.json",
        {
            "dataset_id": "registered-blind",
            "dataset_sha256": "c" * 64,
            "split": "development",
            "results": [
                {"case_id": "case-1", "prompt": "Tìm đồ uống.", "response": "Có đây."}
            ],
        },
    )

    with pytest.raises(ValueError, match="cannot be downgraded"):
        export_review_queues([result], ["candidate"], tmp_path / "blocked")

    assert not list(tmp_path.glob("blocked*"))
