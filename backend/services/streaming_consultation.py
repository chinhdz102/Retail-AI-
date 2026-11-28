"""Fast grounded streaming consultation engine (0-planner & 1-call slim basket).

Extracted from ``customer_consultation.py`` to separate the low-latency SSE
streaming paths from the multi-step safety/complex consultation planner:
  - Path A: Direct 2-target / 2-brand comparison (0 planner calls)
  - Path B: Store-wide promotion browsing (0 planner calls)
  - Path C: Single-category / brand / sugar / attribute / follow-up (0 planner calls)
  - Path D: Direct lexical product lookup (0 planner calls)
  - Path E: Multi-item meal / combo / budget basket (1 slim keyword call -> live stream)
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from typing import Literal

from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.products.knowledge import product_knowledge_facts
from backend.products.repository import ProductRepository
from backend.services.bounded_work import run_catalog_work
from backend.services.customer_consultation import (
    _FAST_PATH_MULTI_OR_COMPARE,
    _PROMO_NEED_RE,
    _STREAM_BASKET_OR_MEAL_RE,
    _STREAM_FILLER_TOKENS,
    _STREAM_HIGH_RISK_DOMAINS,
    _STREAM_LABEL_OR_EFFICACY_RE,
    _STREAM_PROMO_BROWSE_RE,
    _STREAM_SAFE_GUIDANCE_IDS,
    _STREAM_VALUE_RANK_RE,
    ConsultationDecision,
    SearchNeed,
    StreamingConsultationPlan,
    _advice_followup,
    _allowed,
    _ambiguous_identity_question,
    _explicit_sugar_level,
    _fast_path_single_category_plan,
    _local_fallback_text,
    _preserve_constraints,
    _product_discount_pct,
    _product_sizes,
    _related_candidates,
    _retrieve,
    _shopping_utterance,
    _unit_price_info,
)
from backend.services.groq import GroqService
from backend.services.organization_profiles import resolve_organization_profile
from backend.services.product_constraints import title_has_descriptor
from backend.services.query_understanding import (
    strip_retrieval_budget,
    understand_catalog_query,
)
from backend.services.semantic_nlp_router import (
    SemanticRoute,
    clean_native_shopper_query,
    dense_search_skus,
    route_query,
)
from backend.services.sessions import SessionRecord
from backend.services.shopping_guidance import shopping_guidance
from backend.services.stock_inventory import (
    parse_sales_or_stock_comparison,
    parse_stock_ranking_request,
)
from backend.services.streaming_multiturn import (
    _READY_TO_EAT_NEED_RE,
    build_plain_prompt_text,
    filter_edible_candidates,
    is_bulk_pack,
    is_food_or_drink_need,
    is_pure_situational_query,
    prepare_multiturn_streaming,
    prepare_special_turn1_streaming,
    wants_bulk_pack,
)

_EXTENDED_BASKET_RE = re.compile(
    r"\b(?:lien\s+hoan|chuyen\s+di|di\s+xa|cho\s+(?:hai|ba|bon|2|3|4|nhieu)\s+nguoi|"
    r"nhom\s+ban|combo|bua\s+sang|an\s+sang|xem\s+phim|do\s+an\s+va\s+nuoc\s+uong)\b"
)
_ATTR_MODIFIER_STRIP_RE = re.compile(
    r"\b(?:khong\s+duong|it\s+duong|giam\s+duong|it\s+ngot|khong\s+calo|"
    r"it\s+calo|khong\s+co\s+gas|khong\s+gas|khong\s+cay|cay\s+nhat|protein\s+cao)\b"
)

_SINGLE_ITEM_SEASONING_RE = re.compile(
    r"\b(?:gia vi|sot uop|bot uop|goi gia vi|nuoc sot|nuoc cham|tuong ot|muoi cham)\b"
)
_SAVORY_MEAL_CUE_RE = re.compile(
    r"\b(?:bua toi|bua trua|bua com|nau com|lau|mi y|spaghetti|bbq|nuong|"
    r"nau |bun |pho |hu tieu|chao |canh |lam mon)\b"
)
_HOUSEHOLD_UTENSIL_CUE_RE = re.compile(
    r"\b(?:coc|ly|chen|bat|dua|muong|thia|noi|chao|khan|giay|mang boc|tui rac)\b"
)
_SNACK_JUNK_FOR_MEAL_RE = re.compile(
    r"\b(?:banh que|banh trang|snack|bim bim|keo|bong ca|da ca|kho bo|chan ga)\b"
)


_NEG_TRAILING_FILLER_RE = re.compile(
    r"\s+(?:co\s+loai\s+nao|loai\s+nao|cai\s+nao|mon\s+nao|nao|khong|nhe|nha|a|giup|voi)\b.*$"
)
_NATIVE_STREAM_OPENER_RE = re.compile(
    r"(?i)(?:^|(?<=[\s,.;:!?]))"
    r"(?:chúng\s+tôi|chúng\s+ta|bọn\s+mình|bọn\s+tao|"
    r"cho\s+(?:tao|mình|tôi|em)\s+biết(?:\s+là)?|"
    r"muốn\s+hỏi(?:\s+về)?(?:\s+sản\s+phẩm)?|"
    r"hỏi\s+về(?:\s+sản\s+phẩm)?|nói\s+về|tìm\s+hiểu(?:\s+về)?|"
    r"tao|mày|thì(?!\s+là)|về(?!\s+sinh)|biết(?=\s|$|[.,?!]))"
    r"(?=$|[\s,.;:!?])"
)
_STREAM_DETAIL_QUERY_RE = re.compile(
    r"\b(?:cach\s+(?:su\s+)?dung|huong\s+dan\s+su\s+dung|"
    r"(?:su\s+)?dung\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
    r"(?:goi|pha|nau|che\s+bien)\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
    r"thanh\s+phan|chat\s+lieu|lam\s+tu\s+gi|lam\s+bang\s+gi|bao\s+quan|"
    r"xuat\s+xu|nguon\s+goc|san\s+xuat\s+(?:o\s+dau|tai)|cua\s+nuoc\s+nao)\b"
)
_STREAM_DETAIL_STRIP_RE = re.compile(
    r"\b(?:cach\s+(?:su\s+)?dung|huong\s+dan\s+su\s+dung|"
    r"(?:su\s+)?dung\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
    r"(?:goi|pha|nau|che\s+bien)\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
    r"thanh\s+phan(?:\s+chinh)?|chat\s+lieu|lam\s+tu\s+gi|lam\s+bang\s+gi|"
    r"bao\s+quan(?:\s+nhu\s+the\s+nao|\s+the\s+nao|\s+sao)?|"
    r"xuat\s+xu(?:\s+o\s+dau|\s+tu\s+dau|\s+nuoc\s+nao)?|nguon\s+goc|"
    r"san\s+xuat\s+(?:o\s+dau|tai)|cua\s+nuoc\s+nao|"
    r"nhu\s+the\s+nao|the\s+nao|ra\s+sao|chai\s+nay|hop\s+nay|goi\s+nay|loai\s+nay|cai\s+nay)\b"
)
_STREAM_DETAIL_ATTR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "usage",
        re.compile(
            r"\b(?:cach\s+(?:su\s+)?dung|huong\s+dan\s+su\s+dung|"
            r"(?:su\s+)?dung\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
            r"(?:goi|pha|nau|che\s+bien)\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao))\b"
        ),
    ),
    (
        "ingredients",
        re.compile(
            r"\b(?:thanh\s+phan|chat\s+lieu|lam\s+tu\s+gi|lam\s+bang\s+gi|co\s+chat\s+gi)\b"
        ),
    ),
    ("storage", re.compile(r"\b(?:bao\s+quan|cat\s+giu|de\s+o\s+dau)\b")),
    (
        "origin",
        re.compile(r"\b(?:xuat\s+xu|nguon\s+goc|san\s+xuat\s+(?:o\s+dau|tai)|cua\s+nuoc\s+nao)\b"),
    ),
)
_DETAIL_ATTR_LABELS_VI: dict[str, str] = {
    "usage": "hướng dẫn sử dụng",
    "ingredients": "bảng thành phần",
    "storage": "hướng dẫn bảo quản",
    "origin": "thông tin xuất xứ",
}


def _clean_negative_terms(parsed) -> list[str]:
    neg = getattr(parsed, "negative_constraints", ()) or ()
    cleaned: list[str] = []
    for term in neg:
        raw = str(term).strip()
        if not raw:
            continue
        norm = normalize_category_text(raw)
        norm = _NEG_TRAILING_FILLER_RE.sub("", norm).strip()
        if not norm or norm.startswith("duong"):
            continue
        cleaned.append(norm)
    return list(dict.fromkeys(cleaned))


def _item_violates_exclusions(item: ProductSnapshot, excluded: list[str]) -> bool:
    if not excluded:
        return False
    blob = f"{item.name} {item.category or ''}"
    norm_blob = normalize_category_text(blob)
    for term in excluded:
        if title_has_descriptor(blob, term) or term in norm_blob:
            return True
    return False


def _apply_streaming_constraints(
    candidates: list[ProductSnapshot],
    text: str,
    parsed,
    *,
    enforce_budget: bool = True,
) -> list[ProductSnapshot]:
    """Deterministically enforce exclusions, sugar, budget, and explicit pack size."""
    filtered = list(candidates)
    excluded = _clean_negative_terms(parsed)
    if excluded:
        filtered = [p for p in filtered if not _item_violates_exclusions(p, excluded)]
    sugar_req = _explicit_sugar_level(text)
    if sugar_req in {"none", "less"}:
        sugar_plan = ConsultationDecision(
            action="recommend", need=text[:200], sugar=sugar_req,
        )
        sugar_search = SearchNeed(category="Sản phẩm", role="")
        filtered = [p for p in filtered if _allowed(p, sugar_plan, sugar_search)]
    if enforce_budget and parsed.budget_vnd is not None:
        if getattr(parsed, "budget_operator", None) == "LT":
            filtered = [p for p in filtered if p.price_vnd < parsed.budget_vnd]
        else:
            filtered = [p for p in filtered if p.price_vnd <= parsed.budget_vnd]
    req_sizes = _product_sizes(text)
    if req_sizes and filtered:
        size_matched = [p for p in filtered if _product_sizes(p.name) & req_sizes]
        if size_matched:
            filtered = size_matched
    return filtered


def _lean_streaming_product_card(
    p: ProductSnapshot,
    *,
    force_base_dims: set[str] | None = None,
) -> dict[str, object]:
    """Compact verified catalog card (~35% fewer input tokens than full _candidate_facts)."""
    card: dict[str, object] = {
        "name": p.name,
        "price_vnd": p.price_vnd,
    }
    if p.uom:
        card["uom"] = p.uom
    if p.list_price_vnd and p.list_price_vnd > p.price_vnd:
        card["list_price_vnd"] = p.list_price_vnd
    discount = _product_discount_pct(p)
    if discount:
        card["discount_pct"] = discount
    if p.promotion:
        card["promotion"] = p.promotion
    unit_info = _unit_price_info(p, force_base_unit=bool(force_base_dims))
    if unit_info is not None:
        card["unit_price"] = unit_info[1]
    return card


def _clean_native_lexical_query(text: str) -> str:
    """Strip Vietnamese conversational wrappers & budgets while preserving diacritics."""
    cleaned_native = _NATIVE_STREAM_OPENER_RE.sub(" ", text)
    cleaned_native = re.sub(r"\btương\s+ót\b", "tương ớt", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\bsửa\s+tươi\b", "sữa tươi", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\bsửa\s+chua\b", "sữa chua", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\bsửa\s+rửa\s+mặt\b", "sữa rửa mặt", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\b(?:ko|k)\s+đường\b", "không đường", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\b(?:đồ\s+)?ăn\s+vặt\b", "snack", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\btương\s+tự\b", " ", cleaned_native, flags=re.I)
    cleaned_native = re.sub(r"\b(?:đang\s+)?đói\b", " ", cleaned_native, flags=re.I)
    cleaned_native = re.sub(
        r"\btổng(?:\s+(?:tiền|cộng|hết))?\b", " ", cleaned_native, flags=re.I
    )
    cleaned_native = re.sub(
        r"(?<!trái\s)(?<!quả\s)\bnhỏ(?=\s+(?:hơn|nhất|gọn|xíu)\b|\s*$|[.,?!])",
        " ",
        cleaned_native,
        flags=re.I,
    )
    cleaned_native = strip_retrieval_budget(cleaned_native)
    return clean_native_shopper_query(cleaned_native)


def _clean_lexical_query(text: str) -> str:
    """Strip Vietnamese conversational fillers & budgets to isolate product keywords."""
    native_core = _clean_native_lexical_query(text)
    # Keep short diacritic-protected product tokens (e.g. "tỏi"->"toi", "táo"->"tao", "cá"->"ca",
    # "ý" in "mì ý") that collide with ASCII stopwords or 1-char filter after accent folding.
    protected_folded: set[str] = set()
    tokens: list[str] = []
    for nw in native_core.split():
        folded = normalize_category_text(nw)
        if not folded:
            continue
        if nw in {"tỏi", "táo", "cá", "cà", "bơ", "bò", "gà", "mì", "trà", "đá", "cua", "ổi", "lê"}:
            protected_folded.add(folded)
        if len(folded) >= 2 and (folded not in _STREAM_FILLER_TOKENS or folded in protected_folded):
            tokens.append(folded)
        elif nw == "ý":
            tokens.append(folded)
    return " ".join(tokens)


def _strip_attribute_modifiers_for_lex(query: str) -> str:
    cleaned = _ATTR_MODIFIER_STRIP_RE.sub(" ", query)
    return " ".join(cleaned.split())


def _focus_tokens_for_streaming(text: str, product_type: str | None = None) -> list[str]:
    """Extract brand/attribute tokens beyond the base category head for candidate filtering."""
    pt_tokens = set(normalize_category_text(product_type or "").split())
    cleaned = _clean_lexical_query(text)
    return [tok for tok in cleaned.split() if tok not in pt_tokens]


def _filter_streaming_candidates(
    candidates: list[ProductSnapshot],
    text: str,
    product_type: str | None = None,
) -> tuple[list[ProductSnapshot], dict[str, int]]:
    """Prioritize/narrow candidates matching explicit brand or attribute tokens."""
    focus = _focus_tokens_for_streaming(text, product_type)
    if not focus:
        return candidates, {}
    native_core_toks = set(_clean_native_lexical_query(text).split())
    require_native_y = "ý" in native_core_toks
    scores: dict[str, int] = {}
    for item in candidates:
        title_hay = normalize_category_text(f"{item.name} {item.brand or ''}")
        title_words = set(title_hay.split())
        title_compact = title_hay.replace(" ", "")
        raw_hay = f"{item.name} {item.brand or ''} {item.category or ''}"
        hay = normalize_category_text(raw_hay)
        hay_words = set(hay.split())
        hay_compact = hay.replace(" ", "")
        hay_native_toks = set(
            re.sub(
                r"[^\w\s]",
                " ",
                unicodedata.normalize("NFC", raw_hay.lower()),
                flags=re.UNICODE,
            ).split()
        )
        hits = 0
        for tok in focus:
            if tok == "y" and require_native_y:
                if "ý" in hay_native_toks:
                    hits += 2
            elif len(tok) <= 3:
                if tok in title_words:
                    hits += 2
                elif tok in hay_words:
                    hits += 1
            elif tok in title_hay or tok in title_compact:
                hits += 2
            elif tok in hay or tok in hay_compact:
                hits += 1
        if hits > 0:
            scores[item.product_id] = hits
    if not scores:
        return candidates, {}
    best_hits = max(scores.values())
    # Narrow cleanly when explicit brand/attribute matches exist; keep >=2 items if possible.
    matched = [item for item in candidates if scores.get(item.product_id, 0) == best_hits]
    if len(matched) < 2 and best_hits >= 2:
        matched = [item for item in candidates if scores.get(item.product_id, 0) >= 1]
    return (matched or candidates), scores


def _rank_streaming_candidates(
    candidates: list[ProductSnapshot],
    text: str,
    *,
    focus_scores: dict[str, int] | None = None,
    cheaper_priority: bool = False,
) -> list[ProductSnapshot]:
    """Order verified candidates by single-unit preference, lexical fit, value, or promo."""
    norm_text = normalize_category_text(text)
    promo_priority = bool(_PROMO_NEED_RE.search(norm_text))
    value_priority = bool(_STREAM_VALUE_RANK_RE.search(norm_text))
    wants_bulk = wants_bulk_pack(text)
    has_single_unit = any(not is_bulk_pack(item) for item in candidates)
    scores = focus_scores or {}

    def _sort_key(item: ProductSnapshot) -> tuple[int, int, float, float, int, str]:
        lex = -scores.get(item.product_id, 0)
        is_bulk_carton = (
            1
            if (not wants_bulk and has_single_unit and is_bulk_pack(item))
            else 0
        )
        discount = float(_product_discount_pct(item) or 0)
        unit_info = _unit_price_info(item)
        unit_price = float(unit_info[0]) if unit_info is not None else float("inf")
        if cheaper_priority:
            return (
                is_bulk_carton, lex, float(item.price_vnd), unit_price, item.price_vnd, item.name,
            )
        if value_priority:
            return (is_bulk_carton, lex, unit_price, -discount, item.price_vnd, item.name)
        if promo_priority:
            return (is_bulk_carton, lex, -discount, unit_price, item.price_vnd, item.name)
        has_strong_discount = 1.0 if discount >= 15.0 else 0.0
        return (is_bulk_carton, lex, -has_strong_discount, -discount, item.price_vnd, item.name)

    return sorted(candidates, key=_sort_key)


def _build_streaming_plan_payload(
    *,
    text: str,
    primary_products: list[ProductSnapshot],
    related_products: list[ProductSnapshot],
    repository: ProductRepository,
    sources: dict[str, list[int]],
    memory: dict,
    plan_for_fallback: ConsultationDecision,
    mode: Literal["recommend", "compare", "promo", "basket"] = "recommend",
    budget_vnd: int | None = None,
    extra_note: str = "",
) -> StreamingConsultationPlan:
    excluded_terms = list(
        dict.fromkeys(
            term
            for search in (plan_for_fallback.searches or ())
            for term in (search.excluded or ())
            if term
        )
    )
    mode_hint = {
        "recommend": (
            "Giải thích ngắn gọn vì sao sản phẩm nổi bật trong phần SẢN PHẨM GỢI Ý CHÍNH "
            "hợp nhu cầu của khách dựa trên giá, khuyến mãi, quy cách hoặc đơn giá quy đổi."
        ),
        "compare": (
            "So sánh trực tiếp 2 sản phẩm trong SẢN PHẨM GỢI Ý CHÍNH về giá tham khảo, "
            "quy cách/dung tích, đơn giá quy đổi và khuyến mãi để khách dễ chọn."
        ),
        "promo": (
            "Giới thiệu nhanh các ưu đãi nổi bật hiện có trong danh sách theo mức giảm giá "
            "và giá tham khảo."
        ),
        "basket": (
            "Gợi ý gọn bộ sản phẩm trong SẢN PHẨM GỢI Ý CHÍNH theo đúng nhu cầu và ngân sách "
            "(nêu đúng tổng tiền đã tính sẵn nếu có)."
        ),
    }[mode]
    exclusion_rule = (
        " (5) Khách có yêu cầu tránh/loại trừ: chỉ giới thiệu món phù hợp và nhắc nhẹ khách "
        "kiểm tra thêm nhãn/gói gia vị trên bao bì."
        if excluded_terms
        else ""
    )
    system_prompt = (
        "Bạn là Mai, trợ lý siêu thị thân thiện. CHỈ trả lời bằng văn xuôi tiếng Việt tự nhiên, "
        "ngắn gọn trong 2–3 câu liền mạch (TUYỆT ĐỐI KHÔNG xuất JSON, KHÔNG dùng ký tự { } [ ], "
        "không gạch đầu dòng, không markdown **). "
        "XƯNG HÔ BẮT BUỘC: luôn xưng 'mình' và gọi khách là 'bạn' nhất quán trong toàn bộ câu "
        "(TUYỆT ĐỐI KHÔNG xưng 'em' hay 'tôi', KHÔNG viết 'Chào bạn, em...'; hãy mở đầu tự "
        "nhiên như 'Dạ, mình gợi ý...' hoặc 'Với nhu cầu này, mình gợi ý...'). "
        "QUY TẮC: (1) CHỈ nêu ĐÚNG tên sản phẩm, ĐÚNG giá VND đã định dạng (không tự thêm bớt "
        "số 0), khuyến mãi, quy cách, đơn giá có thật trong ngữ cảnh. (2) " + mode_hint + " "
        "LUÔN giới thiệu ĐÚNG sản phẩm trong mục SẢN PHẨM GỢI Ý CHÍNH trước tiên để khớp thẻ "
        "trên màn hình. "
        "(3) Nếu có mục sản phẩm cùng loại khác bên dưới, nhắc nhẹ 1 ý để khách tham khảo thêm. "
        "(4) Không bịa thành phần, công dụng chữa bệnh, cam kết dị ứng, số bán thật hay tồn kho."
        + exclusion_rule
    )
    enriched_memory = dict(memory)
    if "working_basket" not in enriched_memory and primary_products:
        enriched_memory["working_basket"] = [
            {
                "product_id": p.product_id,
                "name": p.name,
                "price_vnd": p.price_vnd,
                "qty": 1,
            }
            for p in primary_products
        ]
    if budget_vnd is not None and "working_budget_vnd" not in enriched_memory:
        enriched_memory["working_budget_vnd"] = int(budget_vnd)
    recent_ids = list(enriched_memory.get("recent_product_ids") or [])
    for p in primary_products:
        if p.product_id in recent_ids:
            recent_ids.remove(p.product_id)
        recent_ids.insert(0, p.product_id)
    if recent_ids:
        enriched_memory["recent_product_ids"] = recent_ids[:8]
    user_context = build_plain_prompt_text(
        customer_request=text,
        primary_products=primary_products,
        related_products=related_products,
        excluded_terms=excluded_terms,
        mode=mode,
        budget_vnd=budget_vnd,
        extra_note=extra_note,
    )
    return StreamingConsultationPlan(
        products=primary_products,
        related_products=related_products,
        memory=enriched_memory,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_context},
        ],
        fallback_text=_local_fallback_text(text, plan_for_fallback, primary_products),
    )


async def _prepare_comparison_streaming(
    *,
    text: str,
    targets: tuple[str, ...],
    repository: ProductRepository,
    previous_memory: dict,
    parsed,
) -> StreamingConsultationPlan | None:
    """Zero-planner local retrieval for 2-target comparisons -> live SSE stream."""
    raw_a, raw_b = targets[0].strip(), targets[1].strip()
    if not raw_a or not raw_b:
        return None
    norm_a = normalize_category_text(raw_a)
    norm_b = normalize_category_text(raw_b)
    query_a = _clean_lexical_query(raw_a) or raw_a
    query_b = _clean_lexical_query(raw_b) or raw_b

    # Inherit the full shared category head (e.g. "tương ớt Chinsu" vs "Cholimex" ->
    # "tương ớt Cholimex", NOT 1-word "tương Cholimex" which matches "Tương cà"!).
    pt_norm = normalize_category_text(parsed.product_type or "") if parsed is not None else ""
    shared_head = ""
    if pt_norm and pt_norm in norm_a:
        shared_head = pt_norm
    else:
        head_words = norm_a.split()
        if len(head_words) >= 3:
            shared_head = " ".join(head_words[:2])
        elif head_words:
            shared_head = head_words[0]

    if shared_head and shared_head not in norm_b and len(norm_b.split()) <= 2:
        query_b_prefixed = f"{shared_head} {query_b}"
    else:
        query_b_prefixed = query_b

    def _search_pair() -> tuple[list[ProductSnapshot], list[ProductSnapshot]]:
        hits_a = repository.search(query=query_a, limit=18)
        hits_b = repository.search(query=query_b_prefixed, limit=18)
        if not hits_b and query_b_prefixed != query_b:
            hits_b = repository.search(query=query_b, limit=18)
        return hits_a, hits_b

    hits_a, hits_b = await run_catalog_work(_search_pair, timeout_seconds=3)
    hits_a = _apply_streaming_constraints(hits_a, text, parsed, enforce_budget=False)
    hits_b = _apply_streaming_constraints(hits_b, text, parsed, enforce_budget=False)
    if not hits_a or not hits_b:
        return None
    if shared_head:
        head_a = [
            p for p in hits_a
            if shared_head in normalize_category_text(f"{p.name} {p.category or ''}")
        ]
        head_b = [
            p for p in hits_b
            if shared_head in normalize_category_text(f"{p.name} {p.category or ''}")
        ]
        if head_a:
            hits_a = head_a
        if head_b:
            hits_b = head_b
    focused_a, scores_a = _filter_streaming_candidates(hits_a, query_a, parsed.product_type)
    focused_b, scores_b = _filter_streaming_candidates(
        hits_b, query_b_prefixed, parsed.product_type
    )
    best_a = _rank_streaming_candidates(focused_a, raw_a, focus_scores=scores_a)[0]
    best_b = next(
        (
            p
            for p in _rank_streaming_candidates(focused_b, raw_b, focus_scores=scores_b)
            if p.product_id != best_a.product_id
        ),
        None,
    )
    if best_b is None:
        return None
    primary = [best_a, best_b]
    related = _related_candidates([*hits_a[:4], *hits_b[:4]], primary, limit=4)
    searches = [
        SearchNeed(category=best_a.category or raw_a, role=raw_a),
        SearchNeed(category=best_b.category or raw_b, role=raw_b),
    ]
    plan = ConsultationDecision(
        action="compare",
        need=text[:500],
        searches=searches,
        max_options=2,
        sugar="any",
    )
    memory = {
        "need": text[:500],
        "last_action": "compare",
        "last_question": "",
        "pending_question": None,
        "answered_slots": previous_memory.get("answered_slots", []),
        "constraints": plan.model_dump(
            exclude={
                "question", "need", "guidance", "action",
                "pending_question", "clarification_answer",
            }
        ),
        "selection": {"picks": [{"candidate": 0}, {"candidate": 1}]},
    }
    return _build_streaming_plan_payload(
        text=text,
        primary_products=primary,
        related_products=related,
        repository=repository,
        sources={best_a.product_id: [0], best_b.product_id: [1]},
        memory=memory,
        plan_for_fallback=plan,
        mode="compare",
    )


async def _prepare_promo_browse_streaming(
    *,
    text: str,
    repository: ProductRepository,
    previous_memory: dict,
    budget_vnd: int | None = None,
) -> StreamingConsultationPlan | None:
    """Zero-planner local scan of top discounted catalog items -> live SSE stream."""
    def _pick_promos() -> list[ProductSnapshot]:
        pool = list(getattr(repository, "products", None) or repository.search("", limit=500))
        discounted = [
            p for p in pool
            if (_product_discount_pct(p) or 0) > 0
            and (budget_vnd is None or p.price_vnd <= budget_vnd)
        ]
        discounted.sort(
            key=lambda p: (-(_product_discount_pct(p) or 0), p.price_vnd, p.name)
        )
        diverse: list[ProductSnapshot] = []
        seen_cats: set[str] = set()
        for item in discounted:
            cat_key = normalize_category_text(item.category or item.department or item.name)
            if cat_key in seen_cats:
                continue
            seen_cats.add(cat_key)
            diverse.append(item)
            if len(diverse) >= 5:
                break
        return diverse or discounted[:5]

    picks = await run_catalog_work(_pick_promos, timeout_seconds=3)
    if not picks:
        return None
    primary = [picks[0]]
    related = picks[1:5]
    plan = ConsultationDecision(
        action="recommend",
        need=text[:500],
        searches=[SearchNeed(category=picks[0].category or "Khuyến mãi", role="khuyến mãi")],
        max_price_vnd=budget_vnd,
        sugar="any",
    )
    memory = {
        "need": text[:500],
        "last_action": "recommend",
        "last_question": "",
        "pending_question": None,
        "answered_slots": previous_memory.get("answered_slots", []),
        "constraints": plan.model_dump(
            exclude={
                "question", "need", "guidance", "action",
                "pending_question", "clarification_answer",
            }
        ),
        "selection": {"picks": [{"candidate": 0}]},
    }
    return _build_streaming_plan_payload(
        text=text,
        primary_products=primary,
        related_products=related,
        repository=repository,
        sources={picks[0].product_id: [0]},
        memory=memory,
        plan_for_fallback=plan,
        mode="promo",
    )


async def _prepare_slim_basket_streaming(
    *,
    text: str,
    repository: ProductRepository,
    groq: GroqService,
    previous_memory: dict,
    budget_vnd: int | None,
    parsed=None,
) -> StreamingConsultationPlan | None:
    """1-call slim keyword planner -> deterministic budget basket -> live SSE stream.

    Replaces the 2-3x non-streaming ConsultationDecision + GroundedSelection + Review
    pipeline for multi-item meal/combo requests (e.g. 'mua đồ nấu lẩu thái dưới 300k').
    """
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Trích xuất 2 đến 4 từ khóa sản phẩm siêu thị ngắn gọn (tiếng Việt) "
                "đúng vai trò ẩm thực cho yêu cầu của khách. Chỉ trả đúng 1 JSON array chuỗi, "
                "không giải thích. Quy tắc: bữa cơm/tối dùng thịt/trứng/đậu + rau xanh (KHÔNG "
                "chọn snack/bánh kẹo); lẩu/BBQ dùng sốt/nước lẩu + thịt/chả viên + rau/mì; "
                "mì Ý dùng mì Ý + sốt mì Ý/cà chua + thịt bò; "
                "xem phim/liên hoan/tiệc nhẹ dùng snack + bánh quy + nước ngọt/trà; "
                "đi xa/mang đi học/không có lò vi sóng dùng bánh mì tươi + bánh bông lan + "
                "xúc xích ăn liền + sữa tươi/nước suối (TUYỆT ĐỐI KHÔNG chọn bột nấu ăn, bóng cá, "
                "sữa bột trẻ em); ăn sáng dùng bánh mì tươi/ngũ cốc/phở/xúc xích + sữa/cà phê. "
                "Ví dụ: [\"bánh mì tươi\", \"sữa tươi\", \"snack\"]."
            ),
        },
        {"role": "user", "content": text[:240]},
    ]
    try:
        fast_fn = getattr(groq, "fast_chat", None)
        if callable(fast_fn):
            result = await asyncio.wait_for(
                fast_fn(prompt_messages, max_tokens=120, temperature=0.1),
                timeout=5.5,
            )
        else:
            result = await asyncio.wait_for(
                groq.chat(prompt_messages, max_tokens=120, reasoning_effort="none"),
                timeout=6.0,
            )
        raw_val = getattr(result, "text", None) or getattr(result, "content", None) or result
        raw = str(raw_val).strip()
        start_idx = raw.find("[")
        end_idx = raw.rfind("]")
        if start_idx < 0 or end_idx <= start_idx:
            return None
        keywords = json.loads(raw[start_idx : end_idx + 1])
        if not isinstance(keywords, list):
            return None
        clean_keywords = [
            str(item).strip()[:60]
            for item in keywords
            if isinstance(item, str) and str(item).strip()
        ][:4]
        if not clean_keywords:
            return None
    except Exception:
        return None

    norm_request = normalize_category_text(text)
    savory_meal = bool(_SAVORY_MEAL_CUE_RE.search(norm_request))
    wants_utensil = bool(_HOUSEHOLD_UTENSIL_CUE_RE.search(norm_request))
    ready_eat = bool(_READY_TO_EAT_NEED_RE.search(norm_request))
    food_need = is_food_or_drink_need(text)
    wants_bulk = wants_bulk_pack(text)
    excluded = _clean_negative_terms(parsed) if parsed is not None else []

    def _search_basket() -> list[tuple[str, list[ProductSnapshot]]]:
        grouped: list[tuple[str, list[ProductSnapshot]]] = []
        for kw in clean_keywords:
            hits = repository.search(query=kw, limit=18)
            if excluded:
                hits = [p for p in hits if not _item_violates_exclusions(p, excluded)]
            if (food_need or savory_meal) and not wants_utensil:
                hits = filter_edible_candidates(hits, ready_to_eat=ready_eat) or [
                    p
                    for p in hits
                    if normalize_category_text(p.department or "") != "phi thuc pham"
                ]
            if savory_meal:
                non_snack = [
                    p
                    for p in hits
                    if not _SNACK_JUNK_FOR_MEAL_RE.search(
                        normalize_category_text(f"{p.name} {p.category or ''}")
                    )
                ]
                hits = non_snack or hits
            if not wants_bulk:
                singles = [p for p in hits if not is_bulk_pack(p)]
                if singles:
                    hits = singles
            if hits:
                grouped.append((kw, hits))
        return grouped

    grouped = await run_catalog_work(_search_basket, timeout_seconds=3)
    if not grouped:
        return None
    if budget_vnd is not None:
        grouped.sort(key=lambda pair: min(p.price_vnd for p in pair[1]))

    # Greedily assemble 1 verified SKU per role respecting total budget_vnd.
    picked: list[ProductSnapshot] = []
    related_pool: list[ProductSnapshot] = []
    used_ids: set[str] = set()
    running_total = 0
    sources: dict[str, list[int]] = {}
    for role_idx, (_kw, hits) in enumerate(grouped):
        ordered = sorted(
            hits,
            key=lambda p: (
                p.price_vnd if budget_vnd is not None else -(_product_discount_pct(p) or 0),
                p.price_vnd,
                p.name,
            ),
        )
        chosen = None
        for item in ordered:
            if item.product_id in used_ids:
                continue
            if budget_vnd is not None and running_total + item.price_vnd > budget_vnd:
                continue
            chosen = item
            break
        if chosen is not None:
            picked.append(chosen)
            used_ids.add(chosen.product_id)
            running_total += chosen.price_vnd
            sources[chosen.product_id] = [role_idx]
        related_pool.extend(ordered[:3])

    if not picked:
        return None
    related = _related_candidates(related_pool, picked, limit=4)
    plan = ConsultationDecision(
        action="recommend",
        need=text[:500],
        basket=len(picked) >= 2,
        total_budget_vnd=budget_vnd,
        searches=[
            SearchNeed(category=p.category or kw, role=kw)
            for p, (kw, _h) in zip(picked, grouped)
        ],
        sugar="any",
    )
    memory = {
        "need": text[:500],
        "last_action": "recommend",
        "last_question": "",
        "pending_question": None,
        "answered_slots": previous_memory.get("answered_slots", []),
        "constraints": plan.model_dump(
            exclude={
                "question", "need", "guidance", "action",
                "pending_question", "clarification_answer",
            }
        ),
        "selection": {"picks": [{"candidate": i} for i in range(len(picked))]},
    }
    return _build_streaming_plan_payload(
        text=text,
        primary_products=picked,
        related_products=related,
        repository=repository,
        sources=sources,
        memory=memory,
        plan_for_fallback=plan,
        mode="basket",
        budget_vnd=budget_vnd,
    )


async def _prepare_product_detail_streaming(
    *,
    text: str,
    answer_text: str,
    norm_answer: str,
    visible: list[ProductSnapshot],
    repository: ProductRepository,
    previous_memory: dict,
) -> StreamingConsultationPlan | None:
    """Path F: Verified product detail/usage/ingredient/storage streaming (0 planner calls)."""
    requested_attrs = [
        attr for attr, pat in _STREAM_DETAIL_ATTR_PATTERNS if pat.search(norm_answer)
    ]
    if not requested_attrs:
        return None
    cleaned_native = _NATIVE_STREAM_OPENER_RE.sub(" ", answer_text)
    cleaned_native = re.sub(r"\bở\s+đâu\b", " ", cleaned_native, flags=re.IGNORECASE)
    stripped_query = _STREAM_DETAIL_STRIP_RE.sub(
        " ", normalize_category_text(strip_retrieval_budget(cleaned_native))
    )
    detail_stop = frozenset(
        {
            "cach", "nhu", "su", "huong", "dan", "thanh",
            "phan", "bao", "quan", "xuat", "xu", "nuoc",
        }
    )
    target_lex = " ".join(
        tok
        for tok in stripped_query.split()
        if len(tok) >= 2 and tok not in _STREAM_FILLER_TOKENS and tok not in detail_stop
    )
    target: ProductSnapshot | None = None
    related: list[ProductSnapshot] = []
    # Keep focus locked on the visible product when the shopper uses a demonstrative
    # reference ("bánh này", "chai này", "sữa đó") matching the active item.
    has_deictic_ref = bool(re.search(r"\b(?:này|đó|kia)\b", answer_text, re.IGNORECASE))
    if visible and has_deictic_ref:
        vis0_hay = normalize_category_text(f"{visible[0].name} {visible[0].category or ''}")
        lex_toks = target_lex.split()
        if not lex_toks or (len(lex_toks) <= 2 and all(t in vis0_hay for t in lex_toks)):
            target = visible[0]
            related = visible[1:5]
    if target is None and target_lex:
        lex_hits = await run_catalog_work(
            repository.search, query=target_lex, limit=8, timeout_seconds=3
        )
        if lex_hits:
            focused, focus_scores = _filter_streaming_candidates(lex_hits, target_lex, None)
            ordered = _rank_streaming_candidates(focused, target_lex, focus_scores=focus_scores)
            target = ordered[0]
            related = _related_candidates(ordered, [target], limit=4)
    elif target is None and visible:
        target = visible[0]
        related = visible[1:5]
    if target is None:
        return None

    # Re-run guidance safety gate with the resolved product name & category so clinical/
    # sensitive/chemical/infant/compatibility items still route to the strict reviewer.
    combined_guidance = shopping_guidance(
        f"{answer_text} {target.name} {target.category or ''}", limit=32
    )
    if any(
        item.get("domain") in _STREAM_HIGH_RISK_DOMAINS
        and item.get("id") not in _STREAM_SAFE_GUIDANCE_IDS
        for item in combined_guidance
    ):
        return None

    facts = await run_catalog_work(
        product_knowledge_facts,
        target,
        ("usage", "ingredients", "storage", "origin", "summary"),
        source="snapshot",
        timeout_seconds=3,
    )
    verified_details: dict[str, str] = {
        fact.attribute: str(fact.value).strip()
        for fact in facts
        if fact.status == "VERIFIED" and str(fact.value or "").strip()
    }
    missing_fields = [attr for attr in requested_attrs if attr not in verified_details]
    ordinary_guidance = [
        {"source": str(item.get("source") or ""), "summary": str(item.get("summary") or "")}
        for item in combined_guidance
        if item.get("domain") == "ordinary" and item.get("summary")
    ][:2]

    product_card = _lean_streaming_product_card(target)
    if target.category:
        product_card["category"] = target.category
    payload_obj: dict[str, object] = {
        "customer_request": text,
        "product": product_card,
        "requested_fields": requested_attrs,
        "verified_details": verified_details,
        "missing_verified_fields": missing_fields,
    }
    if ordinary_guidance and missing_fields:
        payload_obj["general_guidance"] = ordinary_guidance

    system_prompt = (
        "Bạn là Mai, trợ lý siêu thị thân thiện. Trả lời trực tiếp bằng tiếng Việt tự nhiên, "
        "ngắn gọn trong 2–3 câu liền mạch (không gạch đầu dòng, không markdown **). "
        "XƯNG HÔ BẮT BUỘC: luôn xưng 'mình' và gọi khách là 'bạn' nhất quán trong toàn bộ câu "
        "(TUYỆT ĐỐI KHÔNG xưng 'em' hay 'tôi', mở đầu tự nhiên bằng 'Dạ, ...'). "
        "QUY TẮC NGHIÊM NGẶT: "
        "(1) CHỈ sử dụng thông tin có thật trong verified_details của JSON. "
        "(2) Nếu mục khách hỏi nằm trong missing_verified_fields, nói rõ là hệ thống chưa có "
        "thông tin riêng in trên bao bì của sản phẩm này và khuyên khách đọc kỹ nhãn trước khi "
        "dùng; TUYỆT ĐỐI KHÔNG tự bịa hướng dẫn sử dụng, thành phần, liều lượng hay xuất xứ. "
        "(3) Nếu có general_guidance, có thể chia sẻ ngắn gọn 1 ý tham khảo chung (nêu rõ là "
        "hướng dẫn chung, không thay thế nhãn bao bì sản phẩm)."
    )

    if not missing_fields:
        joined = "; ".join(
            f"{_DETAIL_ATTR_LABELS_VI.get(attr, attr)}: {verified_details[attr]}"
            for attr in requested_attrs
        )
        fallback_text = (
            f"Dạ, với {target.name} ({target.price_vnd:,}đ), thông tin mình có là {joined}. "
            "Bạn xem thêm trên bao bì khi sử dụng để đảm bảo chính xác nhất nhé."
        ).replace(",", ".")
    else:
        missing_vi = ", ".join(_DETAIL_ATTR_LABELS_VI.get(attr, attr) for attr in missing_fields)
        fallback_text = (
            f"Dạ, hiện tại hệ thống mình chưa có {missing_vi} riêng in trên bao bì của "
            f"{target.name}, nên bạn vui lòng xem kỹ nhãn và hướng dẫn trên bao bì "
            "trước khi dùng nhé."
        )

    memory = {
        "need": previous_memory.get("need") or text[:500],
        "last_action": "recommend",
        "last_question": "",
        "pending_question": None,
        "answered_slots": previous_memory.get("answered_slots", []),
        "constraints": previous_memory.get("constraints", {}),
        "selection": {"picks": [{"candidate": 0}]},
    }
    return StreamingConsultationPlan(
        products=[target],
        related_products=related,
        memory=memory,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        fallback_text=fallback_text,
    )


async def _prepare_open_need_streaming(
    *,
    text: str,
    answer_text: str,
    repository: ProductRepository,
    groq: GroqService,
    previous_memory: dict,
    parsed,
) -> StreamingConsultationPlan | None:
    """Path G: 1-call Groq LPU keyword expansion for open/situational needs -> live stream.

    Handles novel or situational Vietnamese requests that do not literally name a catalog
    product (e.g. 'đau họng mua gì dễ chịu', 'quà biếu bố mẹ tầm 300k', 'say xe mua gì')
    in ~1.0-1.4s total while abstaining back to the full planner for medical/unsafe requests.
    """
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Bạn là bộ ánh xạ sản phẩm siêu thị Thanh Lam Mart. Nếu yêu cầu của khách "
                "hỏi thuốc điều trị bệnh, bệnh lý nặng, dị ứng nghiêm trọng, trẻ sơ sinh, "
                "hóa chất nguy hiểm hoặc ngoài phạm vi siêu thị, trả đúng []. Ngược lại, gợi ý "
                "1 đến 3 từ "
                "khóa sản phẩm siêu thị phổ biến (tiếng Việt ngắn gọn) phù hợp nhất với nhu cầu "
                "đời thường của khách (ví dụ đau rát họng nhẹ -> [\"trà gừng\", \"mật ong\", "
                "\"kẹo ngậm\"], quà biếu/món quà nhỏ -> [\"bánh quy hộp\", \"trà\", \"cà phê\"], "
                "đói/ăn ngay/không lò vi sóng/đi học -> [\"bánh mì tươi\", \"bánh bông lan\", "
                "\"xúc xích ăn liền\"], ăn kiêng -> [\"yến mạch\", \"sữa tươi không đường\", "
                "\"bánh ăn kiêng\"], tỉnh táo/đỡ buồn ngủ -> [\"cà phê nước\", \"nước tăng lực\", "
                "\"trà xanh\"], trời nóng -> [\"trà bí đao\", \"nước dừa\", \"nước suối\"]). "
                "TUYỆT ĐỐI KHÔNG chọn nguyên liệu sống, bột nấu ăn, bóng cá, hay đồ phi thực phẩm "
                "cho nhu cầu ăn uống. CHỈ trả đúng 1 JSON array chuỗi, không giải thích."
            ),
        },
        {"role": "user", "content": text[:240]},
    ]
    try:
        result = await asyncio.wait_for(
            groq.fast_chat(prompt_messages, max_tokens=80, temperature=0.1),
            timeout=4.0,
        )
        raw_val = getattr(result, "text", None) or getattr(result, "content", None) or result
        raw = str(raw_val).strip()
        start_idx = raw.find("[")
        end_idx = raw.rfind("]")
        if start_idx < 0 or end_idx <= start_idx:
            return None
        keywords = json.loads(raw[start_idx : end_idx + 1])
        if not isinstance(keywords, list):
            return None
        clean_keywords = [
            str(item).strip()[:50]
            for item in keywords
            if isinstance(item, str) and str(item).strip()
        ][:3]
        if not clean_keywords:
            return None
    except Exception:
        return None

    food_need = is_food_or_drink_need(answer_text)
    ready_eat = bool(_READY_TO_EAT_NEED_RE.search(normalize_category_text(answer_text)))
    wants_bulk = wants_bulk_pack(answer_text)

    def _search_open() -> list[ProductSnapshot]:
        seen_ids: set[str] = set()
        merged: list[ProductSnapshot] = []
        for kw in clean_keywords:
            norm_kw = normalize_category_text(kw)
            hits = repository.search(query=kw, limit=18)
            grounded_open = [p for p in hits if _hit_is_grounded(kw, p)]
            if grounded_open:
                hits = grounded_open
            else:
                # Reject false partial-token noise (e.g. 'kẹo cao su' -> 'Bánh cao cấp Orion')
                continue
            if food_need:
                edible = filter_edible_candidates(hits, ready_to_eat=ready_eat)
                hits = edible if ready_eat else (edible or hits)
            if not wants_bulk:
                singles = [p for p in hits if not is_bulk_pack(p)]
                if singles:
                    hits = singles
            hits = _apply_streaming_constraints(hits, answer_text, parsed)
            # Prefer products whose title starts with the keyword or whose category matches
            # it directly (e.g. 'Trà gừng' / 'Mật ong', not 'Chân gà vị mật ong').
            head_hits = [
                item
                for item in hits
                if normalize_category_text(item.name).startswith(norm_kw)
                or norm_kw in normalize_category_text(item.category or "")
            ]
            chosen_hits = head_hits or hits
            for item in chosen_hits[:2]:
                if item.product_id not in seen_ids:
                    seen_ids.add(item.product_id)
                    merged.append(item)
        return merged

    candidates = await run_catalog_work(_search_open, timeout_seconds=3)
    if not candidates:
        return None
    top = candidates[0]
    norm_ans_open = normalize_category_text(answer_text)
    is_non_clinical_diet_open = bool(
        re.search(r"\ban\s+kieng\b", norm_ans_open)
        and not re.search(
            r"\b(?:tieu duong|huyet ap|tim mach|gout|benh|than|gan|di ung|mang thai|tre so sinh)\b",
            norm_ans_open,
        )
    )
    post_guidance = shopping_guidance(f"{answer_text} {top.name} {top.category or ''}", limit=16)
    if not is_non_clinical_diet_open and any(
        item.get("domain") in _STREAM_HIGH_RISK_DOMAINS
        and item.get("id") not in _STREAM_SAFE_GUIDANCE_IDS
        for item in post_guidance
    ):
        return None

    primary_products = candidates[:1]
    related_products = _related_candidates(candidates, primary_products, limit=4)
    open_plan = ConsultationDecision(
        action="recommend",
        need=text[:500],
        searches=[
            SearchNeed(
                category=primary_products[0].category or clean_keywords[0],
                role=clean_keywords[0],
                excluded=_clean_negative_terms(parsed),
            )
        ],
        max_price_vnd=parsed.budget_vnd,
        sugar=_explicit_sugar_level(answer_text) or "any",
    )
    memory = {
        "need": text[:500],
        "last_action": "recommend",
        "last_question": "",
        "pending_question": None,
        "answered_slots": previous_memory.get("answered_slots", []),
        "constraints": open_plan.model_dump(
            exclude={
                "question", "need", "guidance", "action",
                "pending_question", "clarification_answer",
            }
        ),
        "selection": {"picks": [{"candidate": 0}]},
    }
    return _build_streaming_plan_payload(
        text=text,
        primary_products=primary_products,
        related_products=related_products,
        repository=repository,
        sources={primary_products[0].product_id: [0]},
        memory=memory,
        plan_for_fallback=open_plan,
        mode="recommend",
    )


_PACK_OR_MEASURE_PREFIX_RE = re.compile(
    r"^(?:thùng|lốc|bịch|hộp|gói|túi|chai|lon|vỉ|khay|can|hũ|lọ|cặp|combo|bộ|set)\s+"
    r"(?:\d+\s*(?:gói|chai|lon|hộp|túi|vỉ|miếng|chiếc|cuộn|cây|thanh|quả|trái)?\s*)?",
    flags=re.IGNORECASE,
)
_TITLE_MODIFIER_SPLIT_RE = re.compile(
    r"\b(?:vị|hương\s+vị|hương|chiết\s+xuất|tinh\s+chất|tinh\s+dầu|ruột|nhân)\b",
    flags=re.IGNORECASE,
)
_GENERIC_ATTR_FOCUS_WORDS = frozenset(
    {
        "duong", "ngot", "calo", "gas", "cay", "tuoi", "lanh", "nong", "mat", "dong",
        "chai", "lon", "hop", "goi", "thung", "loc", "tui", "bich", "can", "tuyp",
        "hu", "lo", "vi", "khay", "cuc", "vien", "mieng", "thanh", "thoi", "cuon",
        "an", "lien", "uong", "giai", "khat", "nho", "to",
        "be", "mini", "it", "khong", "giam", "cao", "thap", "sach", "thom", "mem",
        "min", "gion", "deo", "ngon", "chua", "man", "dam", "nhat", "nguyen", "chat",
        "huu", "co", "tu", "nhien", "nhap", "khau", "noi", "dia", "viet", "nam",
        "han", "quoc", "nhat", "ban", "thai", "lan", "my", "uc", "phap", "duc",
        "cho", "tre", "em", "nguoi", "lon", "gia", "dinh", "loai", "hang", "tot",
    }
)
_HOMOGENEOUS_MULTI_LEAF_CATS = frozenset(
    {
        "Trái cây",
        "Rau củ",
        "Rau gia vị",
        "Nấm",
        "Thủy hải sản",
        "Bánh mỳ",
        "Sữa tươi",
        "Sữa chua",
        "Snack",
        "Cà phê",
        "Trà",
        "Bia, Cider",
        "Dầu ăn",
        "Phô mai",
        "Trứng",
        "Đường",
        "Bơ",
        "Thịt Bò, Bê",
        "Lợn (heo)",
        "Thịt gà",
        "Sữa nguồn gốc thực vật",
        "Hạt sấy khô",
        "Ô mai & Trái cây sấy khô",
        "Giấy ăn, giấy vệ sinh",
        "Băng vệ sinh",
        "Tã bỉm",
    }
)
_SITUATIONAL_OR_ADVICE_RE = re.compile(
    r"\b(?:mua\s+gi|an\s+gi|uong\s+gi|dung\s+gi|tang\s+gi|lam\s+gi|chon\s+gi|"
    r"dau\s+hong|say\s+xe|troi\s+nong|buon\s+ngu|tinh\s+tao|an\s+kieng|giam\s+can|"
    r"qua\s+bieu|tang\s+qua|dang\s+doi|di\s+hoc|di\s+lam|di\s+choi|du\s+lich|"
    r"cam\s+cum|dau\s+bung|di\s+ung|tieu\s+duong|huyet\s+ap|mang\s+thai|"
    r"tre\s+so\s+sinh|em\s+be|cho\s+be|thuoc\s+(?:cam|ho|sot|dau|khang|tri))\b"
)
_OFFLINE_NON_CATALOG_HEAD_RE = re.compile(
    r"\b(?:do\s+khui|mo\s+nap|dung\s+cu\s+mo|bang\s+ca\s+nhan|urgo|"
    r"sac\s+dien\s+thoai|cap\s+sac|ao\s+mua|o\s+du|bong\s+den|banh\s+trang\s+tron)\b"
)
_SEMANTIC_BRIDGE_CACHE: dict[str, tuple[str, str | None, tuple[str, ...]]] = {}
_SEMANTIC_BRIDGE_CACHE_MAX = 512
_SEMANTIC_BRIDGE_SYSTEM_PROMPT = (
    "Bạn là bộ đối chiếu danh mục siêu thị Thanh Lam Mart (Việt Nam). "
    "Từ câu hỏi khách và danh sách ứng viên thực tế trong kho (nhóm C# hoặc sản phẩm S#), "
    "trả đúng 1 JSON:\n"
    '{"pick":"C1"|"S1"|null,"kw":["từ khóa chuẩn trên bao bì sản phẩm nếu cần tìm/lọc hẹp"]}\n'
    "Quy tắc:\n"
    "1. Chọn C# hoặc S# nếu ĐÚNG loại hàng khách hỏi (chấp nhận đồng nghĩa/từ địa phương/"
    "tiếng Anh: vd kẹo cao su=Kẹo gum, lăn nách=Lăn khử mùi, rau câu=Thạch, xì dầu=Nước tương, "
    "pin tiểu=Pin AA/AAA, tàu hũ=Đậu phụ/Đậu hũ).\n"
    "2. Nếu chọn nhóm rộng hơn món khách hỏi (vd khách hỏi mì ý mà nhóm là Mỳ khô), ghi từ "
    'lọc chuẩn vào kw (vd ["spaghetti","mì ý"]).\n'
    "3. TUYỆT ĐỐI KHÔNG chọn nhầm/đánh tráo: phụ kiện ≠ đồ chính (đồ khui bia ≠ bia), "
    "y tế/điện tử/quần áo không có trong ứng viên -> pick=null (vd băng cá nhân ≠ băng keo/"
    "dầu gội/băng vệ sinh; bánh tráng trộn ≠ bánh tráng sống). Nếu pick=null nhưng món đó có "
    "thể bán ở siêu thị dưới tên khác, ghi 1-2 tên phổ biến nhất vào kw; nếu không có ở ứng "
    "viên vẫn giữ pick=null.\n"
    "CHỈ trả JSON, không giải thích."
)


def _leaf_category_segment(product: ProductSnapshot) -> str:
    hier = product.category_hierarchy or ""
    parts = [s.strip() for s in hier.split(">") if s.strip()]
    return parts[-1] if parts else (product.category or "")


def _native_words(text: str) -> list[str]:
    norm = unicodedata.normalize("NFC", text.strip().lower())
    return [t for t in re.sub(r"[^\w\s]", " ", norm, flags=re.UNICODE).split() if t]


def _is_direct_category_browse(native_q: str, sem_cat: str) -> bool:
    """True when the shopper's query directly names the predicted category (safe 0ms browse)."""
    q_toks = _native_words(native_q)
    cat_toks = set(normalize_category_text(sem_cat).split())
    if not q_toks or not cat_toks:
        return False
    return all(normalize_category_text(tok) in cat_toks for tok in q_toks)


def _hit_is_grounded(
    query: str,
    product: ProductSnapshot,
    *,
    require_head_start: bool = False,
) -> bool:
    """Verify a lexical candidate genuinely matches query identity in title + leaf category.

    Prevents false partial-token or department-only matches (e.g. 'băng cá nhân' ->
    'Dầu gội Clear Băng Lạnh' via department 'Chăm sóc cá nhân', or 'kẹo cao su' ->
    'Bánh cao cấp Orion', or 'tương đen' -> 'Mì tương đen').
    """
    q_nt = _native_words(query)
    if not q_nt:
        return False
    leaf = _leaf_category_segment(product)
    raw_name = unicodedata.normalize("NFC", product.name.strip().lower())
    brand_raw = unicodedata.normalize("NFC", (product.brand or "").strip().lower())
    hay_native = unicodedata.normalize("NFC", f"{raw_name} {brand_raw} {leaf.lower()}")
    hay_nt = set(_native_words(hay_native))
    hay_f = normalize_category_text(hay_native)
    hay_ft = set(hay_f.split())
    hay_c = hay_f.replace(" ", "")

    head_part = _TITLE_MODIFIER_SPLIT_RE.split(raw_name, maxsplit=1)[0]
    stripped_head = _PACK_OR_MEASURE_PREFIX_RE.sub("", head_part).strip()
    head_nt = _native_words(stripped_head)
    brand_nt = _native_words(brand_raw)
    leaf_nt = set(_native_words(leaf))
    leaf_ft = set(normalize_category_text(leaf).split())
    # Strip leading brand prefix from title head (e.g. "ORCHID Dầu Thực Vật" -> "dầu thực vật").
    non_brand_head = list(head_nt)
    if brand_nt and len(non_brand_head) > len(brand_nt):
        if non_brand_head[: len(brand_nt)] == brand_nt:
            non_brand_head = non_brand_head[len(brand_nt) :]
    head_and_leaf_nt = set(head_nt) | set(brand_nt) | leaf_nt
    head_and_leaf_ft = (
        set(normalize_category_text(stripped_head).split())
        | set(normalize_category_text(brand_raw).split())
        | leaf_ft
    )
    head_and_leaf_c = normalize_category_text(f"{stripped_head} {brand_raw} {leaf}").replace(
        " ", ""
    )

    def _tok_in(nt: str, nt_set: set[str], ft_set: set[str], compact: str) -> bool:
        ft = normalize_category_text(nt)
        if not ft:
            return False
        has_accent = nt != ft
        if has_accent and len(nt) <= 3:
            return nt in nt_set
        return nt in nt_set or ft in ft_set or (len(ft) >= 4 and ft in compact)

    if len(q_nt) == 1:
        # 1-word query must match in head/brand/leaf (never solely after flavor 'hương'/'vị').
        return _tok_in(q_nt[0], head_and_leaf_nt, head_and_leaf_ft, head_and_leaf_c)

    if require_head_start:
        first_nt = q_nt[0]
        first_ft = normalize_category_text(first_nt)
        head0_nt = {non_brand_head[0]} if non_brand_head else set()
        head0_ft = {normalize_category_text(non_brand_head[0])} if non_brand_head else set()
        brand_ft = set(normalize_category_text(brand_raw).split())
        brand_compact = normalize_category_text(brand_raw).replace(" ", "")
        leaf_compact = normalize_category_text(leaf).replace(" ", "")
        head_start_compact = "".join(
            normalize_category_text(t) for t in non_brand_head[:2]
        )
        q_head2_compact = (
            normalize_category_text(q_nt[0] + q_nt[1]) if len(q_nt) >= 2 else ""
        )
        in_start_or_leaf = (
            _tok_in(first_nt, head0_nt, head0_ft, "")
            or _tok_in(first_nt, set(brand_nt), brand_ft, brand_compact)
            or _tok_in(first_nt, leaf_nt, leaf_ft, leaf_compact)
            or (
                first_ft
                and len(q_head2_compact) >= 4
                and (
                    q_head2_compact == head_start_compact
                    or q_head2_compact in leaf_compact
                )
            )
        )
        if not in_start_or_leaf:
            return False

    matched = sum(1 for nt in q_nt if _tok_in(nt, hay_nt, hay_ft, hay_c))
    if matched < len(q_nt):
        q_comp = "".join(normalize_category_text(t) for t in q_nt)
        if len(q_comp) >= 4 and q_comp in hay_c:
            matched = len(q_nt)
    if len(q_nt) == 2:
        return matched == 2
    return matched >= max(2, (len(q_nt) * 2 + 2) // 3)


def _unmatched_identity_tokens(
    answer_text: str,
    product_type: str | None,
    candidates: list[ProductSnapshot],
) -> list[str]:
    """Return concrete product-identity tokens from query absent from every candidate."""
    focus = _focus_tokens_for_streaming(answer_text, product_type)
    identity_toks = [
        tok
        for tok in focus
        if tok not in _GENERIC_ATTR_FOCUS_WORDS
        and tok not in {"do", "vat", "dung", "cu"}
        and not any(ch.isdigit() for ch in tok)
        and (len(tok) >= 2 or tok == "y")
    ]
    if not identity_toks:
        return []
    cand_blobs: list[tuple[str, set[str], str]] = []
    for item in candidates[:24]:
        leaf_seg = _leaf_category_segment(item)
        hay = normalize_category_text(f"{item.name} {item.brand or ''} {leaf_seg}")
        cand_blobs.append((hay, set(hay.split()), hay.replace(" ", "")))
    missing: list[str] = []
    for tok in identity_toks:
        matched_any = any(
            (tok in words) if len(tok) <= 3 else (tok in hay or tok in compact)
            for hay, words, compact in cand_blobs
        )
        if not matched_any:
            missing.append(tok)
    return missing


async def _semantic_bridge_zero_hit_query(
    *,
    query: str,
    native_q: str,
    repository: ProductRepository,
    groq: GroqService,
    sem_route: SemanticRoute | None,
) -> tuple[list[ProductSnapshot], str | None, bool]:
    """Layer 3 (~250ms, cached) retrieval-grounded semantic catalog bridge for zero-hit queries.

    Grounds the LLM strictly on the top local Thanh Lam Mart leaf candidates + dense SKU matches so
    colloquial Vietnamese synonyms ('kẹo cao su' -> 'Kẹo gum', 'lăn nách' -> 'Lăn khử mùi',
    'hột gà' -> 'Trứng gà') resolve accurately while genuinely out-of-catalog items
    ('băng cá nhân', 'đồ khui bia', 'sạc điện thoại') return confirmed_absent=True.
    """
    cache_key = normalize_category_text(native_q or query)
    cached = _SEMANTIC_BRIDGE_CACHE.get(cache_key) if cache_key else None

    def _resolve_decision(
        pick_kind: str,
        pick_val: str | None,
        kws: tuple[str, ...],
    ) -> tuple[list[ProductSnapshot], str | None]:
        if pick_kind == "cat" and pick_val:
            for kw in kws:
                scoped = repository.search(query=kw, category=pick_val, limit=24)
                grounded = [p for p in scoped if _hit_is_grounded(kw, p)]
                if grounded:
                    return grounded, pick_val
            browse = repository.search(
                query="",
                category=pick_val,
                diversify_by_subcategory=True,
                limit=32,
            )
            if browse:
                return browse, pick_val
        elif pick_kind == "sku" and pick_val:
            sku = repository.get_product_snapshot(pick_val)
            if sku is not None:
                leaf = _leaf_category_segment(sku)
                siblings = repository.search(query="", category=leaf, limit=16) if leaf else []
                merged = [sku, *(p for p in siblings if p.product_id != sku.product_id)]
                return merged, leaf or sku.category
        else:
            for kw in kws:
                hits = repository.search(query=kw, limit=24)
                grounded = [p for p in hits if _hit_is_grounded(kw, p, require_head_start=True)]
                if grounded:
                    return grounded, _leaf_category_segment(grounded[0])
        return [], None

    if cached is not None:
        pick_kind, pick_val, kws = cached
        resolved, resolved_cat = await run_catalog_work(
            _resolve_decision, pick_kind, pick_val, kws, timeout_seconds=2
        )
        return resolved, resolved_cat, (not resolved)

    def _build_shortlist() -> tuple[list[str], dict[str, str], dict[str, ProductSnapshot]]:
        rt = sem_route if sem_route is not None else route_query(query, top_k=3, min_confidence=0.0)
        cand_lines: list[str] = []
        cat_map: dict[str, str] = {}
        sku_map: dict[str, ProductSnapshot] = {}
        if rt is not None:
            for idx, (cat, score) in enumerate(zip(rt.categories, rt.scores), 1):
                if score < 0.08 and idx > 1:
                    continue
                samples = repository.search(query="", category=cat, limit=3)
                if not samples:
                    continue
                cid = f"C{idx}"
                cat_map[cid] = cat
                samp_s = "; ".join(p.name[:42] for p in samples[:2])
                cand_lines.append(f"{cid}: [{cat}] ({samp_s})")
        dense_hits = dense_search_skus(query, top_k=2, min_sim=0.38)
        for j, (pid, _sim) in enumerate(dense_hits, 1):
            prod = repository.get_product_snapshot(pid)
            if prod is not None:
                sid = f"S{j}"
                sku_map[sid] = prod
                cand_lines.append(f"{sid}: {prod.name[:55]} [{_leaf_category_segment(prod)}]")
        return cand_lines, cat_map, sku_map

    cand_lines, cat_map, sku_map = await run_catalog_work(_build_shortlist, timeout_seconds=2)
    user_msg = f"Khách hỏi: {query[:200]}\nỨng viên:\n" + ("\n".join(cand_lines) or "(không có)")
    try:
        res = await asyncio.wait_for(
            groq.fast_chat(
                [
                    {"role": "system", "content": _SEMANTIC_BRIDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=75,
                temperature=0.0,
            ),
            timeout=4.5,
        )
        raw_val = getattr(res, "text", None) or getattr(res, "content", None) or res
        raw = str(raw_val).strip()
        s_i, e_i = raw.find("{"), raw.rfind("}")
        if s_i < 0 or e_i <= s_i:
            return [], None, False
        data = json.loads(raw[s_i : e_i + 1])
        if not isinstance(data, dict):
            return [], None, False
    except Exception:
        return [], None, False

    pick = data.get("pick")
    kws = tuple(
        str(k).strip()[:60]
        for k in (data.get("kw") or [])
        if isinstance(k, str) and str(k).strip()
    )[:3]

    if isinstance(pick, str) and pick in cat_map:
        pick_kind, pick_val = "cat", cat_map[pick]
    elif isinstance(pick, str) and pick in sku_map:
        pick_kind, pick_val = "sku", sku_map[pick].product_id
    else:
        pick_kind, pick_val = "none", None

    if cache_key:
        if len(_SEMANTIC_BRIDGE_CACHE) >= _SEMANTIC_BRIDGE_CACHE_MAX:
            _SEMANTIC_BRIDGE_CACHE.pop(next(iter(_SEMANTIC_BRIDGE_CACHE)))
        _SEMANTIC_BRIDGE_CACHE[cache_key] = (pick_kind, pick_val, kws)

    resolved, resolved_cat = await run_catalog_work(
        _resolve_decision, pick_kind, pick_val, kws, timeout_seconds=2
    )
    return resolved, resolved_cat, (not resolved)


def _build_out_of_catalog_streaming_plan(
    *,
    text: str,
    query_label: str,
    previous_memory: dict,
) -> StreamingConsultationPlan:
    """Instant truthful abstention when confirmed absent from the Thanh Lam Mart catalog."""
    clean_label = query_label.strip() or text.strip()[:60]
    reply = (
        f'Hiện tại trong danh mục tham khảo của cửa hàng mình chưa có sản phẩm "{clean_label}". '
        "Bạn có thể kiểm tra trực tiếp với nhân viên tại quầy hoặc hỏi mình nhóm hàng khác nhé!"
    )
    memory = {
        "need": text[:500],
        "last_action": "abstain",
        "last_question": "",
        "pending_question": None,
        "answered_slots": previous_memory.get("answered_slots", []),
        "constraints": {},
        "selection": {"picks": []},
    }
    return StreamingConsultationPlan(
        products=[],
        related_products=[],
        memory=memory,
        messages=[],
        fallback_text=reply,
        direct_reply=reply,
    )


async def prepare_streaming_consultation(
    *,
    text: str,
    record: SessionRecord,
    repository: ProductRepository,
    groq: GroqService,
) -> StreamingConsultationPlan | None:
    """Prepare a strictly grounded, low-risk consultation for live token streaming.

    Eliminates unnecessary multi-call planner round-trips for single-category,
    brand, sugar/attribute, lexical product, comparison, promo, follow-up,
    verified product detail, and budget basket queries while preserving strict
    safety routing for clinical/allergy/infant/chemical-safety requests.
    """
    if not isinstance(groq, GroqService) or not callable(getattr(groq, "stream_chat", None)):
        return None
    previous_memory = dict(record.shopping.consultation)
    if (
        previous_memory.get("pending_question")
        or previous_memory.get("last_question")
        or previous_memory.get("unprocessed_request")
        or previous_memory.get("unprocessed_answer")
    ):
        return None

    normalize_noisy = getattr(repository, "normalize_noisy_query", None)
    answer_text = normalize_noisy(text) if callable(normalize_noisy) else text
    answer_text = re.sub(r"\btương\s+ót\b", "tương ớt", answer_text, flags=re.IGNORECASE)
    answer_text = re.sub(r"\bsửa\s+tươi\b", "sữa tươi", answer_text, flags=re.IGNORECASE)
    answer_text = re.sub(r"\bsửa\s+chua\b", "sữa chua", answer_text, flags=re.IGNORECASE)
    answer_text = re.sub(r"\bsửa\s+rửa\s+mặt\b", "sữa rửa mặt", answer_text, flags=re.IGNORECASE)
    answer_text = re.sub(r"\b(?:ko|k)\s+đường\b", "không đường", answer_text, flags=re.IGNORECASE)
    norm_answer = normalize_category_text(answer_text)

    if resolve_organization_profile(text) is not None and re.search(
        r"\b(?:gioi\s+thieu|thong\s+tin|la\s+gi|la\s+cong\s+ty|do\s+an|du\s+an|"
        r"cong\s+ty|cua\s+ai|so\s+huu|van\s+hanh|thanh\s+lam\s+mart|"
        r"cua\s+hang\s+thanh\s+lam|sieu\s+thi\s+thanh\s+lam|chatvrm)\b",
        norm_answer,
    ):
        return None

    is_detail_query = bool(_STREAM_DETAIL_QUERY_RE.search(norm_answer))
    is_advice_followup = bool(_advice_followup(text) and previous_memory.get("need"))
    if is_advice_followup and not is_detail_query:
        return None

    visible = [
        p
        for pid in record.shopping.selected_product_ids[:12]
        if (p := repository.get_product_snapshot(pid)) is not None
    ]
    paths = await run_catalog_work(repository.list_category_hierarchies, timeout_seconds=4)
    if len(paths) <= 80:
        return None
    understood_text = _shopping_utterance(answer_text)
    parsed = understand_catalog_query(understood_text, paths)
    # Path M: Stateful multi-turn working-basket & context-locked follow-ups (runs BEFORE
    # Turn-1 intent filters so "Tổng hết bao nhiêu tiền?", "Cho tôi 3 lon", "Còn hàng không?",
    # "Có chai nhỏ hơn không?" stay locked to the active conversation in <1.3s).
    if not is_detail_query and (
        previous_memory.get("need")
        or previous_memory.get("working_basket")
        or previous_memory.get("recent_product_ids")
        or visible
        or record.shopping.active_category
    ):
        mt_plan = await prepare_multiturn_streaming(
            text=text,
            answer_text=answer_text,
            norm_answer=norm_answer,
            visible=visible,
            repository=repository,
            previous_memory=previous_memory,
            parsed=parsed,
            build_payload=_build_streaming_plan_payload,
            active_category_hint=record.shopping.active_category,
        )
        if mt_plan is not None:
            return mt_plan

    # Special Turn-1 Fast-Lane (runs BEFORE label/purchase filters so "5 chai nước ngọt và
    # 3 gói bánh", "protein cao nhất", "kem ăn", "chai lớn hay chai nhỏ" return <1.2s cleanly).
    special_plan = await prepare_special_turn1_streaming(
        text=text,
        answer_text=answer_text,
        norm_answer=norm_answer,
        repository=repository,
        previous_memory=previous_memory,
        parsed=parsed,
        build_payload=_build_streaming_plan_payload,
    )
    if special_plan is not None:
        return special_plan

    if _STREAM_LABEL_OR_EFFICACY_RE.search(norm_answer):
        return None

    if parsed.intent in {"purchase_action", "location"}:
        return None
    if parsed.ranking_criterion == "sales_popularity":
        return None
    if parse_stock_ranking_request(text) is not None:
        return None
    if parse_sales_or_stock_comparison(text) is not None:
        return None
    budget_followup = bool(previous_memory.get("need") and parsed.budget_vnd is not None)
    if parsed.intent == "price" and not budget_followup:
        return None
    if _ambiguous_identity_question(answer_text, parsed):
        return None

    guidance_items = shopping_guidance(understood_text, limit=32)
    is_non_clinical_diet = bool(
        re.search(r"\ban\s+kieng\b", norm_answer)
        and not re.search(
            r"\b(?:tieu duong|huyet ap|tim mach|gout|benh|than|gan|di ung|mang thai|tre so sinh)\b",
            norm_answer,
        )
    )
    if not is_non_clinical_diet and any(
        item.get("domain") in _STREAM_HIGH_RISK_DOMAINS
        and item.get("id") not in _STREAM_SAFE_GUIDANCE_IDS
        for item in guidance_items
    ):
        return None

    # Path F: Direct or follow-up verified product details (usage/ingredients/storage/origin).
    if is_detail_query:
        detail_plan = await _prepare_product_detail_streaming(
            text=text,
            answer_text=answer_text,
            norm_answer=norm_answer,
            visible=visible,
            repository=repository,
            previous_memory=previous_memory,
        )
        if detail_plan is not None:
            return detail_plan
        if is_advice_followup:
            return None

    # Path A: Direct 2-target / 2-brand comparison (0 planner calls).
    comp_targets = tuple(getattr(parsed, "comparison_targets", ()) or ())
    if len(comp_targets) >= 2:
        comp_plan = await _prepare_comparison_streaming(
            text=text,
            targets=comp_targets,
            repository=repository,
            previous_memory=previous_memory,
            parsed=parsed,
        )
        if comp_plan is not None:
            return comp_plan

    # Path B: Store-wide promotion browsing (0 planner calls).
    if (
        not parsed.product_type
        and not getattr(parsed, "brand", None)
        and not getattr(parsed, "brand_preference", None)
        and not _STREAM_BASKET_OR_MEAL_RE.search(norm_answer)
        and _STREAM_PROMO_BROWSE_RE.search(norm_answer)
        and len(_clean_lexical_query(answer_text).split()) <= 2
    ):
        promo_plan = await _prepare_promo_browse_streaming(
            text=text,
            repository=repository,
            previous_memory=previous_memory,
            budget_vnd=parsed.budget_vnd,
        )
        if promo_plan is not None:
            return promo_plan

    # Path E (prioritized ahead of single-item C/D): Multi-item meal/combo/basket
    # requests (excluding single-item seasoning queries like "gia vị ướp BBQ").
    if (
        (_STREAM_BASKET_OR_MEAL_RE.search(norm_answer) or _EXTENDED_BASKET_RE.search(norm_answer))
        and not _SINGLE_ITEM_SEASONING_RE.search(norm_answer)
    ):
        basket_plan = await _prepare_slim_basket_streaming(
            text=text,
            repository=repository,
            groq=groq,
            previous_memory=previous_memory,
            budget_vnd=parsed.budget_vnd,
            parsed=parsed,
        )
        if basket_plan is not None:
            return basket_plan

    # Path C: Single-category / Brand / Sugar / Follow-up (0 planner calls).
    # Skip on pure situational eating queries ("100k đồ ăn cho 2 người", "không có lò vi sóng")
    # so they route cleanly to Path E/G with ready-to-eat filtering instead of "Bóng cá chay".
    plan = (
        None
        if is_pure_situational_query(norm_answer)
        else _fast_path_single_category_plan(
            answer_text,
            parsed,
            paths,
            previous_memory,
            budget_followup=budget_followup,
            explicit_sizes=_product_sizes(answer_text),
            repository=repository,
            for_streaming=True,
        )
    )
    cheaper_followup = bool(
        re.search(
            r"\b(?:re hon|gia re|gia mem|it tien hon|re nhat|mem hon|thap hon)\b",
            norm_answer,
        )
    )
    other_followup = bool(
        re.search(
            r"\b(?:loai khac|mau khac|mon khac|cai khac|xem them|con loai nao|con mau nao)\b",
            norm_answer,
        )
    )
    if plan is None and not parsed.product_type and (cheaper_followup or other_followup):
        prev_constraints = previous_memory.get("constraints", {})
        prev_searches = (
            prev_constraints.get("searches") if isinstance(prev_constraints, dict) else []
        )
        if (
            not (isinstance(prev_constraints, dict) and prev_constraints.get("basket"))
            and isinstance(prev_searches, list)
            and 1 <= len(prev_searches) <= 4
            and all(isinstance(s, dict) and s.get("category") for s in prev_searches)
            and not _FAST_PATH_MULTI_OR_COMPARE.search(norm_answer)
        ):
            reused = [
                SearchNeed(
                    category=str(s["category"]),
                    role=str(s.get("role") or s["category"]),
                    excluded=list(s.get("excluded") or []),
                    required_sale_unit=s.get("required_sale_unit"),
                )
                for s in prev_searches
            ]
            prior_need = str(previous_memory.get("need") or "").strip()
            need_text = f"{prior_need}; {answer_text}"[:500] if prior_need else answer_text[:500]
            plan = _preserve_constraints(
                ConsultationDecision(
                    action="recommend",
                    need=need_text,
                    searches=reused,
                    max_price_vnd=parsed.budget_vnd if parsed.budget_scope != "total" else None,
                    total_budget_vnd=parsed.budget_vnd if parsed.budget_scope == "total" else None,
                    strict_price_limit=(parsed.budget_operator == "LT"),
                    sugar="any",
                    needs_label_verification=False,
                ),
                previous_memory,
                answer_text,
                parsed_budget=parsed.budget_vnd,
                explicit_sizes=_product_sizes(answer_text),
                paths=paths,
            )

    if plan is not None and (
        plan.action == "recommend"
        and not plan.basket
        and not plan.child_snack
        and not plan.unresolved_needs
    ):
        sources: dict[str, list[int]] = {}
        candidates = await run_catalog_work(
            _retrieve, repository, paths, plan, visible, sources, timeout_seconds=4,
        )
        candidates = _apply_streaming_constraints(candidates, answer_text, parsed)
        if candidates:
            focused, focus_scores = _filter_streaming_candidates(
                candidates, answer_text, parsed.product_type
            )
            lex_extra: list[ProductSnapshot] = []
            if not focus_scores and _focus_tokens_for_streaming(answer_text, parsed.product_type):
                cleaned_focus_q = _clean_native_lexical_query(answer_text)
                if cleaned_focus_q:
                    scoped_cat = (
                        plan.searches[0].category
                        if len(plan.searches) == 1 and plan.searches[0].category
                        else None
                    )
                    lex_extra = await run_catalog_work(
                        repository.search,
                        query=cleaned_focus_q,
                        category=scoped_cat,
                        limit=12,
                        timeout_seconds=3,
                    )
                    lex_extra = _apply_streaming_constraints(lex_extra, answer_text, parsed)
                    if lex_extra:
                        lex_ids = {item.product_id for item in lex_extra}
                        combined = [
                            *lex_extra,
                            *(item for item in candidates if item.product_id not in lex_ids),
                        ]
                        focused, focus_scores = _filter_streaming_candidates(
                            combined, answer_text, parsed.product_type
                        )
                        if not focus_scores:
                            focused = lex_extra
                            focus_scores = {item.product_id: 1 for item in lex_extra}
            # If the shopper specified concrete product-identity words (e.g. 'đồ khui bia',
            # 'bánh tráng trộn', 'pin tiểu') that matched ZERO candidates in Path C's parent
            # category, verify with synonym-aware scoped search before handing off to Path D.
            unmatched_toks = (
                []
                if (lex_extra or other_followup or cheaper_followup or budget_followup)
                else _unmatched_identity_tokens(answer_text, parsed.product_type, focused)
            )
            if unmatched_toks:
                cleaned_syn_q = _clean_native_lexical_query(answer_text)
                scoped_syn_cat = (
                    plan.searches[0].category
                    if len(plan.searches) == 1 and plan.searches[0].category
                    else None
                )
                if cleaned_syn_q and scoped_syn_cat:
                    syn_hits = await run_catalog_work(
                        repository.search,
                        query=cleaned_syn_q,
                        category=scoped_syn_cat,
                        limit=6,
                        timeout_seconds=2,
                    )
                    if syn_hits:
                        unmatched_toks = []
            unmatched_identity = bool(unmatched_toks)
            if not unmatched_identity:
                if other_followup and visible:
                    visible_ids = {item.product_id for item in visible}
                    unseen = [item for item in focused if item.product_id not in visible_ids]
                    focused = unseen or focused
                ordered = _rank_streaming_candidates(
                    focused,
                    answer_text,
                    focus_scores=focus_scores,
                    cheaper_priority=cheaper_followup,
                )
                pick_count = 2 if plan.max_options == 2 and len(ordered) >= 2 else 1
                primary_products = ordered[:pick_count]
                related_products = _related_candidates(ordered, primary_products, limit=4)
                picks_payload = [{"candidate": i} for i in range(len(primary_products))]
                memory = {
                    "need": plan.need or previous_memory.get("need", text[:500]),
                    "last_action": "recommend",
                    "last_question": "",
                    "pending_question": None,
                    "answered_slots": previous_memory.get("answered_slots", []),
                    "constraints": plan.model_dump(
                        exclude={
                            "question", "need", "guidance", "action",
                            "pending_question", "clarification_answer",
                        }
                    ),
                    "selection": {"picks": picks_payload},
                }
                return _build_streaming_plan_payload(
                    text=text,
                    primary_products=primary_products,
                    related_products=related_products,
                    repository=repository,
                    sources=sources,
                    memory=memory,
                    plan_for_fallback=plan,
                    mode="recommend",
                )

    # Path D: Ultra-fast Semantic NLP Router (<1ms) + Diacritic-Preserving Lexical Search.
    # Skip pure situational needs ("đang đói", "ăn kiêng", "mang đi học") -> route to Path G.
    excluded_terms = _clean_negative_terms(parsed)
    cleaned_native_lex = _clean_native_lexical_query(answer_text)
    cleaned_lex = _clean_lexical_query(answer_text)
    if excluded_terms and cleaned_lex:
        excluded_toks = {
            tok for term in excluded_terms for tok in normalize_category_text(term).split()
        }
        cleaned_lex = " ".join(
            tok for tok in cleaned_lex.split() if tok not in excluded_toks
        )
        cleaned_native_lex = " ".join(
            tok
            for tok in cleaned_native_lex.split()
            if normalize_category_text(tok) not in excluded_toks
        )
    lex_search_q = _strip_attribute_modifiers_for_lex(cleaned_lex) or cleaned_lex
    native_search_q = cleaned_native_lex or lex_search_q
    if _explicit_sugar_level(answer_text) or re.search(
        r"\b(?:khong\s+cay|khong\s+gas|khong\s+co\s+gas|it\s+calo)\b", norm_answer
    ):
        native_attr_stripped = re.sub(
            r"\b(?:không\s+đường|ít\s+đường|giảm\s+đường|ít\s+ngọt|không\s+calo|ít\s+calo|"
            r"không\s+có\s+gas|không\s+gas|không\s+cay|cay\s+nhất)\b",
            " ",
            native_search_q,
            flags=re.IGNORECASE,
        )
        native_attr_stripped = " ".join(native_attr_stripped.split())
        if native_attr_stripped:
            native_search_q = native_attr_stripped
    if (
        (lex_search_q or native_search_q)
        and not is_pure_situational_query(norm_answer)
        and (
            not _STREAM_BASKET_OR_MEAL_RE.search(norm_answer)
            or _SINGLE_ITEM_SEASONING_RE.search(norm_answer)
        )
        and not _FAST_PATH_MULTI_OR_COMPARE.search(norm_answer)
    ):
        sem_route = route_query(answer_text, min_confidence=0.40)
        explicit_brand = getattr(parsed, "brand", None) or getattr(parsed, "brand_preference", None)
        _RAW_PRODUCE_CATS = frozenset(
            {
                "Trái cây",
                "Rau củ",
                "Rau gia vị",
                "Nấm",
                "Thủy hải sản",
                "Thịt Bò, Bê",
                "Lợn (heo)",
                "Thịt gà",
                "Bơ",
            }
        )

        def _postprocess_hits(
            raw_items: list[ProductSnapshot],
            sem_cat_name: str | None,
        ) -> tuple[list[ProductSnapshot], list[ProductSnapshot]]:
            processed = list(raw_items)
            if (
                processed
                and is_food_or_drink_need(answer_text)
                and sem_cat_name not in _RAW_PRODUCE_CATS
            ):
                edible = filter_edible_candidates(
                    processed,
                    ready_to_eat=bool(_READY_TO_EAT_NEED_RE.search(norm_answer)),
                )
                if edible:
                    processed = edible
            return _apply_streaming_constraints(processed, answer_text, parsed), processed

        def _lex_search() -> tuple[list[ProductSnapshot], list[ProductSnapshot], str | None]:
            matched_sem_cat: str | None = None
            if lex_search_q in {"do uong", "nuoc uong"} and _explicit_sugar_level(answer_text):
                raw_hits = [
                    *repository.search(query="sua chua uong it duong", limit=10),
                    *repository.search(query="nuoc giai khat", limit=16),
                    *repository.search(query="nuoc tra", limit=12),
                ]
            else:
                raw_hits = []
                if sem_route is not None and not explicit_brand:
                    sem_cat = sem_route.categories[0]
                    # Stage 1a: Direct category browse when shopper explicitly names the category
                    # (e.g. 'trái cây', 'rau củ', 'sữa chua', 'snack', 'kẹo gum', 'thạch').
                    if _is_direct_category_browse(native_search_q, sem_cat):
                        cat_browse = repository.search(
                            query="",
                            category=sem_cat,
                            diversify_by_subcategory=True,
                            limit=32,
                        )
                        if cat_browse:
                            raw_hits = cat_browse
                            matched_sem_cat = sem_cat
                    if not raw_hits:
                        scoped_hits = repository.search(
                            query=native_search_q, category=sem_cat, limit=32
                        )
                        grounded_scoped = [
                            p for p in scoped_hits if _hit_is_grounded(native_search_q, p)
                        ]
                        if grounded_scoped:
                            raw_hits = grounded_scoped
                            matched_sem_cat = sem_cat
                    if (
                        not raw_hits
                        and len(sem_route.categories) >= 2
                        and sem_route.scores[1] >= 0.18
                    ):
                        sec_cat = sem_route.categories[1]
                        sec_hits = repository.search(
                            query=native_search_q, category=sec_cat, limit=32
                        )
                        grounded_sec = [
                            p for p in sec_hits if _hit_is_grounded(native_search_q, p)
                        ]
                        if grounded_sec:
                            raw_hits = grounded_sec
                            matched_sem_cat = sec_cat
                # Only fall back to unscoped lexical search when semantic router isn't already
                # strongly locked onto a category (prevents 'hoa quả' escaping Trái cây into
                # 'Thạch kem hoa quả'), and enforce strict head-noun grounding (or sibling-leaf
                # agreement with sem_route, e.g. 'Thạch rau câu' in 'Thạch cân ký').
                if not raw_hits and (
                    explicit_brand
                    or sem_route is None
                    or sem_route.confidence < 0.65
                ):
                    unscoped = repository.search(query=native_search_q, limit=32)
                    sem_head = (
                        normalize_category_text(sem_route.categories[0]).split()[:1]
                        if sem_route is not None
                        else []
                    )
                    raw_hits = [
                        p
                        for p in unscoped
                        if _hit_is_grounded(
                            native_search_q,
                            p,
                            require_head_start=not (
                                bool(explicit_brand)
                                or (
                                    bool(sem_head)
                                    and normalize_category_text(
                                        _leaf_category_segment(p)
                                    ).split()[:1]
                                    == sem_head
                                )
                            ),
                        )
                    ]
            filtered_hits, final_raw = _postprocess_hits(raw_hits, matched_sem_cat)
            return filtered_hits, final_raw, matched_sem_cat

        lex_hits, raw_lex_hits, matched_sem_cat = await run_catalog_work(
            _lex_search, timeout_seconds=3
        )

        # Stage 2 (Zero-hit Semantic Catalog Bridge): runs ONLY when Stage 1 found 0 grounded
        # lexical hits (colloquial synonyms like 'kẹo cao su', 'lăn nách', 'son dưỡng môi',
        # 'hột gà', 'pin tiểu' OR out-of-catalog queries like 'băng cá nhân', 'đồ khui bia').
        if not raw_lex_hits and not _SITUATIONAL_OR_ADVICE_RE.search(norm_answer):
            raw_native_q = _clean_native_lexical_query(text) or native_search_q
            norm_raw_text = normalize_category_text(text)

            def _single_leaf_fast_match(
                *, allow_broad_homogeneous: bool
            ) -> tuple[list[ProductSnapshot], str | None]:
                if _OFFLINE_NON_CATALOG_HEAD_RE.search(
                    norm_answer
                ) or _OFFLINE_NON_CATALOG_HEAD_RE.search(norm_raw_text):
                    return [], None
                if (
                    sem_route is not None
                    and sem_route.confidence >= 0.55
                    and not explicit_brand
                ):
                    sem_cat = sem_route.categories[0]
                    browse = repository.search(
                        query="",
                        category=sem_cat,
                        diversify_by_subcategory=True,
                        limit=32,
                    )
                    if browse:
                        leaves = {_leaf_category_segment(p) for p in browse}
                        if len(leaves) == 1 and (
                            allow_broad_homogeneous
                            or (sem_route.confidence >= 0.58 and sem_route.margin >= 0.40)
                        ):
                            return browse, sem_cat
                        if (
                            allow_broad_homogeneous
                            and sem_cat in _HOMOGENEOUS_MULTI_LEAF_CATS
                            and sem_route.confidence >= 0.65
                        ):
                            return browse, sem_cat
                if allow_broad_homogeneous:
                    dense_hits = dense_search_skus(answer_text, top_k=3, min_sim=0.50)
                    if dense_hits:
                        rt_any = sem_route or route_query(answer_text, top_k=2, min_confidence=0.0)
                        top_prod = repository.get_product_snapshot(dense_hits[0][0])
                        if top_prod is not None and rt_any is not None:
                            top_leaf = _leaf_category_segment(top_prod)
                            hier_norm = normalize_category_text(
                                top_prod.category_hierarchy or top_leaf
                            )
                            rt_cat = rt_any.categories[0]
                            rt_cat_norm = normalize_category_text(rt_cat)
                            if rt_cat_norm and rt_cat_norm in hier_norm:
                                leaf_prods = repository.search(
                                    query="", category=rt_cat, limit=24
                                )
                                if leaf_prods:
                                    return leaf_prods, rt_cat
                return [], None

            # Stage 2a (<1ms): High-confidence, wide-margin SINGLE-LEAF match (e.g.
            # 'kẹo cao su' -> 'Kẹo gum' 0.60, 'lăn nách' -> 'Lăn khử mùi nữ' 0.71,
            # 'son dưỡng môi' -> 'Mỹ phẩm t.điểm môi' 0.89) avoids an extra LLM round-trip.
            if (
                sem_route is not None
                and sem_route.confidence >= 0.58
                and sem_route.margin >= 0.40
                and len(raw_native_q.split()) <= 3
            ):
                fast_raw, fast_cat = await run_catalog_work(
                    _single_leaf_fast_match, allow_broad_homogeneous=False, timeout_seconds=2
                )
                if fast_raw:
                    matched_sem_cat = fast_cat
                    lex_hits, raw_lex_hits = _postprocess_hits(fast_raw, matched_sem_cat)

            # Stage 2b (~250ms, cached): Retrieval-Grounded Groq LPU Semantic Bridge for
            # unseen slang/regional synonyms ('hột gà') & truthful out-of-catalog rejection
            # ('băng cá nhân', 'đồ khui bia', 'bánh tráng trộn', 'sạc điện thoại').
            if not raw_lex_hits:
                can_fast_lane_fn = getattr(groq, "_can_use_groq_fast_lane", None)
                bridge_confirmed_absent = False
                if callable(can_fast_lane_fn) and can_fast_lane_fn():
                    bridge_raw, bridge_cat, bridge_confirmed_absent = (
                        await _semantic_bridge_zero_hit_query(
                            query=text,
                            native_q=raw_native_q,
                            repository=repository,
                            groq=groq,
                            sem_route=sem_route,
                        )
                    )
                    if bridge_raw:
                        matched_sem_cat = bridge_cat
                        lex_hits, raw_lex_hits = _postprocess_hits(bridge_raw, matched_sem_cat)
                    elif (
                        bridge_confirmed_absent
                        and not is_advice_followup
                        and 1 <= len(raw_native_q.split()) <= 4
                    ):
                        return _build_out_of_catalog_streaming_plan(
                            text=text,
                            query_label=raw_native_q,
                            previous_memory=previous_memory,
                        )
                # Offline or transient timeout fallback (never runs if bridge confirmed absent).
                if not raw_lex_hits and not bridge_confirmed_absent:
                    fb_raw, fb_cat = await run_catalog_work(
                        _single_leaf_fast_match, allow_broad_homogeneous=True, timeout_seconds=2
                    )
                    if fb_raw:
                        matched_sem_cat = fb_cat
                        lex_hits, raw_lex_hits = _postprocess_hits(fb_raw, matched_sem_cat)
        zero_hit_attr_note = ""
        if not lex_hits and raw_lex_hits and _explicit_sugar_level(answer_text) is not None:
            # Stay strictly inside the matched category ("tương ớt ít đường" / "cà phê ít đường")
            # rather than falling back to the slow planner and jumping to biscuits or bird's nest!
            lex_hits = raw_lex_hits
            zero_hit_attr_note = (
                "BẮT BUỘC nói rõ: trong catalog hiện chưa có mẫu nào ghi nhãn ít/không đường "
                "hoặc bảng định lượng đường cụ thể cho nhóm này, nên mình giới thiệu các mẫu "
                "phổ biến đúng nhóm để khách kiểm tra bảng dinh dưỡng trên nhãn bao bì."
            )
        if lex_hits:
            focused, focus_scores = _filter_streaming_candidates(
                lex_hits, lex_search_q, matched_sem_cat
            )
            # When the semantic NLP router already locked onto a clean Thanh Lam Mart category
            # (e.g. "Trái cây" for "có cam không"), prioritize SKUs whose leading head noun
            # matches the shopper's query so "Cam Sành / Cam Úc" beats "Dưa lưới ruột cam".
            if matched_sem_cat and lex_search_q:
                q_toks = set(lex_search_q.split())
                head_matched = [
                    p
                    for p in focused
                    if (
                        p_toks := normalize_category_text(p.name).split()
                    )
                    and any(tok in q_toks for tok in p_toks[:2])
                ]
                if head_matched:
                    focused = head_matched
            ordered = _rank_streaming_candidates(
                focused,
                answer_text,
                focus_scores=focus_scores,
                cheaper_priority=cheaper_followup,
            )
            primary_products = ordered[:1]
            related_products = _related_candidates(ordered, primary_products, limit=4)
            top_cat = primary_products[0].category or lex_search_q
            lex_plan = ConsultationDecision(
                action="recommend",
                need=text[:500],
                searches=[
                    SearchNeed(category=top_cat, role=lex_search_q, excluded=excluded_terms)
                ],
                max_price_vnd=parsed.budget_vnd,
                sugar=_explicit_sugar_level(answer_text) or "any",
            )
            memory = {
                "need": text[:500],
                "last_action": "recommend",
                "last_question": "",
                "pending_question": None,
                "answered_slots": previous_memory.get("answered_slots", []),
                "constraints": lex_plan.model_dump(
                    exclude={
                        "question", "need", "guidance", "action",
                        "pending_question", "clarification_answer",
                    }
                ),
                "selection": {"picks": [{"candidate": 0}]},
            }
            return _build_streaming_plan_payload(
                text=text,
                primary_products=primary_products,
                related_products=related_products,
                repository=repository,
                sources={primary_products[0].product_id: [0]},
                memory=memory,
                plan_for_fallback=lex_plan,
                mode="recommend",
                budget_vnd=parsed.budget_vnd,
                extra_note=zero_hit_attr_note,
            )

    # Path G: Open/situational 1-call keyword expansion on Groq LPU fast-lane (~1.1s total).
    can_fast_lane = getattr(groq, "_can_use_groq_fast_lane", None)
    if (
        callable(can_fast_lane)
        and can_fast_lane()
        and len(norm_answer.split()) >= 3
        and not is_advice_followup
    ):
        open_plan = await _prepare_open_need_streaming(
            text=text,
            answer_text=answer_text,
            repository=repository,
            groq=groq,
            previous_memory=previous_memory,
            parsed=parsed,
        )
        if open_plan is not None:
            return open_plan

    return None
