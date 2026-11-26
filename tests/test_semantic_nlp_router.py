"""Regression tests for the ultra-fast Vietnamese semantic NLP router and head-noun retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import Settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.customer_consultation import prepare_streaming_consultation
from backend.services.groq import GroqService
from backend.services.semantic_nlp_router import MODEL_PATH, route_query, warm_semantic_nlp_router
from backend.services.sessions import InMemorySessionStore


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data" / "thanh_lam_products.json"
    )


def test_semantic_nlp_router_artifact_loads_and_separates_head_nouns_from_modifiers() -> None:
    assert MODEL_PATH.is_file()
    assert warm_semantic_nlp_router() is True

    expected_routes = [
        ("có trái cây gì ko", {"Trái cây"}),
        ("có hoa quả gì ko", {"Trái cây"}),
        ("có táo không", {"Trái cây"}),
        ("có cam không", {"Trái cây"}),
        ("có nho không", {"Trái cây"}),
        ("có chuối không", {"Trái cây"}),
        ("có xoài không", {"Trái cây"}),
        ("kẹo dẻo trái cây có loại nào", {"Kẹo & Chocolate"}),
        ("trái cây sấy khô có không", {"Ô mai & Trái cây sấy khô"}),
        ("có nước cam không", {"Nước hoa quả"}),
        ("nước lau sàn hương cam có không", {"Nước lau sàn nhà"}),
        ("có rau gì ko", {"Rau củ", "Rau gia vị"}),
        ("có chanh không", {"Rau gia vị"}),
        ("có tỏi không", {"Rau gia vị"}),
        ("mì tôm chanh có loại nào", {"Mì ăn liền"}),
        ("có cá gì không", {"Thủy hải sản"}),
        ("có cà phê đóng chai không", {"Cà phê"}),
        ("có sữa chua không", {"Sữa chua"}),
        ("tôi muốn ăn vặt có cái gì ko", {"Snack"}),
        ("tương ót nào ít đường nhất", {"Tương ớt"}),
        ("có bột ngọt không", {"Mỳ chính"}),
        ("có nước rửa chén trà xanh không", {"Nước rửa chén bát"}),
    ]
    for query, allowed in expected_routes:
        routed = route_query(query, min_confidence=0.45)
        assert routed is not None, f"Router abstained on: {query}"
        assert routed.categories[0] in allowed, (
            f"Query {query!r} routed to {routed.categories[0]!r} ({routed.confidence:.2f}), "
            f"expected one of {allowed}"
        )


@pytest.mark.asyncio
async def test_streaming_consultation_returns_true_head_noun_products(
    snapshot: SnapshotProductRepository,
) -> None:
    stub = GroqService(settings=Settings(_env_file=None, chat_llm_api_key="test-key"))
    store = InMemorySessionStore()

    cases: list[tuple[str, str, tuple[str, ...]]] = [
        ("có trái cây gì ko", "Trái cây", ("kẹo", "thạch", "kem đánh răng")),
        ("có hoa quả gì ko", "Trái cây", ("kẹo", "thạch", "bánh")),
        ("có táo không", "Táo", ("củ sả", "kẹo", "sữa")),
        ("có cam không", "Cam", ("dưa lưới", "lau sàn", "kẹo")),
        ("có nho không", "Nho", ("sữa", "kẹo")),
        ("có chuối không", "Chuối", ("sữa chua", "kẹo")),
        ("có xoài không", "Xoài", ("bánh quế", "kẹo")),
        ("có rau gì ko", "Rau củ", ("bánh cracker", "kẹo")),
        ("có cá gì không", "Thủy hải sản", ("café", "cà phê")),
        ("có nước cam không", "Nước hoa quả", ("lau sàn", "rửa chén")),
        ("có chanh không", "Rau gia vị", ("mì tôm", "nước rửa chén")),
        ("có tỏi không", "Tỏi", ("rau ngổ", "củ sả")),
        ("có sữa chua không", "Sữa chua", ("kẹo", "bánh")),
        ("kẹo dẻo trái cây có loại nào", "Kẹo", ()),
        ("mì tôm chanh có loại nào", "Mì ăn liền", ()),
    ]

    for query, expected_substr, forbidden_substrs in cases:
        rec = store.create(f"sem-{query}")
        plan = await prepare_streaming_consultation(
            text=query, record=rec, repository=snapshot, groq=stub
        )
        assert plan is not None and plan.products, f"No streaming plan for {query!r}"
        top = plan.products[0]
        blob = f"{top.name} {top.category_hierarchy or ''}".casefold()
        assert expected_substr.casefold() in blob, (
            f"Query {query!r} returned {top.name!r} [{top.category_hierarchy}], "
            f"expected {expected_substr!r}"
        )
        for bad in forbidden_substrs:
            assert bad not in top.name.casefold(), (
                f"Query {query!r} leaked modifier product {top.name!r} (matched {bad!r})"
            )
