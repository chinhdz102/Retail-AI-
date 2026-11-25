"""Deterministic synthetic sales for local demos, never a store-sales source."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from backend.products.catalog import ProductSnapshot, matches_price_constraint
from backend.products.category_taxonomy import normalize_category_text
from backend.products.repository import ProductRepository
from backend.services.catalog_preferences import remove_excluded_brand_phrases
from backend.services.query_understanding import QueryUnderstandingResult


@dataclass(frozen=True)
class DemoSalesItem:
    product: ProductSnapshot
    units_sold_30d: int


def simulated_units(product_id: str) -> int:
    digest = hashlib.sha256(f"retail-demo-sales-v1|{product_id}".encode()).digest()
    return 10 + int.from_bytes(digest[:4], "big") % 990


def rank_demo_sales(
    repository: ProductRepository,
    query: QueryUnderstandingResult,
    category: str | None,
    selected_ids: tuple[str, ...] = (),
    sales_counts: Mapping[str, int] | None = None,
) -> tuple[DemoSalesItem, ...]:
    if sales_counts is None:
        from backend.services.demo_sales_store import get_demo_sales_store

        sales_counts = get_demo_sales_store().counts()
    if not category and not selected_ids:
        return ()
    brands = {normalize_category_text(brand) for brand in repository.list_brand_names(limit=1000)}
    excluded: set[str] = set()
    for constraint in query.negative_constraints:
        value = re.sub(
            r"^(?:(?:loai|hang|nhan|thuong hieu|cua)\s+)+",
            "",
            normalize_category_text(constraint),
        )
        if value not in brands:
            return ()
        excluded.add(value)
    unresolved = [
        reason for reason in query.ambiguity
        if reason != "requested ranking data is not represented by the product catalog"
    ]
    if (query.clarification_needed and (not query.ambiguity or unresolved)) or query.use_case:
        return ()
    price_cap = query.budget_vnd
    if price_cap is not None and query.budget_scope == "total":
        if not query.quantity:
            return ()
        price_cap = (price_cap - (query.budget_operator == "LT")) // query.quantity
        price_operator = "LTE"
    else:
        price_operator = query.budget_operator
    reference = normalize_category_text(
        remove_excluded_brand_phrases(query.product_reference, list(brands))
    )
    # Ranking wrappers aren't product modifiers. Keep genuine modifiers (size,
    # material, variant) in the repository query so demo mode cannot erase them.
    reference = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:k|ngan|nghin|dong|vnd)\b", " ", reference)
    fillers = set(
        "nao la loai cai mon san pham hang nhat nhut duoi tren toi da khong qua "
        "ban chay seller best most popular the one which within under budget "
        "gia tam khoang ngan sach trong nay minh toi t m cho giup voi "
        "tong cong ca don moi mot tung chiec mua lay cua brand "
        "o sieu thi cua hang shop ben day gioi thieu may vai ngon tot hay "
        "tu van goi y de xuat xem thu tim co khong nhe nha di a goi "
        "nhieu duoc luot thi v vay z zay the sao nhi ha ak ah ne nua "
        "kia do ko k dc oi em mai ban chi anh".split()
    ) | set(normalize_category_text(category).split())
    if query.quantity:
        reference = re.sub(rf"\b{query.quantity}\b", " ", reference)
    search_query = " ".join(word for word in reference.split() if word not in fillers)
    if selected_ids and not category:
        products = [repository.get_product_snapshot(product_id) for product_id in selected_ids]
    else:
        products = []
        seen: set[str] = set()
        for offset in range(0, 10_000, 20):
            page = repository.search(
                query=search_query,
                category=category,
                brand=query.brand_preference,
                price_operator=price_operator,
                price_value_vnd=price_cap,
                limit=20,
                offset=offset,
            )
            if any(product.product_id in seen for product in page):
                # A broken pagination source must not produce a false winner.
                return ()
            seen.update(product.product_id for product in page)
            products.extend(page)
            if len(page) < 20:
                break
        else:
            return ()
    ranked = [
        DemoSalesItem(product, sales_counts[product.product_id])
        for product in products
        if product is not None
        and product.product_id in sales_counts
        and normalize_category_text(product.brand) not in excluded
        and (not product.stock_is_current or product.sellable_stock > 0)
        and matches_price_constraint(product.price_vnd, price_operator, price_cap)
        and (
            not query.brand_preference
            or normalize_category_text(product.brand)
            == normalize_category_text(query.brand_preference)
        )
        and set(search_query.split())
        <= set(
            normalize_category_text(
                f"{product.name} {product.brand} {product.category_hierarchy}"
            ).split()
        )
    ]
    ranked.sort(key=lambda item: (-item.units_sold_30d, item.product.product_id))
    return tuple(ranked[:3])


def render_demo_sales(items: tuple[DemoSalesItem, ...], language: str) -> str:
    first = items[0]
    product = first.product
    if language == "en":
        return (
            f"Based on simulated demo sales in the last 30 days, {product.name} leads this "
            f"selection with {first.units_sold_30d} units sold. "
            "Would you like its price or suggested shelf location?"
        )
    if language == "zh":
        return (
            f"根据近30天演示销量数据，{product.name}以{first.units_sold_30d}件排名第一。"
            "您想查看价格还是建议货架位置？"
        )
    price = f"{product.price_vnd:,}đ".replace(",", ".")
    return (
        f"Theo số liệu bán mô phỏng (demo) trong nhóm này, {product.name} bán nhiều nhất: "
        f"{first.units_sold_30d} sản phẩm trong 30 ngày. "
        f"Giá tham khảo {price}."
    )
