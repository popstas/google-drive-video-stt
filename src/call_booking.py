"""The upcoming-call journal: one JSON object per line, appended as bookings arrive.

Append-only is deliberate. Bookings arrive from an external system at unpredictable
times, and a rescheduled call is simply a newer line for the same ``task_id`` — no
read-modify-write, so a crash mid-write costs at most the line being written, and the
file greps by eye when someone asks why a recording did not match.
"""

from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Bookings older than this are dropped on read. A constant rather than a config key:
# the journal only exists to match a recording that lands hours after its call, and no
# deployment needs to tune that.
RETENTION_DAYS = 30


@dataclass(frozen=True)
class CallBooking:
    """One upcoming call: which Planfix task, whose calendar, and when it starts."""

    task_id: str
    manager_email: str
    start_time: datetime  # timezone-aware, UTC


def _to_dict(booking: CallBooking) -> dict[str, str]:
    return {
        "task_id": booking.task_id,
        "manager_email": booking.manager_email,
        "start_time": booking.start_time.astimezone(timezone.utc).isoformat(),
    }


def _from_dict(raw: object) -> CallBooking | None:
    if not isinstance(raw, dict):
        return None
    task_id = raw.get("task_id")
    manager_email = raw.get("manager_email")
    start_time = raw.get("start_time")
    if not isinstance(task_id, str) or not isinstance(manager_email, str):
        return None
    if not isinstance(start_time, str):
        return None
    try:
        parsed = datetime.fromisoformat(start_time)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return CallBooking(
        task_id=task_id,
        manager_email=manager_email,
        start_time=parsed.astimezone(timezone.utc),
    )


def append(path: Path, booking: CallBooking) -> None:
    """Append one booking to the journal, serialized under an exclusive lock.

    The receiver is threaded, so two bookings can arrive at once; the lock is what
    keeps their lines from interleaving into a torn record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_to_dict(booking), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load(path: Path, *, now: datetime | None = None) -> list[CallBooking]:
    """Read the journal, dropping stale entries and deduplicating by ``task_id``.

    A missing file is an empty journal, not an error — no booking has arrived yet. An
    I/O error propagates on purpose: the caller must be able to tell "no bookings"
    from "could not read the bookings", because only the first one may mark a
    recording as permanently skipped.
    """
    if not path.exists():
        return []

    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(days=RETENTION_DAYS)

    by_task_id: dict[str, CallBooking] = {}
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed booking on line %d", lineno)
                continue
            booking = _from_dict(raw)
            if booking is None:
                logger.warning("Skipping incomplete booking on line %d", lineno)
                continue
            if booking.start_time < cutoff:
                continue
            # Last line wins: a rescheduled call arrives as a newer entry.
            by_task_id[booking.task_id] = booking

    return list(by_task_id.values())


def match(
    bookings: list[CallBooking],
    *,
    email: str,
    video_start: datetime,
    threshold_minutes: int,
) -> CallBooking | None:
    """Return the manager's booking closest to ``video_start`` within the threshold.

    Nearest-wins rather than first-wins so two back-to-back calls by the same manager
    resolve to the right task instead of whichever happened to be read first.
    """
    if not email:
        return None

    wanted = email.strip().lower()
    window = timedelta(minutes=threshold_minutes)
    candidates = [
        booking
        for booking in bookings
        if booking.manager_email.strip().lower() == wanted
        and abs(booking.start_time - video_start) <= window
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: abs(b.start_time - video_start))
