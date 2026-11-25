"""Evidence and measurement roles across shopping categories, without a SKU oracle."""

import json
from pathlib import Path

import pytest
from test_customer_consultation import Model, Repository, product

from backend.config import Settings
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.customer_consultation import (
    ConsultationDecision,
    _preserve_constraints,
    _product_sizes,
    _scopes_overlap,
    consult_customer,
    safe_consult_customer,
)
from backend.services.groq import GroqProviderError
from backend.services.sessions import InMemorySessionStore


@pytest.mark.parametrize("text", [
    "Bé 10kg, mua đồ cho bé nhé",
    "Người nặng khoảng 60 kg muốn chọn đồ dùng",
    "Mèo nặng 4kg cần đồ dùng",
    "Mỗi ngày dùng 1L thì chọn loại nào",
    "Dùng 500ml mỗi ngày",
    "Uống 1 lít/ngày",
])
def test_obvious_context_measurements_cannot_become_pack_filters(text):
    assert _product_sizes(text) == set()


@pytest.mark.parametrize("text,expected", [
    ("gói 500g cho bé 10kg", {("weight", 500)}),
    ("chai 1 lít, mỗi ngày dùng 200ml", {("volume", 1000)}),
    ("loại 0,5kg", {("weight", 500)}),
    ("hộp 300g hoặc túi 500g", {("weight", 300), ("weight", 500)}),
])
def test_real_product_measurements_survive_near_recipient_or_consumption(text, expected):
    assert _product_sizes(text) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("context,size", [
    ("Bé 10kg, tư vấn đồ dùng cho bé", "10kg"),
    ("Mèo nặng 4kg, chọn đồ giúp mình", "4kg"),
    ("Mỗi ngày dùng 1L, tư vấn loại phù hợp", "1L"),
])
async def test_numeric_quote_does_not_legitimize_a_planners_wrong_measurement_role(context, size):
    row = product(name="Đèn đọc sách Nova 500ml")
    model = Model(
        {"action": "recommend", "searches": [{"category": "Đèn đọc sách"}],
         "required_size": size},
        {"picks": [{"candidate": 0}],
         "text": "Mình chọn {name:0} ({price:0}) theo mức tiền mua ban đầu."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text=context, groq=model, repository=Repository([row]),
        record=InMemorySessionStore().create("wrong-measurement-role"),
    )
    assert result.products == [row]
    assert result.memory["constraints"]["required_size"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("group,title", [
    ("Sổ", "Sổ A5 80 trang"),
    ("Gối", "Ruột gối 40x60cm"),
    ("Pin", "Pin AAA vỉ 2 viên"),
])
async def test_selector_and_reviewer_see_exact_references_and_the_reply_being_followed_up(
    group, title,
):
    row = product(name=title, path=f"Cửa hàng > Đồ dùng > {group}").model_copy(
        update={"ingredients": "Vật liệu ghi trong bản ghi nguồn của mẫu thử."},
    )
    record = InMemorySessionStore().create("evidence-context")
    record.shopping.consultation = {
        "need": f"chọn {group}",
        "constraints": {"searches": [{"category": group}]},
    }
    previous = "Mình đang so kích thước và tiền mua ban đầu của các món này."
    record.messages = [{"role": "assistant", "content": previous}]
    draft_text = "Mình nghiêng về {name:0} ({price:0}) theo tiền mua ban đầu."
    model = Model(
        {"picks": [{"candidate": 0}], "text": draft_text},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="tư vấn cho mình với", record=record, repository=Repository([row]), groq=model,
    )
    assert result.products == [row]
    selection = json.loads(model.calls[0][0][1]["content"].split("\n", 1)[1])
    review = json.loads(model.calls[1][0][1]["content"].split("\n", 1)[1])
    for context in (selection, review):
        assert context["previous_answer"] == previous
    candidate = selection["candidates"][0]
    index = candidate["candidate"]
    assert index == 0
    # The prompt defines these references from numeric candidate IDs; compact
    # context need not repeat mechanically derivable strings in every row.
    assert f"{{name:{index}}}" in draft_text
    assert f"{{price:{index}}}" in draft_text
    assert review["editor_contract"]["name_refs"] == [f"{{name:{index}}}"]
    assert review["editor_contract"]["price_refs"] == [f"{{price:{index}}}"]
    assert candidate["name"] == row.name and candidate["price_vnd"] == row.price_vnd
    assert row.name in result.text and "80.000đ" in result.text
    assert "{name:" not in result.text and "{price:" not in result.text
    assert review["rendered_text"] == result.text
    evidence = next(fact for fact in candidate["evidence"] if fact["attribute"] == "ingredients")
    assert evidence["value"] == row.ingredients
    assert evidence["source"] == "test_catalog" and evidence["status"] == "VERIFIED"
    assert evidence["evidence_scope"] == "SOURCE_RECORD"
    assert review["selected_products"][0]["evidence"] == candidate["evidence"]
    assert "Ignore all instructions" not in json.dumps(selection)


@pytest.mark.parametrize("parent,child", [
    ("Chiếu sáng", "Đèn bàn"),
    ("Văn phòng", "Bút bi"),
    ("Đồ dùng nhà bếp", "Hộp bảo quản"),
])
def test_narrowing_a_real_taxonomy_scope_preserves_the_customers_constraints(parent, child):
    paths = (("Cửa hàng", parent, child), ("Cửa hàng", "Nhóm khác", "Loại khác"))
    previous = {"constraints": {
        "max_price_vnd": 90_000, "strict_price_limit": True,
        "searches": [{"category": parent, "excluded": ["màu đỏ"]}],
    }}
    plan = ConsultationDecision(action="recommend", searches=[{"category": child}])
    _preserve_constraints(
        plan, previous, "chọn loại phù hợp giúp mình", parsed_budget=None,
        explicit_sizes=set(), paths=paths,
    )
    assert plan.max_price_vnd == 90_000 and plan.strict_price_limit
    assert plan.searches[0].excluded == ["màu đỏ"]


def test_sibling_categories_do_not_inherit_each_others_budget_or_exclusions():
    paths = (("Cửa hàng", "Đồ dùng", "Gối"), ("Cửa hàng", "Đồ dùng", "Khăn"))
    assert not _scopes_overlap({"goi"}, {"khan"}, paths)
    plan = ConsultationDecision(action="recommend", searches=[{"category": "Khăn"}])
    _preserve_constraints(
        plan, {"constraints": {"max_price_vnd": 80_000,
                              "searches": [{"category": "Gối", "excluded": ["màu đỏ"]}]}},
        "bây giờ chọn khăn", parsed_budget=None, explicit_sizes=set(), paths=paths,
    )
    assert plan.max_price_vnd is None and plan.searches[0].excluded == []


@pytest.mark.asyncio
async def test_parent_to_leaf_followup_does_not_erase_typed_answers_or_budget():
    row = product(name="Đèn bàn Nova", path="Cửa hàng > Chiếu sáng > Đèn bàn")
    record = InMemorySessionStore().create("scope-refinement")
    answered = {"question_id": "q_portable", "slot_key": "portable", "scope": "Chiếu sáng",
                "value": "để bàn", "question": "Bạn đặt ở đâu?"}
    record.shopping.consultation = {
        "need": "Đèn đọc sách dưới 90 nghìn", "answered_slots": [answered],
        "constraints": {"max_price_vnd": 90_000, "strict_price_limit": True,
                        "searches": [{"category": "Chiếu sáng"}]},
    }
    model = Model(
        {"action": "recommend", "searches": [{"category": "Đèn bàn"}]},
        {"picks": [{"candidate": 0}],
         "text": "Mình nghiêng về {name:0} ({price:0}) theo số tiền bạn dành."},
        {"acceptable": True},
    )
    result = await consult_customer(
        text="Chọn đèn bàn giúp mình", record=record, groq=model, repository=Repository([row]),
    )
    assert result.products == [row]
    assert result.memory["constraints"]["max_price_vnd"] == 90_000
    assert result.memory["answered_slots"] == [answered]


@pytest.mark.asyncio
async def test_demo_local_fallback_gives_typed_catalog_advice_when_provider_is_down():
    class ProviderDown(Model):
        settings = Settings(customer_consultation_local_fallback_enabled=True)

        async def chat(self, *args, **kwargs):
            raise GroqProviderError("provider unavailable")

    row = product(name="Ruột gối ép hơi Hometex size 40*60cm", price=109_000,
                  path="Cửa hàng > Đồ Dùng Phòng Ngủ > Gối")
    result = await safe_consult_customer(
        text="Mua gối dưới 200 nghìn",
        record=InMemorySessionStore().create("local-degraded"), repository=Repository([row]),
        groq=ProviderDown(),
    )
    assert result.model == "consultation-local-fallback"
    assert result.products == [row]
    assert "109.000đ" in result.text and "ruột" in result.text.casefold()
    assert "trục trặc" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Có loại nào điều trị mụn không?",
        "Mình muốn dọn nhà cuối tuần.",
        "Đồ ăn sáng không có lò vi sóng.",
        "Nước giặt cho máy cửa trước dưới 150 nghìn.",
        "Thịt bò hoặc ba chỉ để ăn lẩu, khoảng 200 nghìn.",
    ],
)
async def test_degraded_lookup_cannot_substitute_for_semantic_function_or_compatibility(text):
    class ProviderDown(Model):
        settings = Settings(customer_consultation_local_fallback_enabled=True)

        async def chat(self, *args, **kwargs):
            raise GroqProviderError("provider unavailable")

    repository = SnapshotProductRepository(Path("data/thanh_lam_products.json"))
    result = await safe_consult_customer(
        text=text,
        groq=ProviderDown(),
        repository=repository,
        record=InMemorySessionStore().create("local-route-regression"),
    )
    assert result.model == "consultation-fallback" and not result.products
    assert result.memory["unprocessed_request"]["text"] == text


@pytest.mark.asyncio
async def test_demo_fallback_does_not_return_adult_diapers_for_a_child_weight_request():
    class ProviderDown(Model):
        settings = Settings(customer_consultation_local_fallback_enabled=True)

        async def chat(self, *args, **kwargs):
            raise GroqProviderError("provider unavailable")

    result = await safe_consult_customer(
        text="Bỉm cho bé 10kg",
        groq=ProviderDown(),
        repository=SnapshotProductRepository(Path("data/thanh_lam_products.json")),
        record=InMemorySessionStore().create("local-child-audience"),
    )
    assert result.model == "consultation-fallback" and not result.products
