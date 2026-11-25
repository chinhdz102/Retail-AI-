"""Tests for Pipecat Smart Turn v3.2 ONNX end-of-turn detection & Vietnamese lexical rules."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.services.smart_turn import (
    DEFAULT_SMART_TURN_MODEL,
    MAX_WINDOW_SAMPLES,
    SAMPLE_RATE,
    classify_vietnamese_lexical_turn,
    compute_whisper_log_mel_features,
    predict_turn_completion,
    truncate_or_left_pad,
)


def test_truncate_or_left_pad_preserves_trailing_audio_window() -> None:
    short = np.ones(SAMPLE_RATE * 2, dtype=np.float32) * 0.25
    padded = truncate_or_left_pad(short)
    assert padded.shape == (MAX_WINDOW_SAMPLES,)
    assert float(np.max(np.abs(padded[: SAMPLE_RATE * 6]))) == 0.0
    assert float(np.mean(padded[-SAMPLE_RATE * 2 :])) == pytest.approx(0.25)

    long_audio = np.arange(SAMPLE_RATE * 10, dtype=np.float32)
    trimmed = truncate_or_left_pad(long_audio)
    assert trimmed.shape == (MAX_WINDOW_SAMPLES,)
    assert float(trimmed[0]) == float(SAMPLE_RATE * 2)
    assert float(trimmed[-1]) == float(SAMPLE_RATE * 10 - 1)


def test_whisper_log_mel_features_shape_and_bounds() -> None:
    t = np.arange(SAMPLE_RATE * 2, dtype=np.float32) / SAMPLE_RATE
    speech_like = 0.2 * np.sin(2 * np.pi * 240.0 * t)
    feats = compute_whisper_log_mel_features(speech_like, do_normalize=True)
    assert feats.shape == (80, 800)
    assert feats.dtype == np.float32
    assert np.isfinite(feats).all()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tương ớt Chin Su chai 1kg giá bao nhiêu vậy?", "complete"),
        ("Ở đây có loại nào ít đường hơn không", "complete"),
        ("Thêm vào giỏ", "complete"),
        ("Thanh toán", "complete"),
        ("Cho mình xem thành phần nhé", "complete"),
        ("Cho mình hỏi là", "incomplete"),
        ("Cho mình hỏi", "incomplete"),
        ("Tương ớt Chin Su chai", "incomplete"),
        ("Mình đang tìm loại sữa tươi mà", "incomplete"),
        ("Ở đây có", "incomplete"),
        ("Sữa tươi với cả", "incomplete"),
        ("Tương ớt Chin Su 1kg", "ambiguous"),
        ("", "ambiguous"),
    ],
)
def test_vietnamese_lexical_turn_classification(text: str, expected: str) -> None:
    assert classify_vietnamese_lexical_turn(text) == expected


def test_predict_turn_completion_fastpaths_and_onnx_inference() -> None:
    t = np.arange(SAMPLE_RATE * 2, dtype=np.float32) / SAMPLE_RATE
    audio = (0.15 * np.sin(2 * np.pi * 200.0 * t)).astype(np.float32)

    complete = predict_turn_completion(audio, partial_text="Giá bao nhiêu vậy?", language="vi")
    assert complete.complete is True
    assert complete.model_used == "lexical-fastpath"

    incomplete = predict_turn_completion(audio, partial_text="Cho mình hỏi là", language="vi")
    assert incomplete.complete is False
    assert incomplete.model_used == "lexical-fastpath"

    if DEFAULT_SMART_TURN_MODEL.is_file():
        ambiguous = predict_turn_completion(audio, partial_text="Tương ớt Chin Su", language="vi")
        assert ambiguous.model_used == "smart-turn-v3.2-cpu"
        assert 0.0 <= ambiguous.probability <= 1.0
        assert ambiguous.elapsed_ms < 1000.0

    # Graceful fallback if model path is unavailable (e.g. serverless deployment).
    fallback = predict_turn_completion(
        audio,
        partial_text="Tương ớt Chin Su",
        language="vi",
        model_path=Path("data/models/does-not-exist.onnx"),
    )
    assert fallback.complete is True
    assert fallback.model_used == "lexical-fallback"
