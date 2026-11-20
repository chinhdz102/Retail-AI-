from datetime import UTC, datetime, timedelta

import pytest

from backend.services import retail_information
from backend.services.retail_information import PostgresRetailInformationRepository


class FakeRetailInformationConnection:
    def __init__(self, feed=None, rows=(), mapped=True):
        self.feed = feed
        self.rows = list(rows)
        self.mapped = mapped

    async def fetchrow(self, query, *args):
        if "FROM retail_kiosks" in query:
            if not self.mapped:
                return None
            return {
                "store_code": "BRANCH_12",
                "store_name": "WinMart Minh Khai",
                "address": "12 phố Minh Khai",
                "phone": None,
                "opening_hours": None,
                "timezone": "Asia/Ho_Chi_Minh",
                "latitude": 0.0,
                "longitude": 0.0,
                "source_name": "approved store master",
                "source_uri": "https://example.test/store/12",
                "verified_at": datetime.now(UTC),
                "organization_code": "WINCOMMERCE",
            }
        if "FROM retail_data_feeds" in query:
            return self.feed
        raise AssertionError(f"Unexpected query: {query}")

    async def fetch(self, query, *args):
        if "FROM promotion_campaigns" in query or "FROM inventory_receipts" in query:
            return self.rows
        if "FROM store_nearby_places" in query:
            return self.rows
        raise AssertionError(f"Unexpected query: {query}")

    async def close(self):
        return None


def _feed(*, coverage_complete: bool, start=None, end=None):
    return {
        "provider": "verified test feed",
        "status": "ACTIVE",
        "last_success_at": datetime.now(UTC),
        "coverage_start_at": start,
        "coverage_end_at": end,
        "coverage_complete": coverage_complete,
        "source_uri": "https://example.test/feed",
    }


def _use_fake_connection(monkeypatch, connection):
    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(retail_information.asyncpg, "connect", connect)


def test_unmapped_kiosk_cannot_read_an_arbitrary_store(monkeypatch):
    connection = FakeRetailInformationConnection(mapped=False)
    _use_fake_connection(monkeypatch, connection)

    result = PostgresRetailInformationRepository("postgresql://unused").lookup(
        "store", "kiosk-unknown"
    )

    assert result.status == "DATA_UNAVAILABLE"
    assert result.store is None


def test_empty_receipt_result_requires_full_recent_coverage(monkeypatch):
    now = datetime.now(UTC)
    connection = FakeRetailInformationConnection(
        feed=_feed(
            coverage_complete=True,
            start=now - timedelta(days=3),
            end=now,
        )
    )
    _use_fake_connection(monkeypatch, connection)

    result = PostgresRetailInformationRepository("postgresql://unused").lookup(
        "receipts", "kiosk-12"
    )

    assert result.status == "DATA_UNAVAILABLE"
    assert result.items == ()


def test_empty_receipt_result_is_negative_only_for_a_complete_seven_day_feed(monkeypatch):
    now = datetime.now(UTC)
    connection = FakeRetailInformationConnection(
        feed=_feed(
            coverage_complete=True,
            start=now - timedelta(days=8),
            end=now + timedelta(minutes=1),
        )
    )
    _use_fake_connection(monkeypatch, connection)

    result = PostgresRetailInformationRepository("postgresql://unused").lookup(
        "receipts", "kiosk-12"
    )

    assert result.status == "SUCCESS"
    assert result.items == ()


def test_zero_coordinates_are_valid_for_a_verified_store_anchor(monkeypatch):
    connection = FakeRetailInformationConnection(
        feed=_feed(coverage_complete=True),
        rows=[
            {
                "place_name": "Clinic",
                "place_category": "healthcare",
                "distance_m": 50,
                "attribution": "OpenStreetMap contributors",
            }
        ],
    )
    _use_fake_connection(monkeypatch, connection)

    result = PostgresRetailInformationRepository("postgresql://unused").lookup(
        "nearby", "kiosk-12"
    )

    assert result.status == "SUCCESS"
    assert result.items[0]["place_name"] == "Clinic"


def test_empty_campaign_result_requires_complete_current_feed(monkeypatch):
    connection = FakeRetailInformationConnection(
        feed=_feed(coverage_complete=False, end=datetime.now(UTC) + timedelta(days=1))
    )
    _use_fake_connection(monkeypatch, connection)

    result = PostgresRetailInformationRepository("postgresql://unused").lookup(
        "promotions", "kiosk-12"
    )

    assert result.status == "DATA_UNAVAILABLE"
    assert "complete current coverage" in result.reason


@pytest.mark.parametrize("ops_feed_path", ["", "ops_feed.json"])
def test_snapshot_catalog_reads_branch_facts_only_with_the_ops_feed_enabled(
    monkeypatch, ops_feed_path
):
    from backend import config

    connection = FakeRetailInformationConnection()
    _use_fake_connection(monkeypatch, connection)
    settings = config.Settings(
        _env_file=None, catalog_repository="snapshot", ops_feed_path=ops_feed_path,
        database_url="postgresql+asyncpg://retail:retail@localhost:5432/retail",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    result = retail_information.lookup_retail_information("store", "kiosk-01")

    if ops_feed_path:
        assert result.status == "SUCCESS"
        assert result.store["store_code"] == "BRANCH_12"
    else:
        assert result.status == "DATA_UNAVAILABLE"
        assert result.reason == "Branch information is not available in the active demo repository."


def test_new_arrivals_still_on_sale_come_first_and_sold_out_are_marked(monkeypatch) -> None:
    from backend.services import retail_tools
    from backend.services.retail_information import RetailInformationResult

    class Product:
        def __init__(self, stock):
            self.sellable_stock = stock

    stock = {"WIN_1": 0, "WIN_2": 9, "WIN_3": 0, "WIN_4": 4}

    class Repo:
        def get_product_snapshot(self, product_id):
            return Product(stock[product_id]) if product_id in stock else None

    rows = tuple(
        {"product_id": pid, "product_name": pid, "received_at": f"2026-11-19T0{i}:00:00+00:00"}
        for i, pid in enumerate(["WIN_1", "WIN_2", "WIN_2", "WIN_3", "WIN_4", "WIN_9"])
    )
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: Repo())
    monkeypatch.setattr(
        retail_tools, "lookup_retail_information",
        lambda kind, kiosk_id, category=None: RetailInformationResult(
            "SUCCESS", kind, store={"store_code": "S"}, items=rows, source="feed"
        ),
    )
    observation = retail_tools.execute_retail_information_tool(
        "list_recent_stock_receipts", "{}", kiosk_id="kiosk-01"
    )
    items = observation.content["items"]
    # In stock first (unknown products are not hidden), one line per product, sold out last
    assert [i["product_id"] for i in items] == ["WIN_2", "WIN_4", "WIN_9", "WIN_1", "WIN_3"]
    assert [bool(i.get("sold_out")) for i in items] == [False, False, False, True, True]
