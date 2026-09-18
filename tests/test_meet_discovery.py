"""Discovery that asks Meet, and the one rule that makes it safe: the mark.

The mark says "everything that started before this is dealt with". Every test here
is about the difference between that and "I looked at this time" -- which is the
difference between finding a slow recording and losing it.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from src import delegation, main, meet_api, meet_mark
from src.auth import AuthError
from src.config import EmployeeFolder

from tests.test_main import _item, make_config

NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)


def _config(emails, tmp_path, **extra):
    cfg = make_config(folders=["placeholder"], data_dir=tmp_path, stt_provider="", **extra)
    return replace(
        cfg,
        folders=tuple(
            EmployeeFolder(folder_id=f"f{i + 1}", email=email)
            for i, email in enumerate(emails)
        ),
        google_service_account={"client_email": "reader@project.iam.gserviceaccount.com"},
        run_discovery="meet",
    )


def _fleet(config, service=None):
    services = {folder.folder_id: service or MagicMock() for folder in config.folders}
    return delegation.Fleet(config=config, fallback=MagicMock(), services=services)


def _conference(
    name="conferenceRecords/c1",
    start="2026-09-18T11:00:00Z",
    end="2026-09-18T11:30:00Z",
    recordings=(),
    unreadable=False,
):
    return meet_api.MeetConference(
        name=name,
        space="spaces/s",
        start_time=start,
        end_time=end or "",
        recordings=tuple(recordings),
        unreadable=unreadable,
    )


def _recording(file_id="file-1", state="FILE_GENERATED"):
    return meet_api.MeetRecording(
        name="conferenceRecords/c1/recordings/r1",
        state=state,
        file_id=file_id,
        start_time="2026-09-18T11:01:00Z",
        end_time="2026-09-18T11:29:00Z",
    )


@pytest.fixture(autouse=True)
def _frozen_now(mocker):
    """A fixed now, so "how long have we waited" is a fact and not a race."""
    clock = mocker.patch("src.main.datetime", wraps=dt.datetime)
    clock.now.return_value = NOW
    return clock


def _ask(mocker, conferences, *, error=None):
    mocker.patch("src.main.build_meet_service", return_value=MagicMock())
    if error is not None:
        return mocker.patch("src.main.meet_api.conferences_since", side_effect=error)
    return mocker.patch(
        "src.main.meet_api.conferences_since", return_value=list(conferences)
    )


def _drive(mocker, *, parents=("folder-1",), items=None, owner="one@example.com"):
    mocker.patch(
        "src.main.drive.file_placement", return_value=(list(parents), owner)
    )
    return mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=list(items if items is not None else [_item("file-1", "call.mp4")]),
    )


def test_nothing_new_costs_one_question_per_employee(mocker, tmp_path):
    """The whole point: an empty answer must not touch Drive at all."""
    asked = _ask(mocker, [])
    listed = mocker.patch("src.main.drive.list_folder_state")
    config = _config(["one@example.com", "two@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert [items for _, items in found.listings] == [[], []]
    assert asked.call_count == 2
    listed.assert_not_called()


def test_a_new_recording_becomes_the_folder_it_lives_in(mocker, tmp_path):
    """Meet names a file; everything downstream is keyed on the folder holding it."""
    _ask(mocker, [_conference(recordings=[_recording()])])
    listed = _drive(mocker, items=[_item("file-1", "call.mp4")])
    config = _config(["one@example.com"], tmp_path)
    fleet = _fleet(config)

    found = main._discover_by_meet(fleet, config)

    assert found.listings[0][0] == "f1"
    assert [item["file"]["id"] for item in found.listings[0][1]] == ["file-1"]
    assert listed.call_args.args == (fleet.service_for("f1"), "folder-1")


def test_two_recordings_in_one_folder_list_it_once(mocker, tmp_path):
    _ask(
        mocker,
        [
            _conference(name="conferenceRecords/a", recordings=[_recording("file-1")]),
            _conference(name="conferenceRecords/b", recordings=[_recording("file-2")]),
        ],
    )
    listed = _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    main._discover_by_meet(_fleet(config), config)

    assert listed.call_count == 1


def test_the_mark_moves_to_now_when_everything_is_finished(mocker, tmp_path):
    _ask(mocker, [_conference(recordings=[_recording()])])
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == NOW


def test_a_recording_with_no_file_yet_holds_the_mark(mocker, tmp_path):
    """Meet names a conference minutes before its file exists; stepping over it loses it."""
    _ask(
        mocker,
        [
            _conference(
                start="2026-09-18T11:50:00Z",
                end="2026-09-18T11:55:00Z",
                recordings=[_recording(file_id=None, state="ENDED")],
            )
        ],
    )
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == dt.datetime(2026, 9, 18, 11, 50, tzinfo=dt.timezone.utc)


def test_a_conference_still_in_progress_holds_the_mark(mocker, tmp_path):
    """It has no recording because it is still going; nobody has decided anything."""
    _ask(mocker, [_conference(start="2026-09-18T11:45:00Z", end=None)])
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == dt.datetime(2026, 9, 18, 11, 45, tzinfo=dt.timezone.utc)


def test_an_ended_conference_nobody_recorded_does_not_hold_the_mark(mocker, tmp_path):
    _ask(mocker, [_conference()])
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == NOW


def test_a_conference_whose_recordings_could_not_be_read_holds_the_mark(mocker, tmp_path):
    _ask(mocker, [_conference(start="2026-09-18T11:40:00Z", unreadable=True)])
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == dt.datetime(2026, 9, 18, 11, 40, tzinfo=dt.timezone.utc)


def test_a_file_that_never_arrives_is_let_go_after_the_wait(mocker, tmp_path, caplog):
    """One failed recording must not freeze discovery for the whole fleet."""
    _ask(
        mocker,
        [
            _conference(
                start="2026-09-15T11:00:00Z",
                end="2026-09-15T11:30:00Z",
                recordings=[_recording(file_id=None, state="ENDED")],
            )
        ],
    )
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path, )

    with caplog.at_level(logging.WARNING):
        found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == NOW
    assert any("stop waiting" in record.getMessage() for record in caplog.records)


def test_the_wait_is_configurable(mocker, tmp_path):
    _ask(
        mocker,
        [
            _conference(
                start="2026-09-18T07:00:00Z",
                end="2026-09-18T07:30:00Z",
                recordings=[_recording(file_id=None, state="ENDED")],
            )
        ],
    )
    _drive(mocker)
    config = replace(_config(["one@example.com"], tmp_path), meet_wait_hours=2)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == NOW


def test_the_earliest_unfinished_conference_wins(mocker, tmp_path):
    """The mark is one moment for everybody, so it sits at the oldest loose end."""
    _ask(
        mocker,
        [
            _conference(
                name="conferenceRecords/late",
                start="2026-09-18T11:50:00Z",
                end=None,
            ),
            _conference(
                name="conferenceRecords/early",
                start="2026-09-18T11:10:00Z",
                end=None,
            ),
        ],
    )
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == dt.datetime(2026, 9, 18, 11, 10, tzinfo=dt.timezone.utc)


def test_the_saved_mark_is_where_the_next_question_starts(mocker, tmp_path):
    asked = _ask(mocker, [])
    saved = dt.datetime(2026, 9, 18, 9, 30, tzinfo=dt.timezone.utc)
    meet_mark.write(meet_mark.path_for(tmp_path), saved)
    config = _config(["one@example.com"], tmp_path)

    main._discover_by_meet(_fleet(config), config)

    assert asked.call_args.args[1] == saved


def test_without_a_mark_the_first_look_window_is_used(mocker, tmp_path):
    """A wiped data dir must cost one longer listing, never the whole archive."""
    asked = _ask(mocker, [])
    config = replace(_config(["one@example.com"], tmp_path), meet_first_look_hours=48)

    main._discover_by_meet(_fleet(config), config)

    assert asked.call_args.args[1] == NOW - dt.timedelta(hours=48)


def test_the_first_look_never_reaches_further_back_than_since(mocker, tmp_path):
    """What is in scope stays run.since's job; the mark only says where to resume."""
    asked = _ask(mocker, [])
    config = replace(
        _config(["one@example.com"], tmp_path),
        meet_first_look_hours=168,
        run_since="2026-09-17",
    )

    main._discover_by_meet(_fleet(config), config)

    assert asked.call_args.args[1] == dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc)


def test_one_employee_failing_holds_the_mark_and_walks_them(mocker, tmp_path):
    """A mark moved on everybody else's work would lose that employee's conferences."""
    mocker.patch("src.main.build_meet_service", return_value=MagicMock())
    mocker.patch(
        "src.main.meet_api.conferences_since",
        side_effect=[meet_api.MeetError("refused"), []],
    )
    walked = mocker.patch(
        "src.main.drive.list_folder_tree_state", return_value=[_item("v1", "a.mp4")]
    )
    mocker.patch("src.main.notify.notify_error")
    saved = dt.datetime(2026, 9, 18, 9, 30, tzinfo=dt.timezone.utc)
    meet_mark.write(meet_mark.path_for(tmp_path), saved)
    config = _config(["one@example.com", "two@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.folder_errors == 1
    assert found.meet_mark == saved
    assert [call.args[1] for call in walked.call_args_list] == ["f1"]
    assert ("f1", [_item("v1", "a.mp4")]) in found.listings


def test_with_no_fallback_a_failing_employee_is_only_counted(mocker, tmp_path):
    mocker.patch("src.main.build_meet_service", return_value=MagicMock())
    mocker.patch(
        "src.main.meet_api.conferences_since", side_effect=meet_api.MeetError("refused")
    )
    walked = mocker.patch("src.main.drive.list_folder_tree_state")
    mocker.patch("src.main.notify.notify_error")
    config = replace(_config(["one@example.com"], tmp_path), meet_fallback="none")

    found = main._discover_by_meet(_fleet(config), config)

    assert found.folder_errors == 1
    assert found.listings == []
    walked.assert_not_called()


def test_an_address_the_domain_refuses_is_the_same_kind_of_failure(mocker, tmp_path):
    mocker.patch(
        "src.main.build_meet_service", side_effect=AuthError("cannot impersonate")
    )
    mocker.patch("src.main.drive.list_folder_tree_state", return_value=[])
    mocker.patch("src.main.notify.notify_error")
    config = _config(["gone@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.folder_errors == 1


def test_a_folder_with_nobody_attached_is_walked(mocker, tmp_path):
    """There is no account to ask, so it is read exactly the way it always was."""
    asked = _ask(mocker, [])
    walked = mocker.patch(
        "src.main.drive.list_folder_tree_state", return_value=[_item("v1", "a.mp4")]
    )
    config = _config(["one@example.com"], tmp_path)
    config = replace(
        config, folders=config.folders + (EmployeeFolder(folder_id="pinned"),)
    )

    main._discover_by_meet(_fleet(config), config)

    assert asked.call_count == 1
    assert [call.args[1] for call in walked.call_args_list] == ["pinned"]


def test_a_folder_that_cannot_be_listed_holds_the_mark(mocker, tmp_path):
    """The conferences were read, but their folders were not: that is unfinished work."""
    _ask(mocker, [_conference(recordings=[_recording()])])
    mocker.patch(
        "src.main.drive.file_placement", side_effect=RuntimeError("no access")
    )
    mocker.patch("src.main.notify.notify_error")
    saved = dt.datetime(2026, 9, 18, 9, 30, tzinfo=dt.timezone.utc)
    meet_mark.write(meet_mark.path_for(tmp_path), saved)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.folder_errors == 1
    assert found.meet_mark == saved


def test_meet_mode_without_a_service_account_refuses(tmp_path):
    config = make_config(folders=["f1"], data_dir=tmp_path, stt_provider="")

    with pytest.raises(SystemExit, match="service account"):
        main._discover_by_meet(_fleet(config), config)


def test_the_mode_is_reachable_through_discover(mocker, tmp_path):
    by_meet = mocker.patch("src.main._discover_by_meet", return_value=main._Discovery([], None))
    config = _config(["one@example.com"], tmp_path)

    main._discover(_fleet(config), config, mode="meet")

    by_meet.assert_called_once()


# --- the mark is saved by the cycle, and only by one that finished -------------


def _cycle(mocker, tmp_path, *, process=None, config=None):
    config = config or _config(["one@example.com"], tmp_path)
    mocker.patch(
        "src.delegation.auth.build_drive_service", return_value=MagicMock()
    )
    mocker.patch("src.delegation.meet_root.owner_name", return_value="Owner")
    _ask(mocker, [_conference(recordings=[_recording()])])
    _drive(mocker)
    mocker.patch("src.main.process_item", side_effect=process)
    mocker.patch("src.main.notify.notify_error")
    main.run_once(MagicMock(), config, mode="meet")
    return meet_mark.read(meet_mark.path_for(tmp_path))


def test_a_cycle_that_drained_saves_the_mark(mocker, tmp_path):
    saved = _cycle(mocker, tmp_path, process=lambda *a, **k: None)

    assert saved == NOW


def test_a_cycle_that_failed_on_something_holds_the_mark(mocker, tmp_path):
    """The recording is still not done, and Meet will not mention it twice."""

    def explode(*args, **kwargs):
        raise RuntimeError("transcription failed")

    saved = _cycle(mocker, tmp_path, process=explode)

    assert saved is None


def test_a_dry_run_never_moves_the_mark(mocker, tmp_path):
    config = _config(["one@example.com"], tmp_path)
    mocker.patch("src.delegation.auth.build_drive_service", return_value=MagicMock())
    mocker.patch("src.delegation.meet_root.owner_name", return_value="Owner")
    _ask(mocker, [_conference(recordings=[_recording()])])
    _drive(mocker)
    mocker.patch("src.main.process_item")

    main.run_once(MagicMock(), config, mode="meet", dry_run=True)

    assert meet_mark.read(meet_mark.path_for(tmp_path)) is None


# --- one conference, one piece of work ----------------------------------------
#
# A call between two watched employees is listed by both of their accounts, and Meet
# hands both the same Drive file: the organiser's. Processing it twice would
# transcribe one conversation twice and race two uploads into the same folder.


def _placements(mocker, by_file):
    """Drive answering "where does this file live, and whose is it"."""
    return mocker.patch(
        "src.main.drive.file_placement", side_effect=lambda service, file_id: by_file[file_id]
    )


def test_the_same_conference_from_two_employees_is_one_item(mocker, tmp_path):
    _ask(mocker, [_conference(recordings=[_recording("file-1")])])
    placed = _placements(mocker, {"file-1": (["folder-1"], "one@example.com")})
    listed = mocker.patch(
        "src.main.drive.list_folder_state", return_value=[_item("file-1", "call.mp4")]
    )
    config = _config(["one@example.com", "two@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert placed.call_count == 1
    assert listed.call_count == 1
    assert [(fid, [i["file"]["id"] for i in items]) for fid, items in found.listings] == [
        ("f1", ["file-1"]),
        ("f2", []),
    ]


def test_a_recording_is_attributed_to_whoever_owns_it(mocker, tmp_path):
    """The owner's Drive is where the file lives, and where its artifacts must go."""
    _ask(mocker, [_conference(recordings=[_recording("file-1")])])
    _placements(mocker, {"file-1": (["folder-1"], "two@example.com")})
    listed = mocker.patch(
        "src.main.drive.list_folder_state", return_value=[_item("file-1", "call.mp4")]
    )
    config = _config(["one@example.com", "two@example.com"], tmp_path)
    fleet = _fleet(config)
    fleet.services["f2"] = MagicMock()

    found = main._discover_by_meet(fleet, config)

    assert [(fid, [i["file"]["id"] for i in items]) for fid, items in found.listings] == [
        ("f1", []),
        ("f2", ["file-1"]),
    ]
    assert listed.call_args.args == (fleet.service_for("f2"), "folder-1")


def test_a_recording_owned_outside_the_fleet_is_left_alone(mocker, tmp_path, caplog):
    """The walk never touches one either: it reaches the employee only as a shortcut,
    and no path follows shortcuts. Processing it would mean writing artifacts into the
    Drive of somebody this service was never given."""
    _ask(mocker, [_conference(recordings=[_recording("file-1")])])
    _placements(mocker, {"file-1": (["folder-1"], "client@elsewhere.example")})
    listed = mocker.patch("src.main.drive.list_folder_state")
    config = _config(["one@example.com"], tmp_path)

    with caplog.at_level(logging.INFO):
        found = main._discover_by_meet(_fleet(config), config)

    listed.assert_not_called()
    assert found.listings == [("f1", [])]
    assert any("outside the configured" in r.getMessage() for r in caplog.records)


def test_an_address_in_another_case_is_still_the_same_employee(mocker, tmp_path):
    """Google addresses are case-insensitive; a capital would look like an outsider."""
    _ask(mocker, [_conference(recordings=[_recording("file-1")])])
    _placements(mocker, {"file-1": (["folder-1"], "One@Example.com")})
    mocker.patch(
        "src.main.drive.list_folder_state", return_value=[_item("file-1", "call.mp4")]
    )
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert [i["file"]["id"] for _, items in found.listings for i in items] == ["file-1"]


def test_an_owner_who_is_also_being_walked_is_not_listed_twice(mocker, tmp_path):
    """The walk reads that whole folder; listing it here too would duplicate the work."""
    mocker.patch("src.main.build_meet_service", return_value=MagicMock())
    mocker.patch(
        "src.main.meet_api.conferences_since",
        side_effect=[
            meet_api.MeetError("refused"),
            [_conference(recordings=[_recording("file-1")])],
        ],
    )
    _placements(mocker, {"file-1": (["folder-1"], "one@example.com")})
    listed = mocker.patch("src.main.drive.list_folder_state")
    walked = mocker.patch(
        "src.main.drive.list_folder_tree_state", return_value=[_item("file-1", "call.mp4")]
    )
    mocker.patch("src.main.notify.notify_error")
    config = _config(["one@example.com", "two@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    listed.assert_not_called()
    assert [call.args[1] for call in walked.call_args_list] == ["f1"]
    assert [fid for fid, _ in found.listings] == ["f2", "f1"]


# --- no hold lasts for ever ---------------------------------------------------


def test_a_conference_that_never_ends_is_released_after_the_wait(mocker, tmp_path, caplog):
    """A record Meet never closes would otherwise pin the mark and re-read everything."""
    _ask(mocker, [_conference(start="2026-09-15T11:00:00Z", end=None)])
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    with caplog.at_level(logging.WARNING):
        found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == NOW
    assert any("stop waiting" in r.getMessage() for r in caplog.records)


def test_a_conference_that_stays_unreadable_is_released_after_the_wait(mocker, tmp_path):
    _ask(
        mocker,
        [
            _conference(
                start="2026-09-15T11:00:00Z", end="2026-09-15T11:30:00Z", unreadable=True
            )
        ],
    )
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == NOW


def test_a_conference_still_running_inside_the_wait_still_holds(mocker, tmp_path):
    _ask(mocker, [_conference(start="2026-09-18T11:45:00Z", end=None)])
    _drive(mocker)
    config = _config(["one@example.com"], tmp_path)

    found = main._discover_by_meet(_fleet(config), config)

    assert found.meet_mark == dt.datetime(2026, 9, 18, 11, 45, tzinfo=dt.timezone.utc)
