from __future__ import annotations

import datetime as dt

from src import meet_mark

MOMENT = dt.datetime(2026, 9, 18, 10, 30, 15, tzinfo=dt.timezone.utc)


def test_a_saved_moment_comes_back(tmp_path):
    path = meet_mark.path_for(tmp_path)

    meet_mark.write(path, MOMENT)

    assert meet_mark.read(path) == MOMENT


def test_nothing_saved_reads_as_nothing(tmp_path):
    assert meet_mark.read(meet_mark.path_for(tmp_path)) is None


def test_an_unreadable_mark_costs_a_long_listing_not_a_crash(tmp_path):
    """Refusing to parse must not become "start from the epoch", which is a bill."""
    path = meet_mark.path_for(tmp_path)
    path.write_text("last tuesday", encoding="utf-8")

    assert meet_mark.read(path) is None


def test_an_empty_mark_reads_as_nothing(tmp_path):
    path = meet_mark.path_for(tmp_path)
    path.write_text("   \n", encoding="utf-8")

    assert meet_mark.read(path) is None


def test_a_naive_moment_is_kept_as_utc(tmp_path):
    """Writing local time would move the window by hours on a server outside UTC."""
    path = meet_mark.path_for(tmp_path)

    meet_mark.write(path, dt.datetime(2026, 9, 18, 10, 30, 15))

    assert meet_mark.read(path) == MOMENT
    assert path.read_text(encoding="utf-8").endswith("Z")


def test_a_moment_elsewhere_is_stored_in_utc(tmp_path):
    path = meet_mark.path_for(tmp_path)
    elsewhere = MOMENT.astimezone(dt.timezone(dt.timedelta(hours=3)))

    meet_mark.write(path, elsewhere)

    assert meet_mark.read(path) == MOMENT


def test_the_previous_mark_survives_an_interrupted_write(tmp_path, mocker):
    path = meet_mark.path_for(tmp_path)
    meet_mark.write(path, MOMENT)
    mocker.patch("pathlib.Path.replace", side_effect=OSError("interrupted"))

    try:
        meet_mark.write(path, MOMENT + dt.timedelta(hours=1))
    except OSError:
        pass

    assert meet_mark.read(path) == MOMENT


def test_forgetting_the_mark_says_whether_there_was_one(tmp_path):
    path = meet_mark.path_for(tmp_path)

    assert meet_mark.clear(path) is False
    meet_mark.write(path, MOMENT)
    assert meet_mark.clear(path) is True
    assert meet_mark.read(path) is None
