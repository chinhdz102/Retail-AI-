"""Synced 30-day catalog sales loader (FRN Store 18 crosswalk + monthly catalog sales)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

SalesSource = Literal["SIMULATED", "FRN_STORE_18", "CATALOG_MONTHLY"]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYNCED_SALES_PATH = ROOT / "data" / "synced_sales_30d.json"


@dataclass(frozen=True)
class SyncedSalesData:
    counts: dict[str, int]
    sources: dict[str, SalesSource]
    latest_month: str = ""
    frn_store_id: int | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.counts)

    def get(self, product_id: str) -> tuple[int | None, SalesSource | None]:
        val = self.counts.get(product_id)
        if val is None:
            return None, None
        return val, self.sources.get(product_id, "CATALOG_MONTHLY")


@lru_cache(maxsize=4)
def _load_synced_sales(path: Path, _mtime_ns: int, _size: int) -> SyncedSalesData:
    raw = json.loads(path.read_text(encoding="utf-8"))
    counts = {str(k): int(v) for k, v in (raw.get("counts") or {}).items()}
    raw_sources = raw.get("sources") or {}
    sources: dict[str, SalesSource] = {}
    for pid in counts:
        src = raw_sources.get(pid)
        if src in ("FRN_STORE_18", "CATALOG_MONTHLY", "SIMULATED"):
            sources[pid] = src
        else:
            sources[pid] = "CATALOG_MONTHLY"
    return SyncedSalesData(
        counts=counts,
        sources=sources,
        latest_month=str(raw.get("latest_month") or ""),
        frn_store_id=int(raw["frn_store_id"]) if raw.get("frn_store_id") is not None else None,
    )


def get_synced_sales_data(path: Path | None = None) -> SyncedSalesData:
    target = (path or DEFAULT_SYNCED_SALES_PATH).resolve()
    if not target.is_file():
        return SyncedSalesData(counts={}, sources={})
    stat = target.stat()
    return _load_synced_sales(target, stat.st_mtime_ns, stat.st_size)
