"""Resolve local Vietnamese grammar before matching shopping context."""

from __future__ import annotations

import re
import unicodedata


def product_measurement_text(text: str) -> str:
    """Leave product measurements available, masking recipient/dose context.

    The result is accentless text intended only for unit parsing. It does not
    replace customer wording or discard context before semantic planning.
    Package grammar still belongs to the caller; this only identifies explicit
    non-product roles such as a person's weight or a daily serving volume.
    """
    # Preserve the Vietnamese animal/pronoun distinction before removing
    # accents: ``chó 4kg`` is body weight; ``cho 2kg`` may be a purchase amount.
    native = unicodedata.normalize("NFC", text.casefold())
    native = re.sub(r"\bchó\b", "recipientdog", native)
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", native.replace("đ", "d"))
        if unicodedata.category(char) != "Mn"
    )
    normalized = re.sub(r"(?<=\d)\s*[-–—]\s*(?=\d)", " den ", normalized)
    number = r"\d+(?:[.,]\d+)?"
    recipient = (
        r"(?:nguoi|be|tre(?: em| con| so sinh)?|so sinh|em be|me|bo|ong|ba|"
        r"meo|recipientdog|thu cung|"
        r"baby|babies|child|children|adult|mother|father|dog|puppy|cat|kitten|pet|"
        r"toi|minh|em|anh|chi|i|he|she)"
    )
    context = (
        rf"(?:{number}(?:\s*(?:kg|gr|g))?|cua|minh|toi|em|nha|con|nang|can nang|trong luong|"
        r"khoang|tam|hon|gan|tu|den|toi|la|duoc|hien|co|thang|tuoi|"
        r"my|our|a|an|the|weighs?|weighing|aged?|months?|years?|old|about|around|to|kg|g)"
    )
    recipient_tail = re.compile(rf"\b{recipient}\s+(?:{context}\s+)*$")
    weight_tail = re.compile(rf"\bcan nang\s+(?:{context}\s+)*$")
    consumption_tail = re.compile(
        r"\b(?:(?:moi|mot)\s+(?:ngay|bua|lan)|(?:ngay|bua|lan)\s+(?:dung|uong|an)|"
        r"(?:daily|per day|per meal|each day|each meal))\s+"
        r"(?:(?:can|dung|uong|an|khoang|tam|het|toi da|toi thieu|khong qua|duoi|"
        r"drink|drinks|eat|eats|use|uses|about|around|up to)\s+)*$"
    )
    consumption_suffix = re.compile(
        r"\s*(?:(?:/|moi\s+|mot\s+)(?:ngay|bua|lan)|"
        r"(?:a|per|each)\s+(?:day|meal|dose)|daily)\b"
    )
    spans = []
    for match in re.finditer(
        rf"(?<!\d){number}\s*(?P<unit>ml|lit|l|kg|gr|g)\b", normalized,
    ):
        prefix = normalized[max(0, match.start() - 160):match.start()]
        suffix = normalized[match.end():match.end() + 50]
        body_weight = match["unit"] in {"kg", "gr", "g"} and (
            recipient_tail.search(prefix) or weight_tail.search(prefix)
        )
        if body_weight or consumption_tail.search(prefix) or consumption_suffix.match(suffix):
            spans.append(match.span())
    for start, end in reversed(spans):
        normalized = normalized[:start] + " " * (end - start) + normalized[end:]
    return normalized


def resolve_possessive_size(text: str) -> str:
    """Distinguish a small owned space from a reference to a baby.

    A space noun + owner + size adjective + degree particle is a predicate
    (``bếp em bé quá``). Keep recipient phrases such as ``cho em bé`` intact.
    This changes neither a product identity nor a customer preference.
    """
    return re.sub(
        r"\b((?:phòng|phong|căn hộ|can ho|nhà|nha|bếp|bep|góc|goc|tủ|tu|bàn|ban|kệ|ke)"
        r"\s+(?:(?:của|cua)\s+)?(?:em|mình|minh|tôi|toi|tớ|to|tui|t)\s+"
        r"(?:(?:rất|rat|hơi|hoi|khá|kha)\s+)?)(?:bé|be)"
        r"(?=\s+(?:lắm|lam|quá|qua|xíu|xiu|tí|ti|thôi|thoi)\b)",
        r"\1nhỏ",
        text,
        flags=re.IGNORECASE,
    )
