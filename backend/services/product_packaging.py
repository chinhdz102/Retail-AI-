"""Read the sale unit separately from units mentioned inside a multipack.

Literal packaging observations do not establish portability, ingredients,
product suitability, or readiness to use.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from backend.products.catalog import ProductSnapshot

SaleUnit = Literal[
    "chai", "lon", "hộp", "gói", "túi", "lốc", "thùng", "vỉ", "khay",
    "cuộn", "lọ", "hũ", "tuýp", "ly", "can", "bộ", "cái", "quyển", "đôi",
]
_UNITS = (
    "chai", "lon", "hộp", "gói", "túi", "lốc", "thùng", "vỉ", "khay",
    "cuộn", "lọ", "hũ", "tuýp", "ly", "can", "bộ", "cái", "quyển", "đôi",
)


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
                   if unicodedata.category(c) != "Mn")


_CANONICAL = {_fold(unit): unit for unit in _UNITS}
_PATTERN = "|".join(re.escape(unit) for unit in _CANONICAL)
_OUTER_PACK = re.compile(
    rf"^\s*(?P<unit>loc|thung|vi|bo|hop|goi|tui)\s*"
    rf"(?P<count>\d+)\s*(?P<inner>{_PATTERN})(?!\w)",
)
_TITLE_UNIT = re.compile(
    rf"(?<!\w)(?P<unit>{_PATTERN})\s*\d+(?:[.,]\d+)?\s*(?:ml|lit|l|kg|gam|gr|g)(?!\w)",
)


def sale_unit_observation(product: ProductSnapshot) -> dict:
    """An explicit outer pack overrides abbreviated imported uom wording.

    The snapshot often calls 'Lốc 6 chai ...' uom 'Gói 6'. An inner bottle
    mention cannot authorize buying one bottle for the whole pack's price.
    Unknown units stay unknown; volume and category cannot establish them.
    """
    native = unicodedata.normalize("NFC", product.name)
    outer = _OUTER_PACK.search(_fold(native))
    if outer:
        return {"unit": _CANONICAL[outer["unit"]], "quote": native[:outer.end()].strip(),
                "source_field": "name", "outer_count": int(outer["count"])}
    uom = unicodedata.normalize("NFC", product.uom or "").strip()
    match = re.fullmatch(rf"(?P<unit>{_PATTERN})(?:\s+\d+)?", _fold(uom))
    title = _TITLE_UNIT.search(_fold(native))
    # Imported 'Gói' is also the administrative unit for a refill 'túi 2kg'.
    # Preserve the explicit pouch wording instead of rejecting a real pouch.
    # Do not generalize this to bottles/boxes or units inside a multipack.
    if match and _CANONICAL[match["unit"]] == "gói" and title and title["unit"] == "tui":
        return {"unit": "túi", "quote": native[title.start():title.end()], "source_field": "name"}
    if match:
        return {"unit": _CANONICAL[match["unit"]], "quote": uom, "source_field": "uom"}
    if _fold(uom) == "loc, lo, lo":
        return {"unit": "lốc", "quote": uom, "source_field": "uom"}
    if title:
        return {"unit": _CANONICAL[title["unit"]],
                "quote": native[title.start():title.end()], "source_field": "name"}
    return {}


def sale_unit_matches(product: ProductSnapshot, requested: str | None) -> bool:
    """Reject known mismatches; an absent observation is not positive evidence."""
    if not requested:
        return True
    observed = sale_unit_observation(product)
    return not observed or observed["unit"] == requested


def mentions_sale_unit(text: str, unit: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(_fold(unit))}(?!\w)", _fold(text)))


def _customer_text(text: str) -> str:
    return _fold(re.sub(r"\b(?:đừng|chớ)\b", "không chọn", text, flags=re.I))


def _purchase_mention(text: str, start: int) -> bool:
    prefix = re.split(r"[,;.!?\n]|\b(?:nhung|ma|va|roi)\b", text[:start])[-1]
    if re.search(r"\b(?:khong|chua|tranh)\b(?:\s+\w+){0,5}\s*$", prefix):
        return False
    # A consumed or already-owned quantity does not specify what to buy.
    descriptions = list(re.finditer(r"\b(?:uong|an|dung|tieu thu|con|co|het|da mua|tung mua)\b",
                                   prefix))
    requests = list(re.finditer(r"\b(?:mua|chon|lay|can|tim|doi|sang)\b", prefix))
    return not descriptions or bool(requests and requests[-1].start() > descriptions[-1].start())


def requests_sale_unit(text: str, unit: str) -> bool:
    """Literal customer support for a planner's role-specific container choice."""
    text = _customer_text(text)
    return any(_purchase_mention(text, match.start()) for match in re.finditer(
        rf"(?<!\w){re.escape(_fold(unit))}(?!\w)", text,
    ))


def explicit_sale_unit(text: str) -> str | None:
    """Recognize a positive quantity/container phrase, never a category-unit rule.

    Different requested units are left to the planner to assign to roles.
    A negated quantity or a bare unit mention does not impose a constraint.
    """
    text = _customer_text(text)
    units = set()
    for match in re.finditer(
        rf"\b(?:(?:mot|1|hai|2|ba|3|vai|may)\s+|dang\s+)(?P<unit>{_PATTERN})(?!\w)",
        text,
    ):
        if not _purchase_mention(text, match.start()):
            continue
        units.add(_CANONICAL[match["unit"]])
    return next(iter(units)) if len(units) == 1 else None
