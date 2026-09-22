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

    own = {domain.strip().lower() for domain in own_domains}
    best: tuple[tuple[bool, dt.timedelta], list[str]] | None = None
    for event in response.get("items") or []:
        if event.get("status") == "cancelled" or not _has_meet(event):
            continue
        begins = _event_start(event)
        # The listing returns every event *overlapping* the window, so a workshop
        # that began hours earlier is in it; only one that starts near the call is
        # the call.
        if begins is None or abs(begins - start) > window:
            continue
        emails = _outsiders(event, own)
        # An event with outsiders first, then the nearest: a standing internal sync
        # at the same slot must not hide the client call booked over it.
        rank = (not emails, abs(begins - start))
        if best is None or rank < best[0]:
            best = (rank, emails)
    return best[1] if best else []


def _outsiders(event: dict, own: set[str]) -> list[str]:
    """The event's attendees that are not its owner, a room, or a colleague."""
    emails: set[str] = set()
    for attendee in event.get("attendees") or []:
        if attendee.get("self") or attendee.get("resource"):
            continue
        email = str(attendee.get("email") or "").strip().lower()
        if "@" not in email or email.rsplit("@", 1)[1] in own:
            continue
        emails.add(email)
    return sorted(emails)
