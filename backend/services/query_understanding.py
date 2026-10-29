"""Lightweight, catalog-grounded parsing for conversational retail queries.

This module does not attempt to infer product benefits. It separates a product
reference from an explicitly stated use case and ranking request so the catalog
retriever does not require purpose words to appear in the product title.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.products.category_taxonomy import (
    CatalogCategoryMatch,
    catalog_leaf_query_phrase,
    category_alias_refines_explicit_audience,
    category_is_parent,
    normalize_category_text,
    resolve_catalog_category,
    resolve_catalog_category_alias,
    resolve_catalog_category_with_explicit_audience,
)
from backend.services.vietnamese_intent import normalize_vietnamese

QueryIntent = Literal[
    "product_search",
    "recommendation",
    "location",
    "price",
    "brand",
    "variant",
    "attribute",
    "comparison",
    "alternative",
    "purchase_action",
    "general",
]
RankingCriterion = Literal[
    "relevance",
    "price_ascending",
    "price_descending",
    "sales_popularity",
    "customer_rating",
]
BudgetScope = Literal["per_item", "total"]
BudgetOperator = Literal["LT", "LTE"]


class QueryUnderstandingResult(BaseModel):
    """Validated intermediate representation; all text values come from the user/catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: QueryIntent
    secondary_intents: tuple[QueryIntent, ...] = ()
    product_type: str | None = None
    product_types: tuple[str, ...] = ()
    product_reference: str = ""
    use_case: str | None = None
    hard_constraints: tuple[str, ...] = ()
    soft_preferences: tuple[str, ...] = ()
    negative_constraints: tuple[str, ...] = ()
    budget_vnd: int | None = Field(default=None, ge=0)
    budget_scope: BudgetScope | None = None
    budget_operator: BudgetOperator | None = None
    quantity: int | None = Field(default=None, ge=1, le=10_000)
    brand_preference: str | None = None
    comparison_scope: str | None = None
    comparison_targets: tuple[str, ...] = ()
    conversation_reference: str | None = None
    ranking_criterion: RankingCriterion = "relevance"
    ambiguity: tuple[str, ...] = ()
    clarification_needed: bool = False


_RANKING_CUES: tuple[tuple[RankingCriterion, re.Pattern[str]], ...] = (
    (
        "sales_popularity",
        re.compile(
            r"\b(?:ban chay(?: nhat| nhut)?|duoc mua nhieu|mua nhieu(?: nhat| nhut)?|"
            r"pho bien nhat|nhieu nguoi chon|duoc ua chuong nhat|best seller|"
            r"bestseller|most popular|most sold)\b"
        ),
    ),
    (
        "customer_rating",
        re.compile(
            r"\b(?:ngon nh(?:at|ut)|tot nh(?:at|ut)|danh gia cao nh(?:at|ut)|"
            r"duoc yeu thich nh(?:at|ut)|"
            r"best rated|highest rated|best tasting)\b"
        ),
    ),
    (
        "price_ascending",
        re.compile(
            r"\b(?:(?:loai|mon|san pham|goi) nao re nhat|re nhat|it tien nhat|"
            r"(?:loai|mon|san pham|cai|goi) nao it tien|"
            r"gia re(?: nhat)?|(?:loai|mon|san pham) nao re|"
            r"re(?!\s+hon)\b|gia mem|gia de chiu(?: nhat)?|de chiu nhat|"
            r"gia phai chang|gia thap nhat|thap nhat|gia thap|"
            r"cheapest|lowest price|lowest cost|least expensive)\b"
        ),
    ),
    (
        "price_descending",
        re.compile(r"\b(?:dat nhat|gia cao nhat|cao nhat|most expensive|highest price)\b"),
    ),
)
_PURPOSE_MARKER = re.compile(
    r"\b(?:de|dung de|mua de|tim de|phu hop voi|phu hop cho|"
    r"danh cho nhu cau|dung cho nhu cau|for the purpose of)\s+(.+)$"
)
_DIRECT_USE_CASE = re.compile(
    r"\b(?:an|nau|che bien|pha|uong|boi|thoa|rua|giat|lau|nhung)\s+"
    r"(?:mon\s+)?(?:lau|nuong|chien|xao|hap|ham|luoc|ran|tiec|da kho|"
    r"toc dau|da nhay cam)\b"
)
_PERSONAL_CARE_NEED = re.compile(
    r"\b(?:da(?:\s+(?:mat|dau))?|toc|scalp|skin|hair)\s+"
    r"(?:(?:cua\s+)?(?:minh|toi|em|anh|chi|ban)\s+)?"
    r"(?:(?:rat|hoi|nhanh|dang|bi|de|cuc|qua|hon|hay|thuong)\s+){0,3}"
    r"(?:kho|dau|nhay cam|mun|hon hop|bet|gau|rung|gay rung|lao hoa)"
    r"(?:\s+(?:cang|xo|xu|nhieu|nhanh))?\b"
)
_PERSONAL_CARE_CONTEXT_TAIL = re.compile(
    r"^\s+(?:sau\s+(?:khi|luc)|khi|luc|vi|do|boi|nhat\s+la\s+khi|"
    r"dac\s+biet\s+khi|vao)(?:\s+[\w]+){0,4}\b"
)
_NEGATION = re.compile(
    r"\b(?:khong|ko|khum|tranh|loai bo|loai tru|bo qua|avoid|without|except|no|"
    r"dung(?=\s+(?:lay|mua|chon|dung|su\s+dung|use|buy|choose)\b))\s+"
    r"(?:(?:thich|muon|can|lay|mua|dung|chon|co|tim|use|buy|choose|want)\s+){0,2}"
    r"(?!biet\b|ro\b|nho\b|can\s+gi\b)([^,.;!?]{1,80})"
)
_NEGATED_CATEGORY_CORRECTION = re.compile(
    r"\b(?:khong|ko|khum)\s+phai\s+(?:loai\s+)?(.+?)(?:[,.!?;:]|$)"
)
_COURTESY_TAIL_PARTICLES = frozenset({"a", "ha", "nha", "nhe", "nhi", "please"})
_NEGATION_TAIL_STOP = re.compile(
    r"\b(?:uu\s+tien|prefer|truoc\s+tien|sau\s+do|va|nhung|con|hay|hoac|neu|if|doi\s+(?:thanh|sang)|but|and|or|please|nhe|nha|duoi|tren|"
    r"toi\s+da|khong\s+qua|ngan\s+sach|qua|under|up\s+to|less\s+than|budget|"
    r"cho\s+(?:minh|toi|em|anh|chi|ban)\s+(?:xem|hoi|biet)|"
    r"cho\s+xem|xem\s+giup|tim|minh|toi|em|anh|chi|ban|biet|noi|hoi)\b"
    r"|\b\d+(?:[.,]\d+)?\s*(?:trieu|tr|k|ngan|nghin|dong|vnd)\b"
)
_FINAL_SELECTION = re.compile(
    r"\b(?:truoc mat|truoc tien|dau tien|truoc het|tam thoi)\s+(?:thi\s+)?"
    r"(?:(?:cho\s+)?(?:minh|toi|em|anh|chi|ban)\s+)?"
    r"(?:xem|tim|gioi thieu|chon|mua|lay|tra cuu|goi y)\s+(?:nhom\s+)?(.+)$|"
    r"\b(?:minh|toi|em|anh|chi|ban)\s+(?:thi\s+)?(?:dang\s+)?"
    r"(?:can|muon|tim|chon|lay|xem)\s+(.+)$|"
    r"\b(?:gioi thieu|xem|tim|chon|mua|lay|tra cuu|goi y)\s+"
    r"(?:nhom\s+)?(.+?)\s+truoc$"
)
_COMPARISON_OPERAND_SEPARATOR = re.compile(r"\b(?:voi|vs|versus|hay|hoac|va|and|or)\b")
_COMPARISON_TARGET_QUESTION_TAIL = re.compile(
    r"\s*,?\s*(?:(?:loai|cai|mon|san pham)\s+nao|which\s+(?:one|product))\b.*$"
)
_PRODUCT_ATTRIBUTE_MARKER = re.compile(r"\b(?:vi|huong|mui|mau|flavor|scent|color)\b")
_NUMBER_WORD = (
    r"(?:khong|mot|hai|ba|bon|tu|nam|lam|sau|bay|tam|chin|muoi|"
    r"chuc|tram)(?:\s+(?:mot|hai|ba|bon|tu|nam|lam|sau|bay|tam|chin|muoi|chuc|tram)){0,4}"
)
_NUMBER_VALUE = rf"(?:\d{{1,4}}|{_NUMBER_WORD})"
_BUDGET_AMOUNT = rf"(?:\d{{1,4}}(?:\s+\d{{3}})*|{_NUMBER_WORD})"
_QUANTITY = re.compile(
    rf"\b({_NUMBER_VALUE})\s*(?:cai|chai|lon|hop|goi|tui|bich|thung|loc|vi|"
    rf"quyen|qua|mieng|vien|ly)\b"
)
_BUDGET = re.compile(
    rf"\b(?:duoi|toi da|khong qua|dung qua|tam|khoang|trong muc|trong tam|"
    rf"ngan sach(?: la| tam)?|under|up to|less than)\s+"
    rf"({_BUDGET_AMOUNT})\s*(trieu|tr|k|ngan|nghin|dong|vnd|d)\b"
)
_BUDGET_WITH_MONEY = re.compile(
    rf"\b(?:minh|toi|em|anh|chi|ban)?\s*co\s+(?:(?:khoang|tam)\s+)?"
    rf"({_BUDGET_AMOUNT})\s*"
    r"(trieu|tr|k|ngan|nghin|dong|vnd|d)\b"
)
_BUDGET_TOTAL_SCOPE = re.compile(
    r"\b(?:tong\s+(?:cong|tien)|ca\s+(?:don|gio\s+hang)|"
    r"toan\s+bo(?:\s+don)?|in\s+total|total\s+budget|whole\s+order|entire\s+order)\b"
)
_BUDGET_PER_ITEM_SCOPE = re.compile(
    r"\b(?:moi\s+(?:chai|cai|hop|goi|tui|lon|san\s+pham|mon)|"
    r"per\s+(?:item|bottle|pack|product)|each)\b"
)
_CONVERSATION_REFERENCE = re.compile(
    r"\b(?:loai nao khac|cai nao khac|mon nao khac|san pham nao khac|"
    r"loai khac|cai khac|mon khac|san pham khac|nho hon|re hon|"
    r"lua chon nao khac|phuong an nao khac|lua chon khac|phuong an khac|"
    r"dat hon|quay lai (?:mon|cai|san pham|loai) ban dau|"
    r"back to (?:the )?(?:original|initial) (?:item|product|one)|"
    r"mon ban dau|cai ban dau|san pham ban dau|loai ban dau|"
    r"cai do|mon do|san pham do|loai dau tien|mon dau tien|"
    r"san pham dau tien|no|cai ay|loai ay|that one|the other one|"
    r"first item|first product|first one|cheaper|more expensive|instead)\b"
)
_GENERIC_REFERENCES = frozenset(
    {
        "cai nao",
        "loai nao",
        "mon nao",
        "san pham nao",
        "hang nao",
        "lua chon nao khac",
        "phuong an nao khac",
        "loai khac",
        "cai khac",
        "mon khac",
        "lua chon khac",
        "phuong an khac",
        "loai dau tien",
        "mon dau tien",
        "san pham dau tien",
        "quay lai mon ban dau",
        "quay lai cai ban dau",
        "quay lai san pham ban dau",
        "quay lai loai ban dau",
        "mon ban dau",
        "cai ban dau",
        "san pham ban dau",
        "loai ban dau",
        "back to original item",
        "back to original product",
        "back to initial item",
        "first item",
        "first product",
        "first one",
        "re hon",
        "dat hon",
        "nho hon",
        "cheaper",
        "more expensive",
        "the other one",
    }
)
_GENERIC_FOLLOW_UP = re.compile(
    r"^(?:co\s+)?(?:loai nao(?: khac)?|cai nao(?: khac)?|mon nao(?: khac)?|"
    r"san pham nao(?: khac)?|loai khac|cai khac|mon khac|san pham khac|"
    r"lua chon nao khac|phuong an nao khac|lua chon khac|phuong an khac|"
    r"loai dau tien|mon dau tien|san pham dau tien|first item|first product|first one|"
    r"re hon|dat hon|nho hon|cai do|mon do|san pham do|"
    r"that one|the other one|cheaper|more expensive)(?:\s+(?:khong|nua|khong a))?$"
)


def prior_product_page_reference_kind(text: str) -> str | None:
    """Resolve explicit references to the first item in the active browse flow."""
    normalized = " ".join(normalize_vietnamese(text).split())
    if re.search(
        r"\b(?:(?:quay|tro)\s+lai\s+)?(?:mon|cai|san\s+pham|loai)\s+ban\s+dau\b",
        normalized,
    ) or re.search(
        r"\bback\s+to\s+(?:the\s+)?(?:original|initial)\s+(?:item|product|one)\b",
        normalized,
    ):
        return "original"
    return None


_COMPARISON = re.compile(
    r"\b(?:so sanh|phan biet|co gi khac|khac gi|khac nhau|vs|versus|compare|difference)\b"
)
_LOCATION = re.compile(
    r"\b(?:o dau|o day|nam o dau|ke nao|day nao|khu nao|ngan nao|vi tri nao|"
    r"vi tri (?:quay|ke|day|khu|ngan)|gan (?:quay|ke|day|khu) nao|"
    r"ben nao|o (?:ke|day|khu|ngan|quay|tu mat|tu lanh)|"
    r"nam (?:o|trong|gan|khu)\b|"
    r"chi (?:minh )?(?:vi tri|duong)|where is|where can i find)\b"
)
_LOCATION_REFERENCE_SUFFIX = re.compile(
    r"\b(?:nam\s+(?:o|trong|tren|gan|khu)\b|"
    r"(?:o|tai|trong|gan)\s+(?:khu|ke|day|ngan|quay|tu|tu\s+mat|tu\s+lanh))\b"
)
_EXPLICIT_NAMED_LOCATION_SUFFIX = re.compile(r"\bnằm\s+(?:ở|trong|trên|gần|khu)\b", re.IGNORECASE)
_PRICE = re.compile(
    r"\b(?:gia(?!\s+dinh\b)|bao nhieu(?: tien)?|bao tien|may dong|price|how much|cost)\b"
)
_DIRECT_PRICE_QUESTION = re.compile(
    r"\b(?:bao nhieu(?: tien)?|bao tien|may dong|how much|what(?:'s| is) the price|"
    r"what does it cost)\b"
)
_PURCHASE_ACTION = re.compile(
    r"\b(?:thanh toan|cho vao gio|them vao gio|dat hang|xac nhan don|checkout|place an order)\b"
)
_RECOMMEND = re.compile(
    r"\b(?:tu van|goi y|de xuat|chon giup|chon ho|nen mua|nen chon|"
    r"recommend|suggest|help me choose|looking for|"
    r"(?:loai|hang|mon|san pham) nao\s+(?:on|tot|ngon|phu hop|de dung|ben|chat luong))\b"
)
_BRAND_QUESTION = re.compile(
    r"\b(?:thuong hieu|nhan hieu)\s+(?:nao|gi)\b|"
    r"\bnhan(?:\s+hang)?\s+(?:nao|gi)\b|"
    r"\b(?:brand|which brand|what brand|hang nao)\b"
)
_AMBIGUOUS_WHICH_BRAND = re.compile(r"\bhang nao\b")
_GENERIC_RECOMMENDATION_QUALIFIER = re.compile(
    r"^\s*(?:on|tot|ngon|re|ban chay|phu hop|de dung|ben|chat luong)\b"
)
_VARIANT_QUESTION = re.compile(
    r"\b(?:loai nao|co loai khac|kich co|size|dung tich|quy cach|variant|pack size|volume)\b"
)
_ATTRIBUTE_QUESTION = re.compile(
    r"\b(?:(?<!tong )cong dung|thanh phan|huong dan su dung|dung nhu the nao|"
    r"ingredients|benefits?|usage instructions|specifications?|"
    r"an toan|di ung|sensitive skin|safe|co giup\b|giup .{1,100}\bkhong\b|"
    r"does it help|will it help)\b"
)
_BROAD_UNRESOLVED_REQUEST = re.compile(
    r"\b(?:do an|do uong|mon gi|hang hoa|loai nao|cai nao|san pham nao|"
    r"mua gi|can gi|recommendation|something to eat|something to drink)\b"
)
_REQUEST_PREFIX = re.compile(
    r"^(?:(?:minh|toi|em|anh|chi|ban)\s+)?"
    r"(?:dang\s+)?(?:muon(?:\s+tim)?|can(?:\s+tim)?|tim\s+giup(?:\s+(?:minh|toi|em|anh|chi))?|"
    r"tim|cho\s+(?:minh|toi|em|anh|chi)\s+(?:xem|tim|hoi(?:\s+ve)?)|"
    r"cho\s+xem|xem\s+giup|show\s+me|looking\s+for|"
    r"please\s+(?:find|show)|find\s+me|find)\b\s*"
)
_CATEGORY_REQUEST_CONNECTOR = re.compile(
    r"(?:^|\s)(?:co|can|muon|tim|mua|dung|goi y|tu van|recommend|suggest)"
    r"(?:\s+(?:tim|mua|dung))?\s*$"
)
_RAW_CATEGORY_REQUEST_CONNECTOR = re.compile(
    r"(?:^|\s)(?:có|co|cần|can|muốn|muon|tìm|tim|mua|dùng|dung|"
    r"gợi\s+ý|goi\s+y|tư\s+vấn|tu\s+van|recommend|suggest)"
    r"(?:\s+(?:tìm|tim|mua|dùng|dung))?\s*$",
    re.IGNORECASE,
)
_IMPLICIT_NEED_FILLER = frozenset(
    "minh toi em anh chi ban minh tui muon can co tim mua dung goi tu van cho xem hang "
    "loai nhom san pham nao nay trong cua tai o duoc khong a nhe nha please find show "
    "looking for product products category type um uhm neu hoi danh muc ay the if ask "
    "this that me i".split()
)
_IMPLICIT_QUERY_TAIL_FILLER = frozenset(
    "nao nay do trong cua tai o hang co khong a vay nhe nha please".split()
)
_USE_CASE_REQUEST_CLOSINGS = (
    "co loai nao khong",
    "co loai nao",
    "co mon nao khong",
    "co mon nao",
    "co san pham nao khong",
    "co san pham nao",
    "co loai gi khong",
    "co mon gi khong",
    "giup cho minh",
    "giup cho toi",
    "giup cho em",
    "giup cho anh",
    "giup cho chi",
    "giup minh",
    "giup toi",
    "giup em",
    "giup anh",
    "giup chi",
    "giup tui",
    "giup t",
    "giup tao",
    "gium minh",
    "gium toi",
    "please help me",
)


def _raw_span_for_normalized_span(
    raw: str,
    normalized: str,
    start: int,
    end: int,
) -> tuple[int, int]:
    """Map a span in accent-folded text back to its original user text."""
    raw_words = list(re.finditer(r"[^\W_]+", raw, flags=re.UNICODE))
    normalized_words = list(re.finditer(r"[^\W_]+", normalized, flags=re.UNICODE))
    if not raw_words or not normalized_words:
        return max(0, min(start, len(raw))), max(0, min(end, len(raw)))

    expanded: list[tuple[str, int]] = []
    for raw_index, raw_word in enumerate(raw_words):
        expanded.extend(
            (word, raw_index) for word in normalize_vietnamese(raw_word.group()).split()
        )
    targets = [word.group() for word in normalized_words]
    source_indices: list[int | None] = [None] * len(targets)
    opcodes = SequenceMatcher(
        None,
        [word for word, _index in expanded],
        targets,
        autojunk=False,
    ).get_opcodes()
    for tag, source_start, source_end, target_start, target_end in opcodes:
        target_count = target_end - target_start
        source_count = source_end - source_start
        if tag == "equal":
            for offset in range(target_count):
                source_indices[target_start + offset] = expanded[source_start + offset][1]
        elif tag == "replace" and source_count:
            for offset in range(target_count):
                source_offset = min(
                    source_count - 1,
                    round(offset * source_count / max(1, target_count)),
                )
                source_indices[target_start + offset] = expanded[source_start + source_offset][1]
        elif tag == "insert":
            neighbor = (
                source_indices[target_start - 1]
                if target_start > 0
                else (expanded[0][1] if expanded else 0)
            )
            for target_index in range(target_start, target_end):
                source_indices[target_index] = neighbor

    hits = [
        index
        for index, word in enumerate(normalized_words)
        if word.start() < end and word.end() > start
    ]
    if not hits:
        hits = [
            min(
                range(len(normalized_words)),
                key=lambda index: abs(normalized_words[index].start() - start),
            )
        ]
    aligned = [source_indices[index] for index in hits if source_indices[index] is not None]
    if not aligned:
        return max(0, min(start, len(raw))), max(0, min(end, len(raw)))
    return raw_words[min(aligned)].start(), raw_words[max(aligned)].end()


def _strip_matches(raw: str, normalized: str, pattern: re.Pattern[str]) -> tuple[str, str | None]:
    """Remove matching normalized spans while retaining the customer's original wording."""
    matches = list(pattern.finditer(normalized))
    if not matches:
        return raw, None
    for match in matches:
        start, end = _raw_span_for_normalized_span(raw, normalized, match.start(), match.end())
        if match.lastindex:
            capture_start, capture_end = _raw_span_for_normalized_span(
                raw,
                normalized,
                match.start(1),
                match.end(1),
            )
            use_case = raw[capture_start:capture_end].strip(" ,.!?;:")
        else:
            use_case = None
        cleaned = f"{raw[:start]} {raw[end:]}"
        return " ".join(cleaned.split()).strip(" ,.!?;:"), use_case
    return raw, None


def _strip_purpose_clause(raw: str, normalized: str) -> tuple[str, str | None]:
    """Separate an infinitive ``để`` clause without mistaking ``dễ`` for it."""
    for match in _PURPOSE_MARKER.finditer(normalized):
        if normalized.startswith("de", match.start()):
            marker_start, marker_end = _raw_span_for_normalized_span(
                raw,
                normalized,
                match.start(),
                match.start() + 2,
            )
            raw_marker = raw[marker_start:marker_end].casefold()
            captured = normalize_vietnamese(match.group(1)).strip()
            if raw_marker == "dễ" or (raw_marker == "de" and captured.startswith("chiu")):
                # Accent folding makes ``dễ chịu`` look like ``để chịu``.
                # Preserve the preference as user text instead of deleting it
                # from the product request or treating it as a preparation.
                continue

        start, end = _raw_span_for_normalized_span(
            raw,
            normalized,
            match.start(),
            match.end(),
        )
        capture_start, capture_end = _raw_span_for_normalized_span(
            raw,
            normalized,
            match.start(1),
            match.end(1),
        )
        use_case = raw[capture_start:capture_end].strip(" ,.!?;:")
        cleaned = f"{raw[:start]} {raw[end:]}"
        return " ".join(cleaned.split()).strip(" ,.!?;:"), use_case
    return raw, None


def _trim_use_case_request_closing(value: str) -> str:
    """Remove a trailing polite request phrase from a captured purpose clause."""
    normalized = " ".join(normalize_vietnamese(value).split())
    for closing in _USE_CASE_REQUEST_CLOSINGS:
        if normalized.endswith(f" {closing}"):
            return " ".join(value.split()[: -len(closing.split())]).strip(" ,.!?;:")
    if normalized.endswith(" khong"):
        return " ".join(value.split()[:-1]).strip(" ,.!?;:")
    return value.strip(" ,.!?;:")


def _ranking_criterion(normalized: str) -> RankingCriterion:
    for criterion, cue in _RANKING_CUES:
        if cue.search(normalized):
            return criterion
    return "relevance"


def _budget_vnd(normalized: str) -> int | None:
    match = _budget_match(normalized)
    if match is None:
        return None
    amount = _number_expression(match.group(1))
    if amount is None:
        return None
    unit = match.group(2)
    multiplier = (
        1_000_000 if unit in {"trieu", "tr"} else 1_000 if unit in {"k", "ngan", "nghin"} else 1
    )
    if unit is None and "chuc" in match.group(1).split():
        multiplier = 1_000
    value = round(amount * multiplier)
    return value if 0 <= value <= 100_000_000 else None


def _budget_operator(normalized: str) -> BudgetOperator | None:
    match = _budget_match(normalized)
    if match is None:
        return None
    cue = normalized[match.start() : match.start(1)]
    return "LT" if re.search(r"\b(?:duoi|under|less than)\b", cue) else "LTE"


def _budget_match(normalized: str) -> re.Match[str] | None:
    for match in _BUDGET.finditer(normalized):
        # Accent folding makes “sữa tắm nam” look like the approximate-budget
        # phrase “tầm năm”. Do not treat the product-gender suffix as a price.
        prefix = normalized[: match.start(1)]
        if re.search(r"\bsua\s+tam\s*$", prefix):
            continue
        # ``nước khoáng, không phải ...`` folds to ``nuoc khoang khong phai``.
        # Here ``khoang`` is part of the product name, not an approximate-budget
        # cue followed by the number zero.
        if match.group(1) == "khong" and re.match(r"\s+phai\b", normalized[match.end(1) :]):
            continue
        return match
    return _BUDGET_WITH_MONEY.search(normalized)


def _selected_focus_reference(
    raw: str,
    normalized: str,
    category_hierarchies: tuple[tuple[str, ...], ...],
) -> str | None:
    """Use an explicit final selection to scope lookup while retaining comparison intent."""
    for cue in _FINAL_SELECTION.finditer(normalized):
        group_index = next(
            (index for index in (1, 2, 3) if cue.group(index) is not None),
            0,
        )
        if not group_index:
            continue
        focus = " ".join((cue.group(group_index) or "").split()).strip(" ,.!?;:")
        if not focus:
            continue
        focus_match = resolve_catalog_category(focus, category_hierarchies)
        if focus_match is None:
            focus_match = resolve_catalog_category_alias(focus, category_hierarchies)
        sibling_focus = False

        # Predicate-only choices such as ``nhóm tiệt trùng`` inherit the
        # named subject from a comparison operand, then resolve only among
        # sibling categories of that subject in the live taxonomy.
        prefix = normalized[: cue.start()].strip(" ,.!?;:")
        operand_parts = _COMPARISON_OPERAND_SEPARATOR.split(prefix)
        if len(operand_parts) >= 2:
            left_match = resolve_catalog_category(operand_parts[0], category_hierarchies)
            if left_match is None:
                left_match = resolve_catalog_category_alias(operand_parts[0], category_hierarchies)
            if left_match is not None:
                focus_tokens = set(normalize_vietnamese(focus).split()) - {
                    "nhom",
                    "loai",
                    "hang",
                    "san",
                    "pham",
                    "truoc",
                    "nhe",
                }
                siblings: set[str] = set()
                left_path = next(
                    (
                        path
                        for path in category_hierarchies
                        if path
                        and normalize_vietnamese(path[-1])
                        == normalize_vietnamese(left_match.category)
                    ),
                    (),
                )
                for path in category_hierarchies:
                    if (
                        len(path) < 2
                        or not left_path
                        or path[:-1] != left_path[:-1]
                        or normalize_vietnamese(path[-1])
                        == normalize_vietnamese(left_match.category)
                    ):
                        continue
                    leaf_tokens = set(normalize_vietnamese(path[-1]).split())
                    if focus_tokens and focus_tokens <= leaf_tokens:
                        siblings.add(path[-1])
                if len(siblings) == 1:
                    focus_match = resolve_catalog_category(
                        next(iter(siblings)), category_hierarchies
                    )
                    sibling_focus = focus_match is not None
        if focus_match is None:
            continue
        if sibling_focus:
            # The predicate alone may resolve to an unrelated catalog label;
            # use the unique sibling label that the comparison context proves.
            return focus_match.category

        start, end = _raw_span_for_normalized_span(
            raw, normalized, cue.start(group_index), cue.end(group_index)
        )
        selected_raw = raw[start:end].strip(" ,.!?;:")
        # Resolver matches need at least one taxonomy-bound exact/alias phrase;
        # avoid changing a product reference based on generic selection filler.
        if selected_raw and (focus_match.exact_phrase or focus_match.matched_tokens):
            return selected_raw
    return None


def _is_colloquial_address_not_quantity(raw: str, match: re.Match[str]) -> bool:
    phrase = raw[match.start() : match.end()]
    return bool(re.match(r"\bbày\s+tui\b", phrase, re.IGNORECASE))


_NUMBER_WORD_VALUES = {
    "khong": 0,
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "tu": 4,
    "nam": 5,
    "lam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
}


def _number_expression(value: str) -> float | None:
    """Parse common spoken Vietnamese quantities and budgets without an LLM."""
    value = " ".join(normalize_vietnamese(value).split())
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
        return float(re.sub(r"[.,]", "", value))
    if re.fullmatch(r"\d{1,3}(?:\s+\d{3})+", value):
        return float(re.sub(r"\s+", "", value))
    try:
        return float(value.replace(",", "."))
    except ValueError:
        pass
    tokens = value.split()
    if not tokens or any(
        token not in {*_NUMBER_WORD_VALUES, "muoi", "chuc", "tram"} for token in tokens
    ):
        return None
    total = 0
    group = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "tram":
            base = group or 1
            group = base * 100
        elif token in {"muoi", "chuc"}:
            if group < 10:
                group = (group or 1) * 10
            else:
                group += 10
        else:
            number = _NUMBER_WORD_VALUES[token]
            if index + 1 < len(tokens) and tokens[index + 1] == "tram":
                group += number * 100
                index += 1
            elif index + 1 < len(tokens) and tokens[index + 1] in {"muoi", "chuc"}:
                group += number * 10
                index += 1
            else:
                group += number
        index += 1
    total += group
    return float(total)


def _is_brand_question(normalized: str, raw: str) -> bool:
    match = _BRAND_QUESTION.search(normalized)
    if match is None:
        return False
    label_question = re.search(r"\bnhan(?:\s+hang)?\s+(?:nao|gi)\b", match.group())
    if label_question:
        raw_label = re.search(r"\bnhãn(?:\s+hàng)?\s+(?:nào|gì)\b", raw, re.IGNORECASE)
        if raw_label:
            prefix = normalize_vietnamese(raw[: raw_label.start()])
            return not bool(re.search(r"\b(?:theo|trên|ghi)\s*$", prefix))
        # Avoid treating accentless ``nhận hàng nào`` as a brand question.
        prefix = normalized[: match.start()]
        return bool(re.search(r"\b(?:thuong hieu|nhan hieu|brand)\b", prefix))
    if not _AMBIGUOUS_WHICH_BRAND.search(match.group()):
        return True
    # "hãng nào" asks for a manufacturer, while "hàng nào ổn/ngon" asks for a
    # recommendation. Accentless "hang nao" stays conservative unless syntax
    # clearly asks what a named product belongs to.
    raw_match = re.search(r"\bhãng\s+nào\b", raw, re.IGNORECASE)
    if raw_match:
        return True
    suffix = normalized[match.end() :]
    prefix = normalized[: match.start()]
    if _GENERIC_RECOMMENDATION_QUALIFIER.match(suffix):
        return False
    return bool(re.search(r"\b(?:cua|thuoc|from|made by)\s*$", prefix))


def _comparison_targets(raw: str, normalized: str) -> tuple[str, ...]:
    """Keep each explicit operand when a turn compares multiple entities."""
    marker = _COMPARISON.search(normalized)
    if marker is None:
        return ()

    comparison_verb = re.search(r"\b(?:so sanh|phan biet|compare|difference between)\b", normalized)
    body_start = comparison_verb.end() if comparison_verb else 0
    # In postposed comparisons ("A với B khác nhau thế nào"), the comparison
    # marker follows the entities; with a leading verb, it precedes them.
    if comparison_verb:
        body_end = len(normalized)
        trailing_comparison = re.search(
            r"\b(?:co gi khac|khac gi|khac nhau|differ(?:ence)?|the nao)\b",
            normalized[body_start:],
        )
        if trailing_comparison:
            body_end = body_start + trailing_comparison.start()
    elif marker.group() in {"vs", "versus"}:
        body_end = len(normalized)
    else:
        body_end = marker.start()

    body = normalized[body_start:body_end]
    body = _COMPARISON_TARGET_QUESTION_TAIL.sub("", body)
    body_end = body_start + len(body)
    between_prefix = re.match(r"\s*(?:giua|between)\s+", body)
    if between_prefix:
        body_start += between_prefix.end()
        body = normalized[body_start:body_end]

    boundaries = [match for match in _COMPARISON_OPERAND_SEPARATOR.finditer(body)]
    if not boundaries:
        return ()
    segments: list[tuple[int, int]] = []
    cursor = body_start
    for boundary in boundaries:
        segments.append((cursor, body_start + boundary.start()))
        cursor = body_start + boundary.end()
    segments.append((cursor, body_end))

    targets: list[str] = []
    for start, end in segments:
        segment = normalized[start:end]
        left = len(segment) - len(segment.lstrip(" ,.!?;:"))
        right = len(segment.rstrip(" ,.!?;:"))
        start += left
        end = min(end, start + max(0, right - left))
        if end <= start:
            continue
        target_norm = normalized[start:end]
        leading_filler = re.match(r"^(?:loai|mon|san pham|hang)\s+", target_norm)
        if leading_filler:
            start += leading_filler.end()
            target_norm = target_norm[leading_filler.end() :]
        for tail in (
            r"\s+(?:khac nhau(?: nhu the nao)?|khac gi|co gi khac|the nao)$",
            r"\s+(?:la|thi)$",
            r"\s+(?:giup\s+(?:minh|toi|em|anh|chi)|nhe|nha|duoc\s+khong)$",
        ):
            target_norm = re.sub(tail, "", target_norm).strip()
            end = start + len(target_norm)
        if not target_norm:
            continue
        raw_start, raw_end = _raw_span_for_normalized_span(raw, normalized, start, end)
        target = raw[raw_start:raw_end].strip(" ,.!?;:")
        target = re.sub(
            r"\s+(?:là|thì)$|\s+(?:giúp\s+(?:mình|tôi|em|anh|chị)|nhé|nha|được\s+không)$",
            "",
            target,
            flags=re.IGNORECASE,
        ).strip()
        if target:
            targets.append(target)
    return tuple(dict.fromkeys(targets)) if len(targets) >= 2 else ()


def _intent_cue_outside_exact_category(
    normalized_query: str,
    category_match: CatalogCategoryMatch | None,
    cue_pattern: re.Pattern[str],
) -> re.Match[str] | None:
    """Ignore an intent keyword when it is part of an exact taxonomy name.

    Catalog labels can contain ordinary words such as ``giá`` or abbreviations
    such as ``vs``. Remove exact category mentions before interpreting those
    tokens as price/comparison operators; explicit cues elsewhere in the turn
    remain active (e.g. ``so sánh Giá đỗ với ...``).
    """
    search_text = normalized_query
    if category_match is not None:
        category_phrase = normalize_vietnamese(category_match.category)
        if category_phrase:
            search_text = re.sub(
                rf"(?<!\w){re.escape(category_phrase)}(?!\w)",
                " ",
                search_text,
            )
    return cue_pattern.search(search_text)


_PRIMARY_TYPE_FILLERS = frozenset(
    "minh dang toi em anh chi ban can muon tim mua xem cho giup vay co khong cua hang a nhe nha "
    "loai nhom vai may mot quyen chai hop goi tui loc thung size kg g gam ml lit "
    "the nao giup voi please find show me some any a few couple".split()
)


def _single_word_category_is_primary(product_type: str, query: str) -> bool:
    """Avoid classifying a mentioned accessory as the requested product type."""
    type_tokens = normalize_vietnamese(product_type).split()
    if len(type_tokens) != 1:
        return True
    query_tokens = normalize_vietnamese(query).split()
    try:
        category_index = query_tokens.index(type_tokens[0])
    except ValueError:
        return False
    preceding = [
        token
        for token in query_tokens[:category_index]
        if token not in _PRIMARY_TYPE_FILLERS and not token.isdigit()
    ]
    return not preceding


def _negative_constraints(normalized: str, raw: str | None = None) -> tuple[str, ...]:
    values: list[str] = []
    # A request not to claim an unverified fact is an epistemic instruction,
    # not a request to filter products by the last word in that sentence.
    constraint_text = re.sub(r"\b(?:nhung\s+)?dung\s+khang\s+dinh\b.*$", " ", normalized)
    for match in _NEGATION.finditer(constraint_text):
        # A question particle such as ``không?`` asks whether a product exists;
        # the words after it may start a separate request and are not a filter.
        if raw and match.group().startswith("khong "):
            _raw_start, raw_end = _raw_span_for_normalized_span(
                raw,
                normalized,
                match.start(),
                match.start() + len("khong"),
            )
            raw_tail = raw[raw_end:]
            if re.match(r"\s*[?？]", raw_tail):
                continue
            # Vietnamese often places a follow-up request after a comma
            # instead of a question mark: ``có bán X không, tìm giúp mình``.
            # Treat that ``không`` as a yes/no particle when the next clause
            # clearly asks us to search/check/show, not as a product filter.
            post_particle = re.sub(r"^[\s,;]+", "", raw_tail)
            normalized_post_particle = normalize_vietnamese(post_particle).strip()
            if re.match(r"\s*[,;]", raw_tail) and re.match(
                r"^(?:tim|kiem tra|tra cuu|xem|cho|gui|noi)\b",
                normalized_post_particle,
            ):
                continue
        # ``không phải ...`` is commonly a correction or contrast in Vietnamese,
        # not a request to filter products by a missing catalog attribute.
        if re.match(r"(?:khong|ko|khum)\s+phai\b", match.group()):
            continue
        phrase = match.group(1).strip()
        if raw:
            raw_start, raw_end = _raw_span_for_normalized_span(
                raw,
                normalized,
                match.start(1),
                match.end(1),
            )
            raw_clause = raw[raw_start:raw_end]
            clause_boundary = re.search(r"[,;!?\n]", raw_clause)
            if clause_boundary:
                raw_clause = raw_clause[: clause_boundary.start()]
            phrase = " ".join(normalize_vietnamese(raw_clause).split()).strip(" ,.!?;:")
        # ``không cần gợi ý`` and similar meta instructions control the
        # assistant's response style; they do not describe a product filter.
        if re.match(
            r"^(?:goi y|de xuat|tu van|recommend(?:ation|ations)?|suggest(?:ion|ions)?|advice)\b",
            phrase,
        ):
            continue
        prefix = constraint_text[max(0, match.start() - 24) : match.start()]
        if re.search(r"\b(?:neu|tru khi|unless)\s*$", prefix):
            continue
        # Yes/no tails such as "còn không, giá bao nhiêu?" are not negative
        # product filters; nor is "không cần thương hiệu" an exclusion.
        if phrase.startswith(("gia bao nhieu", "bao gia", "bao tien", "may dong")):
            continue
        if phrase.startswith("can ") and re.search(r"\b(?:thuong hieu|nhan hieu|hang)\b", phrase):
            continue
        if match.group().startswith("khong ") and re.search(r"\b(?:co|con|het)\s*$", prefix):
            continue
        if phrase in {"shop", "ban", "ban oi", "a", "nha", "nhi", "ha"}:
            continue
        phrase = re.sub(r"\s+khong$", "", phrase).strip()
        stop = _NEGATION_TAIL_STOP.search(phrase)
        if stop:
            phrase = phrase[: stop.start()].strip()
        phrase = re.sub(r"^(?:loai|mon|mat hang|hang hoa)\s+", "", phrase)
        phrase = " ".join(phrase.split())
        # In Vietnamese, ``no bụng`` means filling/satiating, not the English
        # determiner ``no``. Accent removal makes those forms lexically collide.
        if match.group().startswith("no ") and phrase == "bung":
            continue
        if (
            phrase
            and not re.search(r"(?:^|\s)(?:khong|ko|khum)$", phrase)
            and phrase
            not in {
                "biet",
                "ro",
                "nho",
                "co",
                "cho",
                "xem",
                "phai",
                "muon",
                "can",
                "con hang",
                "het hang",
                "san pham nao",
                "san pham",
            }
        ):
            values.append(phrase)
    return tuple(dict.fromkeys(values))


def _trim_courtesy_tail(value: str) -> str:
    tokens = value.split()
    while len(tokens) > 1 and normalize_vietnamese(tokens[-1]) in _COURTESY_TAIL_PARTICLES:
        tokens.pop()
    return " ".join(tokens)


def _strip_request_prefix(raw: str, normalized: str) -> tuple[str, str]:
    """Remove common request wrappers without rewriting the product wording."""
    match = _REQUEST_PREFIX.match(normalized)
    if match is None:
        return raw, normalized
    prefix_word_count = len(match.group().split())
    raw_parts = raw.split(maxsplit=prefix_word_count)
    if len(raw_parts) <= prefix_word_count:
        return "", ""
    cleaned_raw = raw_parts[prefix_word_count].strip(" ,.!?;:")
    cleaned_normalized = " ".join(normalize_vietnamese(cleaned_raw).split())
    return cleaned_raw, cleaned_normalized


@lru_cache(maxsize=16)
def _catalog_category_ngrams(
    category_hierarchies: tuple[tuple[str, ...], ...],
) -> frozenset[tuple[str, ...]]:
    phrases: set[tuple[str, ...]] = set()
    for path in category_hierarchies:
        for segment in path:
            tokens = tuple(normalize_vietnamese(segment).split())
            for width in range(2, len(tokens) + 1):
                phrases.update(
                    tokens[start : start + width] for start in range(0, len(tokens) - width + 1)
                )
    return frozenset(phrases)


@lru_cache(maxsize=16)
def _normalized_catalog_segments(
    category_hierarchies: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                " ".join(normalize_vietnamese(segment).split())
                for path in category_hierarchies
                for segment in path
                if segment.strip()
            },
            key=lambda segment: (-len(segment), segment),
        )
    )


def _span_belongs_to_catalog_category(
    normalized_text: str,
    span: re.Match[str],
    category_hierarchies: tuple[tuple[str, ...], ...],
) -> bool:
    """Avoid treating part of a full taxonomy label as a separate use case."""
    for segment in _normalized_catalog_segments(category_hierarchies):
        for occurrence in re.finditer(
            rf"(?<!\w){re.escape(segment)}(?!\w)",
            normalized_text,
        ):
            if occurrence.start() <= span.start() and occurrence.end() >= span.end():
                return True
    return False


def _implicit_need_before_catalog_type(
    raw: str,
    category_hierarchies: tuple[tuple[str, ...], ...],
) -> tuple[str, str] | None:
    """Separate a stated customer need from a category in indirect wording.

    This looks for a catalog-backed category phrase and a request connector
    (e.g. ``có dầu gội``), then keeps the preceding customer need as context.
    It never infers product suitability from that need.
    """
    raw_words = list(re.finditer(r"[^\W_]+", raw, flags=re.UNICODE))
    if not raw_words:
        return None
    normalized_words = [normalize_vietnamese(match.group()) for match in raw_words]
    normalized_words = [word for token in normalized_words for word in token.split()]
    if len(normalized_words) != len(raw_words):
        return None

    category_phrases = _catalog_category_ngrams(category_hierarchies)
    mentions: list[tuple[int, int, tuple[str, ...]]] = []
    for phrase in category_phrases:
        if not phrase:
            continue
        width = len(phrase)
        for start in range(0, len(normalized_words) - width + 1):
            if tuple(normalized_words[start : start + width]) == phrase:
                mentions.append((start, start + width, phrase))
    if not mentions:
        return None

    spans = []
    for start, end, phrase in mentions:
        raw_prefix = raw[: raw_words[start].start()].strip(" ,.!?;:")
        normalized_prefix = " ".join(normalize_vietnamese(raw_prefix).split())
        connector = _CATEGORY_REQUEST_CONNECTOR.search(normalized_prefix)
        if connector is None:
            continue
        raw_connector = _RAW_CATEGORY_REQUEST_CONNECTOR.search(raw_prefix)
        if raw_connector is None:
            continue
        need_text = raw_prefix[: raw_connector.start()].strip(" ,.!?;:")
        normalized_need = " ".join(normalize_vietnamese(need_text).split())
        need_words = normalized_need.split()
        if catalog_leaf_query_phrase(need_text, category_hierarchies):
            # A previous product/category mention is not a customer need.
            # This prevents a trailing phrase such as "dùng hằng ngày" from
            # reclassifying the already stated product as the use case.
            continue
        need_tokens = set(need_words) - _IMPLICIT_NEED_FILLER
        if len(need_tokens) < 2:
            continue
        raw_end = raw_words[end - 1].end()
        suffix = raw[raw_end:].strip(" ,.!?;:")
        suffix_tokens = set(normalize_vietnamese(suffix).split())
        if suffix_tokens and suffix_tokens <= _IMPLICIT_QUERY_TAIL_FILLER:
            suffix = ""
        reference = raw[raw_words[start].start() : raw_end].strip()
        if suffix:
            reference = f"{reference} {suffix}"
        spans.append((len(phrase), reference, need_text))
    if not spans:
        return None
    _, reference, need = max(spans, key=lambda item: (item[0], -len(item[1])))
    return reference, need


def _personal_care_need_before_or_after_product(
    raw: str,
    normalized: str,
    category_hierarchies: tuple[tuple[str, ...], ...],
) -> tuple[str, str] | None:
    """Separate a stated skin/hair condition from catalog product wording.

    The condition remains unverified context. A category or product type must
    come from the catalog; a condition-only query stays a clarification rather
    than being mistaken for a homographic item such as ``bánh đa khô``.
    """
    matches = list(_PERSONAL_CARE_NEED.finditer(normalized))
    if not matches:
        return None
    for match in reversed(matches):
        care_end = match.end()
        context_tail = _PERSONAL_CARE_CONTEXT_TAIL.match(normalized[care_end:])
        if context_tail:
            care_end += context_tail.end()
        raw_start, raw_end = _raw_span_for_normalized_span(
            raw,
            normalized,
            match.start(),
            care_end,
        )
        need = raw[raw_start:raw_end].strip(" ,.!?;:")
        leading_word = re.match(r"[^\W_]+", need, flags=re.UNICODE)
        if leading_word is None or leading_word.group().casefold() not in {
            "da",
            "tóc",
            "toc",
            "skin",
            "hair",
            "scalp",
        }:
            # Accent folding turns ``đa khô`` into ``da kho``. Keep the
            # Vietnamese product term ``bánh đa khô`` out of skin semantics.
            continue
        cleaned = f"{raw[:raw_start]} {raw[raw_end:]}"
        cleaned = re.sub(
            r"\b(?:cho|với|voi|dành\s+cho|danh\s+cho|phù\s+hợp\s+với|phu\s+hop\s+voi)\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip(" ,.!?;:")
        query = catalog_leaf_query_phrase(cleaned, category_hierarchies)
        has_catalog_type = bool(
            query
            or resolve_catalog_category(cleaned, category_hierarchies)
            or resolve_catalog_category_alias(cleaned, category_hierarchies)
        )
        if has_catalog_type:
            return cleaned, need
        prefix = " ".join(normalize_vietnamese(raw[:raw_start]).split())
        suffix = " ".join(normalize_vietnamese(raw[raw_end:]).split())
        prefix_tokens = set(prefix.split())
        suffix_tokens = set(suffix.split())
        if (
            not prefix_tokens.intersection({"banh"})
            and prefix_tokens <= _IMPLICIT_NEED_FILLER
            and suffix_tokens <= _IMPLICIT_QUERY_TAIL_FILLER
        ):
            return cleaned, need
    return None


def understand_catalog_query(
    text: str,
    category_hierarchies: tuple[tuple[str, ...], ...] = (),
    previous_query: str | None = None,
    previous_queries: tuple[str, ...] = (),
) -> QueryUnderstandingResult:
    """Parse broad retail intent without looking up or fabricating product facts.

    Taxonomy resolution is delegated to the live catalog hierarchy. Purpose
    clauses are retained as soft context and removed from the strict product
    search phrase; ranking cues are surfaced separately from product identity.
    """
    raw = " ".join((text or "").split())[:2000]
    normalized = " ".join(normalize_vietnamese(raw).split())
    ranking = _ranking_criterion(normalized)
    product_reference, _ = (
        _strip_matches(
            raw,
            normalized,
            next(
                (cue for criterion, cue in _RANKING_CUES if criterion == ranking),
                re.compile(r"(?!x)x"),
            ),
        )
        if ranking != "relevance"
        else (raw, None)
    )
    normalized_reference = " ".join(normalize_vietnamese(product_reference).split())
    product_reference, normalized_reference = _strip_request_prefix(
        product_reference,
        normalized_reference,
    )
    focus_reference = _selected_focus_reference(raw, normalized, category_hierarchies)
    if focus_reference:
        product_reference = focus_reference
        normalized_reference = " ".join(normalize_vietnamese(product_reference).split())

    purpose_pattern = _PURPOSE_MARKER
    purpose_match = purpose_pattern.search(normalized_reference)
    purpose_is_catalog_type = bool(
        purpose_match
        and resolve_catalog_category_alias(
            purpose_match.group(1),
            category_hierarchies,
        )
    )
    if purpose_is_catalog_type:
        use_case = None
    else:
        product_reference, use_case = _strip_purpose_clause(
            product_reference,
            normalized_reference,
        )
    normalized_reference = " ".join(normalize_vietnamese(product_reference).split())
    if use_case is None and not purpose_is_catalog_type:
        direct_match = next(
            (
                match
                for match in _DIRECT_USE_CASE.finditer(normalized_reference)
                if not _span_belongs_to_catalog_category(
                    normalized_reference,
                    match,
                    category_hierarchies,
                )
            ),
            None,
        )
        if direct_match:
            use_start, use_end = _raw_span_for_normalized_span(
                product_reference,
                normalized_reference,
                direct_match.start(),
                direct_match.end(),
            )
            use_case = product_reference[use_start:use_end].strip(" ,.!?;:")
            product_reference = product_reference[:use_start].strip(" ,.!?;:")
            normalized_reference = " ".join(normalize_vietnamese(product_reference).split())
    if use_case is None and not purpose_is_catalog_type:
        implicit_need = _implicit_need_before_catalog_type(
            product_reference,
            category_hierarchies,
        )
        if implicit_need is not None:
            product_reference, use_case = implicit_need
            normalized_reference = " ".join(normalize_vietnamese(product_reference).split())
    if use_case is None and not purpose_is_catalog_type:
        personal_care_need = _personal_care_need_before_or_after_product(
            product_reference,
            normalized_reference,
            category_hierarchies,
        )
        if personal_care_need is not None:
            product_reference, use_case = personal_care_need
            normalized_reference = " ".join(normalize_vietnamese(product_reference).split())
    if use_case:
        use_case = _trim_use_case_request_closing(use_case)

    explicit_location = _EXPLICIT_NAMED_LOCATION_SUFFIX.search(product_reference)
    location_suffix = _LOCATION_REFERENCE_SUFFIX.search(normalized_reference)
    if explicit_location:
        product_reference = product_reference[: explicit_location.start()].strip(" ,.!?;:")
        normalized_reference = " ".join(normalize_vietnamese(product_reference).split())
    elif location_suffix:
        suffix_start = location_suffix.start()
        if normalized_reference.startswith("nam ", suffix_start):
            # Accent folding makes the audience word ``nam`` collide with
            # ``nằm``. Keep it when the full phrase maps to a male category;
            # otherwise treat it as the location verb.
            candidate = resolve_catalog_category(
                f"{normalized_reference[:suffix_start]} nam",
                category_hierarchies,
            )
            candidate_tokens = normalize_vietnamese(candidate.category).split() if candidate else []
            if "nam" in candidate_tokens:
                remainder_location = re.search(
                    r"\b(?:o|tai|trong|gan)\s+(?:khu|ke|day|ngan|quay|tu|tu\s+mat|tu\s+lanh)\b",
                    normalized_reference[suffix_start:],
                )
                if remainder_location:
                    suffix_start += remainder_location.start()
        raw_start, _raw_end = _raw_span_for_normalized_span(
            product_reference,
            normalized_reference,
            suffix_start,
            len(normalized_reference),
        )
        product_reference = product_reference[:raw_start].strip(" ,.!?;:")
        normalized_reference = " ".join(normalize_vietnamese(product_reference).split())

    reference_key = re.sub(r"[^\w\s]", " ", normalized_reference).strip()
    if reference_key in _GENERIC_REFERENCES or _GENERIC_FOLLOW_UP.fullmatch(reference_key):
        product_reference = ""
        normalized_reference = ""

    # When a customer corrects the requested type ("A, không phải B"), only
    # the positive part may establish the search scope. Otherwise an exact
    # catalog match on B can override A before the exclusion is applied.
    category_reference = product_reference
    normalized_category_reference = normalized_reference
    negative_category_without_positive_scope = False
    negated_category = _NEGATED_CATEGORY_CORRECTION.search(normalized_reference)
    if negated_category:
        excluded_phrase = _trim_courtesy_tail(negated_category.group(1).strip())
        positive_end = negated_category.start()
        if positive_end > 0:
            _positive_start, positive_raw_end = _raw_span_for_normalized_span(
                product_reference,
                normalized_reference,
                0,
                positive_end,
            )
            category_reference = product_reference[:positive_raw_end].strip(" ,.!?;:")
        positive_tokens = [
            token
            for token in normalized_reference[:positive_end].split()
            if token not in _PRIMARY_TYPE_FILLERS
        ]
        if not positive_tokens:
            category_reference = ""
            product_reference = ""
            negative_category_without_positive_scope = True
        else:
            product_reference = category_reference
        normalized_category_reference = " ".join(
            normalize_vietnamese(category_reference).split()
        )

    try:
        # An exact taxonomy label must win over a shorter synonym embedded in
        # it (for example, "cá nước ngọt tươi" contains the alias "nước ngọt").
        exact_match = resolve_catalog_category(category_reference, category_hierarchies)
        alias_match = resolve_catalog_category_alias(category_reference, category_hierarchies)
        exact_leaf_phrase = catalog_leaf_query_phrase(category_reference, category_hierarchies)
        exact_leaf_is_named = bool(
            exact_match is not None
            and exact_leaf_phrase
            and normalize_vietnamese(exact_leaf_phrase)
            == normalize_vietnamese(exact_match.category)
            and not category_is_parent(exact_match.category, category_hierarchies)
        )
        alias_refines_audience = bool(
            exact_match is not None
            and alias_match is not None
            and category_alias_refines_explicit_audience(
                category_reference,
                exact_match.category,
                alias_match.category,
            )
        )
        audience_match = resolve_catalog_category_with_explicit_audience(
            category_reference,
            category_hierarchies,
            alias_match or exact_match,
        )
        alias_refines_type = bool(
            alias_match is not None
            and alias_match.exact_phrase
            and not category_is_parent(alias_match.category, category_hierarchies)
            and (
                exact_match is None
                or category_is_parent(exact_match.category, category_hierarchies)
                or len(alias_match.matched_tokens) > len(exact_match.matched_tokens)
            )
        )
        category_match = audience_match or (
            alias_match
            if alias_refines_type and not alias_refines_audience
            else (
                exact_match
                if exact_match is not None
                and (alias_match is None or (exact_leaf_is_named and not alias_refines_audience))
                else alias_match or exact_match
            )
        )
        attribute_marker = _PRODUCT_ATTRIBUTE_MARKER.search(normalized_category_reference)
        # Attribute words such as ``hương`` and ``mùi`` can also be part of a
        # canonical taxonomy label (for example ``Dầu hướng dương`` or
        # ``Lăn khử mùi Nam``). Preserve a complete named leaf instead of
        # truncating it to a shorter alias before the marker.
        if attribute_marker and not exact_leaf_is_named:
            primary_phrase = normalized_category_reference[: attribute_marker.start()].strip()
            # In colloquial Vietnamese, "dầu gội đầu" is simply shampoo;
            # the second "đầu" is not part of a catalog category name.
            primary_phrase = re.sub(r"\bdau\s+goi\s+dau\b", "dau goi", primary_phrase)
            primary_phrase = re.sub(
                r"^(?:tu van|gioi thieu|goi y|de xuat|recommend|suggest)\s+",
                "",
                primary_phrase,
            )
            primary_exact = resolve_catalog_category(primary_phrase, category_hierarchies)
            primary_alias = resolve_catalog_category_alias(primary_phrase, category_hierarchies)
            primary_alias_refines_exact = bool(
                primary_alias is not None
                and primary_alias.exact_phrase
                and (
                    primary_exact is None
                    or category_is_parent(primary_exact.category, category_hierarchies)
                    or len(primary_alias.matched_tokens) > len(primary_exact.matched_tokens)
                )
            )
            primary_match = (
                primary_alias if primary_alias_refines_exact else primary_exact or primary_alias
            )
            if primary_match is not None:
                category_match = primary_match
        if category_match is None:
            # A product type can be embedded in a longer utterance with
            # audience, size, or preference words. Match the longest taxonomy
            # phrase rather than requiring the entire query to equal the label.
            leaf_phrase = catalog_leaf_query_phrase(category_reference, category_hierarchies)
            if leaf_phrase:
                exact_match = resolve_catalog_category(leaf_phrase, category_hierarchies)
                alias_match = resolve_catalog_category_alias(leaf_phrase, category_hierarchies)
                category_match = (
                    exact_match
                    if exact_match is not None
                    and (
                        alias_match is None
                        or not category_is_parent(exact_match.category, category_hierarchies)
                    )
                    else alias_match or exact_match
                )
    except (TypeError, ValueError):
        category_match = None
        alias_match = None
    product_type = category_match.category if category_match is not None else None
    alias_backed_type = bool(
        category_match is not None
        and alias_match is not None
        and category_match == alias_match
        and alias_match.exact_phrase
    )
    if (
        product_type
        and not alias_backed_type
        and not _single_word_category_is_primary(product_type, normalized_category_reference)
    ):
        product_type = None

    comparison = _intent_cue_outside_exact_category(normalized, category_match, _COMPARISON)
    price_cue = _intent_cue_outside_exact_category(normalized, category_match, _PRICE)
    reference_match = _CONVERSATION_REFERENCE.search(normalized)
    context_query = previous_query
    context_product_type = None
    context_use_case = None
    for prior_text in previous_queries[-4:]:
        prior = understand_catalog_query(
            prior_text,
            category_hierarchies,
            previous_query=context_query,
        )
        if prior.product_type:
            same_previous_type = prior.product_type == context_product_type
            if context_product_type and not same_previous_type:
                context_use_case = None
            context_product_type = prior.product_type
            if prior.use_case:
                context_use_case = prior.use_case
            if prior.conversation_reference and same_previous_type:
                context_query = prior.product_type
            elif prior.ranking_criterion != "relevance":
                # A ranking follow-up such as "which one is cheapest?" often
                # produces conversational filler as product_reference. Keep
                # the resolved catalog type as scope instead of replacing it
                # with that filler.
                context_query = prior.product_type
            else:
                context_query = prior.product_reference or prior.product_type
        elif context_query and (
            prior.ranking_criterion != "relevance"
            or prior.intent in {"alternative", "price", "comparison"}
            or "alternative" in prior.secondary_intents
        ):
            # A short ranking/price/alternative turn often omits the category.
            # Keep the latest explicit product scope across that elliptical turn.
            continue
        else:
            context_query = None
            context_product_type = None
            context_use_case = None
    if (
        product_type is None
        and context_query
        and (reference_match or ranking != "relevance" or comparison or price_cue)
    ):
        previous = understand_catalog_query(context_query, category_hierarchies)
        product_type = previous.product_type
        if product_type and not product_reference:
            product_reference = previous.product_reference
    if (
        use_case is None
        and context_use_case
        and context_product_type
        and (product_type is None or product_type == context_product_type)
    ):
        use_case = context_use_case
    negative_constraints = list(_negative_constraints(normalized, raw))
    # Retain the user's original spelling/accents for taxonomy resolution and
    # diagnostics; matching still normalizes these values before comparison.
    for index, constraint in enumerate(negative_constraints):
        normalized_constraint = normalize_vietnamese(constraint)
        spans = list(re.finditer(rf"(?<!\w){re.escape(normalized_constraint)}(?!\w)", normalized))
        if spans:
            match = spans[-1]
            start, end = _raw_span_for_normalized_span(raw, normalized, match.start(), match.end())
            negative_constraints[index] = raw[start:end].strip(" ,.!?;:")
    # A correction can exclude a catalog category or a product-title subtype
    # (e.g. ``mì ly``) even when that subtype has no taxonomy node.
    excluded_match = _NEGATED_CATEGORY_CORRECTION.search(normalized)
    if excluded_match and product_type:
        excluded_phrase = _trim_courtesy_tail(excluded_match.group(1).strip())
        excluded_start = excluded_match.start(1) + (
            len(excluded_match.group(1)) - len(excluded_match.group(1).lstrip())
        )
        excluded_end = excluded_start + len(excluded_phrase)
        excluded_type = resolve_catalog_category(excluded_phrase, category_hierarchies)
        if excluded_type is None:
            excluded_type = resolve_catalog_category_alias(excluded_phrase, category_hierarchies)
        same_type = bool(
            excluded_type
            and normalize_vietnamese(excluded_type.category) == normalize_vietnamese(product_type)
        )
        both_in_path = bool(
            excluded_type
            and not same_type
            and any(
                normalize_vietnamese(product_type) in {normalize_vietnamese(item) for item in path}
                and normalize_vietnamese(excluded_type.category)
                in {normalize_vietnamese(item) for item in path}
                for path in category_hierarchies
            )
        )
        if not both_in_path and normalize_vietnamese(excluded_phrase) != normalize_vietnamese(
            product_type
        ):
            if raw:
                raw_start, raw_end = _raw_span_for_normalized_span(
                    raw, normalized, excluded_start, excluded_end
                )
                excluded_surface = raw[raw_start:raw_end].strip(" ,.!?;:")
            else:
                excluded_surface = excluded_phrase
            if excluded_surface:
                negative_constraints.append(excluded_surface)
    negative_constraints = list(dict.fromkeys(negative_constraints))
    quantity_match = next(
        (
            match
            for match in _QUANTITY.finditer(normalized)
            if not _is_colloquial_address_not_quantity(raw, match)
        ),
        None,
    )
    quantity_value = _number_expression(quantity_match.group(1)) if quantity_match else None
    # ``không`` also appears in ordinary exclusion and budget phrases. The
    # quantity pattern can consequently match it before a unit (for example,
    # "không ... chai"). Zero is not a valid purchase quantity; ignore that
    # lexical match instead of constructing an invalid Pydantic result.
    quantity = int(quantity_value) if quantity_value is not None and quantity_value >= 1 else None
    budget_vnd = _budget_vnd(normalized)
    budget_operator = _budget_operator(normalized)
    budget_scope: BudgetScope | None = None
    budget_scope_clarification = False
    ambiguities_budget = ""
    if budget_vnd is not None:
        explicit_total_scope = bool(_BUDGET_TOTAL_SCOPE.search(normalized))
        explicit_per_item_scope = bool(_BUDGET_PER_ITEM_SCOPE.search(normalized))
        if explicit_total_scope and explicit_per_item_scope:
            budget_scope_clarification = True
            ambiguities_budget = "budget has conflicting per-item and total scopes"
        elif explicit_total_scope:
            budget_scope = "total"
            if quantity is None:
                budget_scope_clarification = True
                ambiguities_budget = "total budget needs an explicit quantity"
        elif explicit_per_item_scope:
            budget_scope = "per_item"
        elif quantity is not None and quantity > 1:
            budget_scope_clarification = True
            ambiguities_budget = "budget scope is unclear for a multi-item quantity"
        else:
            budget_scope = "per_item"
    intent: QueryIntent
    strong_recommendation = bool(
        re.search(
            r"\b(?:tu van|goi y|gioi thieu|de xuat|chon|nen mua|nen chon|"
            r"nen lay|recommend|suggest|help me choose)\b",
            normalized,
        )
    )
    if _PURCHASE_ACTION.search(normalized):
        intent = "purchase_action"
    elif comparison:
        intent = "comparison"
    elif _LOCATION.search(normalized):
        intent = "location"
    elif price_cue and (
        not strong_recommendation or _DIRECT_PRICE_QUESTION.search(normalized)
    ):
        intent = "price"
    elif reference_match and (not product_reference or _GENERIC_FOLLOW_UP.fullmatch(reference_key)):
        intent = "alternative"
    elif strong_recommendation or use_case:
        intent = "recommendation"
    elif _ATTRIBUTE_QUESTION.search(normalized):
        intent = "attribute"
    elif _is_brand_question(normalized, raw):
        intent = "brand"
    elif _VARIANT_QUESTION.search(normalized):
        intent = "variant"
    elif _RECOMMEND.search(normalized) or use_case:
        intent = "recommendation"
    elif ranking != "relevance":
        intent = "recommendation"
    elif raw:
        intent = "product_search"
    else:
        intent = "general"

    if negative_category_without_positive_scope:
        product_reference = ""
    ambiguities: list[str] = []
    if negative_category_without_positive_scope:
        ambiguities.append("the excluded product type was named without a positive request")
    if ambiguities_budget:
        ambiguities.append(ambiguities_budget)
    if ranking in {"sales_popularity", "customer_rating"}:
        ambiguities.append("requested ranking data is not represented by the product catalog")
    if negative_constraints:
        ambiguities.append("negative product attributes require a verified catalog field")
    if intent in {"product_search", "recommendation", "price", "location", "comparison"} and not (
        product_reference.strip() or product_type
    ):
        ambiguities.append("product or category is not explicit in this turn")

    clarification_needed = negative_category_without_positive_scope or not bool(
        product_type or product_reference.strip()
    )
    if budget_scope_clarification:
        clarification_needed = True
    broad_unresolved_request = bool(_BROAD_UNRESOLVED_REQUEST.search(normalized))
    if not product_type and broad_unresolved_request:
        clarification_needed = True
    if not product_type and intent == "attribute" and not price_cue:
        clarification_needed = True
    if (
        product_type
        and intent == "recommendation"
        and broad_unresolved_request
        and category_is_parent(product_type, category_hierarchies)
    ):
        clarification_needed = True
    hard_constraints = tuple(negative_constraints)
    soft_preferences = (use_case,) if use_case else ()
    conversation_reference = None
    if reference_match:
        reference_start, reference_end = _raw_span_for_normalized_span(
            raw, normalized, reference_match.start(), reference_match.end()
        )
        conversation_reference = raw[reference_start:reference_end]
    comparison_targets = _comparison_targets(raw, normalized) if comparison else ()
    known_categories = {
        normalize_category_text(category): category
        for path in category_hierarchies
        for category in path
    }
    comparison_product_types: list[str] = []
    for target in comparison_targets:
        target_category = resolve_catalog_category(target, category_hierarchies)
        if target_category is None:
            target_category = resolve_catalog_category_alias(target, category_hierarchies)
        canonical_category = (
            known_categories.get(normalize_category_text(target_category.category))
            if target_category is not None
            else None
        )
        if canonical_category and (
            target_category.exact_phrase
            or normalize_category_text(target_category.category) in known_categories
        ):
            comparison_product_types.append(canonical_category)
    primary_product_type = (
        known_categories.get(normalize_category_text(product_type)) if product_type else None
    )
    product_types = tuple(
        dict.fromkeys(
            comparison_product_types or ([primary_product_type] if primary_product_type else [])
        )
    )

    detected_intents: list[QueryIntent] = []
    if _PURCHASE_ACTION.search(normalized):
        detected_intents.append("purchase_action")
    if comparison:
        detected_intents.append("comparison")
    if _LOCATION.search(normalized):
        detected_intents.append("location")
    if (
        price_cue
        or re.search(r"\b(?:re hon|dat hon|cheaper|more expensive)\b", normalized)
        or ranking in {"price_ascending", "price_descending"}
    ):
        detected_intents.append("price")
    if strong_recommendation or _RECOMMEND.search(normalized) or use_case:
        detected_intents.append("recommendation")
    if _ATTRIBUTE_QUESTION.search(normalized):
        detected_intents.append("attribute")
    if _is_brand_question(normalized, raw):
        detected_intents.append("brand")
    if _VARIANT_QUESTION.search(normalized):
        detected_intents.append("variant")
    if reference_match:
        detected_intents.append("alternative")
    if product_type or product_reference.strip():
        detected_intents.append("product_search")
    primary_intent = intent
    secondary_intents = tuple(
        detected for detected in dict.fromkeys(detected_intents) if detected != primary_intent
    )

    return QueryUnderstandingResult(
        intent=intent,
        secondary_intents=secondary_intents,
        product_type=product_type,
        product_types=product_types,
        product_reference=product_reference.strip(" ,.!?;:"),
        use_case=use_case,
        hard_constraints=hard_constraints,
        soft_preferences=soft_preferences,
        negative_constraints=tuple(negative_constraints),
        budget_vnd=budget_vnd,
        budget_scope=budget_scope,
        budget_operator=budget_operator,
        quantity=quantity,
        comparison_scope=raw.strip(" ,.!?;:") if comparison else None,
        comparison_targets=comparison_targets,
        conversation_reference=conversation_reference,
        ranking_criterion=ranking,
        ambiguity=tuple(ambiguities),
        clarification_needed=clarification_needed,
    )
