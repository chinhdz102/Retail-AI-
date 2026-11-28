"""Deterministic snapshot stock ranking, inventory overviews, and sales/stock comparisons.

Every response transparently cites the catalog snapshot date and clarifies that
live physical shelf inventory has not been verified when ``stock_is_current`` is False.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.products.repository import ProductRepository
from backend.services.semantic_nlp_router import route_query

StockRankingMode = Literal["most", "least", "low", "out", "overview"]


@dataclass(frozen=True)
class StockRankingRequest:
    mode: StockRankingMode
    top_n: int
    category_hint: str | None


@dataclass(frozen=True)
class SalesOrStockComparisonRequest:
    metric: Literal["sales", "stock"]
    target_a: str
    target_b: str


_MOST_STOCK_RE = re.compile(
    r"\b(?:ton\s+kho\s+(?:nhieu|cao|lon)\s+nhat|"
    r"(?:con\s+)?(?:ton\s+kho|hang)\s+(?:nhieu|cao)\s+nhat|"
    r"top\s+\d+\s+(?:san\s+pham|mat\s+hang|mon|loai)?\s*ton\s+kho\s+(?:cao|nhieu)\s+nhat|"
    r"top\s+\d+\s+ton\s+kho|"
    r"ton\s+nhieu\s+nhat|nhieu\s+ton\s+kho\s+nhat|highest\s+stock|most\s+in\s+stock)\b"
)
_LEAST_STOCK_RE = re.compile(
    r"\b(?:ton\s+kho\s+(?:it|thap)\s+nhat|con\s+(?:ton\s+)?(?:it|thap)\s+nhat|"
    r"lowest\s+stock|least\s+stock)\b"
)
_LOW_STOCK_RE = re.compile(
    r"\b(?:sap\s+het(?:\s+hang)?|gan\s+het(?:\s+hang)?|low\s+stock|running\s+low)\b"
)
_OUT_OF_STOCK_OVERVIEW_RE = re.compile(
    r"\b(?:co\s+(?:san\s+pham|mat\s+hang|mon|loai)\s+nao\s+het\s+hang|"
    r"(?:san\s+pham|mat\s+hang|mon|loai)\s+nao\s+(?:da\s+|dang\s+)?het\s+hang|"
    r"nhung\s+(?:san\s+pham|mon)\s+nao\s+het\s+hang|"
    r"any\s+out\s+of\s+stock|which\s+(?:products?|items?)\s+(?:are\s+)?out\s+of\s+stock)\b"
)
_CATEGORY_OVERVIEW_RE = re.compile(
    r"\b(?:kiem\s+tra\s+ton\s+kho\s+(?:nhom|muc|danh\s+muc|hang)\s+(.+)|"
    r"ton\s+kho\s+(?:nhom|muc|danh\s+muc)\s+(.+)|"
    r"nhom\s+(.+?)\s+con\s+(?:nhung\s+)?(?:mon|loai|san\s+pham|mat\s+hang)\s+nao|"
    r"check\s+stock\s+(?:for|of)\s+(.+))\b"
)
_TOP_N_RE = re.compile(r"\btop\s+(\d{1,2})\b")
_COMPARE_METRIC_RE = re.compile(
    r"\bso\s+sanh\s+(?:(luot\s+ban|doanh\s+so|muc\s+ban\s+chay|ban\s+chay)|"
    r"(ton\s+kho|so\s+luong\s+ton|hang\s+ton))\s+(?:cua\s+)?(.+?)\s+"
    r"(?:va|voi|vs|versus)\s+(.+?)\s*[?.!]*$"
)


def _format_vnd(amount: int) -> str:
    return f"{int(amount):,}đ".replace(",", ".")


def _snapshot_date_str(products: tuple[ProductSnapshot, ...]) -> str:
    for p in products:
        if p.data_timestamp is not None:
            return p.data_timestamp.date().strftime("%d/%m/%Y")
    return ""


def _strip_stock_wrappers(text: str) -> str:
    norm = normalize_category_text(text)
    cleaned = re.sub(
        r"\b(?:kiem\s+tra|xem|cho\s+biet|cho\s+xem|top\s+\d+|"
        r"ton\s+kho|so\s+luong\s+ton|con\s+ton|sap\s+het\s+hang|sap\s+het|"
        r"gan\s+het\s+hang|het\s+hang|con\s+hang|nhieu\s+nhat|cao\s+nhat|"
        r"it\s+nhat|thap\s+nhat|san\s+pham|mat\s+hang|nhom|danh\s+muc|"
        r"cua\s+hang|sieu\s+thi|nhung|mon|loai|cai|nao|co|khong|ko|khum|"
        r"hien\s+tai|hien\s+nay|trong\s+kho|o\s+day)\b",
        " ",
        norm,
    )
    return " ".join(cleaned.split())


def parse_stock_ranking_request(text: str) -> StockRankingRequest | None:
    """Detect store-wide or category inventory ranking / overview requests."""
    norm = " ".join(normalize_category_text(text).split())
    if not norm:
        return None
    m_top = _TOP_N_RE.search(norm)
    requested_n = int(m_top.group(1)) if m_top else 0
    top_n = max(1, min(requested_n, 10)) if requested_n else 3

    mode: StockRankingMode | None = None
    cat_hint: str | None = None
    if _MOST_STOCK_RE.search(norm):
        mode = "most"
        top_n = max(1, min(requested_n, 10)) if requested_n else 3
    elif _LEAST_STOCK_RE.search(norm):
        mode = "least"
    elif _LOW_STOCK_RE.search(norm):
        mode = "low"
        top_n = max(1, min(requested_n, 10)) if requested_n else 5
    elif _OUT_OF_STOCK_OVERVIEW_RE.search(norm):
        mode = "out"
        top_n = max(1, min(requested_n, 10)) if requested_n else 5
    else:
        m_ov = _CATEGORY_OVERVIEW_RE.search(norm)
        if m_ov:
            mode = "overview"
            top_n = max(1, min(requested_n, 10)) if requested_n else 5
            cat_hint = next((g.strip() for g in m_ov.groups() if g and g.strip()), None)

    if mode is None:
        return None
    if not cat_hint:
        stripped = _strip_stock_wrappers(text)
        if stripped:
            cat_hint = stripped
    return StockRankingRequest(mode=mode, top_n=top_n, category_hint=cat_hint)


def _category_matches_product(product: ProductSnapshot, category: str) -> bool:
    target = normalize_category_text(category)
    if not target:
        return True
    segs = [
        normalize_category_text(seg)
        for seg in (product.category_hierarchy or product.category or "").split(">")
        if seg.strip()
    ]
    if target in segs:
        return True
    return any(target in seg for seg in segs)


def resolve_inventory_category(
    repository: ProductRepository,
    text: str,
    explicit_category: str | None,
    hint: str | None,
) -> str | None:
    """Resolve optional category scope for inventory queries without hallucinating."""
    if explicit_category:
        return explicit_category
    if not hint:
        return None
    norm_hint = normalize_category_text(hint)
    if not norm_hint or norm_hint in {"san pham", "mat hang", "cua hang", "sieu thi"}:
        return None
    # Match directly against official taxonomy segments (e.g. "bánh kẹo", "trái cây", "mì ăn liền")
    for path in repository.list_category_hierarchies():
        for seg in path:
            if normalize_category_text(seg) == norm_hint:
                return seg
    # Fall back to our sub-millisecond leaf semantic router on the clean hint phrase
    rt = route_query(hint, min_confidence=0.45)
    if rt is not None:
        return rt.categories[0]
    rt_full = route_query(text, min_confidence=0.55)
    if rt_full is not None:
        return rt_full.categories[0]
    return None


def rank_snapshot_stock(
    repository: ProductRepository,
    *,
    mode: StockRankingMode,
    category: str | None,
    top_n: int = 3,
) -> tuple[tuple[ProductSnapshot, ...], int, int]:
    """Deterministically rank/filter catalog items by snapshot stock.

    Returns ``(selected_products, zero_stock_count, low_stock_count)`` in scope.
    """
    raw_products: list[ProductSnapshot]
    repo_products = getattr(repository, "products", None)
    if repo_products is not None:
        raw_products = list(repo_products)
        if category:
            filtered = [p for p in raw_products if _category_matches_product(p, category)]
            raw_products = (
                filtered
                if filtered
                else repository.search(query="", category=category, limit=500)
            )
    else:
        raw_products = repository.search(query="", category=category, limit=500)

    if not raw_products:
        return (), 0, 0

    zero_items = sorted(
        (p for p in raw_products if p.stock <= 0),
        key=lambda p: (p.name.casefold(), p.product_id),
    )
    low_items = sorted(
        (p for p in raw_products if 0 < p.stock <= 5),
        key=lambda p: (p.stock, p.name.casefold(), p.product_id),
    )

    if mode == "most":
        ordered = sorted(raw_products, key=lambda p: (-p.stock, p.product_id))
        return tuple(ordered[:top_n]), len(zero_items), len(low_items)
    if mode == "least":
        ordered = sorted(raw_products, key=lambda p: (p.stock, p.product_id))
        return tuple(ordered[:top_n]), len(zero_items), len(low_items)
    if mode == "low":
        pool = (
            low_items
            if low_items
            else sorted(raw_products, key=lambda p: (p.stock, p.product_id))
        )
        return tuple(pool[:top_n]), len(zero_items), len(low_items)
    if mode == "out":
        if zero_items:
            return tuple(zero_items[:top_n]), len(zero_items), len(low_items)
        ordered = sorted(raw_products, key=lambda p: (p.stock, p.product_id))
        return tuple(ordered[:top_n]), 0, len(low_items)
    # overview: diversify by subcategory / stock availability
    overview = repository.search(
        query="",
        category=category,
        diversify_by_subcategory=True,
        limit=max(top_n, 5),
    )
    if not overview:
        overview = sorted(raw_products, key=lambda p: (-p.stock, p.product_id))[:top_n]
    return tuple(overview[:top_n]), len(zero_items), len(low_items)


def render_stock_ranking(
    products: tuple[ProductSnapshot, ...],
    *,
    mode: StockRankingMode,
    language: str,
    category: str | None = None,
    zero_count: int = 0,
    low_count: int = 0,
) -> str:
    """Format an epistemically honest stock ranking / overview answer."""
    if not products:
        if language == "en":
            return "No matching products were found in the current catalog snapshot."
        return "Hiện mình không tìm thấy sản phẩm nào thuộc nhóm đó trong snapshot catalog."

    snap_date = _snapshot_date_str(products)
    date_note_vi = f" ngày {snap_date}" if snap_date else ""
    scope_vi = f"trong nhóm {category}" if category else "toàn cửa hàng"

    entries = [
        f"{idx}. {p.name} (snapshot ghi nhận {p.stock} {p.uom or 'đơn vị'}, "
        f"giá tham khảo {_format_vnd(p.price_vnd)})"
        for idx, p in enumerate(products, 1)
    ]
    joined = "; ".join(entries)

    if language == "en":
        entries_en = [
            f"{idx}. {p.name} (snapshot: {p.stock} {p.uom or 'units'}, "
            f"ref. {_format_vnd(p.price_vnd)})"
            for idx, p in enumerate(products, 1)
        ]
        return (
            f"According to the catalog snapshot{f' ({snap_date})' if snap_date else ''}, "
            + "; ".join(entries_en)
            + ". Live shelf stock has not been verified and may differ in store."
        )

    disclaimer = (
        f"Lưu ý: đây là tồn kho ghi nhận theo snapshot{date_note_vi}, "
        "tồn thực tế tại quầy hiện tại chưa được xác minh."
    )
    if mode == "most":
        return (
            f"Theo snapshot{date_note_vi}, các mặt hàng tồn kho cao nhất {scope_vi}: "
            f"{joined}. {disclaimer}"
        )
    if mode == "least":
        return (
            f"Theo snapshot{date_note_vi}, các mặt hàng tồn kho thấp nhất {scope_vi}: "
            f"{joined}. {disclaimer}"
        )
    if mode == "low":
        if low_count > 0:
            return (
                f"Theo snapshot{date_note_vi}, {scope_vi} có {low_count} mặt hàng "
                f"ghi nhận tồn thấp (1–5 đơn vị), tiêu biểu: {joined}. {disclaimer}"
            )
        return (
            f"Theo snapshot{date_note_vi}, các mặt hàng tồn ghi nhận thấp nhất {scope_vi}: "
            f"{joined}. {disclaimer}"
        )
    if mode == "out":
        if zero_count > 0:
            return (
                f"Theo snapshot{date_note_vi}, {scope_vi} có {zero_count} mặt hàng ghi nhận tồn 0: "
                f"{joined}. {disclaimer}"
            )
        return (
            f"Theo snapshot{date_note_vi}, {scope_vi} chưa ghi nhận mặt hàng nào tồn bằng 0; "
            f"các mẫu tồn thấp nhất gồm: {joined}. {disclaimer}"
        )
    return (
        f"Theo snapshot{date_note_vi}, một số mặt hàng {scope_vi} kèm tồn ghi nhận: "
        f"{joined}. {disclaimer}"
    )


def render_stock_candidates(
    products: tuple[ProductSnapshot, ...],
    language: str,
) -> str:
    """Show snapshot stock per matching variant when customer asks availability for a brand/line."""
    shown = products[:4]
    snap_date = _snapshot_date_str(shown)
    if language == "en":
        items = ", ".join(
            f"{p.name} ({_format_vnd(p.price_vnd)}, snapshot stock: {p.stock} {p.uom or 'units'})"
            for p in shown
        )
        return (
            f"I found several matching items: {items}. "
            f"These stock counts are from the catalog snapshot"
            f"{f' ({snap_date})' if snap_date else ''}; live in-store stock is unverified. "
            "Which exact variant would you like to check?"
        )
    if language == "zh":
        items = "、".join(
            f"{p.name}（{_format_vnd(p.price_vnd)}，快照库存 {p.stock} {p.uom or '件'}）"
            for p in shown
        )
        return (
            f"我找到几种匹配商品：{items}。以上为目录快照库存，门店实时库存尚未核实。"
            "您想查询哪一款规格？"
        )
    date_vi = f" ngày {snap_date}" if snap_date else ""
    items = ", ".join(
        f"{p.name} ({_format_vnd(p.price_vnd)}, snapshot tồn {p.stock} {p.uom or 'đơn vị'})"
        for p in shown
    )
    return (
        f"Mình tìm thấy vài mặt hàng gần giống: {items}. "
        f"Lưu ý đây là số liệu tồn kho theo snapshot{date_vi}, "
        "tồn hiện tại tại quầy chưa được xác minh. "
        "Bạn muốn tra sản phẩm nào trong số này để mình kiểm tra cho đúng?"
    )


def parse_sales_or_stock_comparison(text: str) -> SalesOrStockComparisonRequest | None:
    """Detect explicit sales or stock comparisons between two named products/brands."""
    norm = " ".join(normalize_category_text(text).split())
    m = _COMPARE_METRIC_RE.search(norm)
    if not m:
        return None
    metric: Literal["sales", "stock"] = "sales" if m.group(1) else "stock"
    target_a = m.group(3).strip()
    target_b = m.group(4).strip()
    if not target_a or not target_b:
        return None
    # Inherit leading category noun from target_a onto target_b (e.g. "mì Hảo Hảo" vs "Omachi"
    # -> "mì Omachi") when target_b is a bare brand name.
    a_words = target_a.split()
    b_words = target_b.split()
    if len(a_words) >= 2 and len(b_words) == 1 and a_words[0] in {
        "mi", "sua", "nuoc", "tra", "bia", "banh", "keo", "dau", "tuong",
    }:
        target_b = f"{a_words[0]} {target_b}"
    return SalesOrStockComparisonRequest(metric=metric, target_a=target_a, target_b=target_b)


def render_sales_or_stock_comparison(
    req: SalesOrStockComparisonRequest,
    repository: ProductRepository,
    *,
    language: str,
    demo_sales_counts: Mapping[str, int] | None = None,
    sales_source: str = "SIMULATED",
) -> tuple[str, tuple[ProductSnapshot, ...], dict[str, int]] | None:
    """Deterministically compare 30-day sales or snapshot stock for two items."""
    hits_a = repository.search(query=req.target_a, limit=40)
    hits_b = repository.search(query=req.target_b, limit=40)
    if not hits_a or not hits_b:
        return None

    def _pick_best(
        hits: list[ProductSnapshot], exclude_id: str | None = None
    ) -> ProductSnapshot | None:
        pool = [p for p in hits if p.product_id != exclude_id]
        if not pool:
            return None
        if req.metric == "sales" and demo_sales_counts:
            pool.sort(key=lambda p: (-(demo_sales_counts.get(p.product_id, 0)), p.product_id))
            return pool[0]
        pool.sort(key=lambda p: (-p.stock, p.price_vnd, p.product_id))
        return pool[0]

    def _display_name(p: ProductSnapshot) -> str:
        if p.brand and normalize_category_text(p.brand) not in normalize_category_text(p.name):
            return f"{p.name} ({p.brand})"
        return p.name

    prod_a = _pick_best(hits_a)
    if prod_a is None:
        return None
    prod_b = _pick_best(hits_b, exclude_id=prod_a.product_id)
    if prod_b is None:
        return None

    name_a = _display_name(prod_a)
    name_b = _display_name(prod_b)

    if req.metric == "sales":
        if not demo_sales_counts:
            return None
        sa = demo_sales_counts.get(prod_a.product_id, 0)
        sb = demo_sales_counts.get(prod_b.product_id, 0)
        is_sim = sales_source == "SIMULATED"
        sim_tag = ""
        prefix = (
            "Theo số liệu bán 30 ngày"
            if is_sim
            else "Theo số liệu bán 30 ngày gần nhất"
        )
        if sa == sb:
            verdict = f"Hai mẫu này hiện có lượt bán{sim_tag} ngang nhau ({sa} sản phẩm/30 ngày)."
        else:
            leader_name = name_a if sa > sb else name_b
            verdict = f"Trong hai mẫu này, {leader_name} có lượt bán{sim_tag} cao hơn."
        reply = (
            f"{prefix}, đại diện bán chạy nhất của hai dòng là: "
            f"{name_a} ({sa} sản phẩm, giá {_format_vnd(prod_a.price_vnd)}) và "
            f"{name_b} ({sb} sản phẩm, giá {_format_vnd(prod_b.price_vnd)}). "
            f"{verdict}"
        )
        return reply, (prod_a, prod_b), {prod_a.product_id: sa, prod_b.product_id: sb}

    snap_date = _snapshot_date_str((prod_a, prod_b))
    date_vi = f" ngày {snap_date}" if snap_date else ""
    reply = (
        f"Theo tồn kho ghi nhận trong snapshot{date_vi}, mẫu tồn cao nhất của hai dòng là: "
        f"{name_a} ghi nhận {prod_a.stock} {prod_a.uom or 'đơn vị'} "
        f"(giá tham khảo {_format_vnd(prod_a.price_vnd)}); "
        f"{name_b} ghi nhận {prod_b.stock} {prod_b.uom or 'đơn vị'} "
        f"(giá tham khảo {_format_vnd(prod_b.price_vnd)}). "
        "Lưu ý đây là số liệu snapshot, tồn thực tế tại quầy hiện tại chưa được xác minh."
    )
    return reply, (prod_a, prod_b), {}
