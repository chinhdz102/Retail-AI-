from pathlib import Path

import pytest

from evaluation.live_groq_stt_benchmark import _mp4_duration_seconds, audio_duration_seconds


@pytest.mark.parametrize(
    ("version", "timescale", "duration", "expected"),
    [
        (0, 44_100, 2_102_940, 47.68571428571428),
        (1, 90_000, 4_500_000, 50.0),
    ],
)
def test_mp4_movie_header_duration(version, timescale, duration, expected):
    end = 40 if version == 1 else 28
    content = bytearray(end)
    content[4:8] = b"mvhd"
    content[8] = version
    if version == 0:
        content[20:24] = timescale.to_bytes(4, "big")
        content[24:28] = duration.to_bytes(4, "big")
    else:
        content[28:32] = timescale.to_bytes(4, "big")
        content[32:40] = duration.to_bytes(8, "big")

    assert _mp4_duration_seconds(bytes(content)) == pytest.approx(expected)
    assert audio_duration_seconds(Path("sample.m4a"), bytes(content)) == pytest.approx(expected)


def test_mp4_duration_returns_none_without_a_valid_movie_header():
    assert _mp4_duration_seconds(b"not an mp4 file") is None
    assert _mp4_duration_seconds(b"mvhd\x02\x00\x00\x00" + bytes(32)) is None
