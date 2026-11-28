"""Context-aware multi-turn working-basket, unit-comparison & food-safe streaming helpers.

Keeps Turn 2+ follow-ups, basket arithmetic, unit-size comparisons, frozen-category gaps,
and constrained zero-hit queries fast (<1.5s), strictly grounded in the active session
context, and immune to cross-department jumps or raw JSON leaks.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.products.repository import ProductRepository
from backend.services.bounded_work import run_catalog_work

_BULK_UOMS = frozenset({"thung", "loc", "goi 6", "khay 6", "hop 6"})
_BULK_NAME_RE = re.compile(
    r"\b(?:thung|loc\s+\d+|goi\s+6|24\s*(?:lon|chai|hop|goi)|12\s*(?:lon|chai|hop)|"
    r"30\s*goi|20\s*goi|10\s*goi|6\s*(?:lon|chai))\b"
)
_WANTS_BULK_RE = re.compile(r"\b(?:thung|loc|si|so luong lon|gia dinh lon|carton)\b")

_FOOD_OR_DRINK_NEED_RE = re.compile(
    r"\b(?:an vat|an nhe|an sang|an trua|an toi|an ngay|an lien|an kieng|an chay|"
    r"do an|mon an|thuc an|do uong|nuoc uong|giai khat|uong|dang doi|doi bung|"
    r"xem phim|lien hoan|tiec|picnic|di xa|chuyen di|mang di hoc|lo vi song|"
    r"tinh tao|do buon ngu|buon ngu|khong cay|khong gas|it duong|it ngot|it calo|"
    r"banh|keo|snack|sua|mi |pho |chao |tra |ca phe|nuoc ngot|nuoc suoi|nuoc ep)\b"
)
_READY_TO_EAT_NEED_RE = re.compile(
    r"\b(?:an ngay|an lien|khong can che bien|khong co lo vi song|mang di hoc|"
    r"xem phim|an vat|an nhe|chuyen di|di xa|lien hoan|an kieng|dang doi|doi bung|"
    r"cho (?:hai|ba|bon|2|3|4) nguoi|nhom ban|bua sang|an sang)\b"
)
_NON_READY_FOOD_RE = re.compile(
    r"\b(?:bot nang|bot mi|bot banh|bot gao|bot nep|bot bap|bot chien|"
    r"bong ca|da ca|dau an|dau thuc vat|bo thuc vat|nuoc mam|hat nem|muoi an|"
    r"sua bot|grow plus|pediasure|nan optipro|similac|ensure|enfamil|"
    r"mi chinh|bot ngot|tieu xay|gia vi|hanh tim|toi cu|cu sa|rau muong|"
    r"thit tuoi|thit heo|thit bo|ca tuoi|ga tuoi|tom tuoi)\b"
)
_SPICY_TITLE_RE = re.compile(
    r"\b(?:cay|spicy|samyang|kim chi|tomyum|tom yum|sa te|sate|ot|chua cay|han quoc)\b"
)
_BITTER_COFFEE_RE = re.compile(r"\b(?:ca phe|cafe|coffee|den da|espresso|americano)\b")
_CARBONATED_RE = re.compile(
    r"\b(?:co ga|co gas|coca|pepsi|sprite|fanta|7up|mirinda|soda|sting|red bull|"
    r"monster|thums up|warrior|number 1|nuoc ngot|tang luc)\b"
)
_DAIRY_MILK_RE = re.compile(
    r"\b(?:sua tuoi|sua tiet trung|sua thanh trung|sua bot|sua dac|sua chua|vinamilk|"
    r"th true milk|dalat milk|devondale|co gai ha lan|meiji)\b"
)
_PURE_SITUATIONAL_NO_NOUN_RE = re.compile(
    r"^(?:toi |minh |em |anh |chi )?"
    r"(?:dang doi|muon mua do an|goi y do an|can do uong|toi dang an kieng|"
    r"toi muon mua mot mon qua|toi co \d|tim nhung san pham an lien|"
    r"goi y bua sang|goi y do an|tim combo|co gi uong cho do buon ngu|"
    r"mua nuoc suoi chai lon|mua mi theo thung|co loai nuoc nao tuong tu|"
    r"co loai banh nao tuong tu|neu het mi )"
)
_QTY_WORD_MAP = {
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "nam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
    "muoi": 10,
}


def format_vnd(amount: int) -> str:
    return f"{int(amount):,}đ".replace(",", ".")


def is_bulk_pack(item: ProductSnapshot) -> bool:
    norm_uom = normalize_category_text(item.uom or "")
    if norm_uom in _BULK_UOMS:
        return True
    norm_name = normalize_category_text(item.name)
    return bool(_BULK_NAME_RE.search(norm_name))


def wants_bulk_pack(text: str) -> bool:
    return bool(_WANTS_BULK_RE.search(normalize_category_text(text)))


def is_food_or_drink_need(text: str) -> bool:
    return bool(_FOOD_OR_DRINK_NEED_RE.search(normalize_category_text(text)))


def filter_edible_candidates(
    items: list[ProductSnapshot],
    *,
    ready_to_eat: bool = False,
    allow_milk: bool = True,
    allow_spicy: bool = True,
    allow_carbonated: bool = True,
    allow_bitter: bool = True,
) -> list[ProductSnapshot]:
    """Filter out non-food departments and inappropriate raw/baby/excluded items."""
    out: list[ProductSnapshot] = []
    for p in items:
        dept = normalize_category_text(p.department or "")
        hier = normalize_category_text(f"{p.category_hierarchy or ''} {p.category or ''}")
        name = normalize_category_text(p.name)
        if (
            dept == "phi thuc pham"
            or "hoa my pham" in hier
            or "cham soc" in hier
            or "gia dung" in hier
        ):
            continue
        if ready_to_eat and _NON_READY_FOOD_RE.search(f"{name} {hier}"):
            continue
        if not allow_milk and _DAIRY_MILK_RE.search(f"{name} {hier}"):
            continue
        if not allow_spicy and (p.spicy is True or _SPICY_TITLE_RE.search(name)):
            continue
        if not allow_carbonated and (
            _CARBONATED_RE.search(name)
            or ("nuoc ngot co ga" in hier and "khong ga" not in name and "khong gas" not in name)
        ):
            continue
        if not allow_bitter and _BITTER_COFFEE_RE.search(f"{name} {hier}"):
            continue
        out.append(p)
    return out


def sort_single_unit_first(
    items: list[ProductSnapshot],
    text: str = "",
    *,
    cheaper_first: bool = False,
    promo_first: bool = False,
) -> list[ProductSnapshot]:
    allow_bulk = wants_bulk_pack(text)
    has_single = any(not is_bulk_pack(p) for p in items)

    def _key(p: ProductSnapshot) -> tuple[int, float, int, str]:
        bulk_pen = 1 if (not allow_bulk and has_single and is_bulk_pack(p)) else 0
        disc = float(_product_discount_pct(p) or 0)
        if promo_first:
            return (bulk_pen, -disc, p.price_vnd, p.name)
        if cheaper_first:
            return (bulk_pen, float(p.price_vnd), -int(disc), p.name)
        return (bulk_pen, -disc if disc >= 10 else 0.0, p.price_vnd, p.name)

    return sorted(items, key=_key)


def build_plain_prompt_text(
    *,
    customer_request: str,
    primary_products: list[ProductSnapshot],
    related_products: list[ProductSnapshot],
    excluded_terms: list[str] | None = None,
    mode: str = "recommend",
    budget_vnd: int | None = None,
    extra_note: str = "",
) -> str:
    """Format grounded facts in plain Vietnamese lines (never raw JSON) so models never leak
    JSON syntax or misread unformatted integer prices like 15900 -> 159.000đ.
    """
    force_base_dims = _batch_base_unit_dims([*primary_products, *related_products])
    lines = [f"Yêu cầu của khách: {customer_request}"]
    if budget_vnd is not None:
        lines.append(f"Ngân sách khách nêu: {format_vnd(budget_vnd)}")
    if excluded_terms:
        lines.append(f"Khách muốn tránh: {', '.join(excluded_terms)}")
    if extra_note:
        lines.append(f"Lưu ý bắt buộc: {extra_note}")
    lines.append("SẢN PHẨM GỢI Ý CHÍNH (phải giới thiệu ĐÚNG sản phẩm và ĐÚNG giá sau):")
    for idx, p in enumerate(primary_products, 1):
        parts = [f"{idx}. {p.name}", f"Giá bán: {format_vnd(p.price_vnd)}"]
        if p.uom:
            parts.append(f"Quy cách: {p.uom}")
        if p.list_price_vnd and p.list_price_vnd > p.price_vnd:
            parts.append(f"Giá gốc: {format_vnd(p.list_price_vnd)}")
        disc = _product_discount_pct(p)
        if disc:
            parts.append(f"Giảm {disc}%")
        unit_info = _unit_price_info(p, force_base_unit=bool(force_base_dims))
        if unit_info is not None:
            parts.append(f"Đơn giá quy đổi: {unit_info[1]}")
        lines.append(" - " + " | ".join(parts))
    if mode == "basket" and primary_products:
        total = sum(p.price_vnd for p in primary_products)
        lines.append(f"Tổng tiền bộ gợi ý: {format_vnd(total)}")
    if related_products:
        rel_str = "; ".join(f"{p.name} ({format_vnd(p.price_vnd)})" for p in related_products[:3])
        lines.append(f"Sản phẩm cùng loại khác bên dưới: {rel_str}")
    return "\n".join(lines)


def _extract_qty(norm_text: str, default: int = 1) -> int:
    m = re.search(
        r"\b(\d{1,2})\s*(?:lon|chai|goi|hop|cai|bich|tui|ly|thung|loc|mon|loai)?\b", norm_text
    )
    if m:
        val = int(m.group(1))
        if 1 <= val <= 50:
            return val
    for word, num in _QTY_WORD_MAP.items():
        if re.search(rf"\b{word}\s+(?:lon|chai|goi|hop|cai|bich|tui|ly)\b", norm_text):
            return num
    return default


def _parse_budget_vnd(norm_text: str) -> int | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:k|nghin|ngan)\b", norm_text)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1000)
    m2 = re.search(r"(\d{1,3}(?:[.,]\d{3})+)\s*(?:d|dong|vnd)?\b", norm_text)
    if m2:
        return int(m2.group(1).replace(".", "").replace(",", ""))
    return None


def _extract_volume_ml(name: str) -> float | None:
    norm = normalize_category_text(name)
    m_l = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:l|lit)\b", norm)
    if m_l:
        return float(m_l.group(1).replace(",", ".")) * 1000.0
    m_ml = re.search(r"(\d+(?:[.,]\d+)?)\s*ml\b", norm)
    if m_ml:
        return float(m_ml.group(1).replace(",", "."))
    m_kg = re.search(r"(\d+(?:[.,]\d+)?)\s*kg\b", norm)
    if m_kg:
        return float(m_kg.group(1).replace(",", ".")) * 1000.0
    m_g = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:g|gr)\b", norm)
    if m_g:
        return float(m_g.group(1).replace(",", "."))
    return None


def _hydrate_basket(
    raw_basket: list[dict[str, Any]] | None,
    repository: ProductRepository,
    fallback_visible: list[ProductSnapshot],
) -> list[tuple[ProductSnapshot, int]]:
    items: list[tuple[ProductSnapshot, int]] = []
    if isinstance(raw_basket, list):
        for entry in raw_basket:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("product_id") or "")
            qty = max(1, int(entry.get("qty") or 1))
            prod = repository.get_product_snapshot(pid) if pid else None
            if prod is not None:
                items.append((prod, qty))
    if not items and fallback_visible:
        items = [(p, 1) for p in fallback_visible[:3]]
    return items


def _serialize_basket(items: list[tuple[ProductSnapshot, int]]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": p.product_id,
            "name": p.name,
            "price_vnd": p.price_vnd,
            "qty": qty,
        }
        for p, qty in items
    ]


def _make_memory(
    previous_memory: dict,
    *,
    need: str,
    primary_products: list[ProductSnapshot],
    basket: list[tuple[ProductSnapshot, int]] | None = None,
    budget_vnd: int | None = None,
    last_action: str = "recommend",
    extra_excluded: list[str] | None = None,
) -> dict:
    mem = dict(previous_memory)
    mem["need"] = need[:500]
    mem["last_action"] = last_action
    mem["last_question"] = ""
    mem["pending_question"] = None
    mem["selection"] = {"picks": [{"candidate": i} for i in range(len(primary_products))]}
    if basket is not None:
        mem["working_basket"] = _serialize_basket(basket)
    elif primary_products:
        mem["working_basket"] = _serialize_basket([(p, 1) for p in primary_products])
    if budget_vnd is not None:
        mem["working_budget_vnd"] = int(budget_vnd)
    elif "working_budget_vnd" in previous_memory:
        mem["working_budget_vnd"] = previous_memory["working_budget_vnd"]
    recent_ids = list(previous_memory.get("recent_product_ids") or [])
    for p in primary_products:
        if p.product_id in recent_ids:
            recent_ids.remove(p.product_id)
        recent_ids.insert(0, p.product_id)
    mem["recent_product_ids"] = recent_ids[:8]
    existing_ex = list(previous_memory.get("session_exclusions") or [])
    for ex in extra_excluded or []:
        if ex and ex not in existing_ex:
            existing_ex.append(ex)
    if existing_ex:
        mem["session_exclusions"] = existing_ex
    return mem


def _direct_plan(
    *,
    reply: str,
    primary_products: list[ProductSnapshot],
    related_products: list[ProductSnapshot],
    memory: dict,
) -> StreamingConsultationPlan:
    return StreamingConsultationPlan(
        products=primary_products,
        related_products=related_products,
        memory=memory,
        messages=[],
        fallback_text=reply,
        direct_reply=reply,
    )


def _format_basket_summary(
    basket: list[tuple[ProductSnapshot, int]],
    budget_vnd: int | None = None,
) -> tuple[str, int]:
    total = sum(p.price_vnd * qty for p, qty in basket)
    parts = [f"{qty} × {p.name} ({format_vnd(p.price_vnd * qty)})" for p, qty in basket]
    body = "; ".join(parts)
    if budget_vnd is not None:
        rem = budget_vnd - total
        if rem >= 0:
            return (
                f"{body}. Tổng cộng là {format_vnd(total)}, còn dư {format_vnd(rem)} "
                f"so với ngân sách {format_vnd(budget_vnd)} nhé.",
                total,
            )
        return (
            f"{body}. Tổng cộng là {format_vnd(total)}, đang vượt ngân sách "
            f"{format_vnd(budget_vnd)} là {format_vnd(-rem)} nhé.",
            total,
        )
    return f"{body}. Tổng cộng là {format_vnd(total)} nhé.", total


async def prepare_special_turn1_streaming(
    *,
    text: str,
    answer_text: str,
    norm_answer: str,
    repository: ProductRepository,
    previous_memory: dict,
    parsed,
    build_payload: Callable[..., StreamingConsultationPlan],
) -> StreamingConsultationPlan | None:
    """Fast-lane handlers for frozen ice cream gap, unit-size comparisons, brand alternatives,
    explicit multi-item quantity combos, and zero-hit attribute queries (<1.2s, 0 crashes).
    """
    budget = parsed.budget_vnd if parsed is not None else _parse_budget_vnd(norm_answer)

    # 1. Frozen ice cream gap: Thanh Lam Mart snapshot only has whipping cream
    #    & personal care creams.
    if re.search(
        r"^(?:co\s+(?:nhung\s+)?loai\s+kem\s+nao|cua\s+hang\s+co\s+(?:ban\s+)?kem\s+(?:khong|nao)|"
        r"co\s+kem\s+an\s+khong|kem\s+an\s+co\s+loai\s+nao)\b",
        norm_answer,
    ) and not re.search(
        r"\b(?:danh rang|chong nang|duong da|cao rau|whipping|sua tuoi|lam banh)\b",
        norm_answer,
    ):
        whipping = await run_catalog_work(
            repository.search, query="kem sua tuoi", limit=4, timeout_seconds=3
        )
        reply = (
            "Dạ, hiện tại danh mục snapshot siêu thị chưa có nhóm kem ăn đông lạnh "
            "(kem que, kem hộp ăn liền). Trong hệ thống hiện chỉ có kem sữa tươi làm bánh "
            "(whipping cream) và các loại kem chăm sóc cá nhân (kem dưỡng da, kem chống nắng, "
            "kem đánh răng) — bạn muốn mình gợi ý nhóm nào không ạ?"
        )
        mem = _make_memory(previous_memory, need=text, primary_products=whipping[:1])
        return _direct_plan(
            reply=reply,
            primary_products=whipping[:1],
            related_products=whipping[1:4],
            memory=mem,
        )

    # 2. Explicit unit-size economics comparisons ("chai lớn hay chai nhỏ", "thùng hay gói").
    if re.search(
        r"\b(?:chai lon hay chai nho|lon hay nho|theo thung hay theo goi|thung hay goi)\b",
        norm_answer,
    ):
        is_noodle = bool(re.search(r"\b(?:mi|my)\b", norm_answer))
        search_q = "mi an lien" if is_noodle else "nuoc khoang"
        hits = await run_catalog_work(
            repository.search, query=search_q, limit=40, timeout_seconds=3
        )
        if is_noodle:
            packs = [p for p in hits if is_bulk_pack(p)]
            singles = [p for p in hits if not is_bulk_pack(p)]
            if packs and singles:
                p_bulk = sorted(packs, key=lambda p: p.price_vnd)[0]
                p_single = sorted(singles, key=lambda p: p.price_vnd)[0]
                primary = [p_bulk, p_single]
                related = _related_candidates(hits, primary, limit=3)
                mem = _make_memory(
                    previous_memory, need=text, primary_products=primary, last_action="compare"
                )
                return build_payload(
                    text=text,
                    primary_products=primary,
                    related_products=related,
                    repository=repository,
                    sources={p_bulk.product_id: [0], p_single.product_id: [1]},
                    memory=mem,
                    plan_for_fallback=ConsultationDecision(action="compare", need=text[:400]),
                    mode="compare",
                )
        else:
            singles = [p for p in hits if not is_bulk_pack(p) and _extract_volume_ml(p.name)]
            if len(singles) >= 2:
                singles.sort(key=lambda p: _extract_volume_ml(p.name) or 0)
                small_p = singles[0]
                large_p = singles[-1]
                if small_p.product_id != large_p.product_id:
                    primary = [large_p, small_p]
                    related = _related_candidates(singles, primary, limit=3)
                    mem = _make_memory(
                        previous_memory, need=text, primary_products=primary, last_action="compare"
                    )
                    return build_payload(
                        text=text,
                        primary_products=primary,
                        related_products=related,
                        repository=repository,
                        sources={large_p.product_id: [0], small_p.product_id: [1]},
                        memory=mem,
                        plan_for_fallback=ConsultationDecision(action="compare", need=text[:400]),
                        mode="compare",
                    )

    # 3. Brand/product substitute queries ("tương tự Sting nhưng rẻ hơn", "thay thế mì Hảo Hảo",
    # "tương tự Oreo").
    sub_match = re.search(
        r"\b(?:tuong tu|thay the|giong)\s+(?:nhu\s+)?([a-z0-9\s]{2,28}?)(?:\s+(?:nhung|ma|thi|khong|ko|re hon|it duong)|$)",  # noqa: E501
        norm_answer,
    )
    if sub_match and not re.search(r"\b(?:loai nay|cai nay|chai nay|mon nay)\b", norm_answer):
        ref_brand = sub_match.group(1).strip()
        if ref_brand and len(ref_brand) >= 2:
            cheaper = bool(re.search(r"\b(?:re hon|gia mem|thap hon)\b", norm_answer))
            if "sting" in ref_brand:
                cat_q = "nuoc tang luc"
            elif "hao hao" in ref_brand or "mi " in ref_brand:
                cat_q = "mi an lien"
            elif "oreo" in ref_brand:
                cat_q = "banh quy"
            else:
                cat_q = ref_brand
            hits = await run_catalog_work(
                repository.search, query=cat_q, limit=30, timeout_seconds=3
            )
            others = [
                p
                for p in hits
                if ref_brand.split()[-1] not in normalize_category_text(f"{p.name} {p.brand or ''}")
            ]
            pool = others or hits
            pool = sort_single_unit_first(pool, text, cheaper_first=cheaper)
            if pool:
                primary = pool[:1]
                related = _related_candidates(pool, primary, limit=4)
                mem = _make_memory(previous_memory, need=text, primary_products=primary)
                return build_payload(
                    text=text,
                    primary_products=primary,
                    related_products=related,
                    repository=repository,
                    sources={primary[0].product_id: [0]},
                    memory=mem,
                    plan_for_fallback=ConsultationDecision(action="recommend", need=text[:400]),
                    mode="recommend",
                )

    # 4. Explicit multi-item quantity combo ("5 chai nước ngọt và 3 gói bánh, tổng dưới 100.000đ").
    qty_combo = re.search(
        r"\b(\d{1,2})\s*(?:chai|lon)\s+([a-z\s]{3,25}?)\s+va\s+(\d{1,2})\s*(?:goi|hop|cai)\s+([a-z\s]{3,25}?)(?:[,.]|$|\btong\b|\bduoi\b)",  # noqa: E501
        norm_answer,
    )
    if qty_combo:
        q1, item1_q, q2, item2_q = (
            int(qty_combo.group(1)),
            qty_combo.group(2).strip(),
            int(qty_combo.group(3)),
            qty_combo.group(4).strip(),
        )
        hits1 = await run_catalog_work(
            repository.search, query=item1_q, limit=25, timeout_seconds=3
        )
        hits2 = await run_catalog_work(
            repository.search, query=item2_q, limit=25, timeout_seconds=3
        )
        hits1 = sort_single_unit_first(
            filter_edible_candidates(hits1, ready_to_eat=True), text, cheaper_first=True
        )
        hits2 = sort_single_unit_first(
            filter_edible_candidates(hits2, ready_to_eat=True), text, cheaper_first=True
        )
        if hits1 and hits2:
            chosen_pair: tuple[ProductSnapshot, ProductSnapshot] | None = None
            for p1 in hits1[:8]:
                for p2 in hits2[:8]:
                    tot = q1 * p1.price_vnd + q2 * p2.price_vnd
                    if budget is None or tot <= budget:
                        chosen_pair = (p1, p2)
                        break
                if chosen_pair:
                    break
            p1, p2 = chosen_pair or (hits1[0], hits2[0])
            tot = q1 * p1.price_vnd + q2 * p2.price_vnd
            basket = [(p1, q1), (p2, q2)]
            summary, _ = _format_basket_summary(basket, budget)
            reply = f"Dạ, mình gợi ý bộ phù hợp cho bạn: {summary}"
            mem = _make_memory(
                previous_memory,
                need=text,
                primary_products=[p1, p2],
                basket=basket,
                budget_vnd=budget,
            )
            return _direct_plan(
                reply=reply,
                primary_products=[p1, p2],
                related_products=_related_candidates([*hits1[:3], *hits2[:3]], [p1, p2], limit=4),
                memory=mem,
            )

    # 5. Explicit nutrient-ranking question where Thanh Lam Mart lacks nutrition tables
    # ("protein cao nhất", "ít calo").
    if re.search(r"\b(?:protein cao nhat|nhieu protein nhat|it calo|it calories)\b", norm_answer):
        is_milk = "sua" in norm_answer
        is_snack = bool(re.search(r"\b(?:an vat|snack|banh)\b", norm_answer))
        q_core = (
            "sua tuoi khong duong" if is_milk else ("banh an kieng" if is_snack else "yen mach")
        )
        hits = await run_catalog_work(repository.search, query=q_core, limit=18, timeout_seconds=3)
        hits = sort_single_unit_first(filter_edible_candidates(hits, ready_to_eat=True), text)
        if not hits and is_snack:
            hits = await run_catalog_work(
                repository.search, query="banh gao", limit=12, timeout_seconds=3
            )
            hits = sort_single_unit_first(filter_edible_candidates(hits, ready_to_eat=True), text)
        if hits:
            primary = hits[:1]
            related = _related_candidates(hits, primary, limit=4)
            metric_label = "hàm lượng protein" if "protein" in norm_answer else "lượng calo"
            note = (
                f"BẮT BUỘC nói rõ: catalog chưa có bảng số liệu {metric_label} chi tiết để xếp hạng tuyệt đối, "  # noqa: E501
                "nên mình gợi ý mẫu phù hợp nhóm khách hỏi và nhắc khách kiểm tra bảng dinh dưỡng trên bao bì."  # noqa: E501
            )
            mem = _make_memory(
                previous_memory, need=text, primary_products=primary, budget_vnd=budget
            )
            return build_payload(
                text=text,
                primary_products=primary,
                related_products=related,
                repository=repository,
                sources={primary[0].product_id: [0]},
                memory=mem,
                plan_for_fallback=ConsultationDecision(action="recommend", need=text[:400]),
                mode="recommend",
                extra_note=note,
            )

    return None


async def prepare_multiturn_streaming(
    *,
    text: str,
    answer_text: str,
    norm_answer: str,
    visible: list[ProductSnapshot],
    repository: ProductRepository,
    previous_memory: dict,
    parsed,
    build_payload: Callable[..., StreamingConsultationPlan],
    active_category_hint: str | None = None,
) -> StreamingConsultationPlan | None:
    """Path M: Stateful multi-turn working-basket & context-locked follow-up controller."""
    has_context = bool(
        previous_memory.get("need")
        or previous_memory.get("working_basket")
        or previous_memory.get("recent_product_ids")
        or visible
        or active_category_hint
    )
    if not has_context:
        return None

    basket = _hydrate_basket(previous_memory.get("working_basket"), repository, visible)
    budget_vnd: int | None = previous_memory.get("working_budget_vnd")
    turn_budget = (parsed.budget_vnd if parsed is not None else None) or _parse_budget_vnd(
        norm_answer
    )
    if turn_budget is not None and re.search(
        r"\b(?:ngan sach|giam ngan sach|tong cong|duoi|chi con|con)\b", norm_answer
    ):
        budget_vnd = turn_budget
    elif budget_vnd is None and turn_budget is not None:
        budget_vnd = turn_budget

    recent_products: list[ProductSnapshot] = []
    for pid in previous_memory.get("recent_product_ids") or []:
        p = repository.get_product_snapshot(str(pid))
        if p is not None:
            recent_products.append(p)
    for p in visible:
        if all(r.product_id != p.product_id for r in recent_products):
            recent_products.append(p)

    # M1. Exact arithmetic / basket summary ("Tổng hết bao nhiêu tiền rồi?", "Tính tổng tiền",
    # "Danh sách mua hàng có gì?", "Chốt lại...")
    if re.search(
        r"\b(?:tong het bao nhieu|tong tien|tinh tong|danh sach mua hang|chot lai danh sach|"
        r"tong cong bao nhieu|het bao nhieu tien roi)\b",
        norm_answer,
    ) and not re.search(r"\b(?:duoi|khong qua)\s+\d", norm_answer):
        if basket:
            summary, _ = _format_basket_summary(basket, budget_vnd)
            prods = [p for p, _ in basket]
            mem = _make_memory(
                previous_memory,
                need=previous_memory.get("need") or text,
                primary_products=prods,
                basket=basket,
                budget_vnd=budget_vnd,
            )
            return _direct_plan(
                reply=f"Dạ, danh sách tạm chọn hiện tại của bạn gồm: {summary}",
                primary_products=prods,
                related_products=[],
                memory=mem,
            )

    # M2. Remaining budget ("Tôi còn bao nhiêu tiền?")
    if re.search(r"\b(?:con bao nhieu tien|du bao nhieu|con lai bao nhieu)\b", norm_answer):
        if basket and budget_vnd is not None:
            total = sum(p.price_vnd * qty for p, qty in basket)
            rem = budget_vnd - total
            prods = [p for p, _ in basket]
            if rem >= 0:
                reply = (
                    f"Dạ, giỏ tạm chọn của bạn đang hết {format_vnd(total)} trên ngân sách "
                    f"{format_vnd(budget_vnd)}, nên bạn còn dư {format_vnd(rem)} nhé."
                )
            else:
                reply = (
                    f"Dạ, giỏ tạm chọn hiện đang là {format_vnd(total)}, vượt ngân sách "
                    f"{format_vnd(budget_vnd)} khoảng {format_vnd(-rem)} — mình có thể đổi sang mẫu tiết kiệm hơn giúp bạn."  # noqa: E501
                )
            mem = _make_memory(
                previous_memory,
                need=previous_memory.get("need") or text,
                primary_products=prods,
                basket=basket,
                budget_vnd=budget_vnd,
            )
            return _direct_plan(
                reply=reply, primary_products=prods, related_products=[], memory=mem
            )

    # M3. Suggest extra food/drink within remaining budget ("Với số tiền còn lại thì mua thêm được
    # gì?")
    if re.search(r"\b(?:so tien con lai|tien du|con du thi mua them)\b", norm_answer):
        total = sum(p.price_vnd * qty for p, qty in basket)
        rem = (budget_vnd - total) if budget_vnd is not None else 25000
        if rem <= 3000:
            prods = [p for p, _ in basket]
            mem = _make_memory(
                previous_memory,
                need=previous_memory.get("need") or text,
                primary_products=prods,
                basket=basket,
                budget_vnd=budget_vnd,
            )
            return _direct_plan(
                reply=f"Dạ, với số tiền còn lại {format_vnd(max(0, rem))} hiện chưa đủ mua thêm món ăn/đồ uống riêng lẻ trong catalog — bạn muốn mình đổi bớt món hiện tại cho gọn ngân sách không ạ?",  # noqa: E501
                primary_products=prods,
                related_products=[],
                memory=mem,
            )
        pool = await run_catalog_work(repository.search, query="snack", limit=25, timeout_seconds=3)
        pool2 = await run_catalog_work(
            repository.search, query="nuoc khoang", limit=15, timeout_seconds=3
        )
        edible = filter_edible_candidates([*pool, *pool2], ready_to_eat=True)
        existing_ids = {p.product_id for p, _ in basket}
        affordable = [
            p
            for p in sort_single_unit_first(edible, text, cheaper_first=True)
            if p.price_vnd <= rem and p.product_id not in existing_ids
        ]
        if affordable:
            pick = affordable[0]
            mem = _make_memory(
                previous_memory,
                need=previous_memory.get("need") or text,
                primary_products=[pick],
                basket=basket,
                budget_vnd=budget_vnd,
            )
            return build_payload(
                text=text,
                primary_products=[pick],
                related_products=affordable[1:5],
                repository=repository,
                sources={pick.product_id: [0]},
                memory=mem,
                plan_for_fallback=ConsultationDecision(action="recommend", need=text[:400]),
                mode="recommend",
                extra_note=f"Khách còn dư đúng {format_vnd(rem)}; gợi ý món ăn/uống phù hợp trong đúng mức tiền dư này.",  # noqa: E501
            )

    # M4. Multiply previous 2 products ("Nếu mua mỗi loại 3 gói thì hết bao nhiêu?")
    mul_match = re.search(r"\bmoi\s+loai\s+(\d{1,2})\s*(?:goi|chai|lon|hop|cai)?\b", norm_answer)
    if mul_match and len(recent_products) >= 2:
        qty = int(mul_match.group(1))
        p_a, p_b = recent_products[0], recent_products[1]
        sub_a = qty * p_a.price_vnd
        sub_b = qty * p_b.price_vnd
        grand = sub_a + sub_b
        reply = (
            f"Dạ, nếu mua mỗi loại {qty} phần thì: {qty} × {p_a.name} là {format_vnd(sub_a)}, "
            f"và {qty} × {p_b.name} là {format_vnd(sub_b)} — tổng cộng cả hai là {format_vnd(grand)}."  # noqa: E501
        )
        mem = _make_memory(
            previous_memory,
            need=previous_memory.get("need") or text,
            primary_products=[p_a, p_b],
            basket=[(p_a, qty), (p_b, qty)],
            budget_vnd=budget_vnd,
        )
        return _direct_plan(
            reply=reply, primary_products=[p_a, p_b], related_products=[], memory=mem
        )

    # M5. Compare the last 2 discussed products ("So sánh hai loại vừa rồi.")
    if re.search(
        r"\bso sanh\s+(?:hai|2)\s+(?:loai|mon|san pham|chai|goi)\s+(?:vua roi|tren|nay|do)\b",
        norm_answer,
    ):
        if len(recent_products) >= 2:
            p_a, p_b = recent_products[0], recent_products[1]
            mem = _make_memory(
                previous_memory,
                need=previous_memory.get("need") or text,
                primary_products=[p_a, p_b],
                basket=basket,
                budget_vnd=budget_vnd,
                last_action="compare",
            )
            return build_payload(
                text=text,
                primary_products=[p_a, p_b],
                related_products=[],
                repository=repository,
                sources={p_a.product_id: [0], p_b.product_id: [1]},
                memory=mem,
                plan_for_fallback=ConsultationDecision(action="compare", need=text[:400]),
                mode="compare",
            )

    # M6. Bare availability/stock follow-up ("Còn hàng không?", "Tồn kho còn bao nhiêu hộp?")
    # -> lock on active product (never jump to unrelated catalog items!).
    if (
        re.search(
            r"^(?:ton kho|so luong ton|trong kho con|con hang|con khong|con ko|"
            r"co con hang|con bao nhieu|con may)\b",
            norm_answer,
        )
        and recent_products
    ):
        target = recent_products[0]
        snap_date = (
            target.data_timestamp.date().strftime("%d/%m/%Y") if target.data_timestamp else ""
        )
        stock_note = f"snapshot ghi nhận {target.stock} {target.uom or 'đơn vị'}"
        reply = (
            f"Dạ, với {target.name} (giá tham khảo {format_vnd(target.price_vnd)}), "
            f"{stock_note}{f' ngày {snap_date}' if snap_date else ''}; "
            "tồn kho thực tế tại quầy hiện tại chưa được xác minh nên bạn kiểm tra trực tiếp "
            "trên kệ hoặc hỏi nhân viên giúp mình nhé."
        )
        mem = _make_memory(
            previous_memory,
            need=previous_memory.get("need") or text,
            primary_products=[target],
            basket=basket,
            budget_vnd=budget_vnd,
        )
        return _direct_plan(reply=reply, primary_products=[target], related_products=[], memory=mem)

    # M7. Bare price follow-up ("Giá bao nhiêu?") -> lock on active product.
    if (
        re.search(r"^(?:gia bao nhieu|bao nhieu tien|gia sao|gia the nao)\b", norm_answer)
        and recent_products
    ):
        target = recent_products[0]
        reply = (
            f"Dạ, {target.name} có giá {format_vnd(target.price_vnd)}"
            f"{'/' + target.uom.lower() if target.uom else ''} nhé."
        )
        mem = _make_memory(
            previous_memory,
            need=previous_memory.get("need") or text,
            primary_products=[target],
            basket=basket,
            budget_vnd=budget_vnd,
        )
        return _direct_plan(reply=reply, primary_products=[target], related_products=[], memory=mem)

    # M8. Remove/decrement from working basket ("Bỏ một gói mì ra.")
    if re.search(r"\b(?:bo|bot|xoa|huy)\b", norm_answer) and basket:
        rem_qty = _extract_qty(norm_answer, default=1)
        target_idx = None
        for idx, (prod, _q) in enumerate(basket):
            pnorm = normalize_category_text(f"{prod.name} {prod.category or ''}")
            if any(
                tok in pnorm
                for tok in norm_answer.split()
                if len(tok) >= 2
                and tok not in {"bo", "bot", "xoa", "mot", "goi", "chai", "lon", "ra", "di", "thoi"}
            ):
                target_idx = idx
                break
        if target_idx is None:
            target_idx = len(basket) - 1
        prod, old_q = basket[target_idx]
        new_basket = list(basket)
        if old_q > rem_qty:
            new_basket[target_idx] = (prod, old_q - rem_qty)
            action_note = f"bớt {rem_qty} phần {prod.name} (còn {old_q - rem_qty})"
        else:
            new_basket.pop(target_idx)
            action_note = f"bỏ {prod.name} khỏi danh sách"
        prods = [p for p, _ in new_basket]
        if new_basket:
            summary, _ = _format_basket_summary(new_basket, budget_vnd)
            reply = f"Dạ, mình đã {action_note}. Danh sách hiện tại gồm: {summary}"
        else:
            reply = f"Dạ, mình đã {action_note}. Hiện danh sách tạm chọn đang trống — bạn muốn chọn món nào khác không ạ?"  # noqa: E501
        mem = _make_memory(
            previous_memory,
            need=previous_memory.get("need") or text,
            primary_products=prods,
            basket=new_basket,
            budget_vnd=budget_vnd,
        )
        return _direct_plan(reply=reply, primary_products=prods, related_products=[], memory=mem)

    # M9. Quantity update on active item ("Cho tôi 3 lon.", "Chỉ lấy 2 lon thôi.")
    if re.search(
        r"^(?:cho toi|cho minh|chi lay|lay|doi thanh|sua thanh)?\s*(?:\d{1,2}|mot|hai|ba|bon|nam)\s+(?:lon|chai|goi|hop|cai|bich)\s*(?:thoi|nhe|nha|di|a)?$",  # noqa: E501
        norm_answer,
    ) and (basket or recent_products):
        new_qty = _extract_qty(norm_answer, default=1)
        wants_can = "lon" in norm_answer
        active_prod = basket[-1][0] if basket else recent_products[0]
        # If user asks for "lon" (can) while active product is a bottle/carton of the same brand,
        # switch to single can!
        if wants_can and (
            is_bulk_pack(active_prod) or "lon" not in normalize_category_text(active_prod.name)
        ):
            brand_tok = normalize_category_text(active_prod.brand or active_prod.name.split()[0])
            brand_hits = await run_catalog_work(
                repository.search, query=brand_tok, limit=20, timeout_seconds=3
            )
            single_cans = [
                p
                for p in brand_hits
                if not is_bulk_pack(p)
                and ("lon" in normalize_category_text(f"{p.name} {p.uom or ''}"))
            ]
            if single_cans:
                # Preserve sugar-free preference if active product was sugar-free
                if any(
                    k in normalize_category_text(active_prod.name)
                    for k in ("zero", "khong duong", "light", "k calo")
                ):
                    zf = [
                        p
                        for p in single_cans
                        if any(
                            k in normalize_category_text(p.name)
                            for k in ("zero", "khong duong", "light", "k calo")
                        )
                    ]
                    active_prod = (zf or single_cans)[0]
                else:
                    active_prod = single_cans[0]
        new_basket = list(basket)
        if new_basket:
            new_basket[-1] = (active_prod, new_qty)
        else:
            new_basket = [(active_prod, new_qty)]
        summary, _ = _format_basket_summary(new_basket, budget_vnd)
        reply = f"Dạ, mình đã cập nhật thành {new_qty} × {active_prod.name}. Hiện tại: {summary}"
        prods = [p for p, _ in new_basket]
        mem = _make_memory(
            previous_memory,
            need=previous_memory.get("need") or text,
            primary_products=prods,
            basket=new_basket,
            budget_vnd=budget_vnd,
        )
        return _direct_plan(reply=reply, primary_products=prods, related_products=[], memory=mem)

    # M10. Add item to working basket ("Thêm 2 gói mì hảo hảo.", "Thêm một gói bánh nữa.", "Thêm
    # nước uống cho mỗi người.", "Gợi ý thêm món ăn kèm.")
    add_match = re.search(r"\b(?:them|goi y them|bo sung|cong them)\s+(.+)$", norm_answer)
    if add_match and basket:
        raw_add = add_match.group(1).strip()
        add_qty = _extract_qty(raw_add, default=1)
        if "moi nguoi" in norm_answer:
            # Infer group size from prior need (e.g., "cho 4 người")
            grp = re.search(
                r"\b(\d{1,2})\s*nguoi\b",
                normalize_category_text(str(previous_memory.get("need") or "")),
            )
            if grp:
                add_qty = int(grp.group(1))
        if re.search(r"\b(?:mon an kem|an kem)\b", raw_add):
            search_q = "banh"
        elif re.search(r"\b(?:nuoc uong|do uong)\b", raw_add):
            search_q = "nuoc khoang"
        else:
            search_q = re.sub(
                r"\b(?:\d+|mot|hai|ba|bon|nam|goi|chai|lon|hop|cai|nua|nhe|nha|di|cho|moi|nguoi)\b",
                " ",
                raw_add,
            )
            search_q = " ".join(search_q.split())
        if search_q:
            hits = await run_catalog_work(
                repository.search, query=search_q, limit=24, timeout_seconds=3
            )
            hits = filter_edible_candidates(hits, ready_to_eat=True) or hits
            hits = sort_single_unit_first(hits, text, cheaper_first=budget_vnd is not None)
            if hits:
                chosen = hits[0]
                if budget_vnd is not None:
                    cur_total = sum(p.price_vnd * q for p, q in basket)
                    rem = budget_vnd - cur_total
                    fitting = [p for p in hits if p.price_vnd * add_qty <= rem]
                    if fitting:
                        chosen = fitting[0]
                new_basket = [*basket, (chosen, add_qty)]
                summary, _ = _format_basket_summary(new_basket, budget_vnd)
                reply = f"Dạ, mình đã thêm {add_qty} × {chosen.name} ({format_vnd(chosen.price_vnd * add_qty)}). Danh sách hiện tại: {summary}"  # noqa: E501
                prods = [p for p, _ in new_basket]
                mem = _make_memory(
                    previous_memory,
                    need=previous_memory.get("need") or text,
                    primary_products=prods,
                    basket=new_basket,
                    budget_vnd=budget_vnd,
                )
                return _direct_plan(
                    reply=reply, primary_products=prods, related_products=hits[1:4], memory=mem
                )

    # M11. Swap item ("Thôi, đổi thành Pepsi.", "Đổi sang nước trái cây đi.", "Lấy loại ko đường
    # ấy.")
    swap_match = re.search(r"\b(?:doi thanh|doi sang|chuyen sang|thay bang)\s+(.+)$", norm_answer)
    take_zero = bool(
        re.search(
            r"^(?:lay|chon|doi sang)\s+(?:loai|chai|lon)?\s*(?:khong duong|ko duong|it duong)",
            norm_answer,
        )
    )
    if (swap_match or take_zero) and (basket or recent_products):
        if take_zero:
            active_prod = basket[-1][0] if basket else recent_products[0]
            base_q = normalize_category_text(
                active_prod.brand or active_prod.category or active_prod.name
            )
            hits = await run_catalog_work(
                repository.search, query=base_q, limit=30, timeout_seconds=3
            )
            zf = [
                p
                for p in hits
                if any(
                    k in normalize_category_text(p.name)
                    for k in ("zero", "khong duong", "it duong", "light", "k calo")
                )
            ]
            pool = sort_single_unit_first(zf or hits, text)
        else:
            target_q = re.sub(
                r"\b(?:di|nhe|nha|thoi|giup|voi|cho|toi|minh)\b", " ", swap_match.group(1)
            ).strip()
            hits = await run_catalog_work(
                repository.search, query=target_q, limit=25, timeout_seconds=3
            )
            wants_juice = bool(re.search(r"\b(?:trai cay|nuoc ep|hoa qua|nuoc cam)\b", target_q))
            filtered = filter_edible_candidates(
                hits, ready_to_eat=True, allow_carbonated=not wants_juice
            )
            if wants_juice and not filtered:
                juice_hits = await run_catalog_work(
                    repository.search, query="nuoc ep", limit=25, timeout_seconds=3
                )
                filtered = filter_edible_candidates(
                    juice_hits, ready_to_eat=True, allow_carbonated=False
                )
            hits = filtered or hits
            pool = sort_single_unit_first(hits, text, cheaper_first=budget_vnd is not None)
        if pool:
            chosen = pool[0]
            new_basket = list(basket)
            if new_basket:
                # Replace the last drink/matching slot while keeping its qty
                replace_idx = len(new_basket) - 1
                if swap_match and re.search(r"\b(?:nuoc|tra|sua|pepsi|coca)\b", norm_answer):
                    for idx, (p, _q) in enumerate(new_basket):
                        if any(
                            k in normalize_category_text(f"{p.name} {p.category or ''}")
                            for k in ("sua", "nuoc", "tra", "coca", "pepsi")
                        ):
                            replace_idx = idx
                            break
                kept_qty = new_basket[replace_idx][1]
                new_basket[replace_idx] = (chosen, kept_qty)
            else:
                new_basket = [(chosen, 1)]
            prods = [p for p, _ in new_basket]
            mem = _make_memory(
                previous_memory,
                need=previous_memory.get("need") or text,
                primary_products=prods,
                basket=new_basket,
                budget_vnd=budget_vnd,
            )
            return build_payload(
                text=text,
                primary_products=[chosen],
                related_products=_related_candidates(pool, [chosen], limit=4),
                repository=repository,
                sources={chosen.product_id: [0]},
                memory=mem,
                plan_for_fallback=ConsultationDecision(action="recommend", need=text[:400]),
                mode="recommend",
            )

    # M12. Universal context-locked anaphoric follow-ups (works for BOTH food & non-food after
    # ANY Turn 1 — price lookup, search, recommendation, or comparison):
    # - Superlatives/rankings: "loại nào rẻ nhất?", "cái nào đắt nhất?", "loại nào giảm giá nhiều
    #   nhất?", "loại nào tiết kiệm nhất?", "trong số đó loại nào cay nhất?"
    # - Comparatives & browsing: "có loại nào rẻ hơn không?", "còn loại nào khác không?",
    #   "có chai nhỏ hơn không?", "có loại nào lớn hơn không?", "có loại nào dưới 100k không?"
    # - Preferences/exclusions: "ưu tiên khuyến mãi", "không thích sữa/đắng/cay/gas",
    #   "loại nào ít ngọt nhất?", "giảm ngân sách xuống còn 100k"
    from backend.services.semantic_nlp_router import clean_native_shopper_query

    no_new_product_noun = not getattr(parsed, "product_type", None) and (
        not clean_native_shopper_query(answer_text, fallback_to_raw=False)
    )
    is_anaphoric_ranking_or_browse = bool(
        not getattr(parsed, "product_type", None)
        and re.search(
            r"\b(?:re nhat|re hon|gia re|gia thap nhat|thap nhat|gia mem|mem nhat|tiet kiem nhat|"
            r"loi nhat|dat nhat|mac nhat|cao cap nhat|lon nhat|to nhat|nho nhat|lon hon|to hon|"
            r"giam gia.*nhat|khuyen mai.*nhat|dang giam gia|dang khuyen mai|dang sale|"
            r"loai khac|mau khac|mon khac|cai khac|xem them|con loai nao|con mau nao)\b",
            norm_answer,
        )
    )
    is_anaphoric_budget_only = bool(no_new_product_noun and turn_budget is not None)
    is_context_refine = bool(
        is_anaphoric_ranking_or_browse
        or is_anaphoric_budget_only
        or re.search(
            r"\b(?:trong so do|cay nhat|van cay|chai nho hon|loai nho hon|goi nho hon|"
            r"tuong tu nhung|uu tien.*khuyen mai|khong thich|khong uong duoc|khong an duoc|"
            r"it ngot nhat|it duong hon|giam ngan sach|ngan sach cua toi|tong cong duoi)\b",
            norm_answer,
        )
    )
    if is_context_refine and (recent_products or basket or active_category_hint):
        active_prod = (
            recent_products[0]
            if recent_products
            else (basket[0][0] if basket else None)
        )
        active_cat = (active_prod.category if active_prod else "") or (active_category_hint or "")
        prior_need = str(previous_memory.get("need") or "")
        is_food_domain = (
            normalize_category_text(active_prod.department or "") != "phi thuc pham"
            if active_prod is not None
            else True
        )
        exclusions = list(previous_memory.get("session_exclusions") or [])
        if "sua" in norm_answer and "khong thich" in norm_answer:
            exclusions.append("sua")
        if "dang" in norm_answer and "khong thich" in norm_answer:
            exclusions.append("dang")
        if "gas" in norm_answer or "co ga" in norm_answer:
            exclusions.append("gas")
        if "cay" in norm_answer and ("khong an" in norm_answer or "khong cay" in norm_answer):
            exclusions.append("cay")

        wants_spicy = bool(re.search(r"\b(?:cay nhat|van cay)\b", norm_answer))
        wants_smaller = bool(
            re.search(
                r"\b(?:chai nho hon|loai nho hon|goi nho hon|nho hon|nho nhat)\b",
                norm_answer,
            )
        )
        wants_larger = bool(
            re.search(
                r"\b(?:chai lon hon|loai lon hon|lon hon|to hon|lon nhat|to nhat)\b",
                norm_answer,
            )
        )
        wants_promo = bool(re.search(r"\b(?:khuyen mai|giam gia|sale)\b", norm_answer))
        wants_less_sweet = bool(re.search(r"\b(?:it ngot|it duong)\b", norm_answer))
        wants_cheapest = bool(
            re.search(
                r"\b(?:re nhat|gia thap nhat|thap nhat|mem nhat|tiet kiem nhat|loi nhat)\b",
                norm_answer,
            )
        )
        wants_cheaper = bool(
            re.search(r"\b(?:re hon|thap hon|mem hon|gia re|it tien hon)\b", norm_answer)
        )
        wants_priciest = bool(re.search(r"\b(?:dat nhat|mac nhat|cao cap nhat)\b", norm_answer))
        wants_other = bool(
            re.search(
                r"\b(?:loai khac|mau khac|mon khac|cai khac|xem them|con loai nao|con mau nao)\b",
                norm_answer,
            )
        )

        # Choose search scope strictly inside the active domain!
        if (
            active_prod is not None
            and "dang" in exclusions
            and _BITTER_COFFEE_RE.search(
                normalize_category_text(f"{active_prod.name} {active_cat}")
            )
        ):
            scope_hits = await run_catalog_work(
                repository.search, query="nuoc tra", limit=40, timeout_seconds=3
            )
        elif (
            active_prod is not None
            and "sua" in exclusions
            and _DAIRY_MILK_RE.search(
                normalize_category_text(f"{active_prod.name} {active_cat}")
            )
        ):
            scope_hits = await run_catalog_work(
                repository.search, query="banh mi", limit=40, timeout_seconds=3
            )
        elif "gas" in exclusions:
            scope_hits = await run_catalog_work(
                repository.search, query="nuoc khoang", limit=40, timeout_seconds=3
            )
        else:
            # Prefer the most specific active category (leaf of category_hierarchy or
            # active_category_hint) so "Sữa rửa mặt" stays inside face washes (never pulling
            # cheaper sheet masks from broad parent "Chăm Sóc Da"!).
            leaf_cat = ""
            if active_prod is not None and active_prod.category_hierarchy:
                parts = [
                    seg.strip()
                    for seg in active_prod.category_hierarchy.split(">")
                    if seg.strip()
                ]
                if parts:
                    leaf_cat = parts[-1]
            # If displayed items share multiple leaves under one parent (e.g. Trái cây: Táo NK +
            # Dưa NĐ), use active_category_hint or the shared parent instead of a single fruit leaf.
            visible_leaves = {
                [seg.strip() for seg in p.category_hierarchy.split(">") if seg.strip()][-1]
                for p in recent_products
                if p.category_hierarchy
            }
            if len(visible_leaves) >= 2 and active_category_hint:
                cat_target = active_category_hint
            elif len(visible_leaves) >= 2 and active_prod and active_prod.category_hierarchy:
                parts = [
                    seg.strip()
                    for seg in active_prod.category_hierarchy.split(">")
                    if seg.strip()
                ]
                cat_target = parts[-2] if len(parts) >= 2 else (active_cat or leaf_cat)
            else:
                cat_target = leaf_cat or active_category_hint or active_cat
            scope_hits = []
            if cat_target:
                scope_hits = await run_catalog_work(
                    repository.search,
                    query="",
                    category=cat_target,
                    limit=60,
                    timeout_seconds=3,
                )
            if not scope_hits and cat_target:
                scope_hits = await run_catalog_work(
                    repository.search, query=cat_target, limit=50, timeout_seconds=3
                )
            if not scope_hits and (prior_need or (active_prod and active_prod.name)):
                scope_hits = await run_catalog_work(
                    repository.search,
                    query=prior_need or (active_prod.name if active_prod else ""),
                    limit=40,
                    timeout_seconds=3,
                )

        # Always include already-displayed items from the same active domain so we never lose them.
        seen_ids = {p.product_id for p in scope_hits}
        hits = list(scope_hits)
        for p in recent_products:
            if p.product_id not in seen_ids:
                hits.append(p)
                seen_ids.add(p.product_id)

        if is_food_domain:
            food_filtered = filter_edible_candidates(
                hits,
                ready_to_eat=is_food_or_drink_need(prior_need) or is_food_or_drink_need(text),
                allow_milk="sua" not in exclusions,
                allow_spicy="cay" not in exclusions,
                allow_carbonated="gas" not in exclusions,
                allow_bitter="dang" not in exclusions,
            )
            hits = food_filtered or hits
        if wants_spicy:
            spicy_hits = [
                p
                for p in hits
                if p.spicy is True or _SPICY_TITLE_RE.search(normalize_category_text(p.name))
            ]
            hits = spicy_hits or hits
        if wants_smaller and active_prod is not None:
            cur_vol = _extract_volume_ml(active_prod.name)
            smaller = [
                p
                for p in hits
                if not is_bulk_pack(p)
                and (_extract_volume_ml(p.name) or 999999) < (cur_vol or 999999)
            ]
            if not smaller and is_food_domain:
                drink_hits = await run_catalog_work(
                    repository.search, query="nuoc tra", limit=30, timeout_seconds=3
                )
                smaller = [
                    p
                    for p in sort_single_unit_first(drink_hits, text, cheaper_first=True)
                    if not is_bulk_pack(p)
                ]
            hits = smaller or hits
        elif wants_larger:
            sized = [p for p in hits if not is_bulk_pack(p) and _extract_volume_ml(p.name)]
            if sized:
                hits = sorted(
                    sized,
                    key=lambda p: (-(_extract_volume_ml(p.name) or 0), p.price_vnd),
                )
        if wants_less_sweet:
            low_s = [
                p
                for p in hits
                if any(
                    k in normalize_category_text(p.name)
                    for k in (
                        "khong duong",
                        "it duong",
                        "zero",
                        "oolong",
                        "tra xanh",
                        "khoang",
                        "tinh khiet",
                    )
                )
            ]
            hits = low_s or hits
        price_cap = (
            turn_budget
            if turn_budget is not None
            and (
                is_anaphoric_budget_only
                or re.search(r"\b(?:duoi|khong qua|tam)\s+\d", norm_answer)
            )
            else None
        )
        if price_cap is not None:
            under_cap = [p for p in hits if p.price_vnd <= price_cap]
            hits = under_cap or hits
        if wants_other and visible:
            visible_ids = {p.product_id for p in visible}
            unseen = [p for p in hits if p.product_id not in visible_ids]
            hits = unseen or hits
        if (
            wants_cheaper
            and not wants_cheapest
            and active_prod is not None
            and active_prod.price_vnd
        ):
            cheaper_pool = [
                p
                for p in hits
                if p.price_vnd < active_prod.price_vnd and p.product_id != active_prod.product_id
            ]
            hits = cheaper_pool or hits

        if wants_priciest:
            allow_bulk = wants_bulk_pack(text)
            singles = [p for p in hits if allow_bulk or not is_bulk_pack(p)] or hits
            hits = sorted(singles, key=lambda p: (-p.price_vnd, p.name))
        elif not wants_larger:
            hits = sort_single_unit_first(
                hits,
                text,
                cheaper_first=wants_cheapest or wants_cheaper or (price_cap is not None),
                promo_first=wants_promo,
            )

        if hits:
            # If user reduced total budget or set group budget, rebuild affordable basket cleanly
            if budget_vnd is not None and re.search(
                r"\b(?:ngan sach|tong cong duoi)\b", norm_answer
            ):
                picked_basket: list[tuple[ProductSnapshot, int]] = []
                running = 0
                for p, qty in basket:
                    if p.price_vnd * qty + running <= budget_vnd and not _item_excluded(
                        p, exclusions
                    ):
                        picked_basket.append((p, qty))
                        running += p.price_vnd * qty
                if not picked_basket:
                    for p in hits[:3]:
                        if p.price_vnd + running <= budget_vnd:
                            picked_basket.append((p, 1))
                            running += p.price_vnd
                if not picked_basket:
                    picked_basket = [(hits[0], 1)]
                primary = [p for p, _ in picked_basket]
                mem = _make_memory(
                    previous_memory,
                    need=prior_need or text,
                    primary_products=primary,
                    basket=picked_basket,
                    budget_vnd=budget_vnd,
                    extra_excluded=exclusions,
                )
                return build_payload(
                    text=text,
                    primary_products=primary,
                    related_products=_related_candidates(hits, primary, limit=4),
                    repository=repository,
                    sources={p.product_id: [i] for i, p in enumerate(primary)},
                    memory=mem,
                    plan_for_fallback=ConsultationDecision(action="recommend", need=text[:400]),
                    mode="basket" if len(primary) >= 2 else "recommend",
                    budget_vnd=budget_vnd,
                )

            pick = hits[0]
            cat_label = active_category_hint or active_cat or (pick.category or "nhóm này")
            note = ""
            if wants_cheapest:
                note = (
                    f"Khách đang hỏi loại rẻ nhất trong nhóm {cat_label}; giới thiệu đúng sản phẩm "
                    f"có giá thấp nhất trong catalog ({pick.name} — {format_vnd(pick.price_vnd)}) "
                    "và nêu gọn các mẫu giá mềm kế tiếp bên dưới."
                )
            elif wants_priciest:
                note = f"Khách đang hỏi mẫu giá cao nhất trong nhóm {cat_label}."
            elif wants_spicy and "cay nhat" in norm_answer:
                note = "Nói rõ catalog không có thang đo độ cay Scoville chính thức, nên giới thiệu mẫu mì ghi rõ vị cay nổi bật nhất trong nhóm."  # noqa: E501
            elif wants_less_sweet:
                note = "Nói rõ ưu tiên mẫu ghi không đường/ít đường theo tên catalog và khuyên khách kiểm tra bảng dinh dưỡng trên nhãn."  # noqa: E501
            new_basket = list(basket)
            if "sua" in exclusions:
                new_basket = [
                    (p, q)
                    for p, q in new_basket
                    if not _DAIRY_MILK_RE.search(
                        normalize_category_text(f"{p.name} {p.category or ''}")
                    )
                ]
            if not new_basket:
                new_basket = [(pick, 1)]
            mem = _make_memory(
                previous_memory,
                need=prior_need or cat_label or text,
                primary_products=[pick],
                basket=new_basket,
                budget_vnd=budget_vnd,
                extra_excluded=exclusions,
            )
            return build_payload(
                text=text,
                primary_products=[pick],
                related_products=_related_candidates(hits, [pick], limit=4),
                repository=repository,
                sources={pick.product_id: [0]},
                memory=mem,
                plan_for_fallback=ConsultationDecision(
                    action="recommend", need=(prior_need or cat_label or text)[:400]
                ),
                mode="recommend",
                extra_note=note,
            )

    return None


def _item_excluded(p: ProductSnapshot, exclusions: list[str]) -> bool:
    blob = normalize_category_text(f"{p.name} {p.category or ''}")
    if "sua" in exclusions and _DAIRY_MILK_RE.search(blob):
        return True
    if "cay" in exclusions and (p.spicy is True or _SPICY_TITLE_RE.search(blob)):
        return True
    if "gas" in exclusions and _CARBONATED_RE.search(blob):
        return True
    if "dang" in exclusions and _BITTER_COFFEE_RE.search(blob):
        return True
    return False


def is_pure_situational_query(norm_answer: str) -> bool:
    """Return True when the query describes an eating/lifestyle situation without naming a SKU."""
    return bool(
        _PURE_SITUATIONAL_NO_NOUN_RE.search(norm_answer)
        or re.search(
            r"\b(?:dang doi|doi bung|an ngay|khong can che bien|khong co lo vi song|"
            r"mang di hoc|di xa|chuyen di|an kieng|do buon ngu|tinh tao|mon qua nho|"
            r"xem phim|lien hoan|cho \d+ nguoi|cho hai nguoi|cho ba nguoi|cho bon nguoi)\b",
            norm_answer,
        )
    )


from backend.services.customer_consultation import (  # noqa: E402
    ConsultationDecision,
    StreamingConsultationPlan,
    _batch_base_unit_dims,
    _product_discount_pct,
    _related_candidates,
    _unit_price_info,
)
