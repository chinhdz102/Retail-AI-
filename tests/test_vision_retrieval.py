from __future__ import annotations

import pytest

from backend.products.planogram import friendly_location
from backend.services.vision_retrieval import TFLiteVisionRetriever, VisionModelUnavailable


def test_friendly_location_explains_planogram_code() -> None:
    assert friendly_location("A06-B01-L04-P02") == "Dãy 06 · Khoang 01 · Tầng 04 · Ô 02"
    assert friendly_location("A2-L2") == "Khu A2 · Tầng L2"


def test_vision_retriever_fails_closed_without_kaggle_bundle(tmp_path) -> None:
    retriever = TFLiteVisionRetriever(tmp_path)
    with pytest.raises(VisionModelUnavailable, match="TFLite model bundle"):
        retriever.identify(b"image")
