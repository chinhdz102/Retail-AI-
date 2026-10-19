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
        r"\b(?:cho|minh|toi|em|anh|chi|nhe|nha|voi|giup|giup minh|giup toi|vao|vo|gio|hang|"
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

    if state == SessionState.PAYMENT_PENDING and any(
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

    if state == SessionState.CHECKOUT_CONFIRMATION and any(
        phrase in compact
        for phrase in ("huy checkout", "huy thanh toan", "quay lai gio hang", "cancel checkout")
    ):
        return ShoppingAction(ShoppingActionType.CANCEL_CHECKOUT)

    if any(
        phrase in compact
        for phrase in (
            "xem gio",
            "gio hang co gi",
            "trong gio co gi",
            "gio hang cua toi",
            "gio hang cua minh",
            "cart summary",
            "show my cart",
            "view cart",
        )
    ):
        return ShoppingAction(ShoppingActionType.VIEW_CART)

    add_match = re.search(
        r"\b(?:them|bo sung|cho)\s+(.+?)\s+(?:vao|vo)\s+(?:cai\s+)?gio(?:\s+hang)?\b|"
        r"\badd\s+(.+?)\s+to\s+(?:my\s+)?cart\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if add_match:
        original_add_match = re.search(
            r"\b(?:thêm|them|bổ sung|bo sung|cho|add)\s+(.+?)\s+"
            r"(?:vào|vao|vo)\s+(?:cái\s+)?(?:giỏ|gio)(?:\s+(?:hàng|hang))?\b|"
            r"\badd\s+(.+?)\s+to\s+(?:my\s+)?cart\b",
            text,
            flags=re.IGNORECASE,
        )
        raw_query = (
            next((part for part in original_add_match.groups() if part), "")
            if original_add_match
            else next((part for part in add_match.groups() if part), "")
        )
        cleaned_query = _strip_filler(raw_query)
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
        query = _strip_filler(next((part for part in remove_match.groups() if part), ""))
        return ShoppingAction(ShoppingActionType.REMOVE_ITEM, query=query)

    update_match = re.search(
        r"\b(?:doi|dat|cap nhat)\s+(.+?)\s+(?:thanh|len|ve)\s+(\d+)\b",
        normalized,
    )
    if update_match and (
        "gio" in compact or "cart" in compact or state == SessionState.CART_ACTIVE
    ):
        query = _strip_filler(update_match.group(1))
        return ShoppingAction(
            ShoppingActionType.SET_QUANTITY,
            query=query,
            quantity=int(update_match.group(2)),
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
