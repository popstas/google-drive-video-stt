# Agent Runtime Policy Implementation Plan

## Objective

Add a declarative JSON profile and deterministic intent planner so agents can
process Drive videos through the common Deepgram -> OpenAI -> final TXT pipeline
without rebuilding low-level media decisions on each request.

## Task 1: Profile Loader

Files:

- Add `config/pipelines/default.json`
- Modify `.gitignore`
- Add `src/pipeline_profile.py`
- Add `tests/test_pipeline_profile.py`

Steps:

- [ ] Write failing tests for default loading, recursive local merge, invalid
  schema, required secret status, and profile application to a copied `Config`.
- [ ] Add the default profile and local override ignore rule.
- [ ] Implement strict profile parsing and recursive merge.
- [ ] Implement secret preflight and `Config` application.
- [ ] Run focused tests.

## Task 2: Intent Planner

Files:

- Add `src/pipeline_policy.py`
- Add `tests/test_pipeline_policy.py`

Steps:

- [ ] Write failing tests for minimal intent parsing, unknown fields, default
  steps, missing-key output, and confirmation policy.
- [ ] Implement strict intent parsing.
- [ ] Implement deterministic plan expansion and JSON serialization.
- [ ] Run focused tests.

## Task 3: Executor and CLI JSON Surface

Files:

- Add `src/pipeline_executor.py`
- Modify `src/cli.py`
- Add `tests/test_pipeline_executor.py`
- Modify `tests/test_cli.py`

Steps:

- [ ] Write failing tests for execution preflight, confirmation blocking,
  speaker metadata routing, process dispatch, and CLI JSON dispatch.
- [ ] Implement executor reuse of `src.main.process_target()`.
- [ ] Add `gdstt plan --json` and `gdstt execute --json [--confirm]`.
- [ ] Run focused tests.

## Task 4: Setup and Doctor Integration

Files:

- Modify `src/setup.py`
- Modify `src/cli.py`
- Modify `tests/test_setup.py`
- Modify `tests/test_cli.py`

Steps:

- [ ] Write failing tests for profile-aware OpenAI secret prompting and doctor
  boolean secret readiness.
- [ ] Ask only for secrets required by the active profile, using hidden input.
- [ ] Report secret readiness without values.
- [ ] Run focused tests.

## Task 5: Agent Skill and Documentation

Files:

- Modify `README.md`
- Modify `AGENTS.md`
- Modify `skills/gdstt-cli/SKILL.md`
- Modify `skills/gdstt-cli/references/commands.md`
- Modify `skills/gdstt-cli/references/configuration.md`
- Modify `skills/gdstt-cli/examples/openai-full-pipeline.md`
- Modify `docs/skills/registry.json`
- Modify `tests/test_skill_docs.py`
- Refresh generated `.agents` and `.claude` mirrors

Steps:

- [ ] Write failing documentation parity assertions for JSON plan/execute and
  profile behavior.
- [ ] Document the high-level agent flow and secret policy.
- [ ] Refresh generated mirrors.
- [ ] Run skill docs tests and package validator.

## Task 6: Verification

Steps:

- [ ] Run focused new tests.
- [ ] Run `uv run pytest`.
- [ ] Run `uv run ruff check`.
- [ ] Run `uv run python scripts/sync-agent-skills.py --check`.
- [ ] Run `uv run python scripts/check-agent-skill.py`.
- [ ] Run `git diff --check`.
- [ ] Review the final diff for secret leakage and unrelated churn.
