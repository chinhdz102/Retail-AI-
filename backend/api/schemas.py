from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.products.catalog import ProductSnapshot
from backend.shared.contracts import SessionState

LanguageCode = Literal["vi", "en", "zh"]


class SessionCreateRequest(BaseModel):
    kiosk_id: str = Field(default="kiosk-01", min_length=1, max_length=64)


class SessionResponse(BaseModel):
    session_id: UUID
    kiosk_id: str
    state: SessionState
    expires_at: datetime


class AgentRespondRequest(BaseModel):
    session_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    language: LanguageCode = "vi"


class AgentRespondResponse(BaseModel):
    session_id: UUID
    text: str
    model: str
    grounded_product_ids: list[str] = Field(default_factory=list)
    state: SessionState


class ProductSearchResponse(BaseModel):
    items: list[ProductSnapshot]
    data_timestamp: datetime


class TranscriptionResponse(BaseModel):
    session_id: UUID
    text: str
    language: LanguageCode = "vi"
    model: str


class SynthesizeRequest(BaseModel):
    session_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    language: LanguageCode = "vi"
