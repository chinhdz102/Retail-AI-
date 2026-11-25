"""Physical fit guarantees cannot be established from volume or pack facts.

This is a narrow evidence boundary, not a general semantic relevance judge.
Ordinary suggestions to fold or carry an item remain possible.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.products.category_taxonomy import normalize_category_text

_CONTAINER = r"(?:ba\s*lo|tui xach|tui ao|tui quan|cop xe|ke|ngan tu|ngan ban)"
_FIT = re.compile(
    rf"\b(?:vua (?:van |gon )?(?:trong |vao |voi )?{_CONTAINER}|"
    rf"(?:bo|de|mang|cho|nhet)\b.{{0,45}}\b{_CONTAINER}\b.{{0,30}}"
    r"\b(?:khong vuong|khong cong kenh|vua van|vua gon)|"
    r"cam\b.{0,20}\bmot tay\b.{0,20}\b(?:thoai mai|de dang)|"
    r"khong (?:chiem cho|ton dien tich))\b",
)
_WARNING = re.compile(
    r"\b(?:khong (?:the |nen )?(?:khang dinh|suy ra|dam bao|mac dinh)|"
    r"chua (?:the )?(?:khang dinh|ket luan)|"
    r"dung (?:mac dinh|gia dinh)|can kiem tra|kiem tra.{0,25}xem|"
    r"khac(?: han)? voi|khong dong nghia(?: voi)?|"
    r"(?:chu )?khong phai(?: la)?|phan biet(?: giua)?)\b.{0,75}$",
)
_COMFORT = re.compile(
    r"\bkhong (?:(?:lo|so|bi|gay|lam)\s+){0,2}"
    r"(?:day bung|kho tieu|kho chiu|kich ung|di ung|kho da|cham chich)\b",
)
_SUPPLY_DURATION = re.compile(
    r"\b(?:dung|su dung)\b.{0,45}\bduoc\s+mot thoi gian(?:\s+(?:kha|rat))?\s+dai\b|"
    r"\b(?:dung|su dung)\s+(?:duoc\s+)?(?:(?:kha|rat)\s+)?lau(?:\s+(?:dai|hon))?\b|"
    r"\bdu\s+(?:dung\s+)?(?:cho\s+)?ca\s+(?:tuan|thang|nam)\b",
)
_CRITERION = re.compile(
    r"^(?:(?:ban|minh)\s+)?(?:nen|can|hay)\s+(?:xem|doc|kiem tra|doi chieu)\b|"
    r"^neu\s+(?:(?:ban|minh)\s+)?(?:can|muon)\b|"
    r"^(?:ban\s+(?:can|muon|dang tim|yeu cau|chon)|"
    r"voi\s+(?:nhu cau|yeu cau|tieu chi|muc tieu))\b",
)
_PREAMBLE_SPLIT = re.compile(
    r"\b(?:(?:minh|toi|em)\s+(?:chon|uu tien|goi y|tim|loc|de xuat|xem|giu|lay)|thi)\b",
)
_PRODUCT_SUBJECT = re.compile(
    r"\b(?:(?:loai|chai|mon|san pham|tui|goi|hop|lon|can|tuyp|nuoc|sua)\s+(?:nay|do|kia)|"
    r"thanh phan|cong thuc)\b",
)
_POST_ABSENCE_DISCLAIMER = re.compile(
    r"^\s*(?:khong dong nghia|khac(?: han)? voi|chi la\b|khong phai\b|"
    r"khong cho biet|chua cho biet)",
)
_EPISTEMIC_ABSENCE = re.compile(
    r"^(?:thong tin|so lieu|du lieu|nhan|mo ta|chi tiet|bang chung|xac nhan|"
    r"ghi chu|cam ket|ket luan|kiem dinh|chung nhan|danh gia|"
    r"chu|tu|cum tu|ky hieu|dau hieu)\b",
)
_ABSENCE = re.compile(
    r"\bkhong (?P<operator>co|chua|pha|them|bo sung)\s+"
    r"(?P<substance>[a-z0-9 ]{1,70}?)"
    r"(?=\s+(?:va|hay|hoac|nen|de|giup|vi|se|ma|thi|dau|nhe|khi)\b|[.,;:!?]|$)|"
    r"\bkhong (?P<bare>duong|ga|gas|con|ca phe|cafein|caffeine)\b",
)
_EXCLUSIVE_CONTENT = re.compile(
    r"\bchi (?:(?:co|con)\s+(?=(?:duong|chat|thanh phan)\b)|chua\s+)"
    r"[a-z0-9 ]{1,70}?"
    r"(?=\s+(?:nen|de|giup|vi|se|ma|thi|nhe|khi)\b|[.,;:!?]|$)",
)


def _assertions(text: str):
    text = re.sub(r"\{[^{}]+\}", "", text)
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if sentence.rstrip().endswith("?"):
            continue
        for clause in re.split(
            r"[,;:]|\b(?:nhưng|tuy nhiên|song)\b|(?<!chỉ )\bcòn\b", sentence, flags=re.I,
        ):
            yield normalize_category_text(clause)


def _affirmative(pattern: re.Pattern, text: str):
    for clause in _assertions(text):
        for claim in pattern.finditer(clause):
            if not _WARNING.search(clause[:claim.start()]):
                yield claim


def find_unsupported_fit_guarantee(text: str) -> str | None:
    """Return the offending fit-guarantee phrase if present, else None."""
    match = next(_affirmative(_FIT, text), None)
    return match.group(0) if match is not None else None


def unsupported_fit_guarantee(text: str) -> bool:
    """The current projection has no customer container dimensions or fit tests.

    A question or explicit warning is not a positive guarantee. A warning
    cannot authorize a later guarantee after a discourse boundary.
    Product placeholders are removed so literal titles stay data, not claims.
    """
    return find_unsupported_fit_guarantee(text) is not None


def find_unsupported_comfort_guarantee(text: str) -> str | None:
    """Return the offending bodily-comfort promise if present, else None."""
    match = next(_affirmative(_COMFORT, text), None)
    return match.group(0) if match is not None else None


def unsupported_comfort_guarantee(text: str) -> bool:
    """Absence of bodily discomfort is a product promise, not buying advice."""
    return find_unsupported_comfort_guarantee(text) is not None


def find_unsupported_supply_duration(text: str) -> str | None:
    """Return the offending supply-duration phrase if present, else None."""
    for clause in _assertions(text):
        if _CRITERION.search(clause) or re.search(r"^neu (?:ban )?muon\b", clause):
            continue
        for claim in _SUPPLY_DURATION.finditer(clause):
            if not _WARNING.search(clause[:claim.start()]):
                return claim.group(0)
    return None


def unsupported_supply_duration(text: str) -> bool:
    """A pack amount or purchase price alone cannot prove how long it lasts.

    Only a narrow set of affirmative duration phrases is covered. Hypothetical
    shopping criteria, questions and warnings are not duration guarantees.
    """
    return find_unsupported_supply_duration(text) is not None


def _strip_customer_requirement_preamble(text: str) -> str:
    """Drop a leading comma-separated customer requirement list before the main clause.

    For example, 'Với yêu cầu dưới 20 nghìn, không cà phê và hạn chế đường, mình ưu tiên...'
    restates the shopper's criteria rather than asserting product absence. Stop on any
    recommendation verb, discourse shift, or product subject ('loại này', 'chai này').
    """
    text = re.sub(r"\{[^{}]+\}", "", text)
    cleaned_sentences: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if sentence.rstrip().endswith("?"):
            continue
        clauses = sentence.split(",")
        if clauses and _CRITERION.search(normalize_category_text(clauses[0])):
            drop = 1
            for idx in range(1, len(clauses)):
                raw_clause = clauses[idx]
                norm_c = normalize_category_text(raw_clause)
                if (
                    re.search(r"[;:]|\b(?:nhung|tuy nhien|song|con)\b", norm_c)
                    or _PREAMBLE_SPLIT.search(norm_c)
                    or _PRODUCT_SUBJECT.search(norm_c)
                ):
                    break
                drop = idx + 1
            sentence = ", ".join(clauses[drop:])
        if sentence.strip():
            cleaned_sentences.append(sentence)
    return ". ".join(cleaned_sentences)


def find_unsupported_absence_claim(text: str, product_texts: Iterable[str]) -> str | None:
    """Return the offending literal ingredient-absence phrase if unsupported."""
    sources = [normalize_category_text(source) for source in product_texts]
    for clause in _assertions(_strip_customer_requirement_preamble(text)):
        if _CRITERION.search(clause):
            continue
        for claim in _EXCLUSIVE_CONTENT.finditer(clause):
            if not _WARNING.search(clause[:claim.start()]) and not any(
                re.search(re.escape(claim[0]) + r"(?=$|[.,;:!?])", source)
                for source in sources
            ):
                return claim.group(0)
        for claim in _ABSENCE.finditer(clause):
            if _WARNING.search(clause[:claim.start()]):
                continue
            if _POST_ABSENCE_DISCLAIMER.search(clause[claim.end():]):
                continue
            substance = (claim["substance"] or claim["bare"]).strip()
            if claim["operator"] == "co" and _EPISTEMIC_ABSENCE.search(substance):
                continue
            added = claim["operator"] in {"them", "bo sung"}
            if added:
                substance = substance.removesuffix(" vao").strip()
            variants = {substance}
            if substance in {"ga", "gas"}:
                variants = {"ga", "gas"}
            supported = any(
                re.search(
                    r"\bkhong "
                    + (r"(?:them|bo sung)\s+" if added else r"(?:(?:co|chua|pha)\s+)?")
                    + rf"{re.escape(value)}\b",
                    source,
                )
                for source in sources for value in variants
            )
            if not supported:
                return claim.group(0)
    return None


def unsupported_absence_claim(text: str, product_texts: Iterable[str]) -> bool:
    """Require explicit product wording for literal ingredient-absence claims.

    This narrow literal contract does not infer absence from an ingredient list,
    category, the customer's exclusions, or omission from a product's title.
    No-added and absence are distinct operators. An exclusive composition claim
    also implies absence of other ingredients and needs its own explicit wording.
    It is not a general semantic entailment model. Evidence from a different
    selected product must not be passed in to authorize this product's claim.
    """
    return find_unsupported_absence_claim(text, product_texts) is not None
