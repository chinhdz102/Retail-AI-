"""Build a Kaggle-ready SKU image manifest from the tracked catalog.

The manifest deliberately keeps one row per SKU. Recognition is implemented as
gallery retrieval, so adding a new SKU only requires a new reference image and
metadata row. The planogram code remains a separate lookup joined by product_id.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def friendly_location(code: str | None) -> str:
    value = (code or "").strip()
    parts = value.split("-")
    if len(parts) == 4 and all(parts):
        return "Dãy {} · Khoang {} · Tầng {} · Ô {}".format(
            parts[0].removeprefix("A"),
            parts[1].removeprefix("B"),
            parts[2].removeprefix("L"),
            parts[3].removeprefix("P"),
        )
    if len(parts) == 2 and all(parts):
        return f"Khu {parts[0]} · Tầng {parts[1]}"
    return value


def load_locations(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["product_id"]: row["location_code"]
        for row in payload.get("assignments", [])
        if row.get("product_id") and row.get("location_code")
    }


def build_rows(catalog: list[dict], locations: dict[str, str]) -> list[dict]:
    rows = []
    for product in catalog:
        image_url = (product.get("image_url") or "").strip()
        if not image_url:
            continue
        shelf_code = locations.get(product["product_id"], product.get("shelf") or "")
        rows.append({
            "product_id": product["product_id"],
            "name": product["name"],
            "brand": product.get("brand") or "",
            "category": product.get("category_name") or product.get("category") or "",
            "category_hierarchy": product.get("category_hierarchy") or "",
            "image_url": image_url,
            "shelf_code": shelf_code,
            "shelf_friendly": friendly_location(shelf_code),
            "stock": int(product.get("stock") or 0),
        })
    return rows


def write_outputs(rows: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "catalog_manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    with (output_dir / "catalog_manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "rows": len(rows),
        "unique_products": len({row["product_id"] for row in rows}),
        "unique_images": len({row["image_url"] for row in rows}),
        "output_dir": str(output_dir),
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/thanh_lam_products.json"))
    parser.add_argument(
        "--planogram",
        type=Path,
        default=Path("data/thanh_lam_planogram_assignments.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("vision/data"))
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    rows = build_rows(catalog, load_locations(args.planogram))
    write_outputs(rows, args.output_dir)


if __name__ == "__main__":
    main()
