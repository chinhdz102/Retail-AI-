"""Regression tests for latency optimizations: quota cooldowns, streaming accuracy, and cache."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.products.catalog import ProductSnapshot
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.consultation_cache import (
    TurnOneConsultationCache,
    is_stateless_cacheable_turn,
)
from backend.services.customer_consultation import prepare_streaming_consultation
from backend.services.groq import (
    GroqService,
    _cooldown_seconds_for_429,
    _is_long_term_quota_exhausted,
)
from backend.services.sessions import InMemorySessionStore


@pytest.fixture(scope="module")
def snapshot() -> SnapshotProductRepository:
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json"
    )


def test_long_term_429_allowance_exhaustion_sets_one_hour_cooldown() -> None:
    exc = RuntimeError(
        "Your free allowance for ':free' models is used up. It comes back on 30 Nov 2026."
    )
    assert _is_long_term_quota_exhausted(exc) is True
    assert _cooldown_seconds_for_429({}, exc) == 3600.0

    # Ordinary transient 429 with explicit retry-after stays exact.
    transient = RuntimeError("rate limit exceeded")
    assert _is_long_term_quota_exhausted(transient) is False
    assert _cooldown_seconds_for_429({"retry-after": "12"}, transient) == 12.0


@pytest.mark.asyncio
async def test_exhausted_free_key_cools_down_all_free_models_and_rotates_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import groq as groq_module

    now = 500.0
    monkeypatch.setattr(groq_module, "perf_counter", lambda: now)

    calls: list[tuple[str, str]] = []

    class _MockCompletions:
        def __init__(self, slot: str) -> None:
            self.slot = slot

        async def create(self, **kwargs):
            calls.append((self.slot, kwargs["model"]))
            if self.slot == "chat_primary":
                err = RuntimeError(
                    "Your free allowance for ':free' models is used up. It comes back on 30 Nov."
                )
                err.status_code = 429
                err.response = SimpleNamespace(status_code=429, headers={})
                raise err
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="OK from fallback key"),
                    )
                ]
            )

    settings = Settings(
        _env_file=None,
        chat_llm_base_url="https://tokenharbor.ai/v1",
        chat_llm_api_key="thk_exhausted",
        chat_llm_api_key_fallback_1="thk_fresh",
        chat_llm_model="deepseek-v4.1-flash:free",
        chat_llm_fallback_model="mimo-v2.5:free",
    )
    service = GroqService(settings=settings)
    clients = {
        "chat_primary": SimpleNamespace(
            chat=SimpleNamespace(completions=_MockCompletions("chat_primary"))
        ),
        "chat_fallback_1": SimpleNamespace(
            chat=SimpleNamespace(completions=_MockCompletions("chat_fallback_1"))
        ),
    }
    monkeypatch.setattr(
        service,
        "_chat_client_for_credential",
        lambda slot, _key: clients[slot],
    )

    res1 = await service.chat([{"role": "user", "content": "hi"}])
    assert str(res1) == "OK from fallback key"
    assert calls == [
        ("chat_primary", "deepseek-v4.1-flash:free"),
        ("chat_fallback_1", "deepseek-v4.1-flash:free"),
    ]

    # Even 120 seconds later, exhausted primary key must NOT be re-probed!
    now = 620.0
    res2 = await service.chat([{"role": "user", "content": "hi again"}])
    assert str(res2) == "OK from fallback key"
    assert calls == [
        ("chat_primary", "deepseek-v4.1-flash:free"),
        ("chat_fallback_1", "deepseek-v4.1-flash:free"),
        ("chat_fallback_1", "deepseek-v4.1-flash:free"),
    ]
    assert service._is_on_cooldown("mimo-v2.5:free", "chat_primary", now) is True


@pytest.mark.asyncio
async def test_streaming_consultation_enforces_exclusions_sizes_and_single_vs_basket_routing(
    snapshot: SnapshotProductRepository,
) -> None:
    class _FastBasketStub(GroqService):
        def __init__(self) -> None:
            super().__init__(settings=Settings(_env_file=None, chat_llm_api_key="test"))

        async def fast_chat(self, messages, **_kwargs):
            user_text = str(messages[-1]["content"]).casefold()
            if "mì ý" in user_text:
                return '["mì ý", "sốt mì ý", "thịt bò"]'
            return '["thịt heo", "rau cải", "trứng gà"]'

    stub = _FastBasketStub()
    store = InMemorySessionStore()

    # 1. Exclusion: "không cay" must NEVER pick spicy noodles ("Gà Cay", "Tôm chua cay", ...)
    rec = store.create("neg-spicy")
    plan_spicy = await prepare_streaming_consultation(
        text="Tìm giúp mì gói không cay",
        record=rec,
        repository=snapshot,
        groq=stub,
    )
    assert plan_spicy is not None
    assert "cay" not in plan_spicy.products[0].name.casefold()

    # 2. Exclusion: "không đậu nành" must NEVER pick soy milk
    rec2 = store.create("neg-soy")
    plan_soy = await prepare_streaming_consultation(
        text="Sữa hạt không đậu nành có loại nào?",
        record=rec2,
        repository=snapshot,
        groq=stub,
    )
    assert plan_soy is not None
    assert "đậu nành" not in plan_soy.products[0].name.casefold()

    # 3. Exclusion: "không bạc hà" must NEVER pick mint toothpaste
    rec3 = store.create("neg-mint")
    plan_mint = await prepare_streaming_consultation(
        text="Kem đánh răng không bạc hà có loại nào?",
        record=rec3,
        repository=snapshot,
        groq=stub,
    )
    assert plan_mint is not None
    assert "bạc hà" not in plan_mint.products[0].name.casefold()

    # 4. Pack size enforcement in comparison: "1 lít" must compare 1L vs 1L!
    rec4 = store.create("cmp-1l")
    plan_cmp = await prepare_streaming_consultation(
        text="So sánh sữa tươi TH True Milk và Vinamilk loại 1 lít xem cái nào lợi hơn",
        record=rec4,
        repository=snapshot,
        groq=stub,
    )
    assert plan_cmp is not None
    assert len(plan_cmp.products) == 2
    for prod in plan_cmp.products:
        low = prod.name.casefold()
        assert "1 lít" in low or "1l" in low or "1000ml" in low, prod.name

    # 5. Multi-item meal ("bữa tối dưới 150k cho 2 người") must return >= 2 savory items <= 150k
    rec5 = store.create("meal-dinner")
    plan_meal = await prepare_streaming_consultation(
        text="Gợi ý bữa tối đơn giản dưới 150k cho 2 người",
        record=rec5,
        repository=snapshot,
        groq=stub,
    )
    assert plan_meal is not None
    assert len(plan_meal.products) >= 2
    assert sum(p.price_vnd for p in plan_meal.products) <= 150_000
    for prod in plan_meal.products:
        assert "bánh que" not in prod.name.casefold()


def test_turn_one_consultation_cache_exact_semantic_and_safety_boundaries() -> None:
    cache = TurnOneConsultationCache(max_entries=16, ttl_seconds=60.0)
    store = InMemorySessionStore()
    fresh_rec = store.create("cache-t1")

    assert is_stateless_cacheable_turn("Cà phê hòa tan loại nào đậm vị?", fresh_rec) is True
    # High-risk clinical / allergy queries are never cached.
    assert is_stateless_cacheable_turn("Sữa nào cho người bị tiểu đường?", fresh_rec) is False
    assert is_stateless_cacheable_turn("Mình bị dị ứng đậu phộng ăn gì?", fresh_rec) is False

    # Turn-2 stateful sessions are never cached.
    store.add_message(fresh_rec.session_id, "user", "hi")
    turn2_rec = store.get(fresh_rec.session_id)
    assert is_stateless_cacheable_turn("Cà phê hòa tan loại nào đậm vị?", turn2_rec) is False

    sample_prod = ProductSnapshot(
        product_id="SKU-1",
        name="Cà phê hòa tan rang xay đậm vị 3 trong 1 Nescafé hộp 340g",
        category="Cà phê hòa tan",
        brand="Nescafé",
        price_vnd=80_800,
        stock=10,
        aisle="A1",
    )
    cache.put(
        "Cà phê hòa tan loại nào đậm vị?",
        language="vi",
        catalog_source="snapshot",
        reply_text="Dạ, mẫu Nescafé 3in1 đậm vị 340g giá 80.800đ rất phù hợp ạ.",
        model="qwen/qwen3.8-27b",
        products=[sample_prod],
        related_products=[],
        memory={"need": "cà phê đậm vị", "last_action": "recommend"},
    )

    # Exact hit
    exact = cache.get("cà phê hòa tan loại nào đậm vị?", language="vi", catalog_source="snapshot")
    assert exact is not None
    assert exact.products[0].product_id == "SKU-1"

    # Paraphrased semantic hit (filler words stripped -> Jaccard = 1.0)
    semantic = cache.get(
        "Cho mình hỏi cà phê hòa tan loại nào đậm vị ạ?",
        language="vi",
        catalog_source="snapshot",
    )
    assert semantic is not None
    assert semantic.products[0].product_id == "SKU-1"
    # Mutating returned memory must not corrupt cached memory.
    semantic.memory["need"] = "mutated"
    again = cache.get("Cà phê hòa tan loại nào đậm vị?", language="vi", catalog_source="snapshot")
    assert again is not None and again.memory["need"] == "cà phê đậm vị"

    # Different constraints MUST miss!
    assert (
        cache.get("Cà phê hòa tan ít đường", language="vi", catalog_source="snapshot")
        is None
    )
    assert (
        cache.get("Cà phê hòa tan đậm vị dưới 50k", language="vi", catalog_source="snapshot")
        is None
    )


def test_demo_sales_handles_colloquial_vietnamese_particles_and_parent_category(
    snapshot: SnapshotProductRepository,
) -> None:
    from backend.services.demo_sales import rank_demo_sales, simulated_units
    from backend.services.query_understanding import understand_catalog_query

    paths = snapshot.list_category_hierarchies()
    counts = {p.product_id: simulated_units(p.product_id) for p in snapshot.products}
    for colloquial in (
        "cái nào bán chạy nhất v",
        "loại nào bán chạy nhất vậy",
        "cái nào bán chạy nhất z",
        "món nào bán chạy nhất nhỉ",
    ):
        sq = understand_catalog_query(colloquial, paths)
        assert sq.ranking_criterion == "sales_popularity", colloquial
        ranked = rank_demo_sales(snapshot, sq, "Chăm sóc da", (), counts)
        assert len(ranked) == 3, f"Expected 3 ranked items for {colloquial}"
        assert ranked[0].units_sold_30d >= ranked[1].units_sold_30d >= ranked[2].units_sold_30d


@pytest.mark.asyncio
async def test_streaming_system_prompt_enforces_consistent_minh_ban_pronouns(
    snapshot: SnapshotProductRepository,
) -> None:
    class _Stub(GroqService):
        def __init__(self) -> None:
            super().__init__(settings=Settings(_env_file=None, chat_llm_api_key="test"))

    store = InMemorySessionStore()
    rec = store.create("pronoun-check")
    plan = await prepare_streaming_consultation(
        text="tôi muốn tìm sản phẩm chăm sóc da",
        record=rec,
        repository=snapshot,
        groq=_Stub(),
    )
    assert plan is not None
    sys_prompt = plan.messages[0]["content"]
    assert "mình" in sys_prompt and "bạn" in sys_prompt
    assert "KHÔNG xưng 'em'" in sys_prompt


def test_conversational_chinsu_and_selsun_search_and_diacritic_preservation(
    snapshot: SnapshotProductRepository,
) -> None:
    # Conversational fillers + space/hyphen brand normalization must find Tương ớt Chin-su #1.
    chinsu_hits = snapshot.search("Thì chúng tôi hỏi về tương ớt Chinsu.", limit=6)
    assert len(chinsu_hits) >= 3
    assert all("tương ớt" in p.name.casefold() for p in chinsu_hits[:3])
    assert all(
        "chin-su" in p.name.casefold() or "chin su" in p.name.casefold()
        for p in chinsu_hits[:3]
    )

    selsun_hits = snapshot.search("Tao muốn hỏi về sản phẩm Selsun.", limit=5)
    assert selsun_hits
    assert "selsun" in selsun_hits[0].name.casefold()

    # Diacritic product words (táo, giấy vệ sinh) must NEVER be stripped as fillers.
    apple_hits = snapshot.search("kẹo vị táo", limit=5)
    assert apple_hits and any("táo" in p.name.casefold() for p in apple_hits[:3])
    tissue_hits = snapshot.search("giấy vệ sinh", limit=5)
    assert tissue_hits and any("vệ sinh" in p.name.casefold() for p in tissue_hits[:3])


@pytest.mark.asyncio
async def test_streaming_fast_paths_cover_chinsu_selsun_and_detail_followups(
    snapshot: SnapshotProductRepository,
) -> None:
    class _NoPlannerStub(GroqService):
        def __init__(self) -> None:
            super().__init__(settings=Settings(_env_file=None, chat_llm_api_key="test"))

        async def chat(self, *_args, **_kwargs):
            raise AssertionError("Zero-planner streaming path must not call groq.chat()")

    stub = _NoPlannerStub()
    store = InMemorySessionStore()

    # 1. Colloquial Chin-su chili sauce lookup
    rec_cs = store.create("chinsu-stream")
    plan_cs = await prepare_streaming_consultation(
        text="Thì chúng tôi hỏi về tương ớt Chinsu.",
        record=rec_cs,
        repository=snapshot,
        groq=stub,
    )
    assert plan_cs is not None
    assert "tương ớt" in plan_cs.products[0].name.casefold()
    assert (
        "chin-su" in plan_cs.products[0].name.casefold()
        or "chin su" in plan_cs.products[0].name.casefold()
    )

    # 2. Direct verified ingredients detail query on Chin-su 1kg
    rec_ing = store.create("chinsu-ing")
    plan_ing = await prepare_streaming_consultation(
        text="Tương ớt Chinsu chai 1kg thành phần là gì?",
        record=rec_ing,
        repository=snapshot,
        groq=stub,
    )
    assert plan_ing is not None
    payload_ing = plan_ing.messages[1]["content"]
    assert "verified_details" in payload_ing
    assert '"ingredients":' in payload_ing

    # 3. Colloquial Selsun lookup + follow-up usage question (0 planner calls)
    rec_sel = store.create("selsun-stream")
    plan_sel = await prepare_streaming_consultation(
        text="Tao muốn hỏi về sản phẩm Selsun.",
        record=rec_sel,
        repository=snapshot,
        groq=stub,
    )
    assert plan_sel is not None
    assert "selsun" in plan_sel.products[0].name.casefold()

    rec_sel.shopping.selected_product_ids = [plan_sel.products[0].product_id]
    rec_sel.shopping.consultation = plan_sel.memory
    plan_usage = await prepare_streaming_consultation(
        text="Cho tao biết là cách dùng như thế nào.",
        record=rec_sel,
        repository=snapshot,
        groq=stub,
    )
    assert plan_usage is not None
    assert plan_usage.products[0].product_id == plan_sel.products[0].product_id
    assert '"missing_verified_fields":[]' in plan_usage.messages[1]["content"]
    assert "Lắc kỹ trước khi dùng" in plan_usage.messages[1]["content"]

    # 3b. Unenriched SKU usage follow-up still transparently reports missing_verified_fields
    rec_missing = store.create("missing-usage-stream")
    rec_missing.shopping.selected_product_ids = ["TLM_10007184"]
    rec_missing.shopping.consultation = {"need": "Bánh bao Malai"}
    plan_missing = await prepare_streaming_consultation(
        text="Cho tao biết là cách dùng như thế nào.",
        record=rec_missing,
        repository=snapshot,
        groq=stub,
    )
    assert plan_missing is not None
    assert plan_missing.products[0].product_id == "TLM_10007184"
    assert '"missing_verified_fields":["usage"]' in plan_missing.messages[1]["content"]

    # 4. Clinical/allergy follow-up must STILL bypass streaming for strict safety review
    plan_unsafe = await prepare_streaming_consultation(
        text="Chai này có dùng cho da đầu nhạy cảm bị viêm không?",
        record=rec_sel,
        repository=snapshot,
        groq=stub,
    )
    assert plan_unsafe is None


@pytest.mark.asyncio
async def test_groq_chat_uses_groq_lpu_fast_lane_only_when_opted_in() -> None:
    calls: list[tuple[str, str, str | None]] = []

    class _FakePoolCompletions:
        def __init__(self, label: str) -> None:
            self.label = label

        async def create(self, **kwargs):
            calls.append((self.label, kwargs["model"], kwargs.get("reasoning_effort")))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=f"ok-from-{self.label}"),
                    )
                ]
            )

    def _make_service(*, fast_lane: bool) -> GroqService:
        svc = GroqService(
            settings=Settings(
                _env_file=None,
                groq_api_key="groq-test-key",
                groq_model="qwen/qwen3.8-27b",
                groq_fallback_model="openai/gpt-oss-120b",
                groq_fallback_model_2="openai/gpt-oss-20b",
                chat_llm_api_key="deepseek-test-key",
                chat_llm_base_url="https://tokenharbor.ai/v1",
                chat_llm_model="deepseek-v4.1-flash:free",
                stream_llm_use_groq_lpu=fast_lane,
            )
        )
        groq_client = SimpleNamespace(
            chat=SimpleNamespace(completions=_FakePoolCompletions("groq"))
        )
        chat_client = SimpleNamespace(
            chat=SimpleNamespace(completions=_FakePoolCompletions("chat"))
        )
        svc._client_for_credential = lambda _s, _k: groq_client  # type: ignore[method-assign]
        svc._chat_client_for_credential = lambda _s, _k: chat_client  # type: ignore[method-assign]
        return svc

    # Default (opt-out): stays strictly on dedicated chat LLM.
    svc_off = _make_service(fast_lane=False)
    res_off = await svc_off.chat([{"role": "user", "content": "hi"}])
    assert res_off == "ok-from-chat"
    assert calls[-1][0] == "chat"

    # Opt-in: runs Groq LPU fast-lane first with reasoning_effort="none" on Qwen.
    svc_on = _make_service(fast_lane=True)
    res_on = await svc_on.chat(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
    )
    assert res_on == "ok-from-groq"
    assert calls[-1] == ("groq", "qwen/qwen3.8-27b", "none")


@pytest.mark.asyncio
async def test_deep_audit_fixes_comparison_origin_deictic_and_chinese_search(
    snapshot: SnapshotProductRepository,
) -> None:
    from backend.services.query_understanding import understand_catalog_query

    class _NoPlannerStub(GroqService):
        def __init__(self) -> None:
            super().__init__(settings=Settings(_env_file=None, chat_llm_api_key="test"))

        async def chat(self, *_args, **_kwargs):
            raise AssertionError("Zero-planner streaming path must not call groq.chat()")

    stub = _NoPlannerStub()
    store = InMemorySessionStore()

    # 1. Comparison inherits 2-word category head ("tương ớt", never "tương cà")
    rec_cmp = store.create("cmp-tuong-ot")
    plan_cmp = await prepare_streaming_consultation(
        text="so sánh tương ớt Chinsu và Cholimex",
        record=rec_cmp,
        repository=snapshot,
        groq=stub,
    )
    assert plan_cmp is not None and len(plan_cmp.products) == 2
    assert all("tương ớt" in p.name.casefold() for p in plan_cmp.products), [
        p.name for p in plan_cmp.products
    ]

    # 2. "Xuất xứ của chai này ở đâu?" is NOT shelf-location intent; returns Selsun origin
    paths = snapshot.list_category_hierarchies()
    parsed_origin = understand_catalog_query("Xuất xứ của chai này ở đâu?", paths)
    assert parsed_origin.intent != "location"
    rec_orig = store.create("origin-selsun")
    rec_orig.shopping.selected_product_ids = ["TLM_10016912"]
    rec_orig.shopping.consultation = {"need": "Selsun"}
    plan_orig = await prepare_streaming_consultation(
        text="Xuất xứ của chai này ở đâu?",
        record=rec_orig,
        repository=snapshot,
        groq=stub,
    )
    assert plan_orig is not None
    assert plan_orig.products[0].product_id == "TLM_10016912"
    assert "Việt Nam" in plan_orig.messages[1]["content"]

    # 3. Deictic detail follow-up ("bánh này") stays locked on visible[0]
    rec_deic = store.create("deictic-malai")
    rec_deic.shopping.selected_product_ids = ["TLM_10007184"]
    rec_deic.shopping.consultation = {"need": "Bánh bao Malai"}
    plan_deic = await prepare_streaming_consultation(
        text="cách dùng bánh này thế nào?",
        record=rec_deic,
        repository=snapshot,
        groq=stub,
    )
    assert plan_deic is not None
    assert plan_deic.products[0].product_id == "TLM_10007184"

    # 4. Chinese retail query maps cleanly to milk, never random vegetables
    import unicodedata

    zh_hits = snapshot.search("请问有没有无糖鲜牛奶？", limit=5)
    assert zh_hits
    assert all(
        "sữa" in unicodedata.normalize("NFC", p.name).casefold() for p in zh_hits[:3]
    ), [p.name for p in zh_hits[:3]]



