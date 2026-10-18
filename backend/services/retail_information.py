from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg

RetailInformationKind = Literal["store", "promotions", "receipts", "nearby"]


@dataclass(frozen=True)
class RetailInformationResult:
    status: Literal["SUCCESS", "DATA_UNAVAILABLE", "TEMPORARY_FAILURE"]
    kind: RetailInformationKind
    store: dict[str, Any] | None = None
    items: tuple[dict[str, Any], ...] = ()
    source: str = "retail_information_postgres"
    reason: str | None = None
    source_updated_at: datetime | None = None


class PostgresRetailInformationRepository:
    """Read branch-scoped facts with feed freshness and provenance checks."""

    source_name = "retail_information_postgres"

    def __init__(self, database_url: str) -> None:
        self._dsn = re.sub(r"^postgresql\+asyncpg://", "postgresql://", database_url)

    def _run(self, coroutine):
        return asyncio.run(coroutine)

    async def _store(self, conn: asyncpg.Connection, kiosk_id: str) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT s.store_code, s.store_name, s.address, s.phone, s.opening_hours,
                   s.timezone, s.latitude, s.longitude, s.source_name, s.source_uri,
                   s.verified_at, s.organization_code
            FROM retail_kiosks k
            JOIN retail_stores s ON s.store_code = k.store_code
            WHERE k.kiosk_id = $1
              AND k.active IS TRUE
              AND s.active IS TRUE
              AND s.verified_at >= now() - interval '180 days'
            """,
            kiosk_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def _fresh_feed(
        conn: asyncpg.Connection,
        store_code: str,
        feed_name: str,
        max_age_hours: int,
    ) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT provider, status, last_success_at, coverage_start_at,
                   coverage_end_at, coverage_complete, source_uri
            FROM retail_data_feeds
            WHERE store_code = $1 AND feed_name = $2
              AND status = 'ACTIVE'
              AND last_success_at >= now() - make_interval(hours => $3)
            """,
            store_code,
            feed_name,
            max_age_hours,
        )
        return dict(row) if row else None

    async def _lookup(self, kind: RetailInformationKind, kiosk_id: str, category: str | None):
        conn = await asyncpg.connect(self._dsn, timeout=2)
        try:
            store = await self._store(conn, kiosk_id)
            if store is None:
                return RetailInformationResult(
                    "DATA_UNAVAILABLE",
                    kind,
                    reason="No current verified branch is mapped to this kiosk.",
                )

            if kind == "store":
                return RetailInformationResult("SUCCESS", kind, store=store)

            if kind == "promotions":
                feed = await self._fresh_feed(conn, store["store_code"], "PROMOTIONS", 24)
                if feed is None:
                    return RetailInformationResult(
                        "DATA_UNAVAILABLE", kind, store=store,
                        reason="No fresh verified promotion feed is configured for this branch.",
                    )
                rows = await conn.fetch(
                    """
                    SELECT c.campaign_id, c.campaign_name, c.description, c.terms,
                           c.eligibility, c.starts_at, c.ends_at, c.source_name,
                           c.source_uri, c.verified_at,
                           COALESCE(
                               array_agg(DISTINCT p.name)
                                   FILTER (WHERE p.name IS NOT NULL),
                               '{}'
                           )
                               AS product_names
                    FROM promotion_campaigns c
                    LEFT JOIN promotion_campaign_products cp ON cp.campaign_id = c.campaign_id
                    LEFT JOIN products p ON p.product_id = cp.product_id
                    WHERE c.status = 'ACTIVE'
                      AND c.starts_at <= now() AND c.ends_at > now()
                      AND c.verified_at >= now() - interval '180 days'
                      AND (c.store_code = $1 OR
                           (c.store_code IS NULL AND c.organization_code = $2))
                      AND ($3::text IS NULL OR c.campaign_name ILIKE '%' || $3 || '%' OR
                           c.description ILIKE '%' || $3 || '%')
                    GROUP BY c.campaign_id
                    ORDER BY c.ends_at, c.campaign_name
                    LIMIT 5
                    """,
                    store["store_code"],
                    store["organization_code"],
                    category,
                )
                if not rows and (
                    not feed["coverage_complete"]
                    or feed["coverage_end_at"] is None
                    or feed["coverage_end_at"] < datetime.now(UTC)
                ):
                    return RetailInformationResult(
                        "DATA_UNAVAILABLE", kind, store=store,
                        reason="The promotion feed does not confirm complete current coverage.",
                    )
                return RetailInformationResult(
                    "SUCCESS", kind, store=store,
                    items=tuple(dict(row) for row in rows),
                    source=feed["provider"],
                    source_updated_at=feed["last_success_at"],
                )

            if kind == "receipts":
                feed = await self._fresh_feed(conn, store["store_code"], "RECEIVING", 24)
                if feed is None:
                    return RetailInformationResult(
                        "DATA_UNAVAILABLE", kind, store=store,
                        reason="No fresh verified receiving feed is configured for this branch.",
                    )
                rows = await conn.fetch(
                    """
                    SELECT r.event_id, p.name AS product_name, r.quantity, r.received_at,
                           r.receiving_step, r.source_name, r.source_document_id, r.source_uri
                    FROM inventory_receipts r
                    JOIN products p ON p.product_id = r.product_id
                    WHERE r.store_code = $1 AND r.voided_at IS NULL
                      AND r.received_at <= now()
                      AND r.received_at >= GREATEST(
                          now() - interval '7 days',
                          COALESCE($2::timestamptz, now() - interval '7 days')
                      )
                      AND r.received_at <= LEAST(
                          now(), COALESCE($3::timestamptz, now())
                      )
                      AND ($4::text IS NULL OR p.name ILIKE '%' || $4 || '%')
                    ORDER BY r.received_at DESC
                    LIMIT 5
                    """,
                    store["store_code"], feed["coverage_start_at"],
                    feed["coverage_end_at"], category,
                )
                if not rows:
                    now = datetime.now(UTC)
                    complete_seven_day_window = (
                        feed["coverage_complete"]
                        and feed["coverage_start_at"] is not None
                        and feed["coverage_start_at"] <= now - timedelta(days=7)
                        and feed["coverage_end_at"] is not None
                        and feed["coverage_end_at"] >= now
                    )
                    if not complete_seven_day_window:
                        return RetailInformationResult(
                            "DATA_UNAVAILABLE", kind, store=store,
                            reason="The receiving feed does not cover a complete recent window.",
                        )
                return RetailInformationResult(
                    "SUCCESS", kind, store=store,
                    items=tuple(dict(row) for row in rows), source=feed["provider"],
                    source_updated_at=feed["last_success_at"],
                )

            if store["latitude"] is None or store["longitude"] is None:
                return RetailInformationResult(
                    "DATA_UNAVAILABLE", kind, store=store,
                    reason="The branch has no verified coordinates.",
                )
            feed = await self._fresh_feed(conn, store["store_code"], "NEARBY_PLACES", 24 * 30)
            if feed is None:
                return RetailInformationResult(
                    "DATA_UNAVAILABLE", kind, store=store,
                    reason="No fresh attributed nearby-place snapshot is configured.",
                )
            rows = await conn.fetch(
                """
                SELECT place_name, place_category, address, distance_m, provider,
                       source_uri, attribution, captured_at, valid_until
                FROM store_nearby_places
                WHERE store_code = $1 AND valid_until > now()
                  AND captured_at >= now() - interval '30 days'
                ORDER BY distance_m NULLS LAST, place_name
                LIMIT 10
                """,
                store["store_code"],
            )
            if not rows and not feed["coverage_complete"]:
                return RetailInformationResult(
                    "DATA_UNAVAILABLE", kind, store=store,
                    reason="The nearby-place feed does not confirm complete coverage.",
                )
            return RetailInformationResult(
                "SUCCESS", kind, store=store,
                items=tuple(dict(row) for row in rows), source=feed["provider"],
                source_updated_at=feed["last_success_at"],
            )
        finally:
            await conn.close()

    def lookup(
        self,
        kind: RetailInformationKind,
        kiosk_id: str,
        category: str | None = None,
    ) -> RetailInformationResult:
        return self._run(self._lookup(kind, kiosk_id, category))


def lookup_retail_information(
    kind: RetailInformationKind,
    kiosk_id: str,
    category: str | None = None,
    *,
    database_url: str | None = None,
) -> RetailInformationResult:
    if not kiosk_id.strip():
        return RetailInformationResult(
            "DATA_UNAVAILABLE", kind, reason="A kiosk-to-branch mapping is required."
        )
    if database_url is None:
        from backend.config import get_settings

        settings = get_settings()
        if settings.catalog_repository != "postgres":
            return RetailInformationResult(
                "DATA_UNAVAILABLE", kind,
                reason="Branch information is not available in the active demo repository.",
            )
        database_url = settings.database_url
    try:
        return PostgresRetailInformationRepository(database_url).lookup(kind, kiosk_id, category)
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        return RetailInformationResult(
            "TEMPORARY_FAILURE", kind,
            reason=(
                "The retail information source is temporarily unavailable "
                f"({type(exc).__name__})."
            ),
        )
