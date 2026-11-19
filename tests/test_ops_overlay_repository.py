import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from backend.api import main
from backend.config import Settings
from backend.products.ops_overlay_repository import OpsOverlayRepository
from backend.products.repository import get_product_repository
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools

FIXTURE = Path(__file__).parent / "fixtures" / "ops_feed.example.json"
FIXTURE_DAY = date(2026, 11, 19)  # published_at and the first lot date in the fixture
MARKDOWN_ID = "WIN_10281708"  # 6 lots at 30% off expiring on the fixture day, 10 more the next day
ZERO_SNAPSHOT_ID = "WIN_10140279"  # snapshot stock 0, Ops stock 12
SOLD_OUT_ID = "WIN_10242930"  # snapshot stock 3, Ops stock 0
UNMAPPED_ID = "WIN_10171076"  # shampoo, not in the feed


def _shift(value: str, days: int) -> str:
    if "T" in value:
        return (datetime.fromisoformat(value) + timedelta(days=days)).isoformat()
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def write_feed(path: Path, days_from_today: int = 0, **header) -> Path:
    """Copy the bridge example, moving every date so the fixture day lands on today (+ offset)."""
    feed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    days = (datetime.now(UTC).date() - FIXTURE_DAY).days + days_from_today
    feed["published_at"] = _shift(feed["published_at"], days)
    for item in feed["items"]:
        if item["earliest_expiry"]:
            item["earliest_expiry"] = _shift(item["earliest_expiry"], days)
        for lot in item["lots"]:
            lot["expiry"] = _shift(lot["expiry"], days)
    feed.update(header)
    path.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    return path


def _touch_later(path: Path, text: str) -> None:
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before + 10_000_000, before + 10_000_000))


@pytest.fixture()
def base():
    return get_product_repository(Settings(catalog_repository="snapshot", ops_feed_path=""))


@pytest.fixture()
def feed_path(tmp_path):
    return write_feed(tmp_path / "ops_feed.json")


@pytest.fixture()
def repo(feed_path):
    return get_product_repository(
        Settings(catalog_repository="snapshot", ops_feed_path=str(feed_path))
    )


def test_feed_fields_are_merged_and_shelf_survives(repo, base):
    assert isinstance(repo, OpsOverlayRepository)
    assert repo.source_name == base.source_name == "winmart_json_snapshot"
    assert repo.dataset_sha256 == base.dataset_sha256
    product = repo.get_product_snapshot(MARKDOWN_ID.lower())
    original = base.get_product_snapshot(MARKDOWN_ID)
    today = datetime.now(UTC).date()
    assert product.stock == product.sellable_stock == 16
    assert product.expiry_at == today.isoformat()
    assert product.price_vnd == product.list_price_vnd == 19000
    assert product.sale_price_vnd == 13300
    assert product.promotion.startswith("Giảm 30% cho 6 sản phẩm HSD")
    assert product.price_quality_status == "SALE"
    assert product.data_timestamp.date() == today
    assert product.shelf == original.shelf is not None
    assert product.shelf_source == original.shelf_source == "RECOMMENDED_PLANOGRAM"
    assert (product.name, product.barcode, product.image_url) == (
        original.name, original.barcode, original.image_url
    )
    regular = repo.get_product_snapshot(ZERO_SNAPSHOT_ID)
    assert (regular.stock, regular.promotion, regular.price_quality_status) == (12, None, "NORMAL")
    assert repo.get_product_snapshot(UNMAPPED_ID) == base.get_product_snapshot(UNMAPPED_ID)
    assert len(repo.products) == len(base.products)


def test_in_stock_filter_uses_overlaid_stock(repo, base):
    assert base.search(query=ZERO_SNAPSHOT_ID, in_stock=True) == []
    found = repo.search(query=ZERO_SNAPSHOT_ID, in_stock=True)
    assert [p.product_id for p in found] == [ZERO_SNAPSHOT_ID]
    assert [p.product_id for p in base.search(query=SOLD_OUT_ID, in_stock=True)] == [SOLD_OUT_ID]
    assert repo.search(query=SOLD_OUT_ID, in_stock=True) == []
    assert repo.search(category="drink", in_stock=False, limit=3)  # category aliases still apply


def test_ops_markdowns_lead_the_promotion_list(repo, base):
    assert MARKDOWN_ID not in {p.product_id for p in base.list_promotions(limit=10)}
    promotions = repo.list_promotions(limit=5)
    assert promotions[0].product_id == MARKDOWN_ID
    assert len(promotions) == 5
    assert all(p.promotion for p in promotions)
    observation = retail_tools.execute_catalog_tool(
        "list_product_promotions", json.dumps({"limit": 5}), repository=repo
    )
    assert observation.content["items"][0]["product_id"] == MARKDOWN_ID
    unrelated = repo.list_promotions(category="sữa", limit=5)
    assert MARKDOWN_ID not in {p.product_id for p in unrelated}


@pytest.mark.asyncio
async def test_snapshot_endpoint_serves_the_overlay(repo, monkeypatch):
    monkeypatch.setattr(main, "get_product_repository", lambda *_: repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        payload = (await client.get(f"/products/{MARKDOWN_ID}/snapshot")).json()
    assert payload["stock"] == 16
    assert payload["expiry_at"] == datetime.now(UTC).date().isoformat()
    assert payload["shelf"]
    published = json.loads(Path(repo.feed_path).read_text(encoding="utf-8"))["published_at"]
    assert datetime.fromisoformat(payload["data_timestamp"]) == datetime.fromisoformat(published)


def test_broken_feed_keeps_the_last_valid_one(repo, feed_path, caplog):
    assert repo.get_product_snapshot(MARKDOWN_ID).stock == 16
    with caplog.at_level(logging.WARNING):
        _touch_later(feed_path, "{not json")
        assert repo.get_product_snapshot(MARKDOWN_ID).stock == 16
        feed = json.loads(FIXTURE.read_text(encoding="utf-8"))
        feed["version"] = 2
        _touch_later(feed_path, json.dumps(feed))
        assert repo.get_product_snapshot(MARKDOWN_ID).stock == 16
        feed["version"] = 1
        feed["items"][3]["lots"][0]["expiry"] = "not-a-date"
        _touch_later(feed_path, json.dumps(feed))
        assert repo.get_product_snapshot(MARKDOWN_ID).stock == 16
    assert sum("keeping the last valid feed" in r.message for r in caplog.records) == 3
    new_feed = feed_path.with_name("next.json")
    write_feed(new_feed)
    feed = json.loads(new_feed.read_text(encoding="utf-8"))
    feed["items"][3]["sellable_qty"] = 7
    _touch_later(feed_path, json.dumps(feed))
    assert repo.get_product_snapshot(MARKDOWN_ID).stock == 7


def test_stale_feed_drops_expired_lots(tmp_path, base):
    path = write_feed(tmp_path / "stale.json", days_from_today=-1)
    repo = get_product_repository(Settings(catalog_repository="snapshot", ops_feed_path=str(path)))
    product = repo.get_product_snapshot(MARKDOWN_ID)
    today = datetime.now(UTC).date()
    # The 6 marked-down units expired yesterday.
    assert product.stock == product.sellable_stock == 10
    assert product.expiry_at == today.isoformat()
    assert product.promotion is None
    assert (product.sale_price_vnd, product.price_quality_status) == (19000, "NORMAL")
    assert product.data_timestamp.date() == today - timedelta(days=1)  # age stays visible
    assert repo.list_promotions(limit=5)[0].product_id != MARKDOWN_ID


def test_missing_feed_serves_the_plain_snapshot(tmp_path, base):
    repo = get_product_repository(
        Settings(catalog_repository="snapshot", ops_feed_path=str(tmp_path / "absent.json"))
    )
    assert repo.get_product_snapshot(MARKDOWN_ID) == base.get_product_snapshot(MARKDOWN_ID)


def test_feature_off_keeps_the_original_repositories(base):
    assert type(base) is SnapshotProductRepository
    assert Settings(_env_file=None).ops_feed_path == ""
    for mode in ("demo", "postgres"):
        with pytest.raises(ValueError, match="OPS_FEED_PATH"):
            get_product_repository(Settings(catalog_repository=mode, ops_feed_path="feed.json"))
