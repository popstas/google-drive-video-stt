Deepgram-only Simplification + Agent Keypoints Skill

## Overview

Strip google-drive-video-stt down to a lean, Deepgram-only core and fold the
real `keypoints-transcription` workflow in as the agent-facing skill. Remove the
extra STT providers, the JSON-intent planning layer, and the interactive setup
wizard. Keep popstas' polling daemon (Docker + Telegram). Per recording, produce
a speaker-named transcript plus a Keypoints document, written either to Google
Drive or to a local folder.

Full design: `docs/superpowers/specs/2026-06-03-deepgram-only-simplification-design.md`.
Reference skill files: `docs/reference/relabel_transcript.py`,
`docs/reference/keypoints-transcription-SKILL.md`.

## Context

- Files involved (existing): `src/main.py`, `src/config.py`, `src/cli.py`,
  `src/drive.py`, `src/extractor.py`, `src/postprocess.py`,
  `src/openai_pipeline.py`, `src/stt/` (`deepgram_provider.py`, `base.py`,
  `__init__.py`, `transcribe.py`, `deepgram_usage.py`), `skills/gdstt-cli/`,
  `README.md`, `.env.example`, `CLAUDE.md`.
- Files to delete: `src/stt/google_provider.py`, `src/stt/openai_provider.py`,
  `src/stt/asr_provider.py`, `src/stt/chunker.py`, `src/pipeline_profile.py`,
  `src/pipeline_policy.py`, `src/pipeline_executor.py`, `src/setup.py`,
  `config/pipelines/`, `skills/gdstt-cli/references/`,
  `skills/gdstt-cli/examples/`, and the matching `tests/test_*` files.
- Files to create: `src/relabel_transcript.py`, `src/output.py`,
  `tests/test_relabel_transcript.py`, `tests/test_output.py`.
- Related patterns: env-driven frozen `Config` built by `load_config()`;
  sibling-file idempotency in `drive.list_folder_state`; provider dispatch in
  `src/stt/__init__.py:get_provider`; transcript flow in
  `src/main.py:process_item` (Deepgram -> postprocess/keypoints -> upload).
- Dependencies: existing `requests`, `openai` (kept for the auto keypoints path),
  `google-api-python-client`; ffmpeg on PATH. No new external dependencies.

## Development Approach

- Testing approach: Regular (code first, then tests), matching the repo's
  one-test-file-per-module convention; all external services mocked, no network.
- Complete each task fully before moving to the next.
- CRITICAL: every task MUST include new/updated tests.
- CRITICAL: all tests must pass (`uv run pytest`) and lint clean
  (`uv run ruff check`) before starting the next task.
- Keep `STT_PROVIDER=""` MP3-only mode working throughout (popstas behavior).

## Implementation Steps

### Task 1: Reduce STT layer to Deepgram only

**Files:**
- Modify: `src/stt/__init__.py`, `src/stt/transcribe.py`, `src/stt/base.py`
- Delete: `src/stt/google_provider.py`, `src/stt/openai_provider.py`,
  `src/stt/asr_provider.py`, `src/stt/chunker.py`
- Delete tests: `tests/test_stt_google.py`, `tests/test_stt_openai.py`,
  `tests/test_stt_asr.py`, `tests/test_stt_chunker.py`,
  `tests/test_stt_deepgram_factory.py` (if it asserts multi-provider dispatch)
- Modify tests: `tests/test_stt_transcribe.py`, `tests/test_stt_contract.py`

- [x] In `src/stt/__init__.py`, drop the `openai`/`google`/`asr` branches from
      `get_provider`; keep only the `deepgram` branch (and the unknown-provider
      error).
- [x] In `src/stt/transcribe.py`, remove the chunked fallback path (Deepgram is
      full-file via `transcribe_full`); keep the Deepgram cost logging.
- [x] Delete the four provider/chunker source files listed above.
- [x] Delete the obsolete provider tests; update `test_stt_transcribe.py` and
      `test_stt_contract.py` to cover only the Deepgram full-file path.
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 2: Remove the JSON-intent layer, setup wizard, and dead CLI commands

**Files:**
- Modify: `src/cli.py`
- Delete: `src/pipeline_profile.py`, `src/pipeline_policy.py`,
  `src/pipeline_executor.py`, `src/setup.py`, `config/pipelines/`
- Delete tests: `tests/test_pipeline_profile.py`, `tests/test_pipeline_policy.py`,
  `tests/test_pipeline_executor.py`, `tests/test_setup.py`
- Modify tests: `tests/test_cli.py`

- [x] Remove the `plan`, `execute`, `setup`, and `refresh-names` subcommands and
      their `cmd_*` handlers from `src/cli.py`; drop the now-unused imports
      (`pipeline_executor`, `pipeline_policy`, `pipeline_profile`, `setup`).
- [x] Simplify `cmd_doctor` to report `DATA_DIR`, credentials/token presence,
      `FOLDER_IDS` count, `STT_PROVIDER`, and (with `--drive`) folder listing -
      without `pipeline_profile` readiness.
- [x] Delete the pipeline/setup source files and `config/pipelines/`.
- [x] Delete the obsolete tests; update `test_cli.py` to the reduced command set.
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 3: Trim and extend Config

**Files:**
- Modify: `src/config.py`
- Modify tests: `tests/test_config.py`

- [x] Remove `google_cloud_project`, `google_stt_gcs_bucket`, `asr_url`,
      `openai_postprocess`, and `stt_chunk_seconds` fields and their parsing;
      reduce `SUPPORTED_STT_PROVIDERS` to `("", "deepgram")`.
- [x] Add fields: `output_target` (`drive` default | `folder`), `output_dir`
      (`Path | None`), `openai_keypoints` (bool, default False). Keep
      `openai_api_key`, `openai_model` (default `gpt-5.4-mini`), `openai_batch`.
- [x] Validate: `output_target` in `("drive", "folder")`; when `folder`,
      `output_dir` is required; when `openai_keypoints` is True, `openai_api_key`
      is required. Drop the google/asr validation blocks.
- [x] Update `test_config.py`: remove google/asr/openai-STT cases; add
      output-target and openai-keypoints validation cases.
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 4: Add the output destination layer

**Files:**
- Create: `src/output.py`
- Create test: `tests/test_output.py`
- Modify: `src/main.py`

- [x] Implement `src/output.py` with `write_artifact(service, *, base_name,
      suffix, text, folder_id, config, tmp_dir, existing_id=None)` that, for
      `OUTPUT_TARGET=drive`, writes a temp file and uploads/updates the sibling
      via existing `drive.upload` / `drive.update_file`; for `folder`, writes
      `<output_dir>/<base_name><suffix>` and creates `output_dir` if missing.
- [x] Refactor `src/main.py:_save_and_upload_txt` to call
      `output.write_artifact(..., suffix=".txt")`; keep the Drive path behavior
      identical when `OUTPUT_TARGET=drive`.
- [x] Write `tests/test_output.py` covering both targets (mock `drive`; assert
      folder file path/content and Drive upload call).
- [x] Update `tests/test_main.py` for the new save path.
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 5: Adopt relabel_transcript.py and the `relabel` command

**Files:**
- Create: `src/relabel_transcript.py` (from `docs/reference/relabel_transcript.py`)
- Create test: `tests/test_relabel_transcript.py`
- Modify: `src/cli.py`

- [x] Copy the reference script into `src/relabel_transcript.py`; expose a
      `relabel(src_text, map_cfg) -> str` function (parse + resolve + block-merge)
      callable in-process, keeping the existing CLI `main()` for standalone use.
- [x] Add a `relabel` CLI subcommand (`--in`, `--out`, `--map`, `--no-header`)
      in `src/cli.py` that calls into the module.
- [x] Write `tests/test_relabel_transcript.py`: `default` map applied, `exceptions`
      override by verbatim text, consecutive same-name blocks merged, unmapped
      labels reported, and `[HH:MM:SS] Speaker N:` parsing - assert utterance text
      is byte-for-byte preserved.
- [x] Update `test_cli.py` to cover `relabel` dispatch.
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 6: Add the `latest` command

**Files:**
- Modify: `src/drive.py`, `src/cli.py`
- Modify tests: `tests/test_drive.py`, `tests/test_cli.py`

- [x] Add `find_newest_mp4(service, folder_id) -> dict | None` in `src/drive.py`
      using `files.list` ordered by `createdTime desc` (mp4, not trashed),
      returning the file dict or None when empty.
- [x] Add a `latest [--folder ID] [--dry-run]` subcommand in `src/cli.py` that
      resolves the folder (arg or first of `FOLDER_IDS`), finds the newest mp4,
      and dispatches to `main_module.process_target` (honoring `--dry-run`);
      log clearly when the folder has no mp4.
- [x] Write tests: `find_newest_mp4` ordering/empty cases (mock Drive); `latest`
      dispatch and dry-run (mock `process_target`).
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 7: Replace the OpenAI refiner with keypoints generation

**Files:**
- Modify: `src/openai_pipeline.py`, `src/main.py`
- Modify tests: `tests/test_openai_pipeline.py`, `tests/test_main.py`

- [x] In `src/openai_pipeline.py`, replace the verbatim-refiner `INSTRUCTIONS`
      and `refine_transcript` with `generate_keypoints(transcript, file_name, config,
      *, speaker_names=None, usage=None) -> str` that calls the Responses API
      (sync, and batch when `OPENAI_BATCH`) with a Keypoints prompt producing
      `## Задачи` / `## Тезисы` / `## Открытые вопросы` (plain text, no wikilinks).
      Keep the client/proxy/batch plumbing and usage normalization.
- [x] In `src/main.py:process_item`, after the deterministic `postprocess`
      transcript is produced and saved, when `config.openai_keypoints` is set,
      call `generate_keypoints` and write `<base>.keypoints.md` via
      `output.write_artifact(suffix=".keypoints.md")`. Remove the old
      `openai_postprocess` branch.
- [x] Update `tests/test_openai_pipeline.py` to mock the keypoints call (sync +
      batch); update `tests/test_main.py` for the keypoints artifact path.
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 8: Collapse the skill to a single SKILL.md

**Files:**
- Modify: `skills/gdstt-cli/SKILL.md`
- Delete: `skills/gdstt-cli/references/`, `skills/gdstt-cli/examples/`
- Modify tests: `tests/test_skill_docs.py`

- [x] Rewrite `SKILL.md` as one file: updated frontmatter `description`; the
      command table (`auth`, `latest`, `process`, `transcribe`, `relabel`,
      `list`, `run`, `run-once`, `speakers set`, `doctor`); the agent keypoints
      workflow (get raw transcript -> reason speakers -> confirm mapping with the
      user -> build MAP.json -> `gdstt relabel` -> write `<base>.keypoints.md` ->
      place in destination); the Keypoints template (Задачи `### Ответственный`,
      Тезисы, Открытые вопросы) minus wikilinks/vault style; safety rules; and a
      one-line pointer to README for Google Drive setup.
- [x] Delete the `references/` and `examples/` directories.
- [x] Reduce `tests/test_skill_docs.py` to assert documented commands match the
      registered CLI subcommands (drop assertions about removed resources).
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 9: Update README, .env.example, and CLAUDE.md

**Files:**
- Modify: `README.md`, `.env.example`, `CLAUDE.md`

- [x] README: collapse the provider matrix to Deepgram; add a "Google Drive
      setup" section (gcloud / ADC / Console OAuth, scopes `drive`, folder-id
      discovery) migrated from the removed `setup.py`; document `OUTPUT_TARGET` /
      `OUTPUT_DIR`, `OPENAI_KEYPOINTS`, and the `latest` / `relabel` commands;
      remove google/asr/openai-STT and `STT_CHUNK_SECONDS` docs.
- [x] `.env.example`: keep core + Deepgram + output + openai-keypoints keys only;
      delete google/asr/openai-STT and chunk keys.
- [x] `CLAUDE.md`: update the architecture/STT-layer description to Deepgram-only
      and the new keypoints/output flow; refresh the project layout listing.
      (CLAUDE.md is a thin pointer to AGENTS.md, which is the source of truth for
      architecture/STT/layout; the real content was updated in AGENTS.md.)
- [x] Run `uv run pytest` and `uv run ruff check` - must pass before next task.

### Task 10: Verify acceptance criteria

- [ ] Run full test suite: `uv run pytest` - all pass.
- [ ] Run linter: `uv run ruff check` - clean.
- [ ] Grep confirms removal: no references to `google_provider`, `asr_provider`,
      `openai_provider`, `chunker`, `pipeline_profile`, `pipeline_policy`,
      `pipeline_executor`, or `setup` remain in `src/`.
- [ ] Spot-check: `uv run python -m src.cli --help` lists exactly `auth`,
      `latest`, `process`, `transcribe`, `relabel`, `list`/`status`, `run`,
      `run-once`, `speakers`, `doctor`.

### Task 11: Update documentation and close out

- [ ] Move `docs/superpowers/specs/2026-06-03-deepgram-only-simplification-design.md`
      reference note and this plan into `docs/plans/completed/`.
- [ ] Update `docs/TODO.md` to drop items now implemented.
- [ ] Remove `docs/reference/` scratch copies once `src/relabel_transcript.py`
      and the rewritten `SKILL.md` are in place.

## Post-Completion

- Set `DEEPGRAM_API_KEY` (and `OPENAI_API_KEY` if `OPENAI_KEYPOINTS=true`) plus
  `OUTPUT_TARGET` / `OUTPUT_DIR` in the deployment `.env`.
- Run one real Drive recording end-to-end (`gdstt latest`) to confirm transcript
  and keypoints land in the configured destination.
- If OAuth scopes ever change, delete `data/token.json` and re-run `gdstt auth`.
