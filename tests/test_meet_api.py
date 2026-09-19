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


# --- who was in the call ------------------------------------------------------


def _attendance_service(
    participants: list[dict],
    sessions: dict[str, list[dict]] | None = None,
    transcripts: list[dict] | None = None,
    entries: dict[str, list[dict]] | None = None,
) -> MagicMock:
    sessions = sessions or {}
    entries = entries or {}
    service = MagicMock()
    records = MagicMock()
    service.conferenceRecords.return_value = records
    calls: list[dict] = []
    service.calls = calls

    def paged(items_for, key):
        def listing(**kwargs):
            calls.append(dict(kwargs, resource=key))
            request = MagicMock()
            request.execute.return_value = {key: items_for(kwargs.get("parent", ""))}
            return request

        return listing

    participants_resource = MagicMock()
    records.participants.return_value = participants_resource
    participants_resource.list.side_effect = paged(lambda _: participants, "participants")
    sessions_resource = MagicMock()
    participants_resource.participantSessions.return_value = sessions_resource
    sessions_resource.list.side_effect = paged(
        lambda parent: sessions.get(parent, []), "participantSessions"
    )

    transcripts_resource = MagicMock()
    records.transcripts.return_value = transcripts_resource
    transcripts_resource.list.side_effect = paged(lambda _: transcripts or [], "transcripts")
    entries_resource = MagicMock()
    transcripts_resource.entries.return_value = entries_resource
    entries_resource.list.side_effect = paged(
        lambda parent: entries.get(parent, []), "transcriptEntries"
    )
    return service


def _participant(
    name,
    display="Someone",
    *,
    joined="2026-09-18T11:00:00Z",
    left="2026-09-18T11:30:00Z",
    kind="signed-in",
    user="users/1",
):
    body = {"name": name, "earliestStartTime": joined, "latestEndTime": left}
    if kind == "signed-in":
        body["signedinUser"] = {"user": user, "displayName": display}
    elif kind == "anonymous":
        body["anonymousUser"] = {"displayName": display}
    else:
        body["phoneUser"] = {"displayName": display}
    return body


def test_one_participant_is_one_presence():
    service = _attendance_service([_participant("conferenceRecords/c1/participants/p1")])

    found = meet_api.attendance(service, "conferenceRecords/c1")

    assert len(found.people) == 1
    person = found.people[0]
    assert person.display_name == "Someone"
    assert person.user_id == "users/1"
    assert person.windows == (
        (
            dt.datetime(2026, 9, 18, 11, 0, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 9, 18, 11, 30, tzinfo=dt.timezone.utc),
        ),
    )


def test_a_latecomer_keeps_their_own_window():
    """The whole point: a person who joined at minute 20 was not there at minute 5."""
    service = _attendance_service(
        [
            _participant("conferenceRecords/c1/participants/p1", "Early"),
            _participant(
                "conferenceRecords/c1/participants/p2",
                "Late",
                joined="2026-09-18T11:20:00Z",
                user="users/2",
            ),
        ]
    )

    found = meet_api.attendance(service, "conferenceRecords/c1")

    late = next(p for p in found.people if p.display_name == "Late")
    assert late.windows[0][0] == dt.datetime(2026, 9, 18, 11, 20, tzinfo=dt.timezone.utc)


def test_one_session_costs_no_extra_request():
    """A sessions call per participant per recording, for nothing, is the usual case."""
    service = _attendance_service([_participant("conferenceRecords/c1/participants/p1")])

    meet_api.attendance(service, "conferenceRecords/c1")

    assert [call["resource"] for call in service.calls] == ["participants"]


def test_a_rejoin_becomes_two_windows():
    """Between the two they were not in the call, so nothing there can be theirs."""
    service = _attendance_service(
        [
            {
                "name": "conferenceRecords/c1/participants/p1",
                "signedinUser": {"user": "users/1", "displayName": "In and out"},
                "earliestStartTime": "2026-09-18T11:00:00Z",
                "latestEndTime": "2026-09-18T11:30:00Z",
            }
        ],
        sessions={
            "conferenceRecords/c1/participants/p1": [
                {"startTime": "2026-09-18T11:00:00Z", "endTime": "2026-09-18T11:05:00Z"},
                {"startTime": "2026-09-18T11:25:00Z", "endTime": "2026-09-18T11:30:00Z"},
            ]
        },
    )

    found = meet_api.attendance(
        service, "conferenceRecords/c1", sessions_for=lambda person: True
    )

    assert len(found.people[0].windows) == 2
    assert found.people[0].windows[1][0] == dt.datetime(
        2026, 9, 18, 11, 25, tzinfo=dt.timezone.utc
    )


def test_an_anonymous_guest_is_still_a_presence():
    """They were in the room, which is the only question this answers."""
    service = _attendance_service(
        [_participant("conferenceRecords/c1/participants/p1", "Guest", kind="anonymous")]
    )

    found = meet_api.attendance(service, "conferenceRecords/c1")

    assert found.people[0].kind == "anonymous"
    assert found.people[0].user_id == ""
    assert found.people[0].display_name == "Guest"


def test_speech_is_not_read_unless_it_is_asked_for():
    """The cycle only needs who was there; the words cost two more requests."""
    service = _attendance_service([_participant("conferenceRecords/c1/participants/p1")])

    found = meet_api.attendance(service, "conferenceRecords/c1")

    assert all(call["resource"] != "transcripts" for call in service.calls)
    assert found.people[0].spoke is False
    assert found.speech_known is False


def test_who_spoke_comes_from_the_entries_and_not_their_words():
    service = _attendance_service(
        [
            _participant("conferenceRecords/c1/participants/p1", "Talker"),
            _participant("conferenceRecords/c1/participants/p2", "Quiet", user="users/2"),
        ],
        transcripts=[{"name": "conferenceRecords/c1/transcripts/t1"}],
        entries={
            "conferenceRecords/c1/transcripts/t1": [
                {
                    "participant": "conferenceRecords/c1/participants/p1",
                    "text": "words Meet heard wrong",
                }
            ]
        },
    )

    found = meet_api.attendance(service, "conferenceRecords/c1", include_speech=True)

    assert found.speech_known is True
    assert {p.display_name: p.spoke for p in found.people} == {
        "Talker": True,
        "Quiet": False,
    }


def test_a_call_with_no_transcript_leaves_speech_unknown():
    """Nobody spoke and nobody transcribed look alike; saying so is the difference."""
    service = _attendance_service([_participant("conferenceRecords/c1/participants/p1")])

    found = meet_api.attendance(service, "conferenceRecords/c1", include_speech=True)

    assert found.speech_known is False
    assert found.people[0].spoke is False


def test_an_unreadable_conference_is_an_error_not_an_empty_room():
    """Nobody was here and I could not find out lead to opposite decisions."""
    service = _attendance_service([])
    service.conferenceRecords.return_value.participants.return_value.list.side_effect = (
        HttpError(MagicMock(status=403), b'{"error": {"message": "no"}}')
    )

    with pytest.raises(meet_api.MeetError):
        meet_api.attendance(service, "conferenceRecords/c1")


def test_an_empty_conference_is_an_empty_room():
    service = _attendance_service([])

    found = meet_api.attendance(service, "conferenceRecords/c1")

    assert found.people == ()


# --- was anybody actually in the call together --------------------------------


def _presence(name, joined, left, *, user="", kind="signed-in", participant=""):
    return meet_api.Presence(
        display_name=name,
        user_id=user,
        windows=(
            (
                dt.datetime.fromisoformat(joined),
                dt.datetime.fromisoformat(left) if left else None,
            ),
        ),
        kind=kind,
        participant=participant or f"p/{name}",
    )


def _attendance(*people):
    return meet_api.Attendance(conference="conferenceRecords/c1", people=tuple(people))


def test_one_person_was_never_together_with_anyone():
    found = _attendance(
        _presence("Alone", "2026-09-18T11:00:00+00:00", "2026-09-18T11:30:00+00:00", user="users/1")
    )

    assert meet_api.ever_together(found) is False


def test_an_empty_call_was_not_together_either():
    assert meet_api.ever_together(_attendance()) is False


def test_a_single_second_of_overlap_counts():
    """Measured real calls overlap for as little as 15s; a threshold would lose them."""
    found = _attendance(
        _presence("First", "2026-09-18T11:00:00+00:00", "2026-09-18T11:10:01+00:00", user="users/1"),
        _presence("Second", "2026-09-18T11:10:00+00:00", "2026-09-18T11:30:00+00:00", user="users/2"),
    )

    assert meet_api.ever_together(found) is True


def test_two_people_who_never_coincided_are_not_together():
    """The manager waited, gave up, and the client arrived afterwards."""
    found = _attendance(
        _presence("Waited", "2026-09-18T11:00:00+00:00", "2026-09-18T11:10:00+00:00", user="users/1"),
        _presence("Arrived", "2026-09-18T11:20:00+00:00", "2026-09-18T11:30:00+00:00", user="users/2"),
    )

    assert meet_api.ever_together(found) is False


def test_touching_windows_do_not_count_as_together():
    """One left exactly as the other joined: they never saw each other."""
    found = _attendance(
        _presence("Out", "2026-09-18T11:00:00+00:00", "2026-09-18T11:10:00+00:00", user="users/1"),
        _presence("In", "2026-09-18T11:10:00+00:00", "2026-09-18T11:30:00+00:00", user="users/2"),
    )

    assert meet_api.ever_together(found) is False


def test_one_person_on_two_devices_is_still_one_person():
    """Otherwise a laptop plus a phone would make every empty call look attended."""
    found = _attendance(
        _presence("Manager", "2026-09-18T11:00:00+00:00", "2026-09-18T11:30:00+00:00", user="users/1"),
        _presence("Manager", "2026-09-18T11:05:00+00:00", "2026-09-18T11:25:00+00:00", user="users/1"),
    )

    assert meet_api.ever_together(found) is False


def test_two_anonymous_guests_are_two_people():
    """No account to compare, so they are distinct -- which errs towards processing."""
    found = _attendance(
        _presence("", "2026-09-18T11:00:00+00:00", "2026-09-18T11:30:00+00:00",
                  kind="anonymous", participant="p/a"),
        _presence("", "2026-09-18T11:05:00+00:00", "2026-09-18T11:25:00+00:00",
                  kind="anonymous", participant="p/b"),
    )

    assert meet_api.ever_together(found) is True


def test_a_window_still_open_overlaps_whatever_follows():
    """No leaving time means they were still there, so anyone later was with them."""
    found = _attendance(
        _presence("Still here", "2026-09-18T11:00:00+00:00", None, user="users/1"),
        _presence("Later", "2026-09-18T11:20:00+00:00", "2026-09-18T11:30:00+00:00", user="users/2"),
    )

    assert meet_api.ever_together(found) is True


def test_a_rejoin_that_never_coincides_is_still_alone():
    """The gap is what matters, and only the sessions show it."""
    windows = (
        (
            dt.datetime(2026, 9, 18, 11, 0, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 9, 18, 11, 5, tzinfo=dt.timezone.utc),
        ),
        (
            dt.datetime(2026, 9, 18, 11, 25, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 9, 18, 11, 30, tzinfo=dt.timezone.utc),
        ),
    )
    found = _attendance(
        meet_api.Presence("Manager", "users/1", windows, participant="p/1"),
        _presence("Client", "2026-09-18T11:10:00+00:00", "2026-09-18T11:20:00+00:00", user="users/2"),
    )

    assert meet_api.ever_together(found) is False


def test_a_person_with_no_window_at_all_cannot_be_counted_as_present():
    found = _attendance(
        _presence("Known", "2026-09-18T11:00:00+00:00", "2026-09-18T11:30:00+00:00", user="users/1"),
        meet_api.Presence("Unknown", "users/2", (), participant="p/2"),
    )

    assert meet_api.ever_together(found) is False
