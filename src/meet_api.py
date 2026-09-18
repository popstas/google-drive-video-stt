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
        page_token = response.get("nextPageToken")
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
