"""
WinMart Product Crawler
Thu thập toàn bộ mặt hàng và thông tin chi tiết từ WinMart (winmart.vn)
Dữ liệu trích xuất:
- product_id / itemNo / sku
- barcode (mã vạch chuẩn EAN)
- name (tên sản phẩm)
- brand (thương hiệu)
- category (danh mục & ngành hàng cấp 1-5)
- price_vnd (giá gốc niêm yết)
- sale_price_vnd (giá khuyến mãi)
- stock (tồn kho)
- uom (đơn vị tính: hộp, chai, lon, gói...)
- promotion (thông tin giảm giá nếu có)
- description & ingredients (mô tả, thành phần)
- image_url (ảnh sản phẩm chất lượng cao)
"""

import asyncio
import csv
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE_API = "https://api-crownx.winmart.vn/it/api/web/v3/item/category"
SITEMAP_URL = "https://winmart.vn/api/sitemap"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://winmart.vn",
    "Referer": "https://winmart.vn/",
}

STORE_CODE = "1535"
STORE_GROUP_CODE = "1998"
CONCURRENCY_LIMIT = 5
PAGE_SIZE = 50


def clean_html(text: str | None) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def map_shelf(mch1_name: str, mch2_name: str, category_name: str) -> str:
    """Tự động phân bổ vị trí kệ trưng bày cho hệ thống Retail AI"""
    cat = f"{mch1_name} {mch2_name} {category_name}".lower()
    if "sữa" in cat or "bơ" in cat or "trứng" in cat:
        return "B1-L1"
    elif "đồ uống" in cat or "nước" in cat or "bia" in cat or "rượu" in cat:
        return "A1-L2"
    elif "snack" in cat or "bánh" in cat or "kẹo" in cat:
        return "C2-L1"
    elif "mì" in cat or "gạo" in cat or "gia vị" in cat or "thực phẩm" in cat:
        return "C1-L2"
    elif "hóa phẩm" in cat or "giặt" in cat or "tẩy" in cat:
        return "D1-L1"
    elif "rau" in cat or "củ" in cat or "trái cây" in cat or "thịt" in cat:
        return "F1-L1"
    elif "chăm sóc" in cat or "mỹ phẩm" in cat or "cá nhân" in cat:
        return "E1-L2"
    return "A2-L1"


async def fetch_category_page(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    slug: str,
    page: int,
) -> dict[str, Any] | None:
    url = BASE_API
    params = {
        "storeCode": STORE_CODE,
        "storeGroupCode": STORE_GROUP_CODE,
        "slug": slug,
        "pageNumber": page,
        "pageSize": PAGE_SIZE,
    }
    for attempt in range(3):
        async with sem:
            try:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 429:
                    await asyncio.sleep(2 * (attempt + 1))
            except Exception as exc:
                if attempt == 2:
                    print(f"Error [{slug}] p.{page}: {exc}")
                await asyncio.sleep(1)
    return None


async def get_all_category_slugs(client: httpx.AsyncClient) -> list[str]:
    print("-> Đang lấy danh mục từ sitemap WinMart...")
    r = await client.get(SITEMAP_URL)
    locs = re.findall(r"<loc>https://winmart\.vn/([^<]+--c[^<]+)</loc>", r.text)
    slugs = sorted(list(set(locs)))
    print(f"-> Tìm thấy {len(slugs)} danh mục cấp ngành hàng của WinMart.")
    return slugs


def transform_product(raw: dict[str, Any]) -> dict[str, Any]:
    item_no = str(raw.get("itemNo") or "").strip()
    name = str(raw.get("name") or "").strip()
    barcode = str(raw.get("barcode") or "").strip()
    brand = str(raw.get("brandName") or "").strip()
    price = int(raw.get("price") or 0)
    sale_price = int(raw.get("salePrice") or price)
    stock = int(raw.get("quantity") or 0)
    uom = str(raw.get("uomName") or "Món").strip()
    category_code = str(raw.get("categoryCode") or "").strip()
    category_name = str(raw.get("categoryName") or "").strip()
    mch1 = str(raw.get("mch1Name") or "").strip()
    mch2 = str(raw.get("mch2Name") or "").strip()
    mch3 = str(raw.get("mch3Name") or "").strip()
    mch4 = str(raw.get("mch4Name") or "").strip()
    mch5 = str(raw.get("mch5Name") or "").strip()
    media_url = str(raw.get("mediaUrl") or "").strip()
    short_desc = clean_html(raw.get("shortDescription"))
    desc = clean_html(raw.get("description"))
    long_desc = clean_html(raw.get("longDescription"))

    full_desc = desc or short_desc or long_desc or name
    promotion = None
    if sale_price < price and price > 0:
        discount_vnd = price - sale_price
        pct = round((discount_vnd / price) * 100)
        promotion = f"Giảm {discount_vnd:,}đ ({pct}%)".replace(",", ".")

    shelf = map_shelf(mch1, mch2, category_name)

    return {
        "product_id": f"WIN_{item_no}" if item_no else f"WIN_{raw.get('id')}",
        "item_no": item_no,
        "barcode": barcode,
        "name": name,
        "brand": brand,
        "category_code": category_code,
        "category_name": category_name,
        "department": mch1,
        "sub_department": mch2,
        "category_hierarchy": " > ".join(filter(None, [mch1, mch2, mch3, mch4, mch5])),
        "price_vnd": price,
        "sale_price_vnd": sale_price,
        "discount_promotion": promotion,
        "stock": stock,
        "shelf": shelf,
        "uom": uom,
        "image_url": media_url,
        "description": full_desc,
        "seo_url": (
            f"https://winmart.vn/products/{raw.get('seoName')}" if raw.get("seoName") else ""
        ),
        "scraped_at": datetime.now(UTC).isoformat(),
    }


async def crawl_winmart(max_categories: int | None = None) -> list[dict[str, Any]]:
    unique_products: dict[str, dict[str, Any]] = {}
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
        slugs = await get_all_category_slugs(client)
        if max_categories:
            slugs = slugs[:max_categories]

        print(f"-> Bắt đầu cào dữ liệu từ {len(slugs)} danh mục...")

        for idx, slug in enumerate(slugs, start=1):
            first_resp = await fetch_category_page(client, sem, slug, 1)
            if not first_resp:
                continue

            paging = first_resp.get("paging", {})
            total_count = paging.get("totalCount", 0)
            total_pages = paging.get("totalPages", 1)
            cat_name = first_resp.get("data", {}).get("name", slug)

            items = first_resp.get("data", {}).get("items", [])
            for raw_item in items:
                prod = transform_product(raw_item)
                key = prod["item_no"] or prod["product_id"]
                if key not in unique_products:
                    unique_products[key] = prod

            if total_pages > 1:
                tasks = [
                    fetch_category_page(client, sem, slug, p)
                    for p in range(2, total_pages + 1)
                ]
                pages_data = await asyncio.gather(*tasks)
                for page_resp in pages_data:
                    if not page_resp:
                        continue
                    p_items = page_resp.get("data", {}).get("items", [])
                    for raw_item in p_items:
                        prod = transform_product(raw_item)
                        key = prod["item_no"] or prod["product_id"]
                        if key not in unique_products:
                            unique_products[key] = prod

            print(
                f"[{idx}/{len(slugs)}] {cat_name}: {total_count} SP "
                f"(Tổng hiện tại: {len(unique_products)} SP độc nhất)"
            )

    return list(unique_products.values())


def save_results(products: list[dict[str, Any]], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "winmart_products.json"
    csv_path = output_dir / "winmart_products.csv"

    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] Đã lưu {len(products)} sản phẩm vào: {json_path}")

    # Save CSV
    if products:
        keys = list(products[0].keys())
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(products)
        print(f"[OK] Đã lưu định dạng CSV vào: {csv_path}")


if __name__ == "__main__":
    max_cats = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        max_cats = int(sys.argv[1])
        print(f"-> Chế độ test: crawl tối đa {max_cats} danh mục.")

    data_dir = Path(__file__).resolve().parent / "data"
    products = asyncio.run(crawl_winmart(max_categories=max_cats))
    save_results(products, data_dir)
