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
from backend.services.groq import GroqProviderError, GroqService


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
        pass

    async def chat(self, messages, model=None, tools=None, tool_choice=None) -> str:
        return "Xin lỗi, hiện tại tôi chưa xác minh được giá sản phẩm này."


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
