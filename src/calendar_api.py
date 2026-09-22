"""Who was invited to a call, by address -- the one thing Meet will not say.

The Meet API names participants and gives opaque user ids; the calendar event behind
the call carries the invitees' addresses, a Calendly invitee included (Calendly writes
the booking into the host's calendar). The event is found by time, not by Meet code:
the call's start is exact, and the code would need a Meet request the walk cannot make.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable


def _utc(moment: dt.datetime) -> dt.datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def _rfc3339(moment: dt.datetime) -> str:
    return _utc(moment).isoformat().replace("+00:00", "Z")


def _event_start(event: dict) -> dt.datetime | None:
    """When a timed event begins; ``None`` for an all-day one or an unreadable time."""
    raw = (event.get("start") or {}).get("dateTime")
    if not raw:
        return None
    try:
        return _utc(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def _has_meet(event: dict) -> bool:
    if event.get("hangoutLink"):
        return True
    entry_points = (event.get("conferenceData") or {}).get("entryPoints") or []
    return any(point.get("entryPointType") == "video" for point in entry_points)


def client_emails(
    service,
    *,
    start: dt.datetime,
    own_domains: Iterable[str],
    window_minutes: int,
) -> list[str]:
    """The outside invitees of the Meet event nearest to ``start``, sorted.

    Outside means: not the calendar's owner, not a room, and not at one of
    ``own_domains``. An empty list is the answer for "no such event" and for "only
    colleagues were invited" alike -- the caller has nothing to do in either case.
    """
    start = _utc(start)
    window = dt.timedelta(minutes=window_minutes)
    response = service.events().list(
        calendarId="primary",
        singleEvents=True,
        timeMin=_rfc3339(start - window),
        timeMax=_rfc3339(start + window),
    ).execute()

    nearest: tuple[dt.timedelta, dict] | None = None
    for event in response.get("items") or []:
        if event.get("status") == "cancelled" or not _has_meet(event):
            continue
        begins = _event_start(event)
        if begins is None:
            continue
        distance = abs(begins - start)
        if nearest is None or distance < nearest[0]:
            nearest = (distance, event)
    if nearest is None:
        return []

    own = {domain.strip().lower() for domain in own_domains}
    emails: set[str] = set()
    for attendee in nearest[1].get("attendees") or []:
        if attendee.get("self") or attendee.get("resource"):
            continue
        email = str(attendee.get("email") or "").strip().lower()
        if "@" not in email or email.rsplit("@", 1)[1] in own:
            continue
        emails.add(email)
    return sorted(emails)
