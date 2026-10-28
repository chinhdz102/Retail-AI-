from __future__ import annotations

import hashlib

import pytest

from backend.config import Settings
from evaluation import live_text_conversation_benchmark as benchmark


def test_reproducibility_metadata_hashes_cases_without_exposing_them() -> None:
    metadata = benchmark._reproducibility_metadata(b"private case payload")

    assert metadata["case_payload_sha256"] == hashlib.sha256(
        b"private case payload"
    ).hexdigest()
    assert metadata["git_commit"]
    assert metadata["tracked_worktree_clean"] in {True, False, None}
    assert metadata["tracked_diff_sha256"]
    assert metadata["runtime"]["python"]
    assert "private case payload" not in repr(metadata)


class FakeCatalogRepository:
    def __init__(self, product_ids: set[str]) -> None:
        self.product_ids = product_ids

    def get_product_snapshot(self, product_id: str):
        return object() if product_id in self.product_ids else None


def test_case_catalog_preflight_checks_expected_and_excluded_ids(monkeypatch) -> None:
    repository = FakeCatalogRepository({"COKE_ZERO_330", "WATER01"})
    monkeypatch.setattr(benchmark, "get_product_repository", lambda _settings: repository)
    settings = Settings(_env_file=None, catalog_repository="demo")
    cases = [
        {
            "turns": [
                {
                    "expected_product_ids": ["COKE_ZERO_330"],
                    "must_not_include_product_ids": ["WATER01"],
                }
            ]
        }
    ]

    result = benchmark._preflight_case_catalog(cases, settings)

    assert result == {
        "status": "PASSED",
        "catalog_repository": "demo",
        "checked_product_ids": 2,
    }


def test_case_catalog_preflight_checks_any_expected_product_ids(monkeypatch) -> None:
    repository = FakeCatalogRepository({"WATER01", "TEA01"})
    monkeypatch.setattr(benchmark, "get_product_repository", lambda _settings: repository)
    settings = Settings(_env_file=None, catalog_repository="demo")

    result = benchmark._preflight_case_catalog(
        [{"turns": [{"expected_product_ids_any": ["WATER01", "TEA01"]}]}],
        settings,
    )

    assert result["status"] == "PASSED"
    assert result["checked_product_ids"] == 2


def test_turn_grading_accepts_any_grounded_alternative_product() -> None:
    grade = benchmark.grade_turn(
        {"expected_product_ids_any": ["WATER01", "TEA01"]},
        {"text": "Mình có vài lựa chọn thay thế.", "grounded_product_ids": ["TEA01"]},
        200,
    )

    assert grade["checks"]["expected_product_ids_any"] is True
    assert grade["passed"] is True


def test_turn_grading_checks_product_id_subset_against_previous_visible_list() -> None:
    grade = benchmark.grade_turn(
        {
            "expected_product_ids_relation": {
                "operation": "subset_of_turn",
                "turn": 1,
            }
        },
        {"text": "Giá của loại thứ hai là 29.000đ.", "grounded_product_ids": ["TOWEL02"]},
        200,
        prior_turn_responses=[
            {"grounded_product_ids": ["TOWEL01", "TOWEL02", "TOWEL03"]}
        ],
    )

    assert grade["checks"]["expected_product_ids_relation"] is True
    assert grade["passed"] is True


def test_turn_grading_rejects_product_outside_previous_visible_list() -> None:
    grade = benchmark.grade_turn(
        {
            "expected_product_ids_relation": {
                "operation": "subset_of_turn",
                "turn": 1,
            }
        },
        {"text": "Giá của loại thứ hai là 29.000đ.", "grounded_product_ids": ["SOAP01"]},
        200,
        prior_turn_responses=[{"grounded_product_ids": ["TOWEL01", "TOWEL02"]}],
    )

    assert grade["checks"]["expected_product_ids_relation"] is False
    assert grade["passed"] is False


def test_turn_grading_checks_list_removal_against_original_minus_referenced_item() -> None:
    grade = benchmark.grade_turn(
        {
            "expected_product_ids_relation": {
                "operation": "equal_difference",
                "base_turn": 1,
                "subtract_turn": 2,
            }
        },
        {"text": "Đã bỏ món đó khỏi danh sách.", "grounded_product_ids": ["TOWEL01", "TOWEL03"]},
        200,
        prior_turn_responses=[
            {"grounded_product_ids": ["TOWEL01", "TOWEL02", "TOWEL03"]},
            {"grounded_product_ids": ["TOWEL02"]},
        ],
    )

    assert grade["checks"]["expected_product_ids_relation"] is True
    assert grade["passed"] is True


def test_turn_grading_rejects_incorrect_list_removal_or_missing_reference_turn() -> None:
    expectation = {
        "expected_product_ids_relation": {
            "operation": "equal_difference",
            "base_turn": 1,
            "subtract_turn": 2,
        }
    }
    wrong_result = benchmark.grade_turn(
        expectation,
        {"text": "Đã bỏ món đó khỏi danh sách.", "grounded_product_ids": ["TOWEL01"]},
        200,
        prior_turn_responses=[
            {"grounded_product_ids": ["TOWEL01", "TOWEL02", "TOWEL03"]},
            {"grounded_product_ids": ["TOWEL02"]},
        ],
    )
    missing_reference = benchmark.grade_turn(
        {
            "expected_product_ids_relation": {
                "operation": "equal_turn",
                "turn": 2,
            }
        },
        {"text": "Đã bỏ món đó khỏi danh sách.", "grounded_product_ids": ["TOWEL02"]},
        200,
        prior_turn_responses=[{"grounded_product_ids": ["TOWEL01"]}],
    )

    assert wrong_result["checks"]["expected_product_ids_relation"] is False
    assert missing_reference["checks"]["expected_product_ids_relation"] is False


def test_turn_grading_can_assert_alternative_novelty_across_prior_pages() -> None:
    grade = benchmark.grade_turn(
        {
            "expected_product_ids_relation": {
                "operation": "disjoint_from_turns",
                "turns": [1, 2],
            }
        },
        {"text": "Mình có món khác đây.", "grounded_product_ids": ["TOWEL04"]},
        200,
        prior_turn_responses=[
            {"grounded_product_ids": ["TOWEL01", "TOWEL02"]},
            {"grounded_product_ids": ["TOWEL03"]},
        ],
    )

    assert grade["checks"]["expected_product_ids_relation"] is True


def test_case_catalog_preflight_rejects_demo_ids_against_snapshot(monkeypatch) -> None:
    repository = FakeCatalogRepository({"WIN_10194711"})
    monkeypatch.setattr(benchmark, "get_product_repository", lambda _settings: repository)
    settings = Settings(_env_file=None, catalog_repository="snapshot")
    cases = [{"turns": [{"expected_product_ids": ["COKE_ZERO_330"]}]}]

    with pytest.raises(ValueError, match="Case catalog mismatch") as error:
        benchmark._preflight_case_catalog(cases, settings)

    assert "COKE_ZERO_330" in str(error.value)
    assert "Select a compatible case set" in str(error.value)


def test_case_catalog_preflight_does_not_claim_postgres_was_verified(monkeypatch) -> None:
    def unexpected_repository_lookup(_settings):
        raise AssertionError("PostgreSQL must not be described as locally verified")

    monkeypatch.setattr(benchmark, "get_product_repository", unexpected_repository_lookup)
    settings = Settings(_env_file=None, catalog_repository="postgres")
    cases = [{"turns": [{"expected_product_ids": ["COKE_ZERO_330"]}]}]

    result = benchmark._preflight_case_catalog(cases, settings)

    assert result["status"] == "NOT_RUN"
    assert result["catalog_repository"] == "postgres"
    assert "not implemented" in result["reason"]


def test_case_catalog_preflight_is_not_required_without_product_assertions() -> None:
    settings = Settings(_env_file=None, catalog_repository="snapshot")

    result = benchmark._preflight_case_catalog([{"turns": [{"text": "Chào bạn."}]}], settings)

    assert result == {
        "status": "NOT_REQUIRED",
        "catalog_repository": "snapshot",
        "checked_product_ids": 0,
    }
