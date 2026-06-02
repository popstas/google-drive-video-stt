# Single Agent Skill Bundle Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace committed host-specific skill mirrors and duplicated skill references with one installable `skills/gdstt-cli/` bundle, then remove stale repository artifacts.

**Architecture:** `skills/gdstt-cli/` is the only complete skill tree in git. `gh skill install` owns host-specific installation paths for Codex, Claude Code, and other supported hosts. Repository validation checks the canonical bundle and a temporary local install without generating mirrors.

**Tech Stack:** Python 3.11+, pytest, ruff, GitHub CLI `gh skill`

---

### Task 1: Make The One-Bundle Contract Fail First

**Files:**
- Modify: `tests/test_skill_docs.py`

- [x] **Step 1: Replace mirror expectations with a one-bundle assertion**

```python
def test_repository_tracks_one_installable_skill_bundle():
    forbidden_paths = (
        REPO_ROOT / ".agents" / "skills",
        REPO_ROOT / ".claude" / "skills",
        REPO_ROOT / "docs" / "skills",
        REPO_ROOT / "scripts" / "sync-agent-skills.py",
    )

    assert (CANONICAL_SKILL_ROOT / "SKILL.md").exists()
    for path in forbidden_paths:
        assert not path.exists(), f"obsolete duplicate skill surface remains: {path}"
```

- [x] **Step 2: Run the focused test and verify it fails on tracked mirrors**

Run: `uv run pytest tests/test_skill_docs.py::test_repository_tracks_one_installable_skill_bundle -q`

Expected: FAIL because `.agents/skills`, `.claude/skills`, `docs/skills`, and the sync script still exist.

### Task 2: Remove Mirrors And Simplify Validation

**Files:**
- Delete: `.agents/skills/gdstt-cli/`
- Delete: `.claude/skills/gdstt-cli/`
- Delete: `docs/skills/`
- Delete: `scripts/sync-agent-skills.py`
- Modify: `scripts/check-agent-skill.py`
- Modify: `tests/test_skill_docs.py`

- [x] **Step 1: Delete generated and duplicated trees**

Use `apply_patch` deletions for tracked mirror trees, duplicate references, registry metadata, and the obsolete synchronization script.

- [x] **Step 2: Simplify package validation**

Keep canonical package checks, resource routing checks, example checks,
`gh skill publish --dry-run`, and temporary local installation. Remove registry,
mirror, and synchronization checks.

- [x] **Step 3: Run the skill-doc test suite**

Run: `uv run pytest tests/test_skill_docs.py -q`

Expected: PASS.

### Task 3: Update Current Documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `skills/gdstt-cli/SKILL.md`
- Modify: `skills/gdstt-cli/references/provider-extension.md`
- Modify: `skills/gdstt-cli/references/troubleshooting.md`
- Modify: `.gitignore`

- [x] **Step 1: Document one canonical skill bundle**

Replace generated-mirror instructions with `gh skill install` commands for
Codex and Claude Code. Route provider notes and troubleshooting directly to
files bundled under `skills/gdstt-cli/references/`.

- [x] **Step 2: Ignore host installation directories**

Add:

```gitignore
.agents/skills/
.claude/skills/
```

- [x] **Step 3: Run documentation checks**

Run: `uv run pytest tests/test_skill_docs.py -q`

Expected: PASS.

### Task 4: Remove Superseded Artifacts

**Files:**
- Delete: `.claude/settings.json`
- Delete: `docs/plan.md`
- Delete: `docs/TODO.md`
- Delete: `docs/2026-06-01-project-skill-review.md`
- Delete: `docs/changelog-30-05-26/`

- [x] **Step 1: Delete stale files**

Remove the machine-specific Claude setting, superseded prototype plan,
completed queue, migration memo, and PR-review changelog.

- [x] **Step 2: Search for stale references**

Run:

```powershell
rg -n "docs/skills|sync-agent-skills|generated mirror|\.agents/skills|\.claude/skills|docs/TODO|docs/plan\.md|changelog-30-05-26|project-skill-review" -g "!docs/superpowers/**" .
```

Expected: no current documentation or code references remain, except explicit
gitignore entries and tests preventing reintroduction.

### Task 5: Verify The Cleanup

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-single-agent-skill-bundle-cleanup.md`

- [x] **Step 1: Run focused validation**

```powershell
uv run pytest tests/test_skill_docs.py -q
uv run python scripts/check-agent-skill.py
gh skill publish --dry-run
```

- [x] **Step 2: Run repository validation**

```powershell
uv run pytest -q
uv run ruff check
git diff --check
```

- [x] **Step 3: Inspect final repository state**

Run:

```powershell
git status --short
git diff --stat HEAD
```

Expected: one canonical skill tree remains, no secrets are staged, and only
cleanup-related files changed.
