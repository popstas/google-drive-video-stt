# Drive modifiedTime Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop appProperty writes from moving a Drive file's `modifiedTime`, and restore the date on the 1274 recordings the backlog marking already moved.

**Architecture:** `drive.set_file_app_properties` reads the file's current `modifiedTime` and sends it back in the same `files().update()` call, so a bookkeeping write lands as a no-op on the date. A new `gdstt bookings restore-dates` command repairs the existing damage: two thin Drive primitives fetch timestamps and write one back, a pure selection function decides which files qualify, and the command wires them together behind `--dry-run`.

**Tech Stack:** Python 3.11+, `googleapiclient` (Drive API v3), pytest with `pytest-mock`, `uv` for running everything.

**Spec:** `docs/superpowers/specs/2026-08-13-drive-modified-time-design.md`

## Global Constraints

- Python 3.11+, `from __future__ import annotations` at the top of every module, `X | None` unions.
- Run everything through `uv`: `uv run pytest`, `uv run ruff check`. Never create another venv, never install packages.
- No new third-party dependencies.
- ruff: line length 100, target py311. Every task ends green on `uv run ruff check` **and** `uv run pytest`.
- Tests mock the Drive service with `MagicMock`; **no test may make a network call**.
- `modifiedTime` and `createdTime` are RFC 3339 strings as Drive returns them (`2026-08-11T22:13:27.539Z`). Never compare them as strings — parse them (see Task 2).
- Never use `git commit --amend` in this repo: a pre-commit hook regenerates `CHANGELOG.md` from commit messages, and amending corrupts the release notes. If the hook reports "files were modified by this hook", re-stage the changed files and run the same `git commit` again. Never pass `--no-verify`.
- Each task ends with its own commit, using the message given in the task.

---

### Task 1: Preserve modifiedTime on appProperty writes

`set_file_app_properties` already promises in its docstring to merge properties
"without changing its content", but Drive treats every `files().update()` as an
edit and moves the file's date. This task makes the function honour its own
promise. It is the whole prevention half of the spec.

**Files:**
- Modify: `src/drive.py:320-335` (`set_file_app_properties`)
- Test: `tests/test_drive.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `set_file_app_properties(service, file_id, app_properties: dict[str, str | None]) -> dict` — same signature as today, now issuing a `files().get(fields="modifiedTime")` before its update.

**Watch out:** `tests/test_drive.py:221` already contains
`test_set_file_app_properties_updates_metadata_only`, which asserts the exact
`update()` call arguments. It **will** fail once the body carries `modifiedTime`.
Update that assertion — do not delete the test.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_drive.py`:

```python
def test_set_file_app_properties_preserves_the_modified_time():
    """Writing a property must not move the file's date.

    Drive counts any files.update as an edit. People sort these shared folders by
    "Last modified", so a bookkeeping write that bumps the date corrupts the column.
    """
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "modifiedTime": "2025-03-14T18:24:53.633Z"
    }
    service.files.return_value.update.return_value.execute.return_value = {"id": "v1"}

    drive.set_file_app_properties(service, "v1", {"booking_match": "none"})

    service.files.return_value.get.assert_called_once_with(
        fileId="v1",
        fields="modifiedTime",
        supportsAllDrives=True,
    )
    service.files.return_value.update.assert_called_once_with(
        fileId="v1",
        body={
            "appProperties": {"booking_match": "none"},
            "modifiedTime": "2025-03-14T18:24:53.633Z",
        },
        fields="id, name, appProperties",
        supportsAllDrives=True,
    )


def test_set_file_app_properties_preserves_the_date_when_deleting_a_property():
    """The rematch path sends a null value to delete a property; same rule applies."""
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "modifiedTime": "2025-03-14T18:24:53.633Z"
    }
    service.files.return_value.update.return_value.execute.return_value = {"id": "v1"}

    drive.set_file_app_properties(service, "v1", {"booking_match": None})

    body = service.files.return_value.update.call_args.kwargs["body"]
    assert body["appProperties"] == {"booking_match": None}
    assert body["modifiedTime"] == "2025-03-14T18:24:53.633Z"


def test_set_file_app_properties_omits_modified_time_when_drive_returns_none():
    """A response without modifiedTime must not send a null date and 400 the request."""
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {}
    service.files.return_value.update.return_value.execute.return_value = {"id": "v1"}

    drive.set_file_app_properties(service, "v1", {"booking_match": "none"})

    body = service.files.return_value.update.call_args.kwargs["body"]
    assert "modifiedTime" not in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_drive.py -k set_file_app_properties -v`

Expected: the three new tests FAIL — `get.assert_called_once_with` raises
`AssertionError: Expected 'get' to be called once. Called 0 times.`, and the body
assertions fail because `modifiedTime` is absent.
`test_set_file_app_properties_updates_metadata_only` still PASSES at this point.

- [ ] **Step 3: Implement the preservation**

Replace `set_file_app_properties` in `src/drive.py`:

```python
def set_file_app_properties(
    service: Any,
    file_id: str,
    app_properties: dict[str, str | None],
) -> dict:
    """Merge appProperties onto a Drive file without changing its content.

    Drive counts every ``files.update`` as an edit: it moves ``modifiedTime``, sets
    ``lastModifyingUser`` and appends "You edited an item" to the activity feed.
    These properties are our own bookkeeping, not a user edit, and people sort these
    shared folders by "Last modified" -- so the date has to survive the write.
    Reading the current value and sending it straight back in the same request keeps
    it exactly where it was.

    Preservation is unconditional rather than opt-in: every call site writes
    bookkeeping, and a flag is something a future call site forgets to pass.
    """
    current = (
        service.files()
        .get(fileId=file_id, fields="modifiedTime", supportsAllDrives=True)
        .execute()
    )
    body: dict[str, Any] = {"appProperties": app_properties}
    modified_time = current.get("modifiedTime")
    if modified_time:
        # A blank value would clear the date rather than preserve it, so only send
        # one Drive actually gave us.
        body["modifiedTime"] = modified_time
    return (
        service.files()
        .update(
            fileId=file_id,
            body=body,
            fields="id, name, appProperties",
            supportsAllDrives=True,
        )
        .execute()
    )
```

- [ ] **Step 4: Update the existing assertion**

In `tests/test_drive.py:221`, `test_set_file_app_properties_updates_metadata_only`
now needs the mocked `get` and the extra body key:

```python
def test_set_file_app_properties_updates_metadata_only():
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "modifiedTime": "2026-01-02T03:04:05.678Z"
    }
    service.files.return_value.update.return_value.execute.return_value = {"id": "v1"}

    drive.set_file_app_properties(service, "v1", {"speaker_names": "[\"A\", \"B\"]"})

    service.files.return_value.update.assert_called_once_with(
        fileId="v1",
        body={
            "appProperties": {"speaker_names": "[\"A\", \"B\"]"},
            "modifiedTime": "2026-01-02T03:04:05.678Z",
        },
        fields="id, name, appProperties",
        supportsAllDrives=True,
    )
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q` — expected: all pass, no failures.
Run: `uv run ruff check` — expected: `All checks passed!`

Other suites exercise this function through `booking_gate` and `main` with
`MagicMock` services; a bare `MagicMock` returns a `MagicMock` from `.execute()`,
whose `.get("modifiedTime")` is also a `MagicMock` (truthy), so those tests keep
passing. If any test asserts the exact update body, update it the same way.

- [ ] **Step 6: Commit**

```bash
git add src/drive.py tests/test_drive.py
git commit -m "fix: keep the Drive modifiedTime when writing appProperties"
```

---

### Task 2: Drive timestamp primitives and the selection rule

Everything the repair needs except the command itself: reading timestamps,
writing one back, and — the part that decides which of 1274 production files get
touched — the predicate.

**Files:**
- Modify: `src/drive.py` (add two functions after `_list_files_by_mime`)
- Modify: `src/booking_gate.py` (add the selection function)
- Test: `tests/test_drive.py`, `tests/test_booking_gate.py`

**Interfaces:**
- Consumes: `drive.BOOKING_MATCH_PROPERTY` (`"booking_match"`, already in `src/drive.py`), `drive.MP4_MIME`, `drive.PAGE_SIZE`.
- Produces:
  - `drive.list_mp4_timestamps(service, folder_id: str) -> list[dict]` — each dict has `id`, `name`, `createdTime`, `modifiedTime`, `appProperties`.
  - `drive.set_file_modified_time(service, file_id: str, modified_time: str) -> dict`
  - `booking_gate.select_stale_marks(files: Iterable[dict]) -> list[tuple[str, str, str]]` — `(file_id, name, created_time)`.

- [ ] **Step 1: Write the failing Drive tests**

Add to `tests/test_drive.py`:

```python
def test_list_mp4_timestamps_requests_the_timestamp_fields_and_pages():
    """The polling loop's listing deliberately omits timestamps; this one needs them."""
    service = MagicMock()
    pages = [
        {"files": [{"id": "v1", "name": "a.mp4"}], "nextPageToken": "page2"},
        {"files": [{"id": "v2", "name": "b.mp4"}]},
    ]
    service.files.return_value.list.return_value.execute.side_effect = pages

    result = drive.list_mp4_timestamps(service, "f1")

    assert [f["id"] for f in result] == ["v1", "v2"]
    first_call = service.files.return_value.list.call_args_list[0].kwargs
    assert "createdTime" in first_call["fields"]
    assert "modifiedTime" in first_call["fields"]
    assert "appProperties" in first_call["fields"]
    assert "'f1' in parents" in first_call["q"]
    assert "video/mp4" in first_call["q"]
    assert service.files.return_value.list.call_args_list[1].kwargs["pageToken"] == "page2"


def test_set_file_modified_time_sends_only_the_date():
    """The marks must survive the repair, or the backlog would be re-transcribed."""
    service = MagicMock()
    service.files.return_value.update.return_value.execute.return_value = {"id": "v1"}

    drive.set_file_modified_time(service, "v1", "2025-03-14T18:24:52.949Z")

    service.files.return_value.update.assert_called_once_with(
        fileId="v1",
        body={"modifiedTime": "2025-03-14T18:24:52.949Z"},
        fields="id, name, modifiedTime",
        supportsAllDrives=True,
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_drive.py -k "list_mp4_timestamps or set_file_modified_time" -v`

Expected: FAIL with `AttributeError: module 'src.drive' has no attribute 'list_mp4_timestamps'`.

- [ ] **Step 3: Implement the two Drive primitives**

Add to `src/drive.py`, directly after `_list_files_by_mime`:

```python
def list_mp4_timestamps(service: Any, folder_id: str) -> list[dict]:
    """Return every mp4 in a folder with its timestamps and appProperties.

    ``_list_files_by_mime`` keeps its field list small because the polling loop calls
    it every cycle. Only the date repair needs ``createdTime``/``modifiedTime``, so it
    asks for them here rather than widening the hot path.
    """
    files: list[dict] = []
    page_token: str | None = None
    query = (
        f"'{folder_id}' in parents and mimeType = '{MP4_MIME}' and trashed = false"
    )
    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields=(
                    "nextPageToken, "
                    "files(id, name, createdTime, modifiedTime, appProperties)"
                ),
                pageSize=PAGE_SIZE,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def set_file_modified_time(service: Any, file_id: str, modified_time: str) -> dict:
    """Set a file's modifiedTime, leaving appProperties and content untouched.

    The body carries only the date: the ``booking_match`` marks must survive, or the
    polling loop would reconsider the whole backlog and re-transcribe it.
    """
    return (
        service.files()
        .update(
            fileId=file_id,
            body={"modifiedTime": modified_time},
            fields="id, name, modifiedTime",
            supportsAllDrives=True,
        )
        .execute()
    )
```

- [ ] **Step 4: Run the Drive tests**

Run: `uv run pytest tests/test_drive.py -k "list_mp4_timestamps or set_file_modified_time" -v`
Expected: PASS.

- [ ] **Step 5: Write the failing selection tests**

Add to `tests/test_booking_gate.py`:

```python
def test_select_stale_marks_picks_marked_files_whose_date_drifted():
    files = [
        {
            "id": "v1",
            "name": "old call.mp4",
            "createdTime": "2025-03-14T18:24:52.949Z",
            "modifiedTime": "2026-08-11T22:13:27.539Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert booking_gate.select_stale_marks(files) == [
        ("v1", "old call.mp4", "2025-03-14T18:24:52.949Z")
    ]


def test_select_stale_marks_skips_files_without_the_mark():
    """Drive nudges modifiedTime a fraction of a second at upload.

    Those files were never touched by us, and resetting them would rewrite real
    history. The mark -- not a time window -- is what identifies our writes.
    """
    files = [
        {
            "id": "v2",
            "name": "untouched.mp4",
            "createdTime": "2026-08-10T15:09:02.818Z",
            "modifiedTime": "2026-08-10T15:09:03.633Z",
            "appProperties": {},
        }
    ]

    assert booking_gate.select_stale_marks(files) == []


def test_select_stale_marks_skips_already_restored_files():
    """A second run must be a no-op, so the repair can be re-run safely."""
    files = [
        {
            "id": "v3",
            "name": "restored.mp4",
            "createdTime": "2025-03-14T18:24:52.949Z",
            "modifiedTime": "2025-03-14T18:24:52.949Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert booking_gate.select_stale_marks(files) == []


def test_select_stale_marks_compares_times_not_strings():
    """Two spellings of the same instant must not read as drift.

    Drive varies the fractional-second width. Compared as text, ".5Z" sorts above
    ".50Z" -- 'Z' outranks '0' -- so a string comparison calls these two equal
    instants a drift and would rewrite a file that nobody touched. Note the widths:
    createdTime is the longer one, which is the only ordering that catches the bug.
    """
    files = [
        {
            "id": "v4",
            "name": "equal.mp4",
            "createdTime": "2025-03-14T18:24:52.50Z",
            "modifiedTime": "2025-03-14T18:24:52.5Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert booking_gate.select_stale_marks(files) == []


def test_select_stale_marks_skips_files_with_unparseable_times():
    files = [
        {
            "id": "v5",
            "name": "broken.mp4",
            "createdTime": "not-a-date",
            "modifiedTime": "2026-08-11T22:13:27.539Z",
            "appProperties": {"booking_match": "none"},
        }
    ]

    assert booking_gate.select_stale_marks(files) == []
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_booking_gate.py -k select_stale_marks -v`
Expected: FAIL with `AttributeError: module 'src.booking_gate' has no attribute 'select_stale_marks'`.

- [ ] **Step 7: Implement the selection rule**

Add to `src/booking_gate.py`. Add `from collections.abc import Iterable` and
`from datetime import datetime` to its imports if they are not already there:

```python
def _parse_drive_time(value: object) -> datetime | None:
    """Parse an RFC 3339 Drive timestamp, or None when it is missing or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def select_stale_marks(files: Iterable[dict]) -> list[tuple[str, str, str]]:
    """Pick the recordings whose modifiedTime our own unmatched mark moved.

    Returns ``(file_id, name, created_time)`` for each file that carries the
    ``booking_match`` property and whose modifiedTime sits past its createdTime.

    The property is the entire predicate. A timestamp window would have caught only
    the files written in one particular run, and would sweep up recordings Drive
    itself nudged at upload -- files nobody here ever wrote to. Selecting on our own
    mark keeps the repair to files we are certain we touched.

    Files already at ``modifiedTime == createdTime`` are skipped, so re-running the
    repair writes nothing.
    """
    selected: list[tuple[str, str, str]] = []
    for item in files:
        properties = item.get("appProperties") or {}
        if drive.BOOKING_MATCH_PROPERTY not in properties:
            continue
        created_raw = item.get("createdTime")
        created = _parse_drive_time(created_raw)
        modified = _parse_drive_time(item.get("modifiedTime"))
        if created is None or modified is None or modified <= created:
            continue
        selected.append((item["id"], item.get("name", ""), str(created_raw)))
    return selected
```

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q` — expected: all pass.
Run: `uv run ruff check` — expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/drive.py src/booking_gate.py tests/test_drive.py tests/test_booking_gate.py
git commit -m "feat: add Drive timestamp helpers and the stale-mark selection rule"
```

---

### Task 3: `gdstt bookings restore-dates`

The operator-facing half. `--dry-run` exists because the damage this repairs was
itself caused by a mass write nobody previewed.

**Files:**
- Modify: `src/cli.py` (command function near `cmd_bookings_rematch:530`, parser wiring near `bookings_sub:917-926`)
- Modify: `skills/gdstt-cli/SKILL.md:130` (the `### \`bookings <list|rematch FILE_ID>\`` section)
- Modify: `README.md:891` (the call-bookings section)
- Modify: `AGENTS.md:292`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `drive.list_mp4_timestamps(service, folder_id) -> list[dict]`, `drive.set_file_modified_time(service, file_id, modified_time) -> dict`, `booking_gate.select_stale_marks(files) -> list[tuple[str, str, str]]`, `config.folders` (each item has `.folder_id`).
- Produces: `cli.cmd_bookings_restore_dates(args) -> None`, registered as `gdstt bookings restore-dates [--dry-run]`.

**Watch out:** `tests/test_skill_docs.py` requires every **top-level** subcommand to
be documented; `bookings` already is, so this task cannot break it. Document the new
subcommand inside the existing `bookings` section rather than adding a new `###`
header, and keep `SKILL.md` under its 400-line cap (it is at 396).

- [ ] **Step 1: Write the failing CLI tests**

Add to `tests/test_cli.py`:

```python
def test_bookings_restore_dates_restores_selected_files(tmp_path, monkeypatch, capsys):
    config_path = write_cli_config(
        tmp_path, folders=[{"folder_id": "f1", "email": "a@example.com"}]
    )
    service = MagicMock()
    monkeypatch.setattr(cli.auth, "build_drive_service", lambda **kwargs: service)
    monkeypatch.setattr(
        cli.drive,
        "list_mp4_timestamps",
        lambda svc, folder_id: [
            {
                "id": "v1",
                "name": "old.mp4",
                "createdTime": "2025-03-14T18:24:52.949Z",
                "modifiedTime": "2026-08-11T22:13:27.539Z",
                "appProperties": {"booking_match": "none"},
            }
        ],
    )
    restored = []
    monkeypatch.setattr(
        cli.drive,
        "set_file_modified_time",
        lambda svc, fid, when: restored.append((fid, when)),
    )

    cli.main(["--config", str(config_path), "bookings", "restore-dates"])

    assert restored == [("v1", "2025-03-14T18:24:52.949Z")]
    assert "1" in capsys.readouterr().out


def test_bookings_restore_dates_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    """The whole point of the flag: see the list before touching production files."""
    config_path = write_cli_config(
        tmp_path, folders=[{"folder_id": "f1", "email": "a@example.com"}]
    )
    service = MagicMock()
    monkeypatch.setattr(cli.auth, "build_drive_service", lambda **kwargs: service)
    monkeypatch.setattr(
        cli.drive,
        "list_mp4_timestamps",
        lambda svc, folder_id: [
            {
                "id": "v1",
                "name": "old.mp4",
                "createdTime": "2025-03-14T18:24:52.949Z",
                "modifiedTime": "2026-08-11T22:13:27.539Z",
                "appProperties": {"booking_match": "none"},
            }
        ],
    )

    def fail(*args, **kwargs):
        raise AssertionError("--dry-run must not write")

    monkeypatch.setattr(cli.drive, "set_file_modified_time", fail)

    cli.main(["--config", str(config_path), "bookings", "restore-dates", "--dry-run"])

    out = capsys.readouterr().out
    assert "v1" in out
    assert "dry-run" in out or "would" in out


def test_bookings_restore_dates_walks_every_configured_folder(
    tmp_path, monkeypatch, capsys
):
    config_path = write_cli_config(
        tmp_path,
        folders=[
            {"folder_id": "f1", "email": "a@example.com"},
            {"folder_id": "f2", "email": "b@example.com"},
        ],
    )
    monkeypatch.setattr(cli.auth, "build_drive_service", lambda **kwargs: MagicMock())
    seen = []
    monkeypatch.setattr(
        cli.drive,
        "list_mp4_timestamps",
        lambda svc, folder_id: seen.append(folder_id) or [],
    )
    monkeypatch.setattr(cli.drive, "set_file_modified_time", lambda *a, **k: None)

    cli.main(["--config", str(config_path), "bookings", "restore-dates"])

    assert seen == ["f1", "f2"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k restore_dates -v`
Expected: FAIL — argparse exits with `invalid choice: 'restore-dates'`.

- [ ] **Step 3: Implement the command**

Add to `src/cli.py`, directly after `cmd_bookings_rematch`:

```python
def cmd_bookings_restore_dates(args: argparse.Namespace) -> None:
    """Restore modifiedTime on recordings whose date the unmatched mark moved.

    Writing ``booking_match=none`` counted as an edit in Drive and moved every marked
    recording's date, which broke sorting in the shared folders. This walks the
    configured folders and puts each marked file's modifiedTime back to its
    createdTime -- the closest recoverable value, since the original was overwritten.
    """
    config = load_config(config_path=args.config, validate_providers=False)
    service = auth.build_drive_service(config=config)
    total = 0
    for folder in config.folders:
        files = drive.list_mp4_timestamps(service, folder.folder_id)
        for file_id, name, created in booking_gate.select_stale_marks(files):
            total += 1
            if args.dry_run:
                print(f"would restore\t{file_id}\t{created}\t{name}")
                continue
            drive.set_file_modified_time(service, file_id, created)
            print(f"restored\t{file_id}\t{created}\t{name}")
    if args.dry_run:
        print(f"{total} file(s) would be restored; re-run without --dry-run to apply")
    else:
        print(f"Restored modifiedTime on {total} file(s)")
```

- [ ] **Step 4: Wire the parser**

In `src/cli.py`, after the `p_bookings_rematch.set_defaults(...)` line (around
line 926):

```python
    p_bookings_restore = bookings_sub.add_parser(
        "restore-dates",
        help="Restore modifiedTime on recordings whose date the unmatched mark moved",
    )
    p_bookings_restore.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be restored without writing anything",
    )
    p_bookings_restore.set_defaults(func=cmd_bookings_restore_dates)
```

- [ ] **Step 5: Run the CLI tests**

Run: `uv run pytest tests/test_cli.py -k restore_dates -v`
Expected: PASS.

- [ ] **Step 6: Document the command**

In `skills/gdstt-cli/SKILL.md`, change the section header on line 130 to
`### \`bookings <list|rematch FILE_ID|restore-dates>\`` and add to that section:

```markdown
- `gdstt bookings restore-dates [--dry-run]` - возвращает `modifiedTime` записей,
  которым его сдвинула простановка метки `booking_match`, обратно на `createdTime`.
  Запись метки в Drive считается редактированием и ломает сортировку папок по дате.
  Сначала запускать с `--dry-run` и смотреть список.
```

In `README.md`, in the call-bookings section near line 891, after the sentence
about `gdstt bookings rematch`:

```markdown
Writing the mark counts as an edit in Drive, so it moves the recording's
"Last modified" date. `gdstt bookings restore-dates --dry-run` lists the files
whose date was moved that way, and the same command without the flag puts each
one back to its creation time.
```

In `AGENTS.md`, near line 292, extend the existing line so it reads:

```markdown
- `bookings list` / `bookings rematch` / `bookings restore-dates` use
  `load_config(validate_providers=False)`
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q` — expected: all pass, including `tests/test_skill_docs.py`.
Run: `uv run ruff check` — expected: `All checks passed!`
Run: `wc -l skills/gdstt-cli/SKILL.md` — expected: at most 400.

- [ ] **Step 8: Commit**

```bash
git add src/cli.py tests/test_cli.py skills/gdstt-cli/SKILL.md README.md AGENTS.md
git commit -m "feat: add gdstt bookings restore-dates"
```

---

## Manual verification

Run against the live deployment on `us1.dev.expertizeme.org` after deploying, in
this order. Prevention must be live before the repair runs, so restored dates stay
restored.

1. Deploy the branch to us1 and restart the container:
   `docker compose build && docker compose up -d --force-recreate`
2. Confirm prevention works on a single file. Pick one marked recording, note its
   `modifiedTime`, run `gdstt bookings rematch <file-id>` then wait one cycle for it
   to be re-marked, and confirm `modifiedTime` is unchanged from the noted value.
   Before this change that round trip moved the date twice.
3. `gdstt bookings restore-dates --dry-run` — expect roughly 1274 lines and a
   closing count. Read the list: every name should be a recording, and no file
   should appear whose date you moved yourself.
4. `gdstt bookings restore-dates` — expect the same count, reported as restored.
5. Re-run `gdstt bookings restore-dates --dry-run` — expect `0 file(s) would be
   restored`. The pass is idempotent.
6. Open one of the folders in Drive, sort by "Last modified", and confirm the order
   matches upload order again.
7. Confirm the marks survived: `docker logs` on the next cycle must still report
   `pending=0`, not a wave of `skipped_unmatched`. A wave means the repair stripped
   the appProperties and the backlog is being reconsidered.
