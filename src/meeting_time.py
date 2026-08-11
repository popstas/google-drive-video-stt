"""Parse a meeting's start time out of a Google Meet recording's Drive name.

The Drive name is the only reliable source for when the call *started*: Drive's
``createdTime`` is when the finished recording landed, i.e. the start plus the call's
own length plus processing, which is 30-60 minutes later for a half-hour meeting.
Matching that against a booked start time with a ±15-minute window would never hit.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Offsets in minutes east of UTC.
#
# This table is a code constant, not config: a wrong offset silently mis-times every
# match for that zone, so it should change under review. Two abbreviations are
# genuinely ambiguous worldwide and are resolved here for the deployments this
# service actually serves: ``IST`` is India (+5:30), not Israel or Ireland, and
# ``CST`` is US Central (-6), not China. Anything not listed yields ``None`` rather
# than a guess.
_ZONE_OFFSET_MINUTES = {
    "UTC": 0,
    "GMT": 0,
    "WET": 0,
    "BST": 60,
    "WEST": 60,
    "CET": 60,
    "CEST": 120,
    "EET": 120,
    "EEST": 180,
    "MSK": 180,
    "IST": 330,
    "EST": -300,
    "EDT": -240,
    "CST": -360,
    "CDT": -300,
    "MST": -420,
    "MDT": -360,
    "PST": -480,
    "PDT": -420,
}

# ``2026/08/06 16:29 GMT+04:00``, ``2026-07-19 10:25 GMT+2``,
# ``2026_07_04 08_59 GMT+04_00``, ``2026/07/02 21:56 CEST``.
#
# The date/time separator is a literal space or ``T`` on purpose: allowing ``_`` too
# would make ``2026-07-15_18-04-32_speedup`` (a locally renamed test file, no zone)
# look like a timestamped meeting.
_MEETING_TIME_RE = re.compile(
    r"(?P<year>\d{4})[-/_](?P<month>\d{2})[-/_](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2})[:_](?P<minute>\d{2})(?:[:_]\d{2})?"
    r"\s*(?P<zone>(?:GMT|UTC)\s*[+-]\s*\d{1,2}(?:[:_]\d{2})?|[A-Z]{2,4})"
)

_OFFSET_RE = re.compile(
    r"(?:GMT|UTC)\s*(?P<sign>[+-])\s*(?P<hours>\d{1,2})(?:[:_](?P<minutes>\d{2}))?"
)


def _zone_offset(zone: str) -> timedelta | None:
    """Resolve a zone token to a UTC offset, or ``None`` when it is unknown."""
    offset_match = _OFFSET_RE.fullmatch(zone)
    if offset_match is not None:
        hours = int(offset_match.group("hours"))
        minutes = int(offset_match.group("minutes") or 0)
        total = hours * 60 + minutes
        if offset_match.group("sign") == "-":
            total = -total
        return timedelta(minutes=total)

    named = _ZONE_OFFSET_MINUTES.get(zone.upper())
    if named is None:
        return None
    return timedelta(minutes=named)


def parse_meeting_start(drive_name: str) -> datetime | None:
    """Return the meeting's start time in UTC, or ``None`` when the name has none.

    A name without a parseable timestamp is not an error: hand-uploaded and renamed
    recordings exist, and the caller treats them as unmatched rather than failing the
    file.
    """
    if not drive_name:
        return None

    match = _MEETING_TIME_RE.search(drive_name)
    if match is None:
        return None

    offset = _zone_offset(match.group("zone"))
    if offset is None:
        logger.info(
            "Unknown timezone %r in recording name %r; treating it as untimed",
            match.group("zone"),
            drive_name,
        )
        return None

    try:
        local = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            tzinfo=timezone(offset),
        )
    except ValueError:
        # e.g. month 13 or day 32 in a name that happens to look like a date.
        logger.info("Unusable date in recording name %r", drive_name)
        return None

    return local.astimezone(timezone.utc)
