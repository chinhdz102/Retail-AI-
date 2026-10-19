from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from backend.config import Settings, get_settings
from backend.products.catalog import (
    DEMO_PRODUCTS,
    PriceOperator,
    ProductSnapshot,
    get_product_snapshot,
    search_products,
)


@dataclass(frozen=True)
class CatalogAdditions:
    comparable: bool
    products: tuple[ProductSnapshot, ...] = ()
    current_imported_at: datetime | None = None
    previous_imported_at: datetime | None = None


class ProductRepository(Protocol):
    source_name: str

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
    ) -> list[ProductSnapshot]: ...

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None: ...

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]: ...

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions: ...


class DemoProductRepository:
    source_name = "demo_product_catalog"
    supports_current_stock = True
    supports_spicy_filter = True

    def search(self, **criteria) -> list[ProductSnapshot]:
        return search_products(**criteria)

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return get_product_snapshot(product_id)

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]:
        products = [product for product in DEMO_PRODUCTS if product.promotion]
        if category:
            normalized_category = category.casefold().strip()
            products = [
                product
                for product in products
                if normalized_category in product.category.casefold()
                or normalized_category in (product.category_hierarchy or "").casefold()
            ]
        return products[: max(1, min(limit, 10))]

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions:
        # The demo catalog has no prior complete import to compare against.
        return CatalogAdditions(comparable=False)


def get_product_repository(settings: Settings | None = None) -> ProductRepository:
    selected = settings or get_settings()
    if selected.catalog_repository == "demo":
        return DemoProductRepository()
    if selected.catalog_repository == "postgres":
        from backend.products.postgres_repository import PostgresProductRepository

        return PostgresProductRepository(selected.database_url)
    if selected.catalog_repository == "snapshot":
        snapshot_path = Path(selected.catalog_snapshot_path)
        if not snapshot_path.is_absolute():
            snapshot_path = Path(__file__).resolve().parents[2] / snapshot_path
        stat = snapshot_path.stat()
        return _snapshot_repository(snapshot_path.resolve(), stat.st_mtime_ns, stat.st_size)
    raise ValueError(f"Unknown catalog repository: {selected.catalog_repository}")


@lru_cache(maxsize=4)
def _snapshot_repository(path: Path, _mtime_ns: int, _size: int) -> ProductRepository:
    from backend.products.snapshot_repository import SnapshotProductRepository

    return SnapshotProductRepository(path)
