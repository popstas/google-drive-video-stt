from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from src import meet_api

SINCE = dt.datetime(2026, 9, 18, 10, 0, tzinfo=dt.timezone.utc)


def _service(
    conferences: list[dict],
    recordings: dict[str, list[dict]] | None = None,
    *,
    page: int = 100,
) -> MagicMock:
    """A Meet whose listings honour the filter and the paging the client relies on.

    The start_time filter is applied for real: a client that forgot to send it, or
    sent it in the wrong format, would otherwise pass while asking for the whole
    archive every cycle.
    """
    recordings = recordings or {}
    service = MagicMock()
    records = MagicMock()
    service.conferenceRecords.return_value = records
    calls: list[dict] = []
    service.calls = calls

    def conf_list(**kwargs):
        calls.append(dict(kwargs, resource="conferences"))
        wanted = kwargs.get("filter", "")
        found = conferences
        marker = 'start_time>="'
        if marker in wanted:
            floor = wanted.split(marker, 1)[1].split('"', 1)[0]
            found = [c for c in conferences if c.get("startTime", "") >= floor]
        start = int(kwargs.get("pageToken") or 0)
        size = kwargs.get("pageSize") or page
        chunk = found[start:start + size]
        body: dict = {"conferenceRecords": chunk}
        if start + size < len(found):
            body["nextPageToken"] = str(start + size)
        request = MagicMock()
        request.execute.return_value = body
        return request

    records.list.side_effect = conf_list

    recordings_resource = MagicMock()
    records.recordings.return_value = recordings_resource

    def rec_list(**kwargs):
        calls.append(dict(kwargs, resource="recordings"))
        found = recordings.get(kwargs.get("parent", ""), [])
        start = int(kwargs.get("pageToken") or 0)
        size = kwargs.get("pageSize") or page
        chunk = found[start:start + size]
        body: dict = {"recordings": chunk}
        if start + size < len(found):
            body["nextPageToken"] = str(start + size)
        request = MagicMock()
        request.execute.return_value = body
        return request

    recordings_resource.list.side_effect = rec_list
    return service


def _conference(name: str, start: str, end: str | None = "2026-09-18T12:10:00Z") -> dict:
    body = {"name": name, "startTime": start, "space": "spaces/abc"}
    if end is not None:
        body["endTime"] = end
    return body


def _recording(state: str = "FILE_GENERATED", file_id: str | None = "file-1") -> dict:
    body = {
        "name": "conferenceRecords/c1/recordings/r1",
        "state": state,
        "startTime": "2026-09-18T12:05:00Z",
        "endTime": "2026-09-18T12:09:00Z",
    }
    if file_id is not None:
        body["driveDestination"] = {"file": file_id, "exportUri": "https://example"}
    return body


def test_the_window_is_asked_for_by_start_time():
    """The whole point is not re-reading the archive: the filter must actually go."""
    service = _service(
        [
            _conference("conferenceRecords/old", "2026-09-18T09:00:00Z"),
            _conference("conferenceRecords/new", "2026-09-18T11:00:00Z"),
        ],
        {"conferenceRecords/new": [_recording()]},
    )

    found = meet_api.conferences_since(service, SINCE)

    assert [c.name for c in found] == ["conferenceRecords/new"]
    assert service.calls[0]["filter"] == 'start_time>="2026-09-18T10:00:00.000Z"'


def test_a_naive_moment_is_read_as_utc():
    """Config and files carry naive stamps; sending one as local time shifts the window."""
    service = _service([_conference("conferenceRecords/c", "2026-09-18T11:00:00Z")])

    meet_api.conferences_since(service, dt.datetime(2026, 9, 18, 10, 0))

    assert service.calls[0]["filter"] == 'start_time>="2026-09-18T10:00:00.000Z"'


def test_every_page_is_followed():
    confs = [
        _conference(f"conferenceRecords/c{i}", f"2026-09-18T1{i}:00:00Z") for i in range(5)
    ]
    service = _service(confs, page=2)

    found = meet_api.conferences_since(service, SINCE, page_size=2)

    assert len(found) == 5


def test_a_full_page_is_asked_for():
    """Paging one conference at a time would cost a request per call, for nothing."""
    service = _service([_conference("conferenceRecords/c", "2026-09-18T11:00:00Z")])

    meet_api.conferences_since(service, SINCE)

    assert service.calls[0]["pageSize"] == 100


def test_a_recording_still_being_written_is_returned_as_unfinished():
    """Dropping it would move the mark past a call whose file was merely slow."""
    service = _service(
        [_conference("conferenceRecords/c1", "2026-09-18T11:00:00Z")],
        {"conferenceRecords/c1": [_recording(state="ENDED", file_id=None)]},
    )

    conference = meet_api.conferences_since(service, SINCE)[0]

    assert len(conference.recordings) == 1
    assert conference.recordings[0].file_id is None
    assert conference.recordings[0].ready is False
    assert conference.recordings[0].state == "ENDED"


def test_a_finished_recording_carries_its_drive_file():
    service = _service(
        [_conference("conferenceRecords/c1", "2026-09-18T11:00:00Z")],
        {"conferenceRecords/c1": [_recording()]},
    )

    recording = meet_api.conferences_since(service, SINCE)[0].recordings[0]

    assert recording.file_id == "file-1"
    assert recording.ready is True


def test_a_conference_still_in_progress_says_so():
    """It has no recording because it is still happening, not because nobody recorded."""
    service = _service(
        [_conference("conferenceRecords/live", "2026-09-18T11:00:00Z", end=None)]
    )

    conference = meet_api.conferences_since(service, SINCE)[0]

    assert conference.ended is False
    assert conference.recordings == ()


def test_an_ended_conference_nobody_recorded_is_ended_and_empty():
    service = _service([_conference("conferenceRecords/c1", "2026-09-18T11:00:00Z")])

    conference = meet_api.conferences_since(service, SINCE)[0]

    assert conference.ended is True
    assert conference.recordings == ()


def test_recordings_are_paged_too():
    service = _service(
        [_conference("conferenceRecords/c1", "2026-09-18T11:00:00Z")],
        {"conferenceRecords/c1": [_recording(), _recording(), _recording()]},
        page=2,
    )

    found = meet_api.conferences_since(service, SINCE, page_size=2)

    assert len(found[0].recordings) == 3


def test_nothing_new_costs_one_request():
    service = _service([])

    assert meet_api.conferences_since(service, SINCE) == []
    assert len(service.calls) == 1


def test_a_disabled_api_says_which_api_to_enable():
    """The first error a new deployment meets, and it says nothing about scopes."""
    service = _service([_conference("conferenceRecords/c", "2026-09-18T11:00:00Z")])
    service.conferenceRecords.return_value.list.side_effect = HttpError(
        MagicMock(status=403),
        b'{"error": {"status": "PERMISSION_DENIED", "message": "Google Meet API has '
        b'not been used in project 1234 before or it is disabled."}}',
    )

    with pytest.raises(meet_api.MeetError) as excinfo:
        meet_api.conferences_since(service, SINCE)

    assert "meet.googleapis.com" in str(excinfo.value)
    assert "enable" in str(excinfo.value).lower()


def test_another_refusal_is_passed_on_with_its_own_words():
    service = _service([])
    service.conferenceRecords.return_value.list.side_effect = HttpError(
        MagicMock(status=400), b'{"error": {"message": "Invalid filter was provided"}}'
    )

    with pytest.raises(meet_api.MeetError) as excinfo:
        meet_api.conferences_since(service, SINCE)

    assert "Invalid filter" in str(excinfo.value)


def test_one_conference_failing_does_not_lose_the_others():
    """A single unreadable conference must not cost the cycle every other recording."""
    service = _service(
        [
            _conference("conferenceRecords/bad", "2026-09-18T11:00:00Z"),
            _conference("conferenceRecords/good", "2026-09-18T11:30:00Z"),
        ],
        {"conferenceRecords/good": [_recording()]},
    )
    recordings_resource = service.conferenceRecords.return_value.recordings.return_value
    real = recordings_resource.list.side_effect

    def fail_one(**kwargs):
        if kwargs.get("parent") == "conferenceRecords/bad":
            raise HttpError(MagicMock(status=500), b'{"error": {"message": "backend"}}')
        return real(**kwargs)

    recordings_resource.list.side_effect = fail_one

    found = meet_api.conferences_since(service, SINCE)

    assert [c.name for c in found] == ["conferenceRecords/bad", "conferenceRecords/good"]
    assert found[0].unreadable is True
    assert found[0].recordings == ()
    assert found[1].recordings[0].file_id == "file-1"
