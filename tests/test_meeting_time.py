from datetime import datetime, timezone

import pytest

from src.meeting_time import parse_meeting_start


def _utc(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "name, expected",
    [
        # Parenthesized, hyphen date, GMT offset without minutes.
        (
            "cnv-bezm-efi (2026-07-19 10:25 GMT+2).mp4",
            _utc(2026, 7, 19, 8, 25),
        ),
        # Slash date, zone abbreviation, ASCII hyphen delimiters.
        (
            "meeting Sonal Harsh with Oksana ExpertizeMe - 2026/07/02 21:56 CEST - Recording.mp4",
            _utc(2026, 7, 2, 19, 56),
        ),
        # Slash date, offset with minutes, en dash before "Recording".
        (
            "Md Thofiq Imamsab and Ekaterina Popova - 2026/08/06 16:29 GMT+04:00 – Recording.mp4",
            _utc(2026, 8, 6, 12, 29),
        ),
        # Cyrillic prefix, en dash, offset with minutes.
        (
            "30-минутная онлайн-встреча Ekaterina Popova(ExpertizeMe) и Dmitrii  - "
            "2026/08/08 09:00 GMT+04:00 – Recording.mp4",
            _utc(2026, 8, 8, 5, 0),
        ),
        # Locally sanitized copy: "/" and ":" became "_".
        (
            "30-минутная онлайн-встреча Ekaterina Popova(ExpertizeMe) и Зинаида - "
            "2026_07_04 08_59 GMT+04_00 – Recording.mp4",
            _utc(2026, 7, 4, 4, 59),
        ),
        # A negative offset zone abbreviation.
        (
            "Standup - 2026/01/09 14:30 EST - Recording.mp4",
            _utc(2026, 1, 9, 19, 30),
        ),
        # Seconds are tolerated when present.
        (
            "Call - 2026/02/03 07:05:11 UTC - Recording.mp4",
            _utc(2026, 2, 3, 7, 5),
        ),
    ],
)
def test_parses_supported_name_shapes(name, expected):
    assert parse_meeting_start(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # No time block at all.
        "2026-07-15_18-04-32_speedup_small_480.mp4",
        # Date and time but no zone.
        "Call - 2026/02/03 07:05 - Recording.mp4",
        # Unknown zone abbreviation: guessing an offset would silently mis-time matches.
        "Call - 2026/02/03 07:05 XYZ - Recording.mp4",
        # No date at all.
        "just-a-recording.mp4",
        "",
    ],
)
def test_returns_none_when_no_usable_time(name):
    assert parse_meeting_start(name) is None


def test_result_is_timezone_aware():
    result = parse_meeting_start("Call - 2026/02/03 07:05 UTC - Recording.mp4")
    assert result is not None
    assert result.tzinfo is not None
