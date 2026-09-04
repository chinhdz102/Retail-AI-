import httpx
import pytest

from backend.api.main import app


@pytest.mark.asyncio
async def test_health_endpoint_returns_service_status() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-retail-backend",
        "version": "0.1.0",
    }
