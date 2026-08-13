from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src import drive
from src.booking_gate import BookingDecision, clear_mark, mark_unmatched, resolve
from src.call_booking import CallBooking, append
from src.config import Config, EmployeeFolder

MATCHED_NAME = "Call with Dmitrii - 2026/08/08 09:00 GMT+04:00 – Recording.mp4"


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("", encoding="utf-8")
    return Config(
        folders=(EmployeeFolder(folder_id="f1", name="Kate", email="kate@example.com"),),
        poll_interval=600,
        bitrate="96k",
        data_dir=tmp_path,
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="sk",
        deepgram_api_key="dg",
        stt_language="ru",
        call_booking_enabled=True,
        call_booking_threshold_minutes=15,
        config_file=config_file,
    )


def _seed(config, *, minutes_offset=0, email="kate@example.com", task_id="851030"):
    start = datetime(2026, 8, 8, 5, minutes_offset, tzinfo=timezone.utc)
    append(
        config.call_bookings_file,
        CallBooking(task_id=task_id, manager_email=email, start_time=start),
    )


def test_disabled_feature_short_circuits(config):
    disabled = replace(config, call_booking_enabled=False)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", disabled)

    assert decision.state == "disabled"
    assert decision.is_matched is False


def test_folder_without_email_is_unmatched(config):
    nameless = replace(
        config, folders=(EmployeeFolder(folder_id="f1"),)
    )
    _seed(config)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", nameless)

    assert decision == BookingDecision(state="unmatched", reason="no-folder-email")


def test_unparseable_name_is_unmatched(config):
    _seed(config)

    decision = resolve({"id": "v1", "name": "hand-uploaded.mp4"}, "f1", config)

    assert decision == BookingDecision(state="unmatched", reason="no-meeting-time")


def test_no_booking_in_window_is_unmatched(config):
    _seed(config, minutes_offset=40)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", config)

    assert decision == BookingDecision(state="unmatched", reason="no-booking")


def test_empty_journal_is_unmatched(config):
    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", config)

    assert decision == BookingDecision(state="unmatched", reason="no-booking")


def test_booking_in_window_matches(config):
    _seed(config, minutes_offset=5)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", config)

    assert decision.state == "matched"
    assert decision.task_id == "851030"
    assert decision.is_matched is True


def test_mark_unmatched_writes_the_property():
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "modifiedTime": "2026-01-02T03:04:05.678Z"
    }

    mark_unmatched(service, "v1")

    service.files.return_value.update.assert_called_once()
    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {
        "appProperties": {drive.BOOKING_MATCH_PROPERTY: drive.BOOKING_MATCH_NONE},
        "modifiedTime": "2026-01-02T03:04:05.678Z",
    }


def test_clear_mark_nulls_the_property():
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "modifiedTime": "2026-01-02T03:04:05.678Z"
    }

    clear_mark(service, "v1")

    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {
        "appProperties": {drive.BOOKING_MATCH_PROPERTY: None},
        "modifiedTime": "2026-01-02T03:04:05.678Z",
    }


def test_select_stale_marks_picks_marked_files_whose_date_drifted():
    from src.booking_gate import select_stale_marks

    files = [
        {
            "id": "v1",
            "name": "old call.mp4",
            "createdTime": "2025-03-14T18:24:52.949Z",
            "modifiedTime": "2026-08-11T22:13:27.539Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert select_stale_marks(files) == [
        ("v1", "old call.mp4", "2025-03-14T18:24:52.949Z")
    ]


def test_select_stale_marks_skips_files_without_the_mark():
    """Drive nudges modifiedTime a fraction of a second at upload.

    Those files were never touched by us, and resetting them would rewrite real
    history. The mark -- not a time window -- is what identifies our writes.
    """
    from src.booking_gate import select_stale_marks

    files = [
        {
            "id": "v2",
            "name": "untouched.mp4",
            "createdTime": "2026-08-10T15:09:02.818Z",
            "modifiedTime": "2026-08-10T15:09:03.633Z",
            "appProperties": {},
        }
    ]

    assert select_stale_marks(files) == []


def test_select_stale_marks_skips_already_restored_files():
    """A second run must be a no-op, so the repair can be re-run safely."""
    from src.booking_gate import select_stale_marks

    files = [
        {
            "id": "v3",
            "name": "restored.mp4",
            "createdTime": "2025-03-14T18:24:52.949Z",
            "modifiedTime": "2025-03-14T18:24:52.949Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert select_stale_marks(files) == []


def test_select_stale_marks_compares_times_not_strings():
    """Two spellings of the same instant must not read as drift.

    Drive varies the fractional-second width. Compared as text, ".5Z" sorts above
    ".50Z" -- 'Z' outranks '0' -- so a string comparison calls these two equal
    instants a drift and would rewrite a file that nobody touched. Note the widths:
    createdTime is the longer one, which is the only ordering that catches the bug.
    """
    from src.booking_gate import select_stale_marks

    files = [
        {
            "id": "v4",
            "name": "equal.mp4",
            "createdTime": "2025-03-14T18:24:52.50Z",
            "modifiedTime": "2025-03-14T18:24:52.5Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert select_stale_marks(files) == []


def test_select_stale_marks_skips_files_with_unparseable_times():
    from src.booking_gate import select_stale_marks

    files = [
        {
            "id": "v5",
            "name": "broken.mp4",
            "createdTime": "not-a-date",
            "modifiedTime": "2026-08-11T22:13:27.539Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert select_stale_marks(files) == []
