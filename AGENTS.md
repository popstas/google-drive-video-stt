# AGENTS.md

Portable repo instructions for google-drive-video-stt. Prefer universal
Markdown and shared repo docs over editor-specific overlays.

## Source of truth layering

- `README.md` - human quickstart and deployment overview.
- `AGENTS.md` - canonical shared repo contract, commands, architecture, and conventions.
- `skills/gdstt-cli/` - canonical installable operator skill package (`SKILL.md` + bundled resources).
- `CLAUDE.md` - thin compatibility shim that points back to `AGENTS.md`.

## Project snapshot

This repository polls Google Drive folders for MP4 files, extracts audio when
needed, transcribes with the configured STT provider, optionally post-processes
the transcript, and uploads sibling artifacts back to Drive.

The code is env-driven through `src/config.py`, the main runtime lives in
`src/main.py`, and STT provider dispatch lives in `src/stt/__init__.py`.

## Commands

```bash
uv tool install --editable .   # install the global gdstt command from this checkout
uv tool update-shell           # refresh PATH helpers for uv-installed tools
uv sync --extra dev          # install deps incl. pytest/ruff (use .venv)
uv run pytest                # run all tests
uv run pytest tests/test_stt_google.py::test_name   # single test
uv run ruff check            # lint (line-length 100, target py311)
uv run python scripts/check-agent-skill.py  # validate the canonical installable skill
gh skill install . gdstt-cli --from-local --agent codex --scope user --force
gh skill install . gdstt-cli --from-local --agent claude-code --scope user --force
gdstt setup                  # first-time local setup wizard (env, auth, Drive check)
gdstt auth [--manual]        # OAuth-only refresh or recovery flow
uv run python -m src.auth    # module entry for the same OAuth flow
uv run python -m src.main    # run the polling loop locally
gdstt <setup|auth|plan|execute|run|run-once|process|transcribe|list>  # operator CLI (src/cli.py)
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

`process_target` (`src/main.py`) is the on-demand entry the CLI's `process` command uses:
it auto-detects file vs folder by `mimeType` (override with `is_folder`), then runs the same
`process_item` over a single file or every pending file in a folder. The `gdstt` CLI
(`src/cli.py`) wraps the same `load_config()`/Drive/STT layers behind argparse subcommands
without duplicating business logic.

**Agent runtime policy** (`src/pipeline_profile.py`, `src/pipeline_policy.py`,
`src/pipeline_executor.py`): `gdstt plan --json` parses a compact intent and
expands `config/pipelines/default.json` plus optional gitignored
`config/pipelines/local.json` into a deterministic plan. `gdstt execute --json`
enforces the same confirmation and secret-readiness gates, then delegates to
`process_target` instead of duplicating processing logic.

**Post-processing** runs in `process_item` after `transcribe_file` and before upload, gated
by config: `openai_postprocess` (LLM path, `src/openai_pipeline.py` — OpenAI Responses API,
optional Batch path) takes precedence over `stt_postprocess` (local path, `src/postprocess.py`).
Both clean whitespace, parse interlocutor names from the file name, map them onto diarized
`Speaker N` labels, and merge spurious extra speakers. An existing sibling `.txt` is
overwritten in place via `drive.update_file` (its `txt_id` flows through `list_folder_state`).

**Error handling is tiered**: `RefreshError`/`AuthError` propagate up to `main()` and
cause `SystemExit(1)` so the container restarts (after re-running `src.auth`); all other
exceptions are logged + sent to Telegram via `notify.notify_error` and the loop continues.

`process_item()` also emits one process summary per worked file with provider,
`processing_mode`, outcome, retry count, and duration. `run-once()` emits one folder summary per
folder and one cycle summary with provider, overall outcome, folder count,
pending count, processed count, failed count, `retry_total`,
`gcs_blob_orphans`, skipped-by-size count, folder-error count, dry-run flag,
and duration.

`gcs_blob_orphans` is a subset of failed items, not an additional parallel
failure count.

**STT layer** (`src/stt/`): `get_provider(config)` dispatches on `STT_PROVIDER` to one of
four `STTProvider` implementations. The base class has two paths — `transcribe_full()`
returns `None` by default (fall back to chunking); providers that do whole-file
transcription override it. `transcribe_file()` (`transcribe.py`) tries `transcribe_full`
first, else splits with `chunk_mp3` (`STT_CHUNK_SECONDS`) and calls `transcribe_chunk` per part.
- `openai` / `asr`: chunked path, `STT_LANGUAGE` optional (auto-detect).
- `google`: `transcribe_full` path — uploads MP3 to GCS, runs Speech-to-Text v2
  `BatchRecognize` with diarization, deletes the blob. `STT_LANGUAGE` is required
  (BCP-47, the `long` model has no auto-detect). Diarization works only for limited
  languages (mostly `en-*`); on unsupported langs it retries once without diarization.
- `deepgram`: full-file path — can use `m4a_copy`, `mp3_96k`, or `mp3_192k`; tuning stays
  provider-specific, but the CLI workflow stays the same.

**Auth/setup** (`src/auth.py`, `src/setup.py`): single OAuth user credential covers both
Drive and Cloud STT (scopes `drive` + `cloud-platform`) — no service account. `gdstt setup`
is the first-run local wizard: it updates `.env`, defaults `STT_PROVIDER` to `deepgram`,
prompts for API keys required by the active pipeline profile, prepares
`data/credentials.json` from ADC client metadata, runs OAuth, and verifies Drive access.
`gdstt auth` remains the smaller OAuth-only path for
refresh, recovery, or headless/manual exchange. `load_credentials` inspects the saved token's
`scopes` directly (because `from_authorized_user_file` echoes the requested scopes, not the
granted ones); a missing scope raises `AuthError` telling you to re-auth. Adding a scope to
`SCOPES` requires deleting `data/token.json` and re-running auth.

## Core invariants

- `STT_PROVIDER` selects the speech-to-text backend. When it is absent,
  `load_config()` treats it as `deepgram`; set `STT_PROVIDER=disabled` to skip
  transcription explicitly. The CLI flow should stay stable even if the provider changes.
- Bootstrap and Drive-only commands use `load_config(validate_providers=False)`:
  `setup`, `auth`, `doctor`, `list` / `status`, `speakers set`, `refresh-names`,
  and `plan`.
- Processing commands validate provider configuration and can spend credits:
  `run`, `run-once`, `process`, `transcribe`, and `execute`.
- Agent JSON intents never contain API keys. Readiness output reports only
  `configured` or `missing`.
- `OPENAI_POSTPROCESS=true` is independent of `STT_PROVIDER` and can refine the
  transcript after any provider.
- `STT_CHUNK_SECONDS` only matters for chunking providers. Deepgram and Google
  use full-file paths.
- Idempotency relies on `appProperties.source_video_id` and sibling stem
  matching as a fallback.
- New or changed operator behavior should be reflected in
  `tests/test_skill_docs.py`.

## Skill layering policy

- Keep one primary operator skill in `skills/gdstt-cli/SKILL.md`.
- The main skill must stay sufficient for the default workflow: auth, inspect,
  single-file processing, reprocess, folder safety, and provider switching basics.
- Use bundled scenario playbooks under `skills/gdstt-cli/examples/`
  only for external setup, folder-wide safety, or recovery tasks. Ordinary
  project use should stay in the main skill flow instead of being routed
  through a scenario file.
- Split out only reference-heavy, low-frequency material: provider tuning matrices,
  troubleshooting, recovery, extension workflow, or long error-code tables.
- Companion references live under `skills/gdstt-cli/references/`.
- Provider extension workflow lives in
  `skills/gdstt-cli/references/provider-extension.md`.
- Prefer companion reference docs over separate active subskills. The main skill may
  point to them, but the base workflow must stay usable from one skill.
- Use separate skills only if they have a genuinely different trigger, audience,
  and decision tree. If that day comes, add them as sibling folders under
  `skills/`, not as nested subskills inside the main `gdstt-cli` package.

## Current provider posture

Deepgram is the current operational default, but instructions must remain
provider-agnostic. Keep the common CLI workflow stable and move provider tuning
into provider-specific notes.

Today that means:

- Deepgram examples may be the default examples.
- Google STT setup stays opt-in and separate from Drive-only setup.
- OpenAI post-processing stays documented as a layer on top of any STT provider.

## Conventions

- Tests mock all external services (Drive, OpenAI, Google STT, ffmpeg); one test file per
  `src` module. No network in tests.
- `from __future__ import annotations` + `X | None` style type hints throughout.
- Compute Drive-name stems with `drive.drive_stem` (`os.path.splitext`, string-based), never
  `Path(...).stem`/`.name` — Drive names may contain `/`, which `Path` would treat as a path
  separator and drop. Sanitize local temp filenames with `drive.safe_local_name`.
- Secrets and tokens live in `./data` (gitignored); never commit `credentials.json` / `token.json`.

## Portability policy

- Shared rules belong in `AGENTS.md`.
- Canonical portable operator guidance belongs in `skills/gdstt-cli/SKILL.md`.
- Portable interaction assets belong next to the skill under
  `skills/gdstt-cli/examples/` and `references/`, but they should stay
  limited to supporting setup, folder-wide safety, and recovery.
- Install the canonical package into each host with `gh skill install`; do not
  commit host-specific `.agents/skills/` or `.claude/skills/` copies.
- Prefer universal docs over editor-specific overlays.
- Keep bundled references under each installable skill so the bundle remains usable
  outside this repository.
- Validate the package and temporary local install with
  `python scripts/check-agent-skill.py`.
- When operator behavior changes, update the bundled resources and refresh the
  skill `version` and `last_updated` fields.
- If a tool-specific file is required, keep it as a thin pointer back to `AGENTS.md`
  or the main skill rather than redefining runtime behavior.
