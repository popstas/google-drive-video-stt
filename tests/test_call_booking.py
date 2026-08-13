import fcntl
import os
from datetime import datetime, timedelta, timezone

from src import call_booking
from src.call_booking import CallBooking, append, load, match


def _utc(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def _booking(task_id="851030", email="manager@example.com", start=None):
    return CallBooking(
        task_id=task_id,
        manager_email=email,
        start_time=start or _utc(11, 7),
    )


def test_append_then_load_round_trips(tmp_path):
    path = tmp_path / "call_bookings.jsonl"
    booking = _booking()

    append(path, booking)

    assert load(path, now=_utc(11, 12)) == [booking]


def test_append_flushes_and_fsyncs_before_releasing_the_lock(tmp_path, monkeypatch):
    """Regression: the lock must guard the actual write(2), not just the buffer.

    ``handle.write()`` only fills Python's userspace buffer; the real write(2)
    syscall happens wherever that buffer is next flushed. If ``LOCK_UN`` fires
    before the flush, the lock protects nothing -- a concurrent writer's own
    flush/close can interleave its bytes with this line before either actually
    reaches the file, tearing the record that ``load()`` then silently drops.
    """
    calls: list[str] = []
    real_flock = fcntl.flock
    real_fsync = os.fsync

    def recording_flock(fd, op):
        if op == fcntl.LOCK_UN:
            calls.append("unlock")
        return real_flock(fd, op)

    def recording_fsync(fd):
        calls.append("fsync")
        return real_fsync(fd)

    monkeypatch.setattr(call_booking.fcntl, "flock", recording_flock)
    monkeypatch.setattr(call_booking.os, "fsync", recording_fsync)

    append(tmp_path / "call_bookings.jsonl", _booking())

    assert calls == ["fsync", "unlock"]


def test_load_returns_empty_for_missing_file(tmp_path):
    assert load(tmp_path / "absent.jsonl", now=_utc(11, 12)) == []


def test_later_line_wins_for_the_same_task_id(tmp_path):
    path = tmp_path / "call_bookings.jsonl"
    append(path, _booking(start=_utc(11, 7)))
    append(path, _booking(start=_utc(11, 9)))

    loaded = load(path, now=_utc(11, 12))

    assert loaded == [_booking(start=_utc(11, 9))]


def test_entries_older_than_retention_are_dropped(tmp_path):
    path = tmp_path / "call_bookings.jsonl"
    stale = _booking(task_id="old", start=_utc(11, 7) - timedelta(days=31))
    fresh = _booking(task_id="new", start=_utc(11, 7))
    append(path, stale)
    append(path, fresh)

    loaded = load(path, now=_utc(11, 12))

    assert [b.task_id for b in loaded] == ["new"]


def test_corrupt_line_is_skipped(tmp_path):
    path = tmp_path / "call_bookings.jsonl"
    append(path, _booking())
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"task_id": "torn"\n')
    append(path, _booking(task_id="852000"))

    loaded = load(path, now=_utc(11, 12))

    assert sorted(b.task_id for b in loaded) == ["851030", "852000"]


def test_match_returns_booking_inside_threshold():
    booking = _booking(start=_utc(11, 7))

    result = match(
        [booking],
        email="manager@example.com",
        video_start=_utc(11, 7, 10),
        threshold_minutes=15,
    )

    assert result == booking


def test_match_returns_none_outside_threshold():
    result = match(
        [_booking(start=_utc(11, 7))],
        email="manager@example.com",
        video_start=_utc(11, 7, 30),
        threshold_minutes=15,
    )

    assert result is None


def test_match_ignores_email_case():
    booking = _booking(email="Manager@Example.COM")

    result = match(
        [booking],
        email="manager@example.com",
        video_start=_utc(11, 7),
        threshold_minutes=15,
    )

    assert result == booking


def test_match_returns_none_for_a_different_manager():
    result = match(
        [_booking(email="other@example.com")],
        email="manager@example.com",
        video_start=_utc(11, 7),
        threshold_minutes=15,
    )

    assert result is None


def test_match_picks_the_nearest_of_two_candidates():
    near = _booking(task_id="near", start=_utc(11, 7, 5))
    far = _booking(task_id="far", start=_utc(11, 7, 14))

    result = match(
        [far, near],
        email="manager@example.com",
        video_start=_utc(11, 7),
        threshold_minutes=15,
    )

    assert result is not None
    assert result.task_id == "near"


def test_match_returns_none_for_an_empty_journal():
    assert (
        match([], email="a@b.c", video_start=_utc(11, 7), threshold_minutes=15)
        is None
    )
