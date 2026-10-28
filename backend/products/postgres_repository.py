from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import UTC, datetime

import asyncpg

from backend.products.catalog import QUERY_STOP_WORDS, PriceOperator, ProductSnapshot
from backend.products.category_taxonomy import split_category_hierarchy
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
    supports_current_stock = False
    supports_spicy_filter = False

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
                category_column = "COALESCE(NULLIF(c.category_hierarchy, ''), p.search_category)"
                category_terms = {
                    "haircare": ("chăm sóc tóc", "dầu gội", "dầu xả"),
                    "oral_care": (
                        "chăm sóc răng miệng",
                        "kem đánh răng",
                        "bàn chải đánh răng",
                        "nước súc miệng",
                        "chỉ nha khoa",
                    ),
                }.get(category)
                if category_terms:
                    category_matches = [
                        contains(category_column, term) for term in category_terms
                    ]
                    where.append("(" + " OR ".join(category_matches) + ")")
                else:
                    placeholder = bind(f"%{_escape_like_literal(category.strip())}%")
                    where.append(
                        category_column + f" ILIKE {placeholder} ESCAPE '!'")
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
            diversify = bind(bool(criteria.get("diversify_by_subcategory", False)))
            limit = bind(max(1, min(int(criteria.get("limit", 10)), 20)))
            sql = f"""
                WITH latest AS (
                    SELECT DISTINCT ON (product_id) *
                    FROM store_product_snapshots
                    WHERE store_code = 'WINMART_WEB'
                    ORDER BY product_id, source_timestamp DESC, ingested_at DESC
                ), matched AS (
                    SELECT p.product_id, p.item_no, p.barcode, p.name, p.brand,
                           p.category_code, c.category_name, p.department,
                           p.sub_department, c.category_hierarchy, p.uom,
                           p.image_url, p.description, p.seo_url, p.spicy,
                           s.effective_price_vnd, s.regular_price_vnd, s.sale_price_vnd,
                           s.physical_stock, s.discount_promotion, s.source_timestamp,
                           s.price_quality_status, s.dataset_version,
                           p.search_title, p.search_brand, p.search_category,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(
                                   NULLIF(regexp_replace(
                                       c.category_hierarchy,
                                       '^.*>[[:space:]]*',
                                       ''
                                   ), ''),
                                   c.category_name,
                                   p.category_code,
                                   p.product_id
                               )
                               ORDER BY s.effective_price_vnd, p.name
                           ) AS category_leaf_rank
                    FROM products p
                    JOIN latest s ON s.product_id = p.product_id
                    LEFT JOIN categories c ON c.category_code = p.category_code
                    WHERE {" AND ".join(where)}
                )
                SELECT p.product_id, p.item_no, p.barcode, p.name, p.brand,
                       p.category_code, p.category_name, p.department,
                       p.sub_department, p.category_hierarchy, p.uom,
                       p.image_url, p.description, p.seo_url, p.spicy,
                       p.effective_price_vnd, p.regular_price_vnd, p.sale_price_vnd,
                       p.physical_stock, p.discount_promotion, p.source_timestamp,
                       p.price_quality_status, p.dataset_version
                FROM matched p
                WHERE (NOT {diversify} OR p.category_leaf_rank = 1)
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
        diversify_by_subcategory: bool = False,
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
                diversify_by_subcategory=diversify_by_subcategory,
            )
        )

    async def _catalog_terms_evidenced(
        self,
        terms: tuple[str, ...] | list[str],
        category: str,
    ) -> frozenset[str]:
        requested = tuple(
            dict.fromkeys(
                token
                for term in terms
                for token in normalize_search_text(term).split()
                if token
            )
        )
        if not requested:
            return frozenset()

        args: list[object] = [list(requested)]
        where = ["p.active IS TRUE"]
        normalized_category = normalize_search_text(category).strip()
        if normalized_category:
            category_terms = {
                "haircare": ("chăm sóc tóc", "dầu gội", "dầu xả"),
                "oral_care": (
                    "chăm sóc răng miệng",
                    "kem đánh răng",
                    "bàn chải đánh răng",
                    "nước súc miệng",
                    "chỉ nha khoa",
                ),
            }.get(normalized_category, (normalized_category,))
            category_matches = []
            for category_term in category_terms:
                args.append(f"%{_escape_like_literal(normalize_search_text(category_term))}%")
                category_matches.append(
                    f"p.search_category ILIKE ${len(args)} ESCAPE '!'"
                )
            where.append("(" + " OR ".join(category_matches) + ")")

        # `search_name` also contains identifiers and taxonomy; tokenize the
        # three shopper-facing evidence fields so SKU codes and category words
        # cannot count as product-attribute evidence.
        normalized_evidence = "lower(unaccent(replace(replace("
        normalized_evidence += (
            "concat_ws(' ', p.name, p.brand, p.description), 'Đ', 'D'), 'đ', 'd'))))"
        )
        sql = f"""
            WITH requested AS (
                SELECT unnest($1::text[]) AS token
            )
            SELECT DISTINCT requested.token
            FROM requested
            WHERE EXISTS (
                SELECT 1
                FROM products p
                WHERE {' AND '.join(where)}
                  AND requested.token = ANY(
                      regexp_split_to_array({normalized_evidence}, '[^a-z0-9]+')
                  )
            )
        """
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            rows = await conn.fetch(sql, *args)
            return frozenset(str(row["token"]) for row in rows)
        finally:
            await conn.close()

    def catalog_terms_evidenced(
        self,
        terms: tuple[str, ...] | list[str],
        category: str,
    ) -> frozenset[str]:
        return self._run(self._catalog_terms_evidenced(terms, category))

    async def _list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            rows = await conn.fetch(
                """
                SELECT DISTINCT c.category_hierarchy, c.category_name
                FROM products p
                LEFT JOIN categories c ON c.category_code = p.category_code
                WHERE p.active IS TRUE
                ORDER BY c.category_hierarchy NULLS LAST, c.category_name
                """
            )
            paths = {
                split_category_hierarchy(row["category_hierarchy"])
                or ((row["category_name"] or "").strip(),)
                for row in rows
            }
            return tuple(sorted((path for path in paths if path), key=lambda item: item))
        finally:
            await conn.close()

    def list_category_hierarchies(self) -> tuple[tuple[str, ...], ...]:
        return self._run(self._list_category_hierarchies())

    async def _list_speech_terms(self, limit: int) -> tuple[str, ...]:
        bounded_limit = max(4, min(int(limit), 48))
        brand_limit = max(1, (bounded_limit * 3) // 4)
        category_limit = max(1, bounded_limit - brand_limit)
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            brands = await conn.fetch(
                """
                SELECT trim(p.brand) AS term, count(*) AS frequency
                FROM products p
                WHERE p.active IS TRUE
                  AND p.brand IS NOT NULL
                  AND btrim(p.brand) <> ''
                  AND upper(btrim(p.brand)) NOT IN ('NOBRAND', 'NO BRAND')
                GROUP BY trim(p.brand)
                ORDER BY frequency DESC, term
                LIMIT $1
                """,
                brand_limit,
            )
            categories = await conn.fetch(
                """
                SELECT trim(c.category_name) AS term, count(*) AS frequency
                FROM products p
                JOIN categories c ON c.category_code = p.category_code
                WHERE p.active IS TRUE
                  AND c.category_name IS NOT NULL
                  AND btrim(c.category_name) <> ''
                GROUP BY trim(c.category_name)
                ORDER BY frequency DESC, term
                LIMIT $1
                """,
                category_limit,
            )
            return tuple(dict.fromkeys(
                [row["term"] for row in brands] + [row["term"] for row in categories]
            ))
        finally:
            await conn.close()

    def list_speech_terms(self, limit: int = 32) -> tuple[str, ...]:
        return self._run(self._list_speech_terms(limit))

    async def _list_brand_names(self, limit: int) -> tuple[str, ...]:
        bounded_limit = max(1, min(int(limit), 1000))
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            rows = await conn.fetch(
                """
                SELECT trim(p.brand) AS term, count(*) AS frequency
                FROM products p
                WHERE p.active IS TRUE
                  AND p.brand IS NOT NULL
                  AND btrim(p.brand) <> ''
                  AND upper(btrim(p.brand)) NOT IN ('NOBRAND', 'NO BRAND')
                GROUP BY trim(p.brand)
                ORDER BY frequency DESC, term
                LIMIT $1
                """,
                bounded_limit,
            )
            return tuple(row["term"] for row in rows)
        finally:
            await conn.close()

    def list_brand_names(self, limit: int = 300) -> tuple[str, ...]:
        return self._run(self._list_brand_names(limit))

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
            stock_is_current=False,
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
