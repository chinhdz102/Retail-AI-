from __future__ import annotations

from typing import Protocol

from backend.config import Settings, get_settings
from backend.products.catalog import (
    PriceOperator,
    ProductSnapshot,
    get_product_snapshot,
    search_products,
)


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


class DemoProductRepository:
    source_name = "demo_product_catalog"

    def search(self, **criteria) -> list[ProductSnapshot]:
        return search_products(**criteria)

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return get_product_snapshot(product_id)


def get_product_repository(settings: Settings | None = None) -> ProductRepository:
    selected = settings or get_settings()
    if selected.catalog_repository == "demo":
        return DemoProductRepository()
    if selected.catalog_repository == "postgres":
        from backend.products.postgres_repository import PostgresProductRepository

        return PostgresProductRepository(selected.database_url)
    raise ValueError(f"Unknown catalog repository: {selected.catalog_repository}")
