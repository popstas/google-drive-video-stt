# Drive Video STT: CLI, Transcript Post-Processing, Filename Fix & OpenAI Pipeline

## Overview

This plan adopts the queued work in `docs/TODO.md` for the Google Drive video STT service.
The service currently runs headless, polling Drive folders, extracting MP3 from new MP4s, and
optionally transcribing to a sibling `.txt`. The queued work adds an operator-facing CLI over
the existing operations, a project skill that documents it, a transcript post-processing stage
that cleans output and maps speakers to the interlocutor names in the filename, a fix for
dropped characters in output filenames, and a new OpenAI-based transcription/keypoints pipeline.

## Context

- Impacted modules: `src/main.py` (entry point / polling loop), `src/drive.py` (folder state,
  download/upload, sibling detection), `src/extractor.py` (ffmpeg), `src/config.py`
  (env-driven frozen `Config`), `src/stt/` (providers + `get_provider` dispatch), `src/auth.py`.
- All flow is env-driven through `Config` (`load_config()` validates provider-specific vars).
- Idempotency is sibling-file based (presence of `<basename>.mp3` / `.txt` by basename).
- Tests mock all external services (Drive, OpenAI, Google STT, ffmpeg); no network in tests.
- `from __future__ import annotations` + `X | None` hints throughout; ruff line-length 100, py311.
- Adopted from `docs/TODO.md` (5 queued items). CLI scope confirmed as "wrap all existing
  operations" (auth, run loop, run-once, on-demand process, transcribe, list/status).

## Development Approach

- Testing approach: regular
- Complete each task fully before moving to the next
- Update this plan when scope changes during implementation

## Testing Strategy

- Unit tests required for every code-changing Task; mock all external services (no network)
- Run project tests after each Task before proceeding (`uv run pytest`)
- Keep one test file per `src` module, matching existing conventions

## Technical Details

- **CLI**: a single entry point (e.g. `python -m src.cli` / console script) with subcommands
  that wrap existing functions rather than duplicating logic. The polling loop in `src/main.py`
  becomes the `run` subcommand; `run_once()` becomes `run-once`. New subcommands call into the
  existing Drive/extractor/STT layers and reuse `load_config()`.
- **Filename bug**: a Drive name like `... - 2026/05/28 17:27 GMT+04:00 – Recording.mp4` is
  mangled because `/` is treated as a path separator by `Path(...).stem` / `.name`, dropping
  everything before the last `/`. Fix by computing Drive-name stems with `os.path.splitext`
  (string-based, not `Path`) and decoupling local temp filenames from the uploaded Drive name.
- **Transcript post-processing**: runs after STT produces the transcript. Cleans up the text,
  extracts the 2 interlocutor names from the video filename and maps them to `Speaker N`, and
  when extra speakers appear (beyond the expected 2) decides which real speaker each extra one
  merges into. The final transcript overwrites the original `.txt` on Google Drive.
- **OpenAI pipeline**: mirrors the `keypoints-transcription` skill at
  `/home/popstas/projects/text/obsidian/ExpertizeMe/.claude/skills/keypoints-transcription`.
  Config: `openai_api_key`, `proxy_url`, model `gpt-5.4-mini`, prompt derived from the skill.
  Use the modern OpenAI Responses API (optionally the OpenAI Agents SDK so the agent can run the
  Python scripts referenced in the skill). Consider batch-mode support (≈50% cost reduction).

## Implementation Steps

### Task 1: Build CLI interface wrapping all operations

- [x] Add a CLI entry point (e.g. `src/cli.py` with a console-script / `python -m` hook) using the standard arg-parsing approach for the project
- [x] `auth` subcommand → run the interactive OAuth flow (wraps `src/auth.py`)
- [x] `run` subcommand → the polling loop (wraps current `main()` in `src/main.py`)
- [x] `run-once` subcommand → a single polling cycle (wraps `run_once()`)
- [x] `process <file|folder>` subcommand → on-demand extraction (+ transcription) for a given Drive file or folder
- [x] `transcribe <mp3>` subcommand → STT-only on an existing MP3 via the configured provider
- [x] `list` / `status` subcommand → show folder state (sibling MP3/TXT presence) without doing work
- [x] Reuse `load_config()` and existing Drive/extractor/STT layers; do not duplicate business logic
- [x] write tests for the CLI argument parsing and subcommand dispatch (mock the underlying operations)
- [x] run project tests - must pass before next task

### Task 2: Add a project skill documenting all CLI capabilities

- [x] Create the project skill describing every CLI command, its arguments, and example invocations
- [x] Document required/optional env vars and provider-specific configuration referenced by each command
- [x] Keep the skill in sync with the actual command surface implemented in Task 1
- [x] write tests or a doc/CLI consistency check where practical (e.g. assert documented commands match registered subcommands)
- [x] run project tests - must pass before next task

### Task 3: Fix dropped characters in output filenames

- [x] Reproduce the bug: a Drive name containing `/` (e.g. `... - 2026/05/28 17:27 GMT+04:00 – Recording.mp4`) yields siblings missing everything before the last `/`
- [x] Replace `Path(...).stem` / `.name` usage on Drive names with `os.path.splitext` (string-based) in `src/drive.py` and `src/main.py`
- [x] Decouple local temp filenames from the uploaded Drive name so temp paths stay filesystem-safe while uploads keep the original name + correct extension
- [x] Verify sibling-presence detection (`list_folder_state`) still matches by the correct basename after the fix
- [x] write tests covering Drive names with `/` and other path-like characters (no characters dropped; correct sibling naming)
- [x] run project tests - must pass before next task

### Task 4: Add transcript post-processing and speaker mapping

- [x] Add a post-processing step that runs after STT produces the transcript
- [x] Clean up the raw transcript into a final transcript
- [x] Extract the 2 interlocutor names from the video filename and map them to `Speaker N`
- [x] When more speakers appear than expected (only 2 interlocutors), decide which real speaker each extra one should be merged into
- [x] Overwrite the original `.txt` on Google Drive with the final transcript
- [x] write tests for name extraction, speaker mapping, extra-speaker merging, and the Drive overwrite path (mock Drive)
- [x] run project tests - must pass before next task

### Task 5: Add an OpenAI transcription/keypoints pipeline

- [x] Study the `keypoints-transcription` skill at `/home/popstas/projects/text/obsidian/ExpertizeMe/.claude/skills/keypoints-transcription` and base the pipeline + prompt on it
- [x] Add config: `openai_api_key`, `proxy_url`, model `gpt-5.4-mini` (wire into `load_config()` / `Config` with validation)
- [x] Implement the pipeline using the modern OpenAI Responses API (optionally the OpenAI Agents SDK so the agent can run the Python scripts referenced in the skill)
- [x] Integrate as an STT/post-processing provider path consistent with the existing `src/stt/` provider dispatch
- [x] Add batch-mode support (≈50% cost reduction) where applicable, behind config
- [x] write tests mocking the OpenAI API (no network) covering the pipeline and batch vs non-batch paths
- [x] run project tests - must pass before next task

### Task 6: Verify acceptance criteria

- [ ] Verify all requirements from Overview are implemented (CLI, skill, filename fix, post-processing, OpenAI pipeline)
- [ ] Run full project test suite (`uv run pytest`)
- [ ] Run project linter (`uv run ruff check`) - all issues must be fixed

## Post-Completion

*Items requiring manual intervention - no checkboxes, informational only*

- Set `OPENAI_API_KEY` / proxy and any new env vars in the deployment environment and `.env.example`.
- Adding a new STT/post-processing provider may require a real end-to-end run against Drive to confirm transcript overwrite behaves as expected.
- If new OAuth scopes are ever required, delete `data/token.json` and re-run `python -m src.auth`.
