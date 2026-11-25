"""Match explicit title descriptors without turning an absent word into a fact.

Aliases cover spelling and equivalent physical forms, not product suitability,
efficacy or safety. They never add attributes to an otherwise silent title.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

_ALIASES = (
    ("lò xo", "lò co", "spiral"),
    ("cắt lát", "thái lát", "sliced"),
    ("xay", "băm", "minced", "ground"),
    ("tẩm ướp", "ướp sẵn", "marinated"),
    ("túi châm", "túi refill", "refill"),
)
_NEGATIVE = re.compile(r"\b(?:khong|chua)(?:\s+(?:phai|co|can|duoc))?\s+$")


def _fold(text: str) -> str:
    # NFC Vietnamese letters retain their character positions after folding.
    return "".join(c for c in unicodedata.normalize("NFD", text.replace("đ", "d"))
                   if unicodedata.category(c) != "Mn")


def _descriptor_mentions(
    title: str, descriptor: str, *, context: str = "",
) -> Iterator[tuple[str, str, re.Match]]:
    native = unicodedata.normalize("NFC", title.casefold())
    title = _fold(native)
    descriptor = unicodedata.normalize("NFC", descriptor.casefold()).strip()
    if not descriptor:
        return
    variants = {descriptor}
    for group in _ALIASES:
        if group[0] == "lò xo" and not re.search(
            r"\b(?:so|vo|notebook)\b", title + " " + _fold(context.casefold()),
        ):
            continue
        for alias in group:
            pattern = rf"(?<!\w){re.escape(_fold(alias))}(?!\w)"
            match = re.search(pattern, _fold(descriptor))
            if match:
                variants.update(descriptor[:match.start()] + other + descriptor[match.end():]
                                for other in group)
    for variant in variants:
        for match in re.finditer(rf"(?<!\w){re.escape(_fold(variant))}(?!\w)", title):
            quote = native[match.start():match.end()]
            conflicting_accents = any(
                a != b and _fold(a) != a and _fold(b) != b
                for a, b in zip(variant, quote, strict=True)
            )
            if not conflicting_accents:
                yield native, title, match


def title_has_descriptor(title: str, descriptor: str) -> bool:
    for native, folded, match in _descriptor_mentions(title, descriptor):
        prefix_folded = folded[max(0, match.start() - 35):match.start()]
        prefix_native = native[max(0, match.start() - 35):match.start()]
        # Vietnamese "chua" (sour, e.g. "tôm chua cay") folds to the same ASCII
        # as "chưa" (not yet). Never treat culinary "chua" as a negation.
        is_sour_chua = bool(re.search(r"\bchua\s+$", prefix_native))
        if not (_NEGATIVE.search(prefix_folded) and not is_sour_chua):
            return True
    return False


def advice_mentions_excluded(text: str, descriptor: str, *, context: str = "") -> bool:
    """Catch explicit excluded forms in prose, while preserving scoped warnings.

    This is the same literal/physical-alias boundary used for titles, not a
    semantic entailment judge. An earlier negative clause cannot authorize a
    later affirmative mention. Only a nearby negative buying instruction or
    a postposed rejection masks a mention; double negations remain visible.
    """
    # Keep 'dùng' (use) distinct from 'đừng' (do not) before accent folding.
    text = re.sub(r"\b(?:đừng|chớ)\b", "không chọn", text, flags=re.I)
    category_words = _fold(context.casefold()).split()[:2]
    classifier = "loai|dang|mon|san pham|dong|hop|tui|chai|goi|lon|nhom"
    if category_words:
        classifier += "|" + re.escape(" ".join(category_words)) + "|" + re.escape(category_words[0])
    for _, folded, match in _descriptor_mentions(text, descriptor, context=context):
        prefix = re.split(
            r"[,;.!?\n]|\b(?:nhung|tuy nhien|song|hoac|hay)\b",
            folded[:match.start()],
        )[-1][-90:]
        suffix = re.split(r"[,;.!?\n]", folded[match.end():])[0][:60]
        if re.search(
            r"\b(?:khong|chua)(?:\s+(?:nen|duoc|can|chon|lay|mua|dung|an|uong|them|co|phai|"
            r"ghi|noi|neu|de cap|ro|do|vi|muc)){0,4}"
            rf"(?:\s+(?:{classifier}))?(?:\s+co)?\s+$|"
            r"\b(?:tranh|ne|kieng|loai bo|loai tru|bo qua|han che|it|giam|"
            r"khac voi|chuyen tu|doi tu|"
            r"thay(?:\s+(?:cho|vi|the(?:\s+cho)?))?)"
            r"(?:\s+(?:het|cac|nhung|chon|lay|mua|dung|an|uong|loai|dang|mon|dong|"
            r"hop|tui|chai|goi|lon|sua|vi|huong|chu|tu|co)){0,5}\s+$|"
            r"\b(?:minh|toi|em|da|se|nen)\s+loai"
            r"(?:\s+(?:bo|tru|het|cac|nhung|loai|dang|mon|dong|san pham|co|chua)){0,4}\s+$|"
            r"\b(?:ban|nha|phong|khach)\s+da\s+co(?:\s+(?:san|cac|nhung))?\s+$",
            prefix,
        ) and not re.search(
            r"\bkhong\s+(?:tranh|ne|kieng|loai bo|loai tru|bo qua|han che|it|giam|"
            r"thay|chuyen|doi)\b",
            prefix,
        ):
            continue
        if (
            re.search(r"\b(?:minh|toi|em|da|se|nen|thoi)\s+bo\s+$", prefix)
            and not re.match(r"^\s+vao\b", suffix)
        ):
            continue
        if (
            re.search(
                r"\b(?:khong|chua)\s+(?:noi|ghi|neu|biet|xac nhan)"
                r"(?:\s+ro)?(?:\s+(?:la\s+)?co)?\s+$",
                prefix,
            )
            and re.match(r"^\s+hay\s+khong\b", suffix)
        ):
            continue
        if re.match(
            r"^\s+(?:thi\s+)?(?:bo(?:\s+qua)?|loai bo|khong (?:chon|lay|mua|dung))"
            r"(?:\s+(?:nhe|nha|di))?\s*$",
            suffix,
        ):
            continue
        return True
    return False
