# Agent Skill Package And Install Design

Date: 2026-06-01

## Goal

Refactor the `gdstt-cli` Agent Skill into a compact, publishable package with
progressive disclosure while preserving repo-local discovery and adding
optional installation for multiple agent hosts.

The result must support both:

1. clone-and-use workflows, where compatible agents discover the committed
   workspace skill without a separate installation step
2. optional local or remote user-scoped installation through `gh skill`

## Current State

The current package has one large operator skill:

- `.agents/skills/gdstt-cli/SKILL.md`: 624 lines, 2792 words
- `.agents/skills/gdstt-cli/examples/`: three scenario playbooks
- `.agents/skills/gdstt-cli/references/`: three companion references
- `.claude/skills/gdstt-cli/`: synchronized compatibility mirror
- `docs/skills/registry.json`: metadata registry
- `scripts/check-agent-skill.py`: mirror and reference validator

The current package is usable, but the primary `SKILL.md` duplicates details
that already belong in references or playbooks. It also uses hidden
`.agents/skills` as the portable source. That is convenient for workspace
discovery, but it is not the normal publish layout expected by:

```bash
gh skill publish --dry-run
```

`gh skill install` already supports local and remote repositories, host-specific
installation paths, project or user scope, provenance metadata, update tracking,
and a broad host matrix. A custom package manager is not needed.

## Design Principles

### One Active Operator Skill

Keep one active `gdstt-cli` skill. Auth, inspection, processing, provider
switching, and recovery share the same operator decision tree. Splitting them
into multiple active sibling skills would make discovery and routing less
predictable.

### Progressive Disclosure

The primary `SKILL.md` is a router and safe-workflow guide. It must remain useful
on its own for ordinary operation, but detailed setup, configuration, provider
notes, and recovery material load only when needed.

Target size:

- preferred: 250-350 lines
- hard limit enforced by validation: 400 lines

### One Canonical Publish Bundle

Use a non-hidden publishable bundle as the source of truth:

```text
skills/gdstt-cli/
```

Generate workspace mirrors from that canonical bundle:

```text
.agents/skills/gdstt-cli/
.claude/skills/gdstt-cli/
```

Do not maintain mirrors manually.

### Native Installation Tooling

Prefer `gh skill` instead of a repository-specific installer. It already handles:

- remote GitHub installation
- local checkout installation through `--from-local`
- host-specific placement through `--agent`
- project or user placement through `--scope`
- source tracking metadata
- updates through `gh skill update`
- preview before installation

Manual copying remains a documented fallback only when the installed GitHub CLI
does not provide `gh skill`.

## Package Layout

```text
skills/
  gdstt-cli/
    SKILL.md
    references/
      commands.md
      configuration.md
      provider-notes.md
      troubleshooting.md
      provider-extension.md
    examples/
      drive-only-setup.md
      folder-dry-run-size-guard.md
      google-timeout-recovery.md
      openai-full-pipeline.md

.agents/
  skills/
    gdstt-cli/                 # generated mirror

.claude/
  skills/
    gdstt-cli/                 # generated mirror

docs/
  skills/
    registry.json
    provider-notes.md
    troubleshooting.md
    provider-extension.md

scripts/
  sync-agent-skills.py
  check-agent-skill.py
```

Canonical bundled references may continue to mirror selected maintainers'
documents under `docs/skills/`. The sync script owns copying those canonical docs
into `skills/gdstt-cli/references/`, then copying the complete canonical bundle
into the workspace mirrors.

## Primary Skill Contents

Keep these sections in `skills/gdstt-cli/SKILL.md`:

1. Frontmatter with trigger-focused `name`, `description`, version, and update
   date metadata required by the repository contract.
2. `Start Here`: the smallest safe flow for common tasks.
3. `Command Boundaries`: Drive-only commands versus commands that may spend
   credits.
4. A compact command table covering every CLI subcommand.
5. Mutation and spend confirmation rules.
6. Provider switching invariants.
7. A routing table describing exactly when to open each reference or example.
8. A short notes section for core invariants that are unsafe to omit.

Move these details out of the primary skill:

- full Drive OAuth wizard and PowerShell commands
- long command examples and flag combinations
- complete environment variable catalog
- provider-specific tuning matrices
- Google timeout recovery procedure
- OpenAI full-pipeline procedure
- maintainer-only provider extension workflow

## Reference And Playbook Boundaries

### `references/commands.md`

Use for detailed command syntax, aliases, examples, and flag interactions.

### `references/configuration.md`

Use for the environment-variable catalog grouped by operator scenario:

- Drive-only setup
- common runtime behavior
- Deepgram
- OpenAI
- Google STT
- ASR

### `references/provider-notes.md`

Use for provider selection, switching, tuning, and artifact behavior.

### `references/troubleshooting.md`

Use when normal operation fails: empty transcripts, retries, size mismatch,
invalid `FOLDER_IDS`, runtime summaries, retained GCS blobs, and first recovery
commands.

### `references/provider-extension.md`

Use only for maintainer work that adds or changes STT providers.

### `examples/drive-only-setup.md`

Use for first-time Google Drive OAuth setup. Move the full setup wizard and
mutating-command confirmation checklist here.

### `examples/folder-dry-run-size-guard.md`

Use for folder-wide preview and controlled processing.

### `examples/google-timeout-recovery.md`

Use for Google STT timeout-retained blob recovery.

### `examples/openai-full-pipeline.md`

Use when the operator asks for Drive MP4 to final TXT with OpenAI
post-processing.

## Sync And Validation Workflow

Add:

```bash
uv run python scripts/sync-agent-skills.py --write
uv run python scripts/sync-agent-skills.py --check
```

`--write` must:

1. copy canonical maintainer references from `docs/skills/` into
   `skills/gdstt-cli/references/`
2. copy the complete `skills/gdstt-cli/` package into
   `.agents/skills/gdstt-cli/`
3. copy the same package into `.claude/skills/gdstt-cli/`
4. remove stale mirror files that are no longer present in the canonical bundle
5. stay restricted to the expected skill directories

`--check` must report drift without modifying files.

Update `scripts/check-agent-skill.py` and tests so validation covers:

- canonical bundle existence
- registry path points to `skills/gdstt-cli/SKILL.md`
- mirror equality
- bundled reference equality
- required playbook presence
- direct routing links from the primary `SKILL.md`
- primary `SKILL.md` hard limit of 400 lines
- `gh skill publish --dry-run` success when supported by the installed GitHub CLI
- local install smoke test into a temporary directory:

  ```bash
  gh skill install . gdstt-cli --from-local --dir <temp-dir>
  ```

If the installed GitHub CLI predates `gh skill`, the validator should skip the
GitHub CLI integration checks with a clear message while still running the
repository-native checks.

## Installation UX

### Clone And Use

After cloning the repository, compatible workspace-aware hosts use the committed
mirrors directly:

- shared Agent Skills hosts: `.agents/skills/gdstt-cli`
- Claude-compatible hosts: `.claude/skills/gdstt-cli`

No installation is required for repo-local operation.

### Optional Local Checkout Installation

Install from an existing clone into a user-scoped host directory:

```bash
gh skill install . gdstt-cli --from-local --agent codex --scope user
```

Replace `codex` with another supported `--agent` value.

### Optional Remote Installation

Preview before installing:

```bash
gh skill preview wyrtensi/google-drive-video-stt gdstt-cli
```

Install without cloning:

```bash
gh skill install wyrtensi/google-drive-video-stt gdstt-cli --agent codex --scope user
```

Remote installation becomes a supported user path after the canonical bundle is
available from the repository default branch or a release tag. During review,
validate the package from the checkout with `gh skill publish --dry-run` and the
local `--from-local` smoke test.

Update installed skills:

```bash
gh skill update --all
```

### Supported Hosts

README should list representative hosts and point to:

```bash
gh skill install --help
```

as the current source of truth for the complete matrix. Do not hard-code a claim
that only a small fixed set of editors is supported.

### Versioning

Use GitHub releases for stable skill versions. `gh skill install` resolves an
unpinned skill from the latest tagged release before default-branch HEAD.

Document:

```bash
gh skill install wyrtensi/google-drive-video-stt gdstt-cli@v1.4.0 --agent codex --scope user
```

Keep `docs/skills/registry.json`, skill frontmatter version, release tag, and
`last_updated` aligned when publishing a skill release.

## Documentation Updates

Update:

- `README.md`: clone-and-use path, local install, remote install, preview,
  update, pinning, representative host list, and manual-copy fallback
- `AGENTS.md`: canonical source is `skills/gdstt-cli`, mirrors are generated
- `CLAUDE.md`: remain a thin pointer to `AGENTS.md`
- `docs/skills/registry.json`: canonical package path and version update
- `docs/changelog-30-05-26/changelog.md`: package migration note

## Testing Strategy

### Static Repository Tests

Extend `tests/test_skill_docs.py` to assert:

- command coverage remains complete after compaction
- common safe-flow routing remains in the primary skill
- detailed material exists in references and examples
- canonical package and mirrors are identical
- registry points to the canonical package
- primary `SKILL.md` remains within the 400-line hard limit

### Installer Smoke Tests

Use temporary directories only:

```bash
gh skill publish --dry-run
gh skill install . gdstt-cli --from-local --dir <temp-dir>
```

Verify the installed tree contains `SKILL.md`, references, and examples.

### Forward Tests

Run realistic retrieval scenarios against the compact skill:

1. inspect folder state safely without configuring an STT provider
2. preview folder processing with an optional size guard
3. recover from a Google timeout-retained GCS blob
4. switch from Deepgram to OpenAI STT without rewriting the operator workflow
5. run Drive MP4 to final TXT with OpenAI post-processing
6. locate the provider extension checklist as a maintainer

Success means the agent starts from the compact primary skill and opens only the
reference or playbook needed for the task.

## Migration Sequence

1. Add the canonical `skills/gdstt-cli/` package by copying the current portable
   bundle.
2. Add `sync-agent-skills.py`.
3. Move detailed content out of the primary skill into references and examples.
4. Generate `.agents` and `.claude` mirrors.
5. Update registry, validator, tests, README, AGENTS, and changelog.
6. Run static validation, `gh skill publish --dry-run`, and local install smoke
   test.
7. Run forward-test scenarios.
8. Commit and publish through the existing stacked PR workflow.

## Non-Goals

- Do not create a custom package manager.
- Do not publish a new release tag as part of the migration unless explicitly
  requested.
- Do not split routine operator flows into multiple active skills.
- Do not claim support for hosts outside the current `gh skill install --help`
  matrix.
- Do not remove repo-local mirrors.

## Acceptance Criteria

- `skills/gdstt-cli/SKILL.md` is the canonical publish bundle and stays at or
  below 400 lines.
- `.agents` and `.claude` bundles are generated mirrors, not manually maintained
  sources.
- Repo-local discovery works without installation.
- Local checkout installation works through `gh skill install --from-local`.
- Remote GitHub installation works through `gh skill install`.
- README documents preview, install, update, pinning, representative hosts, and
  fallback behavior.
- Repository tests, linter, sync check, validator, publish dry-run, and install
  smoke test pass.
