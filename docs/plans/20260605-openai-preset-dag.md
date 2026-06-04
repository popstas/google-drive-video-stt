# Config-defined OpenAI Preset DAG

## Overview

The OpenAI post-processing step today is a single hardcoded pass: after the
transcript is written, `process_item` optionally runs one fixed-prompt keypoints
generation and writes a single `<base>.keypoints.md`. This makes it impossible to
define multiple LLM outputs, add custom processing without editing code, express
that one output depends on another, or run independent outputs in parallel.

This plan turns that single pass into a **DAG of named presets** defined in
`data/config.yml`. Each preset is one LLM pass with its own instructions; it feeds
on the concatenated outputs of its dependency presets (or the raw transcript when it
has none) and writes its own sibling artifact. Independent presets run in parallel.
`data/config.yml` becomes the single source of truth and replaces `.env`, which is
auto-migrated into YAML on first run. The canonical example chain is
`transcript-cleanup -> keypoints + expertizeme-managers`.

## Context

- Adopted from design spec
  `docs/superpowers/specs/2026-06-05-openai-presets-dag-design.md`.
- Impacted modules: `src/config.py` (env -> YAML rewrite + auto-migration),
  `src/openai_pipeline.py` (generalize the LLM call), new `src/presets.py`,
  new `src/preset_pipeline.py` (DAG executor), `src/main.py` (wiring),
  `src/drive.py` (multi-artifact idempotency), `src/cli.py` (config command,
  `--config`, doctor DAG view).
- Constraints: `Config` stays a frozen dataclass and raises on misconfiguration;
  idempotency relies on the Drive `artifact_type` appProperty plus sibling-stem
  matching; existing `.keypoints.md` files carry `artifact_type=keypoints` and must
  map onto the `keypoints` preset with no migration; secrets are plaintext in the
  already-gitignored `./data`; tests mock all external services with no network.
- Breaking change: `.env` is dropped as a runtime source (auto-migrated to YAML).
- Operator-facing changes must be reflected in `AGENTS.md`, `README.md`,
  `skills/gdstt-cli/SKILL.md`, and `tests/test_skill_docs.py`.

## Development Approach

- Testing approach: regular
- Complete each task fully before moving to the next
- Update this plan when scope changes during implementation

## Testing Strategy

- Unit tests required for every code-changing Task; mock Drive, OpenAI, Deepgram,
  and ffmpeg (no network in the default suite).
- Run the project test suite (`uv run pytest`) after each Task before proceeding.
- One marked, network/credit end-to-end test is added but excluded from the default
  `uv run pytest` run.

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Update plan if implementation deviates from original scope

## Technical Details

**Config layer (`src/config.py`).** `load_config(*, validate_providers=True)` reads
`data/config.yml`. Config-file path resolves to `<data_dir>/config.yml` (default data
dir `./data`), overridable by a `--config PATH` flag or `GDSTT_CONFIG` env var (path
bootstrap only, not a setting). Auto-migration: if the resolved file is absent or
empty and an `.env`/environment configuration is present, build config from env
(reusing current parsing/validation), serialize to YAML, write `data/config.yml`,
then continue from the in-memory values. YAML groups settings under `output`, `stt`
(with nested `deepgram`), and `openai`, plus a top-level `presets` map. The
`OPENAI_KEYPOINTS` gate is replaced by "are there any enabled presets". The
`python-dotenv` dependency and `os.environ` config reads are removed from the normal
load path (env is read only during auto-migration). Same validation rules; raise on
misconfiguration.

**Preset model (`src/presets.py`).** Frozen `Preset` dataclass: `name`,
`instructions`, `depends_on: tuple[str, ...] = ()`, `model: str | None = None`
(falls back to `openai.model`), `batch: bool | None = None` (falls back to
`openai.batch`), `artifact_suffix: str` (default `.<name>.md`), `enabled: bool =
True`. `BUILTIN_PRESETS` ships at least `keypoints` carrying today's `INSTRUCTIONS`
and `.keypoints.md` suffix. `merge_presets(builtins, config_presets)` overrides
built-ins field-by-field, adds new presets, and drops `enabled: false` ones.
`validate_dag(presets)` checks every dependency exists and is enabled and that there
are no cycles; raises on violation.

**DAG executor (`src/preset_pipeline.py`).** Generalize
`OpenAIPipeline.generate_keypoints` into `run(instructions, input_text) -> (text,
usage)` (sync and batch paths take `instructions` as a parameter); keep a thin
`generate_keypoints` wrapper for compatibility. `run_presets(transcript, file_name,
config, presets, *, speaker_names, only=None) -> dict[str, PresetResult]` executes
the DAG with a `ThreadPoolExecutor` capped at `openai.max_parallel`. A preset's input
is its dependency outputs concatenated with a labeled separator per dependency, or
the raw transcript when it has no dependencies. Each preset uses its own model/batch
(falling back to global defaults). `only` restricts execution to a subset. Returns
per-preset text + usage.

**Wiring + idempotency (`src/main.py`, `src/drive.py`).** Replace the
`if config.openai_keypoints:` block: compute enabled presets still missing an
artifact, call `run_presets(..., only=missing)`, and for each non-empty output write
`<base><artifact_suffix>` tagged `artifact_type=<preset-name>`.
`_save_and_upload_keypoints` generalizes to `_save_and_upload_preset(...)`.
`drive.list_folder_state` returns `artifact_ids: dict[str, str]` (preset name ->
Drive file id) keyed by the `artifact_type` appProperty instead of a single
`keypoints_id`; per-file OpenAI-stage "needs" = enabled presets absent from
`artifact_ids`.

**Error handling.** Write each successful preset artifact as soon as it completes; if
a preset fails, skip its dependents but let independent branches finish and persist.
After the stage, if any preset failed, raise an aggregated error so the file retries
on a later cycle and re-runs only the still-missing presets. Preserve the current
tiered behavior (log + Telegram `notify.notify_error`, loop continues).

**End-to-end target.** Drive id `18czgPfHG3SWy8B8xCuKHBtCYqrME0sJC`
("Oksana and Andrei Smirnov", ~5.7 min, ~7.8 MB) — a short two-named-speaker
recording that exercises speaker-named presets cheaply.

## Implementation Steps

### Task 1: config.yml load, auto-migration, and config migrate command

- [ ] Rewrite `load_config()` to read settings from `data/config.yml` with the
      grouped schema (`output`, `stt.deepgram`, `openai`, `presets`), mapping onto
      the frozen `Config` dataclass
- [ ] Resolve the config-file path from `<data_dir>/config.yml`, a `--config PATH`
      flag, and a `GDSTT_CONFIG` env var (path bootstrap only)
- [ ] Implement auto-migration: when `config.yml` is missing or empty and `.env`/env
      is present, build config from env, serialize to YAML, write `data/config.yml`,
      then load from the in-memory values
- [ ] Add a `gdstt config migrate [--force]` command that writes `data/config.yml`
      from the current `.env`/environment and seeds a `presets` block from built-ins
- [ ] Preserve all current validation rules (raise on misconfiguration) and remove
      `python-dotenv`/`os.environ` reads from the normal load path
- [ ] write tests for YAML loading, auto-migration (missing/empty file), validation
      errors, and the `config migrate` command
- [ ] run project tests - must pass before next task

### Task 2: Preset model, built-ins, merge, and DAG validation

- [ ] Add `src/presets.py` with a frozen `Preset` dataclass (name, instructions,
      depends_on, model, batch, artifact_suffix, enabled) and default suffix
      derivation
- [ ] Define `BUILTIN_PRESETS` shipping at least `keypoints` with today's
      `INSTRUCTIONS` and `.keypoints.md` suffix
- [ ] Implement `merge_presets()` so config presets override built-ins field-by-field,
      add new presets, and disable built-ins via `enabled: false`
- [ ] Implement `validate_dag()` to verify dependencies exist and are enabled and
      that the graph has no cycles, raising on violation
- [ ] Parse the YAML `presets` map into `Config.presets` and wire merge + validation
      into `load_config()`
- [ ] write tests for merge (override/add/disable), DAG validation (valid, missing
      dependency, cycle), and suffix derivation
- [ ] run project tests - must pass before next task

### Task 3: DAG executor over the OpenAI pipeline

- [ ] Generalize `OpenAIPipeline` to a `run(instructions, input_text) -> (text,
      usage)` method (sync and batch paths take `instructions` as a parameter)
- [ ] Keep a thin `generate_keypoints` wrapper for compatibility with existing
      callers and tests
- [ ] Add `src/preset_pipeline.py` with `run_presets(...)` executing the DAG via a
      `ThreadPoolExecutor` capped at `openai.max_parallel`, dispatching independent
      presets concurrently once dependencies finish
- [ ] Build each preset's input from its dependency outputs (labeled separator per
      dependency) or the raw transcript when it has no dependencies, honoring
      per-preset model/batch fallback and an `only` subset
- [ ] Aggregate partial failures: persist successful results, skip a failed preset's
      dependents, and surface a combined error after the stage
- [ ] write tests for topological order, concurrent dispatch of independent presets,
      input concatenation, model/batch fallback, `only`, and partial-failure
      aggregation (OpenAI client mocked)
- [ ] run project tests - must pass before next task

### Task 4: main.py wiring and multi-artifact idempotency

- [ ] Replace the single `openai_keypoints` block in `process_item` with the
      preset-DAG run, writing one artifact per produced preset tagged
      `artifact_type=<preset-name>`
- [ ] Generalize `_save_and_upload_keypoints` into `_save_and_upload_preset(...)`
      and route per-preset usage into the process summary
- [ ] Change `drive.list_folder_state` to return `artifact_ids` keyed by the
      `artifact_type` appProperty, keeping existing `.keypoints.md` files mapped to
      the `keypoints` preset
- [ ] Compute per-file OpenAI-stage "needs" as enabled presets absent from
      `artifact_ids` and pass them to `run_presets(..., only=...)`
- [ ] write tests for wiring (one artifact per produced preset, correct
      artifact_type, skip already-present presets) and the new `list_folder_state`
      shape
- [ ] run project tests - must pass before next task

### Task 5: CLI, doctor DAG view, docs, and end-to-end test

- [ ] Add the global `--config PATH` flag and `GDSTT_CONFIG` support to the CLI
      parser and make `gdstt doctor` report the resolved config path and the resolved
      preset DAG (names, dependencies, enabled state)
- [ ] Update `AGENTS.md` (config posture, presets, idempotency note), `README.md`
      (config.yml setup replacing `.env`), and `skills/gdstt-cli/SKILL.md` (operator
      workflow + presets)
- [ ] Update `tests/test_skill_docs.py` invariants to match the new operator behavior
- [ ] Add a marked end-to-end test (excluded from the default `uv run pytest`) that
      runs `gdstt process 18czgPfHG3SWy8B8xCuKHBtCYqrME0sJC` with `output.target=folder`
      into a temp dir and asserts the `.txt` plus each enabled preset artifact exist,
      with `transcript-cleanup` feeding both `keypoints` and `expertizeme-managers`
- [ ] write tests for the `config migrate` CLI output and `--config`/`GDSTT_CONFIG`
      resolution if not already covered
- [ ] run project tests - must pass before next task

### Task 6: Verify acceptance criteria

- [ ] verify all requirements from Overview are implemented (config.yml replaces
      `.env` with auto-migration, presets defined in config, dependency DAG executed
      in parallel, per-preset sibling artifacts, built-ins overridable)
- [ ] run full project test suite (`uv run pytest`)
- [ ] run project linter (`uv run ruff check`) - all issues must be fixed

## Post-Completion

*Items requiring manual intervention - no checkboxes, informational only*

- Run the marked end-to-end test manually against Drive id
  `18czgPfHG3SWy8B8xCuKHBtCYqrME0sJC` once, with valid OpenAI/Deepgram credentials,
  to confirm real transcription + parallel preset artifacts (spends credits).
- On existing deployments, confirm first run auto-generates `data/config.yml` from
  the current `.env` and that subsequent runs read only the YAML.
- Consider a follow-up `--preset NAME` selector on `process`/`latest` (the executor
  already supports `only=`).
