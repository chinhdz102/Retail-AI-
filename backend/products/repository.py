from __future__ import annotations

from collections import Counter
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
from backend.products.category_taxonomy import (
    collect_category_hierarchies,
    diversify_by_category_leaf,
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
        diversify_by_subcategory: bool = False,
        offset: int = 0,
    ) -> list[ProductSnapshot]: ...

    def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]: ...

    def list_speech_terms(self, limit: int = 32) -> tuple[str, ...]: ...

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]: ...

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None: ...

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]: ...

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions: ...


class DemoProductRepository:
    source_name = "demo_product_catalog"
    catalog_version = "demo-seed-v1"
    supports_current_stock = True
    supports_spicy_filter = True

    def search(self, **criteria) -> list[ProductSnapshot]:
        diversify = bool(criteria.pop("diversify_by_subcategory", False))
        offset = max(0, int(criteria.pop("offset", 0)))
        limit = max(1, int(criteria.get("limit", 10)))
        criteria["limit"] = offset + limit
        products = search_products(**criteria)
        if diversify:
            products = diversify_by_category_leaf(products, offset + limit)
        return products[offset : offset + limit]

    def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
        return collect_category_hierarchies(DEMO_PRODUCTS)

    def list_speech_terms(self, limit: int = 32) -> tuple[str, ...]:
        return catalog_speech_terms(DEMO_PRODUCTS, limit=limit)

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
        return catalog_brand_names(DEMO_PRODUCTS, limit=limit)

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


def catalog_speech_terms(
    products: tuple[ProductSnapshot, ...] | list[ProductSnapshot],
    *,
    limit: int = 32,
) -> tuple[str, ...]:
    """Return frequent brands and leaf categories for domain-aware STT spelling."""
    bounded_limit = max(4, min(int(limit), 48))
    brand_counts = Counter(
        product.brand.strip()
        for product in products
        if product.brand and product.brand.strip()
        and product.brand.strip().casefold() not in {"nobrand", "no brand"}
    )
    category_counts = Counter(
        (product.category_hierarchy or product.category).split(">")[-1].strip()
        for product in products
        if (product.category_hierarchy or product.category).strip()
    )
    # Whisper has a small prompt budget. Favor recognizable catalog brands, then
    # reserve the remaining slots for category words. The previous one-brand-per-
    # category sampler spent many slots on repeated high-frequency brands and
    # omitted common spoken names such as Oishi.
    brand_limit = max(1, (bounded_limit * 3) // 4)
    brands = [term for term, _ in brand_counts.most_common(brand_limit)]
    categories = [term for term, _ in category_counts.most_common(bounded_limit - len(brands))]
    return tuple(dict.fromkeys((*brands, *categories)))


def catalog_brand_names(
    products: tuple[ProductSnapshot, ...] | list[ProductSnapshot],
    *,
    limit: int = 300,
) -> tuple[str, ...]:
    bounded_limit = max(1, min(int(limit), 1000))
    counts = Counter(
        product.brand.strip()
        for product in products
        if product.brand and product.brand.strip()
        and product.brand.strip().casefold() not in {"nobrand", "no brand"}
    )
    return tuple(brand for brand, _ in counts.most_common(bounded_limit))


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
