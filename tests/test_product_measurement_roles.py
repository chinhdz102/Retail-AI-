"""Body weight and serving amounts must not become a product pack filter."""

from pathlib import Path

import pytest

from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import customer_consultation as consultation
from backend.services.sessions import InMemorySessionStore
from backend.services.shopping_language import product_measurement_text


@pytest.mark.parametrize("text", [
    "tã cho bé 6 tháng 10kg", "ta cho be 6 thang 10kg",
    "thức ăn cho chó con 4kg", "thức ăn cho mèo 3 tuổi 5kg",
    "sữa cho mẹ 60kg", "sữa cho bé từ 9 đến 14kg", "bé khoảng 8,5kg",
    "for my dog weighing 4kg", "milk for a child weighing 10kg",
    "cat food for my kitten aged 3 months weighing 2kg",
    "tôi nặng 60kg", "I weigh about 60kg",
    "trẻ sơ sinh nặng 3500g", "tã cho bé 9–14kg", "tã cho bé 9kg đến 14kg",
    "sữa mỗi ngày uống 200ml", "sữa uống 200ml một ngày",
    "milk 200ml per day", "daily drink about 0.5L",
])
def test_recipient_and_consumption_units_are_not_product_size(text):
    assert consultation._product_sizes(text) == set()


@pytest.mark.parametrize("text,expected", [
    ("gói 500g cho bé 6 tháng 10kg", {("weight", 500)}),
    ("thức ăn túi 1kg cho mèo 3 tuổi 5kg", {("weight", 1000)}),
    ("milk carton 1L for a child weighing 10kg", {("volume", 1000)}),
    ("sữa hộp 450ml, mỗi ngày uống 200ml", {("volume", 450)}),
    ("gói 900g cho mẹ 60kg", {("weight", 900)}),
    ("mua thịt cho 2kg", {("weight", 2000)}),
    ("máy ủ sữa 500g", {("weight", 500)}),
    ("sữa loại 0,75L", {("volume", 750)}),
    ("gạo trọng lượng 1kg", {("weight", 1000)}),
    ("bag weighs 1kg", {("weight", 1000)}),
])
def test_explicit_product_amounts_survive_context_role_filter(text, expected):
    assert consultation._product_sizes(text) == expected


def test_filter_does_not_mutate_customer_text():
    original = "sữa hộp 450ml cho bé 6 tháng 10kg"
    filtered = product_measurement_text(original)
    assert "450ml" in filtered and "10kg" not in filtered
    assert original == "sữa hộp 450ml cho bé 6 tháng 10kg"


@pytest.fixture(scope="module")
def catalog():
    return SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category,incorrect_size", [
    ("Tư vấn tã quần cho bé 6 tháng 10kg", "Tã quần trẻ em", "10kg"),
    ("Tư vấn sữa bột cho bé từ 9 đến 14kg", "Sữa bột trẻ em", "14kg"),
    ("Tư vấn sữa bột chức năng cho mẹ 60kg", "Sữa bột chức năng", "60kg"),
])
async def test_real_catalog_candidates_survive_bad_model_body_weight_filter(
    catalog, monkeypatch, query, category, incorrect_size,
):
    async def structured(_provider, schema, _prompt, data):
        if schema is consultation.ConsultationDecision:
            return consultation.ConsultationDecision(
                action="recommend", need=query,
                searches=[consultation.SearchNeed(category=category)],
                required_size=incorrect_size,
            ), "test"
        assert schema is consultation.GroundedSelection
        assert data["candidates"], "Body weight must not empty the actual catalog category"
        return consultation.GroundedSelection(
            picks=[consultation.GroundedPick(candidate=0)],
            text="Mình gợi ý {name:0} ({price:0}) để bạn cân nhắc theo quy cách bán.",
        ), "test"

    monkeypatch.setattr(consultation, "_structured", structured)
    result = await consultation.consult_customer(
        text=query, record=InMemorySessionStore().create("body-weight"),
        repository=catalog, groq=object(), review_selection=False,
    )
    assert result.products
    assert result.memory["constraints"]["required_size"] is None
    assert all(product.price_vnd > 0 for product in result.products)
