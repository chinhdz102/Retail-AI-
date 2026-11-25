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
    _batch_base_unit_dims,
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
from backend.services.product_constraints import title_has_descriptor
from backend.services.query_understanding import (
    strip_retrieval_budget,
    understand_catalog_query,
)
from backend.services.sessions import SessionRecord
from backend.services.shopping_guidance import shopping_guidance

_SINGLE_ITEM_SEASONING_RE = re.compile(
    r"\b(?:gia vi|sot uop|bot uop|goi gia vi|nuoc sot|nuoc cham|tuong ot|muoi cham)\b"
)
_SAVORY_MEAL_CUE_RE = re.compile(
    r"\b(?:bua toi|bua trua|bua com|nau com|lau|mi y|spaghetti|bbq|nuong)\b"
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
    r"thanh\s+phan|lam\s+tu\s+gi|bao\s+quan|xuat\s+xu|san\s+xuat\s+o\s+dau)\b"
)
_STREAM_DETAIL_STRIP_RE = re.compile(
    r"\b(?:cach\s+(?:su\s+)?dung|huong\s+dan\s+su\s+dung|"
    r"(?:su\s+)?dung\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
    r"(?:goi|pha|nau|che\s+bien)\s+(?:nhu\s+the\s+nao|the\s+nao|ra\s+sao|sao)|"
    r"thanh\s+phan|lam\s+tu\s+gi|bao\s+quan(?:\s+nhu\s+the\s+nao|\s+the\s+nao|\s+sao)?|"
    r"xuat\s+xu(?:\s+o\s+dau|\s+tu\s+dau)?|san\s+xuat\s+o\s+dau|"
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
    ("ingredients", re.compile(r"\b(?:thanh\s+phan|lam\s+tu\s+gi|co\s+chat\s+gi)\b")),
    ("storage", re.compile(r"\b(?:bao\s+quan|cat\s+giu|de\s+o\s+dau)\b")),
    ("origin", re.compile(r"\b(?:xuat\s+xu|san\s+xuat\s+o\s+dau|cua\s+nuoc\s+nao)\b")),
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


def _clean_lexical_query(text: str) -> str:
    """Strip Vietnamese conversational fillers & budgets to isolate product keywords."""
    cleaned_native = _NATIVE_STREAM_OPENER_RE.sub(" ", text)
    stripped = normalize_category_text(strip_retrieval_budget(cleaned_native))
    tokens = [
        tok
        for tok in stripped.split()
        if len(tok) >= 2 and tok not in _STREAM_FILLER_TOKENS
    ]
    return " ".join(tokens)


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
    scores: dict[str, int] = {}
    for item in candidates:
        hay = normalize_category_text(
            f"{item.name} {item.brand or ''} {item.category or ''}"
        )
        hay_compact = hay.replace(" ", "")
        hits = sum(
            1
            for tok in focus
            if tok in hay or (len(tok) >= 4 and tok in hay_compact)
        )
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
    """Order verified candidates by lexical fit, value/unit-price, or promo discount."""
    norm_text = normalize_category_text(text)
    promo_priority = bool(_PROMO_NEED_RE.search(norm_text))
    value_priority = bool(_STREAM_VALUE_RANK_RE.search(norm_text))
    wants_bulk = bool(re.search(r"\b(?:thung|loc|si|so luong lon|gia dinh lon)\b", norm_text))
    scores = focus_scores or {}

    def _sort_key(item: ProductSnapshot) -> tuple[int, int, float, float, int, str]:
        lex = -scores.get(item.product_id, 0)
        is_bulk_carton = (
            1
            if (
                not wants_bulk
                and not value_priority
                and re.search(r"\bthung\b", normalize_category_text(item.name))
            )
            else 0
        )
        discount = float(_product_discount_pct(item) or 0)
        unit_info = _unit_price_info(item)
        unit_price = float(unit_info[0]) if unit_info is not None else float("inf")
        if cheaper_priority:
            return (
                lex, is_bulk_carton, float(item.price_vnd), unit_price, item.price_vnd, item.name,
            )
        if value_priority:
            return (lex, 0, unit_price, -discount, item.price_vnd, item.name)
        if promo_priority:
            return (lex, 0, -discount, unit_price, item.price_vnd, item.name)
        has_strong_discount = 1.0 if discount >= 15.0 else 0.0
        return (lex, is_bulk_carton, -has_strong_discount, -discount, item.price_vnd, item.name)

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
) -> StreamingConsultationPlan:
    force_base_dims = _batch_base_unit_dims([*primary_products, *related_products])
    primary_facts = [
        _lean_streaming_product_card(p, force_base_dims=force_base_dims)
        for p in primary_products
    ]
    related_summary = [
        {
            k: v
            for k, v in (
                ("name", p.name),
                ("price_vnd", p.price_vnd),
                ("promotion", p.promotion),
            )
            if v
        }
        for p in related_products[:3]
    ]
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
            "Giải thích ngắn gọn vì sao sản phẩm nổi bật (primary_products) hợp nhu cầu "
            "của khách dựa trên giá, khuyến mãi, quy cách hoặc đơn giá quy đổi."
        ),
        "compare": (
            "So sánh trực tiếp 2 sản phẩm trong primary_products về giá tham khảo, "
            "quy cách/dung tích, đơn giá quy đổi và khuyến mãi để khách dễ chọn."
        ),
        "promo": (
            "Giới thiệu nhanh các ưu đãi nổi bật hiện có trong catalog (primary_products "
            "và other_catalog_products) theo mức giảm giá và giá tham khảo."
        ),
        "basket": (
            "Gợi ý gọn bộ sản phẩm trong primary_products theo đúng nhu cầu và ngân sách "
            "(nêu đúng tổng tiền basket_total_vnd nếu có)."
        ),
    }[mode]
    exclusion_rule = (
        " (5) Khách có yêu cầu tránh/loại trừ (excluded_requested): chỉ giới thiệu món phù hợp "
        "và nhắc nhẹ khách kiểm tra thêm nhãn/gói gia vị trên bao bì."
        if excluded_terms
        else ""
    )
    system_prompt = (
        "Bạn là Mai, trợ lý siêu thị thân thiện. Trả lời trực tiếp bằng tiếng Việt tự nhiên, "
        "ngắn gọn trong 2–3 câu liền mạch (không gạch đầu dòng, không markdown **). "
        "XƯNG HÔ BẮT BUỘC: luôn xưng 'mình' và gọi khách là 'bạn' nhất quán trong toàn bộ câu "
        "(TUYỆT ĐỐI KHÔNG xưng 'em' hay 'tôi', KHÔNG viết 'Chào bạn, em...'; hãy mở đầu tự "
        "nhiên như 'Dạ, mình gợi ý...' hoặc 'Với nhu cầu này, mình gợi ý...'). "
        "QUY TẮC: (1) CHỈ nêu tên sản phẩm, giá VND, khuyến mãi, quy cách, đơn giá có thật "
        "trong JSON. (2) " + mode_hint + " "
        "(3) Nếu có other_catalog_products, nhắc nhẹ 1 ý rằng bên dưới còn các mẫu khác để chọn. "
        "(4) Không bịa thành phần, công dụng chữa bệnh, cam kết dị ứng, số bán thật hay tồn kho."
        + exclusion_rule
    )
    payload_obj: dict[str, object] = {
        "customer_request": text,
        "primary_products": primary_facts,
    }
    if related_summary:
        payload_obj["other_catalog_products"] = related_summary
    if excluded_terms:
        payload_obj["excluded_requested"] = excluded_terms
    if mode == "basket" and primary_products:
        payload_obj["basket_total_vnd"] = sum(p.price_vnd for p in primary_products)
        if budget_vnd is not None:
            payload_obj["customer_budget_vnd"] = budget_vnd
    return StreamingConsultationPlan(
        products=primary_products,
        related_products=related_products,
        memory=memory,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")),
            },
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
    # Inherit shared category head word (e.g. "sữa TH True Milk" vs "Vinamilk" -> "sữa Vinamilk").
    head_words = norm_a.split()
    query_a = _clean_lexical_query(raw_a) or raw_a
    query_b = _clean_lexical_query(raw_b) or raw_b
    if head_words and len(norm_b.split()) <= 2 and head_words[0] not in norm_b.split():
        query_b_prefixed = f"{head_words[0]} {query_b}"
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
    best_a = _rank_streaming_candidates(hits_a, raw_a)[0]
    best_b = next(
        (p for p in _rank_streaming_candidates(hits_b, raw_b) if p.product_id != best_a.product_id),
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
                "tiệc trà dùng trà + bánh quy/bông lan; "
                "ăn sáng dùng bánh mì/ngũ cốc/cháo/phở + sữa/cà phê. "
                "Ví dụ: [\"nước lẩu thái\", \"thịt bò\", \"rau cải\", \"mì gói\"]."
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
    excluded = _clean_negative_terms(parsed) if parsed is not None else []

    def _search_basket() -> list[tuple[str, list[ProductSnapshot]]]:
        grouped: list[tuple[str, list[ProductSnapshot]]] = []
        for kw in clean_keywords:
            hits = repository.search(query=kw, limit=12)
            if excluded:
                hits = [p for p in hits if not _item_violates_exclusions(p, excluded)]
            if savory_meal:
                non_snack = [
                    p
                    for p in hits
                    if not _SNACK_JUNK_FOR_MEAL_RE.search(
                        normalize_category_text(f"{p.name} {p.category or ''}")
                    )
                ]
                hits = non_snack or hits
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
    cleaned_first = _clean_lexical_query(answer_text)
    stripped_query = _STREAM_DETAIL_STRIP_RE.sub(" ", cleaned_first)
    detail_stop = frozenset(
        {
            "cach", "nhu", "su", "huong", "dan", "thanh",
            "phan", "bao", "quan", "xuat", "xu",
        }
    )
    target_lex = " ".join(
        tok
        for tok in stripped_query.split()
        if len(tok) >= 2 and tok not in _STREAM_FILLER_TOKENS and tok not in detail_stop
    )
    target: ProductSnapshot | None = None
    related: list[ProductSnapshot] = []
    if target_lex:
        lex_hits = await run_catalog_work(
            repository.search, query=target_lex, limit=8, timeout_seconds=3
        )
        if lex_hits:
            focused, focus_scores = _filter_streaming_candidates(lex_hits, target_lex, None)
            ordered = _rank_streaming_candidates(focused, target_lex, focus_scores=focus_scores)
            target = ordered[0]
            related = _related_candidates(ordered, [target], limit=4)
    elif visible:
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
                "Bạn là bộ ánh xạ sản phẩm siêu thị Thanh Lam Mart. Nếu yêu cầu của khách hỏi thuốc "
                "điều trị bệnh, bệnh lý nặng, dị ứng nghiêm trọng, trẻ sơ sinh, hóa chất nguy "
                "hiểm hoặc ngoài phạm vi siêu thị, trả đúng []. Ngược lại, gợi ý 1 đến 3 từ "
                "khóa sản phẩm siêu thị phổ biến (tiếng Việt ngắn gọn) phù hợp nhất với nhu cầu "
                "đời thường của khách (ví dụ đau rát họng nhẹ -> [\"trà gừng\", \"mật ong\", "
                "\"kẹo ngậm\"], quà biếu -> [\"trà\", \"bánh quy\", \"cà phê\"], trời nóng -> "
                "[\"trà bí đao\", \"nước dừa\", \"nước suối\"]). CHỈ trả đúng 1 JSON array "
                "chuỗi, không giải thích."
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

    def _search_open() -> list[ProductSnapshot]:
        seen_ids: set[str] = set()
        merged: list[ProductSnapshot] = []
        for kw in clean_keywords:
            norm_kw = normalize_category_text(kw)
            hits = repository.search(query=kw, limit=10)
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
    post_guidance = shopping_guidance(f"{answer_text} {top.name} {top.category or ''}", limit=16)
    if any(
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
    norm_answer = normalize_category_text(answer_text)
    if _STREAM_LABEL_OR_EFFICACY_RE.search(norm_answer):
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
    if parsed.intent in {"purchase_action", "location"}:
        return None
    if parsed.ranking_criterion == "sales_popularity":
        return None
    budget_followup = bool(previous_memory.get("need") and parsed.budget_vnd is not None)
    if parsed.intent == "price" and not budget_followup:
        return None
    if _ambiguous_identity_question(answer_text, parsed):
        return None

    guidance_items = shopping_guidance(understood_text, limit=32)
    if any(
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
        _STREAM_BASKET_OR_MEAL_RE.search(norm_answer)
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
    plan = _fast_path_single_category_plan(
        answer_text,
        parsed,
        paths,
        previous_memory,
        budget_followup=budget_followup,
        explicit_sizes=_product_sizes(answer_text),
        repository=repository,
        for_streaming=True,
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
            if not focus_scores and _focus_tokens_for_streaming(answer_text, parsed.product_type):
                cleaned_focus_q = _clean_lexical_query(answer_text)
                if cleaned_focus_q:
                    lex_extra = await run_catalog_work(
                        repository.search, query=cleaned_focus_q, limit=12, timeout_seconds=3
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
                "selection": {"picks": [{"candidate": i} for i in range(len(primary_products))]},
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

    # Path D: Direct lexical product search (e.g. "ba chỉ bò Mỹ loại nào ngon?", 0 planner calls).
    excluded_terms = _clean_negative_terms(parsed)
    cleaned_lex = _clean_lexical_query(answer_text)
    if excluded_terms and cleaned_lex:
        excluded_toks = {
            tok for term in excluded_terms for tok in normalize_category_text(term).split()
        }
        cleaned_lex = " ".join(
            tok for tok in cleaned_lex.split() if tok not in excluded_toks
        )
    if (
        cleaned_lex
        and (
            not _STREAM_BASKET_OR_MEAL_RE.search(norm_answer)
            or _SINGLE_ITEM_SEASONING_RE.search(norm_answer)
        )
        and not _FAST_PATH_MULTI_OR_COMPARE.search(norm_answer)
    ):
        def _lex_search() -> list[ProductSnapshot]:
            hits = repository.search(query=cleaned_lex, limit=24)
            return _apply_streaming_constraints(hits, answer_text, parsed)

        lex_hits = await run_catalog_work(_lex_search, timeout_seconds=3)
        if lex_hits:
            focused, focus_scores = _filter_streaming_candidates(lex_hits, answer_text, None)
            ordered = _rank_streaming_candidates(
                focused,
                answer_text,
                focus_scores=focus_scores,
                cheaper_priority=cheaper_followup,
            )
            primary_products = ordered[:1]
            related_products = _related_candidates(ordered, primary_products, limit=4)
            top_cat = primary_products[0].category or cleaned_lex
            lex_plan = ConsultationDecision(
                action="recommend",
                need=text[:500],
                searches=[
                    SearchNeed(category=top_cat, role=cleaned_lex, excluded=excluded_terms)
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
