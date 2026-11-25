"""Project explicit product-title form observations, never task suitability.

These small, overlapping observations help a semantic chooser read what a
catalog name says. They are not a product taxonomy, an ingredient declaration,
preparation instructions, or rules for accepting/rejecting a shopping task.
An absent observation means unknown, not the opposite form.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from backend.products.catalog import ProductSnapshot

# Apply literal patterns to native catalog spelling: bò/bơ, xay/xây and
# nhân/nhãn are different words. Quotes always retain the original substring.
_RULES = (
    ("binding", "spiral_binding", r"lò\s+(?:xo|co)|spiral"),
    ("form", "ground_or_minced", r"xay(?:\s+nhuyễn)?|băm(?:\s+nhỏ)?|minced|ground"),
    ("form", "sliced", r"cắt\s+lát|thái\s+lát|cắt\s+cuộn|sliced|slices"),
    ("form", "whole_piece", r"nguyên\s+(?:con|miếng)|whole\s+(?:piece|chicken|fish)"),
    ("form", "paste", r"dạng\s+sệt|paste|pate|pa\s+tê|pa-tê"),
    ("form", "powder", r"dạng\s+bột|bột|powder"),
    ("form", "gel", r"dạng\s+gel|gel"),
    ("form", "cream", r"dạng\s+kem|cream"),
    ("form", "serum", r"serum|^tinh\s+chất"),
    ("form", "spray_wording", r"dạng\s+xịt|xịt|spray"),
    ("form", "liquid", r"dạng\s+lỏng|liquid"),
    ("processing_descriptor", "frozen", r"đông\s+lạnh|frozen"),
    ("processing_descriptor", "smoked", r"xông\s+khói|smoked"),
    ("processing_descriptor", "marinated", r"ướp\s+sẵn|tẩm\s+ướp|marinated"),
    ("processing_descriptor", "instant_wording", r"ăn\s+liền|instant"),
    ("processing_descriptor", "premixed_wording", r"pha\s+sẵn|premixed|pre-mixed"),
    ("processing_descriptor", "fully_cooked_wording", r"đã\s+nấu\s+chín|chín\s+sẵn|fully\s+cooked"),
    ("role", "accessory", r"phụ\s+kiện|accessor(?:y|ies)"),
    ("role", "ingredient_wording", r"nguyên\s+liệu|ingredients?\s+for"),
    ("role", "replacement_part", r"đầu\s+thay(?:\s+thế)?|lõi\s+thay(?:\s+thế)?|replacement"),
    ("role", "refill", r"túi\s+châm|châm\s+thêm|refill"),
    ("role", "insert", r"ruột\s+gối|pillow\s+insert"),
    ("role", "container_or_cover", r"túi\s+đựng|hộp\s+đựng|vỏ\s+gối|bọc\s+gối|pillowcase"),
)
_COMPILED = tuple(
    (dimension, value, re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.I))
    for dimension, value, pattern in _RULES
)
_COMPONENT = re.compile(
    r"(?<!\w)(?:nhân|vị|hương|chứa|kèm|với|filled\s+with|flavou?r|with)(?!\w)", re.I,
)
_FILLED_HEAD = re.compile(r"\b(?:bánh|kẹo|snack|dumplings?|buns?|pastr(?:y|ies)|cookies?)\b", re.I)
_FILLED_MARKER = re.compile(r"\b(?:nhân|filled\s+with)\b", re.I)
_COSMETIC_CREAM_HEAD = re.compile(
    r"^\s*kem\s+(?:dưỡng|chống\s+nắng|cạo|tẩy|nền)\b", re.I,
)
_SAUCE_HEAD = re.compile(r"^\s*(?:xốt|sốt|nước\s+sốt|nước\s+xốt|sauce)\b", re.I)
_TOOL_HEAD = re.compile(r"^\s*(?:máy|dụng\s+cụ|bộ\s+dụng\s+cụ|grinder|blender|chopper)\b", re.I)
_COMPOSITE_HEAD = re.compile(r"^\s*(?:bánh|kẹo|snack|mì|phở|cháo|súp|soup)\b", re.I)
_FOOD_BALL = re.compile(
    r"\b(?:(?:thịt\s+)?(?:bò|gà|cá|tôm|mực|heo|hải\s+sản)\s+viên|"
    r"viên\s+(?:thịt|bò|gà|cá|tôm|hải\s+sản)|meatballs?|fish\s+balls?)\b", re.I,
)
_FLAVOUR = re.compile(r"\b(?:vị|hương|flavou?r)\s+[^,;()]{1,45}", re.I)
_NEGATIVE_PREFIX = re.compile(
    r"\b(?:(?:không|chưa)(?:\s+(?:phải|có|cần|được))?\s+|not\s+|non[-\s]*|without\s+)$",
    re.I,
)


def _positive_match(pattern: re.Pattern[str], title: str):
    return next((
        match for match in pattern.finditer(title)
        if not _NEGATIVE_PREFIX.search(title[max(0, match.start() - 40):match.start()])
    ), None)


@lru_cache(maxsize=8192)
def _observations(title: str) -> tuple[tuple[str, str, str, str], ...]:
    # NFC is only used for matching; original titles are commonly already NFC.
    # Match an NFC copy, then keep its quoted spelling; the public boundary
    # below additionally checks that a quote exists in the original title.
    native = unicodedata.normalize("NFC", title)
    component = next((
        match for match in _COMPONENT.finditer(native)
        if not (match[0].casefold() == "vị" and re.search(
            r"\bgia\s*$", native[:match.start()], re.I,
        ))
    ), None)
    component_start = component.start() if component else len(native)
    tool = bool(_TOOL_HEAD.search(native))
    sauce = bool(_SAUCE_HEAD.search(native))
    result = []
    for dimension, value, pattern in _COMPILED:
        if dimension == "binding" and not re.search(r"\b(?:sổ|vở|notebook)\b", native, re.I):
            continue
        match = _positive_match(pattern, native)
        if not match:
            continue
        subject = "mentioned_component" if match.start() > component_start else "product"
        if value == "ground_or_minced" and (tool or sauce):
            subject = "tool_function" if tool else "mentioned_component"
        if value == "ground_or_minced" and _COMPOSITE_HEAD.search(native):
            subject = "mentioned_component"
        if value == "ground_or_minced" and re.search(r"\bhòa\s+tan\b", native, re.I):
            subject = "product_line_descriptor"
        if value == "smoked" and _FILLED_HEAD.match(native) and re.search(
            r"\b(?:thịt|heo|bò|gà|cá)\b", native[:match.start()], re.I,
        ):
            subject = "mentioned_component"
        # A product named "sữa bột pha sẵn" reports both words. Powder is a
        # product-line descriptor here; do not assert the sold form is powder.
        if value == "powder" and re.search(r"\bsữa\s+bột\b.*\bpha\s+sẵn\b", native, re.I):
            subject = "product_line_descriptor"
        if value == "serum" and match.start() > 0 and not native[:match.start()].isspace():
            subject = "product_line_descriptor"
        result.append((dimension, value, match[0], subject))
    filling = _positive_match(_FILLED_MARKER, native)
    if (
        filling and _FILLED_HEAD.search(native[:filling.start()])
        and not re.search(r"\bhạnh\s*$", native[:filling.start()], re.I)
        and not re.match(r"\s+sâm\b", native[filling.end():], re.I)
        and not re.search(r"\b(?:vị|hương)\s*$", native[:filling.start()], re.I)
    ):
        result.append(("structure", "filled_product", filling[0], "product"))
    cosmetic_cream = _COSMETIC_CREAM_HEAD.search(native)
    if cosmetic_cream and not any(value == "cream" for _, value, _, _ in result):
        result.append(("form", "cream", cosmetic_cream[0].strip(), "product"))
    ball = _positive_match(_FOOD_BALL, native)
    if ball:
        subject = "mentioned_component" if ball.start() > component_start or sauce else "product"
        result.append(("form", "formed_food_pieces", ball[0], subject))
    if sauce:
        match = _SAUCE_HEAD.search(native)
        result.append(("form", "sauce", match[0].strip(), "product"))
    flavour = next((
        match for match in _FLAVOUR.finditer(native)
        if not re.search(r"\bgia\s*$", native[:match.start()], re.I)
    ), None)
    if flavour:
        result.append(("descriptor", "flavour_or_scent_wording", flavour[0], "descriptor"))
    return tuple(result[:16])


def product_form_evidence(product: ProductSnapshot) -> list[dict[str, str]]:
    """Return bounded title quotes; absence and suitability remain unknown.

    ``subject`` prevents a minced filling, a grinder's function or a powdered
    product-line name from being mistaken for the physical product being sold.
    The observations never establish efficacy, ingredients, safe consumption,
    compatibility or readiness to eat/use without the product instructions.
    """
    return [
        {
            "dimension": dimension, "value": value, "quote": quote, "subject": subject,
            "source_field": "name", "evidence_scope": "CATALOG_TITLE",
        }
        for dimension, value, quote, subject in _observations(product.name)
        if quote in product.name
    ]
