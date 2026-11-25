"""Form observations are quoted catalog wording, not generated suitability."""

from pathlib import Path

import pytest

from backend.products.catalog import ProductSnapshot
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services.product_form import product_form_evidence


def product(name, **kwargs):
    return ProductSnapshot(product_id="arbitrary-id", name=name, category="Unseen group",
                           price_vnd=1000, stock=1, **kwargs)


def forms(name):
    return {(row["value"], row["subject"]) for row in product_form_evidence(product(name))}


@pytest.mark.parametrize("title,value,subject", [
    ("Sổ lò co dọc A5", "spiral_binding", "product"),
    ("Vở lò xo 100 trang", "spiral_binding", "product"),
    ("Thịt dê xay 250g", "ground_or_minced", "product"),
    ("Bánh bao nhân bò xay 300g", "ground_or_minced", "mentioned_component"),
    ("Bánh bao nhân bò xay 300g", "filled_product", "product"),
    ("Xốt cà chua thịt heo băm 400g", "ground_or_minced", "mentioned_component"),
    ("Xốt cà chua thịt heo băm 400g", "sauce", "product"),
    ("Mì thịt heo băm 400g", "ground_or_minced", "mentioned_component"),
    ("Dụng cụ băm nhỏ rau", "ground_or_minced", "tool_function"),
    ("Máy xay cầm tay", "ground_or_minced", "tool_function"),
    ("Thịt dê cắt lát 250g", "sliced", "product"),
    ("Bò viên gân 500g", "formed_food_pieces", "product"),
    ("Bột bánh pha sẵn 200g", "powder", "product"),
    ("Sữa bột pha sẵn chai 180ml", "powder", "product_line_descriptor"),
    ("Cà phê hòa tan rang xay", "ground_or_minced", "product_line_descriptor"),
    ("Bánh que thịt heo xông khói", "smoked", "mentioned_component"),
    ("Thịt vịt xông khói", "smoked", "product"),
    ("Sữa rửa mặt dạng gel", "gel", "product"),
    ("Kem dưỡng da 50g", "cream", "product"),
    ("Serum dưỡng thể", "serum", "product"),
    ("Kem khử mùi serum", "serum", "product_line_descriptor"),
    ("Túi châm nước rửa tay", "refill", "product"),
    ("Bộ phụ kiện thay thế máy lọc", "accessory", "product"),
    ("Lõi thay thế cây lau sàn", "replacement_part", "product"),
    ("Túi đựng máy xay", "container_or_cover", "product"),
    ("Ruột gối 40x60cm", "insert", "product"),
    ("Bình xịt vệ sinh bếp", "spray_wording", "product"),
    ("Mì ăn liền vị cá", "instant_wording", "product"),
    ("Rong biển tẩm gia vị ăn liền", "instant_wording", "product"),
    ("Bánh vị phô mai", "flavour_or_scent_wording", "descriptor"),
    ("Ready fully cooked chicken", "fully_cooked_wording", "product"),
])
def test_literal_forms_and_component_scope(title, value, subject):
    assert (value, subject) in forms(title)


@pytest.mark.parametrize("title", [
    "Bánh quy hạnh nhân", "Hạt hạnh nhân", "Bánh vị nhân sâm", "Dầu gội nhân sâm",
    "Sữa dưỡng thể nhãn xanh", "Hạt điều nguyên vỏ", "Giấy vệ sinh không lõi",
    "Băng vệ sinh lõi bông", "Máy xây dựng mini",
])
def test_homonyms_are_not_filling_or_replacement_evidence(title):
    assert not {"filled_product", "replacement_part", "ground_or_minced"} & {
        value for value, _subject in forms(title)
    }


def test_title_observations_do_not_import_category_or_description_claims():
    row = product("Sản phẩm mới", category_hierarchy="Thịt > Sơ chế > Thịt thái lát",
                  description="đã nấu chín; safe for cats; cure acne; sliced")
    assert product_form_evidence(row) == []


@pytest.mark.parametrize("title,absent", [
    ("Thịt không xay", "ground_or_minced"),
    ("Bánh bao không nhân", "filled_product"),
    ("Thịt chưa đông lạnh", "frozen"),
    ("Sản phẩm không phải dạng bột", "powder"),
    ("Non-frozen meat", "frozen"),
    ("Sổ không lò xo A5", "spiral_binding"),
    ("Bộ lò xo máy bơm", "spiral_binding"),
])
def test_negated_forms_are_not_positive_observations(title, absent):
    assert absent not in {value for value, _subject in forms(title)}


def test_negation_of_other_attribute_does_not_remove_actual_form():
    assert ("powder", "product") in forms("Không đường dạng bột")
    assert ("ground_or_minced", "product") in forms("Thịt không xay nhưng băm nhỏ")


def test_instant_wording_does_not_establish_ready_to_eat_or_preparation():
    evidence = product_form_evidence(product("Mì ăn liền hương gà 80g"))
    assert {row["value"] for row in evidence} == {
        "instant_wording", "flavour_or_scent_wording",
    }
    assert all("ready" not in row["value"] and "safe" not in row["value"] for row in evidence)


def test_unrelated_product_identity_and_price_cannot_change_projection():
    original = product("Thịt dê xay 250g")
    variant = original.model_copy(update={"product_id": "new", "price_vnd": 90000,
                                          "category": "Different department"})
    assert product_form_evidence(original) == product_form_evidence(variant)


def test_returned_observations_are_fresh_and_every_quote_is_literal():
    row = product("Bánh bao nhân bò xay đông lạnh")
    first = product_form_evidence(row)
    assert all(observation["quote"] in row.name for observation in first)
    first[0]["value"] = "corrupted"
    assert "corrupted" not in {item["value"] for item in product_form_evidence(row)}


def test_real_snapshot_observations_are_bounded_literal_and_nonprescriptive():
    catalog = SnapshotProductRepository(
        Path(__file__).resolve().parents[1] / "data/thanh_lam_products.json",
    )
    observed = 0
    for row in catalog.products:
        evidence = product_form_evidence(row)
        observed += bool(evidence)
        assert len(evidence) <= 16
        for item in evidence:
            assert item["quote"] in row.name
            assert item["source_field"] == "name"
            assert item["evidence_scope"] == "CATALOG_TITLE"
            assert set(item) == {
                "dimension", "value", "quote", "subject", "source_field", "evidence_scope",
            }
    assert observed > 300
