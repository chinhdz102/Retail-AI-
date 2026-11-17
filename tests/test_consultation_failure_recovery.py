"""Ordinary shopping turns must not fail on contradictory retrieval/review rules."""
import json

import pytest
import httpx
from uuid import UUID

from backend.api.main import _chat_provider_http_error
from backend.config import Settings
from backend.products.repository import get_product_repository
from backend.services.consultation import (
    Composition, Plan, Search, _canonical_searches, _render_structured,
    _retrieve_candidates, _review_advice,
    _catalog_recovery_plan, _compose_and_review, consult_customer,
)
from backend.services.groq import GroqConfigurationError, GroqProviderError, GroqService
from tests.test_consultation import Repository
from tests.test_consultation_review import BAD_DRAFT, PLAN, RecordedReplies


@pytest.mark.parametrize("query,head", [
    ("sữa tắm dịu nhẹ", "sữa tắm"),
    ("dầu gội mùi nhẹ", "dầu gội"),
    ("nước giặt mùi không nồng", "nước giặt"),
    ("khăn giấy giá rẻ", "khăn giấy"),
])
def test_subjective_search_tail_keeps_exact_filters(query, head):
    query = Search(query=query, brand="Nhãn khách chọn", required_phrases=["hương hoa"],
                   package_constraints=[{"dimension": "volume_ml", "value": 1000}])
    parsed = _canonical_searches([query])[0]
    assert parsed.query == head
    assert parsed.brand == query.brand
    assert parsed.required_phrases == query.required_phrases
    assert parsed.package_constraints == query.package_constraints


@pytest.mark.parametrize("query", ["thịt bò nạc", "dầu gội bạc hà", "nước giặt không mùi"])
def test_actual_subtype_and_label_variant_are_not_subjective_filler(query):
    assert _canonical_searches([Search(query=query)])[0].query == query


@pytest.mark.asyncio
async def test_preference_words_do_not_erase_an_existing_product_type():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need="Tìm sữa tắm dịu nhẹ, mùi không nồng", decision_mode="browse",
                searches=_canonical_searches([Search(query="sữa tắm dịu nhẹ")]))
    candidates, missing = await _retrieve_candidates(
        repo, plan, sorted({p.category for p in repo.products}), [plan.need])
    assert not missing and candidates
    assert all("sữa tắm" in p.name.casefold() for p in candidates.values())


@pytest.mark.asyncio
async def test_umbrella_department_cannot_substitute_a_different_head_product():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need="Hạn chế đường, muốn mua đồ uống", decision_mode="browse",
                searches=[Search(query="trà", category="Trà - Các Loại Khác",
                                 required_phrases=["không đường"])])
    candidates, _ = await _retrieve_candidates(
        repo, plan, sorted({p.category for p in repo.products}), [plan.need])
    assert not any("cacao" in p.name.casefold() or "ca cao" in p.name.casefold()
                   for p in candidates.values())


@pytest.mark.asyncio
async def test_recovered_no_match_guidance_keeps_the_no_sku_review_contract():
    plan = Plan(need="Sữa tắm mùi nhẹ", decision_mode="guide",
                searches=[Search(query="sữa tắm")])
    answer = _render_structured(Composition(advice=(
        "Mình chưa xác nhận được mùi của một loại cụ thể. "
        "Để chọn sữa tắm dịu nhẹ, mùi không nồng, bạn nên xem nhãn và thử mùi trước.")),
        {}, plan, "fixture")
    model = RecordedReplies({"acceptable": True, "issues": []})
    review = await _review_advice(model, answer, plan.need, [], {})
    assert review.acceptable
    assert "Do NOT demand a SKU" in model.calls[0][0][0]["content"]
    payload = json.loads(model.calls[0][0][-1]["content"])
    assert payload["products"] == [] and payload["available_candidates"] == []


@pytest.mark.parametrize("claim", ["Sữa tắm này dịu nhẹ, mùi không nồng.",
                                  "Ưu tiên mỹ phẩm điều trị mụn."])
def test_preference_scope_does_not_authorize_unverified_sku_or_medical_claims(claim):
    with pytest.raises(GroqProviderError, match="nhãn"):
        _render_structured(Composition(advice=claim), {}, Plan(need="Mua sữa tắm"), "fixture")


def test_failure_log_identifies_the_stage_without_logging_raw_exception(caplog):
    error = GroqProviderError("do not log raw request or secrets")
    error.failure_stage = "review"
    error.semantic_rejected = True
    response = _chat_provider_http_error(error)
    assert response.status_code == 502
    assert "stage=review" in caplog.text and "semantic_rejected=True" in caplog.text
    assert str(error) not in caplog.text


@pytest.mark.asyncio
async def test_sugar_free_filter_keeps_a_multipack_drink_instead_of_tea_candy():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need="Trà không đường", decision_mode="browse",
                searches=[Search(query="trà", required_phrases=["không đường"])])
    candidates, missing = await _retrieve_candidates(
        repo, plan, sorted({p.category for p in repo.products}), [plan.need])
    assert not missing and candidates
    assert any(p.name.casefold().startswith("lốc") for p in candidates.values())
    assert all("kẹo" not in p.name.casefold() for p in candidates.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category,forbidden", [
    ("thịt gà lẩu", "Thịt", ("phở", "cháo", "bánh")),
    ("sữa dưỡng ẩm không dầu chống mụn", "Chăm Sóc Da", ("sữa tắm",)),
    ("kem trị mụn da dầu", "Chăm Sóc Da", ("khử mùi",)),
    ("khăn giấy lau nhà", "Khăn Giấy - Khăn Ướt", ("khăn ướt",)),
])
async def test_optional_query_backoff_never_selects_neighbouring_product_types(
    query, category, forbidden,
):
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    plan = Plan(need="Tư vấn mua hàng", decision_mode="browse",
                searches=[Search(query=query, category=category)])
    candidates, _ = await _retrieve_candidates(
        repo, plan, sorted({p.category for p in repo.products}), [plan.need])
    assert all(not any(word in p.name.casefold() for word in forbidden)
               for p in candidates.values())


def test_outage_recovery_cannot_interpret_oily_skin_as_cooking_oil():
    repo = get_product_repository(Settings(catalog_repository="snapshot"))
    assert _catalog_recovery_plan(
        "Da mình là da dầu, dạo này nổi nhiều mụn, có sản phẩm nào dùng được không?",
        None, repo) is None


@pytest.mark.asyncio
async def test_customer_boundary_recovers_invalid_planner_schema_without_inventing_goods():
    answer = await consult_customer(RecordedReplies({}, {}), Repository(),
                                    "Chọn giúp mình với", [], [])
    assert answer.model == "catalog-guidance" and not answer.products
    assert not answer.purchase_lines and answer.plan.source_user_text == "Chọn giúp mình với"


@pytest.mark.asyncio
async def test_customer_boundary_keeps_state_but_never_releases_semantically_rejected_goods():
    prior = Plan(need="Chọn thịt", searches=[Search(query="thịt bò")],
        budget_vnd=100000, budget_scope="basket", requirements={"owned": "đã có gạo"},
        requirement_quotes={"owned": "đã có gạo"})
    outage = GroqProviderError("quota exhausted", fallback_trace=[{"http_status": 429}])
    model = RecordedReplies(PLAN, BAD_DRAFT,
        {"acceptable": False, "issues": ["Cách dùng không đúng dạng hàng."]}, outage)
    answer = await consult_customer(model, Repository(), "Chọn giúp", [], ["P1"], prior.model_dump())
    assert not answer.products and not answer.purchase_lines
    assert answer.plan.requirements == prior.requirements
    assert answer.plan.budget_vnd == prior.budget_vnd
    assert "Dễ cắt" not in answer.text and "50.000" not in answer.text


@pytest.mark.asyncio
async def test_prose_repair_preserves_llm_selection_and_requires_review():
    product = Repository().product
    other = product.model_copy(update={"product_id": "OTHER", "name": "Thịt heo 500g"})
    script = RecordedReplies({"choices": [{"product_id": "P1", "reason": "Mềm và thơm.",
                                          "evidence_fields": ["name"]}]},
                             {"acceptable": True, "issues": []})
    model = GroqService(Settings(groq_api_key="test"))
    model.chat = script.chat
    answer = await _compose_and_review(model, [{"role": "system", "content": "Compose"}],
        {"P1": product, "OTHER": other}, Plan(need="Chọn thịt", decision_mode="recommend"),
        "Chọn thịt", [])
    assert answer.products == (product,) and len(script.calls) == 2
    reviewed = json.loads(script.calls[-1][0][-1]["content"])
    assert reviewed["draft"] == answer.text
    assert "Mềm" not in answer.text and "thơm" not in answer.text


@pytest.mark.asyncio
async def test_review_outage_after_prose_repair_cannot_trigger_unreviewed_catalog_fallback():
    script = RecordedReplies(PLAN, {"choices": [{"product_id": "P1", "reason": "Mềm và thơm.",
                                               "evidence_fields": ["name"]}]},
        GroqProviderError("review unavailable", fallback_trace=[{"http_status": 429}]))
    model = GroqService(Settings(groq_api_key="test"))
    model.chat = script.chat
    result = await consult_customer(model, Repository(), "Chọn giúp", [], [])
    assert not result.products and result.model == "catalog-guidance"


@pytest.mark.asyncio
async def test_real_api_boundary_returns_a_recovered_turn_and_persists_the_plan(monkeypatch):
    from backend.api import main
    from backend.services import consultation as service

    plan = Plan(need="Bữa tối", budget_vnd=120000, budget_scope="basket",
                requirements={"owned": "đã có gạo"})

    async def rejected(*args, **kwargs):
        error = GroqProviderError("invalid draft")
        error.consultation_plan = plan.model_dump()
        error.failure_stage = "composition_schema"
        raise error

    monkeypatch.setattr(service, "consult", rejected)
    monkeypatch.setitem(main.app.dependency_overrides, main.get_settings,
                        lambda: Settings(catalog_repository="snapshot"))
    monkeypatch.setitem(main.app.dependency_overrides, main.get_groq_service, lambda: object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        sid = (await client.post("/sessions", json={"kiosk_id": "failure-regression"})).json()["session_id"]
        response = await client.post("/agent/respond", json={"session_id": sid,
            "text": "Chọn bữa tối dưới 120 nghìn, có gạo rồi", "language": "vi"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["products"] == [] and payload["grounded_product_ids"] == []
        assert payload["model"] == "catalog-guidance"
        saved = main.session_store.get(UUID(sid)).shopping.constraints["consultation_plan"]
        assert saved["budget_vnd"] == 120000 and saved["requirements"] == plan.requirements
        await client.post(f"/sessions/{sid}/close")


@pytest.mark.asyncio
async def test_customer_recovery_does_not_swallow_configuration_errors():
    class Unconfigured:
        async def chat(self, *args, **kwargs):
            raise GroqConfigurationError("missing credentials")

    with pytest.raises(GroqConfigurationError):
        await consult_customer(Unconfigured(), Repository(), "Chọn giúp", [], [])


@pytest.mark.parametrize("claim", [
    "Không làm tăng đường huyết.", "Không làm tăng đường trong cơ thể.",
    "Giúp ổn định huyết áp.", "Phòng bệnh tiểu đường.",
])
def test_label_sugar_claim_does_not_prove_a_physiological_outcome(claim):
    product = Repository().product.model_copy(update={"name": "Trà không đường 500ml"})
    with pytest.raises(GroqProviderError, match="nhãn"):
        _render_structured(Composition(choices=[{"product_id": "P1", "reason": claim,
            "evidence_fields": ["name"]}]), {"P1": product}, Plan(need="Hạn chế đường"), "test")
