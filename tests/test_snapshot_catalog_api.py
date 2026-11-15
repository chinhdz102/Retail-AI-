import httpx
import pytest

from backend.api import main
from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services import retail_tools
from backend.services.groq import GroqChatResult, GroqService


class NoCatalogToolService(GroqService):
    async def chat(self, *args, **kwargs):
        return GroqChatResult("Mình sẽ kiểm tra giúp bạn.", "test-no-tools")


@pytest.mark.asyncio
async def test_actual_snapshot_fact_and_product_card_reach_the_api(monkeypatch):
    repository = get_product_repository(Settings(catalog_repository="snapshot"))
    product = repository.get_product_snapshot("TLM_10005885")
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda: repository)
    main.app.dependency_overrides[main.get_groq_service] = lambda: NoCatalogToolService("unused")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            session = (await client.post("/sessions", json={"kiosk_id": "catalog-test"})).json()
            response = await client.post(
                "/agent/respond", json={
                    "session_id": session["session_id"],
                    "text": f"{product.name} giá bao nhiêu?",
                },
            )
        assert response.status_code == 200
        payload = response.json()
        assert "30.700đ" in payload["text"]
        assert payload["grounded_product_ids"] == [product.product_id]
        assert payload["products"][0]["product_id"] == product.product_id
        assert payload["products"][0]["price_vnd"] == product.price_vnd
    finally:
        main.app.dependency_overrides.pop(main.get_groq_service, None)


@pytest.mark.asyncio
async def test_failed_advice_preserves_customer_request_for_retry_and_followup(monkeypatch):
    from backend.services.consultation import Advice, Plan
    from backend.services.groq import GroqProviderError

    history_seen = []

    async def consultation(groq, repository, text, history, previous_ids, previous_plan):
        history_seen.append(history)
        if len(history_seen) <= 2:
            raise GroqProviderError("Unavailable")
        return Advice("Mình chọn món dễ nấu cho bạn.", "test", (), Plan(need="bữa tối"))

    monkeypatch.setattr(main, "consult", consultation)
    main.app.dependency_overrides[main.get_settings] = lambda: Settings(
        catalog_repository="snapshot")
    main.app.dependency_overrides[main.get_groq_service] = lambda: NoCatalogToolService()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            session_id = (await client.post("/sessions", json={"kiosk_id": "failure-memory"}))\
                .json()["session_id"]
            question = "Nhà có gạo rồi, mua món ăn tối tổng 200 nghìn."
            for _ in range(2):
                response = await client.post("/agent/respond", json={
                    "session_id": session_id, "text": question, "language": "vi",
                })
                assert response.status_code == 502
            response = await client.post("/agent/respond", json={
                "session_id": session_id, "text": "Tư vấn giúp t với", "language": "vi",
            })
            assert response.status_code == 200
        assert history_seen == [[], [], [{"role": "user", "content": question}]]
    finally:
        main.app.dependency_overrides.pop(main.get_groq_service, None)
        main.app.dependency_overrides.pop(main.get_settings, None)
