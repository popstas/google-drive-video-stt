# Deepgram-only simplification + agent skill — design

Date: 2026-06-03
Branch: `pr-4-final`
Status: approved (brainstorming)

## Problem

`pr-4-final` implemented every queued TODO (CLI, project skill, transcript
post-processing with speaker mapping, filename fix, OpenAI pipeline), but in
doing so grew to ~10k added lines. It now carries four STT providers, a
JSON-intent planning layer, an 18 KB interactive setup wizard, and a 10-file
skill bundle. The owner wants a *simple* tool focused on one job: an agent runs
"расшифруй последний созвон" and it just happens, using Deepgram only.

## Goal

Keep popstas' original working model (headless Drive polling daemon + the
completed-TODO features) but strip the project down to a lean, Deepgram-only
processor with a single small agent skill. No new capabilities beyond a
convenience "process the newest recording" command.

## Decisions (resolved during brainstorming)

1. **Daemon + Docker + Telegram stay.** The background polling model from
   popstas' `main` is kept; Docker/Compose remain its deployment wrapper.
2. **"Renaming" means speaker names** in the transcript text (`Speaker 1` →
   `Yana`), not renaming Drive files.
3. **OpenAI pipeline stays as popstas built it** — a *verbatim refiner*
   (relabel speakers + merge spurious ones + tidy whitespace, no
   summarization), optional via `OPENAI_POSTPROCESS=true`, **batch mode kept**.
   The "keypoints" wording in the old TODO was never implemented as keypoints by
   popstas and is **not** in scope.
4. **JSON-intent layer is removed** (style B). The agent calls plain CLI
   commands; safety is `--dry-run` plus rules documented in the skill.
5. **Deepgram is the only STT provider.** `google`, `asr`, and `openai`-as-STT
   are removed.
6. **MP3 is retained** as an audio source for Deepgram (`DEEPGRAM_AUDIO_SOURCE`
   `m4a_copy` / `mp3_96k` / `mp3_192k`) alongside the sibling-MP3 upload.
7. **`speakers set` is kept** so the agent can assign speaker names manually
   when the filename doesn't parse.
8. **Google Drive setup lives in README**, not in the skill.

## Architecture

The pipeline per file is unchanged in shape:

```
Drive MP4
  └─ ffmpeg → sibling .mp3 (uploaded)      [extractor.extract_mp3]
  └─ ffmpeg → temp audio for STT           [extractor: m4a_copy | mp3_96k | mp3_192k]
       └─ Deepgram Nova-3 + diarization    [stt/deepgram_provider]
            └─ if OPENAI_POSTPROCESS: LLM refiner (does speaker mapping)  [openai_pipeline]
               else:                  deterministic clean + speaker mapping [postprocess]
                 └─ upload sibling .txt     [drive.upload / update]
```

### Keep (core)

- `src/stt/deepgram_provider.py` — Deepgram Nova-3 + diarization (only STT).
- `src/stt/base.py`, `src/stt/__init__.py` — trimmed to Deepgram dispatch.
- `src/stt/transcribe.py` — simplified to the Deepgram full-file path.
- `src/stt/deepgram_usage.py` — best-effort USD cost logging.
- `src/extractor.py` — `extract_mp3` + `extract_m4a_copy`.
- `src/postprocess.py` — deterministic speaker-name mapping + cleanup.
- `src/openai_pipeline.py` — verbatim refiner, optional, with batch.
- `src/drive.py`, `src/auth.py`, `src/notify.py` — Drive, OAuth, Telegram.
- `src/main.py` — polling daemon (`run` / `run_once`) + on-demand
  `process_target`; provider-specific branches reduced to Deepgram.
- `src/config.py` — env-driven `Config`, trimmed to Deepgram + OpenAI-refine.
- `Dockerfile`, `docker-compose.yml` — deploy the daemon.
- `config/deepgram-keyterms.txt` — keyterm prompting.

### Remove (the bloat)

- STT providers: `src/stt/google_provider.py`, `src/stt/openai_provider.py`,
  `src/stt/asr_provider.py`, and `src/stt/chunker.py` (only chunked providers
  used it; Deepgram is full-file).
- JSON-intent layer: `src/pipeline_profile.py`, `src/pipeline_policy.py`,
  `src/pipeline_executor.py`, `config/pipelines/`, and the `plan` / `execute`
  CLI subcommands.
- `src/setup.py` (interactive wizard) — replaced by README instructions.
- `refresh-names` CLI subcommand (file renaming, out of scope).
- Skill resource sprawl: `skills/gdstt-cli/references/*` (5 files) and
  `skills/gdstt-cli/examples/*` (4 files) collapse into one short `SKILL.md`.
- The matching tests for everything removed (e.g. `tests/test_stt_google.py`,
  `tests/test_pipeline_*.py`, `tests/test_setup.py`, `tests/test_skill_docs.py`
  parts that assert removed docs/commands).

### Add

- **`gdstt latest [--folder ID] [--dry-run]`** — pick the newest MP4 in the
  configured folder (or `--folder`) by Drive `createdTime`, then run the full
  pipeline (extract → Deepgram → post-process → upload sibling `.txt`). This is
  the "расшифруй последний созвон" entry point. Implemented by reusing
  `process_target`; the only new logic is "find newest MP4 in a folder", added
  to `src/drive.py` and exposed as a CLI command.

## Final CLI surface

`gdstt <command>` (also `uv run python -m src.cli`):

| Command | Purpose |
| --- | --- |
| `auth [--manual] [url]` | OAuth flow → `data/token.json` |
| `latest [--folder ID] [--dry-run]` | Process the newest MP4 in a folder |
| `process <id> [--folder] [--reprocess-txt] [--dry-run]` | One Drive file/folder |
| `transcribe <audio> [-o PATH]` | Local audio → transcript, no Drive |
| `list` / `status` [--folder ID] | Show sibling MP3/TXT state |
| `run` | Continuous polling daemon |
| `run-once [--dry-run]` | One polling cycle |
| `speakers set <id> <names…>` | Store speaker names on a Drive MP4 |
| `doctor [--drive]` | Check local auth/config (no pipeline profile) |

Removed vs `pr-4-final`: `setup`, `plan`, `execute`, `refresh-names`. The
`--max-size` / `--confirm-large` size guards on `process` / `run-once` are
retained (cheap, and useful for folder runs).

## Configuration

`.env` keeps only the Deepgram + OpenAI-refine surface:

- Core: `FOLDER_IDS`, `POLL_INTERVAL`, `BITRATE`, `DATA_DIR`, `PROXY_URL`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- STT: `STT_PROVIDER` defaults to / is fixed at `deepgram`; `STT_LANGUAGE`
  (default `ru`); `DEEPGRAM_API_KEY` / `DEEPGRAM_API_KEY_FILE`,
  `DEEPGRAM_MODEL`, `DEEPGRAM_DIARIZE_MODEL`, `DEEPGRAM_AUDIO_SOURCE`,
  `DEEPGRAM_TXT_FORMATTER`, `DEEPGRAM_KEYTERMS_ENABLED`, `DEEPGRAM_KEYTERMS_FILE`.
- Post-processing: `STT_POSTPROCESS` (deterministic, default on),
  `OPENAI_POSTPROCESS`, `OPENAI_API_KEY`, `OPENAI_MODEL` (`gpt-5.4-mini`),
  `OPENAI_BATCH`.

Removed: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_GCS_BUCKET`, `ASR_URL`,
`STT_CHUNK_SECONDS`, and any `config/pipelines` profile paths. `load_config`
no longer accepts/branches on the removed providers; `SUPPORTED_STT_PROVIDERS`
reduces to `("", "deepgram")` — `""` keeps popstas' MP3-only mode (extract and
upload the sibling MP3 with no transcription), `"deepgram"` enables STT.

## Skill

One file: `skills/gdstt-cli/SKILL.md` (no `references/`, no `examples/`).
Contents: the command table above, the "latest" headline flow, the safety rules
(ask before spending credits / overwriting TXT; never print secrets), and a
one-line pointer to README for Google Drive setup. Skill frontmatter
`description` updated to match the reduced surface.

## README

Gains a "Google Drive setup" section (gcloud / ADC / Console OAuth, scopes,
folder-id discovery) — migrated from the removed `setup.py` and the existing
README setup prose. The provider matrix collapses to Deepgram. Daemon/Docker
deployment section stays.

## Testing

- Keep and adapt: `tests/test_stt_deepgram*.py`, `tests/test_postprocess.py`,
  `tests/test_openai_pipeline.py`, `tests/test_extractor.py`,
  `tests/test_drive.py`, `tests/test_auth.py`, `tests/test_config.py`,
  `tests/test_main.py`, `tests/test_cli.py`, `tests/test_notify.py`.
- Remove: tests for deleted modules (google/asr/openai-STT providers, chunker,
  pipeline_*, setup) and skill-doc assertions tied to removed resources.
- Add: a `latest` command test (newest-MP4 selection, dispatch to
  `process_target`, `--dry-run`) and a `drive` newest-MP4 helper test.
- `uv run pytest` and `uv run ruff check` must pass after the cut.

## Out of scope

- Keypoints / summarization (explicitly deferred; the OpenAI layer stays a
  verbatim refiner).
- Renaming Drive files / `refresh-names`.
- OpenAI Agents SDK.
- Any new STT provider.

## Acceptance criteria

1. Only Deepgram remains as an STT provider; google/asr/openai-STT code, config,
   and tests are gone.
2. JSON-intent layer and `setup.py` are gone; CLI matches the table above.
3. `gdstt latest` transcribes the newest recording in a folder end-to-end
   (extract → Deepgram → speaker-named TXT → upload), honoring `--dry-run`.
4. Deterministic speaker naming works without OpenAI; `OPENAI_POSTPROCESS=true`
   still routes through the refiner (with batch available).
5. `gdstt speakers set` assigns speaker names used by post-processing.
6. The skill is a single `SKILL.md`; Google Drive setup is in README only.
7. Daemon + Docker + Telegram still function.
8. `uv run pytest` and `uv run ruff check` pass.
