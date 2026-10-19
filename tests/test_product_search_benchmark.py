from __future__ import annotations

import re
from pathlib import Path

from backend.data.catalog_import import analyze_dataset
from backend.products import postgres_repository
from evaluation.run_product_search_benchmark import _case_score, _load_cases, _metrics


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class _Connection:
    def __init__(self) -> None:
        self.query = ""
        self.args: tuple[object, ...] = ()
        self.executed: list[str] = []

    def transaction(self):
        return _Transaction()

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append(query)
        return "SELECT 1"

    async def fetch(self, query: str, *args: object) -> list[dict]:
        self.query = query
        self.args = args
        return []

    async def close(self) -> None:
        return None


def test_postgres_search_includes_all_tokens_and_transposition_fuzzy_path(monkeypatch) -> None:
    connection = _Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", connect)
    repository = postgres_repository.PostgresProductRepository("postgresql://unused")

    assert repository.search(query="coca zreo 600ml", limit=3) == []

    assert "p.search_title" in connection.query
    assert " <% p.search_title" in connection.query
    assert "p.search_name ILIKE" in connection.query
    assert "pg_trgm.word_similarity_threshold" in connection.executed[0]
    parameter_numbers = sorted({int(value) for value in re.findall(r"\$(\d+)", connection.query)})
    assert parameter_numbers == list(range(1, len(connection.args) + 1))
    assert "coca zreo 600ml" in connection.args


def test_punctuation_only_search_is_constrained_to_zero_results(monkeypatch) -> None:
    connection = _Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", connect)
    repository = postgres_repository.PostgresProductRepository("postgresql://unused")

    assert repository.search(query="!!!") == []
    assert "WHERE p.active IS TRUE AND s.price_quality_status IN ('NORMAL', 'SALE') AND false" in (
        " ".join(connection.query.split())
    )


def test_like_wildcards_are_treated_as_literal_search_text(monkeypatch) -> None:
    connection = _Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", connect)
    repository = postgres_repository.PostgresProductRepository("postgresql://unused")

    assert repository.search(query="WIN_10005885") == []

    assert "ESCAPE '!'" in connection.query
    assert "%win!_10005885%" in connection.args


def test_bim_bim_slang_searches_snack_titles_and_categories_without_diapers(
    monkeypatch,
) -> None:
    connection = _Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", connect)
    repository = postgres_repository.PostgresProductRepository("postgresql://unused")

    for query in ("bim bim", "bimbim", "giá bim bim"):
        assert repository.search(query=query, limit=3) == []
        assert "p.search_title ILIKE" in connection.query
        assert "p.search_category ILIKE" in connection.query
        assert "%bim bim%" in connection.args
        assert "%snack%" in connection.args


def test_bim_bim_with_flavor_preserves_modifiers_and_restricts_to_snacks(monkeypatch) -> None:
    connection = _Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", connect)
    repository = postgres_repository.PostgresProductRepository("postgresql://unused")

    assert repository.search(query="Bim bim vị phô mai", limit=5) == []

    assert "%bim bim%" in connection.args
    assert "%snack%" in connection.args
    assert "%pho%" in connection.args
    assert "%mai%" in connection.args
    assert "p.search_category ILIKE" in connection.query
    assert " AND " in connection.query


def test_diacritic_bim_word_keeps_the_regular_category_search_path(monkeypatch) -> None:
    connection = _Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", connect)
    repository = postgres_repository.PostgresProductRepository("postgresql://unused")

    assert repository.search(query="bỉm") == []

    assert "p.search_category ILIKE" in connection.query
    assert "%bim%" in connection.args
    assert "%bim bim%" not in connection.args
    assert connection.query.count("p.search_category ILIKE") >= 2


def test_expanded_product_resolution_cases_reference_the_checked_in_catalog() -> None:
    root = Path(__file__).resolve().parents[1]
    cases = _load_cases(root / "evaluation/cases/product_resolution_v2.jsonl")
    products, _quarantined, report = analyze_dataset(root / "data/winmart_products.json")
    accepted_ids = {product.product_id for product in products}

    assert report["rows_total"] == 3_099
    assert len(cases) >= 20
    assert all(
        product_id in accepted_ids
        for case in cases
        for product_id in case["gold_product_ids"]
    )


def test_product_search_scores_reciprocal_rank_and_recall_at_three() -> None:
    score = _case_score(["other", "WIN_10194711", "third"], {"WIN_10194711"}, 3)
    summary = _metrics([score], [12.5])

    assert score["reciprocal_rank"] == 0.5
    assert summary["recall_at_1"] == 0
    assert summary["recall_at_3"] == 1
    assert summary["mrr"] == 0.5
    assert summary["median_latency_ms"] == 12.5
