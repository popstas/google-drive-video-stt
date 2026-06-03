---
name: gdstt-cli
description: Use when operating google-drive-video-stt through gdstt, setting up Google Drive OAuth access, inspecting Drive folder state, processing Drive MP4 files, setting speaker names, or transcribing local audio.
license: MIT
version: 1.7.1
last_updated: 2026-06-02
---

# gdstt CLI

Operator guide for the Google Drive video STT service. Prefer safe,
single-target commands, avoid printing secrets, and ask before any action that
changes Google Cloud, Drive, local auth files, or spends STT credits.

## Start Here

Use the smallest path that fits the task:

1. First-time local setup: `gdstt setup` -> `gdstt list` -> `gdstt process <file-id> --dry-run`
2. OAuth refresh or headless recovery: `gdstt auth` or `gdstt auth --manual`
3. Agent JSON processing: `gdstt plan --json '<intent>'` -> `gdstt execute --json '<intent>'`
4. Safe low-level single-file processing: `gdstt process <file-id> --dry-run` -> `gdstt process <file-id>`
5. Folder-wide low-level work: preview first with `gdstt run-once --dry-run` or folder `process --dry-run`
6. Provider or failure detail: read the matching resource from the routing table below

Current operational default examples assume `STT_PROVIDER=deepgram`, but keep
the same CLI flow if the provider changes later.

## Invocation

```bash
gdstt <command> [args]
uv run python -m src.cli <command> [args]
```

On Windows local checkouts:

```powershell
.\.venv\Scripts\gdstt.exe <command> [args]
uv run python -m src.cli <command> [args]
```

Use `PYTHONIOENCODING=utf-8` or the installed `gdstt.exe` wrapper when printing
Cyrillic names from ad-hoc Python or PowerShell scripts.

## Command Boundaries

Bootstrap and Drive-only commands use `load_config(validate_providers=False)`:

- `setup`
- `auth`
- `doctor`
- `list` / `status`
- `speakers set`
- `refresh-names`
- `plan`

Processing commands validate provider config and can spend STT credits:

- `run`
- `run-once`
- `process`
- `transcribe`
- `execute`

Start with Drive-only commands when checking auth, folder ids, or artifact
state. Read `references/commands.md` when you need detailed syntax, aliases,
examples, or flag interactions.

## Commands

### `setup`

Create or update `.env`, default the provider to Deepgram, discover gcloud and
ADC metadata when available, run OAuth, and verify Drive access.

### `auth [--manual] [response_url]`

Create or refresh local OAuth credentials. Normal mode opens a localhost browser
flow. `--manual` prints the authorization URL; passing `response_url` completes
that manual exchange.

### `doctor [--drive]`

Check Drive/OAuth configuration. Add `--drive` only when authentication and
folder listing are intended.

### `list` / `status`

Read sibling MP3/TXT state without processing files.

### `plan --json '<intent>'`

Expand a compact agent intent into a deterministic processing plan without
mutating Drive. On PowerShell, prefer `--json-file <path>` to avoid native
process quoting surprises.

### `execute --json '<intent>' [--confirm]`

Execute the same intent after policy checks. Add `--confirm` only when the plan
reports a confirmation gate. `--json-file <path>` is also supported.

### `run`

Run the continuous polling loop. Ask before use: it can repeatedly spend credits
across every pending configured folder.

### `run-once`

Run one polling cycle. Prefer `--dry-run` first for folder-wide work.

### `process <target> [--folder] [--reprocess-txt] [--dry-run] [--max-size SIZE] [--confirm-large]`

Process one Drive file or folder. Prefer a single file id. Ask before
`--reprocess-txt`, folder execution, or `--confirm-large`.

### `speakers set <file-id> <name...>`

Store explicit speaker names on the source MP4. Reprocessing is separate and
can spend credits.

### `refresh-names <file-id>`

Rename linked generated artifacts after an MP4 rename without running STT.

### `transcribe <audio> [-o PATH]`

Transcribe a local audio file without touching Drive.

### `relabel --in SRC --out OUT --map MAP.json [--no-header]`

Deterministically rename transcript speakers from a MAP.json (default label ->
name, with verbatim-text exceptions) and merge consecutive same-speaker turns.
Utterance text is preserved byte-for-byte.

## Safety Rules

- Ask before commands that mutate Google Cloud, Drive, local auth files, or
  spend STT/OpenAI credits.
- `setup` is the default first-run path, but it still mutates `.env`,
  `data/credentials.json`, `data/token.json`, and gcloud configuration after
  explicit confirmation.
- Prefer one file before one folder; prefer one dry run before one real folder
  run.
- Prefer `gdstt plan --json ...` before agent-driven execution. `gdstt execute`
  enforces the same policy gates even when planning is skipped.
- Report best-effort OpenAI refinement token counters from `usage.openai` after
  execution. OpenAI dollar cost may remain `null`.
- Treat `txt_uploaded` and `mp3_uploaded` as actual uploads from that execution,
  not as desired profile state.
- Secret readiness is reported only as `configured` or `missing`. Never place
  API keys in JSON intents or command output.
- Inline JSON `speakers` overrides apply only to Drive MP4 file targets.
- Provider overrides must pass their required-setting preflight before Drive
  processing starts.
- `--max-size` is optional and disabled by default. Do not invent a global
  threshold.
- Add `--confirm-large` only after explicit human approval.
- `--reprocess-txt` reruns STT and intentionally overwrites the linked TXT.
- `run` has no preview mode. Use it only after controlled checks match
  expectations.
- Never print API keys, OAuth tokens, `credentials.json`, or `token.json`.

## Provider Switching Contract

- `STT_PROVIDER` selects the backend; the CLI workflow stays stable.
- `OPENAI_POSTPROCESS=true` can refine text after any STT provider.
- `STT_CHUNK_SECONDS` applies only to chunking providers; it is ignored by
  Deepgram and Google full-file paths.
- Keep Drive setup separate from provider setup.
- Change one provider-specific tuning value at a time and validate on one file.
- Treat `config/pipelines/default.json` as the versioned agent pipeline profile.
- Put machine-specific profile overrides in gitignored
  `config/pipelines/local.json`.
- Use compact JSON intents for agent decisions, then expand them through the
  deterministic planner before execution.

Read `references/provider-notes.md` when selecting, switching, or tuning
Deepgram, Google STT, OpenAI STT, or ASR.

## Resource Routing

Bundled references and examples are installed resources, not nested skills.
Open only the resource needed for the task:

| Task | Read |
| --- | --- |
| Detailed command syntax, aliases, or flag combinations | `references/commands.md` |
| Environment variables or provider configuration | `references/configuration.md` |
| Provider selection, switching, tuning, or Deepgram artifact behavior | `references/provider-notes.md` |
| Empty transcript, retries, size mismatch, invalid `FOLDER_IDS`, summaries, or recovery | `references/troubleshooting.md` |
| Adding or replacing an STT provider as a maintainer | `references/provider-extension.md` |
| First-time local setup wizard and gcloud/ADC fallback | `examples/drive-only-setup.md` |
| Folder-wide dry run or optional size guard | `examples/folder-dry-run-size-guard.md` |
| Google STT timeout-retained GCS blob | `examples/google-timeout-recovery.md` |
| Drive MP4 to final TXT with OpenAI post-processing | `examples/openai-full-pipeline.md` |

## Core Notes

- Empty transcripts fail intentionally; do not accept a blank TXT as success.
- Transient Drive metadata, folder-state, and download reads retry before the
  cycle gives up. Uploads are not retried automatically.
- Download size mismatch removes the partial temp file before retry or recovery.
- `FOLDER_IDS` containing only commas or whitespace fails fast.
- Idempotency uses `appProperties.source_video_id` and sibling stem matching as
  a legacy fallback.
- `process_item` logs provider, `processing_mode`, outcome, retry count, and
  duration.
- `run-once` logs folder summaries and one cycle summary with provider, overall
  outcome, pending/processed/failed counts, `retry_total`, `gcs_blob_orphans`,
  skipped-size count, folder errors, dry-run state, and duration.
- `gcs_blob_orphans` is a subset of failed items, not an extra failure count.

For deeper failure handling and recovery steps, read
`references/troubleshooting.md`.
