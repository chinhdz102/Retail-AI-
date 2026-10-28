from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.products.catalog import ProductSnapshot


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class EvidenceScope(StrEnum):
    """What a fact's evidence actually establishes."""

    SOURCE_RECORD = "SOURCE_RECORD"
    CURRENT_OPERATIONAL = "CURRENT_OPERATIONAL"
    PHYSICAL_STORE = "PHYSICAL_STORE"
    RETAILER_LISTING = "RETAILER_LISTING"
    RECOMMENDED_PLANOGRAM = "RECOMMENDED_PLANOGRAM"
    UNKNOWN = "UNKNOWN"


class ProductKnowledgeFact(BaseModel):
    """One product attribute with source, scope, and time/version provenance.

    ``observed_at`` is when the source record was captured. ``verified_at`` is
    reserved for an actual independent verification date; a crawl timestamp is
    not a verification event.
    """

    model_config = ConfigDict(frozen=True)

    product_id: str
    attribute: str
    value: Any | None = None
    status: EvidenceStatus
    evidence_scope: EvidenceScope = EvidenceScope.UNKNOWN
    source: str | None = None
    source_field: str | None = None
    observed_at: datetime | None = None
    verified_at: datetime | None = None
    catalog_version: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


_DIRECT_FIELDS: dict[str, str] = {
    "name": "name",
    "brand": "brand",
    "category": "category_hierarchy",
    "price_vnd": "price_vnd",
    "stock": "stock",
    "promotion": "promotion",
    "expiry_at": "expiry_at",
    "ingredients": "ingredients",
    "spicy": "spicy",
}

_UNSUPPORTED_ATTRIBUTES = frozenset(
    {
        "availability",
        "benefits",
        "taste",
        "usage",
        "suitability",
        "nutrition",
        "calories",
        "caffeine",
        "allergens",
    }
)
_PHYSICALLY_VERIFIED_SHELF_SOURCES = frozenset({"PHYSICALLY_VERIFIED"})


def product_knowledge_facts(
    product: ProductSnapshot,
    attributes: tuple[str, ...] | list[str],
    *,
    source: str,
    catalog_version: str | None = None,
) -> tuple[ProductKnowledgeFact, ...]:
    """Map requested attributes to direct snapshot evidence or explicit UNKNOWN.

    The function deliberately does not infer benefits, taste, use, or suitability
    from names, category, brand, or retailer copy.
    """
    version = product.catalog_version or catalog_version
    result: list[ProductKnowledgeFact] = []
    for requested in dict.fromkeys(value.strip().casefold() for value in attributes):
        if requested == "availability":
            if product.stock_is_current:
                result.append(
                    ProductKnowledgeFact(
                        product_id=product.product_id,
                        attribute=requested,
                        value=product.sellable_stock > 0,
                        status=EvidenceStatus.VERIFIED,
                        evidence_scope=EvidenceScope.CURRENT_OPERATIONAL,
                        source=source,
                        source_field="sellable_stock",
                        observed_at=product.data_timestamp,
                        catalog_version=version,
                        confidence=1.0,
                        note=(
                            "Current operational stock as represented by the active catalog "
                            "at observed_at; this is not a separate verification event."
                        ),
                    )
                )
            else:
                result.append(
                    ProductKnowledgeFact(
                        product_id=product.product_id,
                        attribute=requested,
                        status=EvidenceStatus.UNKNOWN,
                        evidence_scope=EvidenceScope.UNKNOWN,
                        source=source,
                        catalog_version=version,
                        note="The stored stock is a dated snapshot, not current availability.",
                    )
                )
            continue

        if requested == "shelf":
            if product.shelf and product.shelf_source not in _PHYSICALLY_VERIFIED_SHELF_SOURCES:
                result.append(
                    ProductKnowledgeFact(
                        product_id=product.product_id,
                        attribute=requested,
                        value=product.shelf,
                        status=EvidenceStatus.INFERRED,
                        evidence_scope=EvidenceScope.RECOMMENDED_PLANOGRAM,
                        source=source,
                        source_field="shelf",
                        observed_at=product.data_timestamp,
                        catalog_version=version,
                        confidence=0.5,
                        note=(
                            "The stored locator is not marked PHYSICALLY_VERIFIED; actual "
                            "store placement is unverified."
                        ),
                    )
                )
            elif product.shelf:
                result.append(
                    ProductKnowledgeFact(
                        product_id=product.product_id,
                        attribute=requested,
                        value=product.shelf,
                        status=EvidenceStatus.VERIFIED,
                        evidence_scope=EvidenceScope.PHYSICAL_STORE,
                        source=source,
                        source_field="shelf",
                        observed_at=product.data_timestamp,
                        catalog_version=version,
                        confidence=1.0,
                        note=(
                            "The catalog source marks this location as physically verified; "
                            "a separate verification timestamp is not available."
                        ),
                    )
                )
            else:
                result.append(_unknown_fact(product, requested, source, version))
            continue

        if requested == "description":
            if product.description:
                result.append(
                    ProductKnowledgeFact(
                        product_id=product.product_id,
                        attribute=requested,
                        value=product.description,
                        status=EvidenceStatus.INFERRED,
                        evidence_scope=EvidenceScope.RETAILER_LISTING,
                        source=source,
                        source_field="description",
                        observed_at=product.data_timestamp,
                        catalog_version=version,
                        confidence=0.5,
                        note=(
                            "Retailer listing text; do not treat marketing language as a "
                            "verified benefit, safety, or suitability claim."
                        ),
                    )
                )
            else:
                result.append(_unknown_fact(product, requested, source, version))
            continue

        source_field = _DIRECT_FIELDS.get(requested)
        if requested == "category":
            value = product.category_hierarchy or product.category
            source_field = "category_hierarchy" if product.category_hierarchy else "category"
        else:
            value = getattr(product, source_field, None) if source_field else None
        if value is not None:
            qualifier = None
            if requested == "stock" and not product.stock_is_current:
                qualifier = "Current stock is unknown."
            elif requested == "promotion":
                qualifier = "Current campaign terms and validity are unverified."
            result.append(
                ProductKnowledgeFact(
                    product_id=product.product_id,
                    attribute=requested,
                    value=value,
                    status=EvidenceStatus.VERIFIED,
                    evidence_scope=EvidenceScope.SOURCE_RECORD,
                    source=source,
                    source_field=source_field,
                    observed_at=product.data_timestamp,
                    catalog_version=version,
                    confidence=1.0,
                    note=(
                        "Verified only as a value present in the active catalog source "
                        "record. observed_at is the record capture time; this does not "
                        "establish current store conditions or independent product-label "
                        "verification." + (f" {qualifier}" if qualifier else "")
                    ),
                )
            )
        elif requested in _DIRECT_FIELDS or requested in _UNSUPPORTED_ATTRIBUTES:
            result.append(_unknown_fact(product, requested, source, version))
        else:
            result.append(_unknown_fact(product, requested, source, version))
    return tuple(result)


def _unknown_fact(
    product: ProductSnapshot,
    attribute: str,
    source: str,
    catalog_version: str | None,
) -> ProductKnowledgeFact:
    return ProductKnowledgeFact(
        product_id=product.product_id,
        attribute=attribute,
        status=EvidenceStatus.UNKNOWN,
        evidence_scope=EvidenceScope.UNKNOWN,
        source=source,
        catalog_version=catalog_version,
        note="The active catalog has no attribute-level evidence; do not infer this fact.",
    )
