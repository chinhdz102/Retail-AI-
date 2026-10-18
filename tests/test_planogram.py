from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

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
        ROOT / "data" / "winmart_planogram_assignments.json",
        ROOT / "data" / "winmart_planogram_3d_labeled.png",
        output,
    )
    svg = output.read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    text_nodes = [node.text or "" for node in root.iter() if node.tag.endswith("text")]
    titles = [node.text or "" for node in root.iter() if node.tag.endswith("title")]
    payload = json.loads(
        (ROOT / "data" / "winmart_planogram_assignments.json").read_text(
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
        ROOT / "data" / "winmart_products.json",
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
