"""Lightweight Turn-1 semantic & normalized-intent consultation response cache.

Inspired by Hugging Face Open-Source AI Cookbook / GPTCache patterns, without
requiring heavy embedding models so serverless cold-starts stay tiny:
  - Tier 1: O(1) exact normalized Vietnamese query lookup.
  - Tier 2: High-precision token Jaccard match (>= 0.88) strictly within the
    same constraint bucket (catalog, language, budget, budget operator, sugar,
    exclusions, explicit pack sizes, and brands).

Safety & isolation guarantees:
  - ONLY caches stateless Turn-1 catalog consultations (no prior conversation,
    cart items, pending actions, or consultation memory).
  - Never caches medical/clinical/allergy/chemical-safety queries or error/
    fallback responses.
  - Deep-copies consultation memory on read & write so sessions stay isolated.
"""

from __future__ import annotations

import copy
import re
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Any

from backend.products.catalog import ProductSnapshot
from backend.products.category_taxonomy import normalize_category_text
from backend.services.sessions import SessionRecord

_HIGH_RISK_CACHE_BYPASS_RE = re.compile(
    r"\b(?:chua\s+|di ung|nhay cam|tieu duong|huyet ap|tim mach|"
    r"so sinh|mang thai|ba bau|uong thuoc|benh|dau da day|trao nguoc|"
    r"viem|nhiem trung|tron hoa chat|pha tay|javel|"
    r"cho be an|tre em an|tre nho an|giam can|an kieng)\b"
)

_CACHE_FILLER_TOKENS = frozenset({
    "cho", "minh", "toi", "em", "anh", "chi", "hoi", "xem", "coi", "tim",
    "mua", "chon", "goi", "y", "tu", "van", "gioi", "thieu", "giup", "voi",
    "nhe", "nha", "a", "oi", "mai", "ban", "shop", "sieu", "thi", "cua",
    "hang", "co", "loai", "nao", "cai", "mon", "san", "pham", "la", "gi",
    "the", "sao", "muon", "can", "lay", "duoc", "khong", "ha", "nhi",
})

_SUGAR_NONE_RE = re.compile(r"\b(?:khong duong|0 duong|zero sugar|sugar free)\b")
_SUGAR_LESS_RE = re.compile(r"\b(?:it duong|giam duong|han che duong|low sugar)\b")
_BUDGET_RE = re.compile(
    r"\b(?:(duoi|nho hon|it hon|toi da|khong qua|tam|khoang|gan)\s+)?"
    r"(\d+(?:[.,]\d+)?)\s*(k|nghin|ngan|tr|trieu|d|dong|vnd)\b"
)
_SIZE_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(ml|l|lit|g|gr|gram|kg)\b")
_NEGATION_PHRASE_RE = re.compile(
    r"\b(?:khong|ko|k|tranh|kieng)\s+([a-z0-9]+(?:\s+[a-z0-9]+)?)"
)


def _extract_light_signature(
    norm_text: str,
    *,
    catalog_source: str,
    language: str,
) -> tuple[Any, ...]:
    """Build a deterministic constraint bucket key with zero heavy parser calls."""
    sugar = (
        "none"
        if _SUGAR_NONE_RE.search(norm_text)
        else "less"
        if _SUGAR_LESS_RE.search(norm_text)
        else "any"
    )
    budgets = tuple(
        sorted(
            (m.group(1) or "", m.group(2).replace(",", "."), m.group(3))
            for m in _BUDGET_RE.finditer(norm_text)
        )
    )
    sizes = tuple(
        sorted(
            (m.group(1).replace(",", "."), m.group(2))
            for m in _SIZE_RE.finditer(norm_text)
        )
    )
    negations = tuple(
        sorted(
            m.group(1).strip()
            for m in _NEGATION_PHRASE_RE.finditer(norm_text)
            if m.group(1).strip() not in {"co", "ban", "biet", "ro", "can"}
        )
    )
    return (catalog_source, language, sugar, budgets, sizes, negations)


def _content_tokens(norm_text: str) -> frozenset[str]:
    return frozenset(
        tok
        for tok in norm_text.split()
        if len(tok) >= 2 and tok not in _CACHE_FILLER_TOKENS
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


@dataclass(frozen=True)
class CachedConsultationEntry:
    text: str
    model: str
    products: tuple[ProductSnapshot, ...]
    related_products: tuple[ProductSnapshot, ...]
    memory: dict[str, Any]
    content_tokens: frozenset[str]
    constraint_signature: tuple[Any, ...]
    expires_at: float


def is_stateless_cacheable_turn(text: str, record: SessionRecord) -> bool:
    """Return True only for fresh, stateless, low-risk Turn-1 shopping queries."""
    stripped = text.strip()
    if len(stripped) < 4 or len(stripped) > 260:
        return False
    if record.messages:
        return False
    state_val = str(getattr(record.state, "value", record.state))
    if record.shopping.cart_reference or state_val == "CART_ACTIVE":
        return False
    if (
        record.shopping.consultation
        or record.shopping.selected_product_ids
        or record.shopping.pending_action
        or record.shopping.pending_clarification
    ):
        return False
    norm = normalize_category_text(stripped)
    if not norm or _HIGH_RISK_CACHE_BYPASS_RE.search(norm):
        return False
    return True


class TurnOneConsultationCache:
    """Bounded LRU + TTL exact & normalized-intent cache for Turn-1 consultations."""

    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 300.0,
        similarity_threshold: float = 0.88,
    ) -> None:
        self._max_entries = max(16, int(max_entries))
        self._ttl_seconds = max(10.0, float(ttl_seconds))
        self._similarity_threshold = float(similarity_threshold)
        self._entries: OrderedDict[tuple[str, str, str], CachedConsultationEntry] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0

    def _evict_expired(self, now: float) -> None:
        expired_keys = [
            key for key, entry in self._entries.items() if entry.expires_at <= now
        ]
        for key in expired_keys:
            self._entries.pop(key, None)

    def get(
        self,
        text: str,
        *,
        language: str,
        catalog_source: str,
    ) -> CachedConsultationEntry | None:
        now = monotonic()
        self._evict_expired(now)
        norm = normalize_category_text(text)
        if not norm:
            self.misses += 1
            return None

        # Tier 1: Exact normalized query match — O(1).
        exact_key = (catalog_source, language, norm)
        exact = self._entries.get(exact_key)
        if exact is not None and exact.expires_at > now:
            self._entries.move_to_end(exact_key)
            self.hits += 1
            return CachedConsultationEntry(
                text=exact.text,
                model=exact.model,
                products=exact.products,
                related_products=exact.related_products,
                memory=copy.deepcopy(exact.memory),
                content_tokens=exact.content_tokens,
                constraint_signature=exact.constraint_signature,
                expires_at=exact.expires_at,
            )

        # Tier 2: Semantic normalized-intent match within the exact constraint bucket.
        sig = _extract_light_signature(norm, catalog_source=catalog_source, language=language)
        q_tokens = _content_tokens(norm)
        if len(q_tokens) >= 2:
            best_key: tuple[str, str, str] | None = None
            best_entry: CachedConsultationEntry | None = None
            best_score = 0.0
            for key, entry in self._entries.items():
                if entry.constraint_signature != sig:
                    continue
                if len(entry.content_tokens) < 2:
                    continue
                score = _jaccard(q_tokens, entry.content_tokens)
                if score >= self._similarity_threshold and score > best_score:
                    best_score = score
                    best_key = key
                    best_entry = entry
            if best_key is not None and best_entry is not None:
                self._entries.move_to_end(best_key)
                self.hits += 1
                return CachedConsultationEntry(
                    text=best_entry.text,
                    model=best_entry.model,
                    products=best_entry.products,
                    related_products=best_entry.related_products,
                    memory=copy.deepcopy(best_entry.memory),
                    content_tokens=best_entry.content_tokens,
                    constraint_signature=best_entry.constraint_signature,
                    expires_at=best_entry.expires_at,
                )

        self.misses += 1
        return None

    def put(
        self,
        text: str,
        *,
        language: str,
        catalog_source: str,
        reply_text: str,
        model: str,
        products: list[ProductSnapshot] | tuple[ProductSnapshot, ...],
        related_products: list[ProductSnapshot] | tuple[ProductSnapshot, ...],
        memory: dict[str, Any],
    ) -> None:
        cleaned_reply = reply_text.strip()
        if not cleaned_reply or not products:
            return
        lower_model = model.casefold()
        if any(
            bad in lower_model
            for bad in ("fallback", "provider-", "clarification", "error")
        ):
            return
        norm = normalize_category_text(text)
        if not norm:
            return
        now = monotonic()
        self._evict_expired(now)
        exact_key = (catalog_source, language, norm)
        entry = CachedConsultationEntry(
            text=cleaned_reply,
            model=model,
            products=tuple(products),
            related_products=tuple(related_products),
            memory=copy.deepcopy(memory),
            content_tokens=_content_tokens(norm),
            constraint_signature=_extract_light_signature(
                norm, catalog_source=catalog_source, language=language
            ),
            expires_at=now + self._ttl_seconds,
        )
        self._entries[exact_key] = entry
        self._entries.move_to_end(exact_key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


_GLOBAL_CONSULTATION_CACHE = TurnOneConsultationCache()


def get_consultation_cache() -> TurnOneConsultationCache:
    return _GLOBAL_CONSULTATION_CACHE
