"""Asking Google Meet what was recorded, instead of searching Drive for it.

The walk costs one request per meeting folder per cycle, so it grows with every call
ever held. Meet answers the same question -- "anything new?" -- in one request per
employee, whatever the size of the archive, and names the Drive file each recording
produced.

This module knows nothing about folders, employees or the pipeline. It turns one
already-impersonated Meet client into conferences and their recordings, and reports
what it could not read rather than quietly returning less.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# The documented maximum; the default is 25. In steady state the window holds a
# handful of conferences, so asking for the maximum means one request, not four.
PAGE_SIZE = 100


class MeetError(Exception):
    """A refusal from Meet, said in words an operator can act on."""


@dataclass(frozen=True)
class MeetRecording:
    """One recording of one conference, ready or not.

    ``file_id`` is the Drive file, and it is set only once Meet has finished writing
    it (state ``FILE_GENERATED``); ``STARTED`` and ``ENDED`` carry no destination.
    The distinction is the reason this is a field rather than an absence: discovery
    has to tell "nothing to do" from "not finished yet", or it would move its mark
    past a call whose recording was merely slow.
    """

    name: str
    state: str
    file_id: str | None
    start_time: str
    end_time: str

    @property
    def ready(self) -> bool:
        """Whether the file exists in Drive and can be processed now."""
        return bool(self.file_id)


@dataclass(frozen=True)
class MeetConference:
    """One conference, with whatever recordings it has produced so far."""

    name: str
    space: str
    start_time: str
    end_time: str
    recordings: tuple[MeetRecording, ...] = ()
    # True when Meet named the conference but refused to list its recordings. The
    # difference from "no recordings" matters: an unreadable conference is unfinished
    # business and must hold discovery's mark, while an empty one is done with.
    unreadable: bool = False

    @property
    def ended(self) -> bool:
        """Whether the call is over.

        A conference still in progress has no recordings *yet*; it has not decided
        anything. Only an ended conference with no recordings means nobody recorded.
        """
        return bool(self.end_time)


@dataclass(frozen=True)
class Presence:
    """One participant, and when they were actually in the call.

    ``windows`` is what makes this worth asking for: a person who joined twenty
    minutes in was not there for the first twenty, so nothing said then can be
    theirs. An open end (``None``) means the API did not say when they left, which
    happens while a conference is still running.

    The API gives a display name and an opaque user id, never an address -- there is
    no scope here that maps one to the other. Matching a presence to a configured
    employee is therefore done on the name, by the caller that knows the names.
    """

    display_name: str
    user_id: str
    windows: tuple[tuple[dt.datetime, dt.datetime | None], ...]
    kind: str = "signed-in"
    spoke: bool = False
    # The participant resource, kept so speech can be matched back to them.
    participant: str = ""


@dataclass(frozen=True)
class Attendance:
    """Everyone the API says was in one conference.

    ``speech_known`` is separate from every ``spoke`` flag on purpose: "nobody said
    anything" and "nobody transcribed this call" look identical in a list of silent
    people, and they mean opposite things to anything that decides on them.
    """

    conference: str
    people: tuple[Presence, ...] = ()
    speech_known: bool = False


def _identity_key(person: Presence) -> str:
    """What makes two presences the same human.

    The account when there is one: a manager on a laptop and a phone joins twice, and
    counting that as two people would make every call they waited through look
    attended. Without an account -- a dial-in, an anonymous guest -- each presence is
    its own person, which errs towards processing the call rather than skipping it.
    """
    return person.user_id or person.participant


def ever_together(attendance: Attendance) -> bool:
    """Whether two different people were in the call at the same moment.

    The question behind "did anybody come". Not how long they overlapped: measured
    real calls had as little as fifteen seconds of it, so any duration threshold
    would quietly throw away real conversations. Touching windows -- one leaving
    exactly as the other joins -- are not an overlap; they never saw each other.

    A window with no end is still open, so anyone who joined afterwards was with them.
    """
    windows: list[tuple[dt.datetime, dt.datetime | None, str]] = []
    for person in attendance.people:
        key = _identity_key(person)
        for start, end in person.windows:
            windows.append((start, end, key))
    for i, (start_a, end_a, key_a) in enumerate(windows):
        for start_b, end_b, key_b in windows[i + 1:]:
            if key_a == key_b:
                continue
            latest_start = max(start_a, start_b)
            if end_a is not None and end_a <= latest_start:
                continue
            if end_b is not None and end_b <= latest_start:
                continue
            return True
    return False


def _moment(text: str) -> dt.datetime | None:
    """One of Meet's timestamps as an aware UTC datetime, or ``None``."""
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Meet returned a time I cannot read: %r", text)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _identity(payload: dict) -> tuple[str, str, str]:
    """Who this participant is: display name, user id, and what kind of join it was.

    A dial-in or an anonymous guest has no account, and that is not a reason to leave
    them out: they were in the room, which is the whole question here.
    """
    signed = payload.get("signedinUser") or {}
    if signed:
        return signed.get("displayName", "") or "", signed.get("user", "") or "", "signed-in"
    anonymous = payload.get("anonymousUser") or {}
    if anonymous:
        return anonymous.get("displayName", "") or "", "", "anonymous"
    phone = payload.get("phoneUser") or {}
    return phone.get("displayName", "") or "", "", "phone"


def _windows(
    service, payload: dict, page_size: int, sessions_for: Callable[[dict], bool] | None
) -> tuple[tuple[dt.datetime, dt.datetime | None], ...]:
    """When this participant was in the call.

    ``earliestStartTime`` and ``latestEndTime`` come free with the listing and are
    right for anyone who joined once, which is the normal case. They are *generous*
    for somebody who left and rejoined: the gap is inside the window. Asking for the
    sessions closes the gap and costs one request per participant, so the caller
    decides -- a decision that only tightens windows never makes a call look emptier
    than it was, which is the direction a cost-saving rule must fail in.
    """
    if sessions_for is not None and sessions_for(payload):
        sessions = _pages(
            service.conferenceRecords().participants().participantSessions().list,
            "participantSessions",
            page_size,
            parent=payload.get("name", ""),
        )
        found = []
        for session in sessions:
            start = _moment(session.get("startTime", ""))
            if start is not None:
                found.append((start, _moment(session.get("endTime", ""))))
        if found:
            return tuple(sorted(found))
    start = _moment(payload.get("earliestStartTime", ""))
    if start is None:
        return ()
    return ((start, _moment(payload.get("latestEndTime", ""))),)


def _who_spoke(service, conference: str, page_size: int) -> tuple[set[str], bool]:
    """The participants the transcript attributes speech to.

    Only the ``participant`` of each entry is read. Meet's words are not trustworthy
    -- measured on this domain, every Russian call came back tagged ``en-US`` -- but
    which account a turn belongs to does not come from speech recognition at all.

    Returns ``(speakers, known)``. ``known`` is false when there is no transcript, or
    it holds no entries yet: Meet can report a transcript before its entries are
    readable, and a confident empty answer there would name everybody as silent.
    """
    try:
        transcripts = _pages(
            service.conferenceRecords().transcripts().list,
            "transcripts",
            page_size,
            parent=conference,
        )
        speakers: set[str] = set()
        entries = 0
        for transcript in transcripts:
            for entry in _pages(
                service.conferenceRecords().transcripts().entries().list,
                "transcriptEntries",
                page_size,
                parent=transcript.get("name", ""),
            ):
                entries += 1
                participant = entry.get("participant")
                if participant:
                    speakers.add(participant)
        return speakers, entries > 0
    except HttpError as exc:
        # Who was there is the answer that matters; who spoke is a refinement, and
        # losing it must not lose the rest.
        logger.warning("Could not read who spoke in %s: %s", conference, _message(exc))
        return set(), False


def attendance(
    service,
    conference: str,
    *,
    include_speech: bool = False,
    sessions_for: Callable[[dict], bool] | None = None,
    page_size: int = PAGE_SIZE,
) -> Attendance:
    """Who was in ``conference``, and when.

    One request in the normal case. ``include_speech`` costs two more and is only
    worth it for a call being processed, not for every call in a cycle.

    A failure to list the participants is raised, never returned as an empty room:
    "nobody was here" and "I could not find out" lead to opposite decisions, and a
    caller that cannot tell them apart will eventually skip a real call.
    """
    try:
        payloads = _pages(
            service.conferenceRecords().participants().list,
            "participants",
            page_size,
            parent=conference,
        )
    except HttpError as exc:
        raise _translate(exc) from exc

    speakers: set[str] = set()
    known = False
    if include_speech:
        speakers, known = _who_spoke(service, conference, page_size)

    people = []
    for payload in payloads:
        display_name, user_id, kind = _identity(payload)
        people.append(
            Presence(
                display_name=display_name,
                user_id=user_id,
                windows=_windows(service, payload, page_size, sessions_for),
                kind=kind,
                spoke=payload.get("name", "") in speakers,
                participant=payload.get("name", "") or "",
            )
        )
    return Attendance(conference=conference, people=tuple(people), speech_known=known)


def _rfc3339(moment: dt.datetime) -> str:
    """The moment as Meet wants it: UTC, milliseconds, ``Z``.

    A naive datetime is read as UTC rather than as local time. Every stamp this
    service keeps is already UTC, and guessing the machine's timezone here would
    silently shift the window by hours on a server that is not on UTC.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _message(exc: HttpError) -> str:
    """The sentence Google put in the body, or the exception's own words."""
    try:
        body = json.loads(exc.content.decode("utf-8"))
        return (body.get("error") or {}).get("message") or str(exc)
    except (AttributeError, ValueError):
        return str(exc)


def _translate(exc: HttpError) -> MeetError:
    """Name the fix, where there is one.

    The API being switched off in the service account's own project is the first
    failure a new deployment meets, and Google reports it as a plain 403 -- which
    reads like a permission problem with somebody's data when it is a checkbox in
    our own console.
    """
    text = _message(exc)
    if "has not been used in project" in text or "it is disabled" in text:
        return MeetError(
            "The Google Meet API is not enabled for the project this service account "
            "belongs to. Enable meet.googleapis.com in Google Cloud Console for that "
            f"project and try again. Google said: {text}"
        )
    return MeetError(f"Meet refused the request: {text}")


def _pages(resource_list, key: str, page_size: int, **params) -> list[dict]:
    """Every page of one listing, in the order Meet returned them."""
    items: list[dict] = []
    page_token = None
    while True:
        response = resource_list(pageSize=page_size, pageToken=page_token, **params).execute()
        items.extend(response.get(key) or [])
        token = response.get("nextPageToken")
        # Only a real token continues the listing. Anything else -- absent, empty, or
        # a value this client cannot send back -- ends it, because a loop that trusts
        # whatever it is handed here does not end at all.
        page_token = token if isinstance(token, str) and token else None
        if not page_token:
            return items


def _recording_from(payload: dict) -> MeetRecording:
    destination = payload.get("driveDestination") or {}
    return MeetRecording(
        name=payload.get("name", ""),
        state=payload.get("state", ""),
        file_id=destination.get("file") or None,
        start_time=payload.get("startTime", ""),
        end_time=payload.get("endTime", ""),
    )


def conferences_since(
    service,
    since: dt.datetime,
    *,
    page_size: int = PAGE_SIZE,
) -> list[MeetConference]:
    """The conferences this account has been in since ``since``, with their recordings.

    Conferences are returned whether or not they recorded anything, because "ended
    and nobody recorded" and "still going" are answers discovery needs and cannot
    infer from a list of recordings alone.

    One conference whose recordings cannot be listed is marked ``unreadable`` and the
    rest are still returned: a single backend failure must cost a conference, not the
    whole fleet's cycle. A failure of the listing itself has no such fallback and is
    raised.
    """
    records = service.conferenceRecords()
    try:
        payloads = _pages(
            records.list, "conferenceRecords", page_size,
            filter=f'start_time>="{_rfc3339(since)}"',
        )
    except HttpError as exc:
        raise _translate(exc) from exc

    conferences: list[MeetConference] = []
    for payload in payloads:
        name = payload.get("name", "")
        recordings: tuple[MeetRecording, ...] = ()
        unreadable = False
        try:
            recordings = tuple(
                _recording_from(item)
                for item in _pages(
                    records.recordings().list, "recordings", page_size, parent=name
                )
            )
        except HttpError as exc:
            unreadable = True
            logger.warning(
                "Could not list the recordings of one conference: %s", _message(exc)
            )
        conferences.append(
            MeetConference(
                name=name,
                space=payload.get("space", ""),
                start_time=payload.get("startTime", ""),
                end_time=payload.get("endTime", ""),
                recordings=recordings,
                unreadable=unreadable,
            )
        )
    return conferences
