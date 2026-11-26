"""Read-only inter-agent bridge connecting ``ops_chest`` state to ``Retail-AI-``.

Implements the cross-module read-only contract (Appendix C):
  1. Loads the shared SKU crosswalk (``data/bridge/crosswalk.csv``, 164 fresh SKUs)
     mapping ``ops_chest`` ``ops_sku`` identifiers to ``Retail-AI-`` ``product_id`` values.
  2. Polls ``ops_chest`` read-only endpoints (``/api/inventory/items``,
     ``/api/inventory/expiring``, ``/api/inventory/summary``) with a bounded TTL.
  3. Enforces HITL isolation: only manager-approved markdowns
     (``status in {"APPROVED", "EXECUTED"}``) propagate to customer prices;
     ``PENDING`` or ``REJECTED`` proposals are ignored.
  4. Suppresses live out-of-stock SKUs from available browse/promo recommendations
     while honestly reporting ``stock=0`` with ``stock_is_current=True`` on direct lookup.
  5. Fails closed to static snapshot caveats (``stock_is_current=False``) whenever
     ``ops_chest`` is unreachable, errors, or exceeds ``freshness_ttl_seconds``.
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.products.catalog import PriceOperator, ProductSnapshot
from backend.products.repository import CatalogAdditions, ProductRepository, ProductSort

logger = logging.getLogger(__name__)

_APPROVED_MARKDOWN_STATUSES = frozenset({"APPROVED", "EXECUTED"})


@dataclass(frozen=True)
class OpsCrosswalkEntry:
    ops_sku: str
    frn_id: str
    product_id: str
    thanh_lam_name: str
    thanh_lam_category: str
    price_vnd: int
    method: str


class OpsCrosswalkIndex:
    """Bi-directional index between ``ops_chest`` fresh SKUs and ``Retail-AI-`` product IDs."""

    def __init__(self, entries: tuple[OpsCrosswalkEntry, ...]) -> None:
        self.entries = entries
        self.by_ops_sku: dict[str, OpsCrosswalkEntry] = {e.ops_sku: e for e in entries}
        self.by_product_id: dict[str, OpsCrosswalkEntry] = {e.product_id: e for e in entries}

    @classmethod
    def from_csv(cls, path: Path) -> OpsCrosswalkIndex:
        raw_lines = [
            line
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        entries: list[OpsCrosswalkEntry] = []
        for row in csv.DictReader(raw_lines):
            ops_sku = (row.get("ops_sku") or "").strip()
            product_id = (row.get("product_id") or "").strip()
            if not ops_sku or not product_id:
                continue
            entries.append(
                OpsCrosswalkEntry(
                    ops_sku=ops_sku,
                    frn_id=(row.get("frn_id") or "").strip(),
                    product_id=product_id,
                    thanh_lam_name=(row.get("thanh_lam_name") or "").strip(),
                    thanh_lam_category=(row.get("thanh_lam_category") or "").strip(),
                    price_vnd=int(float(row.get("price_vnd") or 0)),
                    method=(row.get("method") or "").strip(),
                )
            )
        return cls(tuple(entries))

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class OpsLiveItemState:
    ops_sku: str
    product_id: str
    on_hand: int
    expiry_at: str | None = None
    approved_markdown_price_vnd: int | None = None
    approved_discount_pct: int | None = None
    markdown_status: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class OpsBranchSnapshot:
    is_fresh: bool
    fetched_at_monotonic: float
    as_of: datetime | None
    items_by_product_id: dict[str, OpsLiveItemState]
    branch_summary: dict[str, Any]
    error: str | None = None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class OpsChestBridgeClient:
    """Fail-closed HTTP client polling ``ops_chest`` read-only inventory & markdown endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        crosswalk: OpsCrosswalkIndex,
        freshness_ttl_seconds: int = 300,
        timeout_seconds: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.crosswalk = crosswalk
        self.freshness_ttl_seconds = max(1, int(freshness_ttl_seconds))
        self.timeout_seconds = max(0.2, float(timeout_seconds))
        self._transport = transport
        self._cached: OpsBranchSnapshot | None = None

    def invalidate_cache(self) -> None:
        self._cached = None

    def get_snapshot(self, *, force_refresh: bool = False) -> OpsBranchSnapshot:
        now_mono = time.monotonic()
        if (
            not force_refresh
            and self._cached is not None
            and (now_mono - self._cached.fetched_at_monotonic) < self.freshness_ttl_seconds
        ):
            return self._cached

        snapshot = self._fetch_once(now_mono)
        self._cached = snapshot
        return snapshot

    def _fetch_once(self, now_mono: float) -> OpsBranchSnapshot:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                summary_res = client.get("/api/inventory/summary")
                items_res = client.get("/api/inventory/items")
                expiring_res = client.get("/api/inventory/expiring")
        except httpx.HTTPError as exc:
            logger.warning("ops_chest bridge unreachable; failing closed: %s", exc)
            return OpsBranchSnapshot(
                is_fresh=False,
                fetched_at_monotonic=now_mono,
                as_of=None,
                items_by_product_id={},
                branch_summary={},
                error=str(exc),
            )

        if (
            summary_res.status_code != 200
            or items_res.status_code != 200
            or expiring_res.status_code != 200
        ):
            err = (
                f"non-200 ops_chest response: summary={summary_res.status_code}, "
                f"items={items_res.status_code}, expiring={expiring_res.status_code}"
            )
            logger.warning("ops_chest bridge error; failing closed: %s", err)
            return OpsBranchSnapshot(
                is_fresh=False,
                fetched_at_monotonic=now_mono,
                as_of=None,
                items_by_product_id={},
                branch_summary={},
                error=err,
            )

        try:
            summary_body = summary_res.json()
            items_body = items_res.json()
            expiring_body = expiring_res.json()
        except ValueError as exc:
            logger.warning("ops_chest bridge invalid JSON; failing closed: %s", exc)
            return OpsBranchSnapshot(
                is_fresh=False,
                fetched_at_monotonic=now_mono,
                as_of=None,
                items_by_product_id={},
                branch_summary={},
                error=str(exc),
            )

        now_utc = datetime.now(UTC)
        as_of = _parse_iso_datetime(
            summary_body.get("as_of") or summary_body.get("generated_at")
            if isinstance(summary_body, dict)
            else None
        )
        if as_of is not None:
            age_seconds = (now_utc - as_of).total_seconds()
            if age_seconds > self.freshness_ttl_seconds:
                return OpsBranchSnapshot(
                    is_fresh=False,
                    fetched_at_monotonic=now_mono,
                    as_of=as_of,
                    items_by_product_id={},
                    branch_summary=summary_body if isinstance(summary_body, dict) else {},
                    error=f"stale snapshot ({age_seconds:.1f}s > {self.freshness_ttl_seconds}s)",
                )

        # Parse approved FEFO markdowns strictly enforcing HITL status guard.
        approved_markdowns: dict[str, tuple[int, int, str, str | None]] = {}
        raw_expiring = (
            expiring_body.get("items", expiring_body)
            if isinstance(expiring_body, dict)
            else expiring_body
        )
        if isinstance(raw_expiring, list):
            for rec in raw_expiring:
                if not isinstance(rec, dict):
                    continue
                status = str(rec.get("status") or "").strip().upper()
                if status not in _APPROVED_MARKDOWN_STATUSES:
                    continue
                sku = str(rec.get("sku") or rec.get("ops_sku") or "").strip()
                if sku not in self.crosswalk.by_ops_sku:
                    continue
                md_price = rec.get("markdown_price_vnd")
                disc_pct = rec.get("discount_pct")
                if md_price is None or int(md_price) <= 0:
                    continue
                approved_markdowns[sku] = (
                    int(md_price),
                    int(disc_pct or 0),
                    status,
                    str(rec.get("expiry_at") or "").strip() or None,
                )

        mapped: dict[str, OpsLiveItemState] = {}
        raw_items = (
            items_body.get("items", items_body) if isinstance(items_body, dict) else items_body
        )
        if isinstance(raw_items, list):
            for rec in raw_items:
                if not isinstance(rec, dict):
                    continue
                sku = str(rec.get("sku") or rec.get("ops_sku") or "").strip()
                entry = self.crosswalk.by_ops_sku.get(sku)
                if entry is None:
                    continue
                on_hand = max(0, int(rec.get("on_hand", rec.get("stock", 0))))
                expiry_at = str(rec.get("expiry_at") or "").strip() or None
                updated_at = _parse_iso_datetime(rec.get("updated_at")) or as_of or now_utc
                md = approved_markdowns.get(sku)
                mapped[entry.product_id] = OpsLiveItemState(
                    ops_sku=sku,
                    product_id=entry.product_id,
                    on_hand=on_hand,
                    expiry_at=(md[3] if md and md[3] else expiry_at),
                    approved_markdown_price_vnd=(md[0] if md else None),
                    approved_discount_pct=(md[1] if md else None),
                    markdown_status=(md[2] if md else None),
                    updated_at=updated_at,
                )

        return OpsBranchSnapshot(
            is_fresh=True,
            fetched_at_monotonic=now_mono,
            as_of=as_of or now_utc,
            items_by_product_id=mapped,
            branch_summary=summary_body if isinstance(summary_body, dict) else {},
        )


class BridgedProductRepository:
    """ProductRepository wrapper overlaying fresh ``ops_chest`` stock & approved FEFO markdowns."""

    def __init__(
        self,
        base: ProductRepository,
        bridge: OpsChestBridgeClient,
    ) -> None:
        self._base = base
        self.bridge = bridge
        self.source_name = f"{base.source_name}+ops_chest_bridge"
        self.catalog_version = getattr(base, "catalog_version", None)
        self.supports_current_stock = True
        self.supports_spicy_filter = getattr(base, "supports_spicy_filter", False)
        self.supports_price_sort = getattr(base, "supports_price_sort", True)

    def _overlay_product(
        self,
        product: ProductSnapshot,
        snapshot: OpsBranchSnapshot,
    ) -> ProductSnapshot:
        if not snapshot.is_fresh:
            return product
        live = snapshot.items_by_product_id.get(product.product_id)
        if live is None:
            return product

        updates: dict[str, Any] = {
            "stock": live.on_hand,
            "stock_is_current": True,
        }
        if live.updated_at is not None:
            updates["data_timestamp"] = live.updated_at
        if live.expiry_at is not None:
            updates["expiry_at"] = live.expiry_at

        regular_price = product.list_price_vnd or product.price_vnd
        if (
            live.approved_markdown_price_vnd is not None
            and 0 < live.approved_markdown_price_vnd < regular_price
        ):
            pct = live.approved_discount_pct or round(
                (regular_price - live.approved_markdown_price_vnd) * 100 / regular_price
            )
            updates["list_price_vnd"] = regular_price
            updates["sale_price_vnd"] = live.approved_markdown_price_vnd
            updates["price_vnd"] = live.approved_markdown_price_vnd
            updates["promotion"] = f"Giảm {pct}% FEFO (đã duyệt từ ops_chest)"
        return product.model_copy(update=updates)

    @property
    def products(self) -> tuple[ProductSnapshot, ...]:
        base_products = getattr(self._base, "products", ())
        snapshot = self.bridge.get_snapshot()
        if not snapshot.is_fresh:
            return tuple(base_products)
        return tuple(self._overlay_product(p, snapshot) for p in base_products)

    def search(
        self,
        query: str = "",
        category: str | None = None,
        max_price_vnd: int | None = None,
        spicy: bool | None = None,
        in_stock: bool = False,
        limit: int = 10,
        brand: str | None = None,
        price_operator: PriceOperator | None = None,
        price_value_vnd: int | None = None,
        diversify_by_subcategory: bool = False,
        offset: int = 0,
        sort_by: ProductSort | None = None,
    ) -> list[ProductSnapshot]:
        snapshot = self.bridge.get_snapshot()
        # Fetch a slightly wider candidate pool when live filtering may suppress OOS SKUs.
        fetch_limit = (
            max(limit + offset + 8, limit * 2) if (snapshot.is_fresh and in_stock) else limit
        )
        raw = self._base.search(
            query=query,
            category=category,
            max_price_vnd=max_price_vnd,
            spicy=spicy,
            in_stock=False if (snapshot.is_fresh and in_stock) else in_stock,
            limit=fetch_limit,
            brand=brand,
            price_operator=price_operator,
            price_value_vnd=price_value_vnd,
            diversify_by_subcategory=diversify_by_subcategory,
            offset=0 if (snapshot.is_fresh and in_stock) else offset,
            sort_by=sort_by,
        )
        overlaid = [self._overlay_product(p, snapshot) for p in raw]
        if snapshot.is_fresh and in_stock:
            overlaid = [
                p
                for p in overlaid
                if not (p.stock_is_current and p.stock <= 0)
            ]
            if max_price_vnd is not None:
                overlaid = [p for p in overlaid if p.price_vnd <= max_price_vnd]
            return overlaid[offset : offset + limit]
        return overlaid

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        base_item = self._base.get_product_snapshot(product_id)
        if base_item is None:
            return None
        return self._overlay_product(base_item, self.bridge.get_snapshot())

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]:
        snapshot = self.bridge.get_snapshot()
        approved_promos: list[ProductSnapshot] = []
        seen_ids: set[str] = set()
        if snapshot.is_fresh:
            for pid, live in snapshot.items_by_product_id.items():
                if live.approved_markdown_price_vnd is None or live.on_hand <= 0:
                    continue
                base_item = self._base.get_product_snapshot(pid)
                if base_item is None:
                    continue
                if category:
                    cat_norm = category.casefold().strip()
                    haystack = (
                        f"{base_item.category} "
                        f"{base_item.category_hierarchy or ''} "
                        f"{base_item.name}"
                    ).casefold()
                    if cat_norm not in haystack:
                        continue
                overlaid = self._overlay_product(base_item, snapshot)
                approved_promos.append(overlaid)
                seen_ids.add(pid)

        base_promos = [
            self._overlay_product(p, snapshot)
            for p in self._base.list_promotions(category=category, limit=limit + len(seen_ids))
            if p.product_id not in seen_ids
        ]
        # Suppress live out-of-stock SKUs from active promotions list.
        if snapshot.is_fresh:
            base_promos = [p for p in base_promos if not (p.stock_is_current and p.stock <= 0)]
        return (approved_promos + base_promos)[: max(1, limit)]

    def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
        return self._base.list_category_hierarchies()

    def list_speech_terms(self, limit: int = 32) -> tuple[str, ...]:
        return self._base.list_speech_terms(limit=limit)

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
        return self._base.list_brand_names(limit=limit)

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions:
        return self._base.list_catalog_additions(category=category, limit=limit)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)
