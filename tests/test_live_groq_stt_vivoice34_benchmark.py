from evaluation.live_groq_stt_vivoice34_benchmark import TARGET_PROVINCES, select_samples


def _row(
    row_idx: int,
    province: str,
    speaker_id: str,
    *,
    transcript: str = "Một câu tiếng Việt dùng để kiểm tra nhận diện giọng nói.",
    duration: float = 30.0,
    audio: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "row_idx": row_idx,
        "province": province,
        "speaker_id": speaker_id,
        "transcript": transcript,
        "duration_s": duration,
        "audio": audio if audio is not None else {"src": f"https://example.test/{row_idx}.wav"},
    }


def test_select_samples_uses_stable_dataset_row_ids_and_one_per_province() -> None:
    rows = [
        _row(index, province, f"speaker-{index}")
        for index, province in enumerate(TARGET_PROVINCES)
    ]
    rows.extend(
        [
            _row(100, TARGET_PROVINCES[0], "speaker-later"),
            _row(101, "Không có trong bộ test", "speaker-other"),
        ]
    )

    samples = select_samples(rows)

    assert [sample["row_idx"] for sample in samples] == list(range(len(TARGET_PROVINCES)))
    assert [sample["province"] for sample in samples] == list(TARGET_PROVINCES)
    assert len({sample["speaker_id"] for sample in samples}) == len(samples)
    assert all(sample["_audio"]["src"].endswith(f"{sample['row_idx']}.wav") for sample in samples)


def test_select_samples_skips_missing_audio_and_out_of_range_duration() -> None:
    province = TARGET_PROVINCES[0]
    rows = [
        _row(1, province, "speaker-quiet", duration=10),
        _row(2, province, "speaker-no-audio", audio={}),
        _row(3, province, "speaker-valid"),
    ]

    samples = select_samples(rows)

    assert len(samples) == 1
    assert samples[0]["row_idx"] == 3
    assert samples[0]["speaker_id"] == "speaker-valid"
