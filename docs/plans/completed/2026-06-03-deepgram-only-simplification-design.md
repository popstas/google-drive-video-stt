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
"расшифруй последний созвон" and it just happens, using Deepgram only — and the
result should include the speaker-corrected transcript **plus a Keypoints
summary**, like the `keypoints-transcription` skill the old TODO referenced.

## Goal

Keep popstas' original working model (headless Drive polling daemon) but strip
the project to a lean, Deepgram-only core, and fold the real
`keypoints-transcription` workflow in as the agent-facing skill. Two artifacts
per recording: a speaker-named transcript and a Keypoints document.

## Decisions (resolved during brainstorming)

1. **Daemon + Docker + Telegram stay.** The background polling model from
   popstas' `main` is kept; Docker/Compose remain its deployment wrapper.
2. **"Renaming" means speaker names** in the transcript text (`Speaker 1` →
   `Yana`), not renaming Drive files.
3. **JSON-intent layer is removed** (style B). The agent calls plain CLI
   commands; safety is `--dry-run` plus rules documented in the skill.
4. **Deepgram is the only STT provider.** `google`, `asr`, and `openai`-as-STT
   are removed.
5. **MP3 is retained** as an audio source for Deepgram (`DEEPGRAM_AUDIO_SOURCE`
   `m4a_copy` / `mp3_96k` / `mp3_192k`) alongside the sibling-MP3 upload.
6. **`speakers set` is kept** so the agent can assign speaker names manually
   when the filename doesn't parse.
7. **Google Drive setup lives in README**, not in the skill.

### Keypoints integration (from the real `keypoints-transcription` skill)

The actual reference skill (uploaded by the owner) is model-driven and produces
**two** documents — a speaker-corrected transcript and a `Keypoints` summary
(Задачи / Тезисы / Открытые вопросы) — using a deterministic
`relabel_transcript.py` that applies a model-decided `MAP.json` (a `default`
label→name map plus per-utterance `exceptions`). It is smarter than popstas'
free-text rewrite: the model only decides the speaker MAP; the script guarantees
the utterance text stays verbatim.

8. **Adopt that two-document model.** Output = a speaker-corrected transcript +
   a Keypoints document. `relabel_transcript.py` is brought into the project as
   the canonical deterministic relabeler (used by both paths below).
9. **Two execution paths (2в, agent-first):**
   - **Agent path (primary)** — the project skill `SKILL.md` carries the
     `keypoints-transcription` workflow adapted to this project: get the raw
     diarized transcript, reason about speaker→name mapping, **confirm the
     mapping with the user**, build `MAP.json`, run `relabel_transcript.py`,
     write the Keypoints document, place both in the destination. The agent (the
     Claude session) does the reasoning; no OpenAI API call is needed here.
   - **Auto path (best-effort, optional)** — the daemon / `latest` / `process`
     stay a plain conveyor: Deepgram → deterministic speaker names from the
     filename (`postprocess.py`) → transcript. When `OPENAI_KEYPOINTS=true`, an
     OpenAI API call additionally produces a Keypoints document unattended.
10. **Output destination is env-configurable:** `OUTPUT_TARGET=drive` writes the
    sibling files on Google Drive next to the video (current behavior);
    `OUTPUT_TARGET=folder` writes them into `OUTPUT_DIR`. Names are plain text —
    **no Obsidian wikilinks, no `Management/` table, no vault style rules, no
    off-record redaction** (all dropped for simplicity).

## Artifacts and naming

Per recording with base name `<base>` (the video stem, slash-safe — see filename
fix already in `pr-4-final`):

- `<base>.mp3` — extracted audio (Drive sibling; unchanged from popstas).
- `<base>.txt` — speaker-named transcript (the auto path's primary output).
- `<base>.keypoints.md` — Keypoints summary (agent path always; auto path when
  `OPENAI_KEYPOINTS=true`).

Both `.txt` and `.keypoints.md` go to the configured destination (Drive sibling
or `OUTPUT_DIR`).

## Architecture

### Auto path (daemon / `latest` / `process`)

```
Drive MP4
  └─ ffmpeg → sibling .mp3 (uploaded)            [extractor.extract_mp3]
  └─ ffmpeg → temp STT audio (m4a_copy|mp3_96k|mp3_192k)  [extractor]
       └─ Deepgram Nova-3 + diarization          [stt/deepgram_provider]
            └─ deterministic speaker names from filename   [postprocess]
                 └─ write <base>.txt to destination        [output]
            └─ if OPENAI_KEYPOINTS: OpenAI → <base>.keypoints.md  [openai_pipeline]
                 └─ write to destination                   [output]
```

### Agent path (project skill, primary)

```
human → agent: "сделай расшифровку последнего созвона"
  1. gdstt latest --dry-run / gdstt transcribe → raw diarized transcript
  2. agent reasons: Speaker N → real names (filename + content), spot extras
  3. agent shows mapping, asks the human to confirm
  4. agent writes MAP.json (default + exceptions)
  5. gdstt relabel --in RAW --out <base>.txt --map MAP.json   [relabel_transcript.py]
  6. agent writes <base>.keypoints.md (Задачи / Тезисы / Открытые вопросы)
  7. place both in destination (gdstt upload / OUTPUT_DIR)
```

### Keep (core)

- `src/stt/deepgram_provider.py` — Deepgram Nova-3 + diarization (only STT).
- `src/stt/base.py`, `src/stt/__init__.py` — trimmed to Deepgram dispatch.
- `src/stt/transcribe.py` — simplified to the Deepgram full-file path.
- `src/stt/deepgram_usage.py` — best-effort USD cost logging.
- `src/extractor.py` — `extract_mp3` + `extract_m4a_copy`.
- `src/postprocess.py` — deterministic speaker-name mapping + cleanup (auto path).
- `src/drive.py`, `src/auth.py`, `src/notify.py` — Drive, OAuth, Telegram.
- `src/main.py` — polling daemon (`run` / `run_once`) + on-demand
  `process_target`; provider branches reduced to Deepgram.
- `src/config.py` — env-driven `Config`, trimmed + new keys (see Configuration).
- `Dockerfile`, `docker-compose.yml` — deploy the daemon.
- `config/deepgram-keyterms.txt` — keyterm prompting.

### Add

- `src/relabel_transcript.py` — adopted from the reference skill: deterministic
  relabel + block-merge driven by `MAP.json` (`default` + `exceptions`),
  guaranteeing verbatim utterance text. Supports the `[HH:MM:SS] Speaker N:`
  diarized format this project emits. (Krisp `**Label | MM:SS**` support carried
  over verbatim; harmless.)
- `src/output.py` — destination layer: `write_artifact(name, text, ...)` routes
  to Drive sibling upload or `OUTPUT_DIR` based on `OUTPUT_TARGET`. Used by the
  auto path and exposed for the agent path.
- `gdstt latest` — find newest MP4 in a folder by Drive `createdTime`, run the
  auto path. New helper in `src/drive.py` + CLI command.
- `gdstt relabel --in IN --out OUT --map MAP.json` — thin CLI wrapper over
  `relabel_transcript.py` for the agent path.
- Keypoints generation for the auto path in `src/openai_pipeline.py`
  (`generate_keypoints(transcript, names) -> str`), gated by `OPENAI_KEYPOINTS`.
  `relabel_transcript.py` covers the deterministic relabel, so the auto path
  builds its MAP from `postprocess`-derived names rather than a free-text rewrite.

### Remove (the bloat)

- STT providers: `src/stt/google_provider.py`, `src/stt/openai_provider.py`,
  `src/stt/asr_provider.py`, and `src/stt/chunker.py` (Deepgram is full-file).
- JSON-intent layer: `src/pipeline_profile.py`, `src/pipeline_policy.py`,
  `src/pipeline_executor.py`, `config/pipelines/`, and the `plan` / `execute`
  CLI subcommands.
- `src/setup.py` (interactive wizard) — replaced by README instructions.
- `refresh-names` CLI subcommand (file renaming, out of scope).
- popstas' free-text verbatim-rewrite refiner prompt in `openai_pipeline.py`
  (replaced by the MAP + keypoints approach). Keep the OpenAI client/batch
  plumbing.
- Skill resource sprawl: `skills/gdstt-cli/references/*` (5 files) and
  `skills/gdstt-cli/examples/*` (4 files) collapse into one `SKILL.md`.
- Tests for everything removed.

## Final CLI surface

`gdstt <command>` (also `uv run python -m src.cli`):

| Command | Purpose |
| --- | --- |
| `auth [--manual] [url]` | OAuth flow → `data/token.json` |
| `latest [--folder ID] [--dry-run]` | Auto-process the newest MP4 in a folder |
| `process <id> [--folder] [--reprocess-txt] [--dry-run]` | One Drive file/folder |
| `transcribe <audio> [-o PATH]` | Local audio → raw transcript, no Drive |
| `relabel --in IN --out OUT --map MAP` | Apply a speaker MAP deterministically |
| `list` / `status` [--folder ID] | Show sibling MP3/TXT state |
| `run` | Continuous polling daemon |
| `run-once [--dry-run]` | One polling cycle |
| `speakers set <id> <names…>` | Store speaker names on a Drive MP4 |
| `doctor [--drive]` | Check local auth/config |

Removed vs `pr-4-final`: `setup`, `plan`, `execute`, `refresh-names`. The
`--max-size` / `--confirm-large` guards on `process` / `run-once` are retained.

## Configuration

`.env` surface:

- Core: `FOLDER_IDS`, `POLL_INTERVAL`, `BITRATE`, `DATA_DIR`, `PROXY_URL`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Output: `OUTPUT_TARGET` (`drive` default | `folder`), `OUTPUT_DIR` (required
  when `folder`).
- STT: `STT_PROVIDER` (`""` = MP3-only mode, `deepgram` = STT); `STT_LANGUAGE`
  (default `ru`); `DEEPGRAM_API_KEY` / `DEEPGRAM_API_KEY_FILE`, `DEEPGRAM_MODEL`,
  `DEEPGRAM_DIARIZE_MODEL`, `DEEPGRAM_AUDIO_SOURCE`, `DEEPGRAM_TXT_FORMATTER`,
  `DEEPGRAM_KEYTERMS_ENABLED`, `DEEPGRAM_KEYTERMS_FILE`.
- Post-processing: `STT_POSTPROCESS` (deterministic names, default on),
  `OPENAI_KEYPOINTS` (auto-path keypoints, default off), `OPENAI_API_KEY`,
  `OPENAI_MODEL` (`gpt-5.4-mini`), `OPENAI_BATCH`.

Removed: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_GCS_BUCKET`, `ASR_URL`,
`STT_CHUNK_SECONDS`, `config/pipelines` paths, and `OPENAI_POSTPROCESS` (the
free-text refiner). `SUPPORTED_STT_PROVIDERS` reduces to `("", "deepgram")`.

## Skill

One file: `skills/gdstt-cli/SKILL.md` (no `references/`, no `examples/`). It
carries the adapted `keypoints-transcription` workflow plus the CLI map:

- Headline flow: "сделай расшифровку последнего созвона" → `gdstt latest` (or
  `transcribe`) → reason speakers → **confirm mapping** → `gdstt relabel` →
  write `<base>.keypoints.md` → place in destination.
- The Keypoints template (Задачи `### Ответственный` / Тезисы / Открытые
  вопросы), kept from the reference, minus wikilinks and vault style.
- CLI command table and safety rules (ask before spending credits / overwriting;
  never print secrets).
- One-line pointer to README for Google Drive setup.

Dropped from the reference: `Management/` wikilink table, Obsidian vault style
(длинные тире / ёлочки), off-record redaction, vault folder defaults.

## README

Gains a "Google Drive setup" section (gcloud / ADC / Console OAuth, scopes,
folder-id discovery) migrated from the removed `setup.py`. Documents
`OUTPUT_TARGET` / `OUTPUT_DIR`. Provider matrix collapses to Deepgram.
Daemon/Docker deployment section stays.

## Testing

- Keep and adapt: `tests/test_stt_deepgram*.py`, `tests/test_postprocess.py`,
  `tests/test_extractor.py`, `tests/test_drive.py`, `tests/test_auth.py`,
  `tests/test_config.py`, `tests/test_main.py`, `tests/test_cli.py`,
  `tests/test_notify.py`.
- Reuse `tests/test_openai_pipeline.py` for the new keypoints call (mock OpenAI).
- Remove: tests for deleted modules (google/asr/openai-STT, chunker, pipeline_*,
  setup) and skill-doc assertions tied to removed resources.
- Add: `tests/test_relabel_transcript.py` (default map, exceptions, block-merge,
  unmapped-label warning, verbatim guarantee); `tests/test_output.py` (drive vs
  folder routing); `latest` command + newest-MP4 helper tests.
- `uv run pytest` and `uv run ruff check` must pass after the cut.

## Out of scope

- Renaming Drive files / `refresh-names`.
- Obsidian vault output (wikilinks, `Management/` notes, vault style, redaction).
- OpenAI Agents SDK; any new STT provider.
- Free-text LLM transcript rewriting (replaced by MAP-based relabel).

## Acceptance criteria

1. Only Deepgram remains as STT; google/asr/openai-STT code, config, and tests
   are gone, as are the JSON-intent layer and `setup.py`.
2. CLI matches the table above (incl. new `latest` and `relabel`).
3. `gdstt latest` transcribes the newest recording in a folder end-to-end
   (extract → Deepgram → speaker-named transcript → destination), honoring
   `--dry-run`.
4. `relabel_transcript.py` is in the project, applies a `MAP.json` (default +
   exceptions) keeping utterance text verbatim, and is callable via
   `gdstt relabel`.
5. The skill `SKILL.md` carries the agent keypoints workflow (reason → confirm →
   relabel → keypoints) and produces `<base>.txt` + `<base>.keypoints.md`.
6. `OUTPUT_TARGET` routes artifacts to Drive sibling or `OUTPUT_DIR`.
7. `OPENAI_KEYPOINTS=true` makes the auto path emit a keypoints document.
8. `gdstt speakers set` assigns speaker names used by post-processing.
9. Daemon + Docker + Telegram still function; `STT_PROVIDER=""` keeps MP3-only.
10. `uv run pytest` and `uv run ruff check` pass.
