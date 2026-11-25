"""Seed <=999 simulated sales per accepted catalog item, preserving existing rows."""

from __future__ import annotations

import json

from backend.config import get_settings
from backend.products.repository import get_product_repository
from backend.services.demo_sales_store import get_demo_sales_store


def main() -> None:
    settings = get_settings()
    if settings.app_env != "development":
        raise SystemExit("Demo sales can only be seeded in development")
    repository = get_product_repository(settings)
    rows = getattr(repository, "products", None)
    if rows is None:
        rows, seen = [], set()
        for offset in range(0, 100_000, 20):
            page = repository.search(limit=20, offset=offset)
            if any(row.product_id in seen for row in page):
                raise ValueError("Catalog pagination repeated a page")
            seen.update(row.product_id for row in page)
            rows.extend(page)
            if len(page) < 20:
                break
        else:
            raise ValueError("Catalog scan exceeded the demo seed bound")
    store = get_demo_sales_store(settings)
    summary = store.seed(row.product_id for row in rows)
    print(json.dumps({"database": str(store.path), "source": "SIMULATED", **summary}))


if __name__ == "__main__":
    main()
