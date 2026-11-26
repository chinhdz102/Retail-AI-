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


_UNVERIFIED_EXCLUSION_CUES = re.compile(
    r"\b(?:di ung|kich ung|gay|duong|calo|kcal|bao quan|gluten|lactose|cay|ga|gas|"
    r"caffeine|cafein|huong lieu|chat tao|khong tot|doc hai)\b"
)


def _strip_ranking_phrases(reference: str, category: str | None) -> str:
    """Remove non-modifier ranking/stock/price/top-N wrapper phrases before keyword matching."""
    cleaned = re.sub(
        r"\b\d+(?:[.,\s]\d{3})*(?:[.,]\d+)?\s*(?:k|ngan|nghin|dong|vnd|d)\b",
        " ",
        reference,
    )
    cleaned = re.sub(r"\btop\s+\d{1,2}\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:con\s+bao\s+nhieu(?:\s+(?:goi|chai|lon|hop|tuyp|cai|thung|loc))?(?:\s+trong\s+kho)?|"
        r"ton\s+kho\s+bao\s+nhieu|bao\s+nhieu\s+(?:goi|chai|lon|hop|tuyp|cai|tien)|"
        r"trong\s+kho|ton\s+kho|co\s+con\s+hang\s+khong|con\s+hang\s+khong|con\s+hang|"
        r"va\s+gia\s+bao\s+nhieu|gia\s+bao\s+nhieu|vua\s+ban\s+chay\s+vua\s+re|vua\s+re|"
        r"vua\s+duoc\s+danh\s+gia\s+cao(?:\s+nhat)?|danh\s+gia\s+cao(?:\s+nhat)?|"
        r"nhieu\s+nguoi\s+mua(?:\s+nhat)?|dat\s+khach(?:\s+nhat)?|hot\s+nhat|"
        r"hien\s+nay|hien\s+tai|luot\s+ban\s+(?:cao|nhieu)(?:\s+nhat)?|"
        r"cua\s+hang|sieu\s+thi|mat\s+hang|instant\s+noodles?|best\s+sellers?|"
        r"most\s+popular|which\s+is|is\s+the)\b",
        " ",
        cleaned,
    )
    norm_cat = normalize_category_text(category or "")
    if "snack" in norm_cat or "an vat" in norm_cat:
        cleaned = re.sub(r"\b(?:do\s+)?an\s+vat\b", " ", cleaned)
    if "nuoc" in norm_cat or "giai khat" in norm_cat or "co ga" in norm_cat:
        cleaned = re.sub(r"\b(?:nuoc\s+)?giai\s+khat\b", " ", cleaned)
    if "keo gum" in norm_cat:
        cleaned = re.sub(r"\bkeo\s+cao\s+su\b", " ", cleaned)
    return " ".join(cleaned.split())


def rank_demo_sales(
    repository: ProductRepository,
    query: QueryUnderstandingResult,
    category: str | None,
    selected_ids: tuple[str, ...] = (),
    sales_counts: Mapping[str, int] | None = None,
    *,
    allow_storewide: bool = False,
    top_n: int = 3,
    prefer_affordable: bool = False,
) -> tuple[DemoSalesItem, ...]:
    if sales_counts is None:
        from backend.services.demo_sales_store import get_demo_sales_store

        sales_counts = get_demo_sales_store().counts()
    if not category and not selected_ids and not allow_storewide:
        return ()
    brands = {normalize_category_text(brand) for brand in repository.list_brand_names(limit=1000)}
    excluded: set[str] = set()
    excluded_name_phrases: list[str] = []
    for constraint in query.negative_constraints:
        value = re.sub(
            r"^(?:(?:loai|hang|nhan|thuong hieu|cua|phai)\s+)+",
            "",
            normalize_category_text(constraint),
        ).strip()
        if not value:
            continue
        if value in brands:
            excluded.add(value)
        elif not _UNVERIFIED_EXCLUSION_CUES.search(value) and len(value.split()) <= 3:
            # Allow genuine sub-brand exclusions (e.g. "không phải Hảo Hảo" -> brand is Acecook
            # while "Hảo Hảo" is in product.name), while still failing closed on health/safety.
            excluded_name_phrases.append(value)
        else:
            return ()
    allowed_ambiguities = {"requested ranking data is not represented by the product catalog"}
    if allow_storewide:
        allowed_ambiguities.add("product or category is not explicit in this turn")
    unresolved = [reason for reason in query.ambiguity if reason not in allowed_ambiguities]
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
    raw_ref = remove_excluded_brand_phrases(query.product_reference, list(brands))
    for ex_phrase in excluded_name_phrases:
        raw_ref = re.sub(
            rf"\b(?:khong|ko|k|tru|bo|khong\s+phai|khong\s+lay)\s+{re.escape(ex_phrase)}\b",
            " ",
            normalize_category_text(raw_ref),
        )
    reference = _strip_ranking_phrases(normalize_category_text(raw_ref), category)
    # Ranking wrappers aren't product modifiers. Keep genuine modifiers (size,
    # material, variant) in the repository query so demo mode cannot erase them.
    fillers = set(
        "nao la loai cai mon san pham hang nhat nhut duoi tren toi da khong qua "
        "ban chay seller best most popular the one which within under budget "
        "gia tam khoang ngan sach trong nay minh toi t m cho giup voi "
        "tong cong ca don moi mot tung chiec mua lay cua brand "
        "o sieu thi cua hang shop ben day gioi thieu may vai ngon tot hay "
        "tu van goi y de xuat xem thu tim co khong nhe nha di a goi "
        "nhieu duoc luot thi v vay z zay the sao nhi ha ak ah ne nua "
        "kia do ko k dc oi em mai ban chi anh top va vua nhung qua gi".split()
    ) | set(normalize_category_text(category or "").split())
    if query.quantity:
        reference = re.sub(rf"\b{query.quantity}\b", " ", reference)
    search_query = " ".join(word for word in reference.split() if word not in fillers)
    if selected_ids and not category:
        products = [repository.get_product_snapshot(product_id) for product_id in selected_ids]
    elif not category and not search_query and not query.brand_preference and allow_storewide:
        repo_products = getattr(repository, "products", None)
        if repo_products is not None:
            products = list(repo_products)
        else:
            products = repository.search(
                query="",
                price_operator=price_operator,
                price_value_vnd=price_cap,
                limit=500,
            )
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
    # Confirm every sub-brand exclusion genuinely exists in the candidate pool before ranking;
    # otherwise fail closed so unverified non-brand constraints are never silently erased.
    for ex_phrase in excluded_name_phrases:
        if not any(
            product is not None and ex_phrase in normalize_category_text(product.name)
            for product in products
        ):
            return ()
    ranked = [
        DemoSalesItem(product, sales_counts[product.product_id])
        for product in products
        if product is not None
        and product.product_id in sales_counts
        and normalize_category_text(product.brand) not in excluded
        and all(
            ex_phrase not in normalize_category_text(product.name)
            for ex_phrase in excluded_name_phrases
        )
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
    if prefer_affordable and price_cap is None and len(ranked) > 3:
        prices = sorted(item.product.price_vnd for item in ranked)
        quartile_cap = prices[max(0, len(prices) // 4)]
        affordable_cap = min(25_000, quartile_cap) if prices[0] <= 25_000 else quartile_cap
        affordable = [item for item in ranked if item.product.price_vnd <= affordable_cap]
        if affordable:
            ranked = affordable
            ranked.sort(
                key=lambda item: (
                    -item.units_sold_30d,
                    item.product.price_vnd,
                    item.product.product_id,
                )
            )
            return tuple(ranked[: max(3, top_n)])
    ranked.sort(key=lambda item: (-item.units_sold_30d, item.product.product_id))
    return tuple(ranked[: max(3, top_n)])


def render_demo_sales(
    items: tuple[DemoSalesItem, ...],
    language: str,
    *,
    top_n: int = 1,
    include_stock: bool = False,
    storewide: bool = False,
) -> str:
    first = items[0]
    product = first.product
    snap_date = (
        product.data_timestamp.date().strftime("%d/%m/%Y") if product.data_timestamp else ""
    )
    if language == "en":
        if top_n > 1 and len(items) > 1:
            listed = "; ".join(
                f"{i}. {it.product.name} ({it.units_sold_30d} units/30d, "
                f"{it.product.price_vnd:,}đ)".replace(",", ".")
                for i, it in enumerate(items[:top_n], 1)
            )
            return f"Based on simulated 30-day demo sales, top items are: {listed}."
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
    scope_vi = "toàn cửa hàng" if storewide else "trong nhóm này"
    if top_n > 1 and len(items) > 1:
        shown = items[:top_n]
        entries = []
        for idx, it in enumerate(shown, 1):
            p = it.product
            price_s = f"{p.price_vnd:,}đ".replace(",", ".")
            stock_s = (
                f", snapshot tồn {p.stock} {p.uom or 'đơn vị'}" if include_stock else ""
            )
            entries.append(
                f"{idx}. {p.name} ({it.units_sold_30d} sản phẩm/30 ngày, "
                f"giá tham khảo {price_s}{stock_s})"
            )
        tail = (
            f" Lưu ý tồn kho ghi nhận theo snapshot ngày {snap_date}, tồn thực tế tại quầy "
            "chưa được xác minh."
            if include_stock and snap_date
            else ""
        )
        return (
            f"Theo số liệu bán mô phỏng (demo) {scope_vi}, top {len(shown)} bán chạy nhất là: "
            + "; ".join(entries)
            + "."
            + tail
        )
    price = f"{product.price_vnd:,}đ".replace(",", ".")
    base = (
        f"Theo số liệu bán mô phỏng (demo) {scope_vi}, {product.name} bán nhiều nhất: "
        f"{first.units_sold_30d} sản phẩm trong 30 ngày. "
        f"Giá tham khảo {price}."
    )
    if include_stock:
        date_vi = f" ngày {snap_date}" if snap_date else ""
        base += (
            f" Snapshot{date_vi} ghi nhận tồn {product.stock} {product.uom or 'đơn vị'} "
            "(tồn thực tế tại quầy hiện tại chưa được xác minh)."
        )
    return base
