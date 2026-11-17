from evaluation.vietnamese_tone_metrics import score, strip_tone


def test_tone_diagnostic_cannot_hide_the_reported_hotpot_error():
    result = score("Em mua thịt ăn lẩu.", "Em mua thịt ăn lấu.")
    assert result["syllable_errors"] == result["tone_only_substitutions"] == 1
    assert result["tone_stripped_syllable_errors"] == 0
    assert result["tone_confusions"] == {"hỏi → sắc": 1}


def test_tone_diagnostic_keeps_vowel_shape_separate_and_normalizes_unicode():
    assert strip_tone("lẩu sữa đỗ") == "lâu sưa đô"
    result = score("sữa bò", "sửa bo")
    assert result["tone_only_substitutions"] == 2
    assert score("lẩu", "lâu")["tone_only_substitutions"] == 1
    assert score("lẩu", "lấu")["vowel_shape_substitutions"] == 0
    assert score("lẩu", "láu")["vowel_shape_substitutions"] == 1
    assert score("sữa", "su\u031b\u0303a")["exact"]


def test_tone_diagnostic_counts_missing_words_and_empty_results_as_errors():
    result = score("không đường", "đường")
    assert result["syllable_errors"] == 1
    assert score("không đường", "")["syllable_errors"] == 2
