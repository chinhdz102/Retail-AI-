from __future__ import annotations

from typing import Any

import pytest

from backend.config import get_settings
from backend.products import postgres_repository
from backend.products.postgres_repository import PostgresProductRepository
from backend.services import retail_information
from backend.services.retail_information import PostgresRetailInformationRepository


class _Transaction:
    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _Connection:
    def __init__(self, *, fail_query: bool = False) -> None:
        self.close_timeout: float | None = None
        self.fail_query = fail_query

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def execute(self, *_args: Any, **_kwargs: Any) -> str:
        return "OK"

    async def fetch(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        if self.fail_query:
            raise TimeoutError("query exceeded its configured deadline")
        return []

    async def fetchrow(self, *_args: Any, **_kwargs: Any) -> None:
        if self.fail_query:
            raise TimeoutError("query exceeded its configured deadline")
        return None

    async def close(self, *, timeout: float | None = None) -> None:
        self.close_timeout = timeout


@pytest.mark.parametrize("operation,arguments", [
    ("search", {"query": "Coca Cola"}),
    ("catalog_terms_evidenced", {"terms": ("sugar",), "category": "drink"}),
    ("list_category_hierarchies", {}),
    ("list_speech_terms", {}),
    ("list_brand_names", {}),
    ("get_product_snapshot", {"product_id": "missing"}),
    ("list_promotions", {}),
    ("list_catalog_additions", {}),
])
@pytest.mark.parametrize("fail_query", [False, True])
def test_all_postgres_reads_bound_connection_query_and_cleanup(
    monkeypatch, operation: str, arguments: dict[str, Any], fail_query: bool,
) -> None:
    captured: dict[str, Any] = {}
    connection = _Connection(fail_query=fail_query)
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setenv("POSTGRES_COMMAND_TIMEOUT_SECONDS", "0.7")
    get_settings.cache_clear()

    async def fake_connect(dsn: str, **kwargs: Any) -> _Connection:
        captured["dsn"] = dsn
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(postgres_repository.asyncpg, "connect", fake_connect)
    try:
        repository = PostgresProductRepository("postgresql+asyncpg://unused")
        if fail_query:
            with pytest.raises(TimeoutError):
                getattr(repository, operation)(**arguments)
        else:
            getattr(repository, operation)(**arguments)
    finally:
        get_settings.cache_clear()
    assert captured["dsn"] == "postgresql://unused"
    assert captured["timeout"] == 0.4
    assert captured["command_timeout"] == 0.7
    assert connection.close_timeout == 0.7


@pytest.mark.parametrize("kind", ["store", "promotions", "receipts", "nearby"])
def test_branch_information_reads_use_the_same_deadlines(monkeypatch, kind: str) -> None:
    captured: dict[str, Any] = {}
    connection = _Connection()

    async def fake_connect(dsn: str, **kwargs: Any) -> _Connection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(retail_information.asyncpg, "connect", fake_connect)
    repository = PostgresRetailInformationRepository("postgresql://unused")
    result = repository.lookup(kind, "unmapped-kiosk")

    assert result.status == "DATA_UNAVAILABLE"
    assert captured["timeout"] == get_settings().postgres_connect_timeout_seconds
    assert captured["command_timeout"] == get_settings().postgres_command_timeout_seconds
    assert connection.close_timeout == captured["command_timeout"]
