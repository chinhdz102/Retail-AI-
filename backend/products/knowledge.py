from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    MANUFACTURER_DECLARATION = "MANUFACTURER_DECLARATION"
    MANUFACTURER_CLAIM = "MANUFACTURER_CLAIM"
    UNKNOWN = "UNKNOWN"


class ProductKnowledgeFact(BaseModel):
    """One product attribute with source, scope, and time/version provenance.

    ``observed_at`` is when the source record was captured. ``verified_at`` is
    when the evidence was checked against its cited source; that check does not
    independently establish product efficacy, label accuracy, or suitability.
    """

    model_config = ConfigDict(frozen=True)

    product_id: str
    attribute: str
    value: Any | None = None
    status: EvidenceStatus
    evidence_scope: EvidenceScope = EvidenceScope.UNKNOWN
    source: str | None = None
    source_url: str | None = None
    source_field: str | None = None
    observed_at: datetime | None = None
    verified_at: datetime | None = None
    catalog_version: str | None = None
    evidence_version: str | None = None
    product_match_scope: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


class ManufacturerEnrichmentFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attribute: str
    value: Any
    status: EvidenceStatus
    evidence_scope: EvidenceScope
    source: str = Field(min_length=1, max_length=160)
    source_url: str = Field(min_length=1, max_length=2048)
    source_field: str = Field(min_length=1, max_length=240)
    observed_at: datetime
    verified_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("source_url")
    @classmethod
    def require_safe_https_source(cls, value: str) -> str:
        if any(character.isspace() or ord(character) < 0x20 for character in value):
            raise ValueError("Manufacturer source URL contains whitespace or control characters")
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "Manufacturer source URL must be an absolute credential-free HTTPS URL"
            )
        return value

    @model_validator(mode="after")
    def require_consistent_evidence_semantics(self) -> ManufacturerEnrichmentFact:
        if self.evidence_scope == EvidenceScope.MANUFACTURER_DECLARATION:
            if self.status != EvidenceStatus.VERIFIED or self.verified_at is None:
                raise ValueError(
                    "A manufacturer declaration must be verified as published and include "
                    "verified_at"
                )
        elif self.evidence_scope == EvidenceScope.MANUFACTURER_CLAIM:
            if self.status != EvidenceStatus.INFERRED:
                raise ValueError("Manufacturer marketing claims must remain INFERRED")
        else:
            raise ValueError("External enrichment must use a manufacturer evidence scope")
        return self


class ManufacturerProductEnrichment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str = Field(min_length=1, max_length=64)
    product_name: str = Field(min_length=1, max_length=240)
    brand: str = Field(min_length=1, max_length=120)
    barcode: str = Field(min_length=1, max_length=64)
    match_scope: str
    facts: tuple[ManufacturerEnrichmentFact, ...] = Field(min_length=1, max_length=20)

    @field_validator("match_scope")
    @classmethod
    def require_known_match_scope(cls, value: str) -> str:
        if value not in {"EXACT_VARIANT", "PRODUCT_LINE"}:
            raise ValueError("match_scope must be EXACT_VARIANT or PRODUCT_LINE")
        return value

    @model_validator(mode="after")
    def require_unique_supported_attributes(self) -> ManufacturerProductEnrichment:
        allowed = {
            "benefits",
            "usage",
            "ingredients",
            "nutrition",
            "calories",
            "caffeine",
            "allergens",
        }
        attributes = [fact.attribute for fact in self.facts]
        if len(attributes) != len(set(attributes)):
            raise ValueError(f"Duplicate enriched attribute for {self.product_id}")
        unsupported = sorted(set(attributes) - allowed)
        if unsupported:
            raise ValueError(f"Unsupported enrichment attributes: {', '.join(unsupported)}")
        return self


class ManufacturerEnrichmentRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    generated_at: datetime
    source_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    records: tuple[ManufacturerProductEnrichment, ...]

    @model_validator(mode="after")
    def require_unique_products(self) -> ManufacturerEnrichmentRegistry:
        ids = [record.product_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("Manufacturer enrichment registry contains duplicate product IDs")
        if self.schema_version != "1.0":
            raise ValueError("Unsupported manufacturer enrichment registry schema")
        return self


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
_DEFAULT_ENRICHMENT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "product_knowledge_enrichment_pilot.json"
)


def _safe_source_url(product: ProductSnapshot) -> str | None:
    """Expose only absolute HTTP(S) catalog links as evidence locators."""
    value = product.seo_url
    if not value:
        return None
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return value
    except ValueError:
        pass
    return None


@lru_cache(maxsize=8)
def _load_manufacturer_enrichment(
    path: str,
    modified_ns: int,
    file_size: int,
) -> tuple[dict[str, ManufacturerProductEnrichment], str, str]:
    del modified_ns, file_size  # The stat values are part of the cache key.
    raw = Path(path).read_bytes()
    registry = ManufacturerEnrichmentRegistry.model_validate_json(raw)
    return (
        {record.product_id: record for record in registry.records},
        hashlib.sha256(raw).hexdigest(),
        registry.source_catalog_sha256,
    )


def _manufacturer_record_for_product(
    product: ProductSnapshot,
    path: Path | None,
) -> tuple[ManufacturerProductEnrichment | None, str | None]:
    source = path or _DEFAULT_ENRICHMENT_PATH
    try:
        stat = source.stat()
    except OSError:
        return None, None
    try:
        records, registry_sha256, source_catalog_sha256 = _load_manufacturer_enrichment(
            str(source.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        # Invalid or unavailable enrichment fails closed to ordinary catalog evidence.
        return None, None
    if (
        product.catalog_version
        and source_catalog_sha256.casefold() != product.catalog_version.casefold()
    ):
        return None, registry_sha256
    record = records.get(product.product_id)
    if record is None:
        return None, registry_sha256
    if (
        record.product_name != product.name
        or record.brand.casefold() != str(product.brand or "").casefold()
        or record.barcode != str(product.barcode or "")
    ):
        return None, registry_sha256
    return record, registry_sha256


def _enriched_fact(
    product: ProductSnapshot,
    fact: ManufacturerEnrichmentFact,
    *,
    match_scope: str,
    registry_sha256: str,
    catalog_version: str | None,
) -> ProductKnowledgeFact:
    return ProductKnowledgeFact(
        product_id=product.product_id,
        attribute=fact.attribute,
        value=fact.value,
        status=fact.status,
        evidence_scope=fact.evidence_scope,
        source=fact.source,
        source_url=fact.source_url,
        source_field=fact.source_field,
        observed_at=fact.observed_at,
        verified_at=fact.verified_at,
        catalog_version=catalog_version,
        evidence_version=f"manufacturer-enrichment-v1:{registry_sha256}",
        product_match_scope=match_scope,
        confidence=fact.confidence,
        note=fact.note,
    )


def product_knowledge_facts(
    product: ProductSnapshot,
    attributes: tuple[str, ...] | list[str],
    *,
    source: str,
    catalog_version: str | None = None,
    enrichment_path: Path | None = None,
) -> tuple[ProductKnowledgeFact, ...]:
    """Map requested attributes to direct snapshot evidence or explicit UNKNOWN.

    The function deliberately does not infer benefits, taste, use, or suitability
    from names, category, brand, or retailer copy.
    """
    version = product.catalog_version or catalog_version
    result: list[ProductKnowledgeFact] = []
    manufacturer_record, registry_sha256 = _manufacturer_record_for_product(
        product,
        enrichment_path,
    )
    manufacturer_facts = (
        {fact.attribute: fact for fact in manufacturer_record.facts}
        if manufacturer_record
        else {}
    )
    for requested in dict.fromkeys(value.strip().casefold() for value in attributes):
        direct_field = _DIRECT_FIELDS.get(requested)
        direct_value = getattr(product, direct_field, None) if direct_field else None
        enriched = manufacturer_facts.get(requested)
        # Prefer direct active-catalog values when they exist. Enrichment may fill
        # absent fields, but can never override price, stock, or physical state.
        if (
            enriched is not None
            and registry_sha256 is not None
            and (direct_field is None or direct_value is None)
        ):
            assert manufacturer_record is not None
            result.append(
                _enriched_fact(
                    product,
                    enriched,
                    match_scope=manufacturer_record.match_scope,
                    registry_sha256=registry_sha256,
                    catalog_version=version,
                )
            )
            continue

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
                        source_url=_safe_source_url(product),
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
                    source_url=_safe_source_url(product),
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
