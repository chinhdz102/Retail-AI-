from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import UTC, datetime

import asyncpg

from backend.products.catalog import QUERY_STOP_WORDS, PriceOperator, ProductSnapshot
from backend.products.planogram import recommended_shelf
from backend.products.repository import CatalogAdditions


def normalize_search_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return " ".join(
        "".join(char for char in word if unicodedata.category(char) != "Mn")
        for word in decomposed.split()
    )


def _escape_like_literal(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _normalize_product_query(raw_query: str) -> tuple[str, list[str], bool]:
    normalized = normalize_search_text(raw_query).strip()
    tokens = [
        token
        for token in re.split(r"\W+", normalized)
        if token and token not in QUERY_STOP_WORDS
    ]
    # "bim bim" is common Vietnamese slang for snacks. Accent folding otherwise
    # makes it collide with "bỉm" (diapers), since both normalize to "bim".
    explicit_diaper = "bỉm" in raw_query.casefold()
    if not explicit_diaper:
        expanded_tokens: list[str] = []
        for token in tokens:
            expanded_tokens.extend(["bim", "bim"] if token == "bimbim" else [token])
        tokens = expanded_tokens
    is_snack_phrase = not explicit_diaper and any(
        tokens[index : index + 2] == ["bim", "bim"]
        for index in range(max(0, len(tokens) - 1))
    )
    return " ".join(tokens), tokens, is_snack_phrase


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
            raw_query = str(criteria.get("query", "")).strip()
            query, tokens, is_snack_phrase = _normalize_product_query(raw_query)
            category = normalize_search_text(criteria.get("category"))
            brand = normalize_search_text(criteria.get("brand"))
            where: list[str] = [
                "p.active IS TRUE",
                "s.price_quality_status IN ('NORMAL', 'SALE')",
            ]
            args: list[object] = []

            def bind(value: object) -> str:
                args.append(value)
                return f"${len(args)}"

            lexical_tokens: list[str] = []
            exact_identifier = "false"

            def contains(column: str, token: str) -> str:
                pattern = bind(f"%{_escape_like_literal(token)}%")
                return f"{column} ILIKE {pattern} ESCAPE '!'"

            if is_snack_phrase:
                raw_exact = bind(raw_query)
                exact_identifier = (
                    f"upper(p.product_id) = upper({raw_exact}) OR "
                    f"upper(p.item_no) = upper({raw_exact}) OR "
                    f"upper(p.barcode) = upper({raw_exact})"
                )
                phrase_index = next(
                    index
                    for index in range(len(tokens) - 1)
                    if tokens[index : index + 2] == ["bim", "bim"]
                )
                phrase_match = contains("p.search_title", "bim bim")
                extra_title_matches = [
                    contains("p.search_title", token)
                    for index, token in enumerate(tokens)
                    if index not in {phrase_index, phrase_index + 1}
                ]
                title_phrase_match = " AND ".join([phrase_match, *extra_title_matches])
                snack_category_match = contains("p.search_category", "snack")
                category_phrase_match = " AND ".join(
                    [snack_category_match, *extra_title_matches]
                )
                snack_search_match = (
                    f"(({title_phrase_match}) OR ({category_phrase_match}))"
                )
                where.append(f"(({exact_identifier}) OR {snack_search_match})")
                lexical_tokens.append(title_phrase_match)
                fuzzy_tokens: list[str] = []
            elif tokens:
                raw_exact = bind(raw_query)
                exact_identifier = (
                    f"upper(p.product_id) = upper({raw_exact}) OR "
                    f"upper(p.item_no) = upper({raw_exact}) OR "
                    f"upper(p.barcode) = upper({raw_exact})"
                )
                for token in tokens:
                    lexical_match = " OR ".join(
                        contains(column, token)
                        for column in ("p.search_name", "p.search_category", "p.search_brand")
                    )
                    lexical_tokens.append(f"({lexical_match})")
                query_match = " AND ".join(lexical_tokens)
                fuzzy_tokens = [token for token in tokens if len(token) >= 4]
                if fuzzy_tokens:
                    fuzzy_parts: list[str] = []
                    for token, lexical_match in zip(tokens, lexical_tokens, strict=True):
                        if len(token) < 4:
                            fuzzy_parts.append(lexical_match)
                            continue
                        placeholder = bind(token)
                        fuzzy_parts.append(
                            f"({lexical_match} OR {placeholder} <% p.search_title OR "
                            f"{placeholder} <% p.search_brand OR "
                            f"{placeholder} <% p.search_category)"
                        )
                    fuzzy_match = " AND ".join(fuzzy_parts)
                    where.append(
                        f"(({exact_identifier}) OR ({query_match}) OR ({fuzzy_match}))"
                    )
                else:
                    fuzzy_tokens = []
                    where.append(f"(({exact_identifier}) OR ({query_match}))")
            else:
                fuzzy_tokens = []
                if raw_query:
                    where.append("false")
            if "bỉm" in raw_query.casefold():
                # Preserve the explicit diaper meaning after accent folding turns
                # both "bỉm" and the snack word "bim" into the same token.
                where.append(contains("p.search_category", "bim"))
            if category:
                for token in category.split():
                    placeholder = bind(f"%{_escape_like_literal(token)}%")
                    where.append(f"p.search_category ILIKE {placeholder} ESCAPE '!'")
            if brand:
                for token in brand.split():
                    placeholder = bind(f"%{_escape_like_literal(token)}%")
                    where.append(f"p.search_brand ILIKE {placeholder} ESCAPE '!'")
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
            prefix = bind(f"{_escape_like_literal(query)}%") if query else "NULL"
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
                       s.price_quality_status, s.dataset_version
                FROM products p
                JOIN latest s ON s.product_id = p.product_id
                LEFT JOIN categories c ON c.category_code = p.category_code
                WHERE {" AND ".join(where)}
                ORDER BY
                    CASE
                        WHEN {exact_identifier} THEN 0
                        WHEN p.search_title = {exact} THEN 1
                        WHEN p.search_title LIKE {prefix} ESCAPE '!' THEN 2
                        WHEN {" AND ".join(lexical_tokens) if lexical_tokens else "false"} THEN 3
                        ELSE 4
                    END,
                    CASE WHEN {exact} IS NULL THEN 0
                         ELSE word_similarity({exact}, p.search_title) END DESC,
                    CASE WHEN {exact} IS NULL THEN 0
                         ELSE similarity(p.search_title, {exact}) END DESC,
                    p.name
                LIMIT {limit}
            """
            async with conn.transaction():
                if fuzzy_tokens:
                    await conn.execute(
                        "SELECT set_config('pg_trgm.word_similarity_threshold', '0.12', true)"
                    )
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
                       s.price_quality_status, s.dataset_version
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

    async def _list_promotions(
        self, category: str | None, limit: int
    ) -> list[ProductSnapshot]:
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            args: list[object] = [max(1, min(int(limit), 10))]
            category_filter = ""
            if category:
                args.append(f"%{_escape_like_literal(category.strip())}%")
                category_filter = (
                    "AND (c.category_name ILIKE $2 ESCAPE '!' "
                    "OR c.category_hierarchy ILIKE $2 ESCAPE '!')"
                )
            rows = await conn.fetch(
                f"""
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
                       s.price_quality_status, s.dataset_version
                FROM products p
                JOIN latest s ON s.product_id = p.product_id
                LEFT JOIN categories c ON c.category_code = p.category_code
                WHERE p.active IS TRUE
                  AND s.discount_promotion IS NOT NULL
                  AND btrim(s.discount_promotion) <> ''
                  {category_filter}
                ORDER BY s.source_timestamp DESC, p.name
                LIMIT $1
                """,
                *args,
            )
            return [self._to_snapshot(row) for row in rows]
        finally:
            await conn.close()

    def list_promotions(
        self, category: str | None = None, limit: int = 5
    ) -> list[ProductSnapshot]:
        return self._run(self._list_promotions(category, limit))

    async def _list_catalog_additions(
        self, category: str | None, limit: int
    ) -> CatalogAdditions:
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            versions = await conn.fetch(
                """
                SELECT dataset_version, imported_at
                FROM catalog_import_runs
                WHERE store_code = 'WINMART_WEB'
                  AND is_complete IS TRUE
                ORDER BY imported_at DESC, dataset_version DESC
                LIMIT 2
                """
            )
            if len(versions) < 2:
                return CatalogAdditions(
                    comparable=False,
                    current_imported_at=versions[0]["imported_at"] if versions else None,
                )

            current_version, previous_version = versions
            args: list[object] = [
                current_version["dataset_version"],
                previous_version["dataset_version"],
                max(1, min(int(limit), 10)),
            ]
            category_filter = ""
            if category:
                args.append(f"%{_escape_like_literal(category.strip())}%")
                placeholder = f"${len(args)}"
                category_filter = (
                    f"AND (c.category_name ILIKE {placeholder} ESCAPE '!' "
                    f"OR c.category_hierarchy ILIKE {placeholder} ESCAPE '!')"
                )
            rows = await conn.fetch(
                f"""
                SELECT p.product_id, p.item_no, p.barcode, p.name, p.brand,
                       p.category_code, c.category_name, p.department,
                       p.sub_department, c.category_hierarchy, p.uom,
                       p.image_url, p.description, p.seo_url, p.spicy,
                       s.effective_price_vnd, s.regular_price_vnd, s.sale_price_vnd,
                       s.physical_stock, s.discount_promotion, s.source_timestamp,
                       s.price_quality_status, s.dataset_version
                FROM store_product_snapshots s
                JOIN products p ON p.product_id = s.product_id
                LEFT JOIN categories c ON c.category_code = p.category_code
                WHERE s.store_code = 'WINMART_WEB'
                  AND s.dataset_version = $1
                  AND NOT EXISTS (
                      SELECT 1 FROM store_product_snapshots prior
                      WHERE prior.store_code = s.store_code
                        AND prior.dataset_version = $2
                        AND prior.product_id = s.product_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM catalog_quarantine_records prior_quarantine
                      WHERE prior_quarantine.dataset_version = $2
                        AND prior_quarantine.product_id = s.product_id
                  )
                  {category_filter}
                ORDER BY s.source_timestamp DESC, p.name
                LIMIT $3
                """,
                *args,
            )
            return CatalogAdditions(
                comparable=True,
                products=tuple(self._to_snapshot(row) for row in rows),
                current_imported_at=current_version["imported_at"],
                previous_imported_at=previous_version["imported_at"],
            )
        finally:
            await conn.close()

    def list_catalog_additions(
        self, category: str | None = None, limit: int = 5
    ) -> CatalogAdditions:
        return self._run(self._list_catalog_additions(category, limit))

    @staticmethod
    def _to_snapshot(row) -> ProductSnapshot:
        timestamp = row["source_timestamp"] or datetime.now(UTC)
        shelf = recommended_shelf(row["product_id"], row["dataset_version"])
        return ProductSnapshot(
            product_id=row["product_id"],
            name=row["name"],
            category=row["category_name"] or "",
            price_vnd=row["effective_price_vnd"],
            stock=row["physical_stock"],
            shelf=shelf,
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
            shelf_source="RECOMMENDED_PLANOGRAM" if shelf else "SIMULATED",
        )
