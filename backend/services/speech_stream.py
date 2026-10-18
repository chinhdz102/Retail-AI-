from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpeechAudioStream:
    """Open upstream audio response kept alive while the browser consumes it."""

    response: Any
    content_type: str
    voice_id: str

    def iter_bytes(self):
        return self.response.aiter_bytes()

    async def aclose(self) -> None:
        await self.response.aclose()
