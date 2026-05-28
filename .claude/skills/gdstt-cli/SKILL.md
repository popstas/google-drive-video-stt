---
name: gdstt-cli
description: Operate the Google Drive video STT service via its `gdstt` CLI. Use when running OAuth, the polling loop, on-demand processing of a Drive file/folder, transcribing a local audio file, or inspecting folder state (sibling MP3/TXT presence). Triggers on "gdstt", "run the polling loop", "process a Drive file", "transcribe an mp3", "list folder state".
---

# gdstt CLI

Operator-facing CLI for the headless Google Drive video STT service. It wraps the
existing operations (OAuth, polling loop, on-demand processing, STT, folder
inspection) without duplicating business logic — every subcommand calls into the
same `load_config()` / Drive / extractor / STT layers the service uses.

## Invocation

The CLI is exposed two equivalent ways:

```bash
gdstt <command> [args]            # console script (from `pip install -e .` / `uv sync`)
uv run python -m src.cli <command> [args]
```

`ffmpeg` must be on PATH for any command that extracts audio (`run`, `run-once`,
`process`). All configuration is read from the environment (and a `.env` file if
present) through `load_config()`; see `.env.example` for the full list.

## Commands

### `auth [response_url]`

Run the interactive OAuth flow and save `token.json` into `DATA_DIR` (default
`data/`). The single user credential covers both Drive and Cloud STT (scopes
`drive` + `cloud-platform`). Pass the redirect `response_url` only for the manual
copy-paste flow; omit it for the normal browser flow.

```bash
gdstt auth
gdstt auth "http://localhost/?code=4/abc123&scope=..."
```

Env: `DATA_DIR`. Requires `credentials.json` in `DATA_DIR`.

### `run`

Run the polling loop (`src/main.py:main`). Builds the Drive service once, then
loops `run_once()` every `POLL_INTERVAL` seconds until interrupted. This is the
same entry point the Docker container uses.

```bash
gdstt run
```

Env: `FOLDER_IDS`, `POLL_INTERVAL`, `BITRATE`, `DATA_DIR`, STT vars (see below),
optional `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `PROXY_URL`.

### `run-once`

Run a single polling cycle (one `run_once()`) and exit. Useful for cron-style
invocation or manual one-shot processing of all configured folders.

```bash
gdstt run-once
```

Env: same as `run`.

### `process <target> [--folder]`

On-demand extraction (and transcription, if STT is enabled) for a specific Drive
file or folder. By default the target type is auto-detected; pass `--folder` to
force treating the ID as a folder.

```bash
gdstt process 1AbCfileId            # auto-detect file vs folder
gdstt process 1XyZfolderId --folder # force folder
```

Env: `BITRATE`, `DATA_DIR`, STT vars.

### `transcribe <audio> [-o/--output PATH]`

STT-only on an existing local audio file (e.g. an MP3) using the configured
provider. Prints the transcript to stdout, or writes it to `--output`. Does not
touch Drive.

```bash
gdstt transcribe ./meeting.mp3
gdstt transcribe ./meeting.mp3 -o ./meeting.txt
```

Env: `STT_PROVIDER` (must be set to a real provider), `STT_LANGUAGE`,
`STT_CHUNK_SECONDS`, and the provider-specific vars below.

### `list` (alias `status`) `[--folder FOLDER_ID]`

Show folder state — which monitored MP4s already have a sibling `.mp3` / `.txt` —
without doing any work. Inspects `FOLDER_IDS` by default, or a single folder via
`--folder`. Exits 1 if no folders are configured and none is passed.

```bash
gdstt list                          # all configured FOLDER_IDS
gdstt status --folder 1XyZfolderId  # single folder (status is an alias)
```

Output line format: `[mp3] [txt] <filename>` (each tag shows `---` when absent).

Env: `FOLDER_IDS`, `DATA_DIR`.

## Environment variables

Common (all commands that touch Drive/STT):

- `FOLDER_IDS` — comma-separated Drive folder IDs to monitor.
- `POLL_INTERVAL` — loop interval in seconds (default 600; `run` only).
- `BITRATE` — MP3 bitrate for ffmpeg extraction (default `96k`).
- `DATA_DIR` — holds `credentials.json` / `token.json` (default `data`).
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — optional error notifications.
- `PROXY_URL` — optional proxy for Telegram, OpenAI, ASR, Deepgram (http/https/socks5).

STT selection:

- `STT_PROVIDER` — `""` (disabled), `openai`, `google`, `asr`, or `deepgram`.
- `STT_LANGUAGE` — language hint. Optional for openai/asr (auto-detect when empty);
  defaults to `ru` for deepgram; **required** BCP-47 (e.g. `en-US`) for google.
- `STT_CHUNK_SECONDS` — chunk length for chunked providers (default 600; ignored by
  google/deepgram which transcribe the whole file).

Provider-specific (validated by `load_config()` only when that provider is selected):

- `openai`: `OPENAI_API_KEY` (required).
- `deepgram`: `DEEPGRAM_API_KEY` or `DEEPGRAM_API_KEY_FILE` (required), plus optional
  `DEEPGRAM_MODEL`, `DEEPGRAM_DIARIZE_MODEL`, `DEEPGRAM_AUDIO_SOURCE`,
  `DEEPGRAM_TXT_FORMATTER`, `DEEPGRAM_KEYTERMS_ENABLED`, `DEEPGRAM_KEYTERMS_FILE`.
- `google`: `GOOGLE_CLOUD_PROJECT` and `GOOGLE_STT_GCS_BUCKET` (required), plus
  `STT_LANGUAGE` (required). Re-run `gdstt auth` after first enabling so the
  `cloud-platform` scope is granted.
- `asr`: `ASR_URL` (required) — base URL of a whisper-asr-webservice instance.

## Notes

- `auth` errors (`RefreshError` / `AuthError`) mean the saved token is missing a
  scope or expired — re-run `gdstt auth`.
- Idempotency is sibling-file based: a file with a sibling `.mp3` is not re-extracted;
  a file with a sibling `.txt` is not re-transcribed. Use `list` to see current state.
- Tests for the CLI live in `tests/test_cli.py`; the command surface documented here
  is kept in sync by `tests/test_skill_docs.py`.
