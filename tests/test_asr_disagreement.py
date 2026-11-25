import pytest

from backend.services.asr_disagreement import catalog_anchor_disagreement, needs_confirmation


@pytest.mark.parametrize(
    "left,right",
    [
        ("Chai 1 lít, không phải 100ml.", "CHAI MỘT LÍT KHÔNG PHẢI MỘT TRĂM MI LI LÍT"),
        ("Khoảng 150 nghìn.", "KHOẢNG MỘT TRĂM NĂM MƯƠI NGHÌN"),
        ("Giá 20.000 đồng.", "GIÁ HAI MƯƠI NGHÌN ĐỒNG"),
    ],
)
def test_case_punctuation_and_equivalent_number_formats_do_not_cause_confirmation(left, right):
    confirm, distance = needs_confirmation(left, right, {"avg_logprob": -0.8})
    assert not confirm and distance == 0


@pytest.mark.parametrize(
    "left,right",
    [
        ("Lấy sữa không đường", "Lấy sữa đường"),
        ("Đừng lấy thịt heo nhé", "Lấy thịt heo nhé"),
        ("Lấy loại không đường", "Lấy loại không đá"),
        ("Em không cần loại có mùi nông", "EM KHÔNG CẦN LOẠI CÓ MÙI NỒNG"),
    ],
)
def test_negation_disagreement_requires_review_even_if_provider_confident(left, right):
    confirm, _ = needs_confirmation(left, right, {"avg_logprob": -0.05})
    assert confirm


def test_low_score_alone_does_not_reject_an_agreed_sentence():
    assert not needs_confirmation("Lấy sữa nhé", "LẤY SỮA NHÉ", {"avg_logprob": -0.6})[0]


@pytest.mark.parametrize("left,right", [
    ("Mua vở Tano và bút Lupi nhé", "Mua vở Tamo và bút Lugi nhé"),
    ("Về dự án hỗ trợ thêm mô hình dòng diệt", "VỀ DỰ ÁN HỖ TRỢ THÊM MÔ HÌNH LÀ CÁI RONG RIỀNG"),
])
def test_high_provider_confidence_cannot_hide_substantive_decoder_disagreement(left, right):
    assert needs_confirmation(left, right, {"avg_logprob": -0.01})[0]


def test_a_small_noncritical_word_omission_does_not_force_confirmation():
    assert not needs_confirmation(
        "Mình mua một chai nước để mang đi làm nhé",
        "Mua một chai nước để mang đi làm nhé", {"avg_logprob": -0.01},
    )[0]


def test_disagreement_never_selects_a_replacement_transcript():
    result = needs_confirmation("Có thuốc không", "Lấy bánh mì nhé", {"avg_logprob": -0.7})
    assert result[0] is True and isinstance(result[1], float)


@pytest.mark.parametrize("left,right", [
    ("Mình chỉ có 150 nghìn", "Mình chỉ có năm mươi nghìn"),
    ("Lấy chai 1 lít", "Lấy chai một trăm ml"),
    ("Gói 250g", "Gói năm trăm gam"),
    ("Ngân sách một triệu hai trăm nghìn", "Ngân sách một triệu ba trăm nghìn"),
    ("Lấy chai 1,5l", "Lấy chai hai lít"),
    ("Chai 500ml nhé", "Chai nhé"),
    ("Lấy sữa nhé", "Mang nước giặt sang quầy thu ngân"),
])
def test_critical_amount_or_large_disagreement_needs_confirmation_even_if_confident(left, right):
    assert needs_confirmation(left, right, {"avg_logprob": -0.01})[0]


@pytest.mark.parametrize("left,right", [
    ("Lấy chai 1 lít", "Lấy chai một nghìn ml"),
    ("Gói 1kg", "Gói một nghìn gam"),
    ("Tầm một trăm năm mươi ngàn", "Tầm 150 nghìn"),
    ("Chai 1,5l", "Chai một lít rưỡi"),
    ("Ngân sách một triệu hai trăm nghìn", "Ngân sách 1200000 đồng"),
])
def test_equivalent_units_and_colloquial_numbers_do_not_force_confirmation(left, right):
    assert not needs_confirmation(left, right, {"avg_logprob": -0.1})[0]


@pytest.mark.parametrize("left,right,names,expected", [
    ("Nước giặt OMO với A-RIN loại nào đáng tiền?",
     "NƯỚC GIẶT OMO VỚI ARIEL LOẠI NÀO ĐÁNG TIỀN", ["OMO", "ARIEL"], True),
    ("Cho sữa Vinami không đường", "CHO SỮA VINAMILK KHÔNG ĐƯỜNG", ["VINAMILK"], True),
    ("Mua OMONO", "Mua OMO", ["OMO"], True),
    ("Chọn Lock&Lock", "CHỌN LOCK LOCK", ["Lock&Lock"], False),
    ("Sữa VINAMILK chai 1 lít", "SỮA VINAMILK CHAI MỘT LÍT", ["VINAMILK"], False),
    ("Lấy sữa nhé", "Lấy nước nhé", ["VINAMILK", "ARIEL"], False),
    ("Khăn A", "Khăn nhé", ["A", "B"], False),
])
def test_short_catalog_identity_difference_is_not_hidden_by_global_distance(
    left, right, names, expected,
):
    assert catalog_anchor_disagreement(left, right, names) is expected
