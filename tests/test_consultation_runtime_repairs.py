"""Regressions from actual stalls, exact price echoes and unshown comparisons."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_customer_consultation import Model, Repository, product

from backend.config import Settings
from backend.services.consultation_text import decode_bound_text
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReply,
    GroundedSelection,
    _render,
    _render_selection,
    consult_customer,
)
from backend.services.groq import GroqService
from backend.services.sessions import InMemorySessionStore


@pytest.mark.parametrize("raw_endpoint", [False, True])
@pytest.mark.asyncio
async def test_stalled_chat_attempt_leaves_time_for_a_configured_fallback(raw_endpoint):
    calls = []
    cancelled = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary":
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append("primary")
                raise
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Đã trả lời từ model dự phòng."), finish_reason="stop",
        )])
        return SimpleNamespace(headers={}, parse=lambda: response) if raw_endpoint else response

    completion = SimpleNamespace(create=create)
    if raw_endpoint:
        completion.with_raw_response = SimpleNamespace(create=create)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
    service = GroqService(Settings(
        _env_file=None, groq_api_key="test", groq_model="primary", groq_fallback_model="fallback",
        groq_fallback_model_2="", groq_chat_attempt_timeout_seconds=0.02,
    ), client=client)
    result = await service.chat([{"role": "user", "content": "Tư vấn giúp mình."}])
    assert result.model == "fallback" and cancelled == ["primary"]
    assert calls == ["primary", "fallback"]
    assert result.fallback_trace[0]["error_type"] == "TimeoutError"


@pytest.mark.parametrize("price_literal", ["155.000đ", "155 000 VND", "155\u202f000\u202fVND"])
@pytest.mark.parametrize("middle", ["", "để nhúng lẩu, "])
def test_exact_same_product_price_echo_is_bound_once_and_preserves_the_sentence(
    price_literal, middle,
):
    raw = json.dumps({"picks": [{"candidate": 0}], "text": [{
        "candidate": 0,
        "explanation": (f"Mình chọn {{product}} {middle}giá {price_literal}, "
                        "bạn thái mỏng thêm để nhúng lẩu."),
    }]})
    facts = {0: {"name": "Thịt bò Nova khay 250g", "price_vnd": 155000}}
    bound = json.loads(decode_bound_text(raw, "text", [0], basket=False, product_facts=facts))
    assert bound["text"].count("{price:0}") == 1
    row = product(name=facts[0]["name"], price=155000)
    answer, rows = _render_selection(
        GroundedSelection.model_validate(bound), [row],
        ConsultationDecision(action="recommend", max_price_vnd=200000), {},
    )
    assert "giá 155.000đ" in answer and answer.count("155.000đ") == 1
    assert "thái mỏng thêm" in answer and rows == [row]


@pytest.mark.parametrize("explanation", [
    "Mình chọn {product} giá 150.000đ.",
    "Giá thị trường của {product} giá 155.000đ.",
    "Trung bình {product} giá 155.000đ.",
    "Mình chọn {product} giảm giá 155.000đ.",
])
def test_false_price_or_different_price_meaning_cannot_use_exact_echo_recovery(explanation):
    raw = json.dumps({"picks": [{"candidate": 0}], "text": [{
        "candidate": 0, "explanation": explanation,
    }]})
    bound = json.loads(decode_bound_text(raw, "text", [0], basket=False,
                                       product_facts={0: {"price_vnd": 155000}}))
    row = product(price=155000)
    with pytest.raises(ValueError, match="unbound price"):
        _render_selection(GroundedSelection.model_validate(bound), [row],
                          ConsultationDecision(action="recommend"), {})


@pytest.mark.parametrize("comparison", [
    "dưới mức", "dưới mốc", "nhỏ hơn mức", "không quá ngân sách", "tối đa mức",
])
def test_same_bound_product_can_compare_its_price_to_the_actual_customer_cap(comparison):
    row = product(name="Nước rửa chén Nova 750g", price=28800)
    selection = GroundedSelection(
        picks=[{"candidate": 0}],
        text=f"Mình chọn {{name:0}} giá {{price:0}}, {comparison} 50 nghìn.",
    )
    answer, rows = _render_selection(
        selection, [row], ConsultationDecision(action="recommend", max_price_vnd=50000), {},
    )
    assert rows == [row] and "28.800đ" in answer


@pytest.mark.parametrize("price,quantity,comparison,amount", [
    (50000, 1, "dưới mức", 50),  # Strict comparison at equality is false.
    (51000, 1, "không quá ngân sách", 50),  # Budget wording must not bypass arithmetic.
    (28800, 2, "dưới mức", 50),  # Two selected units exceed the cap.
    (28800, 1, "dưới mức", 40),  # A cap the customer never supplied.
    (28800, 1, "giá thị trường dưới mức", 50),
])
def test_qualified_budget_comparison_still_requires_evidence(price, quantity, comparison, amount):
    reply = ConsultationReply(
        picks=[{"candidate": 0, "quantity": quantity}],
        text=f"Mình chọn {{name:0}} giá {{price:0}}, {comparison} {amount} nghìn.",
    )
    with pytest.raises(ValueError, match="unbound price"):
        _render(reply, [product(price=price)],
                ConsultationDecision(action="recommend", max_price_vnd=50000))


def test_inclusive_budget_comparison_allows_equal_verified_price():
    selection = GroundedSelection(
        picks=[{"candidate": 0}],
        text="Mình chọn {name:0} giá {price:0}, không quá mức 50 nghìn.",
    )
    row = product(price=50000)
    assert _render_selection(selection, [row], ConsultationDecision(
        action="recommend", max_price_vnd=50000,
    ), {})[1] == [row]


@pytest.mark.parametrize("query", [
    "Có chai nhỏ hơn không, mình không mua cả lốc nhé.",
    "Loại này đắt quá, có loại nào rẻ hơn không?",
])
@pytest.mark.asyncio
async def test_failed_previous_turn_is_not_a_reference_product_that_the_customer_saw(query):
    row = product()
    record = InMemorySessionStore().create("unshown-reference")
    record.shopping.consultation = {
        "need": "mua đồ dùng", "unprocessed_request": {"text": "Chọn giúp một món."},
    }
    model = Model()  # A genuine missing reference needs no invented SKU or provider retry.
    result = await consult_customer(text=query, record=record,
                                    repository=Repository([row]), groq=model)
    assert result.model == "reference-clarification" and result.products == []
    assert "so với" in result.text and "bao nhiêu" in result.text
    assert not model.calls and result.memory["pending_question"]


@pytest.mark.asyncio
async def test_slow_primary_stt_can_use_configured_fallback_within_total_budget(monkeypatch):
    from backend.services.speech_activity import SpeechActivityResult

    monkeypatch.setattr("backend.services.groq.analyze_speech_activity",
                        lambda _: SpeechActivityResult(True, {}))
    calls, cancelled = [], []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "whisper-primary":
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append("primary")
                raise
        return SimpleNamespace(text="Lấy sữa không đường.", segments=[
            SimpleNamespace(start=0, end=1.6, no_speech_prob=0.001,
                            avg_logprob=-0.12, compression_ratio=0.8),
        ])

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    service = GroqService(Settings(
        _env_file=None, groq_api_key="test", groq_stt_model="whisper-primary",
        groq_stt_fallback_model="whisper-fallback", groq_stt_attempt_timeout_seconds=0.02,
        groq_stt_total_timeout_seconds=1,
    ), client=client)
    result = await asyncio.wait_for(
        service.transcribe("vi.wav", b"audio", "audio/wav"), timeout=0.5,
    )
    assert str(result) == "Lấy sữa không đường." and result.model == "whisper-fallback"
    assert calls == ["whisper-primary", "whisper-fallback"] and cancelled == ["primary"]
    assert result.fallback_trace[0]["error_type"] == "TimeoutError"


@pytest.mark.parametrize("preparation_available", [True, False])
@pytest.mark.asyncio
async def test_cloud_stt_uses_optional_transport_without_losing_original_on_preparation_failure(
    monkeypatch, preparation_available,
):
    from backend.services.speech_activity import SpeechActivityResult, SpeechActivityUnavailable

    monkeypatch.setattr("backend.services.groq.analyze_speech_activity",
                        lambda _: SpeechActivityResult(True, {}))

    def prepare(_):
        if not preparation_available:
            raise SpeechActivityUnavailable("Preparation budget exceeded")
        return b"fLaC-prepared"

    monkeypatch.setattr("backend.services.speech_upload.prepare_stt_upload", prepare)
    uploads = []

    async def create(**kwargs):
        uploads.append(kwargs["file"])
        return SimpleNamespace(text="Chọn giúp mình một chai nước.", segments=[
            SimpleNamespace(start=0, end=1.6, no_speech_prob=0.001,
                            avg_logprob=-0.12, compression_ratio=0.8),
        ])

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    service = GroqService(Settings(_env_file=None, groq_api_key="test"), client=client)
    result = await service.transcribe("original.wav", b"original", "audio/wav")
    expected = ("speech.flac", b"fLaC-prepared", "audio/flac") if preparation_available else (
        "original.wav", b"original", "audio/wav",
    )
    assert uploads == [expected] and result.speech_detected
    if preparation_available:
        assert result.quality["stt_upload_original_bytes"] == 8
        assert result.quality["stt_upload_bytes"] == len(b"fLaC-prepared")
