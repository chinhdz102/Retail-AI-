from __future__ import annotations

import re

from backend.products import postgres_repository
from evaluation.run_product_search_benchmark import _case_score, _metrics


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


def test_product_search_scores_reciprocal_rank_and_recall_at_three() -> None:
    score = _case_score(["other", "WIN_10194711", "third"], {"WIN_10194711"}, 3)
    summary = _metrics([score], [12.5])

    assert score["reciprocal_rank"] == 0.5
    assert summary["recall_at_1"] == 0
    assert summary["recall_at_3"] == 1
    assert summary["mrr"] == 0.5
    assert summary["median_latency_ms"] == 12.5
