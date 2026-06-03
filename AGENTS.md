# AGENTS.md

Portable repo instructions for google-drive-video-stt. Prefer universal
Markdown and shared repo docs over editor-specific overlays.

## Source of truth layering

- `README.md` - human quickstart and deployment overview.
- `AGENTS.md` - canonical shared repo contract, commands, architecture, and conventions.
- `skills/gdstt-cli/SKILL.md` - canonical installable operator skill (single file).
- `CLAUDE.md` - thin compatibility shim that points back to `AGENTS.md`.

## Project snapshot

This repository polls Google Drive folders for MP4 files, extracts audio when
needed, transcribes with Deepgram, optionally post-processes the transcript and
generates a Keypoints document, and writes sibling artifacts back to Drive or to a
local folder.

The code is env-driven through `src/config.py`, the main runtime lives in
`src/main.py`, and STT provider dispatch lives in `src/stt/__init__.py`.

## Commands

```bash
uv tool install --editable .   # install the global gdstt command from this checkout
uv tool update-shell           # refresh PATH helpers for uv-installed tools
uv sync --extra dev          # install deps incl. pytest/ruff (use .venv)
uv run pytest                # run all tests
uv run pytest tests/test_config.py::test_name   # single test
uv run ruff check            # lint (line-length 100, target py311)
uv run python scripts/check-agent-skill.py  # validate the canonical installable skill
gh skill install . gdstt-cli --from-local --agent codex --scope user --force
gh skill install . gdstt-cli --from-local --agent claude-code --scope user --force
gdstt auth [--manual]        # OAuth refresh or recovery flow
uv run python -m src.auth    # module entry for the same OAuth flow
uv run python -m src.main    # run the polling loop locally
gdstt <auth|doctor|latest|run|run-once|process|transcribe|relabel|speakers|list>  # operator CLI (src/cli.py)
docker compose up -d --build # containerized deployment (mounts ./data)
```

`ffmpeg` must be on PATH for local runs (bundled in the Docker image).

## Architecture

Headless service: polls Google Drive folders, extracts audio from new MP4s via
ffmpeg, transcribes to a `.txt`, and optionally generates a `.keypoints.md`
document. All flow is env-driven through `Config` (`src/config.py`, frozen
dataclass built by `load_config()` which validates provider-specific required vars
and raises on misconfiguration).

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

**`latest` command** (`src/cli.py` + `drive.find_newest_mp4`): resolves a folder
(arg or first of `FOLDER_IDS`), finds the newest mp4 by `createdTime desc`, and
dispatches it through `process_target` (honoring `--dry-run`).

**Post-processing** runs in `process_item` after `transcribe_file` and before the
artifact is written, gated by `stt_postprocess` (local path, `src/postprocess.py`):
it cleans whitespace, parses interlocutor names from the file name, maps them onto
diarized `Speaker N` labels, and merges spurious extra speakers.

**Keypoints** (`src/openai_pipeline.py`, gated by `openai_keypoints`): after the
transcript is written, `generate_keypoints` calls the OpenAI Responses API (sync, or
the Batch API when `OPENAI_BATCH`) to produce a `<base>.keypoints.md` document
(`## Задачи` / `## Тезисы` / `## Открытые вопросы`, plain text).

**Output layer** (`src/output.py`): `write_artifact` writes each artifact either as
a Drive sibling (`OUTPUT_TARGET=drive` — upload, or update an existing sibling in
place via `drive.update_file`, its id flowing through `list_folder_state`) or into a
local `OUTPUT_DIR` (`OUTPUT_TARGET=folder`).

For deterministic, agent-driven speaker correction, `src/relabel_transcript.py`
(and the `gdstt relabel` command) rewrite `Speaker N` labels from a `MAP.json`
while preserving utterance text byte-for-byte.

**Error handling is tiered**: `RefreshError`/`AuthError` propagate up to `main()` and
cause `SystemExit(1)` so the container restarts (after re-running `src.auth`); all other
exceptions are logged + sent to Telegram via `notify.notify_error` and the loop continues.

`process_item()` also emits one process summary per worked file with provider,
`processing_mode`, outcome, retry count, and duration. `run-once()` emits one folder summary per
folder and one cycle summary with provider, overall outcome, folder count,
pending count, processed count, failed count, `retry_total`,
skipped-by-size count, folder-error count, dry-run flag, and duration.

**STT layer** (`src/stt/`): `get_provider(config)` dispatches on `STT_PROVIDER`,
which is Deepgram-only (`""`/`disabled` skips transcription). The base
`STTProvider` exposes `transcribe_full()`; Deepgram does whole-file transcription
and overrides it. `transcribe_file()` (`transcribe.py`) calls `transcribe_full` and
logs the best-effort Deepgram cost. Deepgram sends a full-file audio copy
(`m4a_copy`, `mp3_96k`, or `mp3_192k`); tuning stays provider-specific, but the CLI
workflow stays stable.

**Auth** (`src/auth.py`): single OAuth user credential covers Drive (scopes `drive`
+ `cloud-platform`) — no service account. `gdstt auth` runs the OAuth flow for
initial setup, refresh, recovery, or headless/manual exchange.
`load_credentials` inspects the saved token's
`scopes` directly (because `from_authorized_user_file` echoes the requested scopes, not the
granted ones); a missing scope raises `AuthError` telling you to re-auth. Adding a scope to
`SCOPES` requires deleting `data/token.json` and re-running auth.

## Core invariants

- `STT_PROVIDER` is Deepgram-only. When it is absent, `load_config()` treats it as
  `deepgram`; set `STT_PROVIDER=disabled` (or empty) to skip transcription and only
  manage MP3 artifacts.
- Bootstrap and Drive-only commands use `load_config(validate_providers=False)`:
  `auth`, `doctor`, `list` / `status`, and `speakers set`.
- Processing commands validate provider configuration and can spend credits:
  `run`, `run-once`, `process`, `latest`, and `transcribe`.
- `relabel` is a local file transform that touches no Drive and spends nothing.
- `OPENAI_KEYPOINTS=true` runs after the transcript is produced and requires
  `OPENAI_API_KEY`; it writes a `<base>.keypoints.md` document.
- `OUTPUT_TARGET` selects where artifacts land: `drive` (siblings) or `folder`
  (`OUTPUT_DIR`, required when `folder`).
- Idempotency relies on `appProperties.source_video_id` and sibling stem
  matching as a fallback.
- New or changed operator behavior should be reflected in
  `tests/test_skill_docs.py` and `skills/gdstt-cli/SKILL.md`.

## Skill layering policy

- The canonical operator skill is a single file: `skills/gdstt-cli/SKILL.md`.
- It must stay sufficient for the default workflow: auth, inspect, single-file
  processing, `latest`, reprocess, folder safety, relabeling, and the agent
  keypoints workflow.
- Keep Google Drive setup (OAuth, scopes, folder-id discovery) in `README.md`, not
  the skill — the skill points to it with one line.
- Use a separate skill only if it has a genuinely different trigger, audience, and
  decision tree. If that day comes, add it as a sibling folder under `skills/`, not
  as a nested subskill inside the `gdstt-cli` package.

## Current provider posture

Deepgram is the only STT provider. Keep the common CLI workflow stable and move
provider tuning into the Deepgram notes in `README.md` / `SKILL.md`.

Today that means:

- Deepgram is the default and only transcription path; `STT_PROVIDER=""` keeps
  MP3-only mode.
- Keypoints generation (`OPENAI_KEYPOINTS`) is documented as an optional layer on
  top of the transcript, independent of the STT path.

## Conventions

- Tests mock all external services (Drive, OpenAI, Deepgram, ffmpeg); one test file
  per `src` module. No network in tests.
- `from __future__ import annotations` + `X | None` style type hints throughout.
- Compute Drive-name stems with `drive.drive_stem` (`os.path.splitext`, string-based), never
  `Path(...).stem`/`.name` — Drive names may contain `/`, which `Path` would treat as a path
  separator and drop. Sanitize local temp filenames with `drive.safe_local_name`.
- Secrets and tokens live in `./data` (gitignored); never commit `credentials.json` / `token.json`.

## Portability policy

- Shared rules belong in `AGENTS.md`.
- Canonical portable operator guidance belongs in the single
  `skills/gdstt-cli/SKILL.md`.
- Install the canonical package into each host with `gh skill install`; do not
  commit host-specific `.agents/skills/` or `.claude/skills/` copies.
- Prefer universal docs over editor-specific overlays.
- Validate the package and temporary local install with
  `python scripts/check-agent-skill.py`.
- When operator behavior changes, update `SKILL.md` and refresh its `version` and
  `last_updated` fields.
- If a tool-specific file is required, keep it as a thin pointer back to `AGENTS.md`
  or the main skill rather than redefining runtime behavior.
