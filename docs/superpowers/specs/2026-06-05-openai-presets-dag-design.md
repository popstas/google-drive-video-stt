# Config-defined OpenAI preset DAG

Date: 2026-06-05

## Problem

Today the OpenAI step is a single hardcoded pass. After the transcript is written,
`process_item` optionally calls `openai_pipeline.generate_keypoints` with one fixed
`INSTRUCTIONS` prompt and writes a single `<base>.keypoints.md`. There is no way to:

- define more than one LLM processing output (e.g. a cleanup pass plus several
  summaries),
- add custom processing without editing code,
- express that some outputs depend on others (a cleanup feeding two summaries),
- run independent outputs in parallel.

All configuration is env-driven through a frozen `Config` built from `.env`.

## Goal

Turn the single OpenAI pass into a **DAG of named presets** defined in
`data/config.yml`. Each preset is one LLM pass with its own instructions. A preset
feeds on the concatenated outputs of its dependency presets, or on the raw
transcript when it has no dependencies, and writes its own sibling artifact.
Independent presets run in parallel. `data/config.yml` becomes the single source of
truth; `.env` is auto-migrated into it on first run and then ignored.

Canonical example chain: `transcript-cleanup -> keypoints + expertizeme-managers`
(cleanup reads the transcript; keypoints and managers each read cleanup's output and
run concurrently).

## Decisions (settled during brainstorming)

- **Config home:** full migration to `data/config.yml`, secrets included, plaintext
  (the `./data` dir is already gitignored). `.env` is dropped as a runtime source.
- **Migration UX:** when `data/config.yml` is missing or empty, generate it from the
  existing `.env`/environment, write it, then load it. No manual step required for
  the common upgrade. `gdstt config migrate [--force]` is the explicit/regenerate
  form.
- **Preset I/O:** input = concatenation of the preset's dependency outputs; a preset
  with no dependencies reads the raw transcript. Every preset writes a sibling
  artifact `<base><artifact_suffix>`.
- **Built-ins:** code ships built-in preset definitions (at minimum `keypoints` with
  today's `INSTRUCTIONS` and `.keypoints.md` suffix). Config presets merge over
  built-ins: a preset named in config overrides the built-in of the same name
  field-by-field, and `enabled: false` disables a built-in.
- **Execution:** run the DAG with a `ThreadPoolExecutor` in topological waves;
  independent presets run concurrently once their dependencies finish. Model and
  batch mode are per-preset settings that fall back to global OpenAI defaults.

## Architecture

### 1. Config layer (`src/config.py` rewrite)

- `load_config(*, validate_providers=True)` reads `data/config.yml` instead of `.env`.
  Config-file path resolution: default `<data_dir>/config.yml` (default data dir
  `./data`), overridable by a `--config PATH` CLI flag or `GDSTT_CONFIG` env var.
  These are bootstrap pointers to the file, not application settings.
- **Auto-migration:** if the resolved config file is absent or empty, and an `.env`
  or environment configuration is present, build the config from env (reusing the
  current parsing/validation), serialize it to YAML, write `data/config.yml`, and
  continue loading from the in-memory values. If neither config.yml nor env yields a
  usable configuration, raise as today.
- YAML shape (illustrative):

  ```yaml
  folder_ids: [abc, def]
  poll_interval: 600
  bitrate: 96k
  data_dir: data
  proxy_url: ""
  output:
    target: drive          # drive | folder
    dir: null              # required when target=folder
  stt:
    provider: deepgram     # "" / disabled => MP3-only
    language: ru
    postprocess: true
    drive_mp3_artifact: true
    deepgram:
      api_key: "..."
      model: nova-3
      diarize_model: latest
      audio_source: m4a_copy
      txt_formatter: word_speaker
      keyterms_enabled: true
      keyterms_file: config/deepgram-keyterms.txt
  openai:
    api_key: "..."
    model: gpt-5.4-mini    # global default
    batch: false           # global default
    max_parallel: 4
  presets:
    transcript-cleanup:
      instructions: "..."
    keypoints:
      depends_on: [transcript-cleanup]   # overrides built-in keypoints
    expertizeme-managers:
      depends_on: [transcript-cleanup]
      instructions: "..."
  ```

- `Config` stays a frozen dataclass. Nested YAML maps onto the existing flat fields
  (or lightly grouped sub-dataclasses); add `presets: tuple[Preset, ...]` and
  `openai_max_parallel: int`. The `OPENAI_KEYPOINTS` boolean gate is replaced by
  "are there any enabled presets".
- Same validation rules (raise on misconfiguration). The `python-dotenv` dependency
  and `os.environ` config reads are removed (env is still read once, during
  auto-migration, by the migration path).

### 2. Preset model (`src/presets.py`, new)

- `@dataclass(frozen=True) class Preset`:
  - `name: str`
  - `instructions: str`
  - `depends_on: tuple[str, ...] = ()`
  - `model: str | None = None`            # falls back to `openai.model`
  - `batch: bool | None = None`           # falls back to `openai.batch`
  - `artifact_suffix: str = ".<name>.md"` # default derived from name
  - `enabled: bool = True`
- `BUILTIN_PRESETS`: registry of code-shipped presets. `keypoints` carries today's
  `INSTRUCTIONS` and `.keypoints.md` suffix.
- `merge_presets(builtins, config_presets) -> dict[str, Preset]`: start from
  built-ins, apply per-field overrides and additions from config; drop presets with
  `enabled: false`.
- `validate_dag(presets)`: every `depends_on` target exists and is enabled; no
  cycles. Raise on violation (consistent with config's raise-on-misconfig invariant).

### 3. DAG executor (`src/preset_pipeline.py`, new)

- Generalize `OpenAIPipeline.generate_keypoints` into a generic
  `run(instructions, input_text) -> (text, usage)` (sync and batch paths take
  `instructions` as a parameter instead of the module-level constant). Keep a thin
  `generate_keypoints` wrapper so existing imports/tests still work.
- `run_presets(transcript, file_name, config, presets, *, speaker_names,
  only=None) -> dict[str, PresetResult]`:
  - Execute the DAG with a `ThreadPoolExecutor` capped at `openai.max_parallel`.
  - A preset's input is the concatenation of its dependency outputs, each prefixed
    with a labeled separator (e.g. `### <dep-name>`); a preset with no dependencies
    receives the raw transcript.
  - Each preset uses its own model/batch (falling back to global defaults).
  - `only` restricts execution to a subset (used by idempotency to run only missing
    presets, and by a future `--preset` flag).
  - Returns `{name: PresetResult(text, usage)}`; per-preset usage flows into the
    process summary's `usage` dict.

### 4. Wiring (`src/main.py`)

- Replace the `if config.openai_keypoints:` block: compute the set of enabled presets
  still missing an artifact for this file, call `run_presets(..., only=missing)`, and
  for each preset with non-empty output write `<base><artifact_suffix>` tagged
  `artifact_type=<preset-name>`.
- `_save_and_upload_keypoints` generalizes to `_save_and_upload_preset(service,
  file_id, file_name, text, folder_id, tmp_dir, config, *, artifact_type, suffix,
  existing_id)`.

### 5. Idempotency / state (`src/drive.py`)

- `list_folder_state` returns `artifact_ids: dict[str, str]` (preset name ->
  Drive file id), keyed by the `artifact_type` appProperty, instead of the single
  `keypoints_id`. Existing keypoints files already carry `artifact_type=keypoints`,
  so they map onto the `keypoints` preset with no migration.
- Per-file "needs" for the OpenAI stage = enabled presets whose `artifact_type` is
  absent from `artifact_ids`. The transcript (`needs_txt`) gating is unchanged.

### 6. Error handling

- Each successful preset's artifact is written as soon as it completes.
- If a preset fails, its dependents are skipped (their inputs are unavailable), but
  independent branches still complete and persist their artifacts.
- After the stage, if any preset failed, raise an aggregated error so the file is
  retried on a later cycle. Because successful artifacts are already written and
  tracked by `artifact_type`, the retry re-runs only the still-missing presets.
- This preserves the current tiered model: the error is logged + sent to Telegram via
  `notify.notify_error`, and the polling loop continues.

### 7. CLI / docs / tests

- New `gdstt config migrate [--force]`: write `data/config.yml` from the current
  `.env`/environment, seeding a `presets` block from the built-ins. `--force`
  overwrites an existing file.
- Global `--config PATH` flag on the parser; `GDSTT_CONFIG` env var for the same.
- `gdstt doctor` reports the resolved config path and prints the resolved preset DAG
  (names, dependencies, enabled state).
- Docs to update: `AGENTS.md` (config posture, presets, idempotency note),
  `README.md` (config.yml setup replacing `.env`), `skills/gdstt-cli/SKILL.md`
  (operator workflow + presets), and `tests/test_skill_docs.py` invariants.

## Testing

Unit (mocked, no network, in default `uv run pytest`):

- `config`: load a YAML fixture; auto-generate config.yml from a fake env when the
  file is missing/empty; validation errors raise.
- `presets`: built-in/config merge (override fields, add preset, disable built-in);
  DAG validation accepts a valid graph and rejects missing-dep and cyclic graphs;
  default `artifact_suffix` derivation.
- `preset_pipeline`: topological execution order; independent presets dispatched
  concurrently; dependency outputs concatenated into a dependent's input; per-preset
  model/batch fallback; `only` subset; partial-failure aggregation. OpenAI client is
  mocked.
- `drive`: `list_folder_state` returns `artifact_ids` keyed by `artifact_type`;
  existing keypoints files map to the `keypoints` preset.
- `cli`: `config migrate` writes the expected YAML; `--config`/`GDSTT_CONFIG`
  resolution.
- `main`: wiring writes one artifact per produced preset with the right
  `artifact_type`; already-present presets are skipped.

End-to-end (network + OpenAI/Deepgram credits; marked, excluded from default
`uv run pytest`):

- Target video: Drive id `18czgPfHG3SWy8B8xCuKHBtCYqrME0sJC`
  ("Oksana and Andrei Smirnov", ~5.7 min, ~7.8 MB) — a short two-named-speaker
  recording that exercises speaker-named presets cheaply.
- Procedure: with `output.target=folder` pointing at a local temp dir (no Drive
  writes), run `gdstt process 18czgPfHG3SWy8B8xCuKHBtCYqrME0sJC`. Assert: the `.txt`
  transcript is produced; each enabled preset wrote `<base><artifact_suffix>`;
  `transcript-cleanup`'s output is non-empty and both `keypoints` and
  `expertizeme-managers` artifacts exist (the parallel branches both ran).

## Suggested phasing (for the implementation plan)

1. `config.yml` load + auto-migration + `gdstt config migrate` (replace `.env`).
2. Preset model: `Preset`, built-ins, merge, DAG validation.
3. DAG executor: generalize the OpenAI pipeline, `run_presets` with the thread pool.
4. `main.py` wiring + multi-artifact idempotency in `drive.list_folder_state`.
5. CLI (`--config`, `doctor` DAG view), docs (AGENTS/README/SKILL), tests incl. the
   marked e2e.

## Out of scope (v1)

- A `--preset NAME` selector on `process`/`latest` (the executor supports `only=`, so
  this is a thin follow-up).
- Env-var interpolation / file-ref secrets inside config.yml (secrets are plaintext).
- Streaming or token-budget-aware preset scheduling.
