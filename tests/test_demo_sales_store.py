"""Persistent demo sales must actually drive ranking, without inventing missing rows."""

import sqlite3

import pytest

from backend.products.catalog import ProductSnapshot
from backend.services.demo_sales import rank_demo_sales
from backend.services.demo_sales_store import DemoSalesStore
from backend.services.query_understanding import understand_catalog_query


def test_seed_persists_and_rerun_preserves_edits(tmp_path):
    store = DemoSalesStore(tmp_path / "sales.sqlite3")
    summary = store.seed([f"item-{i}" for i in range(1200)])
    assert summary["count"] == summary["inserted"] == 1200
    assert 0 <= summary["min"] <= summary["max"] < 1000
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE demo_product_sales SET units_sold_30d = 4 WHERE product_id = 'item-1'"
        )
    assert store.seed(["item-1", "item-new"])["inserted"] == 1
    reloaded = DemoSalesStore(store.path)
    assert reloaded.counts()["item-1"] == 4
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT DISTINCT data_source, period_days FROM demo_product_sales"
        ).fetchall() == [("SIMULATED", 30)]


@pytest.mark.parametrize("invalid", [-1, 1000, 1.2, "no-sales"])
def test_database_rejects_invalid_sales_values(tmp_path, invalid):
    store = DemoSalesStore(tmp_path / "sales.sqlite3")
    store.seed(["item"])
    with sqlite3.connect(store.path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE demo_product_sales SET units_sold_30d = ? WHERE product_id = 'item'",
            (invalid,),
        )


def test_missing_database_is_not_created_by_a_read(tmp_path):
    store = DemoSalesStore(tmp_path / "missing.sqlite3")
    assert store.counts() == {}
    assert not store.path.exists()


def test_editing_persisted_counts_changes_winner_and_missing_rows_are_not_ranked(tmp_path):
    products = [ProductSnapshot(
        product_id=pid, name=f"Trà {pid}", category="Nước trà",
        category_hierarchy="Thực phẩm > Đồ uống > Nước trà", price_vnd=10_000, stock=3,
    ) for pid in ["a", "b", "unseeded"]]

    class Repository:
        def list_brand_names(self, **kwargs):
            return ()

        def search(self, **kwargs):
            return products

    query = understand_catalog_query("nước trà bán chạy nhất")
    store = DemoSalesStore(tmp_path / "sales.sqlite3")
    store.seed(["a", "b"])
    for winner in ["a", "b"]:
        with sqlite3.connect(store.path) as connection:
            connection.execute("UPDATE demo_product_sales SET units_sold_30d = 2")
            connection.execute(
                "UPDATE demo_product_sales SET units_sold_30d = 999 WHERE product_id = ?",
                (winner,),
            )
        ranked = rank_demo_sales(Repository(), query, "Nước trà", sales_counts=store.counts())
        assert ranked[0].product.product_id == winner
        assert ranked[0].units_sold_30d == 999
        assert len(ranked) == 2
