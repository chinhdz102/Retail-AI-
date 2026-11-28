from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from backend.products.snapshot_repository import SnapshotProductRepository
from scripts.generate_planogram import aisle_sign_lines, generate, safe_text
from scripts.generate_planogram_endcap_labels import generate_endcap_overlay

ROOT = Path(__file__).resolve().parents[1]


def test_aisle_sign_names_come_from_catalog_subdepartments() -> None:
    assert aisle_sign_lines("Hoá mỹ phẩm") == ("HOÁ MỸ PHẨM",)
    assert aisle_sign_lines("Thực phẩm công nghệ") == ("THỰC PHẨM", "CÔNG NGHỆ")
    assert aisle_sign_lines("Thực phẩm Tươi sống, Chế biến") == (
        "THỰC PHẨM",
        "TƯƠI SỐNG,",
        "CHẾ BIẾN",
    )


def test_concept_overlay_puts_catalog_code_on_every_individual_aisle_endcap(
    tmp_path: Path,
) -> None:
    output = tmp_path / "endcaps.svg"
    generate_endcap_overlay(
        ROOT / "data" / "thanh_lam_planogram_assignments.json",
        ROOT / "data" / "thanh_lam_planogram_3d_labeled.png",
        output,
    )
    svg = output.read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    text_nodes = [node.text or "" for node in root.iter() if node.tag.endswith("text")]
    titles = [node.text or "" for node in root.iter() if node.tag.endswith("title")]
    payload = json.loads(
        (ROOT / "data" / "thanh_lam_planogram_assignments.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(payload["aisles"]) == 13
    for aisle in payload["aisles"]:
        assert aisle["aisle"] in text_nodes
        assert f'{aisle["aisle"]} · {aisle["sub_department"]}' in titles


def test_planogram_generation_keeps_catalog_rows_and_labels_all_aisles(
    tmp_path: Path,
) -> None:
    result = generate(
        ROOT / "data" / "thanh_lam_products.json",
        tmp_path / "assignments.json",
        tmp_path / "assignments.csv",
        tmp_path / "planogram.svg",
    )
    svg = (tmp_path / "planogram.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    text_nodes = [node.text or "" for node in root.iter() if node.tag.endswith("text")]

    aisle_codes = {
        node
        for node in text_nodes
        if re.fullmatch(r"A\d{2}", node)
    }
    assert aisle_codes == {f"A{number:02d}" for number in range(1, 14)}
    for aisle in result["aisles"]:
        assert f'{aisle["aisle"]} · {aisle["sub_department"]}' in text_nodes
        for label_line in aisle_sign_lines(aisle["sub_department"]):
            assert label_line in text_nodes

    first_product = result["assignments"][0]
    assert safe_text(first_product["name"]) in svg
    assert result["counts"]["assigned_products"] == 3081
    assert result["counts"]["excluded_quarantine_products"] == 18
    assert result["source_dataset_sha256"] in svg
    assert result["source_dataset_canonical_sha256"] == hashlib.sha256(
        ROOT.joinpath("data", "thanh_lam_products.json")
        .read_bytes()
        .replace(b"\r\n", b"\n")
        .replace(b"\r", b"\n")
    ).hexdigest()


def test_snapshot_resolves_planogram_for_lf_catalog_checkout(tmp_path: Path) -> None:
    source_path = ROOT / "data" / "thanh_lam_products.json"
    linux_style_path = tmp_path / "thanh_lam_products.json"
    linux_style_path.write_bytes(
        source_path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    )
    repository = SnapshotProductRepository(linux_style_path)

    payload = json.loads(
        (ROOT / "data" / "thanh_lam_planogram_assignments.json").read_text(
            encoding="utf-8"
        )
    )
    assignment = next(
        item
        for item in payload["assignments"]
        if item["placement_status"] == "RECOMMENDED_SYNTHETIC_PLANOGRAM"
    )
    product = repository.get_product_snapshot(assignment["product_id"])

    assert product is not None
    assert product.shelf == assignment["location_code"]
    assert product.shelf_source == "RECOMMENDED_PLANOGRAM"


def test_describe_planogram_location_covers_all_13_aisles_and_fails_closed() -> None:
    from backend.products.planogram import describe_planogram_location

    for aisle_num in range(1, 14):
        code = f"A{aisle_num:02d}-B01-L03-P04"
        details = describe_planogram_location(code)
        assert details is not None, f"Missing guide for {code}"
        assert details.aisle == f"A{aisle_num:02d}"
        assert details.zone_vi
        assert details.direction_vi
        assert "ngang tầm mắt" in details.level_phrase("vi")
        assert code in details.detailed_sentence("Sản phẩm thử nghiệm", "vi")
        assert "sơ đồ kệ đề xuất" in details.detailed_sentence("Sản phẩm thử nghiệm", "vi").casefold()
        assert "chưa xác minh" in details.detailed_sentence("Sản phẩm thử nghiệm", "vi").casefold()

    # Check all 5 shelf heights match L1 bottom -> L5 top
    assert "dưới cùng" in describe_planogram_location("A10-B05-L01-P01").level_phrase("vi")
    assert "dưới tầm mắt" in describe_planogram_location("A10-B05-L02-P01").level_phrase("vi")
    assert "ngang tầm mắt" in describe_planogram_location("A10-B05-L03-P01").level_phrase("vi")
    assert "cao hơn tầm mắt" in describe_planogram_location("A10-B05-L04-P01").level_phrase("vi")
    assert "trên cùng" in describe_planogram_location("A10-B05-L05-P01").level_phrase("vi")

    # Non-planogram / unmapped codes must fail closed without inventing walking directions
    for unmapped in (None, "", "A2-L2", "A1-B2", "C1-L3", "A99-B01-L01-P01"):
        assert describe_planogram_location(unmapped) is None


def test_shelf_rendering_and_multi_candidates_explain_directions_clearly() -> None:
    from backend.api.main import _localized_location_candidates
    from backend.products.catalog import DEMO_PRODUCTS
    from backend.services.retail_response_style import render_verified_snapshot

    base = DEMO_PRODUCTS[0]
    coke = base.model_copy(
        update={
            "product_id": "TLM_10194711",
            "name": "Nước ngọt Coca-Cola Zero chai 600ml",
            "shelf": "A10-B05-L03-P04",
            "shelf_source": "RECOMMENDED_PLANOGRAM",
        }
    )
    single_vi = render_verified_snapshot(coke, "vi", ("shelf",))
    assert "Thực phẩm công nghệ" in single_vi
    assert "dãy A10" in single_vi
    assert "khoang 5/8" in single_vi
    assert "tầng 3" in single_vi and "ngang tầm mắt" in single_vi
    assert "ô số 4" in single_vi
    assert "A10-B05-L03-P04" in single_vi
    assert "sơ đồ kệ đề xuất" in single_vi.casefold()
    assert "chưa xác minh" in single_vi.casefold()

    single_en = render_verified_snapshot(coke, "en", ("shelf",))
    assert "Packaged Food & Grocery" in single_en
    assert "aisle A10" in single_en
    assert "eye level" in single_en
    assert "A10-B05-L03-P04" in single_en

    # Same aisle & same bay multi-variant
    coke2 = coke.model_copy(
        update={"name": "Nước ngọt Coca-Cola Zero lon 320ml", "shelf": "A10-B05-L02-P02"}
    )
    same_bay = _localized_location_candidates((coke, coke2), "vi")
    assert "Thực phẩm công nghệ" in same_bay
    assert "dãy A10" in same_bay
    assert "khoang 5/8" in same_bay
    assert "A10-B05-L03-P04" in same_bay and "A10-B05-L02-P02" in same_bay
    assert "chưa được xác minh" in same_bay

    # Same aisle, different bays
    water = coke.model_copy(
        update={"name": "Nước khoáng LaVie 500ml", "shelf": "A10-B07-L01-P03"}
    )
    same_aisle = _localized_location_candidates((coke, water), "vi")
    assert "đều nằm ở khu Thực phẩm công nghệ" in same_aisle
    assert "khoang 5/8" in same_aisle and "khoang 7/8" in same_aisle

    # Different aisles
    fresh = coke.model_copy(
        update={"name": "Thịt thăn bò", "shelf": "A13-B05-L03-P01"}
    )
    diff_aisle = _localized_location_candidates((coke, fresh), "vi")
    assert "dãy A10" in diff_aisle and "dãy A13" in diff_aisle
    assert "Thực phẩm tươi sống, chế biến" in diff_aisle
