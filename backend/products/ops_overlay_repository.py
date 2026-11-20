"""Overlay Ops Chest data (stock, lot expiry, near-expiry markdowns) on the WinMart snapshot.

The bridge (separate repository) writes an `ops-feed` v1 JSON file for the products it maps
from the store's inventory system. This repository wraps `SnapshotProductRepository` and patches
only those products, so the WinMart JSON file stays byte-identical and the planogram keeps
matching its hash (shelves survive). Unmapped products keep their snapshot values.

The feed is re-read whenever its mtime or size changes. A malformed feed (bad JSON, wrong
contract or version) is logged and the last valid feed stays in use. A stale feed is still
applied: `data_timestamp` exposes its age and lots past their expiry stop counting as stock.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

from backend.products.catalog import ProductSnapshot, search_products
from backend.products.repository import CatalogAdditions

logger = logging.getLogger(__name__)

FEED_CONTRACT = "ops-feed"
FEED_VERSION = 1


@dataclass(frozen=True)
class _State:
    signature: tuple | None
    products: tuple[ProductSnapshot, ...]
    by_id: dict[str, ProductSnapshot]
    mapped_ids: frozenset[str]
    promoted: tuple[ProductSnapshot, ...]
    published_at: datetime | None = None


def _now(tz) -> datetime:
    return datetime.now(tz)


def _today(published_at: datetime) -> date:
    """Today on the store's clock. Feed dates are local store dates and `published_at` carries the
    store's UTC offset; the UTC date would keep yesterday's lots sellable until 07:00 in Vietnam."""
    return _now(published_at.tzinfo or UTC).date()


def _overlay(
    product: ProductSnapshot, item: dict, published_at: datetime, today: date
) -> ProductSnapshot:
    unit = int(item["unit_price_vnd"])
    sale = int(item["sale_price_vnd"])
    promotion = item.get("promotion_text")
    stock = max(0, int(item["sellable_qty"]))
    expiry = item.get("earliest_expiry")
    lots = item.get("lots") or []
    if item.get("status") == "INACTIVE":  # delisted: never advertise stock or markdowns
        stock, expiry, lots, promotion, sale = 0, None, [], None, unit
    expired = [lot for lot in lots if date.fromisoformat(lot["expiry"]) < today]
    if expired:  # stale feed: lots past expiry are no longer sellable
        live = [lot for lot in lots if lot not in expired]
        stock = max(0, stock - sum(int(lot["qty"]) for lot in expired))
        expiry = min((lot["expiry"] for lot in live), default=None)
        if not any(float(lot["markdown_pct"]) > 0 for lot in live):
            promotion, sale = None, unit
    if expiry is not None:
        date.fromisoformat(expiry)  # validate
    return product.model_copy(update={
        "stock": stock,
        "expiry_at": expiry,
        # Same convention as SnapshotProductRepository: price_vnd is the price a shopper pays now
        # (the marked-down price when a markdown is live), list_price_vnd the regular price.
        "price_vnd": min(unit, sale),
        "list_price_vnd": unit,
        "sale_price_vnd": sale,
        "promotion": promotion,
        "price_quality_status": "SALE" if sale < unit else "NORMAL",
        "data_timestamp": published_at,
    })


class OpsOverlayRepository:
    """Snapshot repository with the Ops Chest feed merged into mapped products."""

    def __init__(self, base, feed_path: Path) -> None:
        self._base = base
        self.feed_path = Path(feed_path)
        # Keep the snapshot identity: category aliases and cart behaviour key off source_name.
        self.source_name = base.source_name
        self.category_aliases = base.category_aliases
        self._lock = threading.Lock()
        self._feed: dict | None = None  # last feed whose header parsed
        self._good_feed: dict | None = None  # last feed whose items merged
        self._feed_signature: tuple | str | None = None
        self._state = _State(None, base.products, base._by_id, frozenset(), ())

    def __getattr__(self, name: str):
        # dataset_sha256, rows_quarantined, ... come from the wrapped snapshot.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._base, name)

    def _read_feed(self, signature: tuple) -> None:
        if signature == self._feed_signature:
            return
        self._feed_signature = signature
        try:
            feed = json.loads(self.feed_path.read_text(encoding="utf-8"))
            if not isinstance(feed, dict) or feed.get("contract") != FEED_CONTRACT \
                    or feed.get("version") != FEED_VERSION:
                raise ValueError("unsupported contract or version")
            if not isinstance(feed.get("items"), list):
                raise ValueError("items must be a list")
            datetime.fromisoformat(feed["published_at"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Ignoring Ops feed %s (%s); keeping the last valid feed", self.feed_path, exc
            )
            return
        self._feed = feed

    def _build(self, feed: dict, today: date, signature: tuple) -> _State:
        published_at = datetime.fromisoformat(feed["published_at"])
        base_by_id = self._base._by_id
        patched: dict[str, ProductSnapshot] = {}
        for item in feed["items"]:
            key = str(item["product_id"]).strip().casefold()
            product = base_by_id.get(key)
            if product is not None:
                patched[key] = _overlay(product, item, published_at, today)
        products = tuple(patched.get(p.product_id.casefold(), p) for p in self._base.products)
        by_id = {p.product_id.casefold(): p for p in products}
        # Near-expiry markdowns first: deepest discount, then earliest expiry.
        def discount(p: ProductSnapshot) -> float:
            regular = max(p.list_price_vnd or p.price_vnd, 1)
            return 1 - (p.sale_price_vnd or p.price_vnd) / regular

        promoted = tuple(sorted(
            (p for p in patched.values() if p.promotion),
            key=lambda p: (-discount(p), p.expiry_at or "9999-12-31", p.product_id),
        ))
        return _State(signature, products, by_id, frozenset(p.product_id for p in patched.values()),
                      promoted, published_at)

    def _current(self) -> _State:
        try:
            stat = self.feed_path.stat()
            file_signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            file_signature = None
        with self._lock:
            if file_signature is not None:
                self._read_feed(file_signature)
            elif self._good_feed is None and self._feed_signature != "missing":
                self._feed_signature = "missing"
                logger.warning("Ops feed %s not found; serving the plain snapshot", self.feed_path)
            for feed in (self._feed, self._good_feed):
                if feed is None:
                    continue
                today = _today(datetime.fromisoformat(feed["published_at"]))
                signature = (id(feed), today)
                if self._state.signature == signature:
                    break
                try:
                    self._state = self._build(feed, today, signature)
                    self._good_feed = self._feed = feed
                    break
                except (ValueError, KeyError, TypeError) as exc:
                    logger.warning(
                        "Ops feed %s has invalid items (%s); keeping the last valid feed",
                        self.feed_path, exc,
                    )
                    self._feed = self._good_feed
            return self._state

    @property
    def products(self) -> tuple[ProductSnapshot, ...]:
        return self._current().products

    @property
    def ops_feed_published_at(self) -> datetime | None:
        return self._current().published_at

    def search(self, **criteria) -> list[ProductSnapshot]:
        state = self._current()
        category = criteria.get("category")
        if category:
            criteria["category"] = self.category_aliases.get(category.casefold().strip(), category)
        return search_products(products=state.products, **criteria)

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return self._current().by_id.get(product_id.strip().casefold())

    def list_promotions(self, category: str | None = None, limit: int = 5) -> list[ProductSnapshot]:
        state = self._current()
        limit = max(1, min(limit, 10))
        ops = search_products(
            products=state.promoted, category=category, in_stock=True, limit=limit
        )
        crawled = tuple(
            p for p in state.products if p.promotion and p.product_id not in state.mapped_ids
        )
        rest = search_products(products=crawled, category=category, in_stock=False, limit=limit)
        return (ops + rest)[:limit]

    def list_catalog_additions(self, **criteria) -> CatalogAdditions:
        return self._base.list_catalog_additions(**criteria)


@lru_cache(maxsize=1)
def ops_overlay_repository(base, feed_path: str) -> OpsOverlayRepository:
    path = Path(feed_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return OpsOverlayRepository(base, path.resolve())
