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
        ("kẹo dẻo trái cây có loại nào", {"Kẹo mềm"}),
        ("thế có kẹo cao su ko", {"Kẹo gum"}),
        ("có singum không", {"Kẹo gum"}),
        ("có socola không", {"Chocolate"}),
        ("có kẹo mút không", {"Kẹo cứng"}),
        ("trái cây sấy khô có không", {"Ô mai & Trái cây sấy khô"}),
        ("có nước cam không", {"Nước hoa quả"}),
        ("nước lau sàn hương cam có không", {"Nước lau sàn nhà"}),
        ("có rau gì ko", {"Rau củ", "Rau gia vị"}),
        ("có chanh không", {"Rau gia vị"}),
        ("có tỏi không", {"Rau gia vị"}),
        ("mì tôm chanh có loại nào", {"Mì ăn liền"}),
        ("có mì ý không", {"Mỳ khô"}),
        ("có cá gì không", {"Thủy hải sản"}),
        ("có cá hộp không", {"Cá, tôm đóng hộp"}),
        ("có pate không", {"Thịt đóng hộp"}),
        ("có cà phê đóng chai không", {"Cà phê"}),
        ("có sữa chua không", {"Sữa chua"}),
        ("tôi muốn ăn vặt có cái gì ko", {"Snack"}),
        ("tương ót nào ít đường nhất", {"Tương ớt"}),
        ("có bột ngọt không", {"Mỳ chính"}),
        ("có dầu oliu không", {"Dầu ô liu"}),
        ("có lăn nách không", {"Lăn khử mùi nữ", "Lăn khử mùi Nam"}),
        ("có son dưỡng môi không", {"Mỹ phẩm t.điểm môi"}),
        ("có chỉ nha khoa không", {"Tăm chỉ nkhoa vslưỡi"}),
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
        ("có trái cây gì ko", "Trái cây", ("kẹo", "thạch", "kem đánh răng", "sấy")),
        ("có hoa quả gì ko", "Trái cây", ("kẹo", "thạch", "bánh", "sấy")),
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
        ("thế có kẹo cao su ko", "Kẹo gum", ("socola", "bánh", "găng tay")),
        ("có singum không", "Kẹo gum", ("socola", "bánh")),
        ("có rau câu không", "Thạch", ("rau răm", "rau ngổ")),
        ("có lăn nách không", "Lăn", ("sữa tắm", "dầu gội")),
        ("có son dưỡng môi không", "dưỡng môi", ("trái cây", "sữa")),
        ("có dầu oliu không", "ô liu", ("dầu gội",)),
        ("có mì ý không", "Mì Ý", ("bò khô", "mì trứng", "mì chũ")),
        ("có cá hộp không", "Cá", ("cà phê", "thịt heo")),
        ("có pate không", "Pate", ("phở", "heo hầm")),
        ("có chỉ nha khoa không", "nha khoa", ("bàn chải",)),
        ("có xì dầu không", "Nước tương", ("dầu ăn",)),
        ("có bột ngọt không", "Bột ngọt", ("đường",)),
        ("có bánh quy không", "Bánh", ("bánh que", "mì")),
        ("có xà bông cục không", "Xà bông", ("dầu gội",)),
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


@pytest.mark.asyncio
async def test_anaphoric_cheapest_followup_stays_locked_to_active_category(
    snapshot: SnapshotProductRepository,
) -> None:
    from backend.services.semantic_nlp_router import clean_native_shopper_query

    # Pure superlative/filler follow-up must NEVER route or search "nhất" -> "Phở Đệ Nhất".
    assert clean_native_shopper_query("loại nào rẻ nhất", fallback_to_raw=False) == ""
    assert route_query("loại nào rẻ nhất") is None

    stub = GroqService(settings=Settings(_env_file=None, chat_llm_api_key="test-key"))
    store = InMemorySessionStore()

    # 1. Non-food Turn 1 (price lookup sets selected_product_ids + active_category,
    #    consultation memory empty) -> Turn 2: "loại nào rẻ nhất" / "loại nào đắt nhất"
    face_washes = snapshot.search(query="", category="Sữa rửa mặt nữ", limit=5)
    assert face_washes
    rec = store.create("srm-followup")
    rec = store.update_shopping(
        rec.session_id,
        selected_product_ids=[p.product_id for p in face_washes],
        active_category="Sữa rửa mặt nữ",
    )
    cheap_plan = await prepare_streaming_consultation(
        text="loại nào rẻ nhất", record=rec, repository=snapshot, groq=stub
    )
    assert cheap_plan is not None and cheap_plan.products
    top_cheap = cheap_plan.products[0]
    assert "sữa rửa mặt" in top_cheap.name.casefold()
    assert "phở" not in top_cheap.name.casefold()
    assert "mặt nạ" not in top_cheap.name.casefold()
    all_srm = snapshot.search(query="", category="Sữa rửa mặt nữ", limit=60)
    assert top_cheap.price_vnd == min(p.price_vnd for p in all_srm)

    expensive_plan = await prepare_streaming_consultation(
        text="loại nào đắt nhất", record=rec, repository=snapshot, groq=stub
    )
    assert expensive_plan is not None and expensive_plan.products
    assert "sữa rửa mặt" in (expensive_plan.products[0].category_hierarchy or "").casefold()
    assert expensive_plan.products[0].price_vnd >= top_cheap.price_vnd

    # 2. Food Turn 1 ("có trái cây gì ko") -> Turn 2: "loại nào rẻ nhất"
    rec2 = store.create("fruit-followup")
    t1_fruit = await prepare_streaming_consultation(
        text="có trái cây gì ko", record=rec2, repository=snapshot, groq=stub
    )
    assert t1_fruit is not None and t1_fruit.products
    rec2 = store.update_shopping(
        rec2.session_id,
        selected_product_ids=[p.product_id for p in t1_fruit.products],
        active_category="Trái cây",
        consultation=t1_fruit.memory,
    )
    t2_fruit = await prepare_streaming_consultation(
        text="loại nào rẻ nhất", record=rec2, repository=snapshot, groq=stub
    )
    assert t2_fruit is not None and t2_fruit.products
    assert "trái cây" in (t2_fruit.products[0].category_hierarchy or "").casefold()
    assert "phở" not in t2_fruit.products[0].name.casefold()


@pytest.mark.asyncio
async def test_out_of_catalog_queries_never_leak_unrelated_products(
    snapshot: SnapshotProductRepository,
) -> None:
    from types import SimpleNamespace

    from backend.services.streaming_consultation import _SEMANTIC_BRIDGE_CACHE

    _SEMANTIC_BRIDGE_CACHE.clear()
    stub = GroqService(settings=Settings(_env_file=None, chat_llm_api_key="test-key"))
    store = InMemorySessionStore()

    # 1. Offline mode (no Groq fast-lane): partial lexical hits like "băng cá nhân" ->
    #    "Dầu gội Clear Băng Lạnh" or parent-bucket leaks like "đồ khui bia" -> "Bia"
    #    and "bánh tráng trộn" -> "Bánh" must NEVER be recommended.
    for query in (
        "có băng cá nhân không",
        "có đồ khui bia không",
        "có bánh tráng trộn không",
        "có sạc điện thoại không",
    ):
        rec = store.create(f"ooc-off-{query}")
        plan = await prepare_streaming_consultation(
            text=query, record=rec, repository=snapshot, groq=stub
        )
        leaked = [p.name for p in (plan.products if plan else [])]
        assert plan is None or not plan.products, (
            f"Offline out-of-catalog query {query!r} leaked {leaked}"
        )

    # 2. Online mode (mocked Groq fast-lane): retrieval-grounded Semantic Bridge resolves
    #    unseen regional slang ("có hột gà không") AND honestly abstains on out-of-catalog items.
    class _MockBridgeGroq(GroqService):
        def _can_use_groq_fast_lane(self) -> bool:
            return True

        async def fast_chat(self, messages, **kwargs):  # type: ignore[override]
            user_txt = str(messages[-1]["content"]).casefold()
            if "hột gà" in user_txt:
                return SimpleNamespace(text='{"pick":null,"kw":["trứng gà"]}')
            if "kẹo cao su" in user_txt:
                return SimpleNamespace(text='{"pick":"C1","kw":["kẹo gum"]}')
            if "tương đen" in user_txt:
                return SimpleNamespace(text='{"pick":null,"kw":["tương đen"]}')
            return SimpleNamespace(text='{"pick":null,"kw":["băng cá nhân"]}')

    mock_groq = _MockBridgeGroq(settings=Settings(_env_file=None, chat_llm_api_key="test-key"))

    rec_egg = store.create("bridge-hot-ga")
    egg_plan = await prepare_streaming_consultation(
        text="có hột gà không", record=rec_egg, repository=snapshot, groq=mock_groq
    )
    assert egg_plan is not None and egg_plan.products
    assert "trứng" in egg_plan.products[0].name.casefold()

    rec_gum = store.create("bridge-keo-cao-su")
    gum_plan = await prepare_streaming_consultation(
        text="thế có kẹo cao su ko", record=rec_gum, repository=snapshot, groq=mock_groq
    )
    assert gum_plan is not None and gum_plan.products
    assert "kẹo gum" in (gum_plan.products[0].category_hierarchy or "").casefold()
    assert "socola" not in gum_plan.products[0].name.casefold()

    rec_bandaid = store.create("bridge-bang-ca-nhan")
    bandaid_plan = await prepare_streaming_consultation(
        text="có băng cá nhân không", record=rec_bandaid, repository=snapshot, groq=mock_groq
    )
    assert bandaid_plan is not None
    assert bandaid_plan.products == []
    assert bandaid_plan.direct_reply and "chưa có" in bandaid_plan.direct_reply.casefold()

    # Unscoped kw fallback must NOT return "Mì tương đen" when shopper asked for "tương đen".
    rec_sauce = store.create("bridge-tuong-den")
    sauce_plan = await prepare_streaming_consultation(
        text="có tương đen không", record=rec_sauce, repository=snapshot, groq=mock_groq
    )
    assert sauce_plan is not None
    assert all("mì" not in p.name.casefold() for p in sauce_plan.products)
    _SEMANTIC_BRIDGE_CACHE.clear()

