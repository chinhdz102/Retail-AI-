from __future__ import annotations

import re
from typing import Literal

from backend.services.query_understanding import understand_catalog_query
from backend.services.vietnamese_intent import normalize_vietnamese

ComparisonMetric = Literal["price", "stock"]
UnsupportedComparisonDimension = Literal["taste", "popularity"]

_CONNECTOR = re.compile(r"\s+(voi|va|vs|versus|with|and)\s+")
_COMPARATIVE_CUE = (
    r"(?:re hon|it tien hon|gia thap hon|tot hon|ngon hon|con nhieu hon|"
    r"con nhieu hang hon|nhieu hang hon|co nhieu hang hon|ban chay hon|"
    r"cheaper|better|more available|more stock|lower price|cost less|less expensive)"
)
_COMPARATIVE_SUBJECT = r"(?:cai nao|ben nao|mon nao|chai nao|loai nao|hang nao)"
_FOLLOWUP_TAIL = re.compile(
    rf"\s+(?:(?:xem\s+)?{_COMPARATIVE_SUBJECT}\s+(?:(?:la|thi)\s+)?{_COMPARATIVE_CUE}\b.*|"
    r"which one (?:is )?(?:cheaper|better|more available|has more stock)|"
    r"which is (?:cheaper|better|more available)|what is (?:cheaper|better))\b.*$"
)
_COMPARISON_FOLLOWUP = re.compile(
    rf"^(?:(?:xem\s+)?{_COMPARATIVE_SUBJECT}\s+(?:(?:la|thi)\s+)?{_COMPARATIVE_CUE}|"
    r"chenh nhau(?:\s+dung)?(?:\s+\d[\d.,\s]*(?:k|nghin|ngan|dong|d)?)?(?:\s+ha)?|"
    r"which one (?:is )?(?:cheaper|better|more available|has more stock)|"
    r"which is (?:cheaper|better|more available)|what is (?:cheaper|better))\b"
)
_POLITE_TAIL = re.compile(r"\s+(?:giup minh|giup toi|please|nhe|nha|thanks|thank you)$")
_DIFFERENCE_TAIL = re.compile(
    r"\s+(?:khac gi(?: nhau)?|khac nhau(?: nhu the nao)?)\b.*$"
)


def is_comparison_request(text: str) -> bool:
    normalized = " ".join(normalize_vietnamese(text).split())
    return bool(
        parse_comparison_queries(text) is not None
        or re.search(r"\bso sanh\b|\bcompare\b|\bversus\b|\bvs\b", normalized)
        or _FOLLOWUP_TAIL.search(normalized)
        or _COMPARISON_FOLLOWUP.search(normalized)
        or (
            _CONNECTOR.search(normalized)
            and re.search(r"\bkhac gi(?: nhau)?\b|\bkhac nhau\b", normalized)
        )
    )


def _parse_comparison_queries_from_patterns(text: str) -> tuple[str, str] | None:
    """Extract operands from known comparison forms before semantic fallback."""
    normalized = " ".join(normalize_vietnamese(text).split())
    prefix = re.match(r"^(?:so(?: sanh(?: giua)?)?|compare(?: between)?)\s+", normalized)
    has_followup_tail = bool(_FOLLOWUP_TAIL.search(normalized))
    body = normalized[prefix.end() :] if prefix else normalized
    connector = _CONNECTOR.search(body)
    if connector is None:
        return None
    # Without an explicit compare/versus cue, only parse an utterance that asks
    # which of two named products matches a comparative criterion.
    difference_question = bool(
        re.search(r"\bkhac gi(?: nhau)?\b|\bkhac nhau\b", normalized)
    )
    if (
        not prefix
        and connector.group(1) not in {"vs", "versus"}
        and not has_followup_tail
        and not difference_question
    ):
        return None

    left = body[: connector.start()].strip()
    right = body[connector.end() :].strip()
    right = _FOLLOWUP_TAIL.sub("", right).strip()
    right = _DIFFERENCE_TAIL.sub("", right).strip()
    right = _POLITE_TAIL.sub("", right).strip()
    if left.startswith("between "):
        left = left.removeprefix("between ").strip()
    left = _POLITE_TAIL.sub("", left).strip()
    if not left or not right:
        return None
    if re.match(r"^(?:chai|lon|hop|goi|tui)\b", right):
        shared_product = re.sub(
            r"\b\d+(?:[.,]\d+)?(?:\s*(?:ml|l|lit|litro|kg|g))?\b",
            " ",
            left,
        )
        shared_product = re.sub(r"\b(?:chai|lon|hop|goi|tui)\b", " ", shared_product)
        shared_product = " ".join(shared_product.split())
        if shared_product:
            right = f"{shared_product} {right}"
    return left, right


def parse_comparison_queries(text: str) -> tuple[str, str] | None:
    """Extract comparison operands, falling back to the shared query IR.

    The local comparison grammar handles price/stock wording. The shared
    understanding parser covers natural difference questions such as
    ``A với B có gì khác theo nhãn?`` without maintaining a second copy of
    every comparison phrase and operand boundary.
    """
    pattern_operands = _parse_comparison_queries_from_patterns(text)
    if pattern_operands is not None:
        return pattern_operands

    understanding = understand_catalog_query(text)
    if understanding.intent != "comparison" or len(understanding.comparison_targets) != 2:
        return None
    targets = tuple(
        " ".join(target.split()).strip(" ,.!?;:")
        for target in understanding.comparison_targets
    )
    if any(not target for target in targets):
        return None
    return targets[0], targets[1]


def comparison_metric(text: str) -> ComparisonMetric | None:
    normalized = " ".join(normalize_vietnamese(text).split())
    if re.search(
        r"\b(?:so sanh (?:theo )?gia|compare (?:by )?price|compare prices?|"
        r"re hon|it tien hon|gia thap hon|chenh nhau|price difference|difference between|"
        r"cheaper|cheapest|lower price|"
        r"cost less|less expensive)\b",
        normalized,
    ):
        return "price"
    if re.search(
        r"\b(?:so sanh (?:theo )?(?:ton kho|hang ton|luong ton)|"
        r"compare (?:by )?(?:stock|inventory|availability)|inventory comparison|"
        r"ton nhieu hon|con nhieu hon|con nhieu hang hon|nhieu hang hon|more stock|"
        r"more available|more in stock)\b",
        normalized,
    ):
        return "stock"
    return None


def unsupported_comparison_dimension(text: str) -> UnsupportedComparisonDimension | None:
    """Identify comparison requests that need evidence unavailable in the catalog."""
    normalized = " ".join(normalize_vietnamese(text).split())
    if re.search(
        r"\b(?:ngon hon|ngon nhat|vi ngon|huong vi|taste better|tastes better|tastier|"
        r"best tasting)\b",
        normalized,
    ):
        return "taste"
    if re.search(
        r"\b(?:ban chay hon|ban chay nhat|pho bien hon|pho bien nhat|"
        r"best selling|bestseller|top seller|more popular|most popular)\b",
        normalized,
    ):
        return "popularity"
    return None
