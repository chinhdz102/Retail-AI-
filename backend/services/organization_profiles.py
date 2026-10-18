from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "data" / "verified_organization_facts.json"
MAX_PUBLIC_FACT_AGE_DAYS = 180


class OrganizationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    url: HttpUrl | None
    verified_at: date | None


class OrganizationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entity_id: str
    entity_type: str
    aliases: list[str]
    display_name: dict[str, str]
    facts: dict[str, list[str]]
    sources: list[OrganizationSource]


class OrganizationProfileFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1)
    profiles: list[OrganizationProfile]


@dataclass(frozen=True)
class OrganizationAnswer:
    profile: OrganizationProfile
    facts: tuple[str, ...]
    source_name: str
    source_url: str | None
    verified_at: date | None


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
    if validated.schema_version != 1:
        raise ValueError("Unsupported organization profile schema version")
    return tuple(validated.profiles)


def resolve_organization_profile(text: str) -> OrganizationProfile | None:
    normalized = _normalize(text)
    profiles = load_organization_profiles()
    # The project must win over FPT when both names appear; no relationship is inferred.
    for profile in profiles:
        if profile.entity_type == "project" and any(
            alias in normalized for alias in map(_normalize, profile.aliases)
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
    source = next(
        (
            item
            for item in profile.sources
            if item.verified_at is None
            or (today - item.verified_at).days <= MAX_PUBLIC_FACT_AGE_DAYS
        ),
        None,
    )
    if source is None:
        return None
    facts = profile.facts.get(language)
    if not facts:
        return None
    return OrganizationAnswer(
        profile=profile,
        facts=tuple(facts),
        source_name=source.name,
        source_url=str(source.url) if source.url else None,
        verified_at=source.verified_at,
    )
