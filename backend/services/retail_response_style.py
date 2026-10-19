from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from backend.products.catalog import ProductSnapshot

Language = Literal["vi", "en", "zh"]


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
    data_date = product.data_timestamp.strftime("%d/%m/%Y")
    expiry_date = _format_catalog_date(product.expiry_at)

    if fields == ("data_timestamp",):
        if language == "en":
            return (
                f"This catalog record is dated {data_date}; "
                "I can't confirm it reflects today's store data."
            )
        if language == "zh":
            return f"这条商品目录记录的日期是 {data_date}；我无法确认它是否反映门店的最新数据。"
        return (
            f"Mục dữ liệu này được ghi nhận ngày {data_date}; "
            "mình chưa thể xác nhận đây là dữ liệu mới nhất tại cửa hàng."
        )

    if language == "en":
        facts: list[str] = []
        if "price_vnd" in fields:
            facts.append(f"reference price {price} VND")
        if "stock" in fields:
            facts.append(
                "the catalog records no sellable units"
                if product.sellable_stock == 0
                else f"the catalog records {product.sellable_stock} sellable units"
            )
        if "shelf" in fields:
            facts.append(_shelf_label(product, language))
        if "promotion" in fields:
            facts.append(f"catalog promotion: {product.promotion or 'none listed'}")
        if "expiry_at" in fields:
            facts.append(f"expiry date {expiry_date}")
        if "ingredients" in fields:
            facts.append(f"ingredients: {product.ingredients}")
        if "spicy" in fields:
            facts.append("spicy" if product.spicy else "not spicy")
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
            facts.append(f"参考价格 {price} 越南盾")
        if "stock" in fields:
            facts.append(
                "目录记录中没有可售库存"
                if product.sellable_stock == 0
                else f"目录记录有 {product.sellable_stock} 件可售"
            )
        if "shelf" in fields:
            facts.append(_shelf_label(product, language))
        if "promotion" in fields:
            facts.append(f"目录促销记录：{product.promotion or '暂无记录'}")
        if "expiry_at" in fields:
            facts.append(f"保质期至 {expiry_date}")
        if "ingredients" in fields:
            facts.append(f"配料：{product.ingredients}")
        if "spicy" in fields:
            facts.append("辣" if product.spicy else "不辣")
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
        facts.append(f"giá tham khảo {price}đ")
    if "stock" in fields:
        facts.append(
            "catalog chưa ghi nhận hàng bán được"
            if product.sellable_stock == 0
            else f"catalog ghi nhận {product.sellable_stock} {product.uom or 'sản phẩm'} có thể bán"
        )
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
        facts.append(f"thành phần: {product.ingredients}")
    if "spicy" in fields:
        facts.append("có vị cay" if product.spicy else "không cay")

    if fields == ("price_vnd",):
        response = f"{product.name} có giá tham khảo {price}đ nhé."
        if include_price_caveat:
            response += " Giá tại cửa hàng có thể khác."
        return response
    if fields == ("stock",):
        stock = (
            "chưa có hàng bán được"
            if product.sellable_stock == 0
            else f"còn {product.sellable_stock} {product.uom or 'sản phẩm'} có thể bán được"
        )
        response = f"{product.name}: {stock} theo catalog."
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
        if product.sellable_stock == 0:
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
        ingredients = (product.ingredients or "").rstrip(".")
        return f"Thành phần của {product.name} gồm {ingredients} nhé."
    if fields == ("spicy",):
        return f"{product.name} {'có vị cay' if product.spicy else 'không cay'} nhé."

    response = f"{product.name}: " + ", ".join(facts) + "."
    has_price = "price_vnd" in fields and include_price_caveat
    has_stock = "stock" in fields and include_stock_caveat
    if has_price and has_stock:
        response += " Giá và tồn thực tế tại cửa hàng có thể khác catalog."
    elif has_price:
        response += " Giá tại cửa hàng có thể khác catalog."
    elif has_stock:
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
            "ở vị trí kệ đề xuất "
            f"{_format_shelf_location(product, language)} "
            "(chưa xác minh vị trí bày thực tế)"
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
