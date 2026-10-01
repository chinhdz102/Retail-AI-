from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import UTC, datetime

import asyncpg

from backend.products.catalog import PriceOperator, ProductSnapshot


def normalize_search_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return " ".join(
        "".join(char for char in word if unicodedata.category(char) != "Mn")
        for word in decomposed.split()
    )


class PostgresProductRepository:
    source_name = "winmart_postgres_catalog"

    def __init__(self, database_url: str) -> None:
        self._dsn = re.sub(r"^postgresql\+asyncpg://", "postgresql://", database_url)

    def _run(self, coroutine):
        # Catalog calls already run in a worker thread at the agent boundary.
        return asyncio.run(coroutine)

    async def _search(self, **criteria) -> list[ProductSnapshot]:
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            query = normalize_search_text(criteria.get("query", ""))
            category = normalize_search_text(criteria.get("category"))
            brand = normalize_search_text(criteria.get("brand"))
            where: list[str] = ["s.price_quality_status IN ('NORMAL', 'SALE')"]
            args: list[object] = []

            def bind(value: object) -> str:
                args.append(value)
                return f"${len(args)}"

            tokens = query.split()
            for token in tokens:
                placeholder = bind(f"%{token}%")
                where.append(
                    f"(p.search_name ILIKE {placeholder} OR "
                    f"p.search_category ILIKE {placeholder} OR "
                    f"p.search_brand ILIKE {placeholder})"
                )
            if category:
                for token in category.split():
                    placeholder = bind(f"%{token}%")
                    where.append(f"p.search_category ILIKE {placeholder}")
            if brand:
                for token in brand.split():
                    placeholder = bind(f"%{token}%")
                    where.append(f"p.search_brand ILIKE {placeholder}")
            if criteria.get("max_price_vnd") is not None:
                where.append(f"s.effective_price_vnd <= {bind(criteria['max_price_vnd'])}")
            operator: PriceOperator | None = criteria.get("price_operator")
            price_value = criteria.get("price_value_vnd")
            if operator is not None and price_value is not None:
                sql_operator = {
                    "LT": "<",
                    "LTE": "<=",
                    "GT": ">",
                    "GTE": ">=",
                    "EQ": "=",
                }[operator]
                where.append(f"s.effective_price_vnd {sql_operator} {bind(price_value)}")
            if criteria.get("spicy") is not None:
                where.append(f"p.spicy IS NOT DISTINCT FROM {bind(criteria['spicy'])}")
            if criteria.get("in_stock"):
                where.append("s.physical_stock > 0")

            exact = bind(query) if query else "NULL"
            prefix = bind(f"{query}%") if query else "NULL"
            limit = bind(max(1, min(int(criteria.get("limit", 10)), 20)))
            sql = f"""
                WITH latest AS (
                    SELECT DISTINCT ON (product_id) *
                    FROM store_product_snapshots
                    WHERE store_code = 'WINMART_WEB'
                    ORDER BY product_id, source_timestamp DESC, ingested_at DESC
                )
                SELECT p.product_id, p.item_no, p.barcode, p.name, p.brand,
                       p.category_code, c.category_name, p.department,
                       p.sub_department, c.category_hierarchy, p.uom,
                       p.image_url, p.description, p.seo_url, p.spicy,
                       s.effective_price_vnd, s.regular_price_vnd, s.sale_price_vnd,
                       s.physical_stock, s.discount_promotion, s.source_timestamp,
                       s.price_quality_status
                FROM products p
                JOIN latest s ON s.product_id = p.product_id
                LEFT JOIN categories c ON c.category_code = p.category_code
                WHERE {" AND ".join(where)}
                ORDER BY
                    CASE
                        WHEN upper(p.product_id) = upper({exact})
                             OR p.item_no = {exact}
                             OR p.barcode = {exact} THEN 0
                        WHEN lower(p.name) = {exact} THEN 1
                        WHEN p.search_name LIKE {prefix} THEN 2
                        ELSE 3
                    END,
                    CASE WHEN {exact} IS NULL THEN 0
                         ELSE similarity(p.search_name, {exact}) END DESC,
                    p.name
                LIMIT {limit}
            """
            rows = await conn.fetch(sql, *args)
            return [self._to_snapshot(row) for row in rows]
        finally:
            await conn.close()

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
    ) -> list[ProductSnapshot]:
        return self._run(
            self._search(
                query=query,
                category=category,
                max_price_vnd=max_price_vnd,
                spicy=spicy,
                in_stock=in_stock,
                limit=limit,
                brand=brand,
                price_operator=price_operator,
                price_value_vnd=price_value_vnd,
            )
        )

    async def _get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            row = await conn.fetchrow(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (product_id) * FROM store_product_snapshots
                    WHERE store_code = 'WINMART_WEB'
                    ORDER BY product_id, source_timestamp DESC, ingested_at DESC
                )
                SELECT p.product_id, p.item_no, p.barcode, p.name, p.brand,
                       p.category_code, c.category_name, p.department,
                       p.sub_department, c.category_hierarchy, p.uom,
                       p.image_url, p.description, p.seo_url, p.spicy,
                       s.effective_price_vnd, s.regular_price_vnd, s.sale_price_vnd,
                       s.physical_stock, s.discount_promotion, s.source_timestamp,
                       s.price_quality_status
                FROM products p JOIN latest s USING(product_id)
                LEFT JOIN categories c ON c.category_code = p.category_code
                WHERE upper(p.product_id) = upper($1) OR p.item_no = $1 OR p.barcode = $1
                ORDER BY CASE WHEN upper(p.product_id) = upper($1) THEN 0 ELSE 1 END
                LIMIT 1
                """,
                product_id.strip(),
            )
            return self._to_snapshot(row) if row else None
        finally:
            await conn.close()

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return self._run(self._get_product_snapshot(product_id))

    @staticmethod
    def _to_snapshot(row) -> ProductSnapshot:
        timestamp = row["source_timestamp"] or datetime.now(UTC)
        return ProductSnapshot(
            product_id=row["product_id"],
            name=row["name"],
            category=row["category_name"] or "",
            price_vnd=row["effective_price_vnd"],
            stock=row["physical_stock"],
            shelf=None,  # Source shelf values are simulated by the crawler.
            promotion=row["discount_promotion"],
            spicy=row["spicy"],
            ingredients=None,
            expiry_at=None,
            data_timestamp=timestamp,
            brand=row["brand"],
            item_no=row["item_no"],
            barcode=row["barcode"],
            category_code=row["category_code"],
            department=row["department"],
            sub_department=row["sub_department"],
            category_hierarchy=row["category_hierarchy"],
            uom=row["uom"],
            image_url=row["image_url"],
            description=row["description"],
            seo_url=row["seo_url"],
            list_price_vnd=row["regular_price_vnd"],
            sale_price_vnd=row["sale_price_vnd"],
            price_quality_status=row["price_quality_status"],
            shelf_source="SIMULATED",
        )
