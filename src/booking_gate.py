"""Decide whether a recording belongs to a booked call, and remember the answer.

The only module that sees both Drive and the booking journal. Everything it needs to
decide comes from the file's Drive metadata and the config, so the polling loop can
ask about a file without downloading it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src import call_booking, drive
from src.config import Config
from src.meeting_time import parse_meeting_start

logger = logging.getLogger(__name__)

DISABLED = "disabled"
MATCHED = "matched"
UNMATCHED = "unmatched"


@dataclass(frozen=True)
class BookingDecision:
    """What the gate concluded about one recording.

    ``reason`` is only set for ``unmatched`` and exists for the log line: "no-booking"
    and "no-meeting-time" call for very different fixes.
    """

    state: str
    task_id: str = ""
    reason: str = ""

    @property
    def is_matched(self) -> bool:
        return self.state == MATCHED


def resolve(file_info: dict, folder_id: str, config: Config) -> BookingDecision:
    """Match one Drive mp4 against the booking journal."""
    if not config.call_booking_enabled:
        return BookingDecision(state=DISABLED)

    employee = config.folder_by_id(folder_id)
    email = employee.email.strip() if employee else ""
    if not email:
        return BookingDecision(state=UNMATCHED, reason="no-folder-email")

    file_name = file_info.get("name", "")
    video_start = parse_meeting_start(file_name)
    if video_start is None:
        return BookingDecision(state=UNMATCHED, reason="no-meeting-time")

    # A read error propagates: the caller must be able to tell "no bookings" from
    # "could not read the bookings", because only the first may mark a file for good.
    bookings = call_booking.load(config.call_bookings_file)
    booking = call_booking.match(
        bookings,
        email=email,
        video_start=video_start,
        threshold_minutes=config.call_booking_threshold_minutes,
    )
    if booking is None:
        return BookingDecision(state=UNMATCHED, reason="no-booking")

    return BookingDecision(state=MATCHED, task_id=booking.task_id)


def mark_unmatched(service: Any, file_id: str) -> None:
    """Record on Drive that this recording matched no booked call."""
    drive.set_file_app_properties(
        service,
        file_id,
        {drive.BOOKING_MATCH_PROPERTY: drive.BOOKING_MATCH_NONE},
    )


def clear_mark(service: Any, file_id: str) -> None:
    """Remove the unmatched mark so the polling loop reconsiders the recording.

    Drive deletes an appProperty when its value is null, which is what ``gdstt
    bookings rematch`` needs: the file must look untouched, not "explicitly matched".
    """
    drive.set_file_app_properties(
        service, file_id, {drive.BOOKING_MATCH_PROPERTY: None}
    )
