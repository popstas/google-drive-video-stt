# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --extra dev          # install deps incl. pytest/ruff (use .venv)
uv run pytest                # run all tests
uv run pytest tests/test_stt_google.py::test_name   # single test
uv run ruff check            # lint (line-length 100, target py311)
uv run python -m src.auth    # one-time interactive OAuth -> data/token.json
uv run python -m src.main    # run the polling loop locally
docker compose up -d --build # containerized deployment (mounts ./data)
```

`ffmpeg` must be on PATH for local runs (bundled in the Docker image).

## Architecture

Headless service: polls Google Drive folders, extracts MP3 from new MP4s via ffmpeg,
optionally transcribes to a sibling `.txt`. All flow is env-driven through `Config`
(`src/config.py`, frozen dataclass built by `load_config()` which validates
provider-specific required vars and raises on misconfiguration).

**Polling loop** (`src/main.py`): `main()` builds the Drive service once, then loops
`run_once()` every `POLL_INTERVAL` seconds. Per file, `process_item` computes two
independent needs — `needs_mp3` (no sibling `.mp3`) and `needs_txt` (STT enabled and no
sibling `.txt`) — so a file already having an MP3 can still get transcribed on a later
cycle. Idempotency comes from `drive.list_folder_state` reporting sibling presence by
basename. All work happens in a per-item `TemporaryDirectory`.

**Error handling is tiered**: `RefreshError`/`AuthError` propagate up to `main()` and
cause `SystemExit(1)` so the container restarts (after re-running `src.auth`); all other
exceptions are logged + sent to Telegram via `notify.notify_error` and the loop continues.

**STT layer** (`src/stt/`): `get_provider(config)` dispatches on `STT_PROVIDER` to one of
three `STTProvider` implementations. The base class has two paths — `transcribe_full()`
returns `None` by default (fall back to chunking); providers that do whole-file
transcription override it. `transcribe_file()` (`transcribe.py`) tries `transcribe_full`
first, else splits with `chunk_mp3` (`STT_CHUNK_SECONDS`) and calls `transcribe_chunk` per part.
- `openai` / `asr`: chunked path, `STT_LANGUAGE` optional (auto-detect).
- `google`: `transcribe_full` path — uploads MP3 to GCS, runs Speech-to-Text v2
  `BatchRecognize` with diarization, deletes the blob. `STT_LANGUAGE` is **required**
  (BCP-47, the `long` model has no auto-detect). Diarization works only for limited
  languages (mostly `en-*`); on unsupported langs it retries once without diarization.

**Auth** (`src/auth.py`): single OAuth user credential covers both Drive and Cloud STT
(scopes `drive` + `cloud-platform`) — no service account. `load_credentials` inspects the
saved token's `scopes` directly (because `from_authorized_user_file` echoes the requested
scopes, not the granted ones); a missing scope raises `AuthError` telling you to re-auth.
Adding a scope to `SCOPES` requires deleting `data/token.json` and re-running `src.auth`.

## Conventions

- Tests mock all external services (Drive, OpenAI, Google STT, ffmpeg); one test file per
  `src` module. No network in tests.
- `from __future__ import annotations` + `X | None` style type hints throughout.
- Secrets and tokens live in `./data` (gitignored); never commit `credentials.json` / `token.json`.
