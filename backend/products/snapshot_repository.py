"""Serve the validated GitHub catalog directly, without a database import."""

from datetime import datetime
from pathlib import Path

from backend.data.catalog_import import analyze_dataset
from backend.products.catalog import ProductSnapshot, search_products
from backend.products.planogram import recommended_shelf
from backend.products.repository import CatalogAdditions


class SnapshotProductRepository:
    source_name = "thanh_lam_json_snapshot"
    category_aliases = {
        "drink": "nước uống", "snack": "bánh snack", "dairy": "sữa",
        "meal": "thực phẩm ăn liền",
    }

    def __init__(self, path: Path) -> None:
        rows, quarantined, report = analyze_dataset(path)
        if not report.get("valid"):
            raise ValueError("The product catalog failed schema validation")
        self.dataset_sha256 = report["dataset_version"]
        self.rows_quarantined = len(quarantined)
        excluded = {row["product_id"] for row in quarantined}
        products = []
        for row in rows:
            if row.product_id in excluded:
                continue
            shelf = recommended_shelf(row.product_id, self.dataset_sha256)
            products.append(
                ProductSnapshot(
                    product_id=row.product_id,
                    name=row.name,
                    category=row.category_name or row.sub_department or row.department or "",
                    price_vnd=min(row.price_vnd, row.sale_price_vnd),
                    stock=row.stock,
                    shelf=shelf,
                    shelf_source="RECOMMENDED_PLANOGRAM" if shelf else "UNKNOWN",
                    promotion=row.discount_promotion
                    if row.sale_price_vnd < row.price_vnd
                    else None,
                    spicy=None,
                    ingredients=None,
                    expiry_at=None,
                    data_timestamp=datetime.fromisoformat(row.scraped_at.replace("Z", "+00:00")),
                    brand=row.brand,
                    item_no=row.item_no,
                    barcode=row.barcode,
                    category_code=row.category_code,
                    department=row.department,
                    sub_department=row.sub_department,
                    category_hierarchy=row.category_hierarchy,
                    uom=row.uom,
                    image_url=row.image_url,
                    description=row.description,
                    seo_url=row.seo_url,
                    list_price_vnd=row.price_vnd,
                    sale_price_vnd=row.sale_price_vnd,
                    price_quality_status="SALE" if row.sale_price_vnd < row.price_vnd else "NORMAL",
                )
            )
        self.products = tuple(products)
        self._by_id = {product.product_id.casefold(): product for product in products}

    def search(self, **criteria) -> list[ProductSnapshot]:
        category = criteria.get("category")
        if category:
            criteria["category"] = self.category_aliases.get(category.casefold().strip(), category)
        return search_products(products=self.products, **criteria)

    def get_product_snapshot(self, product_id: str) -> ProductSnapshot | None:
        return self._by_id.get(product_id.strip().casefold())

    def list_promotions(self, category: str | None = None, limit: int = 5) -> list[ProductSnapshot]:
        products = tuple(product for product in self.products if product.promotion)
        return search_products(
            products=products, category=category, in_stock=False, limit=max(1, min(limit, 10))
        )

    def list_catalog_additions(self, **criteria) -> CatalogAdditions:
        return CatalogAdditions(comparable=False)
