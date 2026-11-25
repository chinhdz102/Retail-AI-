"""Offline named-item recall diagnostic for bounded consultation candidate sampling.

Queries are derived from catalog titles. This measures whether a specifically
named item survives sampling, not semantic task fit, human advice or live stock.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.customer_consultation import _sample_candidates

ROOT = Path(__file__).resolve().parents[1]


def main():
    repository = SnapshotProductRepository(ROOT / "data/thanh_lam_products.json")
    source = ROOT / "backend/services/customer_consultation.py"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    groups = defaultdict(list)
    for product in repository.products:
        groups[product.category_hierarchy or product.category].append(product)
    rows = []
    skipped_large = []
    for path, products in sorted(groups.items()):
        if len(products) <= 8:
            continue  # No sampling is needed, so it cannot affect recall.
        if len(products) > 200:
            skipped_large.append(path)
            continue  # Pagination coverage is a separate experiment.
        products.sort(key=lambda item: (item.price_vnd, item.name))
        old = [products[round(i * (len(products) - 1) / 7)] for i in range(8)]
        for index in sorted({round(i * (len(products) - 1) / 5) for i in range(6)}):
            target = products[index]
            query = f"Chọn giúp mình {target.name}"
            started = perf_counter()
            new = _sample_candidates(products, query)
            rows.append({
                "category": path, "category_size": len(products),
                "query": query, "target_product_id": target.product_id,
                "price_only_recalled": target in old,
                "request_and_price_recalled": target in new,
                "elapsed_ms": round((perf_counter() - started) * 1000, 3),
            })
    times = sorted(row["elapsed_ms"] for row in rows)
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": (
            "Catalog-derived named-item queries; development diagnostic, no human ratings."
        ),
        "source_sha256": before,
        "source_stable": before == hashlib.sha256(source.read_bytes()).hexdigest(),
        "catalog_sha256": repository.dataset_sha256,
        "summary": {
            "queries": len(rows), "categories": len({row["category"] for row in rows}),
            "price_only_recalled": sum(row["price_only_recalled"] for row in rows),
            "request_and_price_recalled": sum(row["request_and_price_recalled"] for row in rows),
            "p50_sampling_ms": statistics.median(times) if times else None,
            "p95_sampling_ms": times[round((len(times) - 1) * .95)] if times else None,
        },
        "excluded_categories_over_pagination_bound": skipped_large,
        "rows": rows,
    }
    path = ROOT / "docs/reports/artifacts/employee_iteration_sampling_20261103.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"]))


if __name__ == "__main__":
    main()
