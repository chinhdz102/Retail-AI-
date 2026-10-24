from evaluation.live_groq_stt_vivos_benchmark import select_one_per_speaker


def _row(speaker: str, filename: str, text: str) -> dict[str, object]:
    return {
        "path": f"test/{speaker}/{filename}",
        "text": text,
        "audio": {"src": f"https://example.test/{filename}"},
    }


def test_selects_one_long_audio_reference_per_speaker_deterministically() -> None:
    rows = [
        _row("VIVOSDEV01", "01.wav", "Đây là một câu kiểm tra đủ dài để đưa vào tập mẫu."),
        _row("VIVOSDEV01", "02.wav", "Đây là câu thứ hai cho cùng một người đọc."),
        _row("VIVOSDEV02", "03.wav", "Một người đọc khác cũng có câu đủ dài để chọn."),
    ]

    first = select_one_per_speaker(rows, seed=23)
    second = select_one_per_speaker(rows, seed=23)

    assert [row["_speaker"] for row in first] == ["VIVOSDEV01", "VIVOSDEV02"]
    assert [row["path"] for row in first] == [row["path"] for row in second]
    assert len(first) == len({row["_speaker"] for row in first})


def test_skips_short_transcripts_and_rows_without_audio_urls() -> None:
    rows = [
        _row("VIVOSDEV01", "short.wav", "Câu ngắn."),
        {
            "path": "test/VIVOSDEV02/no-audio.wav",
            "text": "Đây là một câu đủ dài nhưng không có đường dẫn audio.",
            "audio": {},
        },
        _row("VIVOSDEV03", "valid.wav", "Đây là câu hợp lệ có audio và transcript dài."),
    ]

    selected = select_one_per_speaker(rows)

    assert len(selected) == 1
    assert selected[0]["_speaker"] == "VIVOSDEV03"
    assert selected[0]["_audio"]["src"].endswith("valid.wav")


def test_accepts_dataset_viewer_audio_list_shape() -> None:
    row = _row("VIVOSDEV04", "list-audio.wav", "Transcript đủ dài và gắn với audio dạng danh sách.")
    row["audio"] = [{"src": "https://example.test/list-audio.wav"}]

    selected = select_one_per_speaker([row])

    assert len(selected) == 1
    assert selected[0]["_audio"]["src"].endswith("list-audio.wav")
