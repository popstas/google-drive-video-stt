from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

from src import calendar_api

START = dt.datetime(2026, 9, 22, 10, 0, tzinfo=dt.timezone.utc)
OWN = ("expertizeme.org",)


def _service(events: list[dict]) -> MagicMock:
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": events}
    return service


def _event(minutes: int = 0, attendees=(), **extra) -> dict:
    begins = (START + dt.timedelta(minutes=minutes)).isoformat()
    return {
        "start": {"dateTime": begins},
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "attendees": list(attendees),
        **extra,
    }


def _emails(events, start=START, own=OWN):
    return calendar_api.client_emails(
        _service(events), start=start, own_domains=own, window_minutes=15
    )


def test_outside_invitees_of_the_call_are_returned():
    event = _event(attendees=[
        {"email": "kate@expertizeme.org", "organizer": True},
        {"email": "Client@Gmail.com"},
    ])
    assert _emails([event]) == ["client@gmail.com"]


def test_self_resources_and_own_domain_are_dropped():
    event = _event(attendees=[
        {"email": "boss@other.com", "self": True},
        {"email": "room@resource.calendar.google.com", "resource": True},
        {"email": "colleague@EXPERTIZEME.org"},
        {"email": "client@gmail.com"},
        {"email": "client@gmail.com"},
        {"displayName": "no address"},
    ])
    assert _emails([event]) == ["client@gmail.com"]


def test_own_domains_compare_case_insensitively():
    event = _event(attendees=[{"email": "a@expertizeme.org"}, {"email": "b@x.com"}])
    assert _emails([event], own=("ExpertizeMe.ORG",)) == ["b@x.com"]


def test_the_event_nearest_to_the_call_start_wins():
    far = _event(minutes=-12, attendees=[{"email": "earlier@x.com"}])
    near = _event(minutes=3, attendees=[{"email": "this@x.com"}])
    assert _emails([far, near]) == ["this@x.com"]


def test_an_event_without_a_meet_link_is_not_the_call():
    plain = _event(attendees=[{"email": "lunch@x.com"}])
    del plain["hangoutLink"]
    conference = _event(minutes=10, attendees=[{"email": "call@x.com"}])
    del conference["hangoutLink"]
    conference["conferenceData"] = {"entryPoints": [{"entryPointType": "video"}]}
    assert _emails([plain, conference]) == ["call@x.com"]


def test_all_day_and_cancelled_events_are_skipped():
    all_day = {"start": {"date": "2026-09-22"}, "hangoutLink": "x",
               "attendees": [{"email": "day@x.com"}]}
    cancelled = _event(attendees=[{"email": "gone@x.com"}], status="cancelled")
    live = _event(minutes=5, attendees=[{"email": "live@x.com"}])
    assert _emails([all_day, cancelled, live]) == ["live@x.com"]


def test_nothing_in_the_window_means_no_emails():
    assert _emails([]) == []


def test_a_naive_start_is_read_as_utc():
    event = _event(attendees=[{"email": "c@x.com"}])
    assert _emails([event], start=START.replace(tzinfo=None)) == ["c@x.com"]


def test_the_request_asks_the_primary_calendar_around_the_start():
    service = _service([])
    calendar_api.client_emails(service, start=START, own_domains=OWN, window_minutes=15)
    kwargs = service.events.return_value.list.call_args.kwargs
    assert kwargs["calendarId"] == "primary"
    assert kwargs["singleEvents"] is True
    assert kwargs["timeMin"] == "2026-09-22T09:45:00Z"
    assert kwargs["timeMax"] == "2026-09-22T10:15:00Z"


def test_an_event_that_starts_outside_the_window_is_not_the_call():
    """events.list returns everything *overlapping* the window: a long workshop that
    began two hours earlier is not the ad-hoc call recorded in the middle of it."""
    workshop = _event(minutes=-120, attendees=[{"email": "partner@x.com"}])
    assert _emails([workshop]) == []


def test_an_event_with_outsiders_beats_an_internal_one_at_the_same_time():
    """A standing internal sync must not hide the client call booked over it."""
    sync = _event(minutes=0, attendees=[{"email": "kate@expertizeme.org"}])
    client = _event(minutes=5, attendees=[{"email": "client@gmail.com"}])
    assert _emails([sync, client]) == ["client@gmail.com"]
