from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

DATA_TIMESTAMP = datetime(2026, 8, 31, tzinfo=UTC)


class ProductSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    name: str
    category: str
    price_vnd: int = Field(ge=0)
    stock: int = Field(ge=0)
    shelf: str
    promotion: str | None = None
    spicy: bool = False
    ingredients: str
    expiry_at: str
    data_timestamp: datetime = DATA_TIMESTAMP


DEMO_PRODUCTS: tuple[ProductSnapshot, ...] = (
    ProductSnapshot(
        product_id="COKE_ZERO_330",
        name="Coca-Cola Zero 330ml",
        category="drink",
        price_vnd=13000,
        stock=16,
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
        shelf="A3-L2",
        ingredients="Nước, cà phê, sữa, đường.",
        expiry_at="2026-12-12",
    ),
)

PRODUCT_ALIASES = {
    "COKE_ZERO_330": "coca zero coca-cola 可口可乐 可口可乐零度",
    "MILK01": "milk 牛奶",
    "SNACK01": "snack 零食",
    "NOODLE01": "noodles 面条 泡面",
    "WATER01": "water 水 矿泉水",
    "COFFEE01": "coffee 咖啡",
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
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def search_products(
    query: str = "",
    category: str | None = None,
    max_price_vnd: int | None = None,
    spicy: bool | None = None,
    in_stock: bool = True,
    limit: int = 10,
) -> list[ProductSnapshot]:
    normalized_query = _normalize(query).strip()
    chinese_intent_phrases = (
        "价格", "多少钱", "货架", "哪里", "在哪", "哪一个", "库存", "有货", "数量", "保质期", "过期"
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
            f"{product.product_id} {product.name} {product.category} {product.ingredients} "
            f"{PRODUCT_ALIASES.get(product.product_id, '')}"
        )
        if needle and not all(token in haystack for token in needle.split()):
            continue
        if normalized_category and _normalize(product.category) != normalized_category:
            continue
        if max_price_vnd is not None and product.price_vnd > max_price_vnd:
            continue
        if spicy is not None and product.spicy is not spicy:
            continue
        if in_stock and product.stock <= 0:
            continue
        matches.append(product)
        if len(matches) >= limit:
            break
    return matches


def get_product_snapshot(product_id: str) -> ProductSnapshot | None:
    normalized_id = product_id.strip().upper()
    return next((product for product in DEMO_PRODUCTS if product.product_id == normalized_id), None)
