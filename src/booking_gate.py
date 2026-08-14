"""Decide whether a recording belongs to a booked call, and remember the answer.

The only module that sees both Drive and the booking journal. Everything it needs to
decide comes from the file's Drive metadata and the config, so the polling loop can
ask about a file without downloading it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src import call_booking, drive
from src.config import Config, NameRule
from src.meeting_time import parse_meeting_start

logger = logging.getLogger(__name__)

DISABLED = "disabled"
MATCHED = "matched"
UNMATCHED = "unmatched"

# ``reason`` for a match a name rule forced, as opposed to one the journal produced.
NAME_RULE = "name-rule"


@dataclass(frozen=True)
class BookingDecision:
    """What the gate concluded about one recording.

    ``reason`` explains the state for the log line: for ``unmatched``, "no-booking"
    and "no-meeting-time" call for very different fixes; a ``matched`` decision
    carries it only when a name rule, not the journal, produced the task.
    """

    state: str
    task_id: str = ""
    reason: str = ""

    @property
    def is_matched(self) -> bool:
        return self.state == MATCHED


def _match_name_rule(file_name: str, config: Config) -> NameRule | None:
    """Return the first name rule the recording matches, or None.

    Order is priority: the config lists the rules the operator wants tried first.
    """
    for rule in config.call_booking_name_rules:
        if rule.pattern.search(file_name):
            return rule
    return None


def resolve(file_info: dict, folder_id: str, config: Config) -> BookingDecision:
    """Match one Drive mp4 against the name rules, then the booking journal."""
    file_name = file_info.get("name", "")

    # Before the ``enabled`` gate on purpose: a name rule is a routing mechanism in its
    # own right. With the receiver off, every decision below is ``disabled`` with no
    # task id, so a deployment that only wants "this recording always goes to task X"
    # would never get a Planfix comment. The rule also outranks the journal: it is
    # explicit operator intent, and a demo call must not be re-routed by a real
    # booking that happened to line up with it.
    rule = _match_name_rule(file_name, config)
    if rule is not None:
        logger.info(
            "Name rule %r matched %s: forcing processing into task %s",
            rule.pattern.pattern, file_name, rule.task_id,
        )
        return BookingDecision(state=MATCHED, task_id=rule.task_id, reason=NAME_RULE)

    if not config.call_booking_enabled:
        return BookingDecision(state=DISABLED)

    employee = config.folder_by_id(folder_id)
    email = employee.email.strip() if employee else ""
    if not email:
        return BookingDecision(state=UNMATCHED, reason="no-folder-email")

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


def _parse_drive_time(value: object) -> datetime | None:
    """Parse an RFC 3339 Drive timestamp, or None when it is missing or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def select_stale_marks(files: Iterable[dict]) -> list[tuple[str, str, str]]:
    """Pick the recordings whose modifiedTime our own unmatched mark moved.

    Returns ``(file_id, name, created_time)`` for each file that carries the
    ``booking_match`` property and whose modifiedTime sits past its createdTime.

    The property is the entire predicate. A timestamp window would have caught only
    the files written in one particular run, and would sweep up recordings Drive
    itself nudged at upload -- files nobody here ever wrote to. Selecting on our own
    mark keeps the repair to files we are certain we touched.

    Files already at ``modifiedTime == createdTime`` are skipped, so re-running the
    repair writes nothing.
    """
    selected: list[tuple[str, str, str]] = []
    for item in files:
        properties = item.get("appProperties") or {}
        if drive.BOOKING_MATCH_PROPERTY not in properties:
            continue
        created_raw = item.get("createdTime")
        created = _parse_drive_time(created_raw)
        modified = _parse_drive_time(item.get("modifiedTime"))
        if created is None or modified is None or modified <= created:
            continue
        selected.append((item["id"], item.get("name", ""), created_raw))
    return selected
