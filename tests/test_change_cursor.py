from __future__ import annotations

from src import change_cursor


def test_no_file_means_no_cursor(tmp_path):
    assert change_cursor.read(change_cursor.path_for(tmp_path)) is None


def test_a_saved_cursor_comes_back(tmp_path):
    path = change_cursor.path_for(tmp_path)

    change_cursor.write(path, "tok-1")

    assert change_cursor.read(path) == "tok-1"


def test_an_empty_file_reads_as_no_cursor(tmp_path):
    """An empty or whitespace-only file is the shape a half-finished write leaves
    behind; resuming from "" would silently mean "from the beginning of time"."""
    path = change_cursor.path_for(tmp_path)
    path.write_text("   \n", encoding="utf-8")

    assert change_cursor.read(path) is None


def test_a_saved_cursor_replaces_the_previous_one(tmp_path):
    path = change_cursor.path_for(tmp_path)

    change_cursor.write(path, "tok-1")
    change_cursor.write(path, "tok-2")

    assert change_cursor.read(path) == "tok-2"


def test_an_empty_token_is_never_written(tmp_path):
    """Drive answering with no cursor must leave the last good one in place, not
    replace it with nothing."""
    path = change_cursor.path_for(tmp_path)
    change_cursor.write(path, "tok-1")

    change_cursor.write(path, "")

    assert change_cursor.read(path) == "tok-1"


def test_writing_creates_the_directory(tmp_path):
    path = change_cursor.path_for(tmp_path / "not" / "there" / "yet")

    change_cursor.write(path, "tok-1")

    assert change_cursor.read(path) == "tok-1"


def test_an_interrupted_write_leaves_no_temporary_file_behind(tmp_path):
    path = change_cursor.path_for(tmp_path)

    change_cursor.write(path, "tok-1")

    assert list(p.name for p in tmp_path.iterdir()) == [change_cursor.FILE_NAME]


def test_clearing_reports_whether_there_was_a_cursor(tmp_path):
    path = change_cursor.path_for(tmp_path)
    change_cursor.write(path, "tok-1")

    assert change_cursor.clear(path) is True
    assert change_cursor.clear(path) is False
    assert change_cursor.read(path) is None


def test_an_unreadable_cursor_is_treated_as_absent(tmp_path, mocker):
    """One full sweep is the right price for a broken cursor file. Raising here would
    stop the service over a file it is designed to be able to lose."""
    path = change_cursor.path_for(tmp_path)
    path.write_text("tok-1", encoding="utf-8")
    mocker.patch(
        "pathlib.Path.read_text", side_effect=OSError("disk went away")
    )

    assert change_cursor.read(path) is None
