# Planfix Call-Booking Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link each Drive recording to the Planfix task it belongs to, post the generated meeting summary into that task, and let the polling loop refuse to transcribe recordings that match no booked call.

**Architecture:** An HTTP receiver runs in a daemon thread inside `gdstt run` and appends upcoming-call bookings to a JSONL journal. When the polling loop sees a new mp4, it parses the meeting start time out of the Drive file name, looks for a booking with the same manager email within a time threshold, and either blocks the file (marking it on Drive) or remembers the task id. After the presets finish, the configured preset texts are posted to that Planfix task as one comment.

**Tech Stack:** Python 3.11+, standard library only for the new server (`http.server`, `hmac`, `fcntl`, `json`, `datetime`), `requests` for the outbound call, `pytest` + `pytest-mock`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-08-11-planfix-call-booking-design.md`

## Global Constraints

- Python `>=3.11`; type hints use `from __future__ import annotations` and `X | None` style.
- No new third-party dependencies. The receiver uses the standard library.
- `ruff` line-length is 100, target `py311`. Run `uv run ruff check` before every commit.
- Tests mock all external services and make no external network calls. Binding a loopback socket on `127.0.0.1:0` is allowed.
- One test file per `src` module.
- Compute Drive-name stems with `drive.drive_stem`, never `Path(...).stem` — Drive names may contain `/`.
- Secrets never reach a log line. `call_booking.authorization_token` and `planfix.token` are compared/sent but never logged; `gdstt doctor` reports only set/unset.
- The Planfix comment body is meeting content (PII) and must never be logged.
- Journal retention is the module constant `RETENTION_DAYS = 30`, not a config key.
- `task_id` is a string everywhere; it is converted with `int()` only when building the Planfix request body.
- Every task ends green: `uv run ruff check && uv run pytest`.

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `src/meeting_time.py` | Parse a meeting start time out of a Drive file name. Pure strings, no I/O. |
| `src/call_booking.py` | The `CallBooking` record, the JSONL journal (append/load/prune), and booking matching. No Drive, no HTTP. |
| `src/booking_server.py` | The inbound HTTP receiver and its thread lifecycle. |
| `src/planfix.py` | The outbound `planfix_create_comment` POST. |
| `src/booking_gate.py` | Glue: resolves a per-file `BookingDecision` and reads/writes the Drive mark. |
| `tests/test_meeting_time.py` | Table-driven tests for name parsing. |
| `tests/test_call_booking.py` | Journal and matching tests. |
| `tests/test_booking_server.py` | Real loopback server driven with `http.client`. |
| `tests/test_planfix.py` | Mirrors `tests/test_webhook.py`. |
| `tests/test_booking_gate.py` | Every `BookingDecision` branch. |

**Modified:**

| File | Change |
| --- | --- |
| `src/config.py` | `call_booking` and `planfix` sections, two validations, generated-config entries, `Config.call_bookings_file`. |
| `src/drive.py` | Two appProperty constants surfaced onto `list_folder_state` items. |
| `src/main.py` | Gate in `run_once`, `booking_decision` on `process_item`, Planfix send, server start in `main()`. |
| `src/cli.py` | `gdstt bookings list|rematch`, `doctor` reporting. |
| `docker-compose.yml` | Publish the receiver port. |
| `AGENTS.md`, `README.md`, `skills/gdstt-cli/SKILL.md` | Document the flow, setup, and commands. |
| `tests/test_config.py`, `tests/test_main.py`, `tests/test_drive.py`, `tests/test_cli.py`, `tests/test_docker_deploy.py`, `tests/test_skill_docs.py` | Coverage for the above. |

---

### Task 1: Meeting-time parsing

**Files:**
- Create: `src/meeting_time.py`
- Test: `tests/test_meeting_time.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_meeting_start(drive_name: str) -> datetime | None` — timezone-aware, normalized to UTC.

- [ ] **Step 1: Write the failing test**

Create `tests/test_meeting_time.py`:

```python
from datetime import datetime, timezone

import pytest

from src.meeting_time import parse_meeting_start


def _utc(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "name, expected",
    [
        # Parenthesized, hyphen date, GMT offset without minutes.
        (
            "cnv-bezm-efi (2026-07-19 10:25 GMT+2).mp4",
            _utc(2026, 7, 19, 8, 25),
        ),
        # Slash date, zone abbreviation, ASCII hyphen delimiters.
        (
            "meeting Sonal Harsh with Oksana ExpertizeMe - 2026/07/02 21:56 CEST - Recording.mp4",
            _utc(2026, 7, 2, 19, 56),
        ),
        # Slash date, offset with minutes, en dash before "Recording".
        (
            "Md Thofiq Imamsab and Ekaterina Popova - 2026/08/06 16:29 GMT+04:00 – Recording.mp4",
            _utc(2026, 8, 6, 12, 29),
        ),
        # Cyrillic prefix, en dash, offset with minutes.
        (
            "30-минутная онлайн-встреча Ekaterina Popova(ExpertizeMe) и Dmitrii  - "
            "2026/08/08 09:00 GMT+04:00 – Recording.mp4",
            _utc(2026, 8, 8, 5, 0),
        ),
        # Locally sanitized copy: "/" and ":" became "_".
        (
            "30-минутная онлайн-встреча Ekaterina Popova(ExpertizeMe) и Зинаида - "
            "2026_07_04 08_59 GMT+04_00 – Recording.mp4",
            _utc(2026, 7, 4, 4, 59),
        ),
        # A negative offset zone abbreviation.
        (
            "Standup - 2026/01/09 14:30 EST - Recording.mp4",
            _utc(2026, 1, 9, 19, 30),
        ),
        # Seconds are tolerated when present.
        (
            "Call - 2026/02/03 07:05:11 UTC - Recording.mp4",
            _utc(2026, 2, 3, 7, 5),
        ),
    ],
)
def test_parses_supported_name_shapes(name, expected):
    assert parse_meeting_start(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # No time block at all.
        "2026-07-15_18-04-32_speedup_small_480.mp4",
        # Date and time but no zone.
        "Call - 2026/02/03 07:05 - Recording.mp4",
        # Unknown zone abbreviation: guessing an offset would silently mis-time matches.
        "Call - 2026/02/03 07:05 XYZ - Recording.mp4",
        # No date at all.
        "just-a-recording.mp4",
        "",
    ],
)
def test_returns_none_when_no_usable_time(name):
    assert parse_meeting_start(name) is None


def test_result_is_timezone_aware():
    result = parse_meeting_start("Call - 2026/02/03 07:05 UTC - Recording.mp4")
    assert result is not None
    assert result.tzinfo is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_meeting_time.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.meeting_time'`

- [ ] **Step 3: Write the implementation**

Create `src/meeting_time.py`:

```python
"""Parse a meeting's start time out of a Google Meet recording's Drive name.

The Drive name is the only reliable source for when the call *started*: Drive's
``createdTime`` is when the finished recording landed, i.e. the start plus the call's
own length plus processing, which is 30-60 minutes later for a half-hour meeting.
Matching that against a booked start time with a ±15-minute window would never hit.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Offsets in minutes east of UTC.
#
# This table is a code constant, not config: a wrong offset silently mis-times every
# match for that zone, so it should change under review. Two abbreviations are
# genuinely ambiguous worldwide and are resolved here for the deployments this
# service actually serves: ``IST`` is India (+5:30), not Israel or Ireland, and
# ``CST`` is US Central (-6), not China. Anything not listed yields ``None`` rather
# than a guess.
_ZONE_OFFSET_MINUTES = {
    "UTC": 0,
    "GMT": 0,
    "WET": 0,
    "BST": 60,
    "WEST": 60,
    "CET": 60,
    "CEST": 120,
    "EET": 120,
    "EEST": 180,
    "MSK": 180,
    "IST": 330,
    "EST": -300,
    "EDT": -240,
    "CST": -360,
    "CDT": -300,
    "MST": -420,
    "MDT": -360,
    "PST": -480,
    "PDT": -420,
}

# ``2026/08/06 16:29 GMT+04:00``, ``2026-07-19 10:25 GMT+2``,
# ``2026_07_04 08_59 GMT+04_00``, ``2026/07/02 21:56 CEST``.
#
# The date/time separator is a literal space or ``T`` on purpose: allowing ``_`` too
# would make ``2026-07-15_18-04-32_speedup`` (a locally renamed test file, no zone)
# look like a timestamped meeting.
_MEETING_TIME_RE = re.compile(
    r"(?P<year>\d{4})[-/_](?P<month>\d{2})[-/_](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2})[:_](?P<minute>\d{2})(?:[:_]\d{2})?"
    r"\s*(?P<zone>(?:GMT|UTC)\s*[+-]\s*\d{1,2}(?:[:_]\d{2})?|[A-Z]{2,4})"
)

_OFFSET_RE = re.compile(
    r"(?:GMT|UTC)\s*(?P<sign>[+-])\s*(?P<hours>\d{1,2})(?:[:_](?P<minutes>\d{2}))?"
)


def _zone_offset(zone: str) -> timedelta | None:
    """Resolve a zone token to a UTC offset, or ``None`` when it is unknown."""
    offset_match = _OFFSET_RE.fullmatch(zone)
    if offset_match is not None:
        hours = int(offset_match.group("hours"))
        minutes = int(offset_match.group("minutes") or 0)
        total = hours * 60 + minutes
        if offset_match.group("sign") == "-":
            total = -total
        return timedelta(minutes=total)

    named = _ZONE_OFFSET_MINUTES.get(zone.upper())
    if named is None:
        return None
    return timedelta(minutes=named)


def parse_meeting_start(drive_name: str) -> datetime | None:
    """Return the meeting's start time in UTC, or ``None`` when the name has none.

    A name without a parseable timestamp is not an error: hand-uploaded and renamed
    recordings exist, and the caller treats them as unmatched rather than failing the
    file.
    """
    if not drive_name:
        return None

    match = _MEETING_TIME_RE.search(drive_name)
    if match is None:
        return None

    offset = _zone_offset(match.group("zone"))
    if offset is None:
        logger.info(
            "Unknown timezone %r in recording name %r; treating it as untimed",
            match.group("zone"),
            drive_name,
        )
        return None

    try:
        local = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            tzinfo=timezone(offset),
        )
    except ValueError:
        # e.g. month 13 or day 32 in a name that happens to look like a date.
        logger.info("Unusable date in recording name %r", drive_name)
        return None

    return local.astimezone(timezone.utc)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_meeting_time.py -v && uv run ruff check`
Expected: PASS, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/meeting_time.py tests/test_meeting_time.py
git commit -m "feat: parse meeting start time from recording names"
```

---

### Task 2: Booking record, journal, and matching

**Files:**
- Create: `src/call_booking.py`
- Test: `tests/test_call_booking.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CallBooking(task_id: str, manager_email: str, start_time: datetime)` — frozen dataclass, `start_time` aware UTC.
  - `RETENTION_DAYS: int = 30`
  - `append(path: Path, booking: CallBooking) -> None`
  - `load(path: Path, *, now: datetime | None = None) -> list[CallBooking]`
  - `match(bookings: list[CallBooking], *, email: str, video_start: datetime, threshold_minutes: int) -> CallBooking | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_call_booking.py`:

```python
from datetime import datetime, timedelta, timezone

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_call_booking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.call_booking'`

- [ ] **Step 3: Write the implementation**

Create `src/call_booking.py`:

```python
"""The upcoming-call journal: one JSON object per line, appended as bookings arrive.

Append-only is deliberate. Bookings arrive from an external system at unpredictable
times, and a rescheduled call is simply a newer line for the same ``task_id`` — no
read-modify-write, so a crash mid-write costs at most the line being written, and the
file greps by eye when someone asks why a recording did not match.
"""

from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Bookings older than this are dropped on read. A constant rather than a config key:
# the journal only exists to match a recording that lands hours after its call, and no
# deployment needs to tune that.
RETENTION_DAYS = 30


@dataclass(frozen=True)
class CallBooking:
    """One upcoming call: which Planfix task, whose calendar, and when it starts."""

    task_id: str
    manager_email: str
    start_time: datetime  # timezone-aware, UTC


def _to_dict(booking: CallBooking) -> dict[str, str]:
    return {
        "task_id": booking.task_id,
        "manager_email": booking.manager_email,
        "start_time": booking.start_time.astimezone(timezone.utc).isoformat(),
    }


def _from_dict(raw: object) -> CallBooking | None:
    if not isinstance(raw, dict):
        return None
    task_id = raw.get("task_id")
    manager_email = raw.get("manager_email")
    start_time = raw.get("start_time")
    if not isinstance(task_id, str) or not isinstance(manager_email, str):
        return None
    if not isinstance(start_time, str):
        return None
    try:
        parsed = datetime.fromisoformat(start_time)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return CallBooking(
        task_id=task_id,
        manager_email=manager_email,
        start_time=parsed.astimezone(timezone.utc),
    )


def append(path: Path, booking: CallBooking) -> None:
    """Append one booking to the journal, serialized under an exclusive lock.

    The receiver is threaded, so two bookings can arrive at once; the lock is what
    keeps their lines from interleaving into a torn record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_to_dict(booking), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load(path: Path, *, now: datetime | None = None) -> list[CallBooking]:
    """Read the journal, dropping stale entries and deduplicating by ``task_id``.

    A missing file is an empty journal, not an error — no booking has arrived yet. An
    I/O error propagates on purpose: the caller must be able to tell "no bookings"
    from "could not read the bookings", because only the first one may mark a
    recording as permanently skipped.
    """
    if not path.exists():
        return []

    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(days=RETENTION_DAYS)

    by_task_id: dict[str, CallBooking] = {}
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed booking on line %d", lineno)
                continue
            booking = _from_dict(raw)
            if booking is None:
                logger.warning("Skipping incomplete booking on line %d", lineno)
                continue
            if booking.start_time < cutoff:
                continue
            # Last line wins: a rescheduled call arrives as a newer entry.
            by_task_id[booking.task_id] = booking

    return list(by_task_id.values())


def match(
    bookings: list[CallBooking],
    *,
    email: str,
    video_start: datetime,
    threshold_minutes: int,
) -> CallBooking | None:
    """Return the manager's booking closest to ``video_start`` within the threshold.

    Nearest-wins rather than first-wins so two back-to-back calls by the same manager
    resolve to the right task instead of whichever happened to be read first.
    """
    if not email:
        return None

    wanted = email.strip().lower()
    window = timedelta(minutes=threshold_minutes)
    candidates = [
        booking
        for booking in bookings
        if booking.manager_email.strip().lower() == wanted
        and abs(booking.start_time - video_start) <= window
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: abs(b.start_time - video_start))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_call_booking.py -v && uv run ruff check`
Expected: PASS, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/call_booking.py tests/test_call_booking.py
git commit -m "feat: add the call-booking journal and matching"
```

---

### Task 3: Configuration

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, on `Config`:
  - `call_booking_enabled: bool = False`
  - `call_booking_listen_host: str = "0.0.0.0"`
  - `call_booking_listen_port: int = 8080`
  - `call_booking_token: str = ""`
  - `call_booking_threshold_minutes: int = 15`
  - `call_booking_disable_recognition: bool = False`
  - `planfix_create_comment_url: str = ""`
  - `planfix_token: str = ""`
  - `planfix_presets: tuple[str, ...] = ("keypoints",)`
  - `Config.call_bookings_file -> Path` (property)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`.

These snippets assume a helper `write_config(tmp_path, mapping) -> Path` that dumps `mapping` to `tmp_path/config.yml` and returns the path, plus `load_config`, `init_config`, `yaml` and `pytest` already imported at the top of the file. **Read the top of `tests/test_config.py` first** and use whatever the file already provides — if its helper is named differently, rename the calls below rather than adding a second helper; if it has none, add:

```python
def write_config(tmp_path, mapping):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    return path
```

```python
CALL_BOOKING_BASE = {
    "folders": [{"folder_id": "f1", "name": "Ekaterina", "email": "kate@example.com"}],
    "stt": {"provider": "deepgram", "deepgram": {"api_key": "dg"}},
    "openai": {"api_key": "sk-test"},
}


def test_call_booking_defaults_to_disabled(tmp_path):
    config = load_config(config_path=write_config(tmp_path, CALL_BOOKING_BASE))

    assert config.call_booking_enabled is False
    assert config.call_booking_listen_host == "0.0.0.0"
    assert config.call_booking_listen_port == 8080
    assert config.call_booking_token == ""
    assert config.call_booking_threshold_minutes == 15
    assert config.call_booking_disable_recognition is False


def test_planfix_defaults(tmp_path):
    config = load_config(config_path=write_config(tmp_path, CALL_BOOKING_BASE))

    assert config.planfix_create_comment_url == ""
    assert config.planfix_token == ""
    assert config.planfix_presets == ("keypoints",)


def test_call_booking_and_planfix_are_parsed(tmp_path):
    raw = {
        **CALL_BOOKING_BASE,
        "call_booking": {
            "enabled": True,
            "listen_host": "127.0.0.1",
            "listen_port": 9100,
            "authorization_token": "secret-token",
            "threshold_minutes": 20,
            "disable_recognition": True,
        },
        "planfix": {
            "create_comment_url": "https://crm.example.com/planfix_create_comment",
            "token": "planfix-token",
            "presets": ["keypoints", "action-items"],
        },
    }

    config = load_config(config_path=write_config(tmp_path, raw))

    assert config.call_booking_enabled is True
    assert config.call_booking_listen_host == "127.0.0.1"
    assert config.call_booking_listen_port == 9100
    assert config.call_booking_token == "secret-token"
    assert config.call_booking_threshold_minutes == 20
    assert config.call_booking_disable_recognition is True
    assert config.planfix_create_comment_url == (
        "https://crm.example.com/planfix_create_comment"
    )
    assert config.planfix_token == "planfix-token"
    assert config.planfix_presets == ("keypoints", "action-items")


def test_enabled_receiver_without_token_is_rejected(tmp_path):
    raw = {**CALL_BOOKING_BASE, "call_booking": {"enabled": True}}

    with pytest.raises(ValueError, match="authorization_token"):
        load_config(config_path=write_config(tmp_path, raw))


def test_disable_recognition_with_an_emailless_folder_is_rejected(tmp_path):
    raw = {
        **CALL_BOOKING_BASE,
        "folders": [
            {"folder_id": "f1", "name": "Ekaterina", "email": "kate@example.com"},
            {"folder_id": "f2", "name": "Nameless"},
        ],
        "call_booking": {
            "enabled": True,
            "authorization_token": "t",
            "disable_recognition": True,
        },
    }

    with pytest.raises(ValueError, match="f2"):
        load_config(config_path=write_config(tmp_path, raw))


def test_threshold_minutes_must_be_positive(tmp_path):
    raw = {
        **CALL_BOOKING_BASE,
        "call_booking": {"threshold_minutes": 0},
    }

    with pytest.raises(ValueError, match="threshold_minutes"):
        load_config(config_path=write_config(tmp_path, raw))


def test_call_bookings_file_sits_next_to_the_config(tmp_path):
    config_path = write_config(tmp_path, CALL_BOOKING_BASE)

    config = load_config(config_path=config_path)

    assert config.call_bookings_file == config_path.parent / "call_bookings.jsonl"


def test_generated_config_ships_the_new_sections(tmp_path):
    init_config(tmp_path / "config.yml")

    raw = yaml.safe_load((tmp_path / "config.yml").read_text(encoding="utf-8"))

    assert raw["call_booking"] == {
        "enabled": False,
        "listen_host": "0.0.0.0",
        "listen_port": 8080,
        "authorization_token": "",
        "threshold_minutes": 15,
        "disable_recognition": False,
    }
    assert raw["planfix"] == {
        "create_comment_url": "",
        "token": "",
        "presets": ["keypoints"],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "call_booking or planfix" -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'call_booking_enabled'`

- [ ] **Step 3: Add the fields to `Config`**

In `src/config.py`, add to the `Config` dataclass after `webhook_token`:

```python
    # Inbound call-booking receiver. Disabled by default: enabling it opens a
    # listening port, which a config written before this feature never asked for.
    call_booking_enabled: bool = False
    call_booking_listen_host: str = "0.0.0.0"
    call_booking_listen_port: int = 8080
    call_booking_token: str = ""
    call_booking_threshold_minutes: int = 15
    # When true, the polling loop refuses to transcribe a recording that matched no
    # booked call and marks it on Drive. Manual commands ignore this.
    call_booking_disable_recognition: bool = False
    # Planfix comment target. A blank URL disables the comment; ``planfix_presets``
    # names the preset artifacts concatenated into the comment body, in order.
    planfix_create_comment_url: str = ""
    planfix_token: str = ""
    planfix_presets: tuple[str, ...] = ("keypoints",)
```

Add the journal-path property to `Config` (next to the other derived accessors such as `folder_by_id`):

```python
    @property
    def call_bookings_file(self) -> Path:
        """Where the booking journal lives: alongside the active config file.

        The config file already resolves to the instance directory (``GDSTT_HOME``,
        the mounted volume under Docker), so the journal survives container restarts
        without a second path knob.
        """
        base = self.config_file.parent if self.config_file else self.data_dir
        return base / "call_bookings.jsonl"
```

- [ ] **Step 4: Parse and validate the new sections**

In `_config_from_yaml`, next to the other `_as_mapping` calls, add:

```python
    call_booking = _as_mapping(raw.get("call_booking"), "call_booking")
    planfix = _as_mapping(raw.get("planfix"), "planfix")
```

After the `webhook_*` block, add:

```python
    call_booking_enabled = _yaml_bool(call_booking.get("enabled"), default=False)
    call_booking_listen_host = (
        _yaml_str(call_booking.get("listen_host"), "0.0.0.0") or "0.0.0.0"
    )
    call_booking_listen_port = _parse_positive_int(
        call_booking.get("listen_port"), default=8080, name="call_booking.listen_port"
    )
    call_booking_token = _yaml_str(call_booking.get("authorization_token"))
    call_booking_threshold_minutes = _parse_positive_int(
        call_booking.get("threshold_minutes"),
        default=15,
        name="call_booking.threshold_minutes",
    )
    call_booking_disable_recognition = _yaml_bool(
        call_booking.get("disable_recognition"), default=False
    )

    planfix_create_comment_url = _yaml_str(planfix.get("create_comment_url"))
    planfix_token = _yaml_str(planfix.get("token"))
    planfix_presets = _parse_planfix_presets(planfix.get("presets"))
```

Add the two helpers at module level:

```python
def _parse_positive_int(value: object, *, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got: {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive, got: {parsed}")
    return parsed


def _parse_planfix_presets(value: object) -> tuple[str, ...]:
    """Read ``planfix.presets``, defaulting to the single ``keypoints`` preset."""
    if value is None:
        return ("keypoints",)
    if not isinstance(value, list):
        raise ValueError(f"planfix.presets must be a list, got: {value!r}")
    names = []
    for entry in value:
        name = _yaml_str(entry)
        if not name:
            raise ValueError(f"planfix.presets entries must be names, got: {entry!r}")
        names.append(name)
    return tuple(names)
```

Add the validation function and call it after `folders` is parsed (it needs both `folders` and the `call_booking` values, so place the call after `folders = _parse_folders(...)`):

```python
def _validate_call_booking(
    *,
    enabled: bool,
    token: str,
    disable_recognition: bool,
    folders: tuple[EmployeeFolder, ...],
) -> None:
    """Reject call-booking settings that would fail silently at runtime.

    Both cases are quiet in production and expensive to diagnose: an open endpoint
    that accepts anyone's bookings, and a folder that can never match a booking and so
    would never be transcribed again.
    """
    if enabled and not token.strip():
        raise ValueError(
            "call_booking.enabled is true but call_booking.authorization_token is "
            "empty; the receiver would accept unauthenticated bookings"
        )
    if not disable_recognition:
        return
    emailless = [f.folder_id for f in folders if not f.email.strip()]
    if emailless:
        raise ValueError(
            "call_booking.disable_recognition is true, so every folder must have an "
            "email to match bookings against; these do not: "
            + ", ".join(emailless)
        )
```

Call it right after `folders = _parse_folders(raw.get("folders"))`:

```python
    _validate_call_booking(
        enabled=call_booking_enabled,
        token=call_booking_token,
        disable_recognition=call_booking_disable_recognition,
        folders=folders,
    )
```

Add the nine new keyword arguments to the `return Config(...)` call, after `webhook_token=webhook_token,`:

```python
        call_booking_enabled=call_booking_enabled,
        call_booking_listen_host=call_booking_listen_host,
        call_booking_listen_port=call_booking_listen_port,
        call_booking_token=call_booking_token,
        call_booking_threshold_minutes=call_booking_threshold_minutes,
        call_booking_disable_recognition=call_booking_disable_recognition,
        planfix_create_comment_url=planfix_create_comment_url,
        planfix_token=planfix_token,
        planfix_presets=planfix_presets,
```

- [ ] **Step 5: Seed the generated config**

In `_default_config_dict`, after the `"webhook": {"url": "", "token": ""},` entry:

```python
        # Seeded disabled: enabling this opens a listening port, so it must be an
        # explicit choice rather than something a `config init` turns on.
        "call_booking": {
            "enabled": False,
            "listen_host": "0.0.0.0",
            "listen_port": 8080,
            "authorization_token": "",
            "threshold_minutes": 15,
            "disable_recognition": False,
        },
        # Seeded empty: a blank url disables the Planfix comment.
        "planfix": {
            "create_comment_url": "",
            "token": "",
            "presets": ["keypoints"],
        },
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check`
Expected: PASS — including the pre-existing config tests, which must stay green.

- [ ] **Step 7: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add call_booking and planfix configuration"
```

---

### Task 4: Planfix comment client

**Files:**
- Create: `src/planfix.py`
- Test: `tests/test_planfix.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `send_comment(*, url: str, token: str = "", proxy_url: str = "", task_id: str, description: str) -> bool` — `True` only on a successful POST.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planfix.py`:

```python
import logging
from unittest.mock import MagicMock

import requests

from src import planfix
from src.planfix import send_comment

URL = "https://bot.example.com/agent/leads/tool/planfix_create_comment"


def _post_mock(monkeypatch, *, raises=None):
    response = MagicMock()
    if raises is None:
        response.raise_for_status = MagicMock()
        post = MagicMock(return_value=response)
    else:
        post = MagicMock(side_effect=raises)
    monkeypatch.setattr(planfix.requests, "post", post)
    return post


def test_posts_task_id_and_description(monkeypatch):
    post = _post_mock(monkeypatch)

    sent = send_comment(url=URL, task_id="861300", description="## keypoints\nтезис")

    assert sent is True
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == URL
    assert kwargs["json"] == {"taskId": 861300, "description": "## keypoints\nтезис"}
    assert kwargs["timeout"] == 10


def test_sends_bearer_token(monkeypatch):
    post = _post_mock(monkeypatch)

    send_comment(url=URL, token="planfix-secret", task_id="1", description="x")

    _, kwargs = post.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer planfix-secret"}


def test_blank_url_is_a_no_op(monkeypatch):
    post = _post_mock(monkeypatch)

    assert send_comment(url="   ", task_id="1", description="x") is False
    post.assert_not_called()


def test_blank_description_is_a_no_op(monkeypatch):
    post = _post_mock(monkeypatch)

    assert send_comment(url=URL, task_id="1", description="  ") is False
    post.assert_not_called()


def test_non_numeric_task_id_is_refused(monkeypatch):
    post = _post_mock(monkeypatch)

    assert send_comment(url=URL, task_id="not-a-number", description="x") is False
    post.assert_not_called()


def test_failure_returns_false_and_leaks_nothing(monkeypatch, caplog):
    _post_mock(monkeypatch, raises=requests.ConnectionError("connect to secret-host"))

    with caplog.at_level(logging.WARNING):
        sent = send_comment(
            url=URL,
            token="planfix-secret",
            task_id="861300",
            description="конфиденциальные тезисы встречи",
        )

    assert sent is False
    logged = caplog.text
    assert "ConnectionError" in logged
    assert "planfix-secret" not in logged
    assert "конфиденциальные" not in logged


def test_proxy_is_used_when_set(monkeypatch):
    post = _post_mock(monkeypatch)

    send_comment(url=URL, proxy_url="http://proxy:3128", task_id="1", description="x")

    _, kwargs = post.call_args
    assert kwargs["proxies"] == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_planfix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.planfix'`

- [ ] **Step 3: Write the implementation**

Create `src/planfix.py`:

```python
"""POST a meeting summary into a Planfix task as a comment.

Mirrors :func:`src.webhook.notify_complete`'s contract — a blank URL is a no-op and a
failure never raises — with one difference: this returns whether the POST succeeded.
The caller needs that answer, because it only writes the "already sent" marker on
success, and it escalates a failure to Telegram (a comment that never reached the CRM
is otherwise invisible to a human).

``description`` is the content of a client call. It must never reach a log line.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10


def send_comment(
    *,
    url: str,
    token: str = "",
    proxy_url: str = "",
    task_id: str,
    description: str,
) -> bool:
    """Create a Planfix comment on ``task_id``. Returns True only when it landed."""
    target = url.strip()
    if not target:
        logger.debug("Planfix URL not set, skipping comment")
        return False

    body = description.strip()
    if not body:
        logger.debug("Planfix comment body is empty, skipping comment")
        return False

    try:
        numeric_task_id = int(task_id)
    except (TypeError, ValueError):
        # Rejected at intake too; this is the belt to that suspenders, and it must not
        # raise on the success path of a file that already cost money to transcribe.
        logger.warning("Planfix task id is not numeric, skipping comment")
        return False

    bearer = token.strip()
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        response = requests.post(
            target,
            json={"taskId": numeric_task_id, "description": body},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            proxies=proxies,
        )
        response.raise_for_status()
    except Exception as exc:
        # The status separates a bad token from an unreachable CRM; the exception
        # message can echo the URL, and the body is meeting content, so neither goes in.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning(
            "Failed to create Planfix comment for task %s: %s%s",
            numeric_task_id,
            type(exc).__name__,
            f" (HTTP {status})" if status else "",
        )
        return False

    logger.info("Created Planfix comment on task %s", numeric_task_id)
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_planfix.py -v && uv run ruff check`
Expected: PASS, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/planfix.py tests/test_planfix.py
git commit -m "feat: add the Planfix comment client"
```

---

### Task 5: Inbound booking receiver

**Files:**
- Create: `src/booking_server.py`
- Test: `tests/test_booking_server.py`

**Interfaces:**
- Consumes: `src.call_booking.CallBooking`, `src.call_booking.append`, `src.config.Config`.
- Produces:
  - `MAX_BODY_BYTES: int = 64 * 1024`
  - `BookingServer` with `.port: int`, `.shutdown() -> None`
  - `start(config: Config) -> BookingServer | None` — `None` when disabled; raises `OSError` when the bind fails.
  - `is_running() -> bool` — module-level, consulted by the gate.

- [ ] **Step 1: Write the failing test**

Create `tests/test_booking_server.py`:

```python
import http.client
import json
from datetime import datetime, timezone

import pytest

from src import booking_server
from src.call_booking import load


@pytest.fixture
def server(tmp_path):
    journal = tmp_path / "call_bookings.jsonl"
    instance = booking_server.serve(
        host="127.0.0.1",
        port=0,
        token="secret-token",
        journal_path=journal,
    )
    try:
        yield instance, journal
    finally:
        instance.shutdown()


def _post(instance, body, *, token="secret-token", raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", instance.port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = raw if raw is not None else json.dumps(body)
    conn.request("POST", "/", body=payload, headers=headers)
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.status


VALID = {
    "start_time": "2026-08-11T07:00:00.000000Z",
    "task_id": "851030",
    "manager_email": "manager@example.com",
}


def test_accepts_a_valid_booking(server):
    instance, journal = server

    assert _post(instance, VALID) == 204

    stored = load(journal, now=datetime(2026, 8, 11, 12, tzinfo=timezone.utc))
    assert len(stored) == 1
    assert stored[0].task_id == "851030"
    assert stored[0].manager_email == "manager@example.com"
    assert stored[0].start_time == datetime(2026, 8, 11, 7, tzinfo=timezone.utc)


def test_rejects_a_missing_token(server):
    instance, journal = server

    assert _post(instance, VALID, token=None) == 401
    assert not journal.exists()


def test_rejects_a_wrong_token(server):
    instance, journal = server

    assert _post(instance, VALID, token="wrong-token") == 401
    assert not journal.exists()


def test_rejects_malformed_json(server):
    instance, _ = server

    assert _post(instance, None, raw="{not json") == 400


@pytest.mark.parametrize("missing", ["start_time", "task_id", "manager_email"])
def test_rejects_a_missing_field(server, missing):
    instance, _ = server
    body = {k: v for k, v in VALID.items() if k != missing}

    assert _post(instance, body) == 400


def test_rejects_an_unparseable_start_time(server):
    instance, _ = server

    assert _post(instance, {**VALID, "start_time": "yesterday"}) == 400


def test_rejects_a_non_numeric_task_id(server):
    instance, _ = server

    assert _post(instance, {**VALID, "task_id": "not-a-number"}) == 400


def test_rejects_an_oversized_body(server):
    instance, _ = server
    body = {**VALID, "manager_email": "x" * (booking_server.MAX_BODY_BYTES + 1)}

    assert _post(instance, body) == 413


def test_health_endpoint_is_open(server):
    instance, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", instance.port, timeout=5)
    conn.request("GET", "/health")
    response = conn.getresponse()
    response.read()
    conn.close()

    assert response.status == 200


def test_is_running_tracks_the_started_server(tmp_path):
    assert booking_server.is_running() is False

    instance = booking_server.serve(
        host="127.0.0.1", port=0, token="t", journal_path=tmp_path / "j.jsonl"
    )
    try:
        assert booking_server.is_running() is True
    finally:
        instance.shutdown()

    assert booking_server.is_running() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_booking_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.booking_server'`

- [ ] **Step 3: Write the implementation**

Create `src/booking_server.py`:

```python
"""Receive upcoming-call bookings over HTTP and append them to the journal.

A daemon thread beside the polling loop, on the standard library's server: one POST
endpoint does not justify a web framework in a service whose entire HTTP surface is
``requests``, and a second container would have to share this one's volume anyway.
TLS and the public hostname belong to the reverse proxy in front of it.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.call_booking import CallBooking, append
from src.config import Config

logger = logging.getLogger(__name__)

# A booking is three short fields. Anything larger is a mistake or an attempt to make
# the receiver allocate; refuse it before reading the body.
MAX_BODY_BYTES = 64 * 1024

_running: BookingServer | None = None
_running_lock = threading.Lock()


def is_running() -> bool:
    """Whether a receiver is listening in this process.

    The gate consults this before marking a recording as permanently unmatched: if no
    receiver ever came up, every recording *looks* unmatched, and marking them would
    silently retire the whole backlog.
    """
    with _running_lock:
        return _running is not None


def _parse_start_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _booking_from_payload(payload: object) -> CallBooking | None:
    """Validate the POST body into a booking, or ``None`` when it is unusable."""
    if not isinstance(payload, dict):
        return None

    task_id = payload.get("task_id")
    manager_email = payload.get("manager_email")
    if not isinstance(task_id, (str, int)):
        return None
    if not isinstance(manager_email, str) or not manager_email.strip():
        return None

    task_id_text = str(task_id).strip()
    # Planfix wants a numeric task id. Rejecting it here turns a bad id into an
    # immediate 400 for the sender instead of a dropped comment hours later, on the
    # success path of a file that already cost money to transcribe.
    if not task_id_text.isdecimal():
        return None

    start_time = _parse_start_time(payload.get("start_time"))
    if start_time is None:
        return None

    return CallBooking(
        task_id=task_id_text,
        manager_email=manager_email.strip(),
        start_time=start_time,
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "gdstt-booking/1.0"

    # Set by ``serve``.
    token: str = ""
    journal_path: Path = Path("call_bookings.jsonl")

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib hook name
        # The default handler writes to stderr outside logging; route it through the
        # module logger at debug so request lines do not spam the service log.
        logger.debug("booking receiver: " + fmt, *args)

    def _respond(self, status: HTTPStatus, text: str = "") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # compare_digest keeps the comparison constant-time so a wrong token cannot be
        # guessed a character at a time by timing the response.
        return hmac.compare_digest(header[len(prefix):].strip(), self.token)

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        if self.path.rstrip("/") in ("/health", "health"):
            self._respond(HTTPStatus.OK, "ok")
            return
        self._respond(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        try:
            if not self._authorized():
                self._respond(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._respond(HTTPStatus.BAD_REQUEST, "bad content-length")
                return
            if length > MAX_BODY_BYTES:
                self._respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
                return

            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._respond(HTTPStatus.BAD_REQUEST, "malformed json")
                return

            booking = _booking_from_payload(payload)
            if booking is None:
                self._respond(HTTPStatus.BAD_REQUEST, "invalid booking")
                return

            append(self.journal_path, booking)
            logger.info(
                "Stored booking for task %s at %s",
                booking.task_id,
                booking.start_time.isoformat(),
            )
            self._respond(HTTPStatus.NO_CONTENT)
        except Exception:
            # One bad request must not take the receiver thread down with it.
            logger.exception("Booking receiver failed to handle a request")
            self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")


class BookingServer:
    """A running receiver: the socket, its thread, and how to stop them."""

    def __init__(self, httpd: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._httpd = httpd
        self._thread = thread

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def shutdown(self) -> None:
        global _running
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        with _running_lock:
            if _running is self:
                _running = None


def serve(*, host: str, port: int, token: str, journal_path: Path) -> BookingServer:
    """Bind and start a receiver. Raises ``OSError`` when the port is unavailable."""
    global _running

    handler = type(
        "_BoundHandler",
        (_Handler,),
        {"token": token, "journal_path": journal_path},
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(
        target=httpd.serve_forever,
        name="gdstt-booking-receiver",
        daemon=True,
    )
    thread.start()
    instance = BookingServer(httpd, thread)
    with _running_lock:
        _running = instance
    logger.info("Booking receiver listening on %s:%d", host, instance.port)
    return instance


def start(config: Config) -> BookingServer | None:
    """Start the receiver when the config enables it, else return ``None``."""
    if not config.call_booking_enabled:
        logger.debug("call_booking.enabled is false, not starting the receiver")
        return None
    return serve(
        host=config.call_booking_listen_host,
        port=config.call_booking_listen_port,
        token=config.call_booking_token,
        journal_path=config.call_bookings_file,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_booking_server.py -v && uv run ruff check`
Expected: PASS. If `test_is_running_tracks_the_started_server` fails because an earlier test left a server up, that is a real leak — check the fixture's `finally` rather than relaxing the assertion.

- [ ] **Step 5: Commit**

```bash
git add src/booking_server.py tests/test_booking_server.py
git commit -m "feat: add the inbound call-booking receiver"
```

---

### Task 6: Drive marks and the booking gate

**Files:**
- Modify: `src/drive.py`
- Create: `src/booking_gate.py`
- Test: `tests/test_drive.py`, `tests/test_booking_gate.py`

**Interfaces:**
- Consumes: `src.meeting_time.parse_meeting_start`, `src.call_booking.load`/`match`, `src.booking_server.is_running`, `src.config.Config`.
- Produces:
  - `drive.BOOKING_MATCH_PROPERTY = "booking_match"`, `drive.PLANFIX_COMMENT_TASK_ID_PROPERTY = "planfix_comment_task_id"`, `drive.BOOKING_MATCH_NONE = "none"`
  - `list_folder_state` items gain `booking_match: str` and `planfix_comment_task_id: str`
  - `booking_gate.BookingDecision` with `.state: str`, `.task_id: str`, `.reason: str`, `.is_matched: bool`
  - `booking_gate.resolve(file_info: dict, folder_id: str, config: Config) -> BookingDecision`
  - `booking_gate.mark_unmatched(service, file_id: str) -> None`
  - `booking_gate.clear_mark(service, file_id: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drive.py` (reuse that file's existing fake Drive service helper; the snippet assumes it builds a service whose `files().list()` returns the given file dicts):

```python
def test_list_folder_state_surfaces_booking_properties():
    service = fake_service(
        mp4=[
            {
                "id": "v1",
                "name": "call - 2026/08/08 09:00 GMT+04:00 – Recording.mp4",
                "mimeType": drive.MP4_MIME,
                "appProperties": {
                    "booking_match": "none",
                    "planfix_comment_task_id": "851030",
                },
            }
        ],
    )

    items = drive.list_folder_state(service, "f1")

    assert items[0]["booking_match"] == "none"
    assert items[0]["planfix_comment_task_id"] == "851030"


def test_list_folder_state_defaults_booking_properties_to_blank():
    service = fake_service(
        mp4=[{"id": "v1", "name": "call.mp4", "mimeType": drive.MP4_MIME}],
    )

    items = drive.list_folder_state(service, "f1")

    assert items[0]["booking_match"] == ""
    assert items[0]["planfix_comment_task_id"] == ""
```

Create `tests/test_booking_gate.py`:

```python
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src import drive
from src.booking_gate import BookingDecision, clear_mark, mark_unmatched, resolve
from src.call_booking import CallBooking, append
from src.config import Config, EmployeeFolder

MATCHED_NAME = "Call with Dmitrii - 2026/08/08 09:00 GMT+04:00 – Recording.mp4"


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("", encoding="utf-8")
    return Config(
        folders=(EmployeeFolder(folder_id="f1", name="Kate", email="kate@example.com"),),
        poll_interval=600,
        bitrate="96k",
        data_dir=tmp_path,
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="sk",
        deepgram_api_key="dg",
        stt_language="ru",
        call_booking_enabled=True,
        call_booking_threshold_minutes=15,
        config_file=config_file,
    )


def _seed(config, *, minutes_offset=0, email="kate@example.com", task_id="851030"):
    start = datetime(2026, 8, 8, 5, minutes_offset, tzinfo=timezone.utc)
    append(
        config.call_bookings_file,
        CallBooking(task_id=task_id, manager_email=email, start_time=start),
    )


def test_disabled_feature_short_circuits(config):
    disabled = replace(config, call_booking_enabled=False)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", disabled)

    assert decision.state == "disabled"
    assert decision.is_matched is False


def test_folder_without_email_is_unmatched(config):
    nameless = replace(
        config, folders=(EmployeeFolder(folder_id="f1"),)
    )
    _seed(config)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", nameless)

    assert decision == BookingDecision(state="unmatched", reason="no-folder-email")


def test_unparseable_name_is_unmatched(config):
    _seed(config)

    decision = resolve({"id": "v1", "name": "hand-uploaded.mp4"}, "f1", config)

    assert decision == BookingDecision(state="unmatched", reason="no-meeting-time")


def test_no_booking_in_window_is_unmatched(config):
    _seed(config, minutes_offset=40)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", config)

    assert decision == BookingDecision(state="unmatched", reason="no-booking")


def test_empty_journal_is_unmatched(config):
    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", config)

    assert decision == BookingDecision(state="unmatched", reason="no-booking")


def test_booking_in_window_matches(config):
    _seed(config, minutes_offset=5)

    decision = resolve({"id": "v1", "name": MATCHED_NAME}, "f1", config)

    assert decision.state == "matched"
    assert decision.task_id == "851030"
    assert decision.is_matched is True


def test_mark_unmatched_writes_the_property():
    service = MagicMock()

    mark_unmatched(service, "v1")

    service.files.return_value.update.assert_called_once()
    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {
        "appProperties": {drive.BOOKING_MATCH_PROPERTY: drive.BOOKING_MATCH_NONE}
    }


def test_clear_mark_nulls_the_property():
    service = MagicMock()

    clear_mark(service, "v1")

    _, kwargs = service.files.return_value.update.call_args
    assert kwargs["body"] == {"appProperties": {drive.BOOKING_MATCH_PROPERTY: None}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_booking_gate.py tests/test_drive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.booking_gate'` and `KeyError: 'booking_match'`.

- [ ] **Step 3: Surface the properties in `src/drive.py`**

Add next to the other property constants:

```python
BOOKING_MATCH_PROPERTY = "booking_match"
PLANFIX_COMMENT_TASK_ID_PROPERTY = "planfix_comment_task_id"
# The single value ``booking_match`` ever takes: this recording matched no booked
# call, so the polling loop must leave it alone.
BOOKING_MATCH_NONE = "none"
```

In `list_folder_state`, extend the appended item dict (the listing already requests `appProperties`, so this costs no extra API call):

```python
        mp4_props = mp4.get("appProperties", {}) or {}
        items.append({
            "file": mp4,
            "has_mp3": mp3 is not None,
            "has_txt": txt is not None,
            "mp3_id": mp3["id"] if mp3 else None,
            "mp3_name": mp3["name"] if mp3 else None,
            "txt_id": txt["id"] if txt else None,
            "artifact_ids": artifact_ids,
            "booking_match": mp4_props.get(BOOKING_MATCH_PROPERTY, ""),
            "planfix_comment_task_id": mp4_props.get(
                PLANFIX_COMMENT_TASK_ID_PROPERTY, ""
            ),
        })
```

- [ ] **Step 4: Write `src/booking_gate.py`**

```python
"""Decide whether a recording belongs to a booked call, and remember the answer.

The only module that sees both Drive and the booking journal. Everything it needs to
decide comes from the file's Drive metadata and the config, so the polling loop can
ask about a file without downloading it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src import call_booking, drive
from src.config import Config
from src.meeting_time import parse_meeting_start

logger = logging.getLogger(__name__)

DISABLED = "disabled"
MATCHED = "matched"
UNMATCHED = "unmatched"


@dataclass(frozen=True)
class BookingDecision:
    """What the gate concluded about one recording.

    ``reason`` is only set for ``unmatched`` and exists for the log line: "no-booking"
    and "no-meeting-time" call for very different fixes.
    """

    state: str
    task_id: str = ""
    reason: str = ""

    @property
    def is_matched(self) -> bool:
        return self.state == MATCHED


def resolve(file_info: dict, folder_id: str, config: Config) -> BookingDecision:
    """Match one Drive mp4 against the booking journal."""
    if not config.call_booking_enabled:
        return BookingDecision(state=DISABLED)

    employee = config.folder_by_id(folder_id)
    email = employee.email.strip() if employee else ""
    if not email:
        return BookingDecision(state=UNMATCHED, reason="no-folder-email")

    file_name = file_info.get("name", "")
    video_start = parse_meeting_start(file_name)
    if video_start is None:
        return BookingDecision(state=UNMATCHED, reason="no-meeting-time")

    # A read error propagates: the caller must be able to tell "no bookings" from
    # "could not read the bookings", because only the first may mark a file for good.
    bookings = call_booking.load(config.call_bookings_file)
    booking = call_booking.match(
        bookings,
        email=email,
        video_start=video_start,
        threshold_minutes=config.call_booking_threshold_minutes,
    )
    if booking is None:
        return BookingDecision(state=UNMATCHED, reason="no-booking")

    return BookingDecision(state=MATCHED, task_id=booking.task_id)


def mark_unmatched(service: Any, file_id: str) -> None:
    """Record on Drive that this recording matched no booked call."""
    drive.set_file_app_properties(
        service,
        file_id,
        {drive.BOOKING_MATCH_PROPERTY: drive.BOOKING_MATCH_NONE},
    )


def clear_mark(service: Any, file_id: str) -> None:
    """Remove the unmatched mark so the polling loop reconsiders the recording.

    Drive deletes an appProperty when its value is null, which is what ``gdstt
    bookings rematch`` needs: the file must look untouched, not "explicitly matched".
    """
    drive.set_file_app_properties(
        service, file_id, {drive.BOOKING_MATCH_PROPERTY: None}
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_booking_gate.py tests/test_drive.py -v && uv run ruff check`
Expected: PASS. The pre-existing `test_drive.py` tests must stay green.

- [ ] **Step 6: Commit**

```bash
git add src/drive.py src/booking_gate.py tests/test_drive.py tests/test_booking_gate.py
git commit -m "feat: resolve booking matches and mark unmatched recordings"
```

---

### Task 7: Gate the polling loop

**Files:**
- Modify: `src/main.py` (`process_item` signature, `run_once`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `booking_gate.resolve`, `booking_gate.mark_unmatched`, `booking_server.is_running`, `drive.BOOKING_MATCH_NONE`.
- Produces: `process_item(..., booking_decision: BookingDecision | None = None)`; the cycle summary gains `skipped_unmatched=<int>`.

- [ ] **Step 1: Add the shared gate test helpers**

Append to `tests/test_main.py`. These helpers are self-contained on purpose — they patch `main.drive.list_folder_state` rather than building a fake Drive API, so the gate tests exercise `run_once`'s own logic and nothing else. Tasks 8 and 9 reuse `gate_config`.

```python
# --- call-booking gate helpers -------------------------------------------------

GATE_FOLDER_ID = "f1"


@pytest.fixture
def gate_config(tmp_path):
    """A config with the receiver enabled and the gate armed."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("", encoding="utf-8")
    return Config(
        folders=(
            EmployeeFolder(
                folder_id=GATE_FOLDER_ID, name="Kate", email="kate@example.com"
            ),
        ),
        poll_interval=600,
        bitrate="96k",
        data_dir=tmp_path,
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="sk",
        deepgram_api_key="dg",
        stt_language="ru",
        call_booking_enabled=True,
        call_booking_disable_recognition=True,
        call_booking_threshold_minutes=15,
        config_file=config_file,
    )


def gate_item(file_id="v1", *, booking_match="", planfix_comment_task_id=""):
    """One `list_folder_state` item for an mp4 that still needs a transcript."""
    return {
        "file": {"id": file_id, "name": f"{file_id}.mp4", "mimeType": "video/mp4"},
        "has_mp3": True,
        "has_txt": False,
        "mp3_id": None,
        "mp3_name": None,
        "txt_id": None,
        "artifact_ids": {},
        "booking_match": booking_match,
        "planfix_comment_task_id": planfix_comment_task_id,
    }


def patch_folder_items(monkeypatch, items):
    monkeypatch.setattr(main.drive, "list_folder_state", lambda service, fid: items)


def patch_decision(monkeypatch, decision):
    monkeypatch.setattr(
        main.booking_gate, "resolve", lambda file_info, folder_id, config: decision
    )


UNMATCHED_DECISION = BookingDecision(state="unmatched", reason="no-booking")
MATCHED_DECISION = BookingDecision(state="matched", task_id="851030")
```

Add the imports these need at the top of `tests/test_main.py` (extend the existing import lines rather than duplicating them):

```python
import logging
from dataclasses import replace

import pytest

from src.booking_gate import BookingDecision
from src.config import Config, EmployeeFolder
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_main.py`:

```python
def test_run_once_skips_and_marks_an_unmatched_recording(monkeypatch, gate_config):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    assert marked == ["v1"]


def test_run_once_does_not_mark_when_the_receiver_is_down(
    monkeypatch, gate_config, caplog
):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: False)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    with caplog.at_level(logging.WARNING):
        main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    assert marked == []
    assert "not listening" in caplog.text


def test_run_once_counts_skipped_unmatched_separately(monkeypatch, gate_config, caplog):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    monkeypatch.setattr(main.booking_gate, "mark_unmatched", lambda svc, fid: None)

    with caplog.at_level(logging.INFO):
        main.run_once(MagicMock(), gate_config)

    assert "skipped_unmatched=1" in caplog.text
    assert "processed=0" in caplog.text


def test_run_once_never_revisits_an_already_marked_recording(monkeypatch, gate_config):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    resolve = MagicMock()
    monkeypatch.setattr(main.booking_gate, "resolve", resolve)
    patch_folder_items(monkeypatch, [gate_item("v1", booking_match="none")])
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    resolve.assert_not_called()


def test_run_once_processes_a_matched_recording(monkeypatch, gate_config):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, MATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_called_once()
    assert process_item.call_args.kwargs["booking_decision"] == MATCHED_DECISION


def test_run_once_processes_when_disable_recognition_is_off(monkeypatch, gate_config):
    permissive = replace(gate_config, call_booking_disable_recognition=False)
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), permissive)

    process_item.assert_called_once()
    assert marked == []


def test_process_target_ignores_the_mark_and_the_gate(monkeypatch, gate_config):
    """Manual processing is the supported way to undo a mark."""
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_folder_items(monkeypatch, [gate_item("v1", booking_match="none")])
    monkeypatch.setattr(
        main.drive,
        "get_file_metadata",
        lambda service, fid: {
            "id": "v1", "name": "v1.mp4", "mimeType": "video/mp4",
            "parents": [GATE_FOLDER_ID],
        },
    )
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.process_target(MagicMock(), "v1", gate_config, is_folder=False)

    process_item.assert_called_once()
    assert marked == []
    assert process_item.call_args.kwargs.get("booking_decision") is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_main.py -k "unmatched or marked or booking or gate" -v`
Expected: FAIL — `AttributeError: module 'src.main' has no attribute 'booking_gate'`

- [ ] **Step 4: Import the new modules and extend `process_item`'s signature**

At the top of `src/main.py`, add to the existing `src` imports:

```python
from src import booking_gate, booking_server
```

Change `process_item`'s signature to accept the decision:

```python
def process_item(
    service: Any,
    item: dict,
    folder_id: str,
    config: Config,
    *,
    reprocess_txt: bool = False,
    reprocess_presets: list[str] | None = None,
    booking_decision: booking_gate.BookingDecision | None = None,
) -> _ProcessTelemetry | None:
```

Right after the early `if not needs_mp3 and not needs_txt and not needs_presets: return` block, resolve a decision when the caller did not supply one:

```python
    # `run_once` resolves this itself so it can gate and count; the manual commands do
    # not, and get a decision here purely so a matched call still reaches Planfix.
    if booking_decision is None:
        booking_decision = booking_gate.resolve(file_info, folder_id, config)
```

- [ ] **Step 5: Add the gate to `run_once`**

In `run_once`, add the counter beside the others:

```python
    cycle_skipped_unmatched = 0
```

Replace the `for item in pending:` processing loop's body opening with the gate. Insert immediately before `try:`:

```python
        for item in pending:
            decision = booking_gate.resolve(item["file"], folder_id, config)
            if (
                decision.state == booking_gate.UNMATCHED
                and config.call_booking_disable_recognition
            ):
                file_name = item.get("file", {}).get("name")
                if booking_server.is_running():
                    # Permanent by design: the booking arrives before the call, so a
                    # recording with no booking is not a client call.
                    booking_gate.mark_unmatched(service, item["file"]["id"])
                    logger.info(
                        "Skipping %s in folder %s: no booked call (%s); marked so it "
                        "is not reconsidered (undo with `gdstt bookings rematch`)",
                        file_name, folder_id, decision.reason,
                    )
                else:
                    logger.warning(
                        "Skipping %s in folder %s: no booked call (%s), but the "
                        "booking receiver is not listening, so it is not marked",
                        file_name, folder_id, decision.reason,
                    )
                cycle_skipped_unmatched += 1
                continue
            try:
                telemetry = process_item(
                    service, item, folder_id, config, booking_decision=decision
                )
```

Filter out already-marked files where `pending` is computed, immediately after `pending = _pending_items(items, config)`:

```python
        # A marked recording is settled: reconsidering it every cycle would re-log and
        # re-decide forever. `gdstt bookings rematch` or any manual command revives it.
        pending = [
            item for item in pending
            if item.get("booking_match") != drive.BOOKING_MATCH_NONE
        ]
```

Add the counter to the cycle summary — extend both the format string and the arguments:

```python
    logger.info(
        "Cycle summary [provider=%s, outcome=%s, folders=%d, pending=%d, processed=%d, failed=%d, "
        "retry_total=%d, skipped_size=%d, skipped_unmatched=%d, folder_errors=%d, dry_run=%s, "
        "duration_s=%.3f]",
        ...
        cycle_skipped_size,
        cycle_skipped_unmatched,
        cycle_folder_errors,
        ...
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v && uv run ruff check`
Expected: PASS — including every pre-existing `test_main.py` test. If a pre-existing cycle-summary assertion breaks on the new field, update that assertion; do not drop the field.

- [ ] **Step 7: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: gate the polling loop on booked calls"
```

---

### Task 8: Send the Planfix comment

**Files:**
- Modify: `src/main.py` (`process_item` success path)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `planfix.send_comment`, `booking_gate.BookingDecision`, `drive.set_file_app_properties`, `drive.PLANFIX_COMMENT_TASK_ID_PROPERTY`, `notify.notify_error`.
- Produces: `_planfix_description(artifacts: dict[str, str], preset_names: tuple[str, ...]) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`. `_send_planfix_comment` is tested directly rather than through `process_item`: the branch logic is what matters here, and driving it through the whole pipeline would test the pipeline instead. Step 2 covers the wiring separately.

```python
@pytest.fixture
def planfix_config(gate_config):
    return replace(
        gate_config,
        planfix_create_comment_url="https://crm.example.com/planfix_create_comment",
        planfix_token="planfix-token",
        planfix_presets=("keypoints",),
    )


def test_planfix_description_concatenates_presets_in_order():
    description = main._planfix_description(
        {"keypoints": "Задачи: раз", "action-items": "Сделать два", "meta": "topic: x"},
        ("keypoints", "action-items"),
    )

    assert description == "## keypoints\nЗадачи: раз\n\n## action-items\nСделать два"


def test_planfix_description_skips_presets_without_an_artifact():
    description = main._planfix_description(
        {"keypoints": "Задачи: раз"}, ("keypoints", "action-items")
    )

    assert description == "## keypoints\nЗадачи: раз"


def test_planfix_description_is_blank_when_nothing_matched():
    assert main._planfix_description({"meta": "topic: x"}, ("keypoints",)) == ""


def test_comment_is_sent_for_a_matched_recording(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)
    marked = MagicMock()
    monkeypatch.setattr(main.drive, "set_file_app_properties", marked)

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", planfix_config,
        {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_called_once()
    assert send.call_args.kwargs["task_id"] == "851030"
    assert send.call_args.kwargs["description"] == "## keypoints\nЗадачи: раз"
    marked.assert_called_once()
    assert marked.call_args[0][2] == {"planfix_comment_task_id": "851030"}


def test_comment_is_not_sent_twice(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)

    main._send_planfix_comment(
        MagicMock(),
        gate_item("v1", planfix_comment_task_id="851030"),
        "v1", planfix_config, {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_not_called()


def test_comment_is_not_sent_for_an_unmatched_recording(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", planfix_config,
        {"keypoints": "Задачи: раз"}, UNMATCHED_DECISION,
    )

    send.assert_not_called()


def test_comment_is_not_sent_when_the_url_is_blank(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)
    unconfigured = replace(planfix_config, planfix_create_comment_url="")

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", unconfigured,
        {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_not_called()


def test_failed_comment_notifies_telegram_and_leaves_no_marker(
    monkeypatch, planfix_config
):
    send = MagicMock(return_value=False)
    monkeypatch.setattr(main.planfix, "send_comment", send)
    marked = MagicMock()
    monkeypatch.setattr(main.drive, "set_file_app_properties", marked)
    notified = MagicMock()
    monkeypatch.setattr(main.notify, "notify_error", notified)

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", planfix_config,
        {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_called_once()
    # No marker means `gdstt reprocess` can resend it.
    marked.assert_not_called()
    notified.assert_called_once()
```

- [ ] **Step 2: Write the failing wiring test**

Also append to `tests/test_main.py`. This one goes through `process_item`, so build it by copying the file's existing happy-path `process_item` test (the one that asserts `webhook.notify_complete` fires) and changing only what is listed below — that test already sets up the Drive, STT, and preset mocks this needs.

```python
def test_process_item_sends_the_comment_on_the_success_path(monkeypatch, ...):
    """Copy the existing notify_complete happy-path test and add these three lines."""
    sent = MagicMock()
    monkeypatch.setattr(main, "_send_planfix_comment", sent)
    # ... existing arrange/act from the notify_complete test, passing
    #     booking_decision=MATCHED_DECISION to process_item ...

    sent.assert_called_once()


def test_process_item_withholds_the_comment_when_a_preset_produced_nothing(
    monkeypatch, ...
):
    """Copy the existing 'webhook withheld on unproduced presets' test likewise."""
    sent = MagicMock()
    monkeypatch.setattr(main, "_send_planfix_comment", sent)
    # ... existing arrange/act, with a preset returning blank ...

    sent.assert_not_called()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_main.py -k planfix -v`
Expected: FAIL — `AttributeError: module 'src.main' has no attribute '_planfix_description'`

- [ ] **Step 4: Write the implementation**

Add the import at the top of `src/main.py`:

```python
from src import planfix
```

Add the helper next to `_webhook_payload`:

```python
def _planfix_description(
    artifacts: dict[str, str], preset_names: tuple[str, ...]
) -> str:
    """Concatenate the configured preset artifacts into one comment body.

    Presets are joined in configured order, each under its own heading, and a preset
    with no artifact is skipped rather than emitting an empty section.
    """
    sections = [
        f"## {name}\n{artifacts[name].strip()}"
        for name in preset_names
        if artifacts.get(name, "").strip()
    ]
    return "\n\n".join(sections)
```

Add the sender next to it:

```python
def _send_planfix_comment(
    service: Any,
    item: dict,
    file_id: str,
    config: Config,
    artifacts: dict[str, str],
    booking_decision: booking_gate.BookingDecision,
) -> None:
    """Post the meeting summary into the matched Planfix task, exactly once.

    `process_item` can legitimately reach its success path more than once per file — a
    later cycle that backfills a newly configured preset re-feeds the transcript — so
    the `planfix_comment_task_id` marker, written only after a successful POST, is what
    keeps a second pass from posting a duplicate comment into the task.
    """
    if not booking_decision.is_matched:
        return
    if not config.planfix_create_comment_url:
        return
    if item.get("planfix_comment_task_id"):
        logger.debug(
            "Planfix comment already sent for %s, skipping", file_id
        )
        return

    description = _planfix_description(artifacts, config.planfix_presets)
    if not description:
        logger.warning(
            "No configured Planfix preset produced text for %s; nothing to comment",
            file_id,
        )
        return

    sent = planfix.send_comment(
        url=config.planfix_create_comment_url,
        token=config.planfix_token,
        proxy_url=config.proxy_url,
        task_id=booking_decision.task_id,
        description=description,
    )
    if sent:
        drive.set_file_app_properties(
            service,
            file_id,
            {drive.PLANFIX_COMMENT_TASK_ID_PROPERTY: booking_decision.task_id},
        )
        return

    # Unlike the completion webhook, a lost CRM comment is invisible to a human, so it
    # escalates. No marker is written, so `gdstt reprocess` can resend it.
    notify.notify_error(
        f"Failed to create the Planfix comment on task {booking_decision.task_id} "
        f"for {item.get('file', {}).get('name')}; rerun `gdstt reprocess {file_id}`",
        telegram_bot_token=config.telegram_bot_token,
        telegram_chat_id=config.telegram_chat_id,
        proxy_url=config.proxy_url,
    )
```

Call it in `process_item`'s success path, inside the existing `elif txt_uploaded or artifacts:` branch, after the `notify_complete` `try/except`:

```python
        try:
            _send_planfix_comment(
                service, item, file_id, config, artifacts, booking_decision
            )
        except Exception as exc:
            # A file that transcribed and uploaded must count as processed even if the
            # CRM hand-off misbehaves.
            logger.warning("Planfix comment failed: %s", type(exc).__name__)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v && uv run ruff check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: post meeting keypoints into the matched Planfix task"
```

---

### Task 9: Start the receiver from the polling loop

**Files:**
- Modify: `src/main.py` (`main`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `booking_server.start`.
- Produces: no new symbols.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`. `main()` loops forever, so the helper makes `time.sleep` raise after the first cycle — that is how the test gets control back.

```python
class _StopLoop(Exception):
    """Raised from a patched time.sleep to break main()'s infinite loop."""


def stop_after_one_cycle(monkeypatch, config):
    """Patch main() down to one cycle. Returns the run_once mock."""
    monkeypatch.setattr(main, "load_config", lambda **kwargs: config)
    monkeypatch.setattr(main, "build_drive_service", lambda **kwargs: MagicMock())
    monkeypatch.setattr(main, "is_run_enabled", lambda **kwargs: True)
    run_once = MagicMock()
    monkeypatch.setattr(main, "run_once", run_once)

    def _sleep(_seconds):
        raise _StopLoop

    monkeypatch.setattr(main.time, "sleep", _sleep)
    return run_once


def test_main_starts_the_receiver_when_enabled(monkeypatch, gate_config):
    start = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(main.booking_server, "start", start)
    stop_after_one_cycle(monkeypatch, gate_config)

    with pytest.raises(_StopLoop):
        main.main()

    start.assert_called_once_with(gate_config)


def test_main_survives_a_receiver_bind_failure(monkeypatch, gate_config, caplog):
    monkeypatch.setattr(
        main.booking_server, "start", MagicMock(side_effect=OSError("port in use"))
    )
    notified = MagicMock()
    monkeypatch.setattr(main.notify, "notify_error", notified)
    run_once = stop_after_one_cycle(monkeypatch, gate_config)

    with caplog.at_level(logging.ERROR), pytest.raises(_StopLoop):
        main.main()

    # The polling loop is the primary job; a dead receiver degrades the gate but must
    # not stop transcription.
    run_once.assert_called_once()
    notified.assert_called_once()
    assert "booking receiver" in caplog.text.lower()
```

Note the ordering `stop_after_one_cycle` depends on: `booking_server.start` must be called *before* the `while True:` loop, so the bind failure is handled once at startup rather than on every cycle.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_main.py -k "receiver" -v`
Expected: FAIL — `start` is never called.

- [ ] **Step 3: Write the implementation**

In `main()`, immediately after the Drive service is built and before the `while True:` loop:

```python
    try:
        booking_server.start(config)
    except OSError as exc:
        # Degrade, do not exit: transcription is the primary job. With the receiver
        # down the gate refuses to mark anything (see `run_once`), so nothing is lost —
        # unmatched files simply wait.
        logger.exception("Booking receiver failed to start; continuing without it")
        notify.notify_error(
            f"Booking receiver failed to start on "
            f"{config.call_booking_listen_host}:{config.call_booking_listen_port}: "
            f"{exc}. Call bookings are not being received; recordings will not be "
            f"marked as unmatched until it is back.",
            telegram_bot_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
            proxy_url=config.proxy_url,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v && uv run ruff check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: start the booking receiver alongside the polling loop"
```

---

### Task 10: `gdstt bookings` and doctor reporting

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `call_booking.load`, `booking_gate.clear_mark`, `Config.call_bookings_file`.
- Produces: `cmd_bookings_list(args)`, `cmd_bookings_rematch(args)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`.

**Read the top of `tests/test_cli.py` first.** These snippets need three things from it: a helper that writes a config and returns its path (called `write_cli_config(tmp_path, **sections)` below — use the file's existing helper name if it has one), the way the file invokes the CLI (`cli.main([...])` below), and the name `src/cli.py` imports `build_drive_service` under. If `cli.py` reaches it through another module, patch that path instead of `cli.build_drive_service`. Add these imports to the test file:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.call_booking import CallBooking, append
```

```python
def test_bookings_list_prints_the_journal(tmp_path, capsys, monkeypatch):
    config_path = write_cli_config(tmp_path)
    append(
        tmp_path / "call_bookings.jsonl",
        CallBooking(
            task_id="851030",
            manager_email="kate@example.com",
            start_time=datetime(2026, 8, 11, 7, tzinfo=timezone.utc),
        ),
    )

    cli.main(["--config", str(config_path), "bookings", "list"])

    out = capsys.readouterr().out
    assert "851030" in out
    assert "kate@example.com" in out
    assert "2026-08-11T07:00:00+00:00" in out


def test_bookings_list_reports_an_empty_journal(tmp_path, capsys):
    config_path = write_cli_config(tmp_path)

    cli.main(["--config", str(config_path), "bookings", "list"])

    assert "no bookings" in capsys.readouterr().out.lower()


def test_bookings_rematch_clears_the_mark(tmp_path, monkeypatch):
    config_path = write_cli_config(tmp_path)
    service = MagicMock()
    monkeypatch.setattr(cli, "build_drive_service", lambda **kwargs: service)
    cleared = []
    monkeypatch.setattr(
        cli.booking_gate, "clear_mark", lambda svc, fid: cleared.append(fid)
    )

    cli.main(["--config", str(config_path), "bookings", "rematch", "v1"])

    assert cleared == ["v1"]


def test_doctor_reports_call_booking_without_leaking_the_token(tmp_path, capsys):
    config_path = write_cli_config(
        tmp_path,
        call_booking={
            "enabled": True,
            "authorization_token": "super-secret",
            "listen_port": 9100,
        },
    )

    cli.main(["--config", str(config_path), "doctor"])

    out = capsys.readouterr().out
    assert "call_booking" in out
    assert "9100" in out
    assert "super-secret" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "bookings or call_booking" -v`
Expected: FAIL — `argparse` errors with `invalid choice: 'bookings'`.

- [ ] **Step 3: Write the commands**

Add the imports at the top of `src/cli.py`:

```python
from src import booking_gate, call_booking
```

Add the command functions next to `cmd_speakers_set`:

```python
def cmd_bookings_list(args: argparse.Namespace) -> None:
    """Print the booking journal after dedupe and pruning.

    This is the first thing to look at when a recording did not match: it shows what
    the matcher actually had to work with.
    """
    config = load_config(config_path=args.config, validate_providers=False)
    bookings = call_booking.load(config.call_bookings_file)
    if not bookings:
        print(f"No bookings in {config.call_bookings_file}")
        return
    for booking in sorted(bookings, key=lambda b: b.start_time):
        print(
            f"{booking.task_id}\t{booking.manager_email}\t"
            f"{booking.start_time.isoformat()}"
        )


def cmd_bookings_rematch(args: argparse.Namespace) -> None:
    """Clear the unmatched mark so the polling loop reconsiders a recording."""
    config = load_config(config_path=args.config, validate_providers=False)
    service = build_drive_service(config=config)
    booking_gate.clear_mark(service, args.target)
    print(
        f"Cleared the unmatched mark on {args.target}; "
        f"the next polling cycle will reconsider it"
    )
```

Register the subcommands next to the `speakers` parser:

```python
    p_bookings = sub.add_parser(
        "bookings",
        help="Inspect received call bookings and revive skipped recordings",
    )
    bookings_sub = p_bookings.add_subparsers(dest="bookings_command", required=True)
    p_bookings_list = bookings_sub.add_parser(
        "list", help="Print the call bookings currently in the journal"
    )
    p_bookings_list.set_defaults(func=cmd_bookings_list)
    p_bookings_rematch = bookings_sub.add_parser(
        "rematch",
        help="Clear the unmatched mark on a Drive MP4 so it is reconsidered",
    )
    p_bookings_rematch.add_argument("target", help="Drive MP4 file ID")
    p_bookings_rematch.set_defaults(func=cmd_bookings_rematch)
```

- [ ] **Step 4: Extend `cmd_doctor`**

In `cmd_doctor`, alongside the existing reporting lines, add:

```python
    print(
        f"call_booking: enabled={config.call_booking_enabled}, "
        f"listen={config.call_booking_listen_host}:{config.call_booking_listen_port}, "
        f"token={'set' if config.call_booking_token else 'unset'}, "
        f"threshold_minutes={config.call_booking_threshold_minutes}, "
        f"disable_recognition={config.call_booking_disable_recognition}"
    )
    print(
        f"planfix: url={'set' if config.planfix_create_comment_url else 'unset'}, "
        f"token={'set' if config.planfix_token else 'unset'}, "
        f"presets={', '.join(config.planfix_presets) or '(none)'}"
    )
    print(f"call bookings journal: {config.call_bookings_file}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v && uv run ruff check`
Expected: PASS. Pre-existing `doctor` output assertions may need the new lines accounted for; extend them rather than removing the new output.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat: add gdstt bookings list and rematch"
```

---

### Task 11: Deployment and documentation

**Files:**
- Modify: `docker-compose.yml`, `AGENTS.md`, `README.md`, `skills/gdstt-cli/SKILL.md`
- Test: `tests/test_docker_deploy.py`, `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no new symbols.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_docker_deploy.py`:

```python
def test_compose_publishes_the_booking_receiver_port():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["google-drive-video-stt"]

    assert "8080:8080" in service["ports"]
```

Append to `tests/test_skill_docs.py` (matching that file's existing assertion style):

```python
def test_skill_documents_the_bookings_commands():
    text = Path("skills/gdstt-cli/SKILL.md").read_text(encoding="utf-8")

    assert "gdstt bookings list" in text
    assert "gdstt bookings rematch" in text


def test_agents_documents_the_call_booking_invariants():
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "call_booking" in text
    assert "planfix" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_docker_deploy.py tests/test_skill_docs.py -v`
Expected: FAIL — `KeyError: 'ports'` and missing strings.

- [ ] **Step 3: Publish the port**

In `docker-compose.yml`, add to the service:

```yaml
    ports:
      # The call-booking receiver. Keep it behind a reverse proxy that terminates TLS:
      # the bearer token and the booking payload must not cross the network in plain
      # text. Change both sides together with call_booking.listen_port in config.yml.
      - "8080:8080"
```

- [ ] **Step 4: Document the feature in `AGENTS.md`**

Add to the Architecture section, after the completion-webhook paragraph:

```markdown
**Call bookings and Planfix** (`src/booking_server.py`, `src/call_booking.py`,
`src/meeting_time.py`, `src/booking_gate.py`, `src/planfix.py`): when
`call_booking.enabled` is set, `main()` starts a stdlib HTTP receiver in a daemon
thread. `POST /` (bearer-authenticated, `{start_time, task_id, manager_email}`)
appends a booking to `<GDSTT_HOME>/call_bookings.jsonl`; `GET /health` answers 200.
`run_once` resolves each pending mp4 against that journal via `booking_gate.resolve`:
the meeting start time is parsed out of the Drive file name (not `createdTime`,
which is the *upload* time — start plus the call's length), matched against the
folder employee's email within `call_booking.threshold_minutes`, nearest booking
wins. On the success path `process_item` posts the `planfix.presets` artifacts, each
under its own heading, into the matched task via `planfix.create_comment_url`.
```

Add to Core invariants:

```markdown
- The booking gate applies to the polling loop only. `run_once` resolves the
  decision, blocks the file, and counts `skipped_unmatched`; the manual commands
  (`process`, `latest`, `transcribe`, `reprocess`) let `process_item` resolve its own
  decision, which yields the `task_id` without the gate. Processing a marked file by
  hand is the supported way to revive it, alongside `gdstt bookings rematch`.
- `booking_match=none` is written **only** while `booking_server.is_running()`. If
  the receiver never bound its port, every recording looks unmatched, and marking
  them would silently retire the whole backlog; with the receiver down the loop skips
  without marking and the files wait.
- The Planfix comment is idempotent through the `planfix_comment_task_id`
  appProperty, written only after a successful POST. `process_item` reaches its
  success path again whenever a later cycle backfills a newly configured preset, and
  without the marker that pass would post a duplicate comment.
- `load_config` rejects `call_booking.enabled` without an `authorization_token`, and
  `disable_recognition` while any `folders` entry lacks an `email` — that folder
  could never match a booking, so it would never be transcribed again.
- `bookings list` / `bookings rematch` use `load_config(validate_providers=False)`;
  `rematch` touches only Drive metadata and spends nothing.
```

- [ ] **Step 5: Document setup in `README.md`**

Add a section:

```markdown
## Call bookings and Planfix

An external system can tell gdstt about upcoming calls so each recording is linked to
its Planfix task.

1. Enable the receiver in `config.yml`:

   ```yaml
   call_booking:
     enabled: true
     listen_host: 0.0.0.0
     listen_port: 8080
     authorization_token: <a long random string>
     threshold_minutes: 15
     disable_recognition: false
   planfix:
     create_comment_url: https://<your-host>/agent/leads/tool/planfix_create_comment
     token: <planfix webhook token>
     presets: [keypoints]
   ```

2. Publish port 8080 (already in `docker-compose.yml`) and put a TLS-terminating
   reverse proxy in front of it. The bearer token and the booking payload must not
   cross the network in plain text.

3. Point the external system at `POST https://<your-host>/` with
   `Authorization: Bearer <authorization_token>` and this body:

   ```json
   {"start_time": "2026-08-11T07:00:00.000000Z", "task_id": "851030", "manager_email": "manager@example.com"}
   ```

   `task_id` must be numeric. `GET /health` returns 200 for probes.

4. `manager_email` is matched against the `email` of the `folders` entry the
   recording lives in, and `start_time` against the meeting time in the recording's
   name, within `threshold_minutes`.

Set `disable_recognition: true` once bookings are flowing to stop transcribing
recordings that match no booked call. Those get marked on Drive and skipped for good;
`gdstt bookings list` shows what the matcher had, and `gdstt bookings rematch <file-id>`
revives one.
```

- [ ] **Step 6: Document the operator workflow in `skills/gdstt-cli/SKILL.md`**

Add to the command reference:

```markdown
- `gdstt bookings list` — print the received call bookings (task id, manager email,
  start time) after dedupe and pruning. Start here when a recording was skipped.
- `gdstt bookings rematch <file-id>` — clear the unmatched mark on a Drive MP4 so the
  next polling cycle reconsiders it. Touches only metadata, spends nothing.
```

Add a troubleshooting entry:

```markdown
### A recording was skipped and never transcribed

The polling loop skips a recording when `call_booking.disable_recognition` is on and
it matched no booked call, and marks it so it is not reconsidered. Diagnose in order:

1. `gdstt bookings list` — is there a booking for that manager at that time at all?
2. Does the recording's Drive name carry a meeting time (`… - 2026/08/08 09:00
   GMT+04:00 – Recording`)? A renamed or hand-uploaded file has none and can never
   match.
3. Does the `folders` entry for that folder have the manager's `email`?
4. Once fixed: `gdstt bookings rematch <file-id>`, or just process it directly with
   `gdstt process <file-id>` — manual commands ignore both the mark and the gate.
```

Bump the `version` and `last_updated` fields in the skill's frontmatter.

- [ ] **Step 7: Run the full suite**

Run: `uv run ruff check && uv run pytest`
Expected: PASS, the whole suite green.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml AGENTS.md README.md skills/gdstt-cli/SKILL.md \
        tests/test_docker_deploy.py tests/test_skill_docs.py
git commit -m "docs: document the call-booking receiver and Planfix comments"
```

---

## Manual verification

After Task 11, verify against a real deployment before opening the PR:

1. `gdstt config init` in a scratch `GDSTT_HOME`, then enable `call_booking` with a
   token and `planfix.create_comment_url` pointing at the test task from
   `data/send-to-planfix-example.sh`.
2. `gdstt doctor` — the new `call_booking` and `planfix` lines appear and the tokens
   read `set`, never their values.
3. Start `gdstt run`, then from another shell:
   `curl -X POST http://127.0.0.1:8080/ -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{"start_time":"<a real recording time>","task_id":"861300","manager_email":"<folder email>"}'`
   → `204`, and the line lands in `<GDSTT_HOME>/call_bookings.jsonl`.
4. Repeat with a wrong token → `401`; with `{"task_id":"abc"}` → `400`.
5. `gdstt bookings list` shows the booking.
6. Let a real recording process → a comment appears on Planfix task 861300 with the
   keypoints text; a second `gdstt run-once` posts no duplicate.
7. Set `disable_recognition: true`, drop a recording with no booking into a watched
   folder → the cycle summary reports `skipped_unmatched=1`, the mp4 gains
   `booking_match=none`, and a second cycle does not re-log it.
8. `gdstt bookings rematch <file-id>` → the next cycle reconsiders it.
9. Occupy port 8080 with something else and start `gdstt run` → it logs the bind
   failure, sends a Telegram alert, keeps polling, and marks nothing.
