from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "data" / "verified_organization_facts.json"
MAX_PUBLIC_FACT_AGE_DAYS = 180


class OrganizationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: str
    name: str
    url: HttpUrl | None
    verified_at: date | None
    basis: Literal["official_public", "user_provided"]


class OrganizationClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claim_id: str
    facts: dict[str, str]
    source_ids: list[str] = Field(min_length=1)


class OrganizationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entity_id: str
    entity_type: str
    aliases: list[str]
    display_name: dict[str, str]
    claims: list[OrganizationClaim]
    sources: list[OrganizationSource]

    @model_validator(mode="after")
    def validate_claim_provenance(self) -> OrganizationProfile:
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Organization source_id values must be unique")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Organization claim_id values must be unique")
        known_sources = set(source_ids)
        for source in self.sources:
            if source.basis == "official_public" and (
                source.url is None or source.verified_at is None
            ):
                raise ValueError("Official public sources require a URL and verification date")
        for claim in self.claims:
            if not set(claim.source_ids) <= known_sources:
                raise ValueError(f"Unknown source on organization claim {claim.claim_id}")
        return self


class OrganizationProfileFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1)
    profiles: list[OrganizationProfile]


@dataclass(frozen=True)
class OrganizationAnswer:
    profile: OrganizationProfile
    facts: tuple[str, ...]
    sources: tuple[OrganizationSource, ...]


def _normalize(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", value)
    return " ".join(
        "".join(char for char in token if unicodedata.category(char) != "Mn")
        for token in re.findall(r"[\w+]+", decomposed)
    )


@lru_cache(maxsize=1)
def load_organization_profiles() -> tuple[OrganizationProfile, ...]:
    raw = PROFILE_PATH.read_text(encoding="utf-8")
    validated = OrganizationProfileFile.model_validate_json(raw)
    if validated.schema_version != 2:
        raise ValueError("Unsupported organization profile schema version")
    return tuple(validated.profiles)


def resolve_organization_profile(text: str) -> OrganizationProfile | None:
    normalized = _normalize(text)
    profiles = load_organization_profiles()
    # Explicit project context wins over FPT as a standalone company mention.
    for profile in profiles:
        if profile.entity_type == "project" and any(
            re.search(rf"(?<!\w){re.escape(_normalize(alias))}(?!\w)", normalized)
            for alias in profile.aliases
        ):
            return profile
    if re.search(r"(?<!\w)(?:wincommerce|winmart|winmart\+|win)(?!\w)", normalized):
        return next(
            (profile for profile in profiles if profile.entity_id == "wincommerce"),
            None,
        )
    if re.search(r"(?<!\w)fpt(?!\w)", normalized):
        return next((profile for profile in profiles if profile.entity_id == "fpt"), None)
    return None


def _source_is_current(source: OrganizationSource, today: date) -> bool:
    if source.basis == "user_provided":
        return True
    if source.verified_at is None:
        return False
    age_days = (today - source.verified_at).days
    return 0 <= age_days <= MAX_PUBLIC_FACT_AGE_DAYS


def get_organization_answer(
    text: str,
    language: str,
    *,
    as_of: date | None = None,
) -> OrganizationAnswer | None:
    profile = resolve_organization_profile(text)
    if profile is None:
        return None
    today = as_of or datetime.now(UTC).date()
    source_by_id = {source.source_id: source for source in profile.sources}
    facts: list[str] = []
    used_source_ids: set[str] = set()
    for claim in profile.claims:
        fact = claim.facts.get(language)
        current_sources = [
            source_id
            for source_id in claim.source_ids
            if _source_is_current(source_by_id[source_id], today)
        ]
        if fact and current_sources:
            facts.append(fact)
            used_source_ids.update(current_sources)
    if not facts:
        return None
    sources = tuple(
        source for source in profile.sources if source.source_id in used_source_ids
    )
    return OrganizationAnswer(profile=profile, facts=tuple(facts), sources=sources)
