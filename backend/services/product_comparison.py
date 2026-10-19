from __future__ import annotations

import re
from typing import Literal

from backend.services.vietnamese_intent import normalize_vietnamese

ComparisonMetric = Literal["price", "stock"]
UnsupportedComparisonDimension = Literal["taste", "popularity"]

_CONNECTOR = re.compile(r"\s+(voi|va|vs|versus|with|and)\s+")
_COMPARATIVE_CUE = (
    r"(?:re hon|it tien hon|gia thap hon|tot hon|ngon hon|con nhieu hon|"
    r"con nhieu hang hon|nhieu hang hon|co nhieu hang hon|ban chay hon|"
    r"cheaper|better|more available|more stock|lower price|cost less|less expensive)"
)
_FOLLOWUP_TAIL = re.compile(
    rf"\s+(?:(?:cai nao|ben nao|mon nao)\s+(?:(?:la|thi)\s+)?{_COMPARATIVE_CUE}\b.*|"
    r"which one (?:is )?(?:cheaper|better|more available|has more stock)|"
    r"which is (?:cheaper|better|more available)|what is (?:cheaper|better))\b.*$"
)
_COMPARISON_FOLLOWUP = re.compile(
    rf"^(?:(?:cai nao|ben nao|mon nao)\s+(?:(?:la|thi)\s+)?{_COMPARATIVE_CUE}|"
    r"which one (?:is )?(?:cheaper|better|more available|has more stock)|"
    r"which is (?:cheaper|better|more available)|what is (?:cheaper|better))\b"
)
_POLITE_TAIL = re.compile(r"\s+(?:giup minh|giup toi|please|nhe|nha|thanks|thank you)$")


def is_comparison_request(text: str) -> bool:
    normalized = " ".join(normalize_vietnamese(text).split())
    return bool(
        re.search(r"\bso sanh\b|\bcompare\b|\bversus\b|\bvs\b", normalized)
        or _FOLLOWUP_TAIL.search(normalized)
        or _COMPARISON_FOLLOWUP.search(normalized)
    )


def parse_comparison_queries(text: str) -> tuple[str, str] | None:
    """Extract two explicitly named products from Vietnamese or English comparison text."""
    normalized = " ".join(normalize_vietnamese(text).split())
    prefix = re.match(r"^(?:so sanh(?: giua)?|compare(?: between)?)\s+", normalized)
    has_followup_tail = bool(_FOLLOWUP_TAIL.search(normalized))
    body = normalized[prefix.end():] if prefix else normalized
    connector = _CONNECTOR.search(body)
    if connector is None:
        return None
    # Without an explicit compare/versus cue, only parse an utterance that asks
    # which of two named products matches a comparative criterion.
    if not prefix and connector.group(1) not in {"vs", "versus"} and not has_followup_tail:
        return None

    left = body[:connector.start()].strip()
    right = body[connector.end():].strip()
    right = _FOLLOWUP_TAIL.sub("", right).strip()
    right = _POLITE_TAIL.sub("", right).strip()
    if left.startswith("between "):
        left = left.removeprefix("between ").strip()
    left = _POLITE_TAIL.sub("", left).strip()
    if not left or not right:
        return None
    return left, right


def comparison_metric(text: str) -> ComparisonMetric | None:
    normalized = " ".join(normalize_vietnamese(text).split())
    if re.search(
        r"\b(?:so sanh (?:theo )?gia|compare (?:by )?price|compare prices?|"
        r"re hon|it tien hon|gia thap hon|cheaper|cheapest|lower price|"
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
