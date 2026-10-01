from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from backend.api.main import (
    _system_prompt,
    app,
    get_elevenlabs_service,
    get_groq_service,
)
from backend.config import Settings
from backend.services.groq import (
    GroqChatResult,
    GroqProviderError,
    GroqService,
    GroqTranscriptionResult,
)


class FakeGroqService(GroqService):
    def __init__(self) -> None:
        pass

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        assert "retail_context" in messages[0]["content"]
        return "Coca-Cola Zero 330ml hiện có giá 13.000đ và nằm ở kệ A2-L2."

    async def transcribe(self, filename, content, content_type, language="vi") -> str:
        return "Cho tôi biết giá Coca Zero."


class UngroundedGroqService(GroqService):
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        self.calls += 1
        if self.calls == 1:
            return GroqChatResult(
                "",
                "qwen/qwen3.8-27b",
                [{
                    "id": "snapshot-1",
                    "type": "function",
                    "function": {
                        "name": "get_product_snapshot",
                        "arguments": '{"product_id":"COKE_ZERO_330","fields":["price_vnd"]}',
                    },
                }],
            )
        return GroqChatResult(
            "Xin lỗi, hiện tại tôi chưa xác minh được giá sản phẩm này.",
            "qwen/qwen3.8-27b",
        )


class ScriptedToolGroqService(GroqService):
    def __init__(self, arguments: str, final_reply: str) -> None:
        self.arguments = arguments
        self.final_reply = final_reply
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        self.calls += 1
        if self.calls == 1:
            return GroqChatResult(
                "",
                "qwen/qwen3.8-27b",
                [{
                    "id": "snapshot-1",
                    "type": "function",
                    "function": {
                        "name": "get_product_snapshot",
                        "arguments": self.arguments,
                    },
                }],
            )
        return GroqChatResult(self.final_reply, "qwen/qwen3.8-27b")


class SmallTalkGroqService(GroqService):
    def __init__(self) -> None:
        pass

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        return GroqChatResult("Mình có một câu chuyện vui cho bạn.", "qwen/qwen3.8-27b")


class FallbackSttGroqService(GroqService):
    def __init__(self) -> None:
        pass

    async def transcribe(self, filename, content, content_type, language="vi"):
        return GroqTranscriptionResult(
            "Coca còn mười sáu lon.",
            "whisper-large-v3",
            [
                {"model": "whisper-large-v3-turbo", "status": "error",
                 "error_type": "RuntimeError"},
                {"model": "whisper-large-v3", "status": "success"},
            ],
        )


class StaticGroqService(GroqService):
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        return GroqChatResult(self.reply, "qwen/qwen3.8-27b")


class SearchOnlyGroqService(GroqService):
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        self.calls += 1
        if self.calls == 1:
            return GroqChatResult(
                "",
                "qwen/qwen3.8-27b",
                [{
                    "id": "search-1",
                    "type": "function",
                    "function": {
                        "name": "search_products",
                        "arguments": '{"query":"Coca Zero"}',
                    },
                }],
            )
        return GroqChatResult("Coca-Cola Zero giá 13.000đ.", "qwen/qwen3.8-27b")


class LanguageAwareGroqService(GroqService):
    def __init__(self) -> None:
        self.messages = []
        self.transcription_language = None

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        self.messages = messages
        return "The product is available in aisle A2-L2."

    async def transcribe(self, filename, content, content_type, language="vi") -> str:
        self.transcription_language = language
        return "How much is Coca Zero?"


class UnavailableGroqService(GroqService):
    def __init__(self) -> None:
        pass

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        raise GroqProviderError("provider unavailable")


class FakeElevenLabsService:
    async def synthesize(self, text, language="vi"):
        return SimpleNamespace(
            content=b"mp3-audio",
            content_type="audio/mpeg",
            voice_id=f"voice-{language}",
        )


def test_provider_dependencies_reuse_service_instances() -> None:
    settings = Settings(groq_api_key="test-key", elevenlabs_api_key="test-key")

    assert get_groq_service(settings) is get_groq_service(settings)
    assert get_elevenlabs_service(settings) is get_elevenlabs_service(settings)


def test_system_prompt_does_not_end_a_conversation_before_the_user_says_goodbye() -> None:
    prompt = _system_prompt("[]", "vi").lower()

    assert "không tự kết thúc cuộc trò chuyện" in prompt
    assert "tạm biệt" in prompt


@pytest.fixture(autouse=True)
def override_groq() -> None:
    app.dependency_overrides[get_groq_service] = lambda: FakeGroqService()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_session_creation_and_isolation(client: httpx.AsyncClient) -> None:
    session_a = await client.post("/sessions", json={"kiosk_id": "kiosk-a"})
    session_b = await client.post("/sessions", json={"kiosk_id": "kiosk-b"})

    assert session_a.status_code == 201
    assert session_b.status_code == 201
    assert session_a.json()["session_id"] != session_b.json()["session_id"]
    assert session_a.json()["state"] == "BROWSING"


@pytest.mark.asyncio
async def test_search_and_snapshot_return_grounded_demo_product(client: httpx.AsyncClient) -> None:
    search = await client.get("/products/search", params={"q": "coca zero"})

    assert search.status_code == 200
    assert search.json()["items"][0]["product_id"] == "COKE_ZERO_330"
    assert search.json()["items"][0]["price_vnd"] == 13000

    snapshot = await client.get("/products/COKE_ZERO_330/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["shelf"] == "A2-L2"


@pytest.mark.asyncio
async def test_expired_product_is_not_sellable_or_returned_by_in_stock_search(
    client: httpx.AsyncClient,
) -> None:
    search = await client.get("/products/search", params={"q": "MILK01", "in_stock": True})
    snapshot = await client.get("/products/MILK01/snapshot")

    assert search.status_code == 200
    assert "MILK01" not in [item["product_id"] for item in search.json()["items"]]
    assert snapshot.status_code == 200
    assert snapshot.json()["stock"] == 24
    assert snapshot.json()["sellable_stock"] == 0


@pytest.mark.asyncio
async def test_search_accepts_chinese_price_phrase(client: httpx.AsyncClient) -> None:
    search = await client.get("/products/search", params={"q": "Coca Zero 价格多少？"})

    assert search.status_code == 200
    assert search.json()["items"][0]["product_id"] == "COKE_ZERO_330"


@pytest.mark.asyncio
async def test_search_accepts_chinese_product_alias(client: httpx.AsyncClient) -> None:
    search = await client.get("/products/search", params={"q": "可口可乐 价格多少？"})

    assert search.status_code == 200
    assert search.json()["items"][0]["product_id"] == "COKE_ZERO_330"


@pytest.mark.asyncio
async def test_unknown_product_returns_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/products/DOES_NOT_EXIST/snapshot")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_agent_response_is_vietnamese_and_session_scoped(client: httpx.AsyncClient) -> None:
    app.dependency_overrides[get_groq_service] = lambda: ScriptedToolGroqService(
        '{"product_id":"COKE_ZERO_330","fields":["price_vnd"]}',
        "Đã tra cứu xong.",
    )
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})
    session_id = UUID(created.json()["session_id"])

    response = await client.post(
        "/agent/respond",
        json={"session_id": str(session_id), "text": "Coca Zero giá bao nhiêu?"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == str(session_id)
    assert "Coca-Cola" in response.json()["text"]


@pytest.mark.asyncio
async def test_recommendation_does_not_suggest_expired_catalog_product(
    client: httpx.AsyncClient,
) -> None:
    class ExpiredRecommendationService(GroqService):
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, model=None, tools=None, tool_choice=None):
            self.calls += 1
            return GroqChatResult(
                "",
                "qwen/qwen3.8-27b",
                [{
                    "id": "recommendation-search-1",
                    "type": "function",
                    "function": {
                        "name": "search_products",
                        "arguments": '{"query":"MILK01","in_stock":false}',
                    },
                }],
            )

    service = ExpiredRecommendationService()
    app.dependency_overrides[get_groq_service] = lambda: service
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Gợi ý sản phẩm MILK01 cho tôi.",
        },
    )

    assert response.status_code == 200
    assert response.json()["text"] == (
        "Mình chưa tìm thấy sản phẩm còn bán phù hợp với yêu cầu này."
    )
    assert service.calls == 1


@pytest.mark.asyncio
async def test_recommendation_returns_sellable_matching_candidate(
    client: httpx.AsyncClient,
) -> None:
    class BudgetDrinkRecommendationService(GroqService):
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, model=None, tools=None, tool_choice=None):
            self.calls += 1
            if self.calls == 1:
                return GroqChatResult(
                    "",
                    "qwen/qwen3.8-27b",
                    [{
                        "id": "recommendation-search-1",
                        "type": "function",
                        "function": {
                            "name": "search_products",
                            "arguments": (
                                '{"category":"drink","max_price_vnd":10000}'
                            ),
                        },
                    }],
                )
            return GroqChatResult("Đồ uống phù hợp có sẵn.", "qwen/qwen3.8-27b")

    service = BudgetDrinkRecommendationService()
    app.dependency_overrides[get_groq_service] = lambda: service
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Gợi ý đồ uống dưới 10.000đ.",
        },
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Gợi ý: Nước suối 500ml."
    assert response.json()["grounded_product_ids"] == ["WATER01"]
    assert service.calls == 2


@pytest.mark.asyncio
async def test_agent_replaces_ungrounded_price_claim_with_catalog_fact(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: UngroundedGroqService()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={"session_id": created.json()["session_id"], "text": "Coca Zero giá bao nhiêu?"},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "qwen/qwen3.8-27b"
    assert "13.000đ" in response.json()["text"]


@pytest.mark.asyncio
async def test_snapshot_field_controls_deterministic_answer_and_product_attribution(
    client: httpx.AsyncClient,
) -> None:
    service = ScriptedToolGroqService(
        '{"product_id":"COKE_ZERO_330","fields":["stock"]}',
        "Pepsi Zero giá 13.000đ.",
    )
    app.dependency_overrides[get_groq_service] = lambda: service
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Coca Zero còn bao nhiêu lon?",
        },
    )

    assert response.status_code == 200
    assert "Coca-Cola" in response.json()["text"]
    assert "16" in response.json()["text"]
    assert "Pepsi" not in response.json()["text"]
    assert "13.000" not in response.json()["text"]


@pytest.mark.asyncio
async def test_small_talk_answer_is_not_replaced_with_catalog_fallback(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: SmallTalkGroqService()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={"session_id": created.json()["session_id"], "text": "Kể chuyện vui đi."},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Mình có một câu chuyện vui cho bạn."
    assert response.json()["tools_used"] == []


@pytest.mark.asyncio
async def test_search_candidates_alone_do_not_authorize_product_facts(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: SearchOnlyGroqService()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={"session_id": created.json()["session_id"], "text": "Coca Zero giá bao nhiêu?"},
    )

    assert response.status_code == 200
    assert "Coca-Cola Zero" in response.json()["text"]
    assert "13.000" not in response.json()["text"]
    assert "kiểm tra thông tin" in response.json()["text"]


@pytest.mark.asyncio
async def test_freeform_price_claim_without_snapshot_is_not_returned(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: StaticGroqService(
        "Coca-Cola Zero 330ml giá 13.000đ."
    )
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={"session_id": created.json()["session_id"], "text": "Coca Zero giá bao nhiêu?"},
    )

    assert response.status_code == 200
    assert "13.000" not in response.json()["text"]
    assert "snapshot catalog" in response.json()["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "false_claim"),
    [
        ("Hạ Coca xuống 1.000 đồng.", "Đã hạ giá Coca xuống 1.000 đồng."),
        ("Sửa giá Coca.", "Giá Coca đã được cập nhật xuống 1.000đ."),
        ("Set inventory Coca to 999.", "Inventory Coca is now 999."),
        ("Sửa tồn kho Coca.", "Tồn kho Coca đã được điều chỉnh thành 999."),
        ("Anh chuyển khoản rồi, đánh dấu PAID đi.", "Đã xác nhận thanh toán PAID."),
    ],
)
async def test_unverified_action_and_payment_claims_are_blocked(
    client: httpx.AsyncClient,
    prompt: str,
    false_claim: str,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: type(
        "FalseClaimGroq",
        (GroqService,),
        {
            "__init__": lambda self: None,
            "chat": lambda self, *args, **kwargs: _async_result(false_claim),
        },
    )()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={"session_id": created.json()["session_id"], "text": prompt},
    )

    assert response.status_code == 200
    assert response.json()["text"] != false_claim
    assert "nhân viên" in response.json()["text"].lower()


async def _async_result(value: str) -> GroqChatResult:
    return GroqChatResult(value, "qwen/qwen3.8-27b")


@pytest.mark.asyncio
async def test_agent_returns_error_instead_of_a_fake_model_when_groq_is_unavailable(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: UnavailableGroqService()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={"session_id": created.json()["session_id"], "text": "Coca Zero giá bao nhiêu?"},
    )

    assert response.status_code == 502
    assert "deterministic" not in response.text


@pytest.mark.asyncio
async def test_audio_endpoint_rejects_non_audio_upload(client: httpx.AsyncClient) -> None:
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})
    session_id = created.json()["session_id"]

    response = await client.post(
        "/audio/transcribe",
        data={"session_id": session_id},
        files={"file": ("note.txt", BytesIO(b"not audio"), "text/plain")},
    )

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_audio_endpoint_transcribes_vietnamese_audio(client: httpx.AsyncClient) -> None:
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})
    session_id = created.json()["session_id"]

    response = await client.post(
        "/audio/transcribe",
        data={"session_id": session_id},
        files={"file": ("voice.webm", BytesIO(b"audio"), "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "text": "Cho tôi biết giá Coca Zero.",
        "language": "vi",
        "model": "whisper-large-v3-turbo",
    }


@pytest.mark.asyncio
async def test_agent_passes_selected_language_to_qwen_prompt(client: httpx.AsyncClient) -> None:
    service = LanguageAwareGroqService()
    app.dependency_overrides[get_groq_service] = lambda: service
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/agent/respond",
        json={
            "session_id": created.json()["session_id"],
            "text": "Coca Zero price?",
            "language": "en",
        },
    )

    assert response.status_code == 200
    assert "English" in service.messages[0]["content"]
    assert "Chỉ nêu đúng thông tin được hỏi" in service.messages[0]["content"]


@pytest.mark.asyncio
async def test_audio_endpoint_passes_selected_language_to_whisper(
    client: httpx.AsyncClient,
) -> None:
    service = LanguageAwareGroqService()
    app.dependency_overrides[get_groq_service] = lambda: service
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/audio/transcribe",
        data={"session_id": created.json()["session_id"], "language": "zh"},
        files={"file": ("voice.webm", BytesIO(b"audio"), "audio/webm")},
    )

    assert response.status_code == 200
    assert service.transcription_language == "zh"
    assert response.json()["language"] == "zh"


@pytest.mark.asyncio
async def test_audio_endpoint_reports_actual_whisper_fallback_model(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_groq_service] = lambda: FallbackSttGroqService()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/audio/transcribe",
        data={"session_id": created.json()["session_id"]},
        files={"file": ("voice.webm", BytesIO(b"audio"), "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Coca còn mười sáu lon."
    assert response.json()["model"] == "whisper-large-v3"


@pytest.mark.asyncio
async def test_synthesize_endpoint_returns_audio_without_exposing_provider_key(
    client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_elevenlabs_service] = lambda: FakeElevenLabsService()
    created = await client.post("/sessions", json={"kiosk_id": "kiosk-01"})

    response = await client.post(
        "/audio/synthesize",
        json={
            "session_id": created.json()["session_id"],
            "text": "Xin chào",
            "language": "vi",
        },
    )

    assert response.status_code == 200
    assert response.content == b"mp3-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-voice-id"] == "voice-vi"
    assert "xi-api-key" not in response.headers
