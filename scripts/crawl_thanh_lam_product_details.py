"""Resumable async crawler for official Thanh Lam Mart product details (ingredients, usage, storage, benefits).

Fetches the public SSR ``__NEXT_DATA__`` payload from each product's official
``seo_url`` in ``data/thanh_lam_products.json`` (no CAPTCHA/anti-bot bypass;
stops gracefully if rate-limited), extracts structured Vietnamese fields
(``ingredients``, ``usage``, ``storage``, ``benefits``, ``origin``, ``summary``),
optionally supplements missing ingredients from OpenFoodFacts by EAN barcode,
and writes ``data/thanh_lam_product_details.json`` bound to the catalog SHA-256.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "thanh_lam_products.json"
CHECKPOINT_PATH = ROOT / "data" / ".thanh_lam_details_checkpoint.jsonl"
OUTPUT_PATH = ROOT / "data" / "thanh_lam_product_details.json"

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Section heading detectors for Thanh Lam Mart Vietnamese product pages.
_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ingredients",
        re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:thành phần(?:\s+chính|\s+dinh dưỡng|\s+và\s+công dụng)?|"
            r"nguyên liệu(?:\s+chính)?|chất liệu(?:\s+chính)?)\s*[:：\-]?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "usage",
        re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:hướng dẫn sử dụng(?:\s+và\s+bảo quản)?|"
            r"cách(?:\s+sử)?\s+dụng|cách dùng|cách chế biến|cách pha|liều dùng)\s*[:：\-]?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "storage",
        re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:hướng dẫn bảo quản|bảo quản|cách bảo quản)\s*[:：\-]?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "benefits",
        re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:công dụng(?:\s+sản phẩm)?|đặc điểm nổi bật|"
            r"ưu điểm nổi bật|lợi ích|tác dụng)\s*[:：\-]?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "origin",
        re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:xuất xứ|nơi sản xuất|sản xuất tại|thương hiệu)\s*[:：\-]?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "other",
        re.compile(
            r"^(?:[\d\.\-\*\•]+\s*)?(?:thông tin sản phẩm|mô tả sản phẩm|lưu ý|"
            r"hạn sử dụng|khối lượng|dung tích|quy cách|đối tượng sử dụng)\s*[:：\-]?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
)

_INLINE_INGREDIENT_SENTENCE_RE = re.compile(
    r"([^.\n]*(?:được làm (?:từ|bằng)|làm từ|thành phần (?:gồm|chính|chứa|có|gồm có)|"
    r"chứa thành phần|nguyên liệu (?:tự nhiên |chính )?(?:như|gồm)|"
    r"kết hợp (?:từ|giữa)|chiết xuất từ|bổ sung thành phần)[^.\n]*\.?)",
    re.IGNORECASE,
)


def strip_html_lines(raw_html: str) -> list[str]:
    if not raw_html:
        return []
    text = re.sub(r"</?(?:p|div|br|li|tr|h[1-6]|ul|ol|table)[^>]*>", "\n", raw_html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", raw_line).strip(" \t\r\n")
        if cleaned:
            lines.append(cleaned)
    return lines


def parse_long_description(raw_html: str, product_name: str) -> dict[str, str]:
    lines = strip_html_lines(raw_html)
    if not lines:
        return {}

    buckets: dict[str, list[str]] = {
        "ingredients": [],
        "usage": [],
        "storage": [],
        "benefits": [],
        "origin": [],
    }
    current_section: str | None = None

    for line in lines:
        matched_section: str | None = None
        inline_rest = ""
        # Check if this line starts a known section header (avoid matching long narrative sentences).
        for sec_name, pat in _SECTION_PATTERNS:
            m = pat.match(line)
            if m:
                prefix_len = len(line) - len(m.group(1))
                if prefix_len <= 42:
                    matched_section = sec_name
                    inline_rest = m.group(1).strip()
                    break

        if matched_section is not None:
            current_section = matched_section if matched_section in buckets else None
            if current_section and inline_rest:
                buckets[current_section].append(inline_rest)
            continue

        if current_section and current_section in buckets:
            # Avoid stuffing huge unrelated marketing paragraphs into a single section.
            if sum(len(x) for x in buckets[current_section]) < 550:
                buckets[current_section].append(line)

    result: dict[str, str] = {}
    for key, parts in buckets.items():
        if parts:
            joined = " ".join(dict.fromkeys(parts)).strip()
            if len(joined) > 600:
                joined = joined[:597].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
            result[key] = joined

    # Fallback: if no explicit "Thành phần:" heading existed, extract verified ingredient
    # sentences from the Thanh Lam Mart listing text (e.g. "được làm từ bột mì, thịt heo, trứng cút...").
    full_plain = " ".join(lines)
    if "ingredients" not in result:
        ing_matches = [
            m.group(1).strip()
            for m in _INLINE_INGREDIENT_SENTENCE_RE.finditer(full_plain)
            if 18 <= len(m.group(1).strip()) <= 360
        ]
        if ing_matches:
            snippet = " ".join(dict.fromkeys(ing_matches[:2])).strip()
            if len(snippet) > 480:
                snippet = snippet[:477].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
            result["ingredients"] = snippet

    # Keep a concise summary (skipping bare product-title-only first line).
    summary_lines = [
        ln for ln in lines if ln.casefold().rstrip(".") != product_name.casefold().rstrip(".")
    ]
    summary = " ".join(summary_lines or lines).strip()
    if summary:
        if len(summary) > 520:
            summary = summary[:517].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
        result["summary"] = summary

    return result


async def fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    item: dict[str, Any],
) -> dict[str, Any]:
    url = str(item.get("seo_url") or "").strip()
    pid = str(item["product_id"])
    base: dict[str, Any] = {
        "product_id": pid,
        "product_name": str(item.get("name") or ""),
        "brand": str(item.get("brand") or ""),
        "barcode": str(item.get("barcode") or ""),
        "source_url": url,
        "http_status": 0,
    }
    if not url.startswith("https://"):
        return base

    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(url)
                base["http_status"] = resp.status_code
                if resp.status_code == 200:
                    m = _NEXT_DATA_RE.search(resp.text)
                    if not m:
                        return base
                    data = json.loads(m.group(1))
                    prod = (
                        data.get("props", {})
                        .get("pageProps", {})
                        .get("product")
                    )
                    if isinstance(prod, dict):
                        ld = prod.get("longDescription") or ""
                        if isinstance(ld, str) and ld.strip():
                            parsed = parse_long_description(ld, base["product_name"])
                            base.update(parsed)
                    return base
                if resp.status_code in {429, 500, 502, 503, 504}:
                    await asyncio.sleep(0.8 * (2**attempt))
                    continue
                return base
            except Exception as exc:
                base["error"] = type(exc).__name__
                await asyncio.sleep(0.6 * (2**attempt))
        return base


async def supplement_from_openfoodfacts(
    records: dict[str, dict[str, Any]],
) -> int:
    """Query public OpenFoodFacts API in batches for EAN barcodes missing Thanh Lam Mart ingredients."""
    missing = [
        rec
        for rec in records.values()
        if not rec.get("ingredients")
        and re.fullmatch(r"\d{8,14}", str(rec.get("barcode") or ""))
    ]
    if not missing:
        return 0
    by_barcode = {str(rec["barcode"]): rec for rec in missing}
    barcodes = list(by_barcode.keys())
    added = 0
    headers = {"User-Agent": "MaiRetailKioskCatalogEnricher/1.0"}
    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        for start in range(0, len(barcodes), 80):
            batch = barcodes[start : start + 80]
            try:
                r = await client.get(
                    "https://world.openfoodfacts.org/api/v2/search",
                    params={
                        "code": ",".join(batch),
                        "fields": "code,ingredients_text_vi,ingredients_text",
                        "page_size": str(len(batch)),
                    },
                )
                if r.status_code != 200:
                    continue
                for prod in r.json().get("products", []):
                    code = str(prod.get("code") or "")
                    ing = (
                        str(prod.get("ingredients_text_vi") or "").strip()
                        or str(prod.get("ingredients_text") or "").strip()
                    )
                    if code in by_barcode and len(ing) >= 10:
                        if len(ing) > 550:
                            ing = ing[:547].rsplit(" ", 1)[0] + "..."
                        by_barcode[code]["ingredients"] = ing
                        by_barcode[code]["ingredients_source"] = "OpenFoodFacts"
                        by_barcode[code]["ingredients_source_url"] = (
                            f"https://world.openfoodfacts.org/product/{code}"
                        )
                        added += 1
            except Exception:
                continue
    return added


async def main() -> None:
    raw_bytes = CATALOG_PATH.read_bytes()
    catalog_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    catalog: list[dict[str, Any]] = json.loads(raw_bytes.decode("utf-8"))
    print(f"Loaded {len(catalog)} products (sha256={catalog_sha256[:12]}...)")

    # Resume from existing checkpoint if present.
    done: dict[str, dict[str, Any]] = {}
    if CHECKPOINT_PATH.exists():
        for line in CHECKPOINT_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("product_id"):
                    done[rec["product_id"]] = rec
            except Exception:
                continue
        print(f"Resumed {len(done)} already-crawled items from checkpoint")

    todo = [it for it in catalog if str(it["product_id"]) not in done]
    print(f"Remaining to crawl from thanhlammart.vn: {len(todo)}")

    t0 = time.perf_counter()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    }
    sem = asyncio.Semaphore(24)
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=32)

    if todo:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers=headers,
            limits=limits,
        ) as client:
            with CHECKPOINT_PATH.open("a", encoding="utf-8") as ckpt:
                batch_size = 100
                for offset in range(0, len(todo), batch_size):
                    chunk = todo[offset : offset + batch_size]
                    results = await asyncio.gather(
                        *(fetch_one(client, sem, it) for it in chunk)
                    )
                    for rec in results:
                        done[rec["product_id"]] = rec
                        ckpt.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    ckpt.flush()
                    elapsed = time.perf_counter() - t0
                    crawled_now = offset + len(chunk)
                    rate = crawled_now / max(elapsed, 0.1)
                    has_ing = sum(1 for r in done.values() if r.get("ingredients"))
                    has_sum = sum(1 for r in done.values() if r.get("summary"))
                    print(
                        f"  [{len(done):4d}/{len(catalog)}] {rate:4.1f} req/s | "
                        f"summary={has_sum} | ingredients={has_ing} | elapsed={elapsed:.0f}s",
                        flush=True,
                    )

    print("Supplementing missing ingredients from OpenFoodFacts public EAN database...")
    off_added = await supplement_from_openfoodfacts(done)
    print(f"  OpenFoodFacts added {off_added} additional verified ingredient lists")

    # Build compact production JSON keeping only products with useful extracted details.
    compact_records: dict[str, dict[str, Any]] = {}
    stats = {"summary": 0, "ingredients": 0, "usage": 0, "storage": 0, "benefits": 0, "origin": 0}
    for it in catalog:
        pid = str(it["product_id"])
        rec = done.get(pid)
        if not rec:
            continue
        useful = {
            k: rec[k]
            for k in (
                "ingredients",
                "usage",
                "storage",
                "benefits",
                "origin",
                "summary",
                "ingredients_source",
                "ingredients_source_url",
            )
            if rec.get(k)
        }
        if not useful:
            continue
        for k in stats:
            if k in useful:
                stats[k] += 1
        compact_records[pid] = {
            "product_name": rec["product_name"],
            "brand": rec["brand"],
            "barcode": rec["barcode"],
            "source_url": rec["source_url"],
            **useful,
        }

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "Thanh Lam Mart Official Product Listing",
        "source_catalog_sha256": catalog_sha256,
        "total_catalog_products": len(catalog),
        "enriched_products": len(compact_records),
        "field_counts": stats,
        "records": compact_records,
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    print(
        f"\nDONE! Wrote {OUTPUT_PATH.name} ({size_mb:.2f} MB) | "
        f"enriched={len(compact_records)}/{len(catalog)} | stats={stats}"
    )


if __name__ == "__main__":
    asyncio.run(main())
