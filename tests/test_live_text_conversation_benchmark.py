from __future__ import annotations

import pytest

from backend.config import Settings
from evaluation import live_text_conversation_benchmark as benchmark


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
