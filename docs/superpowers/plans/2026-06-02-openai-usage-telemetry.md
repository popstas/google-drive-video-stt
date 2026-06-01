# OpenAI Usage Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return OpenAI refinement token counts in agent execution JSON without requiring an admin key or changing transcript output.

**Architecture:** Normalize Responses API usage inside `src/openai_pipeline.py`, expose it through an optional collector, and attach it to existing process telemetry. Aggregate token counters in `src/pipeline_executor.py` for one target or a folder.

**Tech Stack:** Python 3.11+, OpenAI Responses API, pytest, ruff

---

### Task 1: Normalize OpenAI Response Usage

**Files:**
- Modify: `src/openai_pipeline.py`
- Test: `tests/test_openai_pipeline.py`

- [ ] Add failing tests for sync response usage, batch body usage, and absent usage.
- [ ] Run `uv run pytest tests/test_openai_pipeline.py -q` and confirm failures.
- [ ] Add a normalized usage helper and `OpenAIPipeline.last_usage`.
- [ ] Capture usage from synchronous response objects and batch JSON bodies.
- [ ] Run `uv run pytest tests/test_openai_pipeline.py -q`.

### Task 2: Forward Usage Through Runtime Telemetry

**Files:**
- Modify: `src/openai_pipeline.py`
- Modify: `src/main.py`
- Modify: `src/pipeline_executor.py`
- Test: `tests/test_openai_pipeline.py`
- Test: `tests/test_main.py`
- Test: `tests/test_pipeline_executor.py`

- [ ] Add failing tests for the optional `refine_transcript(..., usage=collector)` API and executor aggregation.
- [ ] Run focused tests and confirm failures.
- [ ] Copy normalized usage into the optional collector.
- [ ] Add usage to `_ProcessTelemetry` and pass the collector from `process_item()`.
- [ ] Add `usage.openai` aggregation to execution JSON.
- [ ] Run focused tests.

### Task 3: Document And Verify

**Files:**
- Modify: `README.md`
- Modify: `skills/gdstt-cli/SKILL.md`
- Modify: `skills/gdstt-cli/references/commands.md`
- Refresh: `.agents/skills/gdstt-cli/`
- Refresh: `.claude/skills/gdstt-cli/`

- [ ] Document best-effort OpenAI token telemetry and unchanged `cost_usd.openai: null`.
- [ ] Run `uv run python scripts/sync-agent-skills.py --write`.
- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run ruff check`.
- [ ] Run `uv run python scripts/sync-agent-skills.py --check`.
- [ ] Run `uv run python scripts/check-agent-skill.py`.
- [ ] Run `git diff --check`.
- [ ] Reinstall with `uv tool install --force --editable .`.
- [ ] Reinstall skill with `gh skill install . gdstt-cli --from-local --agent codex --scope user --force`.
