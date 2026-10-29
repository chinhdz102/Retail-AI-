from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation import build_semantic_intelligence_v2_frozen as dataset_builder
from evaluation.export_vietnamese_qrels_review import REVIEW_FIELDS, export_review_queues
from evaluation.split_policy import SplitPolicyError, authorize_dataset_split

ROOT = Path(__file__).resolve().parents[1]


def _stable_text_sha256(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def test_known_semantic_frozen_dataset_is_registered_as_exposed_development() -> None:
    dataset = (
        ROOT
        / "evaluation"
        / "cases"
        / "retail_ai_semantic_intelligence_v2_frozen_20261028.json"
    )

    authorization = authorize_dataset_split(dataset, "development")

    assert authorization["authorization_status"] == "registered_exposed_development"
    assert authorization["effective_split"] == "development"
    assert authorization["eligible_for_blind_claim"] is False
    assert authorization["dataset_sha256"] == (
        "0fbfbdcecb83a0cfe2a8fc24bb688526a8a5db4a7ab797a3c73217b677d2e1f1"
    )


def test_known_dataset_hash_is_stable_across_checkout_line_endings(tmp_path: Path) -> None:
    source = (
        ROOT
        / "evaluation"
        / "cases"
        / "retail_ai_semantic_intelligence_v2_frozen_20261028.json"
    )
    content = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lf_path = tmp_path / "frozen-lf.json"
    crlf_path = tmp_path / "frozen-crlf.json"
    lf_path.write_bytes(content)
    crlf_path.write_bytes(content.replace(b"\n", b"\r\n"))

    lf_result = authorize_dataset_split(lf_path, "development")
    crlf_result = authorize_dataset_split(crlf_path, "development")

    assert lf_result["dataset_sha256"] == crlf_result["dataset_sha256"]
    assert lf_result["authorization_status"] == "registered_exposed_development"
    assert crlf_result["authorization_status"] == "registered_exposed_development"


def test_repo_visible_heldout_case_cannot_be_promoted_to_blind_test() -> None:
    dataset = ROOT / "evaluation" / "cases" / "generalized_catalog_consultation_heldout_v1.jsonl"

    with pytest.raises(SplitPolicyError, match="(?i)repository-visible.*development-only"):
        authorize_dataset_split(dataset, "blind_test")


def test_any_repo_visible_file_cannot_be_blind_even_if_registry_claims_it(tmp_path: Path) -> None:
    dataset = ROOT / "tests" / "test_evaluation_split_policy.py"
    digest = _stable_text_sha256(dataset)
    registry = tmp_path / "split_registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "known_exposed_datasets": [],
                "protected_datasets": [
                    {
                        "sha256": digest,
                        "split": "blind_test",
                        "independent_authoring_verified": True,
                        "independent_review_complete": True,
                        "independent_reviewer_count": 2,
                        "adjudication_complete": True,
                        "frozen_before_model_selection": True,
                        "not_used_for_tuning": True,
                        "data_and_labels_external_to_repo": True,
                        "labels_sealed_until_final_evaluation": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SplitPolicyError, match="(?i)repository-visible.*development-only"):
        authorize_dataset_split(dataset, "blind_test", registry_path=registry)


def test_unregistered_external_dataset_is_development_only(tmp_path: Path) -> None:
    dataset = tmp_path / "private-query-set.json"
    dataset.write_text('{"cases": []}\n', encoding="utf-8")

    authorization = authorize_dataset_split(dataset, "development")

    assert authorization["authorization_status"] == "unregistered_development"
    assert authorization["eligible_for_blind_claim"] is False
    with pytest.raises(SplitPolicyError, match="is not registered for blind_test"):
        authorize_dataset_split(dataset, "blind_test")


def test_external_blind_split_requires_exact_hash_and_provenance(tmp_path: Path) -> None:
    dataset = tmp_path / "independent-blind.json"
    dataset.write_text('{"cases": [{"query": "tìm đồ uống"}]}\n', encoding="utf-8")
    digest = _stable_text_sha256(dataset)
    registry = tmp_path / "split_registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "known_exposed_datasets": [],
                "protected_datasets": [
                    {
                        "sha256": digest,
                        "split": "blind_test",
                        "independent_authoring_verified": True,
                        "independent_review_complete": True,
                        "independent_reviewer_count": 2,
                        "adjudication_complete": True,
                        "frozen_before_model_selection": True,
                        "not_used_for_tuning": True,
                        "data_and_labels_external_to_repo": True,
                        "labels_sealed_until_final_evaluation": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    authorization = authorize_dataset_split(dataset, "blind_test", registry_path=registry)

    assert authorization["authorization_status"] == "registered_protected_split"
    assert authorization["effective_split"] == "blind_test"
    assert authorization["eligible_for_blind_claim"] is True


def test_registered_blind_hash_does_not_authorize_modified_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "independent-blind.json"
    dataset.write_text('{"cases": []}\n', encoding="utf-8")
    old_digest = _stable_text_sha256(dataset)
    registry = tmp_path / "split_registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "known_exposed_datasets": [],
                "protected_datasets": [
                    {
                        "sha256": old_digest,
                        "split": "blind_test",
                        "independent_authoring_verified": True,
                        "independent_review_complete": True,
                        "not_used_for_tuning": True,
                        "data_and_labels_external_to_repo": True,
                        "labels_sealed_until_final_evaluation": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dataset.write_text('{"cases": [{"query": "changed"}]}\n', encoding="utf-8")

    with pytest.raises(SplitPolicyError, match="is not registered for blind_test"):
        authorize_dataset_split(dataset, "blind_test", registry_path=registry)


def test_blind_registration_requires_every_independence_and_sealing_flag(tmp_path: Path) -> None:
    dataset = tmp_path / "independent-blind.json"
    dataset.write_text('{"cases": []}\n', encoding="utf-8")
    digest = _stable_text_sha256(dataset)
    registry = tmp_path / "split_registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "known_exposed_datasets": [],
                "protected_datasets": [
                    {
                        "sha256": digest,
                        "split": "blind_test",
                        "independent_authoring_verified": True,
                        "independent_review_complete": False,
                        "independent_reviewer_count": 2,
                        "adjudication_complete": False,
                        "frozen_before_model_selection": True,
                        "not_used_for_tuning": True,
                        "data_and_labels_external_to_repo": True,
                        "labels_sealed_until_final_evaluation": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SplitPolicyError, match="independent_review_complete"):
        authorize_dataset_split(dataset, "blind_test", registry_path=registry)


def test_qrels_exporter_cannot_mark_a_repo_visible_dataset_as_blind(tmp_path: Path) -> None:
    with pytest.raises(SplitPolicyError, match="(?i)repository-visible.*development-only"):
        export_review_queues(
            dataset_builder.OUTPUT,
            tmp_path / "intentionally-missing-results.json",
            tmp_path / "review-queue",
            catalog_path=dataset_builder.CATALOG,
            split="blind_test",
        )


def test_qrels_review_fields_separate_suitability_and_hard_fit_dimensions() -> None:
    assert {
        "use_case_fit",
        "audience_fit",
        "variant_fit",
        "quantity_unit_fit",
        "budget_fit",
    } <= set(REVIEW_FIELDS)

    schema = json.loads(
        (ROOT / "evaluation" / "schemas" / "vietnamese_product_qrel_v1_1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert {
        "use_case",
        "audience",
        "variant",
        "quantity_unit",
        "budget",
    } <= set(schema["properties"]["constraint_fit"]["properties"])
    assert schema["properties"]["schema_version"]["const"] == "1.1"
