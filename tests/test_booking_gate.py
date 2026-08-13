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

    mark_unmatched(service, "v1")

    service.files.return_value.update.assert_called_once()
    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {
        "appProperties": {drive.BOOKING_MATCH_PROPERTY: drive.BOOKING_MATCH_NONE}
    }


def test_clear_mark_nulls_the_property():
    service = MagicMock()

    clear_mark(service, "v1")

    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {"appProperties": {drive.BOOKING_MATCH_PROPERTY: None}}
