---
name: gdstt-cli
description: Use when operating google-drive-video-stt through gdstt, setting up Google Drive OAuth access, inspecting Drive folder state, processing Drive MP4 files, setting speaker names, or transcribing local audio.
license: MIT
version: 1.4.0
last_updated: 2026-06-01
---

# gdstt CLI

Operator guide for the Google Drive video STT service. Prefer safe,
single-target commands, avoid printing secrets, and ask before any action that
changes Google Cloud, Drive, local auth files, or spends STT credits.

## Start Here

Use the smallest path that fits the task:

1. First-time Drive-only setup: `gdstt auth` -> `gdstt doctor` -> `gdstt list`
2. Safe single-file processing: `gdstt process <file-id> --dry-run` -> `gdstt process <file-id>`
3. Folder-wide work: preview first with `gdstt run-once --dry-run` or folder `process --dry-run`
4. Provider or failure detail: read the matching resource from the routing table below

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

Drive-only/read-only commands use `load_config(validate_providers=False)`:

- `auth`
- `doctor`
- `list` / `status`
- `speakers set`
- `refresh-names`

Processing commands validate provider config and can spend STT credits:

- `run`
- `run-once`
- `process`
- `transcribe`

Start with Drive-only commands when checking auth, folder ids, or artifact
state. Read `references/commands.md` when you need detailed syntax, aliases,
examples, or flag interactions.

## Commands

### `auth [response_url]`

Create or refresh local OAuth credentials. Confirm before opening browser flows
or writing local auth files.

### `doctor [--drive]`

Check Drive/OAuth configuration. Add `--drive` only when authentication and
folder listing are intended.

### `list` / `status`

Read sibling MP3/TXT state without processing files.

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

## Safety Rules

- Ask before commands that mutate Google Cloud, Drive, local auth files, or
  spend STT/OpenAI credits.
- Prefer one file before one folder; prefer one dry run before one real folder
  run.
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

Read `references/provider-notes.md` when selecting, switching, or tuning
Deepgram, Google STT, OpenAI STT, or ASR. Repo maintainers should keep its
canonical copy in `docs/skills/provider-notes.md`.

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
| First-time Drive-only OAuth setup | `examples/drive-only-setup.md` |
| Folder-wide dry run or optional size guard | `examples/folder-dry-run-size-guard.md` |
| Google STT timeout-retained GCS blob | `examples/google-timeout-recovery.md` |
| Drive MP4 to final TXT with OpenAI post-processing | `examples/openai-full-pipeline.md` |

Repo maintainers should keep companion docs synchronized with:

- `docs/skills/provider-notes.md`
- `docs/skills/troubleshooting.md`
- `docs/skills/provider-extension.md`

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
`references/troubleshooting.md`. Repo maintainers should keep its canonical copy
in `docs/skills/troubleshooting.md`.
