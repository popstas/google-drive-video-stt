# Agent Skill Package And Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `gdstt-cli` from `skills/gdstt-cli`, keep repo-local mirrors generated, compact the primary skill, and validate local installation with bundled resources.

**Architecture:** `skills/gdstt-cli` becomes the only authored installable package. `scripts/sync-agent-skills.py` synchronizes canonical maintainer references and generates `.agents` and `.claude` mirrors. The primary `SKILL.md` routes agents to installed `references/` and `examples/` resources through explicit task-oriented conditions.

**Tech Stack:** Python 3.11+, pytest, GitHub CLI `gh skill`, Markdown Agent Skills bundles.

---

### Task 1: Lock The Package Contract With Failing Tests

**Files:**
- Modify: `tests/test_skill_docs.py`

- [x] Add tests asserting the registry points to `skills/gdstt-cli/SKILL.md`, the primary skill stays within 400 lines, canonical and mirror packages match, every installed resource has a direct routing condition, and only one `SKILL.md` exists in the bundle.
- [x] Run `uv run pytest tests/test_skill_docs.py -q`.
- [x] Confirm RED failures reference the missing canonical package and old registry path.

### Task 2: Add Canonical Bundle And Sync Script

**Files:**
- Create: `skills/gdstt-cli/`
- Create: `scripts/sync-agent-skills.py`
- Modify: `scripts/check-agent-skill.py`

- [x] Copy the current portable package into `skills/gdstt-cli`.
- [x] Add `sync-agent-skills.py --write|--check` with fixed allowlisted paths and stale-file cleanup.
- [x] Make `check-agent-skill.py` validate the canonical package, generated mirrors, direct resource routing, a 400-line limit, one discoverable `SKILL.md`, `gh skill publish --dry-run`, and a temporary local install smoke test when `gh skill` exists.
- [x] Run sync write and targeted validator tests.

### Task 3: Compact The Primary Skill

**Files:**
- Modify: `skills/gdstt-cli/SKILL.md`
- Create: `skills/gdstt-cli/references/commands.md`
- Create: `skills/gdstt-cli/references/configuration.md`
- Create: `skills/gdstt-cli/examples/openai-full-pipeline.md`

- [x] Keep safe start flow, boundaries, compact command table, mutation rules, provider invariants, resource routing table, and core notes in the primary skill.
- [x] Move detailed command syntax into `references/commands.md`.
- [x] Move environment catalog into `references/configuration.md`.
- [x] Move OpenAI full-pipeline steps into `examples/openai-full-pipeline.md`.
- [x] Ensure every resource has a direct task-oriented routing line in the primary skill.
- [x] Run sync write and confirm primary skill is at most 400 lines.

### Task 4: Update Shared Contracts And Docs

**Files:**
- Modify: `docs/skills/registry.json`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/changelog-30-05-26/changelog.md`
- Modify: `tests/test_skill_docs.py`

- [x] Set registry canonical path to `skills/gdstt-cli/SKILL.md` and bump version to `1.4.0`.
- [x] Document canonical package and generated mirrors in `AGENTS.md`.
- [x] Prefer `gh skill preview`, local `--from-local`, remote install, `gh skill update --all`, and pinning in README; keep manual copying as fallback.
- [x] Add package migration changelog entry.
- [x] Update tests to match the compact routing contract.

### Task 5: Verify And Publish

**Files:**
- Verify all changed files.

- [x] Run `uv run python scripts/sync-agent-skills.py --check`.
- [x] Run `uv run python scripts/check-agent-skill.py`.
- [x] Run `gh skill publish --dry-run`.
- [x] Run a temporary local install smoke test and verify one `SKILL.md`, references, and examples.
- [x] Run `uv run pytest`.
- [x] Run `uv run ruff check`.
- [x] Run `git diff --check`.
- [x] Commit from `wyrtensi`, push a stacked branch, open and merge a PR into `codex/pr-4-review`, and update `popstas/google-drive-video-stt#5`.
