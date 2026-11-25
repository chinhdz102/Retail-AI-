from xml.etree import ElementTree

import pytest

from backend.services.speech_text import retail_ssml_text, vietnamese_integer


def test_vietnamese_integer_handles_retail_scales_and_reading_rules():
    assert vietnamese_integer(0) == "không"
    assert vietnamese_integer(45_000) == "bốn mươi lăm nghìn"
    assert vietnamese_integer(1_205_004) == "một triệu hai trăm lẻ năm nghìn không trăm lẻ bốn"


def test_tts_aliases_currency_and_units_without_changing_visible_text():
    markup = retail_ssml_text("Gói 300g giá 45.000đ, chai 1,5 lít.", "vi")
    assert '<sub alias="ba trăm gam">300g</sub>' in markup
    assert '<sub alias="bốn mươi lăm nghìn đồng">45.000đ</sub>' in markup
    assert '<sub alias="một phẩy năm lít">1,5 lít</sub>' in markup


def test_tts_escapes_xml_and_does_not_normalize_product_sku():
    markup = retail_ssml_text("SKU A&B-300 không đổi < 5.", "vi")
    assert "A&amp;B-300" in markup
    assert "SKU A&amp;B-300" in markup
    assert "&lt; 5" in markup
    assert "alias=" not in markup


def test_non_vietnamese_text_is_only_escaped():
    assert retail_ssml_text("Price 45.000đ & ready", "en") == "Price 45.000đ &amp; ready"


@pytest.mark.parametrize(
    ("text", "alias"),
    [("1.500g", "một nghìn năm trăm gam"), ("0,5kg", "không phẩy năm ki lô gam"),
     ("1.5L", "một phẩy năm lít"), ("330 ml", "ba trăm ba mươi mi li lít"),
     ("32500₫", "ba mươi hai nghìn năm trăm đồng"),
     ("54.000 VND", "năm mươi tư nghìn đồng"), ("1.005đ", "một nghìn không trăm lẻ năm đồng")],
)
def test_retail_formats_preserve_written_amount_and_expand_spoken_amount(text, alias):
    xml = ElementTree.fromstring(f"<speak>{retail_ssml_text(text, 'vi')}</speak>")
    assert "".join(xml.itertext()) == text
    assert xml[0].attrib["alias"] == alias


@pytest.mark.parametrize("text", [
    "SKU A-300g", "https://example.test/500ml", "ABC500ml", "005g", "12,500đ",
    "9999999999999999đ", "300gram", "300-500g", "-100đ", "12.34.56g",
])
def test_ambiguous_numbers_and_identifiers_remain_literal(text):
    markup = retail_ssml_text(text, "vi")
    assert "<sub " not in markup
    assert "".join(ElementTree.fromstring(f"<speak>{markup}</speak>").itertext()) == text


@pytest.mark.parametrize("value", [-1, 10**15, True])
def test_number_reader_rejects_unsupported_values_without_looping(value):
    with pytest.raises(ValueError):
        vietnamese_integer(value)


@pytest.mark.parametrize(
    ("text", "expected_alias"),
    [
        ("15.900đ/Chai", "mười lăm nghìn chín trăm đồng mỗi chai"),
        ("20.957đ/100g", "hai mươi nghìn chín trăm năm mươi bảy đồng trên một trăm gam"),
        ("49.900đ/lít", "bốn mươi chín nghìn chín trăm đồng mỗi lít"),
        ("22%", "hai mươi hai phần trăm"),
        ("99,7%", "chín mươi chín phẩy bảy phần trăm"),
        ("15-20 phút", "mười lăm đến hai mươi phút"),
        ("110 mg", "một trăm mười mi li gam"),
        ("60,3 kcal", "sáu mươi phẩy ba ki lô ca lo"),
        ("1450 ppm", "một nghìn bốn trăm năm mươi pê pê em"),
        ("CO₂", "xê ô hai"),
        ("P/S", "pê ét"),
        ("3in1", "ba trong một"),
        ("NRC", "nước rửa chén"),
        ("GVS", "giấy vệ sinh"),
        ("KDR", "kem đánh răng"),
        ("NXV", "nước xả vải"),
    ],
)
def test_retail_extended_ssml_aliases_preserve_itertext(text, expected_alias):
    markup = retail_ssml_text(text, "vi")
    xml = ElementTree.fromstring(f"<speak>{markup}</speak>")
    assert "".join(xml.itertext()) == text
    assert xml[0].attrib["alias"] == expected_alias


def test_omo_ng_abbreviation_expands_only_after_omo():
    markup = retail_ssml_text("OMO NG Cửa trước 3,6kg", "vi")
    xml = ElementTree.fromstring(f"<speak>{markup}</speak>")
    assert "".join(xml.itertext()) == "OMO NG Cửa trước 3,6kg"
    assert '<sub alias="nước giặt">NG</sub>' in markup
