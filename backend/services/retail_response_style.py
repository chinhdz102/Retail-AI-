from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from backend.products.catalog import ProductSnapshot
from backend.products.knowledge import EvidenceScope, EvidenceStatus, product_knowledge_facts
from backend.products.planogram import describe_planogram_location

Language = Literal["vi", "en", "zh"]

_TITLE_FUNCTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bcửa\s+trước\b", "giặt máy cửa trước"),
    (r"\bcửa\s+trên\b", "giặt máy cửa trên"),
    (r"\bgiặt\s+tay\b", "giặt tay"),
    (r"\b(?:sạch\s+gàu|chống\s+gàu|trị\s+gàu)\b", "làm sạch/chống gàu"),
    (r"\bngăn\s+(?:gãy\s+)?rụng(?:\s+tóc)?\b", "ngăn gãy rụng tóc"),
    (r"\bkhông\s+cay\s+mắt\b", "không cay mắt"),
    (r"\btắm\s+gội(?:\s+toàn\s+thân)?\b", "tắm gội toàn thân"),
    (r"\blàm\s+sạch\s+sâu\b", "làm sạch sâu"),
    (r"\bgiàu\s+canxi\b", "giàu canxi"),
    (r"\bít\s+béo\b", "ít béo"),
    (r"\bphục\s+hồi(?:\s+hư\s+tổn)?\b", "phục hồi tóc"),
    (r"\bsuôn\s+mượt\b", "dưỡng tóc suôn mượt"),
    (r"\bdưỡng\s+(?:tóc|chất|ẩm)\b", "bổ sung dưỡng chất/dưỡng ẩm"),
    (r"\btạo\s+bọt\b", "rửa mặt tạo bọt"),
    (r"\bsáng\s+da\b", "hỗ trợ sáng da"),
    (r"\bmờ\s+thâm\b", "hỗ trợ mờ thâm"),
    (r"\b(?:ngừa\s+mụn|cho\s+da\s+mụn|acne\s+care)\b", "chăm sóc da mụn"),
    (r"\bkhử\s+mùi(?:\s+tanh)?\b", "khử mùi"),
    (r"\b(?:diệt\s+khuẩn|kháng\s+khuẩn)\b", "diệt/kháng khuẩn"),
    (r"\bbảo\s+vệ\s+123\b", "bảo vệ răng miệng"),
    (r"\bngừa\s+sâu\s+răng\b", "ngừa sâu răng"),
    (r"\btrắng\s+răng\b", "làm trắng răng"),
    (r"\b(?:giảm\s+ê\s+buốt|sensitive)\b", "chăm sóc răng nhạy cảm"),
    (r"\b(?:không\s+đường|zero)\b", "không đường"),
    (r"\bít\s+đường\b", "ít đường"),
    (r"\bcó\s+đường\b", "có đường"),
    (r"\bkhông\s+calo\b", "không calo"),
    (r"\btiệt\s+trùng\b", "chế biến tiệt trùng"),
    (r"\bthanh\s+trùng\b", "chế biến thanh trùng"),
    (r"\bmen\s+sống\b", "bổ sung men sống"),
    (r"\b(?:siêu\s+sạch|sạch\s+bóng)\b", "làm sạch bề mặt"),
    (r"\bngăn\s+côn\s+trùng\b", "hỗ trợ ngăn côn trùng"),
    (r"\b(?:mềm\s+vải|làn\s+gió\s+mát|hương\s+nước\s+hoa)\b", "làm mềm và tạo hương thơm"),
    (r"\b(?:ăn\s+liền|pha\s+sẵn)\b", "chế biến tiện lợi"),
)

_TITLE_INGREDIENT_PHRASE_RE = re.compile(
    r"\b((?:nhân|chiết\s+xuất|tinh\s+chất|tinh\s+dầu)\s+"
    r"[^,.;()0-9]+?)(?=\s+(?:\d|gói|túi|chai|lon|hộp|tuýp|can|lốc|thùng|bịch|khay|hũ|lọ|"
    r"sạch|ngăn|khử|dưỡng|tạo|giặt|cửa|cho|loại)\b|$)",
    re.IGNORECASE,
)

_TITLE_INGREDIENT_KEYWORDS: tuple[str, ...] = (
    "thịt heo",
    "trứng cút",
    "trứng muối",
    "xá xíu",
    "thịt bò",
    "thịt gà",
    "tôm chua cay",
    "hải sản",
    "sườn heo",
    "bạc hà",
    "trà xanh",
    "matcha",
    "lô hội",
    "nha đam",
    "đất sét trắng",
    "đất sét",
    "canxi",
    "tơ tằm trắng",
    "vitamin c",
    "collagen",
    "baking soda",
    "hương thảo",
    "muối khoáng",
    "muối hồng",
    "than hoạt tính",
    "sả chanh",
    "chanh sả",
    "chanh",
    "quế",
    "cam bergamot",
    "gừng",
    "cacao",
    "sô cô la",
    "phô mai",
    "hạnh nhân",
    "hạt óc chó",
    "đậu nành",
    "đậu đỏ",
    "gạo lứt",
    "yến mạch",
    "hạt chia",
    "trân châu đường đen",
    "việt quất",
    "lựu đỏ",
    "hương dâu",
    "hương cam",
    "hương đào",
    "100% sữa tươi",
    "men sống",
)


_CATEGORY_ABBREV_RE: tuple[tuple[str, str], ...] = (
    (r"\bt\.thân\b", "toàn thân"),
    (r"\bt\.em\b", "trẻ em"),
    (r"\bt\.trùng\b", "tiệt trùng"),
    (r"\bđ\.lạnh\b", "đông lạnh"),
    (r"\bv\.sinh\b", "vệ sinh"),
)


def _humanize_category_group(label: str) -> str:
    cleaned = label
    for pattern, replacement in _CATEGORY_ABBREV_RE:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def extract_catalog_product_traits(product: ProductSnapshot) -> dict[str, str]:
    """Extract grounded category group, functional traits, and title-stated ingredients."""
    raw_cat = (product.category_hierarchy or product.category or "").strip()
    segments = [seg.strip() for seg in raw_cat.split(">") if seg.strip()]
    category_group = _humanize_category_group(segments[-1]) if segments else "Sản phẩm"

    name_lower = product.name.casefold()
    functions: list[str] = []
    for pattern, label in _TITLE_FUNCTION_PATTERNS:
        if re.search(pattern, name_lower, flags=re.IGNORECASE) and label not in functions:
            functions.append(label)

    ingredients: list[str] = []
    for match in _TITLE_INGREDIENT_PHRASE_RE.finditer(product.name):
        phrase = " ".join(match.group(1).split()).strip(" ,.-")
        if phrase and phrase.casefold() not in {item.casefold() for item in ingredients}:
            ingredients.append(phrase)
    joined_existing = " ".join(ingredients).casefold()
    for kw in _TITLE_INGREDIENT_KEYWORDS:
        if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", name_lower) and kw not in joined_existing:
            ingredients.append(kw)
            joined_existing += f" {kw}"

    return {
        "category_group": category_group,
        "title_functions": ", ".join(functions[:4]),
        "title_ingredients": ", ".join(ingredients[:4]),
    }


def _enriched_ingredients_text(
    product: ProductSnapshot,
) -> tuple[str | None, str | None, EvidenceScope | None]:
    """Return (ingredients_text, source, scope) when verified enrichment exists."""
    try:
        facts = product_knowledge_facts(product, ("ingredients",), source="catalog")
    except Exception:
        return None, None, None
    for fact in facts:
        if fact.status == EvidenceStatus.VERIFIED and isinstance(fact.value, str) and fact.value:
            return fact.value.strip().rstrip("."), fact.source, fact.evidence_scope
    return None, None, None


def _retailer_summary_text(product: ProductSnapshot) -> str | None:
    """Return crawled Thanh Lam Mart product summary when available (never bare title echoes)."""
    try:
        facts = product_knowledge_facts(product, ("description",), source="catalog")
    except Exception:
        return None
    for fact in facts:
        if (
            fact.evidence_scope == EvidenceScope.RETAILER_LISTING
            and (fact.evidence_version or "").startswith("thanh_lam-retailer-details-v1:")
            and isinstance(fact.value, str)
            and len(fact.value.strip()) >= 25
        ):
            summary = fact.value.strip().rstrip(".")
            if len(summary) > 320:
                summary = summary[:317].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
            return summary
    return None


def _ingredients_fact(product: ProductSnapshot, language: Language) -> str:
    if product.ingredients:
        if language == "en":
            return f"ingredients: {product.ingredients}"
        if language == "zh":
            return f"配料：{product.ingredients}"
        return f"thành phần: {product.ingredients}"
    enriched_text, enriched_source, enriched_scope = _enriched_ingredients_text(product)
    if enriched_text:
        if enriched_scope == EvidenceScope.RETAILER_LISTING:
            if language == "en":
                return f"retailer-listed ingredients ({enriched_source}): {enriched_text}"
            if language == "zh":
                return f"商品页标注配料（{enriched_source}）：{enriched_text}"
            return f"thành phần theo {enriched_source}: {enriched_text}"
        if language == "en":
            return f"manufacturer-declared ingredients ({enriched_source}): {enriched_text}"
        if language == "zh":
            return f"厂商公布的配料（{enriched_source}）：{enriched_text}"
        return f"thành phần theo công bố của {enriched_source}: {enriched_text}"
    traits = extract_catalog_product_traits(product)
    if traits["title_ingredients"]:
        if language == "en":
            return (
                f"title-noted ingredients/flavor: {traits['title_ingredients']} "
                "(full ingredient list is not in the catalog; please check the package)"
            )
        if language == "zh":
            return f"商品名称注明的配料/风味：{traits['title_ingredients']}（完整配料表请查看包装）"
        return (
            f"thành phần/hương vị ghi trên tên sản phẩm: {traits['title_ingredients']} "
            "(bảng thành phần chi tiết chưa có trong catalog, bạn xem thêm trên bao bì nhé)"
        )
    return {
        "en": "detailed ingredients are not listed in the catalog (please check the package)",
        "zh": "目录未记录详细配料表（请查看包装）",
        "vi": "catalog chưa có dữ liệu chi tiết về bảng thành phần (bạn xem trên bao bì nhé)",
    }[language]


def snapshot_disclosure_keys(
    product: ProductSnapshot,
    source: str,
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    """Return per-session keys for caveats that accompany volatile catalog facts."""
    volatile_fields = {"price_vnd", "stock", "promotion"}.intersection(fields)
    if not volatile_fields:
        return ()
    fingerprint = "|".join(
        (
            source.strip() or "unknown-source",
            product.product_id,
            product.data_timestamp.isoformat(),
        )
    )
    return tuple(f"{fingerprint}|{field}" for field in sorted(volatile_fields))


def _stock_fact(product: ProductSnapshot, language: Language) -> str:
    if not product.stock_is_current:
        recorded = product.stock
        if language == "en":
            return (
                f"the dated catalog snapshot recorded {recorded} units; "
                "current store availability is unverified"
            )
        if language == "zh":
            return f"旧版目录记录为 {recorded} 件；门店当前库存尚未核实"
        return (
            f"snapshot ghi nhận {recorded} {product.uom or 'sản phẩm'}; "
            "tồn hiện tại chưa được xác minh"
        )
    if product.sellable_stock == 0:
        if language == "en":
            return "the catalog records no sellable units"
        if language == "zh":
            return "目录记录中没有可售库存"
        return "catalog chưa ghi nhận hàng bán được"
    if language == "en":
        return f"the catalog records {product.sellable_stock} sellable units"
    if language == "zh":
        return f"目录记录有 {product.sellable_stock} 件可售"
    return (
        f"catalog ghi nhận {product.sellable_stock} {product.uom or 'sản phẩm'} "
        "có thể bán được"
    )


def _spicy_fact(product: ProductSnapshot, language: Language) -> str:
    if product.spicy is None:
        return {
            "en": "spice level is not listed in the catalog",
            "zh": "目录未记录辣度",
            "vi": "catalog chưa có dữ liệu về độ cay",
        }[language]
    if language == "en":
        return "spicy" if product.spicy else "not spicy"
    if language == "zh":
        return "辣" if product.spicy else "不辣"
    return "có vị cay" if product.spicy else "không cay"


def render_verified_snapshot(
    product: ProductSnapshot,
    language: Language,
    fields: tuple[str, ...],
    *,
    include_price_caveat: bool = True,
    include_stock_caveat: bool = True,
    include_promotion_caveat: bool = True,
) -> str:
    """Render verified catalog fields as concise customer-facing text.

    This module formats product facts only. The API keeps source and timestamp in
    structured response metadata, while callers decide whether a caveat was already
    shown in this session.
    """
    fields = tuple(fields)
    price = f"{product.price_vnd:,}".replace(",", ".")
    price_unit = f"/{product.uom}" if product.uom else ""
    data_date = product.data_timestamp.strftime("%d/%m/%Y")
    expiry_date = _format_catalog_date(product.expiry_at)

    if fields == ("data_timestamp",):
        if language == "en":
            return (
                f"This catalog record is dated {data_date}; "
                "I can't confirm it reflects today's store data, and current store "
                "information may differ."
            )
        if language == "zh":
            return (
                f"这条商品目录记录的日期是 {data_date}；我无法确认它是否反映门店的最新数据，"
                "门店当前信息可能有所不同。"
            )
        return (
            f"Mục dữ liệu này được ghi nhận ngày {data_date}; "
            "mình chưa thể xác nhận đây là dữ liệu mới nhất; thông tin tại cửa hàng có thể khác."
        )

    if fields == ("shelf",):
        if product.shelf_source == "RECOMMENDED_PLANOGRAM":
            details = describe_planogram_location(
                product.shelf,
                product_id=product.product_id,
                category_hint=product.category_hierarchy or product.category,
            )
            if details is not None:
                return details.detailed_sentence(product.name, language, include_caveat=True)
            if language == "en":
                return (
                    f"According to the suggested planogram, {product.name} is at "
                    f"{_format_shelf_location(product, language)}. "
                    "This is a proposed locator and actual store placement is unverified."
                )
            if language == "zh":
                return (
                    f"根据建议货架图，{product.name} 位于 "
                    f"{_format_shelf_location(product, language)}。"
                    "这是建议位置，门店实际摆放尚未核实。"
                )
            return (
                f"Theo sơ đồ kệ đề xuất, {product.name} ở vị trí "
                f"{_format_shelf_location(product, language)} nhé. "
                "Đây là vị trí gợi ý, chưa xác minh cách bày thực tế tại cửa hàng."
            )
        if language == "en":
            return f"{product.name} is on shelf {product.shelf}."
        if language == "zh":
            return f"{product.name} 位于货架 {product.shelf}。"
        return f"{product.name} ở kệ {product.shelf} nhé."

    if language == "en":
        facts: list[str] = []
        if "price_vnd" in fields:
            unit = f" per {product.uom}" if product.uom else ""
            facts.append(f"reference price {price} VND{unit}")
        if "stock" in fields:
            facts.append(_stock_fact(product, language))
        if "shelf" in fields:
            facts.append(_shelf_label(product, language))
        if "promotion" in fields:
            facts.append(f"catalog promotion: {product.promotion or 'none listed'}")
        if "expiry_at" in fields:
            facts.append(f"expiry date {expiry_date}")
        if "ingredients" in fields:
            facts.append(_ingredients_fact(product, language))
        if "spicy" in fields:
            facts.append(_spicy_fact(product, language))
        response = f"{product.name}: " + "; ".join(facts) + "."
        if "price_vnd" in fields and include_price_caveat:
            response += " Store prices may differ from this catalog snapshot."
        if "stock" in fields and include_stock_caveat:
            response += " Store stock may differ from this catalog snapshot."
        if "promotion" in fields and include_promotion_caveat:
            response += " Offer dates and terms are not verified; please confirm in store."
        if "data_timestamp" in fields:
            response += f" This record is dated {data_date}."
        return response

    if language == "zh":
        facts = []
        if "price_vnd" in fields:
            facts.append(f"参考价格 {price} 越南盾{price_unit}")
        if "stock" in fields:
            facts.append(_stock_fact(product, language))
        if "shelf" in fields:
            facts.append(_shelf_label(product, language))
        if "promotion" in fields:
            facts.append(f"目录促销记录：{product.promotion or '暂无记录'}")
        if "expiry_at" in fields:
            facts.append(f"保质期至 {expiry_date}")
        if "ingredients" in fields:
            facts.append(_ingredients_fact(product, language))
        if "spicy" in fields:
            facts.append(_spicy_fact(product, language))
        response = f"{product.name}：" + "；".join(facts) + "。"
        if "price_vnd" in fields and include_price_caveat:
            response += " 门店实际价格可能与目录记录不同。"
        if "stock" in fields and include_stock_caveat:
            response += " 门店实际库存可能与目录记录不同。"
        if "promotion" in fields and include_promotion_caveat:
            response += " 优惠期限和条件尚未核实，请到店确认。"
        if "data_timestamp" in fields:
            response += f" 这条记录日期为 {data_date}。"
        return response

    facts = []
    if "price_vnd" in fields:
        facts.append(f"giá tham khảo {price}đ{price_unit}")
    if "stock" in fields:
        facts.append(_stock_fact(product, language))
    if "shelf" in fields:
        facts.append(_shelf_label(product, language))
    if "promotion" in fields:
        facts.append(
            f"catalog ghi nhận ưu đãi {product.promotion}"
            if product.promotion
            else "catalog chưa có thông tin về khuyến mãi"
        )
    if "expiry_at" in fields:
        facts.append(f"hạn dùng {expiry_date}")
    if "ingredients" in fields:
        facts.append(_ingredients_fact(product, language))
    if "spicy" in fields:
        facts.append(_spicy_fact(product, language))

    if fields == ("price_vnd",):
        return f"{product.name} có giá {price}đ{price_unit} nhé."
    if fields == ("stock",):
        if not product.stock_is_current:
            stock = _stock_fact(product, language)
            response = f"{product.name}: {stock}."
        else:
            stock = (
                "chưa có hàng bán được"
                if product.sellable_stock == 0
                else f"còn {product.sellable_stock} {product.uom or 'sản phẩm'} "
                "có thể bán được theo catalog"
            )
            response = f"{product.name}: {stock}."
        if include_stock_caveat:
            response += " Tồn thực tế tại cửa hàng có thể khác."
        return response
    if fields == ("shelf",):
        if product.shelf_source == "RECOMMENDED_PLANOGRAM":
            return (
                f"Theo sơ đồ kệ đề xuất, {product.name} ở vị trí "
                f"{_format_shelf_location(product, language)} nhé. "
                "Đây là vị trí gợi ý, chưa xác minh cách bày thực tế tại cửa hàng."
            )
        return f"{product.name} ở kệ {product.shelf} nhé."
    if fields == ("promotion",):
        if product.stock_is_current and product.sellable_stock == 0:
            if language == "en":
                return (
                    f"The catalog shows no sellable stock for {product.name}, "
                    "so I can't confirm that a promotion applies."
                )
            if language == "zh":
                return f"目录记录中 {product.name} 没有可售库存，因此我无法确认优惠是否适用。"
            return (
                f"Catalog ghi nhận {product.name} hiện không có hàng bán được, "
                "nên mình chưa thể xác nhận khuyến mãi có áp dụng."
            )
        promotion = (
            f"catalog ghi nhận ưu đãi {product.promotion}"
            if product.promotion
            else "catalog chưa có thông tin về khuyến mãi"
        )
        response = f"{product.name}: {promotion}."
        if include_promotion_caveat:
            response += " Mình chưa xác minh ưu đãi còn hiệu lực tại cửa hàng."
        return response
    if fields == ("expiry_at",):
        return f"Hạn dùng của {product.name} là {expiry_date} nhé."
    if fields == ("ingredients",):
        clean_name = " ".join(product.name.split())
        if product.ingredients:
            ingredients = product.ingredients.rstrip(".")
            return f"Thành phần của {clean_name} gồm {ingredients} nhé."
        enriched_text, enriched_source, enriched_scope = _enriched_ingredients_text(product)
        if enriched_text:
            if enriched_scope == EvidenceScope.RETAILER_LISTING:
                return (
                    f"Theo {enriched_source}, với {clean_name}: {enriched_text}. "
                    "Bạn xem thêm bảng thành phần đầy đủ trên bao bì sản phẩm nhé."
                )
            return (
                f"Theo {enriched_source}, với {clean_name}: {enriched_text}. "
                "Đây là thông tin chung của dòng sản phẩm; bạn xem thêm trên bao bì nhé."
            )
        retailer_summary = _retailer_summary_text(product)
        if retailer_summary:
            return (
                f"Theo thông tin sản phẩm Thanh Lam Mart về {clean_name}: {retailer_summary}. "
                "Mình chưa có bảng thành phần tách riêng chi tiết nên bạn xem thêm trên nhãn "
                "bao bì sản phẩm nhé."
            )
        traits = extract_catalog_product_traits(product)
        if traits["title_ingredients"]:
            return (
                f"Theo tên và nhóm hàng trong danh mục, {product.name} thuộc nhóm "
                f"{traits['category_group']} và có ghi nhận thành phần/hương vị: "
                f"{traits['title_ingredients']}. Mình chưa có dữ liệu đã xác minh cho bảng "
                "thành phần chi tiết nên bạn kiểm tra giúp mình trên nhãn sản phẩm nhé."
            )
        return (
            f"Theo danh mục, {product.name} thuộc nhóm {traits['category_group']}. "
            "Mình chưa có dữ liệu đã xác minh cho bảng thành phần chi tiết nên bạn kiểm tra "
            "giúp mình trên nhãn sản phẩm nhé."
        )
    if fields == ("spicy",):
        if product.spicy is None:
            return (
                f"Catalog chưa có dữ liệu về độ cay của {product.name}; "
                "bạn xem thêm trên bao bì nhé."
            )
        return f"{product.name} {'có vị cay' if product.spicy else 'không cay'} theo catalog nhé."

    response = f"{product.name}: " + ", ".join(facts) + "."
    has_stock = "stock" in fields and include_stock_caveat
    if has_stock:
        response += " Tồn thực tế tại cửa hàng có thể khác catalog."
    if "promotion" in fields and include_promotion_caveat:
        response += " Mình chưa xác minh ưu đãi còn hiệu lực tại cửa hàng."
    if "data_timestamp" in fields:
        response += f" Dữ liệu được ghi nhận ngày {data_date}."
    return response


def _shelf_label(product: ProductSnapshot, language: Language) -> str:
    if product.shelf_source == "RECOMMENDED_PLANOGRAM":
        if language == "en":
            return (
                f"recommended planogram location {_format_shelf_location(product, language)} "
                "(not physically verified)"
            )
        if language == "zh":
            return (
                f"建议货架位置 {_format_shelf_location(product, language)}（尚未核实门店实际摆放）"
            )
        return (
            f"ở {_format_shelf_location(product, language)} "
            "(theo sơ đồ kệ đề xuất, chưa xác minh vị trí bày thực tế)"
        )
    if language == "en":
        return f"shelf {product.shelf}"
    if language == "zh":
        return f"货架 {product.shelf}"
    return f"ở kệ {product.shelf}"


def _format_shelf_location(product: ProductSnapshot, language: Language) -> str:
    location = product.shelf or ""
    if product.shelf_source != "RECOMMENDED_PLANOGRAM":
        return location
    details = describe_planogram_location(
        location,
        product_id=product.product_id,
        category_hint=product.category_hierarchy or product.category,
    )
    if details is not None:
        return details.compact_label(language)
    match = re.fullmatch(r"A(\d{2})-B(\d{2})-L(\d{2})-P(\d{2})", location)
    if match is None:
        return location
    aisle, bay, level, slot = match.groups()
    if language == "en":
        return f"{location} (aisle A{aisle}, bay B{bay}, level L{level}, slot P{slot})"
    if language == "zh":
        return f"{location}（通道 A{aisle}、货架 B{bay}、层 L{level}、位置 P{slot}）"
    return f"{location} (dãy A{aisle}, khoang B{bay}, tầng L{level}, ô P{slot})"


def _format_catalog_date(value: object) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return "chưa có dữ liệu"
    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return raw_value
