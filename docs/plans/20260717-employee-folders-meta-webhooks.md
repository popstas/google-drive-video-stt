# Employee folders, conversation meta (topic + tags), and completion webhooks

## Overview

Five independent improvements queued in `docs/TODO.md`, plus one cleanup:

1. **Speaker-name parsing** — `extract_interlocutor_names` mis-parses the real ExpertizeMe
   recording names, so Speaker 1 gets a whole filename prefix as its name and the second
   speaker often stays `Speaker 2`.
2. **Conversation topic** — one sentence describing what the call was about.
3. **Conversation tags** — picked from a configurable allow-list (`tags.allowed`, already
   seeded in `data/config.yml` but read by nothing).
4. **Employee folders** — `folder_ids: [str]` becomes `folders: [{folder_id, name, email}]`
   so every processed file knows whose folder it came from.
5. **Completion webhooks** — POST the employee plus every analysis result once a file
   finishes processing.
6. **Keyterms cleanup** — three `deepgram-keyterms.txt` copies collapse to one short
   example file, with the live list living in `data/deepgram-keyterms.txt`.

(2) and (3) ship as **one** OpenAI preset (`meta`) writing a YAML-frontmatter artifact —
one API call per file instead of two, and a payload the webhook can parse directly.
(4) is a **clean break**: `folder_ids` is removed, and a config still using it raises a
setup error telling the operator how to rewrite it. (5) is **fire-and-forget**: a webhook
failure is logged and never fails the file, mirroring `notify.notify_error`.

The excluded TODO item (evaluating Google's English transcripts against Deepgram) is
deliberately **not** in this plan.

## Context (from discovery)

**Files/components involved**

- `src/postprocess.py:11-47` — `_DATE_RE`, `_TITLE_SEP_RE`, `_NAME_SEP_RE`,
  `extract_interlocutor_names`; `map_speakers` at `:158-231` consumes the names.
- `src/config.py:45-91` — frozen `Config`; `folder_ids: list[str]` at `:47` is the **first
  and only non-default field**, so field order matters when adding/removing.
  `_parse_folder_ids` at `:122-123`, load/validate at `:418-424`, construction at `:536`,
  serialization at `:820`, init template `_default_config_dict` at `:654-738`.
- `src/presets.py:79-116` — frozen `Preset` dataclass and `BUILTIN_PRESETS` (today exactly
  one entry: `keypoints`). `PACKAGED_PROMPT_ASSETS` at `:32-36` lists the shipped prompts.
- `src/config.py:126-177` — `_resolve_prompt_text` (inline > `prompt_file` as-written >
  `<config_dir>/<prompt_file>` > packaged asset) and `_resolve_presets`.
- `src/main.py:396-584` — `process_item(service, item, folder_id, config, *, ...)`;
  `_run_preset_stage` at `:229-243`; `_ProcessTelemetry` at `:35-44`; the `finally` summary
  at `:558-573`; the success `return` at `:575-584`.
- `src/notify.py:9-41` — the outbound-HTTP pattern to mirror.
- `src/output.py:13-25` — `write_artifact(...)`.

**Related patterns found**

- `notify_error` never raises: blank creds → `logger.debug` + return; `requests.post(...,
  timeout=REQUEST_TIMEOUT, proxies=proxies)` in a `try`; failure → `logger.warning("...: %s",
  type(exc).__name__)` logging only the exception *type* so the token can't leak.
- Tests mock every external service and hit no network; `tests/test_notify.py` monkeypatches
  `notify.requests.post`. One test file per `src` module, flat pytest functions.
- `tests/test_main.py:18,39` — the `make_config` helper defaults `folder_ids=["folderA"]`;
  it is the single choke point for the `folders` migration across ~25 call sites.

**Dependencies identified**

- `tags.allowed` exists in `data/config.yml:50-79` (30 kebab-case tags) but **no `src/` code
  reads it**, and `_config_to_yaml_dict` (`src/config.py:811-873`) would silently **drop** it
  on any whole-Config rewrite. Task 4 fixes that data-loss bug as a side effect.
- `_run_preset_stage` consumes preset texts and discards them; `_save_and_upload_preset` and
  `write_artifact` both return `None`. Nothing propagates artifact text or ids back to
  `process_item`, so the webhook payload needs a plumbing task (Task 6) before it can carry
  "результаты анализов".
- `folder_ids` is referenced in `src/main.py:828,905,923-925`, `src/cli.py:227-234,413,423,
  519-524,742,901`, ~10 test files, `README.md:212-213,226,279,510-511,608`, `AGENTS.md:123`,
  `skills/gdstt-cli/SKILL.md:101,129`, and `data/config.yml:1-2`.
- `src/assets/deepgram-keyterms.txt` and `config/deepgram-keyterms.txt` are **byte-identical**
  (generic tech-interview list); `data/deepgram-keyterms.txt` (gitignored) is the live one.
  `DEEPGRAM_DEFAULT_KEYTERMS_FILE = Path("config/deepgram-keyterms.txt")` at `src/config.py:40`;
  `copy_deepgram_keyterms_asset` at `:778-785`; tests assert the path at
  `tests/test_config.py:1115,1135,1156,1169`.

**Real filenames this must handle** (from `data/results/`)

| Filename stem | Today | Expected |
| --- | --- | --- |
| `30-минутная онлайн-встреча Viktoria Tolstikova(ExpertizeMe) и Oleg - 2026_03_13 …` | `["30-минутная онлайн-встреча Viktoria Tolstikova(ExpertizeMe)", "Oleg"]` | `["Viktoria Tolstikova", "Oleg"]` |
| `30-минутная онлайн-встреча Angelica Munkueva(ExpertizeMe) и Mariia  - 2026_07_08 …` | prefix leaks into name 1 | `["Angelica Munkueva", "Mariia"]` |
| `Ольга х ExpertizeMe - 2026_07_08 …` | `["Ольга х ExpertizeMe"]` (no split) | `["Ольга"]` |
| `Aleksandr Tikhonov and Oksana Ciciarelli - 2026_05_20 …` | correct already | unchanged (regression guard) |
| `zkn-jdcd-cxc (2026-06-16 22_09 GMT+4)` | `["zkn-jdcd-cxc"]` — a Meet code becomes a name | `[]` |

Two bugs surfaced during discovery that are **not** in the TODO text but are in scope:

- `_DATE_RE` matches `-`, `/`, `.` but **not** `_`, so `2026_07_08` never trims. Today the
  `" - "` title split hides it; add `_` to the separator class.
- The Meet-code row above — `zkn-jdcd-cxc` is currently handed to `map_speakers` as a
  person's name.

## Development Approach

- **Testing approach**: **TDD** — write the failing test first, then the code that passes it.
- Complete each task fully before moving to the next.
- Make small, focused changes.
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- Run `uv run pytest` and `uv run ruff check` after each change.
- Backward compatibility: kept everywhere **except** `folder_ids`, whose removal is the
  deliberate, user-approved break (Task 2).

## Testing Strategy

- **Unit tests**: required for every task (see Development Approach above).
- **E2E tests**: this project has no UI and no Playwright/Cypress layer. The equivalent is
  `tests/test_preset_dag_e2e.py` (full preset DAG with mocked OpenAI) — extend it when the
  DAG changes (Task 4), and `scripts/docker-smoke.sh` for the config surface (Task 7).
- No network in tests: mock Drive, OpenAI, Deepgram, ffmpeg, and `requests.post`.
- Target the project's existing coverage standard; every new module ships its own test file.

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with ➕ prefix
- Document issues/blockers with ⚠️ prefix
- Update plan if implementation deviates from original scope
- Keep plan in sync with actual work done

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): code, tests, docs inside this repo.
- **Post-Completion** (no checkboxes): live-config edits, real webhook endpoint verification.

## Implementation Steps

### Task 1: Fix ExpertizeMe filename parsing in `extract_interlocutor_names`

- [x] write failing tests in `tests/test_postprocess.py` for every row of the "Real filenames"
      table above (duration prefix, `(ExpertizeMe)` parenthetical, `" х "` separator,
      Meet-code stem, plus the existing `and` case as a regression guard)
- [x] add `_DURATION_PREFIX_RE` to `src/postprocess.py` matching a leading
      `N-минутная онлайн-встреча` / `N-minute meeting` style prefix, and strip it from the stem
- [x] add `_PARENTHETICAL_RE` and strip `(...)` groups from each candidate part
      (➕ applied to the whole stem *before* the date trim rather than per part — same result
      for every table row, and it also disposes of the unbalanced `(` the Meet-code stem
      would otherwise leave behind after the date cut)
- [x] extend `_NAME_SEP_RE` with the `" х "` (Cyrillic ha) and `" x "` separators
- [x] add `_` to `_DATE_RE`'s separator class so `2026_07_08` trims
- [x] add `_ORG_TOKENS = {"expertizeme"}` and drop parts that are only an org token, so
      `Ольга х ExpertizeMe` yields `["Ольга"]`
- [x] reject Meet-code-like parts (no spaces, lowercase, `xxx-xxxx-xxx` shape) so
      `zkn-jdcd-cxc` yields `[]`
- [x] run `uv run pytest tests/test_postprocess.py` — all pass (30 passed)
- [x] run `uv run pytest && uv run ruff check` — must pass before Task 2
      (572 passed, 2 skipped; ruff clean)

### Task 2: Replace `folder_ids` with `folders` in `Config`

- [x] write failing tests in `tests/test_config.py`: `folders` parses to a tuple of
      `EmployeeFolder`; missing `name`/`email` default to `""`; a bare string entry is
      rejected; a config still carrying `folder_ids` raises `ValueError` naming `folders`
- [x] add a frozen `EmployeeFolder` dataclass (`folder_id`, `name: str = ""`,
      `email: str = ""`) to `src/config.py`
- [x] replace the `folder_ids: list[str]` field (`src/config.py:47`) with
      `folders: tuple[EmployeeFolder, ...]`, keeping it the first non-default field
- [x] replace `_parse_folder_ids` (`:122-123`) with `_parse_folders`, validating each entry is
      a mapping with a non-empty `folder_id`
- [x] raise a setup `ValueError` when the YAML still has `folder_ids`, quoting the
      `folders: [{folder_id, name, email}]` shape in the message
      (➕ keyed on the **presence** of `folder_ids`, not its truthiness — an empty
      `folder_ids: []` is still a stale config and fails loudly rather than starting
      with nothing to poll)
- [x] update `_config_to_yaml_dict` (`:820`) and `_default_config_dict` (`:696`) to emit
      `folders: []`
- [x] add a `Config.folder_ids` read-only property returning `[f.folder_id for f in folders]`
      so iteration sites stay short, and a `folder_by_id(folder_id) -> EmployeeFolder | None`
      lookup for Tasks 6-7
- [x] write tests for the property and the lookup (hit + miss)
- [x] run `uv run pytest tests/test_config.py && uv run ruff check` — must pass before Task 3
      (139 passed; ruff clean)

⚠️ ~~The full suite is intentionally red between Tasks 2 and 3: 183 failures in the other
test modules, all from `make_config(folder_ids=...)` helpers that Task 3's first checkbox
migrates. `tests/test_config.py` — Task 2's gate — is green.~~ **Resolved by Task 3**: the
helpers now build `folders=`, and the full suite is green again (582 passed, 2 skipped).

### Task 3: Wire `folders` through the runtime and CLI

- [x] update the `make_config` helper in `tests/test_main.py:18,39` to build `folders=`, and
      fix the other test modules' helpers (`test_cli`, `test_output`, `test_preset_pipeline`,
      `test_preset_dag_e2e`, `test_stt_contract`, `test_openai_pipeline`,
      `test_stt_transcribe`, `test_stt_deepgram_factory`)
      (➕ added a `_as_folders` shim to `make_config` accepting ids **or** `EmployeeFolder`s,
      so the ~25 folder-agnostic call sites stay `folders=["f1"]` and only the tests that
      care about an employee spell out the dataclass)
- [x] write a failing test that `gdstt doctor` / `config` output lists each folder with its
      employee name and email
      (`test_doctor_lists_each_folder_with_employee_name_and_email`)
- [x] update `src/main.py:828` (`run_once` iteration), `:905` (cycle summary count), and
      `:923-925` (empty-folders `SystemExit`) to read `config.folders`
- [x] update `src/cli.py:227-234` (first-folder default), `:413,423,519-524` (status output),
      and the `--folder` help text at `:742,901`
      (➕ added `_describe_employee(folder)` beside the `_describe_google_*` doctor helpers;
      `cmd_list` keeps using the `config.folder_ids` property since it merges a bare
      `--folder` id with the configured ones)
- [x] update `data/config.yml` to the `folders` shape, carrying the existing folder id
      (⚠️ gitignored local operator config — edited but **not** committed; `name`/`email`
      left empty for the operator to fill per Post-Completion)
- [x] run `uv run pytest && uv run ruff check` — must pass before Task 4
      (582 passed, 2 skipped; ruff clean)

### Task 4: Read `tags.allowed` into `Config` and stop dropping it

- [ ] write failing tests in `tests/test_config.py`: `tags.allowed` parses into
      `Config.tags_allowed`; a missing `tags:` block yields `()`; a non-list raises
      `ValueError`; **`_config_to_yaml_dict` round-trips `tags.allowed` without dropping it**
- [ ] add `tags_allowed: tuple[str, ...] = ()` to `Config` and parse it in `_config_from_yaml`
- [ ] emit `tags.allowed` from `_config_to_yaml_dict` (`:811-873`) — this is the data-loss fix
- [ ] seed an empty `tags: {allowed: []}` block in `_default_config_dict` (`:654-738`)
- [ ] run `uv run pytest tests/test_config.py && uv run ruff check` — must pass before Task 5

### Task 5: Add the `meta` preset (topic + tags) with allow-list injection

- [ ] write a failing test in `tests/test_config.py` that a preset prompt containing
      `{{allowed_tags}}` is rendered with the config's `tags.allowed` list at load time, and
      that a prompt without the placeholder is untouched
- [ ] write a failing test in `tests/test_presets.py` that `meta` is a built-in with
      `artifact_suffix=".meta.md"` and `prompt_file="meta.md"`
- [ ] add `src/assets/prompts/meta.md` instructing a YAML frontmatter reply — `topic:` (one
      sentence) and `tags:` (a list drawn **only** from the `{{allowed_tags}}` placeholder,
      empty list when nothing fits)
- [ ] register `meta.md` in `PACKAGED_PROMPT_ASSETS` (`src/presets.py:32-36`) and confirm
      `pyproject.toml:38` already globs it
- [ ] add the `meta` entry to `BUILTIN_PRESETS` (`:109-116`) with
      `depends_on=("transcript-cleanup",)` — matching how `keypoints` is wired in
      `data/config.yml`
- [ ] render the `{{allowed_tags}}` placeholder inside `_resolve_prompt_text`
      (`src/config.py:126-162`) from `tags_allowed`
- [ ] add `src/meta.py` with `parse_meta(text) -> Meta(topic: str, tags: tuple[str, ...])`
      reading the YAML frontmatter, tolerating a missing/garbled block, and **dropping tags
      not in the allow-list**
- [ ] write `tests/test_meta.py`: well-formed frontmatter; no frontmatter; malformed YAML;
      unknown tag dropped; empty tags list
- [ ] extend `tests/test_preset_dag_e2e.py` so the mocked DAG run writes a `.meta.md` artifact
- [ ] run `uv run pytest && uv run ruff check` — must pass before Task 6

### Task 6: Return preset outputs from `_run_preset_stage` to `process_item`

- [ ] write a failing test in `tests/test_main.py` that `process_item` telemetry carries each
      enabled preset's text keyed by preset name
- [ ] change `_run_preset_stage` (`src/main.py:229-243`) to return
      `dict[str, str]` of preset name → artifact text instead of discarding them
- [ ] add an `artifacts: dict[str, str]` field (default empty) plus `transcript: str` to
      `_ProcessTelemetry` (`:35-44`) and populate them in both producing branches (the
      `needs_txt` branch at `:483-525` and the `needs_presets` re-feed branch at `:526-553`)
- [ ] confirm the `finally` summary log at `:558-573` stays unchanged — no artifact text in logs
- [ ] write tests for the re-feed branch (existing transcript, presets rerun) carrying
      artifacts too
- [ ] run `uv run pytest && uv run ruff check` — must pass before Task 7

### Task 7: Add the completion webhook

- [ ] write failing tests in `tests/test_webhook.py` (monkeypatching `webhook.requests.post`
      like `tests/test_notify.py:8` does): blank URL → no POST; payload shape; token →
      `Authorization: Bearer` header; `requests` raising → logged, **never** re-raised;
      `raise_for_status` 4xx → logged, never re-raised
- [ ] add `src/webhook.py` with `notify_complete(*, url, token, proxy_url, payload)` mirroring
      `notify.notify_error`'s contract: `REQUEST_TIMEOUT = 10`, blank-URL no-op via
      `logger.debug`, proxy dict, `try` + `raise_for_status`, failure → `logger.warning("...:
      %s", type(exc).__name__)` logging only the exception type
- [ ] add `webhook: {url: "", token: ""}` to `Config` (`webhook_url`, `webhook_token`),
      `_config_to_yaml_dict`, and `_default_config_dict`; write config tests for the round-trip
- [ ] build the payload in `process_item` on the **success path only** (`src/main.py:575-584`):
      `{file: {id, name, folder_id}, employee: {name, email}, transcript, artifacts: {...}}`,
      resolving the employee via `config.folder_by_id(folder_id)` from Task 2 and parsing
      `artifacts["meta"]` through `meta.parse_meta` into `{topic, tags}`
- [ ] write tests in `tests/test_main.py`: webhook fired once with the right employee; not
      fired when the file was skipped (the early `return` at `:428-429`); not fired on failure;
      a webhook exception does not fail the file
- [ ] run `uv run pytest && uv run ruff check` — must pass before Task 8

### Task 8: Collapse the three `deepgram-keyterms.txt` copies

- [ ] write a failing test in `tests/test_config.py` that `gdstt config init` writes
      `<config_dir>/deepgram-keyterms-example.txt` and that the packaged example loads
- [ ] delete `config/deepgram-keyterms.txt` (byte-identical duplicate of the packaged asset)
- [ ] replace `src/assets/deepgram-keyterms.txt` with a short
      `src/assets/deepgram-keyterms-example.txt` — a handful of illustrative terms and a
      comment saying the live list belongs in `data/deepgram-keyterms.txt`
- [ ] point `DEEPGRAM_DEFAULT_KEYTERMS_FILE` (`src/config.py:40`) and
      `DEEPGRAM_KEYTERMS_ASSET` (`:41`) at the example name, dropping the `config/` path segment
- [ ] update `copy_deepgram_keyterms_asset` (`:778-785`) to write the example beside `config.yml`
- [ ] update the assertions at `tests/test_config.py:1115,1135,1156,1169`
- [ ] confirm `data/config.yml:22` (`keyterms_file: ./deepgram-keyterms.txt`) still resolves
      the live file and leave it untouched
- [ ] run `uv run pytest && uv run ruff check` — must pass before Task 9

### Task 9: Verify acceptance criteria

- [ ] verify all six Overview items are implemented
- [ ] verify each "Real filenames" table row produces the expected names
- [ ] verify a `folder_ids` config raises the migration error, and `folders` drives the loop
- [ ] verify a webhook failure leaves the file processed and the run green
- [ ] run the full test suite (`uv run pytest`) on the project's supported Pythons
- [ ] run `uv run ruff check` — all issues fixed
- [ ] run `scripts/docker-smoke.sh` — the config-only smoke must survive the keyterms and
      `folders` changes
- [ ] verify test coverage meets project standard (80%+)

### Task 10: [Final] Update documentation

- [ ] update `README.md:212-213,226,279,510-511,608` (`folder_ids` → `folders`, keyterms paths)
      and document the `webhook` config block plus its payload shape
- [ ] update `AGENTS.md:61,123` (keyterms path, `folders`), the core invariants list, and the
      preset list now that `meta` is a second built-in
- [ ] update `skills/gdstt-cli/SKILL.md:101,129,185,193` and bump its `version` /
      `last_updated` fields, per the AGENTS.md skill-layering policy
- [ ] update `tests/test_skill_docs.py` for the changed operator surface
- [ ] check off the five implemented items in `docs/TODO.md`, leaving the English-transcript
      comparison item open

*Note: ralphex automatically moves completed plans to `docs/plans/completed/`*

## Technical Details

**`EmployeeFolder` / config shape**

```yaml
folders:
  - folder_id: 1D0Ep1nVvLahh_NbpHTZE1TFlWtXSXGfz
    name: Олег Иванов
    email: oleg@expertizeme.org

tags:
  allowed: [клиентская-консультация, O-1, EB-1, ...]   # already seeded

webhook:
  url: https://example.com/hooks/gdstt
  token: ""      # optional; sent as "Authorization: Bearer <token>"
```

A config still carrying `folder_ids` fails at load:

```
ValueError: folder_ids is no longer supported; use
  folders:
    - folder_id: <id>
      name: <employee name>
      email: <employee email>
```

**`meta` artifact** (`<base>.meta.md`)

```markdown
---
topic: Консультация по визе O-1 для research-профиля
tags: [клиентская-консультация, O-1, рекомендательные-письма]
---
```

`parse_meta` reads the frontmatter, intersects `tags` with `tags.allowed`, and degrades to
`Meta(topic="", tags=())` on a missing or malformed block — a bad LLM reply must not fail the
file.

**Webhook payload**

```json
{
  "file": {"id": "1a2b", "name": "Ольга х ExpertizeMe - ....mp4", "folder_id": "1D0E"},
  "employee": {"name": "Олег Иванов", "email": "oleg@expertizeme.org"},
  "transcript": "Ольга: ...",
  "artifacts": {
    "meta": {"topic": "...", "tags": ["клиентская-консультация"]},
    "keypoints": "## Задачи\n...",
    "action-items": "..."
  }
}
```

Fired once, on the success path only, after every artifact is written. Non-`meta` presets pass
through as raw text keyed by preset name, so adding a preset to `config.yml` extends the
payload with no code change. An unknown employee (folder without `name`/`email`) sends empty
strings rather than omitting the key.

**Processing flow after this plan**

```
process_item(service, item, folder_id, config)
  └─ transcribe → postprocess (Task 1 parsing)
     └─ _run_preset_stage → {"transcript-cleanup": ..., "meta": ..., "keypoints": ...}
        └─ write artifacts (unchanged)
           └─ webhook.notify_complete(employee=folder_by_id(folder_id), artifacts=...)
              └─ failure → logger.warning, file still counts as processed
```

## Post-Completion

*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**

- Rewrite the live `data/config.yml` to the `folders` shape with each employee's real name and
  email — the clean break means the service will not start until this is done.
- Point `webhook.url` at the real receiver and confirm one end-to-end delivery with a real
  recording (payload carries PII: employee email plus the full transcript — the receiver
  should be HTTPS and token-protected).
- Review the seeded `tags.allowed` list against what the `meta` preset actually picks over a
  handful of real calls; prune or extend the list from evidence.
- Sanity-check `meta` topic quality on a Russian and an English call.

**External system updates**

- The webhook consumer must tolerate the `artifacts` map growing new keys as presets are added.
- `data/deepgram-keyterms.txt` stays gitignored and machine-local; make sure the production
  deployment's `data/` volume has it before the keyterms change ships.
