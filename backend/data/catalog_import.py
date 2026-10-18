from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.products.postgres_repository import normalize_search_text


class WinMartRow(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    product_id: str
    item_no: str
    barcode: str
    name: str
    brand: str | None
    category_code: str | None
    category_name: str | None
    department: str | None
    sub_department: str | None
    category_hierarchy: str | None
    price_vnd: int
    sale_price_vnd: int
    discount_promotion: str | None
    stock: int
    shelf: str | None
    uom: str | None
    image_url: str | None
    description: str | None
    seo_url: str | None
    scraped_at: str


def is_complete_import(report: dict[str, Any]) -> bool:
    return bool(
        report.get("valid") is True
        and report.get("rows_total")
        == report.get("rows_accepted", -1) + report.get("rows_quarantined", -1)
    )


def _is_aware_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def analyze_dataset(path: Path) -> tuple[list[WinMartRow], list[dict[str, Any]], dict[str, Any]]:
    raw_bytes = path.read_bytes()
    dataset_version = hashlib.sha256(raw_bytes).hexdigest()
    try:
        source = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return (
            [],
            [],
            {"valid": False, "fatal_errors": [str(exc)], "dataset_version": dataset_version},
        )
    if not isinstance(source, list):
        return (
            [],
            [],
            {
                "valid": False,
                "fatal_errors": ["Root JSON value must be an array"],
                "dataset_version": dataset_version,
            },
        )

    fatal_errors: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    rows: list[WinMartRow] = []
    quarantined: list[dict[str, Any]] = []
    seen: dict[str, set[str]] = {key: set() for key in ("product_id", "item_no", "barcode")}

    for line_number, raw in enumerate(source, start=1):
        if not isinstance(raw, dict):
            fatal_errors.append({"row": line_number, "reason": "record is not an object"})
            continue
        try:
            row = WinMartRow.model_validate(raw)
        except ValidationError as exc:
            fatal_errors.append(
                {
                    "row": line_number,
                    "reason": "schema validation failed",
                    "fields": [e["loc"] for e in exc.errors()],
                }
            )
            continue
        errors = []
        for field in ("product_id", "item_no", "barcode", "name"):
            if not getattr(row, field).strip():
                errors.append(f"{field} is blank")
        for field in seen:
            value = getattr(row, field)
            if value in seen[field]:
                errors.append(f"duplicate {field}: {value}")
            seen[field].add(value)
        if row.price_vnd < 0 or row.sale_price_vnd < 0 or row.stock < 0:
            errors.append("negative price or stock")
        if not _is_aware_timestamp(row.scraped_at):
            errors.append("scraped_at must be a timezone-aware ISO timestamp")
        if errors:
            fatal_errors.append(
                {"row": line_number, "product_id": row.product_id, "reason": errors}
            )
            continue
        rows.append(row)
        if row.brand is None or not row.brand.strip():
            warnings["brand_missing"] += 1
        if row.image_url is None or not row.image_url.strip():
            warnings["image_missing"] += 1
        if row.description == row.name:
            warnings["description_equals_name"] += 1
        if row.sale_price_vnd > row.price_vnd:
            quarantined.append(
                {
                    "product_id": row.product_id,
                    "reason": "SALE_PRICE_EXCEEDS_REGULAR_PRICE",
                    "raw_record": raw,
                }
            )

    report = {
        "valid": not fatal_errors,
        "dataset_version": dataset_version,
        "source_file": path.name,
        "rows_total": len(source),
        "rows_schema_valid": len(rows),
        "rows_accepted": len(rows) - len(quarantined),
        "rows_quarantined": len(quarantined),
        "quarantine_reasons": dict(Counter(item["reason"] for item in quarantined)),
        "warning_counts": dict(warnings),
        "fatal_errors": fatal_errors,
        "category_count": len({row.category_code for row in rows if row.category_code}),
        "brand_count": len({row.brand for row in rows if row.brand}),
    }
    return rows, quarantined, report


async def import_rows(
    database_url: str,
    rows: list[WinMartRow],
    quarantined: list[dict[str, Any]],
    report: dict[str, Any],
) -> None:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        await conn.execute(schema)
        version = report["dataset_version"]
        import_complete = is_complete_import(report)
        quarantine_ids = {item["product_id"] for item in quarantined}
        source_timestamps = [
            datetime.fromisoformat(row.scraped_at.replace("Z", "+00:00"))
            for row in rows
        ]
        async with conn.transaction():
            for row in rows:
                if row.category_code and row.category_name:
                    await conn.execute(
                        """INSERT INTO categories(
                            category_code, category_name, department,
                            sub_department, category_hierarchy
                        )
                        VALUES($1,$2,$3,$4,$5)
                        ON CONFLICT(category_code) DO UPDATE SET
                            category_name=EXCLUDED.category_name,
                            department=EXCLUDED.department,
                            sub_department=EXCLUDED.sub_department,
                            category_hierarchy=EXCLUDED.category_hierarchy""",
                        row.category_code,
                        row.category_name,
                        row.department,
                        row.sub_department,
                        row.category_hierarchy,
                    )
                search_name = normalize_search_text(
                    " ".join(
                        filter(
                            None,
                            (row.product_id, row.item_no, row.barcode, row.name, row.description),
                        )
                    )
                )
                search_title = normalize_search_text(row.name)
                search_brand = normalize_search_text(row.brand)
                search_category = normalize_search_text(
                    " ".join(
                        filter(
                            None,
                            (
                                row.category_name,
                                row.department,
                                row.sub_department,
                                row.category_hierarchy,
                            ),
                        )
                    )
                )
                await conn.execute(
                    """INSERT INTO products(
                        product_id, item_no, barcode, name, brand, category_code,
                        department, sub_department, spicy, search_name, search_title, search_brand,
                        search_category, uom, image_url, description, seo_url
                    )
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,$12,$13,$14,$15,$16)
                    ON CONFLICT(product_id) DO UPDATE SET
                        item_no=EXCLUDED.item_no,
                        barcode=EXCLUDED.barcode,
                        name=EXCLUDED.name,
                        brand=EXCLUDED.brand,
                        category_code=EXCLUDED.category_code,
                        department=EXCLUDED.department,
                        sub_department=EXCLUDED.sub_department,
                        search_name=EXCLUDED.search_name,
                        search_title=EXCLUDED.search_title,
                        search_brand=EXCLUDED.search_brand,
                        search_category=EXCLUDED.search_category,
                        uom=EXCLUDED.uom,
                        image_url=EXCLUDED.image_url,
                        description=EXCLUDED.description,
                        seo_url=EXCLUDED.seo_url,
                        updated_at=now()""",
                    row.product_id,
                    row.item_no,
                    row.barcode,
                    row.name,
                    row.brand,
                    row.category_code,
                    row.department,
                    row.sub_department,
                    search_name,
                    search_title,
                    search_brand,
                    search_category,
                    row.uom,
                    row.image_url,
                    row.description,
                    row.seo_url,
                )
                if row.product_id in quarantine_ids:
                    continue
                status = "SALE" if row.sale_price_vnd < row.price_vnd else "NORMAL"
                effective_price = min(row.price_vnd, row.sale_price_vnd)
                scraped_at = datetime.fromisoformat(row.scraped_at.replace("Z", "+00:00"))
                await conn.execute(
                    """INSERT INTO store_product_snapshots(
                    product_id, store_code, dataset_version, regular_price_vnd,
                    sale_price_vnd, effective_price_vnd, physical_stock, shelf,
                    shelf_source, discount_promotion, price_quality_status, source_timestamp
                    )
                    VALUES($1,'WINMART_WEB',$2,$3,$4,$5,$6,$7,'SIMULATED',$8,$9,$10)
                    ON CONFLICT(product_id,store_code,dataset_version) DO UPDATE SET
                    regular_price_vnd=EXCLUDED.regular_price_vnd,
                    sale_price_vnd=EXCLUDED.sale_price_vnd,
                    effective_price_vnd=EXCLUDED.effective_price_vnd,
                    physical_stock=EXCLUDED.physical_stock,
                    shelf=EXCLUDED.shelf,
                    discount_promotion=EXCLUDED.discount_promotion,
                    price_quality_status=EXCLUDED.price_quality_status,
                    source_timestamp=EXCLUDED.source_timestamp""",
                    row.product_id,
                    version,
                    row.price_vnd,
                    row.sale_price_vnd,
                    effective_price,
                    row.stock,
                    row.shelf,
                    row.discount_promotion,
                    status,
                    scraped_at,
                )
            for item in quarantined:
                await conn.execute(
                    """INSERT INTO catalog_quarantine_records(
                    dataset_version, product_id, reason, raw_record
                    ) VALUES($1,$2,$3,$4::jsonb)
                    ON CONFLICT(dataset_version,product_id) DO NOTHING""",
                    version,
                    item["product_id"],
                    item["reason"],
                    json.dumps(item["raw_record"], ensure_ascii=False),
                )
            await conn.execute(
                """INSERT INTO catalog_import_runs(
                    store_code, dataset_version, imported_at, source_record_count,
                    accepted_record_count, quarantined_record_count,
                    source_timestamp_min, source_timestamp_max, is_complete
                ) VALUES('WINMART_WEB', $1, now(), $2, $3, $4, $5, $6, $7)
                ON CONFLICT(store_code, dataset_version) DO UPDATE SET
                    imported_at=EXCLUDED.imported_at,
                    source_record_count=EXCLUDED.source_record_count,
                    accepted_record_count=EXCLUDED.accepted_record_count,
                    quarantined_record_count=EXCLUDED.quarantined_record_count,
                    source_timestamp_min=EXCLUDED.source_timestamp_min,
                    source_timestamp_max=EXCLUDED.source_timestamp_max,
                    is_complete=EXCLUDED.is_complete""",
                version,
                report["rows_total"],
                report["rows_accepted"],
                report["rows_quarantined"],
                min(source_timestamps) if source_timestamps else None,
                max(source_timestamps) if source_timestamps else None,
                import_complete,
            )
    finally:
        await conn.close()
