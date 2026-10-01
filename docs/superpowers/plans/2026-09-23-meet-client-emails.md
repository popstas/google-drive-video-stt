# Client Emails From the Calendar Event Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every processed call carries the invited outsiders' email addresses (the client) in its meta document and its Telegram summary, read from the calendar event behind the call.

**Architecture:** A new `src/calendar_api.py` picks the calendar event nearest to the call start that has a Meet link and filters its attendees down to outside addresses. `main._client_emails` calls it (delegated as the folder's employee) from `_write_call_documents`, never failing the recording. `meta_doc.build` stores the list as the code field `client_emails`; `_telegram_summary` renders it as an `Email клиента:` line. Opt-in via `calendar.client_emails`.

**Tech Stack:** Python 3.11, google-api-python-client (Calendar v3, `build("calendar", "v3")`), google-auth service-account delegation, pytest + pytest-mock, ruff.

**Spec:** `docs/superpowers/specs/2026-09-23-meet-client-emails-design.md`

## Global Constraints

- Scope: exactly `https://www.googleapis.com/auth/calendar.events.readonly`.
- Config key: `calendar.client_emails`, default `false`.
- Meta field name: `client_emails` (a list), placed right after `client` in `CODE_FIELDS`.
- Telegram line text: `Email клиента: <a>, <b>`; Telegram only, never the Planfix comment.
- Lookup failure: `logger.warning` + `[]`; never `notify.notify_error`, never raise out of `_write_call_documents`.
- Time window: `config.call_booking_threshold_minutes` minutes either side of the call start.
- Run tests with `.venv/bin/pytest -q`, lint with `.venv/bin/ruff check` (line length 100).
- `skills/gdstt-cli/SKILL.md` must stay ≤ 550 lines (`tests/test_skill_docs.py`).
- Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. The pre-commit hook regenerates `CHANGELOG.md`; when the commit fails with "files were modified by this hook", `git add CHANGELOG.md` and commit again.

## Review Focus

- An event whose `start` has only `date` (all-day) sits in the window → skipped, never crashes on a missing `dateTime`.
- A cancelled event (`status: cancelled`) in the window → skipped, a rescheduled Calendly slot must not win over the live one.
- An attendee entry with no `email`, or `own_domains` / attendee address in different case → no crash, comparison case-insensitive.
- `meeting_start` naive (no tzinfo) → treated as UTC rather than raising `TypeError` on subtraction with aware event times.
- The calendar lookup raising (403 scope not granted, `SERVICE_DISABLED`, `RefreshError` wrapped as `AuthError`) → the `.meta.yml` is still written with `client_emails: []`.

---

### Task 1: Calendar lookup and delegated Calendar client

**Files:**
- Create: `src/calendar_api.py`
- Modify: `src/auth.py` (add `CALENDAR_SCOPES` next to `MEET_SCOPES`, `build_calendar_service` after `build_meet_service`)
- Test: `tests/test_calendar_api.py` (new), `tests/test_auth.py`

**Interfaces:**
- Produces: `calendar_api.client_emails(service, *, start: datetime, own_domains: Iterable[str], window_minutes: int) -> list[str]`
- Produces: `auth.CALENDAR_SCOPES: list[str]`, `auth.build_calendar_service(*, config: Config, subject: str)` returning a Calendar v3 resource.

- [ ] **Step 1: Write the failing tests**

`tests/test_calendar_api.py`:

```python
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
```

Append to `tests/test_auth.py`:

```python
def test_calendar_is_asked_as_the_employee_with_the_read_only_scope(tmp_path, mocker):
    cfg = _delegating_config(tmp_path)
    base = MagicMock()
    delegated = MagicMock()
    base.with_subject.return_value = delegated
    from_info = mocker.patch(
        "src.auth.service_account.Credentials.from_service_account_info",
        return_value=base,
    )
    build_mock = mocker.patch("src.auth.build", return_value="calendar service")

    result = auth.build_calendar_service(config=cfg, subject="one@example.com")

    assert result == "calendar service"
    from_info.assert_called_once_with(
        SERVICE_ACCOUNT_INFO,
        scopes=["https://www.googleapis.com/auth/calendar.events.readonly"],
    )
    base.with_subject.assert_called_once_with("one@example.com")
    build_mock.assert_called_once_with(
        "calendar", "v3", credentials=delegated, cache_discovery=False
    )


def test_calendar_without_delegation_is_refused_with_the_reason(tmp_path, mocker):
    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.uses_delegation = False
    build_mock = mocker.patch("src.auth.build")

    with pytest.raises(auth.AuthError) as excinfo:
        auth.build_calendar_service(config=cfg, subject="one@example.com")

    assert "calendar.events.readonly" in str(excinfo.value)
    build_mock.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest -q tests/test_calendar_api.py tests/test_auth.py -k calendar`
Expected: FAIL — `ImportError: cannot import name 'calendar_api'` / `AttributeError: ... build_calendar_service`.

- [ ] **Step 3: Implement**

`src/calendar_api.py`:

```python
"""Who was invited to a call, by address -- the one thing Meet will not say.

The Meet API names participants and gives opaque user ids; the calendar event behind
the call carries the invitees' addresses, a Calendly invitee included (Calendly writes
the booking into the host's calendar). The event is found by time, not by Meet code:
the call's start is exact, and the code would need a Meet request the walk cannot make.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable


def _utc(moment: dt.datetime) -> dt.datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def _rfc3339(moment: dt.datetime) -> str:
    return _utc(moment).isoformat().replace("+00:00", "Z")


def _event_start(event: dict) -> dt.datetime | None:
    """When a timed event begins; ``None`` for an all-day one or an unreadable time."""
    raw = (event.get("start") or {}).get("dateTime")
    if not raw:
        return None
    try:
        return _utc(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def _has_meet(event: dict) -> bool:
    if event.get("hangoutLink"):
        return True
    entry_points = (event.get("conferenceData") or {}).get("entryPoints") or []
    return any(point.get("entryPointType") == "video" for point in entry_points)


def client_emails(
    service,
    *,
    start: dt.datetime,
    own_domains: Iterable[str],
    window_minutes: int,
) -> list[str]:
    """The outside invitees of the Meet event nearest to ``start``, sorted.

    Outside means: not the calendar's owner, not a room, and not at one of
    ``own_domains``. An empty list is the answer for "no such event" and for "only
    colleagues were invited" alike -- the caller has nothing to do in either case.
    """
    start = _utc(start)
    window = dt.timedelta(minutes=window_minutes)
    response = service.events().list(
        calendarId="primary",
        singleEvents=True,
        timeMin=_rfc3339(start - window),
        timeMax=_rfc3339(start + window),
    ).execute()

    nearest: tuple[dt.timedelta, dict] | None = None
    for event in response.get("items") or []:
        if event.get("status") == "cancelled" or not _has_meet(event):
            continue
        begins = _event_start(event)
        if begins is None:
            continue
        distance = abs(begins - start)
        if nearest is None or distance < nearest[0]:
            nearest = (distance, event)
    if nearest is None:
        return []

    own = {domain.strip().lower() for domain in own_domains}
    emails: set[str] = set()
    for attendee in nearest[1].get("attendees") or []:
        if attendee.get("self") or attendee.get("resource"):
            continue
        email = str(attendee.get("email") or "").strip().lower()
        if "@" not in email or email.rsplit("@", 1)[1] in own:
            continue
        emails.add(email)
    return sorted(emails)
```

In `src/auth.py`, after `MEET_SCOPES`:

```python
# Reading the invitees of an employee's events. Read-only and events-only: the
# service needs attendee addresses, never calendar settings or write access.
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
]
```

After `build_meet_service`:

```python
def build_calendar_service(*, config: Config, subject: str):
    """A Calendar client acting as ``subject``, for the invitees of their calls.

    Delegation only, like Meet: an employee's calendar answers for its owner.
    """
    if not config.uses_delegation:
        raise AuthError(
            "Reading calendar invitees needs a service account with domain-wide "
            "delegation. Configure google.service_account (or "
            f"google.service_account_file) and authorize {' '.join(CALENDAR_SCOPES)}."
        )
    creds = _delegated_credentials(config, subject, scopes=CALENDAR_SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest -q tests/test_calendar_api.py tests/test_auth.py && .venv/bin/ruff check`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/calendar_api.py src/auth.py tests/test_calendar_api.py tests/test_auth.py
git commit -m "feat: read a call's outside invitees from its calendar event"
```

---

### Task 2: `calendar.client_emails` config flag

**Files:**
- Modify: `src/config.py` (dataclass field near `meet_skip_empty_calls`; parse near line 1174; `Config(...)` kwargs near 1415; `_default_config_dict`; `_config_to_yaml_dict` next to `"meet"`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.calendar_client_emails: bool` (default `False`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_calendar_client_emails_defaults_to_off(tmp_path):
    assert _load_config(tmp_path, {}).calendar_client_emails is False


def test_calendar_client_emails_is_read(tmp_path):
    config = _load_config(tmp_path, {"calendar": {"client_emails": True}})
    assert config.calendar_client_emails is True


def test_default_config_writes_the_calendar_client_emails_key():
    """Off in a fresh config: the admin has to authorize the scope first."""
    assert _default_config_dict()["calendar"] == {"client_emails": False}


def test_calendar_client_emails_round_trips(tmp_path):
    config = _load_config(tmp_path, {"calendar": {"client_emails": True}})
    assert _config_to_yaml_dict(config)["calendar"] == {"client_emails": True}
```

(If `_config_to_yaml_dict` is not yet imported in `tests/test_config.py`, import it from `src.config` alongside `_default_config_dict`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest -q tests/test_config.py -k calendar`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'calendar_client_emails'`.

- [ ] **Step 3: Implement**

Dataclass field, after `meet_skip_empty_calls: bool = True`:

```python
    # Read the invited outsiders' addresses from each call's calendar event. Off by
    # default: it needs the calendar.events.readonly scope authorized first.
    calendar_client_emails: bool = False
```

Parse, after `meet_skip_empty_calls = ...`:

```python
    calendar_client_emails = _yaml_bool(
        _as_mapping(raw.get("calendar"), "calendar").get("client_emails"), default=False
    )
```

`Config(...)`: add `calendar_client_emails=calendar_client_emails,` after `meet_skip_empty_calls=meet_skip_empty_calls,`.

`_default_config_dict`, after the `"planfix"` block:

```python
        # true = add the invited outsiders' emails (the client) to .meta.yml and the
        # Telegram summary. Needs calendar.events.readonly authorized for the service
        # account's client id and the Calendar API enabled in its project.
        "calendar": {"client_emails": False},
```

`_config_to_yaml_dict`, after the `"meet"` block:

```python
        "calendar": {"client_emails": config.calendar_client_emails},
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest -q tests/test_config.py && .venv/bin/ruff check`
Expected: PASS. A pre-existing test asserting the exact key set of the default config may fail — add `"calendar"` to its expectation.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add the calendar.client_emails switch"
```

---

### Task 3: Wire the emails into the meta document and the Telegram summary, and document it

**Files:**
- Modify: `src/meta_entity.py` (`CODE_FIELDS`), `src/meta_doc.py` (`build`), `src/main.py` (imports, `_client_emails`, `_write_call_documents`, `_telegram_link_lines`)
- Modify: `README.md`, `AGENTS.md`, `skills/gdstt-cli/SKILL.md`, `docs/TODO.md`
- Test: `tests/test_meta_doc.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `calendar_api.client_emails(...)`, `auth.build_calendar_service(...)`, `Config.calendar_client_emails`.
- Produces: `meta_doc.build(..., client_emails: Iterable[str] = ())` → `document["client_emails"]: list[str]`; `main._client_emails(item: dict, file_name: str, folder_id: str, config: Config) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_meta_doc.py`:

```python
def test_build_carries_the_client_emails(config_with_folder):
    document = _document(config_with_folder, client_emails=["client@gmail.com"])
    assert document["client_emails"] == ["client@gmail.com"]


def test_build_leaves_the_client_emails_empty_by_default(config_with_folder):
    assert _document(config_with_folder)["client_emails"] == []


def test_client_emails_follow_the_client_in_the_field_order():
    fields = meta_entity.CODE_FIELDS
    assert fields.index("client_emails") == fields.index("client") + 1
```

Append to `tests/test_main.py` (near the Telegram summary tests; `replace`, `MagicMock`, `EmployeeFolder`, `meta_entity`, `BookingDecision` are already imported there):

```python
_CALENDAR_START = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


def _calendar_config(**overrides):
    config = make_config(
        folders=[
            EmployeeFolder("folderA", name="Kate", email="kate@expertizeme.org"),
            EmployeeFolder("folderB", name="Bob", email="bob@Other-Own.com"),
        ],
    )
    fields = {
        "calendar_client_emails": True,
        "google_service_account": {"client_email": "sa@p.iam.gserviceaccount.com"},
        **overrides,
    }
    return replace(config, **fields)


def test_client_emails_ask_the_folders_calendar(mocker):
    build = mocker.patch("src.main.auth.build_calendar_service", return_value="svc")
    lookup = mocker.patch(
        "src.main.calendar_api.client_emails", return_value=["client@gmail.com"]
    )

    emails = main._client_emails(
        {"meeting_start": _CALENDAR_START}, "rec.mp4", "folderA", _calendar_config()
    )

    assert emails == ["client@gmail.com"]
    assert build.call_args.kwargs["subject"] == "kate@expertizeme.org"
    kwargs = lookup.call_args.kwargs
    assert kwargs["start"] == _CALENDAR_START
    assert set(kwargs["own_domains"]) == {"expertizeme.org", "other-own.com"}
    assert kwargs["window_minutes"] == 15


def test_client_emails_fall_back_to_the_time_in_the_name(mocker):
    mocker.patch("src.main.auth.build_calendar_service", return_value="svc")
    lookup = mocker.patch("src.main.calendar_api.client_emails", return_value=[])
    name = "Call - 2026/09/22 14:00 GMT+04:00 – Recording.mp4"

    main._client_emails({}, name, "folderA", _calendar_config())

    assert lookup.call_args.kwargs["start"] == _CALENDAR_START


@pytest.mark.parametrize(
    "item, name, folder, overrides",
    [
        ({"meeting_start": _CALENDAR_START}, "rec.mp4", "folderA",
         {"calendar_client_emails": False}),
        ({"meeting_start": _CALENDAR_START}, "rec.mp4", "folderA",
         {"google_service_account": None}),
        ({"meeting_start": _CALENDAR_START}, "rec.mp4", "unknown", {}),
        ({}, "no time here.mp4", "folderA", {}),
    ],
    ids=["flag-off", "no-delegation", "no-folder-email", "no-start"],
)
def test_client_emails_make_no_request_without_what_they_need(
    mocker, item, name, folder, overrides
):
    build = mocker.patch("src.main.auth.build_calendar_service")

    assert main._client_emails(item, name, folder, _calendar_config(**overrides)) == []
    build.assert_not_called()


def test_a_calendar_failure_costs_only_the_emails(mocker, caplog):
    mocker.patch(
        "src.main.auth.build_calendar_service",
        side_effect=main.AuthError("unauthorized_client"),
    )

    emails = main._client_emails(
        {"meeting_start": _CALENDAR_START}, "rec.mp4", "folderA", _calendar_config()
    )

    assert emails == []
    assert "AuthError" in caplog.text


def test_the_meta_document_carries_the_client_emails(tmp_path, mocker):
    mocker.patch("src.main._client_emails", return_value=["client@gmail.com"])
    document = _write_documents(_stt_config(tmp_path), tmp_path, {})
    assert document["client_emails"] == ["client@gmail.com"]


def test_telegram_summary_names_the_clients_email():
    text = main._telegram_summary(
        {"keypoints": "Задачи: раз"},
        ("keypoints",),
        {**_LINKED_DOCUMENT, "client_emails": ["a@x.com", "b@y.com"]},
        ("subject",),
        meta_entities=meta_entity.default_entities(),
    )

    assert "Email клиента: a@x.com, b@y.com" in text
    assert text.index("Email клиента") < text.index("Planfix:")


def test_planfix_comment_does_not_carry_the_clients_email():
    html = main._planfix_description(
        {"keypoints": "Задачи: раз"},
        ("keypoints",),
        {**_LINKED_DOCUMENT, "client_emails": ["a@x.com"]},
        ("subject",),
        meta_entities=meta_entity.default_entities(),
    )

    assert "a@x.com" not in html
```

Check the top of `tests/test_main.py` imports `datetime`, `timezone` and `pytest`; add what is missing.

`Config.google_service_account` is the field `uses_delegation` reads — if the property also accepts `google_service_account_file`, `None` for both is what the "no-delegation" case needs; `make_config` leaves both unset.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest -q tests/test_meta_doc.py tests/test_main.py -k "client_email or clients_email"`
Expected: FAIL — `TypeError: build() got an unexpected keyword argument 'client_emails'`, `AttributeError: module 'src.main' has no attribute '_client_emails'`.

- [ ] **Step 3: Implement**

`src/meta_entity.py` — in `CODE_FIELDS`, after `"client",` add `"client_emails",`.

`src/meta_doc.py` — `build` gains `client_emails: Iterable[str] = (),` after `calendly_event_uuid` (import `Iterable` from `collections.abc`), and the document gains, right after `"client": _client(file_name),`:

```python
        "client_emails": list(client_emails),
```

`src/main.py`:

- Add `auth` and `calendar_api` to the `from src import (...)` block (alphabetical: `auth,` before `booking_gate,`, `calendar_api,` after `booking_server,`).
- Add above `_write_call_documents`:

```python
def _client_emails(item: dict, file_name: str, folder_id: str, config: Config) -> list[str]:
    """The call's invited outsiders, from the employee's calendar; ``[]`` on any doubt.

    A nicety, not a requirement: a missing scope or a calendar outage is a warning and
    an empty list, never a failed recording and never an escalation.
    """
    if not config.calendar_client_emails or not config.uses_delegation:
        return []
    folder = config.folder_by_id(folder_id)
    subject = folder.email.strip() if folder else ""
    if not subject:
        return []
    start = item.get("meeting_start") or parse_meeting_start(file_name)
    if start is None:
        return []
    own_domains = {
        entry.email.rsplit("@", 1)[1].strip().lower()
        for entry in config.folders
        if "@" in entry.email
    }
    try:
        service = auth.build_calendar_service(config=config, subject=subject)
        return calendar_api.client_emails(
            service,
            start=start,
            own_domains=own_domains,
            window_minutes=config.call_booking_threshold_minutes,
        )
    except Exception as exc:
        logger.warning(
            "Could not read the calendar invitees of %s: %s", file_name, type(exc).__name__
        )
        return []
```

- In `_write_call_documents`, pass to `meta_doc.build(...)`:

```python
        client_emails=_client_emails(item, file_name, folder_id, config),
```

- Rename `_telegram_link_lines` → `_telegram_extra_lines` (both definition and its one caller in `_telegram_summary`) and make it emit the email line first:

```python
def _telegram_extra_lines(document: dict[str, object] | None) -> list[str]:
    """The client's email and links to the Planfix task and Calendly booking.

    Telegram only: the CRM already holds the client, and a comment linking to its
    own task is noise.
    """
    if not document:
        return []
    lines = []
    emails = document.get("client_emails") or []
    if isinstance(emails, list) and emails:
        lines.append("Email клиента: " + ", ".join(str(email) for email in emails))
    for label, field_name in _TELEGRAM_LINKS:
        url = " ".join(str(document.get(field_name) or "").split())
        if url:
            lines.append(f"[{label}]({url})")
    return lines
```

Update the comment above `_TELEGRAM_LINKS` if it still says only "links".

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check`
Expected: all PASS. If a test pins the exact `CODE_FIELDS` / meta YAML field list, add `client_emails` after `client` there.

- [ ] **Step 5: Document**

- `README.md`:
  - In the Meet discovery prerequisites table (the row for `meetings.space.readonly`), add a row:
    `| https://www.googleapis.com/auth/calendar.events.readonly authorized for the same client id, and the Google Calendar API enabled in the service account's project | Google Admin Console / Google Cloud Console | only with calendar.client_emails: true; without it each call logs a warning and client_emails stays empty |`
  - In the config reference table, after `call_booking.calendly_url`, add:
    `| calendar.client_emails | false | Read the invited outsiders' addresses (the client) from each call's calendar event into client_emails in the meta document and an "Email клиента:" line of the Telegram summary. Needs delegation and the calendar.events.readonly scope |`
  - In "Telegram summaries", extend the paragraph about the extra header lines: first `Email клиента: <addresses>` (from `calendar.client_emails`), then the Planfix and Calendly links.
- `AGENTS.md`: extend the `_telegram_link_lines` bullet (rename to `_telegram_extra_lines`) with: "`client_emails` comes from `calendar_api.client_emails` via `main._client_emails` (opt-in `calendar.client_emails`, delegated `calendar.events.readonly` as the folder's email): the Meet-linked event nearest the call start, attendees minus self/resources/the folders' own domains; any failure is a warning and `[]`."
- `skills/gdstt-cli/SKILL.md`: append to the same long line that mentions `Calendly: <url>`: ` `Email клиента:` - из календаря (`calendar.client_emails: true`, scope `calendar.events.readonly`).` Keep the file ≤ 550 lines (`wc -l`).
- `docs/TODO.md`: mark `- [x] добавить список email участников из созвона Meet`.

Run: `.venv/bin/pytest -q tests/test_skill_docs.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/meta_entity.py src/meta_doc.py src/main.py tests/test_meta_doc.py tests/test_main.py \
  README.md AGENTS.md skills/gdstt-cli/SKILL.md docs/TODO.md
git commit -m "feat: put the client's email from the calendar into meta and telegram"
```

---

### Task 4: Deploy to us1 and rerun the last two calls

Host facts: SSH only as `us1.dev.expertizeme.org`; deploy dir `~/projects/python/google-drive-video-stt` (branch `pr25`, a git checkout whose `src`/`data` are bind-mounted); compose dir `~/projects/docker/google-drive-video-stt`; container `google-drive-video-stt-app-1`. No dependency changed, so no rebuild. The service account is `drive-audit@expertizeme-docs.iam.gserviceaccount.com`, client id `101429969751747416130`; the user has authorized the calendar scope for it.

- [ ] **Step 1: Ship the commits without GitHub**

```bash
git push us1.dev.expertizeme.org:projects/python/google-drive-video-stt pr25:refs/heads/deploy-client-emails
ssh us1.dev.expertizeme.org 'cd ~/projects/python/google-drive-video-stt && git merge --ff-only deploy-client-emails && git branch -d deploy-client-emails && git log --oneline -1'
```

Expected: fast-forward to the local `HEAD`.

- [ ] **Step 2: Enable the flag, with a backup**

```bash
ssh us1.dev.expertizeme.org 'cd ~/projects/python/google-drive-video-stt && cp data/config.yml data/config.yml.bak-2026-09-23-calendar && docker exec google-drive-video-stt-app-1 gdstt config set calendar.client_emails true && docker exec google-drive-video-stt-app-1 gdstt config get calendar.client_emails'
```

Expected: `true` (a YAML boolean, not the string `"true"` — check `grep -n -A1 "^calendar:" data/config.yml`).

- [ ] **Step 3: Restart and confirm the loop is healthy**

```bash
ssh us1.dev.expertizeme.org 'cd ~/projects/docker/google-drive-video-stt && docker compose restart && sleep 20 && docker compose logs --since 1m | tail -40'
```

Expected: no traceback; the loop starts its cycle.

- [ ] **Step 4: Prove the scope and the API before touching recordings**

```bash
ssh us1.dev.expertizeme.org 'docker exec google-drive-video-stt-app-1 python -c "
from src.config import load_config
from src import auth
cfg = load_config(validate_providers=False)
svc = auth.build_calendar_service(config=cfg, subject=cfg.folders[0].email)
print(len(svc.events().list(calendarId=\"primary\", maxResults=1).execute().get(\"items\", [])))
"'
```

Expected: a number. `unauthorized_client` → the scope is not in the delegation entry yet; `SERVICE_DISABLED` → enable https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=expertizeme-docs. Stop and report either to the user.

- [ ] **Step 5: Pick the last two delivered calls**

```bash
ssh us1.dev.expertizeme.org 'docker exec google-drive-video-stt-app-1 gdstt telegram sent --limit 2'
ssh us1.dev.expertizeme.org 'docker exec google-drive-video-stt-app-1 gdstt doctor | sed -n "/Presets/,/^$/p"'
```

Note both file ids and names, and the reprocess stage number of the `meta` preset (the cheapest stage that still rewrites `.meta.yml`/`.stt` and resends).

- [ ] **Step 6: Clear their Telegram marker and reprocess**

Clearing `telegram_sent_chat_id` is what lets the summary go out again; the `planfix_comment_task_id` marker is left alone, so Planfix gets no duplicate comment. The resend goes to every chat the folder routes the call to (`telegram`, plus `telegram_calendly` for booked calls).

For each `<file-id>`:

```bash
ssh us1.dev.expertizeme.org 'docker exec google-drive-video-stt-app-1 python -c "
from src.config import load_config
from src.cli import _fleet
from src import drive
fleet = _fleet(load_config(validate_providers=False))
fid = \"<file-id>\"
for folder in fleet.config.folders:
    svc = fleet.service_for(folder.folder_id)
    try:
        drive.set_file_app_properties(svc, fid, {drive.TELEGRAM_SENT_CHAT_ID_PROPERTY: None})
        print(\"cleared via\", folder.email); break
    except Exception as exc:
        print(folder.email, type(exc).__name__)
"'
ssh us1.dev.expertizeme.org 'docker exec google-drive-video-stt-app-1 gdstt reprocess <file-id> <meta-stage>'
```

Expected: reprocess log shows the meta stage, `Sending the Telegram summary for ... to chat ...`, no `Could not read the calendar invitees`.

- [ ] **Step 7: Verify the artifacts**

```bash
ssh us1.dev.expertizeme.org 'cd ~/projects/python/google-drive-video-stt/data/results && ls -t *.meta.yml | head -2 | xargs grep -n -A2 "client_emails"'
ssh us1.dev.expertizeme.org 'docker exec google-drive-video-stt-app-1 gdstt telegram sent --limit 2'
```

Expected: non-empty `client_emails` on a call that had an outside invitee; the markers re-written with the chats. Report to the user: both calls, the emails found (or why a call has none — e.g. the event had only colleagues), and that the Telegram messages were re-posted. The `Calendly:` line appears only once the booking sender passes `calendly_event_uuid` and `call_booking.calendly_url` is set — say so.
