from evaluation.live_groq_stt_benchmark import accuracy


def test_accuracy_normalizes_equivalent_vietnamese_number_and_unit_forms() -> None:
    result = accuracy(
        "Sữa Vinamilk không đường có loại một lít không?",
        "Sữa Vinamilk không đường có loại 1L không?",
    )

    assert result["wer"] == 0
    assert result["cer"] == 0


def test_accuracy_normalizes_spoken_and_written_price_numbers() -> None:
    result = accuracy(
        "Có mì ly ăn liền dưới hai mươi nghìn không?",
        "Có mì ly ăn liền dưới 20.000 không?",
    )

    assert result["wer"] == 0
    assert result["cer"] == 0


def test_accuracy_normalizes_brand_ampersand_as_spoken_and() -> None:
    result = accuracy(
        "Nồi cơm điện Lock and Lock nằm ở khu nào?",
        "Nồi cơm điện Lock&Lock nằm ở khu nào?",
    )

    assert result["wer"] == 0
    assert result["cer"] == 0
