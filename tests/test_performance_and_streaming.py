"""Tests for best-seller routing, related_products, fast search, and live SSE streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

import backend.api.main as api
from backend.config import get_settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.demo_sales_store import DemoSalesStore
from backend.services.groq import GroqService
from backend.services.query_understanding import understand_catalog_query
from backend.services.sessions import InMemorySessionStore


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json"
    )


def test_vietnamese_best_seller_variants_map_to_sales_popularity(
    snapshot: SnapshotProductRepository,
) -> None:
    paths = snapshot.list_category_hierarchies()
    for text in (
        "Thì cái nào bán được nhiều nhất?",
        "khăn mặt nào bán nhiều nhất",
        "thịt bò nào lượt bán cao nhất",
        "loại nào bán chạy nhất?",
    ):
        parsed = understand_catalog_query(text, paths)
        assert parsed.ranking_criterion == "sales_popularity", text

    # Must not falsely trigger sales_popularity on generic "có bán nhiều loại không?"
    benign = understand_catalog_query("cửa hàng có bán nhiều loại sữa không?", paths)
    assert benign.ranking_criterion != "sales_popularity"


@pytest.mark.asyncio
async def test_demo_sales_followup_ranks_active_category_and_returns_related_cards(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_store = api.session_store
    api.session_store = InMemorySessionStore()
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    store = DemoSalesStore(tmp_path / "sales_followup.sqlite3")
    store.seed(p.product_id for p in snapshot.products)
    settings = get_settings().model_copy(
        update={
            "app_env": "development",
            "demo_sales_enabled": True,
            "demo_sales_database_path": str(store.path),
            "customer_consultation_enabled": True,
            "chat_llm_api_key": SecretStr("test-key"),
        }
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://test",
        ) as client:
            session_id = (await client.post("/sessions", json={"kiosk_id": "bs-test"})).json()[
                "session_id"
            ]
            # Seed session as if Turn 1 showed a single product in "Khăn mặt"
            one_towel = snapshot.search(category="Khăn mặt", limit=1)[0]
            api.session_store.update_shopping(
                api.UUID(session_id),
                active_category="Khăn mặt",
                active_product_id=one_towel.product_id,
                selected_product_ids=[one_towel.product_id],
            )
            resp = await client.post(
                "/agent/respond",
                json={"session_id": session_id, "text": "Thì cái nào bán được nhiều nhất?"},
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["model"] == "demo-sales"
            assert "sản phẩm trong 30 ngày" in payload["text"]
            assert len(payload["products"]) == 1
            assert payload["products"][0]["sales_data_source"] == "SIMULATED"
            assert payload["products"][0]["demo_units_sold_30d"] > 0
            # Top #2 and #3 ranked items appear in related_products with descending sales
            assert len(payload["related_products"]) >= 1
            top_units = payload["products"][0]["demo_units_sold_30d"]
            for rel in payload["related_products"]:
                assert rel["sales_data_source"] == "SIMULATED"
                assert rel["demo_units_sold_30d"] <= top_units
    finally:
        api.session_store = original_store
        api.app.dependency_overrides.pop(api.get_settings, None)


class _StreamingGroqStub(GroqService):
    def __init__(self, chunks: list[str], delay: float = 0.01) -> None:
        super().__init__(settings=get_settings())
        self._chunks = chunks
        self._delay = delay
        self.stream_called = False

    async def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 260,
        temperature: float = 0.2,
    ) -> AsyncIterator[tuple[str, str]]:
        self.stream_called = True
        for piece in self._chunks:
            await asyncio.sleep(self._delay)
            yield piece, "deepseek-v4.1-flash:free"


@pytest.mark.asyncio
async def test_live_sse_streaming_emits_verified_cards_early_and_persists_session(
    snapshot: SnapshotProductRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_store = api.session_store
    api.session_store = InMemorySessionStore()
    monkeypatch.setattr(api, "get_product_repository", lambda *args: snapshot)
    monkeypatch.setattr(retail_tools, "get_product_repository", lambda *args: snapshot)
    stub = _StreamingGroqStub(
        [
            "Dạ, với khăn mặt dùng hằng ngày, ",
            "mẫu nổi bật bên dưới đang có mức giá rất hợp lý ạ. ",
            "Bên dưới còn có thêm các mẫu cùng loại trong catalog để bạn dễ chọn nhé.",
        ],
        delay=0.01,
    )
    settings = get_settings().model_copy(
        update={
            "app_env": "development",
            "customer_consultation_enabled": True,
            "chat_llm_api_key": SecretStr("test-key"),
            "chat_llm_model": "deepseek-v4.1-flash:free",
        }
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    api.app.dependency_overrides[api.get_groq_service] = lambda: stub
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://test",
        ) as client:
            session_id = (await client.post("/sessions", json={"kiosk_id": "sse-live"})).json()[
                "session_id"
            ]
            events: list[tuple[str, dict[str, Any]]] = []
            current_event: str | None = None
            data_lines: list[str] = []
            async with client.stream(
                "POST",
                "/agent/respond/stream",
                json={
                    "session_id": session_id,
                    "text": "cho mình xem khăn mặt dùng hằng ngày",
                    "language": "vi",
                },
            ) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        data_lines.append(line.removeprefix("data: "))
                    elif not line and current_event is not None:
                        events.append((current_event, json.loads("\n".join(data_lines))))
                        current_event = None
                        data_lines = []

            assert stub.stream_called
            assert events[0][0] == "start"
            start_meta = events[0][1]["response"]
            assert len(start_meta["products"]) == 1
            assert len(start_meta["related_products"]) >= 2
            primary_id = start_meta["products"][0]["product_id"]
            assert all(r["product_id"] != primary_id for r in start_meta["related_products"])

            deltas = [payload["text"] for name, payload in events if name == "delta"]
            assert len(deltas) == 3
            assert events[-1][0] == "complete"
            assert (
                events[-1][1]["response"]["model"]
                == "consultation:deepseek-v4.1-flash:free:stream"
            )

            # Session state was persisted for immediate follow-ups
            record = api.session_store.get(api.UUID(session_id))
            assert record.last_grounded_product_id == primary_id
            assert len(record.messages) == 2
    finally:
        api.session_store = original_store
        api.app.dependency_overrides.pop(api.get_settings, None)
        api.app.dependency_overrides.pop(api.get_groq_service, None)


@pytest.mark.asyncio
async def test_zero_planner_streaming_covers_brand_sugar_promo_comparison_and_keeps_safety(
    snapshot: SnapshotProductRepository,
) -> None:
    from backend.services.customer_consultation import prepare_streaming_consultation
    from backend.services.smalltalk import classify_smalltalk_intent

    # Pure greeting with polite help request bypasses consultation planner completely.
    assert classify_smalltalk_intent("chào Mai, bạn tư vấn giúp mình nhé") == "greeting"
    assert classify_smalltalk_intent("chào Mai, tư vấn giúp mình sữa tươi nhé") is None

    class _NoPlannerStub(GroqService):
        def __init__(self) -> None:
            super().__init__(settings=get_settings())

        async def chat(self, *_args, **_kwargs):
            raise AssertionError("Zero-planner streaming path must not call groq.chat()")

        async def stream_chat(self, *_args, **_kwargs):
            yield "ok", "deepseek-v4.1-flash:free"

    stub = _NoPlannerStub()
    store = InMemorySessionStore()

    zero_planner_cases = [
        ("khăn mặt cotton Thanh Lam Home", "thanh lam"),
        ("cho mình xem sữa tươi không đường", "không đường"),
        ("dầu gội trị gàu nào tốt?", ""),
        ("ba chỉ bò Mỹ loại nào ngon?", "ba chỉ"),
        ("hôm nay siêu thị có gì khuyến mãi không?", ""),
        ("so sánh sữa TH True Milk và Vinamilk", ""),
        ("nước giặt OMO loại nào tiết kiệm nhất?", "omo"),
    ]
    for query, expected_substr in zero_planner_cases:
        rec = store.create("stream-cov")
        plan = await prepare_streaming_consultation(
            text=query, record=rec, repository=snapshot, groq=stub
        )
        assert plan is not None, f"Expected zero-planner streaming for: {query}"
        assert len(plan.products) >= 1, query
        if expected_substr:
            assert expected_substr in plan.products[0].name.casefold(), (
                query,
                plan.products[0].name,
            )
        if query.startswith("so sánh"):
            assert len(plan.products) == 2

    # Clinical/allergy queries must NEVER bypass strict safety review.
    unsafe_rec = store.create("safety-check")
    unsafe_plan = await prepare_streaming_consultation(
        text="sữa nào cho người bị tiểu đường và dị ứng sữa?",
        record=unsafe_rec,
        repository=snapshot,
        groq=stub,
    )
    assert unsafe_plan is None
