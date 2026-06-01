# Changelog

Review and validation notes for PR #4.

## 2026-06-01 - Follow-up changelog relative to PR4 review

This section summarizes the diff that landed after the main PR4 review memo in
`docs/2026-06-01-project-skill-review.md`.

### Runtime hardening and observability

- Empty transcript output is now treated as failure instead of a silent success.
- Transient Drive read paths now retry with bounded retry accounting.
- Downloaded files are validated against expected size and fail fast on mismatch.
- Google STT timeout-retained GCS blobs are surfaced explicitly via
  `gcs_blob_orphans` in cycle summaries.
- Per-item process summaries now expose provider, `processing_mode`, outcome,
  retry count, and duration.
- Cycle summaries now expose provider, overall outcome, `retry_total`,
  `gcs_blob_orphans`, and duration.

### CLI and operator UX

- CLI help now pushes the safe operator path: `doctor -> list -> process --dry-run`
  before folder-wide or continuous processing.
- `run`, `run-once`, and `process` help text now warns more clearly about cost,
  folder scope, and `--reprocess-txt` behavior.
- README CLI guidance now matches the same safe operator flow.

### Skill and documentation contract

- `AGENTS.md` is now the canonical shared contract and `CLAUDE.md` is a thin
  compatibility shim.
- Skill metadata is centralized in `docs/skills/registry.json` with version and
  `last_updated` parity checks.
- Companion docs for provider notes, troubleshooting, and provider extension are
  now formalized and validated.
- `tests/test_skill_docs.py` now guards command parity, Start Here routing,
  scenario grouping, mirror sync, bundled references, and validator presence.

### Portable bundle packaging

- The main operator skill now has a portable installable bundle at
  `.agents/skills/gdstt-cli/`.
- `.claude/skills/gdstt-cli/` is now a compatibility mirror instead of the
  primary source of truth.
- `scripts/check-agent-skill.py` validates bundle sync, bundled references, and
  the compatibility mirror.
- Bundled playbooks are intentionally limited to supporting setup, folder-wide
  safety, and recovery. Ordinary project usage stays in the main skill flow.

### Additional regression coverage

- Added/expanded runtime coverage in `tests/test_main.py`, `tests/test_drive.py`,
  `tests/test_stt_google.py`, and `tests/test_stt_transcribe.py`.
- Added `tests/test_stt_contract.py` for provider-factory/base-contract checks.
- Added missing config coverage for enabled Deepgram keyterms with an unreadable
  file path.
- `tests/test_skill_docs.py` now invokes the agent-skill validator via pytest,
  so bundle drift is no longer only a manual check.

### Intentional non-goals

- No nested subskills were added inside the main `gdstt-cli` bundle.
- Supporting playbooks were kept only for setup/safety/recovery, not for normal
  day-to-day project usage.

## Open Finding - Existing `.txt` is not overwritten by normal processing

### Status

Fixed with an explicit CLI path. Normal polling still skips existing `.txt` files
to avoid repeated STT billing, but `gdstt process <file-id> --reprocess-txt`
runs STT again and overwrites the linked `.txt` in place.

### Evidence

After live-processing one Drive MP4, the sibling `.mp3` and `.txt` were created.
Running `gdstt process <same-file-id>` again exited successfully but did no work:
the existing `.txt` kept the same Drive file id, `modifiedTime`, and size.

Observed before and after:

```text
txt_id: 1_PfhQ4XA10GS7wojKE4816m7nBARH4Yb
modifiedTime: 2026-05-29T21:47:10.410Z
size: 4260
```

### Why

`src/main.py` computes:

```python
needs_txt = stt_enabled and not has_txt
```

If both `.mp3` and `.txt` exist, `process_item()` returns before transcription or
post-processing. That means the `drive.update_file()` path exists, but normal
`run-once` / `process` does not reach it for already-transcribed files.

### Resolution

Added `--reprocess-txt` to `gdstt process`. It is intentionally explicit because
it can spend Deepgram/OpenAI/Google/ASR credits again.

## Open Finding - Renaming a source MP4 breaks sibling detection

### Status

Fixed for newly-created artifacts. Generated `.mp3` / `.txt` files now carry
Drive `appProperties.source_video_id=<mp4-id>`, and folder state matches by that
stable id before falling back to legacy filename-stem matching.

### Evidence

After live-processing the 7.5 MiB file, Drive contained generated siblings:

```text
2023-04-05_13-00-23 Максим Цапов, фронтенд.mp3
2023-04-05_13-00-23 Максим Цапов, фронтенд.txt
```

The source MP4 was then renamed to:

```text
Стас, Максим Цапов 2023-04-05_13-00-23 Максим Цапов, фронтенд.mp4
```

`extract_interlocutor_names()` now correctly extracts:

```text
['Стас', 'Максим Цапов']
```

But `gdstt list` reports the renamed MP4 as `[---] [---]` because sibling
detection matches by current Drive filename stem. The generated `.mp3` / `.txt`
still exist, but their old stem no longer matches the renamed MP4.

### Why

The current idempotency model is filename-stem based. Renaming the source MP4
changes the expected sibling names and disconnects previously generated files.

### Resolution

New uploads and `.txt` updates set:

```text
appProperties.source_video_id=<mp4-id>
appProperties.artifact_type=mp3|txt
```

`gdstt refresh-names <file-id>` can then rename linked artifacts to match the
current MP4 filename without re-running STT. Legacy artifacts created before this
fix are still only discoverable by filename until they are regenerated or
manually linked.

## 2026-05-30 - Accept UTF-8 BOM in OAuth client credentials

### Problem

`gdstt auth` failed before opening the browser when `data/credentials.json` was
written by Windows PowerShell with UTF-8 BOM:

```text
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

This can happen during first setup because the README shows a PowerShell flow
that writes `data/credentials.json`.

### Root Cause

`google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file()` reads the
credentials file with the default JSON loader, which does not accept a leading
UTF-8 BOM.

### Fix

`src/auth.py` now reads the OAuth client config itself with `encoding="utf-8-sig"`
and passes the parsed object to `InstalledAppFlow.from_client_config()`. This
accepts both plain UTF-8 and UTF-8 with BOM.

### Regression Coverage

Added `test_run_interactive_flow_accepts_credentials_json_with_utf8_bom` in
`tests/test_auth.py`.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check
```

Results:

```text
tests/test_auth.py: 19 passed
full pytest: 261 passed, 1 skipped
ruff: All checks passed!
```

## 2026-05-30 - Accept UTF-8 BOM in `.env`

### Problem

`FOLDER_IDS` was present in `.env`, but `load_config()` returned an empty
`folder_ids` list. The first bytes of `.env` were `EF BB BF`, so the first key
was parsed as `\ufeffFOLDER_IDS` instead of `FOLDER_IDS`.

This can also happen during Windows first setup when `.env` is edited or written
by tools that include a UTF-8 BOM.

### Root Cause

`python-dotenv` was called without an explicit encoding and path:

```python
load_dotenv(override=False)
```

That did not strip the UTF-8 BOM from the first key. In tests, it could also find
a parent/project `.env` instead of the intended current working directory file.

### Fix

`src/config.py` now loads the current working directory `.env` explicitly and
uses `encoding="utf-8-sig"`:

```python
load_dotenv(dotenv_path=".env", override=False, encoding="utf-8-sig")
```

### Regression Coverage

Added `test_load_config_accepts_dotenv_with_utf8_bom` in `tests/test_config.py`.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check
```

Results:

```text
tests/test_config.py: 45 passed
full pytest: 262 passed, 1 skipped
ruff: All checks passed!
```

## 2026-05-30 - Print Unicode Drive names from CLI on Windows

### Problem

`gdstt list` successfully read the configured Drive folder, but crashed while
printing an MP4 name containing Cyrillic characters:

```text
UnicodeEncodeError: 'charmap' codec can't encode characters
```

### Root Cause

The Windows command environment used a legacy stdout encoding (`cp1252`). Python
`print()` tried to encode Drive file names into that encoding and failed.

### Fix

`src/cli.py` now reconfigures stdout/stderr to UTF-8 with replacement error
handling at CLI startup:

```python
stream.reconfigure(encoding="utf-8", errors="replace")
```

### Regression Coverage

Added `test_configure_console_encoding_uses_utf8_for_text_streams` in
`tests/test_cli.py`.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check
.\.venv\Scripts\gdstt.exe list
```

Results:

```text
tests/test_cli.py: 17 passed
full pytest: 263 passed, 1 skipped
ruff: All checks passed!
gdstt list: listed 2 mp4 files with Cyrillic names
```

## 2026-05-30 - Accept UTF-8 BOM in Deepgram config files

### Problem

After fixing `.env` and `data/credentials.json`, the same Windows setup issue
could still affect user-managed Deepgram files:

- `DEEPGRAM_API_KEY_FILE`
- `DEEPGRAM_KEYTERMS_FILE`

With UTF-8 BOM, the raw API key could start with `\ufeff`, JSON key files could
fail JSON parsing and be treated as raw text, and the first keyterm/comment line
could include the BOM.

### Root Cause

Both files were read with `encoding="utf-8"`, which preserves a leading UTF-8
BOM as a real character.

### Fix

`src/config.py` now reads both files with `encoding="utf-8-sig"`, accepting both
plain UTF-8 and UTF-8 with BOM.

### Regression Coverage

Added tests in `tests/test_config.py` for:

- raw Deepgram API key file with BOM
- JSON Deepgram API key file with BOM
- Deepgram keyterms file with BOM

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check
```

Results:

```text
tests/test_config.py: 48 passed
full pytest: 266 passed, 1 skipped
ruff: All checks passed!
```

## 2026-05-30 - Decode `ffmpeg` output robustly on Windows

### Problem

During a live `gdstt process` run, MP3/M4A extraction completed successfully, but
Python printed background thread exceptions while reading `ffmpeg` output:

```text
UnicodeDecodeError: 'charmap' codec can't decode byte ...
```

The command still succeeded, but the exception noise makes live operation look
broken and can hide useful `ffmpeg` stderr.

### Root Cause

`subprocess.run(..., text=True)` used the process default encoding. In this
Windows terminal that was a legacy code page, not UTF-8, and it could not decode
some bytes emitted by `ffmpeg`.

### Fix

`src/extractor.py` now passes explicit decoding options to both `ffmpeg` calls:

```python
encoding="utf-8",
errors="replace",
```

This prevents decode crashes while still preserving readable stderr as much as
possible.

### Regression Coverage

Updated `tests/test_extractor.py` to assert both MP3 extraction and M4A copy use
explicit `encoding="utf-8"` and `errors="replace"`.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extractor.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check
```

Results:

```text
tests/test_extractor.py: 9 passed
full pytest: 266 passed, 1 skipped
ruff: All checks passed!
```

## 2026-05-30 - Keep `Speaker 1` / `Speaker 2` as real speakers when merging extras

### Problem

The local post-processor could treat a spurious `Speaker 3` as a real
interlocutor when that stray turn had more words than `Speaker 1` or `Speaker 2`.
That caused incorrect name mapping, e.g. `Speaker 2` could be merged into
`Speaker 1`, while `Speaker 3` received the second real name.

### Root Cause

`map_speakers()` selected the real speakers by word count first. This is useful
as a fallback, but for the normal two-speaker diarization case the canonical
labels `Speaker 1` and `Speaker 2` should remain the real speakers when both are
present.

### Fix

When the expected canonical labels are present (`Speaker 1..N`), `src/postprocess.py`
now treats those labels as the real speakers and merges higher-numbered labels
into them. It only falls back to word-count selection when the canonical labels
are not all present.

### Regression Coverage

Added `test_extra_speaker_does_not_replace_first_two_speakers_when_names_known`
in `tests/test_postprocess.py`.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_postprocess.py -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check
```

Results:

```text
tests/test_postprocess.py: 18 passed
full pytest: 267 passed, 1 skipped
ruff: All checks passed!
```

## 2026-05-30 - Deepgram `m4a_copy` no longer uploads an extra MP3 by default

### Problem

With `STT_PROVIDER=deepgram` and `DEEPGRAM_AUDIO_SOURCE=m4a_copy`, the service
sent a temporary M4A/MP4 audio copy to Deepgram, but still created a sibling
Drive `.mp3` because the older service rule treated MP3 extraction as mandatory
for every MP4.

### Fix

Added `DRIVE_MP3_ARTIFACT`. When unset, it defaults to `false` for Deepgram
`m4a_copy` and `true` for legacy/non-Deepgram flows. Set
`DRIVE_MP3_ARTIFACT=true` to keep uploading an MP3 artifact next to the MP4.

### Regression Coverage

Added tests for config defaults and for `process_item()` behavior with Deepgram
`m4a_copy` both with and without the MP3 artifact flag.

## 2026-05-30 - Explicit speaker names can be stored on the source MP4

### Problem

Filename parsing is useful, but after a Drive rename or an inconsistent meeting
title it can be easier to specify the two real speaker names directly.

### Fix

Added:

```powershell
gdstt speakers set <file-id> "Name 1" "Name 2"
```

The command stores JSON speaker names in Drive `appProperties.speaker_names` on
the source MP4. Local and OpenAI post-processing use those names before falling
back to filename parsing. To apply corrected names to an existing transcript, use:

```powershell
gdstt process <file-id> --reprocess-txt
```

### Live Verification

Tested on the 7.5 MiB Drive MP4:

```text
id=1_7hS4l3umyPG-asmd5IKNancx1l0dCMR
name=Стас, Максим Цапов 2023-04-05_13-00-23 Максим Цапов, фронтенд.mp4
```

Commands:

```powershell
gdstt speakers set 1_7hS4l3umyPG-asmd5IKNancx1l0dCMR "Стас" "Максим Цапов"
gdstt process 1_7hS4l3umyPG-asmd5IKNancx1l0dCMR --reprocess-txt
```

Observed:

```text
[mp3=skip, txt=make]
Deepgram cost: $0.018890
Uploaded: Стас, Максим Цапов 2023-04-05_13-00-23 Максим Цапов, фронтенд.txt
```

The new TXT has:

```text
appProperties.source_video_id=1_7hS4l3umyPG-asmd5IKNancx1l0dCMR
appProperties.artifact_type=txt
```

`gdstt list` now reports the renamed MP4 as `[---] [txt]`. No new `.mp3` was
uploaded because the active config is Deepgram `m4a_copy` with
`DRIVE_MP3_ARTIFACT` defaulting to false.

## 2026-05-30 - Sanitize Windows-reserved filename characters in local temp paths

### Problem

The slash filename fix preserved Drive names correctly, but local temp filenames
on Windows could still fail when a Drive name contained `:` or other
Windows-reserved characters. A common recording name contains time zones like
`17:27 GMT+04:00`.

### Fix

`src/drive.py::safe_local_name()` now replaces all Windows-reserved local
filename characters:

```text
< > : " / \ | ? * NUL
```

Drive upload names are still preserved; only the temporary local filename is
sanitized.

### Regression Coverage

Added `test_safe_local_name_replaces_windows_reserved_characters`.

## 2026-05-30 - Real operator-skill QA runs

### What was tested

Separate agents used the installed `gdstt-cli` skill with real commands and a
prepared Drive state. The large 146.9 MiB file was not processed.

### Results

- Status/read-only workflow correctly reported:

```text
[mp3] [txt] Стас, Максим Цапов 2023-04-05_13-00-23 Максим Цапов, фронтенд.mp4
[---] [---] 2024-08-28_16-00-46 Собеседование, Михаил_speedup_small_480.mp4
```

- `process <small-id> --reprocess-txt` overwrote the linked TXT in place:

```text
txt id: 1UMyvd1FYWKUAGgm40SNnwg7M4qbzJ338
Deepgram cost: $0.018890
```

- Temporary `DRIVE_MP3_ARTIFACT=true` plus `process <small-id>` created only the
linked MP3 and did not run Deepgram:

```text
mp3 id: 1hycivzuPEPwoAlZ3Dkvft5mxIQCaYLyj
appProperties.source_video_id=1_7hS4l3umyPG-asmd5IKNancx1l0dCMR
appProperties.artifact_type=mp3
```

- `refresh-names <small-id>` renamed linked MP3/TXT after a source MP4 rename
without STT.
- `speakers set <small-id> "Первый Тест" "Второй Тест"` followed by
`process <small-id> --reprocess-txt` produced a transcript whose first lines used
those names. The names and transcript were then restored to `Стас` /
`Максим Цапов`.
- A safety-check agent correctly refused to run `run-once` because it would pick
up the pending 146.9 MiB file and spend Deepgram credits.

### Skill Update

The installed skill at `C:\Users\wyrtensi\.codex\skills\gdstt-cli\SKILL.md` was
updated with operator playbooks and Windows invocation examples.

## 2026-05-30 - Google Drive auth playbook added to the skill

### Problem

The skill documented `gdstt auth` as a command, but did not teach agents how to
set up Google Drive access safely. In practice, setup needs project/folder
choices and confirmation before changing gcloud config, enabling APIs, writing
credentials, or opening OAuth.

### Fix

The installed `gdstt-cli` skill now has a Drive-first auth setup playbook:

- ask which Google Cloud project id to use
- ask for the Drive folder id / `FOLDER_IDS`
- ask whether `DATA_DIR=data` is OK
- ask before mutating gcloud config, enabling APIs, writing
  `data/credentials.json`, or running `gdstt auth`
- for Drive-only access, enable only `drive.googleapis.com`
- do not enable `speech.googleapis.com` / `storage.googleapis.com`
- do not set `STT_PROVIDER=google`, `GOOGLE_CLOUD_PROJECT`, or
  `GOOGLE_STT_GCS_BUCKET` unless the human separately asks for Google STT
- explain that the current app OAuth flow still asks for `cloud-platform` scope
  because `src/auth.py` requires it, but that is not the same as enabling Google
  STT APIs or provider config

### Verification

A pressure-test agent was asked to set up Drive access while explicitly not
using Google STT. It ran only read-only discovery (`gcloud` found, current
project/account shown), then asked for project/folder/permission confirmations
and did not enable Speech/Storage or configure Google STT.

## 2026-05-30 - Safer operator commands for agents

### Problem

Agents had to assemble Drive setup checks manually, and folder-wide processing
could accidentally pick up a large pending video and spend STT credits.

### Fix

- Added `gdstt doctor [--drive]`.
  - `doctor` checks `DATA_DIR`, `credentials.json`, `token.json`, `FOLDER_IDS`,
    and `STT_PROVIDER` without validating STT provider secrets.
  - `doctor --drive` additionally authenticates and lists configured Drive
    folders, but still does not process files.
- Added `--dry-run` to `gdstt run-once` and `gdstt process`.
  - It lists/logs what would be processed without downloads, uploads, or STT.
- Added `--max-size SIZE` and `--confirm-large` to `run-once` and `process`.
  - Files larger than `--max-size` are skipped.
  - `--confirm-large` is required to override that skip.

### Verification

Focused tests cover parser dispatch, size parsing, dry-run behavior, and the
large-file guard:

```text
uv run pytest tests/test_cli.py tests/test_main.py -q
73 passed
```

Full verification:

```text
uv run pytest -q
300 passed, 1 skipped

uv run ruff check
All checks passed!
```

Live dry-run check against the configured Drive folder:

```text
gdstt run-once --dry-run --max-size 50MB
Skipping 2024-08-28_16-00-46 Собеседование, Михаил_speedup_small_480.mp4:
size 154.0 MB exceeds --max-size 50.0 MB
Folder 1CzQWPDUcMieSKsj5DuTd5N8d_m0eIKu0: 0 pending file(s)
```

## 2026-05-30 - Drive-only setup wizard clarified in the skill

### Problem

The Google Drive auth playbook was technically safe, but the desired operator
experience is more wizard-like: ask the human to sign in if needed, choose an
existing project or provide a new project name, configure only Drive access for
the agent, then choose the Drive folder. Google STT must stay a separate setup
task.

### Fix

Updated the installed `gdstt-cli` skill with a human-facing Drive setup wizard:

- ask for an existing project id or a new project name
- ask for the Drive folder id, or permission to create/select one later
- ask whether to keep `DATA_DIR=data`
- ask permission before each mutating setup group
- state that only Google Drive access will be configured
- state that Google Speech-to-Text setup is separate
- finish with: `Drive access is ready. Google STT is still not configured.`

The skill also now says `--max-size` is optional and disabled by default. Agents
should use it only when the human asks for a size guard or when offering a manual
safety limit before folder-wide processing.

Agent pressure tests found two wording improvements, now added:

- before `gcloud config set project`, explain that it changes the active project
  in the user's gcloud configuration, not only this repository
- before writing `data/credentials.json` from ADC metadata, explain that only
  OAuth client id/secret are copied and the ADC refresh token is not copied

The README usage line now uses `--max-size SIZE` instead of `--max-size 50MB` so
the example does not look like a default.

### Verification

Added skill-doc tests so these guardrails stay explicit:

```text
uv run pytest tests/test_skill_docs.py tests/test_cli.py -q
33 passed
```

## 2026-05-30 - OpenAI full-pipeline playbook added to the skill

### Problem

The OpenAI post-processing pipeline exists in code and README, but the operator
skill did not clearly teach an agent how to run a whole Drive MP4 -> STT ->
OpenAI-refined TXT workflow from a single human request.

### Fix

Updated the installed `gdstt-cli` skill with:

- `Full Drive MP4 To Final TXT With OpenAI Post-Processing`
- required env vars: `OPENAI_POSTPROCESS=true`, `OPENAI_API_KEY`, optional
  `OPENAI_MODEL`, `OPENAI_BATCH`, `PROXY_URL`
- a safe single-file flow:
  - `gdstt doctor`
  - `gdstt list`
  - `gdstt process <drive-mp4-file-id> --dry-run`
  - `gdstt process <drive-mp4-file-id>`
- the distinction between `STT_PROVIDER=openai` and `OPENAI_POSTPROCESS=true`
- local limits: `gdstt transcribe <audio>` is local STT-only, does not upload to
  Drive, and does not run OpenAI post-processing; there is no single local-MP4
  CLI command

### Verification

Skill-doc tests now assert the OpenAI full-pipeline playbook and local-MP4 limit
are documented.
