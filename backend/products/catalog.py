from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

DATA_TIMESTAMP = datetime(2026, 8, 31, tzinfo=UTC)
PriceOperator = Literal["LT", "LTE", "GT", "GTE", "EQ"]


def matches_price_constraint(
    price_vnd: int,
    operator: PriceOperator | None,
    value_vnd: int | None,
) -> bool:
    if operator is None or value_vnd is None:
        return True
    return {
        "LT": price_vnd < value_vnd,
        "LTE": price_vnd <= value_vnd,
        "GT": price_vnd > value_vnd,
        "GTE": price_vnd >= value_vnd,
        "EQ": price_vnd == value_vnd,
    }[operator]


class ProductSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    name: str
    category: str
    price_vnd: int = Field(ge=0)
    stock: int = Field(ge=0)
    shelf: str | None = None
    promotion: str | None = None
    spicy: bool | None = None
    stock_is_current: bool = True
    ingredients: str | None = None
    expiry_at: str | None = None
    data_timestamp: datetime = DATA_TIMESTAMP
    brand: str | None = None
    item_no: str | None = None
    barcode: str | None = None
    category_code: str | None = None
    department: str | None = None
    sub_department: str | None = None
    category_hierarchy: str | None = None
    uom: str | None = None
    image_url: str | None = None
    description: str | None = None
    seo_url: str | None = None
    list_price_vnd: int | None = None
    sale_price_vnd: int | None = None
    price_quality_status: str = "NORMAL"
    shelf_source: str = "DEMO"

    @computed_field(return_type=int)
    @property
    def sellable_stock(self) -> int:
        """Stock customers can buy, excluding products past their expiry date."""
        if not self.stock_is_current:
            return 0
        if self.stock <= 0:
            return 0
        if self.expiry_at is None:
            return self.stock
        try:
            is_expired = date.fromisoformat(self.expiry_at) < datetime.now(UTC).date()
        except ValueError:
            # An invalid expiry must fail closed for saleability.
            return 0
        return 0 if is_expired else self.stock


DEMO_PRODUCTS: tuple[ProductSnapshot, ...] = (
    ProductSnapshot(
        product_id="COKE_ZERO_330",
        name="Coca-Cola Zero 330ml",
        category="drink",
        price_vnd=13000,
        stock=16,
        spicy=False,
        uom="lon",
        shelf="A2-L2",
        ingredients="Nước có ga, chất tạo ngọt, hương cola.",
        expiry_at="2027-01-20",
    ),
    ProductSnapshot(
        product_id="MILK01",
        name="Sữa tươi ít đường 180ml",
        category="dairy",
        price_vnd=9000,
        stock=24,
        spicy=False,
        uom="hộp",
        shelf="B1-L1",
        promotion="Mua 2 hộp giảm 1.000đ",
        ingredients="Sữa tươi, đường.",
        expiry_at="2026-09-15",
    ),
    ProductSnapshot(
        product_id="SNACK01",
        name="Snack khoai tây vị tự nhiên",
        category="snack",
        price_vnd=18000,
        stock=12,
        uom="gói",
        shelf="C3-L2",
        spicy=False,
        ingredients="Khoai tây, dầu thực vật, muối.",
        expiry_at="2027-02-10",
    ),
    ProductSnapshot(
        product_id="NOODLE01",
        name="Mì ly bò hầm cay",
        category="meal",
        price_vnd=17000,
        stock=9,
        uom="ly",
        shelf="C1-L3",
        spicy=True,
        ingredients="Mì lúa mì, gia vị bò, ớt.",
        expiry_at="2027-04-02",
    ),
    ProductSnapshot(
        product_id="WATER01",
        name="Nước suối 500ml",
        category="drink",
        price_vnd=7000,
        stock=40,
        spicy=False,
        uom="chai",
        shelf="A1-L1",
        ingredients="Nước tinh khiết.",
        expiry_at="2028-06-30",
    ),
    ProductSnapshot(
        product_id="COFFEE01",
        name="Cà phê sữa pha sẵn 235ml",
        category="drink",
        price_vnd=16000,
        stock=18,
        spicy=False,
        uom="chai",
        shelf="A3-L2",
        ingredients="Nước, cà phê, sữa, đường.",
        expiry_at="2026-12-12",
    ),
)

PRODUCT_ALIASES = {
    "COKE_ZERO_330": "coca zero coca-cola 可口可乐 可口可乐零度",
    "MILK01": "milk 牛奶",
    "SNACK01": "snack bim bim bimbim khoai tay vi tu nhien chips crisps 零食",
    "NOODLE01": "noodles 面条 泡面",
    "WATER01": "water nua lit half liter half litre 0.5l 500ml 水 矿泉水",
    "COFFEE01": "coffee 咖啡",
}

DEMO_CATEGORY_ALIASES = {
    "drink": (
        "đồ uống nước uống thức uống nước giải khát drink drinks beverage beverages water soda"
    ),
    "dairy": "sữa chế phẩm từ sữa dairy milk yogurt yoghurt cheese",
    "snack": "đồ ăn vặt thức ăn nhẹ snack snacks crisps chips candy",
    "meal": "món ăn thức ăn bữa ăn meal meals food breakfast lunch dinner noodles",
}

QUERY_STOP_WORDS = frozenset(
    {
        "bao",
        "biet",
        "cho",
        "co",
        "con",
        "cua",
        "dau",
        "dung",
        "gia",
        "giup",
        "han",
        "hang",
        "hay",
        "het",
        "hien",
        "ke",
        "khong",
        "la",
        "luong",
        "muon",
        "nao",
        "nhieu",
        "nhe",
        "nhi",
        "nhiu",
        "tui",
        "hoi",
        "day",
        "vay",
        "o",
        "pham",
        "price",
        "cost",
        "how",
        "much",
        "where",
        "what",
        "which",
        "is",
        "are",
        "the",
        "on",
        "in",
        "at",
        "shelf",
        "stock",
        "available",
        "many",
        "tell",
        "me",
        "please",
        "product",
        "products",
        "can",
        "could",
        "do",
        "does",
        "have",
        "has",
        "find",
        "get",
        "i",
        "you",
        "your",
        "my",
        "this",
        "that",
        "it",
        "some",
        "any",
        "a",
        "an",
        "please",
        "recommend",
        "recommended",
        "suggest",
        "looking",
        "for",
        "under",
        "below",
        "less",
        "than",
        "up",
        "to",
        "most",
        "no",
        "more",
        "with",
        "without",
        "spicy",
        "mild",
        "budget",
        "deal",
        "deals",
        "promotion",
        "promotions",
        "offer",
        "offers",
        "discount",
        "sale",
        "expiry",
        "expiration",
        "ingredients",
        "contains",
        "inside",
        "made",
        "before",
        "by",
        "left",
        "stock",
        "in",
        "is",
        "are",
        "san",
        "so",
        "tien",
        "tim",
        "toi",
        "ton",
        "tra",
        "vi",
        "tri",
        "va",
        "voi",
        "xin",
        "y",
        "价格",
        "多少钱",
        "多少",
        "货架",
        "哪里",
        "在哪",
        "在",
        "哪一个",
        "一个",
        "库存",
        "有货",
        "数量",
        "保质期",
        "过期",
        "请",
        "告诉",
        "我",
        "吗",
        "的",
    }
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.lower())
    unaccented = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return unaccented.replace("đ", "d")


def _one_edit_apart(left: str, right: str) -> bool:
    """Return whether two terms differ by one insertion, deletion, or substitution."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    if len(left) == len(right):
        mismatches = [
            index
            for index, (left_char, right_char) in enumerate(zip(left, right, strict=True))
            if left_char != right_char
        ]
        if len(mismatches) == 1:
            return True
        return (
            len(mismatches) == 2
            and mismatches[1] == mismatches[0] + 1
            and left[mismatches[0]] == right[mismatches[1]]
            and left[mismatches[1]] == right[mismatches[0]]
        )

    shorter, longer = sorted((left, right), key=len)
    short_index = long_index = edits = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        edits += 1
        long_index += 1
        if edits > 1:
            return False
    return True


def _query_matches_product(needle_tokens: list[str], haystack: str) -> bool:
    if not needle_tokens or all(token in haystack for token in needle_tokens):
        return True

    missing = [token for token in needle_tokens if token not in haystack]
    if len(missing) != 1 or len(missing[0]) < 4:
        return False

    exact_identity_anchor = any(
        token in haystack
        and len(token) >= 3
        and not token.isdigit()
        and not re.fullmatch(r"\d+(?:ml|g|kg)", token)
        for token in needle_tokens
    )
    if not exact_identity_anchor:
        return False

    product_terms = re.findall(r"[a-z0-9]+", haystack)
    return any(len(term) >= 4 and _one_edit_apart(missing[0], term) for term in product_terms)


def search_products(
    query: str = "",
    category: str | None = None,
    max_price_vnd: int | None = None,
    spicy: bool | None = None,
    in_stock: bool = True,
    limit: int = 10,
    brand: str | None = None,
    price_operator: PriceOperator | None = None,
    price_value_vnd: int | None = None,
) -> list[ProductSnapshot]:
    normalized_query = _normalize(query).strip()
    chinese_intent_phrases = (
        "价格",
        "多少钱",
        "货架",
        "哪里",
        "在哪",
        "哪一个",
        "库存",
        "有货",
        "数量",
        "保质期",
        "过期",
    )
    for phrase in chinese_intent_phrases:
        normalized_query = normalized_query.replace(phrase, f" {phrase} ")
    query_tokens = [token.strip(".,?!:;()[]{}，。！？；：") for token in normalized_query.split()]
    meaningful_tokens = [token for token in query_tokens if token and token not in QUERY_STOP_WORDS]
    needle = " ".join(meaningful_tokens) if meaningful_tokens else normalized_query
    normalized_category = _normalize(category) if category else None
    matches: list[ProductSnapshot] = []
    for product in DEMO_PRODUCTS:
        haystack = _normalize(
            f"{product.product_id} {product.item_no or ''} {product.barcode or ''} "
            f"{product.name} {product.category} {product.category_hierarchy or ''} "
            f"{DEMO_CATEGORY_ALIASES.get(product.category, '')} "
            f"{product.uom or ''} "
            f"{product.brand or ''} {product.description or ''} {product.ingredients or ''} "
            f"{PRODUCT_ALIASES.get(product.product_id, '')}"
        )
        needle_tokens = needle.split() if needle else []
        if needle and not _query_matches_product(needle_tokens, haystack):
            continue
        if normalized_category:
            category_text = _normalize(
                f"{product.category} {product.category_hierarchy or ''} "
                f"{DEMO_CATEGORY_ALIASES.get(product.category, '')}"
            )
            if not all(token in category_text for token in normalized_category.split()):
                continue
        normalized_brand = _normalize(brand) if brand else None
        if normalized_brand and not all(
            token in _normalize(product.brand or "") for token in normalized_brand.split()
        ):
            continue
        if max_price_vnd is not None and product.price_vnd > max_price_vnd:
            continue
        if not matches_price_constraint(product.price_vnd, price_operator, price_value_vnd):
            continue
        if spicy is not None and product.spicy is not spicy:
            continue
        if in_stock and product.sellable_stock <= 0:
            continue
        matches.append(product)
        if len(matches) >= limit:
            break
    return matches


def get_product_snapshot(product_id: str) -> ProductSnapshot | None:
    normalized_id = product_id.strip().upper()
    return next((product for product in DEMO_PRODUCTS if product.product_id == normalized_id), None)
