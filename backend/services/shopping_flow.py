from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from backend.services.vietnamese_intent import normalize_vietnamese
from backend.shared.contracts import SessionState


class ShoppingActionType(StrEnum):
    VIEW_CART = "VIEW_CART"
    ADD_ITEM = "ADD_ITEM"
    REMOVE_ITEM = "REMOVE_ITEM"
    SET_QUANTITY = "SET_QUANTITY"
    PREPARE_CHECKOUT = "PREPARE_CHECKOUT"
    CONFIRM_CHECKOUT = "CONFIRM_CHECKOUT"
    CHECK_PAYMENT_STATUS = "CHECK_PAYMENT_STATUS"
    CANCEL_CHECKOUT = "CANCEL_CHECKOUT"
    END_SESSION = "END_SESSION"
    RESUME_SHOPPING = "RESUME_SHOPPING"


@dataclass(frozen=True)
class ShoppingAction:
    action: ShoppingActionType
    query: str = ""
    quantity: int | None = None
    candidate_index: int | None = None
    line_items: tuple[tuple[str, int], ...] = ()


_NUMBER_WORDS = {
    "mot": 1,
    "one": 1,
    "hai": 2,
    "two": 2,
    "ba": 3,
    "three": 3,
    "bon": 4,
    "four": 4,
    "nam": 5,
    "five": 5,
    "sau": 6,
    "six": 6,
    "bay": 7,
    "seven": 7,
    "tam": 8,
    "eight": 8,
    "chin": 9,
    "nine": 9,
    "muoi": 10,
    "ten": 10,
}
_UNIT_WORDS = {
    "chai",
    "lon",
    "hop",
    "goi",
    "ly",
    "cai",
    "tui",
    "thung",
    "vien",
    "can",
    "pack",
    "can",
    "bottle",
    "box",
    "piece",
    "pieces",
    "cans",
    "packs",
}


def _strip_filler(value: str) -> str:
    value = re.sub(
        r"\b(?:cho|minh|mình|toi|tôi|tui|em|anh|chi|chị|nhe|nhé|nha|voi|với|giup|giúp|"
        r"giup minh|giup toi|vao|vào|vo|vô|gio|giỏ|hang|hàng|"
        r"cart|please|the|a|an|of|my)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(value.split()).strip(" ,.!?;:")


def _quantity_and_query(value: str) -> tuple[int, str]:
    normalized = normalize_vietnamese(value)
    tokens = normalized.split()
    quantity = 1
    quantity_index = None
    if tokens and tokens[0].isdigit():
        quantity = int(tokens[0])
        quantity_index = 0
    elif tokens and tokens[0] in _NUMBER_WORDS:
        quantity = _NUMBER_WORDS[tokens[0]]
        quantity_index = 0
    if quantity_index is not None:
        tokens.pop(quantity_index)
        if tokens and tokens[0] in _UNIT_WORDS:
            tokens.pop(0)
    # Remove unit-only filler while retaining model/product names and SKU numbers.
    tokens = [token for token in tokens if token not in {"minh", "nhe", "nha", "giup", "toi", "em"}]
    query = " ".join(tokens).strip()
    if not 1 <= quantity <= 20:
        return quantity, query
    return quantity, query


def _explicit_quantity(value: str) -> bool:
    tokens = normalize_vietnamese(value).split()
    return bool(tokens and (tokens[0].isdigit() or tokens[0] in _NUMBER_WORDS))


def _remove_negated_cart_additions(text: str) -> str:
    """Remove cart-write clauses the customer explicitly negates.

    A broad ``cho ... vào giỏ`` pattern can otherwise span across unrelated
    text and mistake phrases such as ``đừng cho vào giỏ`` for authorization.
    Stop at clause punctuation so a later, explicit positive cart action still
    works in the same message.
    """
    return re.sub(
        r"\b(?:đừng|dung|không|khong|chưa|chua|khoan|never|dont|don't|do\s+not|"
        r"no\s+need\s+to)\b(?:\s+\w+){0,3}\s+"
        r"\b(?:thêm|them|bổ\s+sung|bo\s+sung|cho|lấy|lay|mua|add|put|place)\b"
        r"[^,;.!?]{0,120}?\b(?:vào|vao|vô|vo|to|in)\s+(?:cái\s+|cai\s+)?"
        r"(?:(?:my|the)\s+)?(?:giỏ(?:\s+hàng)?|gio(?:\s+hang)?|cart)\b[^,;.!?]*",
        " ",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _implicit_purchase_action(text: str) -> ShoppingAction | None:
    """Parse clear "I'll take/buy …" requests as cart additions.

    The parser deliberately requires an explicit purchase verb and a named
    product. Questions about price, availability, or where to buy remain
    lookups, even when they contain the word "mua".
    """
    if "?" in text or "？" in text:
        return None
    normalized = normalize_vietnamese(text)
    has_plan_context = bool(
        re.search(
            r"\b(?:ke hoach|phuong an|danh sach(?: mua sam)?|nhu cau|plan|shopping list)\b",
            normalized,
        )
    )
    has_explicit_cart_target = bool(
        re.search(
            r"\b(?:vao|vo)\s+(?:cai\s+)?gio(?:\s+hang)?\b|\bto\s+(?:my\s+)?cart\b",
            normalized,
        )
    )
    if has_plan_context and not has_explicit_cart_target:
        # Editing a consultation plan or shopping list is not permission to
        # mutate the real cart.
        return None
    direct_cart_purchase = re.match(
        r"^(?:(?:minh|toi|em|anh|chi)\s+)?(?:them|bo\s+sung|cho|lay|mua)\s+"
        r"(.+?)\s+(?:bo\s+)?(?:vao|vo)\s+(?:cai\s+)?gio(?:\s+hang)?\s*[.!?]*$",
        normalized,
    )
    match = re.match(
        r"^(?:(?:minh|toi|em|anh|chi)\s+)?(?:lay|mua|them)\s+(.+?)\s*[.!?]*$",
        normalized,
    )
    if direct_cart_purchase:
        original_cart_purchase = re.match(
            r"^(?:(?:mình|minh|tôi|toi|em|anh|chị|chi)\s+)?"
            r"(?:thêm|them|bổ\s+sung|bo\s+sung|cho|lấy|lay|mua)\s+(.+?)\s+"
            r"(?:bỏ\s+|bo\s+)?(?:vào|vao|vô|vo)\s+(?:cái\s+|cai\s+)?"
            r"(?:giỏ|gio)(?:\s+(?:hàng|hang))?\s*[.!?]*$",
            text,
            flags=re.IGNORECASE,
        )
        payload = (
            original_cart_purchase.group(1).strip()
            if original_cart_purchase
            else direct_cart_purchase.group(1).strip()
        )
    elif match:
        original_purchase = re.match(
            r"^(?:(?:mình|minh|tôi|toi|em|anh|chị|chi)\s+)?"
            r"(?:lấy|lay|mua|thêm|them)\s+(.+?)\s*[.!?]*$",
            text,
            flags=re.IGNORECASE,
        )
        payload = (
            original_purchase.group(1).strip() if original_purchase else match.group(1).strip()
        )
    else:
        polite_purchase = re.match(
            r"^cho\s+(?:minh|toi|em|anh|chi)\s+(.+?)\s*[.!?]*$",
            normalized,
        )
        direct_quantity_purchase = re.match(
            r"^cho\s+((?:\d+|mot|hai|ba|bon|tu|nam|sau|bay|tam|chin|muoi)"
            r"\s+.+?)\s*[.!?]*$",
            normalized,
        )
        if polite_purchase is not None:
            payload = polite_purchase.group(1).strip()
            if not re.match(
                r"^(?:\d+|mot|hai|ba|bon|tu|nam|sau|bay|tam|chin|muoi)\s+\S+",
                payload,
            ):
                return None
        elif direct_quantity_purchase is not None:
            payload = direct_quantity_purchase.group(1).strip()
        elif direct_cart_purchase is not None:
            payload = direct_cart_purchase.group(1).strip()
        else:
            return None

    normalized_payload = normalize_vietnamese(payload)
    if re.search(
        r"\b(?:gia|bao nhieu|nhieu tien|o dau|cho nao|con hang khong|"
        r"con khong|het chua|mua gi|nen mua|co loai nao|co mon nao|"
        r"phai|bi|nham|het han|bao nhan vien|phan anh|khieu nai|doi tra|"
        r"loi|hong|complaint|issue)\b",
        normalized_payload,
    ):
        return None

    parts = re.split(
        r"\s*(?:,|;|\bvà\b|\bva\b|\bvới\b|\bvoi\b|\bcùng\b|\bcung\b|\band\b|\bplus\b)\s*",
        payload,
        flags=re.IGNORECASE,
    )
    parsed_items = []
    for part in parts:
        part = _strip_filler(part)
        quantity, query = _quantity_and_query(part)
        if not query or not 1 <= quantity <= 20:
            return None
        if query in {"hang", "mon", "san pham", "do"}:
            return None
        parsed_items.append((query, quantity))

    if len(parsed_items) > 1:
        return ShoppingAction(ShoppingActionType.ADD_ITEM, line_items=tuple(parsed_items))
    query, quantity = parsed_items[0]
    return ShoppingAction(ShoppingActionType.ADD_ITEM, query=query, quantity=quantity)


def classify_shopping_action(text: str, state: SessionState) -> ShoppingAction | None:
    normalized = normalize_vietnamese(text)
    compact = " ".join(normalized.split())

    if any(
        phrase in compact
        for phrase in (
            "ket thuc phien",
            "dung mua sam",
            "ket thuc mua sam",
            "bat dau lai phien",
            "end this session",
            "end shopping",
            "start new session",
        )
    ):
        return ShoppingAction(ShoppingActionType.END_SESSION)

    if state == SessionState.HANDOFF and compact in {
        "tiep tuc mua sam",
        "quay lai mua sam",
        "tiep tuc",
        "resume shopping",
        "continue shopping",
        "back to shopping",
    }:
        return ShoppingAction(ShoppingActionType.RESUME_SHOPPING)

    if state == SessionState.CHECKOUT_CONFIRMATION and compact in {
        "xac nhan",
        "dong y",
        "dong y thanh toan",
        "xac nhan thanh toan",
        "xac nhan dat hang",
        "tao don",
        "confirm",
        "confirm checkout",
        "yes",
    }:
        return ShoppingAction(ShoppingActionType.CONFIRM_CHECKOUT)

    if any(
        phrase in compact
        for phrase in (
            "kiem tra thanh toan",
            "thanh toan chua",
            "don hang den dau",
            "trang thai don",
            "minh chuyen tien roi",
            "toi chuyen khoan roi",
            "minh chuyen khoan xong",
            "toi chuyen khoan xong",
            "chuyen khoan xong roi",
            "minh thanh toan xong",
            "toi thanh toan xong",
            "i have paid",
            "payment status",
        )
    ):
        return ShoppingAction(ShoppingActionType.CHECK_PAYMENT_STATUS)

    # A customer may be setting a recommendation constraint and explicitly say
    # they are only browsing. Do not mistake "giỏ" in that sentence for a
    # request to open or mutate the cart.
    if "xem truoc" in compact and "chua them" in compact:
        return None

    action_text = _remove_negated_cart_additions(text)
    if action_text != text:
        normalized = normalize_vietnamese(action_text)
        compact = " ".join(normalized.split())

    if state == SessionState.CHECKOUT_CONFIRMATION and any(
        phrase in compact
        for phrase in ("huy checkout", "huy thanh toan", "quay lai gio hang", "cancel checkout")
    ):
        return ShoppingAction(ShoppingActionType.CANCEL_CHECKOUT)

    if (
        any(
            phrase in compact
            for phrase in (
                "gio hang co gi",
                "trong gio co gi",
                "gio hang cua toi",
                "gio hang cua minh",
                "cart summary",
                "show my cart",
                "view cart",
            )
        )
        or re.search(
            r"\b(?:xem|mo|show|open|view|check)\b.{0,32}\b(?:gio(?:\s+hang)?|cart)\b",
            compact,
        )
        or (state == SessionState.CART_ACTIVE and re.search(r"\btong\s+(?:tien|gio)\b", compact))
    ):
        return ShoppingAction(ShoppingActionType.VIEW_CART)

    implicit_purchase = _implicit_purchase_action(action_text)
    if implicit_purchase is not None:
        return implicit_purchase

    add_match = re.search(
        r"\b(?:them|bo sung|cho|lay|mua)\s+(.+?)\s+"
        r"(?:vao|vo|bo\s+(?:vao|vo))\s+(?:cai\s+)?gio(?:\s+hang)?\b|"
        r"\badd\s+(.+?)\s+to\s+(?:my\s+)?cart\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if add_match:
        add_source = text if action_text == text else action_text
        original_add_match = re.search(
            r"\b(?:thêm|them|bổ sung|bo sung|cho|lấy|lay|mua)\s+(.+?)\s+"
            r"(?:vào|vao|vô|vo|bỏ\s+(?:vào|vô)|bo\s+(?:vao|vo))\s+"
            r"(?:cái\s+)?(?:giỏ|gio)(?:\s+(?:hàng|hang))?\b|"
            r"\badd\s+(.+?)\s+to\s+(?:my\s+)?cart\b",
            add_source,
            flags=re.IGNORECASE,
        )
        raw_query = (
            next((part for part in original_add_match.groups() if part), "")
            if original_add_match
            else next((part for part in add_match.groups() if part), "")
        )
        cleaned_query = _strip_filler(raw_query)
        cleaned_query = re.sub(r"\bbo\s*$", "", cleaned_query).strip()
        parts = re.split(
            r"\s*(?:,|;|\bvà\b|\bva\b|\bvới\b|\bvoi\b|\bcùng\b|\bcung\b|\band\b|\bplus\b)\s*",
            cleaned_query,
            flags=re.IGNORECASE,
        )
        if len(parts) > 1:
            parsed_items = tuple(
                (query, quantity)
                for part in parts
                for quantity, query in [_quantity_and_query(part)]
                if query and 1 <= quantity <= 20
            )
            if len(parsed_items) == len(parts):
                return ShoppingAction(ShoppingActionType.ADD_ITEM, line_items=parsed_items)
        quantity, query = _quantity_and_query(cleaned_query)
        return ShoppingAction(ShoppingActionType.ADD_ITEM, query=query, quantity=quantity)

    remove_match = re.search(
        r"\b(?:bo|xoa|lay)\s+(.+?)\s+(?:khoi|ra khoi)\s+(?:cai\s+)?gio(?:\s+hang)?\b|"
        r"\bremove\s+(.+?)\s+from\s+(?:my\s+)?cart\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if remove_match:
        raw_query = _strip_filler(next((part for part in remove_match.groups() if part), ""))
        has_quantity = _explicit_quantity(raw_query)
        quantity, query = _quantity_and_query(raw_query)
        return ShoppingAction(
            ShoppingActionType.REMOVE_ITEM,
            query=query,
            quantity=quantity if has_quantity else None,
        )

    implicit_remove_match = re.search(r"\b(?:bo|xoa|lay)\s+(.+?)\s+(?:ra|di)\b", normalized)
    if state == SessionState.CART_ACTIVE and implicit_remove_match:
        raw_query = _strip_filler(implicit_remove_match.group(1))
        has_quantity = _explicit_quantity(raw_query)
        quantity, query = _quantity_and_query(raw_query)
        return ShoppingAction(
            ShoppingActionType.REMOVE_ITEM,
            query=query,
            quantity=quantity if has_quantity else None,
        )

    if state == SessionState.CART_ACTIVE:
        set_to_match = re.search(
            r"\b(?:giam|bot|tang|doi|dat|cap nhat|set|reduce|increase|change)\s+"
            r"(?:con|ve|thanh|len|xuong|to|down to|up to)?\s*"
            r"(\d+|mot|hai|ba|bon|tu|nam|sau|bay|tam|chin|muoi)"
            r"(?:\s+(?:chai|lon|hop|goi|ly|cai|bottle|box|pack|can))?"
            r"(?:\s+thoi)?\s*[.!?]*$",
            compact,
        )
        if set_to_match:
            amount = set_to_match.group(1)
            return ShoppingAction(
                ShoppingActionType.SET_QUANTITY,
                query="cai nay",
                quantity=int(amount) if amount.isdigit() else _NUMBER_WORDS[amount],
            )

    update_match = re.search(
        r"\b(?:doi|dat|cap nhat|tang|giam|set|increase|decrease)\s+(.+?)\s+"
        r"(?:thanh|len|ve|to)\s+(\d+|mot|hai|ba|bon|tu|nam|sau|bay|tam|chin|muoi)\b"
        r"(?:\s+(?:chai|lon|hop|goi|ly|cai|bottle|box|pack|can))?",
        normalized,
    )
    if update_match and (
        "gio" in compact or "cart" in compact or state == SessionState.CART_ACTIVE
    ):
        query = _strip_filler(update_match.group(1))
        amount = update_match.group(2)
        return ShoppingAction(
            ShoppingActionType.SET_QUANTITY,
            query=query,
            quantity=int(amount) if amount.isdigit() else _NUMBER_WORDS[amount],
        )

    if any(
        phrase in compact
        for phrase in (
            "thanh toan gio",
            "tinh tien gio",
            "thanh toan giup minh",
            "thanh toan giup toi",
            "checkout",
            "pay for my cart",
            "place my order",
            "create an order",
            "create order",
            "tao don",
            "dat hang giup minh",
            "dat hang giup toi",
            "minh dat hang",
            "toi dat hang",
            "em dat hang",
            "dat hang ngay",
        )
    ) or compact in {"dat hang", "order", "place an order"}:
        return ShoppingAction(ShoppingActionType.PREPARE_CHECKOUT)

    if state == SessionState.CART_ACTIVE and compact in {
        "thanh toan",
        "tinh tien",
        "pay",
        "pay now",
        "checkout now",
    }:
        return ShoppingAction(ShoppingActionType.PREPARE_CHECKOUT)

    selection = re.fullmatch(
        r"(?:loai\s+)?(?:so\s+)?"
        r"(1|2|3|4|5|dau tien|thu hai|thu ba|thu bon|thu nam|cai dau|cai thu hai)",
        compact,
    )
    if selection:
        value = selection.group(1)
        index = (
            int(value) - 1
            if value.isdigit()
            else {
                "dau tien": 0,
                "cai dau": 0,
                "thu hai": 1,
                "cai thu hai": 1,
                "thu ba": 2,
                "thu bon": 3,
                "thu nam": 4,
            }[value]
        )
        return ShoppingAction(ShoppingActionType.ADD_ITEM, candidate_index=index)

    return None
