"""Persistent 30-day sales ranking store for the Thanh Lam Mart kiosk catalog."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from backend.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_product_sales (
    product_id TEXT PRIMARY KEY,
    units_sold_30d INTEGER NOT NULL
        CHECK (typeof(units_sold_30d) = 'integer' AND units_sold_30d BETWEEN 0 AND 999),
    period_days INTEGER NOT NULL DEFAULT 30 CHECK (period_days = 30),
    data_source TEXT NOT NULL DEFAULT 'SIMULATED' CHECK (data_source = 'SIMULATED'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class DemoSalesStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()

    def seed(self, product_ids: Iterable[str]) -> dict[str, int]:
        from backend.services.demo_sales import simulated_units

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=2) as connection:
            connection.executescript(SCHEMA)
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO demo_product_sales (product_id, units_sold_30d) "
                "VALUES (?, ?)",
                ((pid, simulated_units(pid)) for pid in dict.fromkeys(product_ids)),
            )
            inserted = connection.total_changes - before
            count, minimum, maximum = connection.execute(
                "SELECT count(*), min(units_sold_30d), max(units_sold_30d) "
                "FROM demo_product_sales"
            ).fetchone()
        _read_counts.cache_clear()
        return {"inserted": inserted, "count": count, "min": minimum, "max": maximum}

    def counts(self) -> dict[str, int]:
        if not self.path.is_file():
            return {}
        stat = self.path.stat()
        # Return a copy so a caller cannot change cached persisted observations.
        return dict(_read_counts(self.path, stat.st_mtime_ns, stat.st_size))


@lru_cache(maxsize=8)
def _read_counts(path: Path, _mtime_ns: int, _size: int) -> tuple[tuple[str, int], ...]:
    # Read-only mode never creates a missing/empty sales database on a request.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
        return tuple(connection.execute(
            "SELECT product_id, units_sold_30d FROM demo_product_sales "
            "WHERE data_source = 'SIMULATED' AND period_days = 30"
        ))


def get_demo_sales_store(settings: Settings | None = None) -> DemoSalesStore:
    path = Path((settings or get_settings()).demo_sales_database_path)
    return DemoSalesStore(path if path.is_absolute() else ROOT / path)
