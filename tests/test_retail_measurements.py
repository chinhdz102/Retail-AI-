import pytest

from backend.services.retail_measurements import number_value, package_values
from backend.services.speech_text import vietnamese_integer


@pytest.mark.parametrize("text,dimension,expected", [
    ("chai một lít", "volume_ml", {1000}),
    ("loại một lít rưỡi", "volume_ml", {1500}),
    ("một phẩy năm lít", "volume_ml", {1500}),
    ("chai 1,5L", "volume_ml", {1500}),
    ("năm trăm mi li lít", "volume_ml", {500}),
    ("nửa cân", "mass_g", {500}),
    ("hai ký rưỡi", "mass_g", {2500}),
    ("35 cm", "length_cm", {35}),
    ("dài ba mươi lăm xen ti mét trở lên", "length_cm", {35}),
    ("ba trăm năm mươi mi li mét", "length_cm", {35}),
    ("lốc 2 chai x 500ml", "volume_ml", {500}),
    ("lốc 2x500ml", "volume_ml", {500}),
    ("lốc 2×500ml", "volume_ml", {500}),
    ("lốc 2 x500ml", "volume_ml", {500}),
    ("túi 1.000g", "mass_g", {1000}),
    ("chai 1.250,5ml", "volume_ml", {1250.5}),
    ("chai 0.500L", "volume_ml", {500}),
    ("mã ABx500ml", "volume_ml", set()),
    ("loại 1.2.3 lít", "volume_ml", set()),
    ("một lít", "mass_g", set()),
    ("hai ba trăm gam", "mass_g", set()),
    ("năm mươi mươi lít", "volume_ml", set()),
    ("một lít", "length_cm", set()),
])
def test_explicit_written_and_spoken_sizes_without_rewriting_or_summing(text, dimension, expected):
    assert package_values(text, dimension) == expected


def test_spoken_number_parser_roundtrips_supported_cardinals():
    for value in [*range(1000), 1001, 1005, 1515, 10001, 15050, 100000, 150150]:
        assert number_value(vietnamese_integer(value)) == value
