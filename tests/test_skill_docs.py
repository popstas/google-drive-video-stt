from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from src import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ID = "gdstt-cli"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"
README_PATH = REPO_ROOT / "README.md"
CANONICAL_SKILL_ROOT = REPO_ROOT / "skills" / SKILL_ID
SKILL_PATH = CANONICAL_SKILL_ROOT / "SKILL.md"


def _registered_commands() -> set[str]:
    """All subcommand names (including aliases) registered on the CLI parser."""
    parser = cli.build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(subparsers_action.choices.keys())


def _documented_command_entries(text: str) -> list[tuple[str, int]]:
    r"""Command names documented in ``###`` headers, with line numbers."""
    registered = _registered_commands()
    entries: list[tuple[str, int]] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("### "):
            continue
        for span in re.findall(r"`([^`]+)`", line):
            matches = [
                command
                for command in registered
                if span == command or span.startswith(command + " ")
            ]
            assert len(matches) <= 1, (
                f"ambiguous documented command span {span!r} on line {line_number}"
            )
            if matches:
                entries.append((matches[0], line_number))

    return entries


def _skill_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "skill must start with YAML frontmatter"

    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        assert sep, f"invalid frontmatter line: {line!r}"
        data[key.strip()] = value.strip().strip('"')
    return data


def test_skill_file_exists_with_valid_frontmatter():
    assert SKILL_PATH.exists(), f"missing skill file: {SKILL_PATH}"

    text = SKILL_PATH.read_text(encoding="utf-8")
    frontmatter = _skill_frontmatter(text)

    assert frontmatter["name"] == SKILL_ID
    assert frontmatter["description"]
    assert frontmatter["description"].strip(), "skill description must be non-empty"
    assert re.fullmatch(r"\d+\.\d+\.\d+", frontmatter["version"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", frontmatter["last_updated"])


def test_skill_is_a_single_compact_file():
    """The skill collapsed to one SKILL.md with no references/ or examples/."""
    skill_files = list(CANONICAL_SKILL_ROOT.rglob("SKILL.md"))
    assert skill_files == [SKILL_PATH]
    assert len(SKILL_PATH.read_text(encoding="utf-8").splitlines()) <= 400

    assert not (CANONICAL_SKILL_ROOT / "references").exists()
    assert not (CANONICAL_SKILL_ROOT / "examples").exists()

    other_files = [
        path
        for path in CANONICAL_SKILL_ROOT.rglob("*")
        if path.is_file() and path != SKILL_PATH
    ]
    assert other_files == [], f"unexpected bundled resources remain: {other_files}"


def _git_tracked_paths() -> set[str]:
    """Repo-relative posix paths of files tracked by git."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {entry for entry in result.stdout.split("\0") if entry}


def test_repository_tracks_one_installable_skill_bundle():
    # Guard the layering policy against *committed* duplicates only. A locally
    # installed, gitignored ``.claude/skills`` (an operator's own skill install)
    # must not fail this — so check git-tracked paths, not the raw filesystem.
    forbidden_prefixes = (
        ".agents/skills",
        ".claude/skills",
        "docs/skills",
    )
    forbidden_files = ("scripts/sync-agent-skills.py",)

    assert SKILL_PATH.exists()

    tracked = _git_tracked_paths()
    offenders = sorted(
        path
        for path in tracked
        if path in forbidden_files
        or any(path == prefix or path.startswith(prefix + "/") for prefix in forbidden_prefixes)
    )
    assert not offenders, f"obsolete duplicate skill surface tracked in git: {offenders}"


def test_documented_commands_match_registered_subcommands():
    text = SKILL_PATH.read_text(encoding="utf-8")
    entries = _documented_command_entries(text)
    registered = _registered_commands()
    documented = {command for command, _ in entries}

    duplicates: dict[str, list[int]] = {}
    for command, line_number in entries:
        duplicates.setdefault(command, []).append(line_number)
    duplicates = {
        command: line_numbers
        for command, line_numbers in duplicates.items()
        if len(line_numbers) > 1
    }

    missing = registered - documented
    extra = documented - registered
    assert not duplicates, f"skill documents commands multiple times: {duplicates}"
    assert not missing, f"CLI commands not documented in the skill: {sorted(missing)}"
    assert not extra, f"skill documents commands that are not registered: {sorted(extra)}"


def test_skill_documents_keypoints_workflow():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Рабочий процесс Keypoints (агент)" in text
    assert "## Задачи" in text
    assert "## Тезисы" in text
    assert "## Открытые вопросы" in text
    assert "### Ответственный" in text
    # README carries Google Drive setup, not the skill.
    assert "README.md" in text
    # The default Keypoints template stays plain (the responsible heading is a
    # plain name, not a wikilink); vault wikilinks are an opt-in layer only.
    assert "### [[" not in text.split("## Vault")[0]


def test_skill_vault_integration_is_optional_and_impersonal():
    """Vault integration is documented as a fill-in-the-blanks optional layer and
    must never ship real names or vault paths."""
    text = SKILL_PATH.read_text(encoding="utf-8")

    # Documented as an optional layer with placeholder wikilinks to fill in.
    assert "Vault" in text
    assert "[[<" in text, "vault section must use placeholder wikilinks like [[<Имя Фамилия>]]"

    # No personal data may ship in the public skill: every wikilink must be a
    # fill-in placeholder ([[<...>]]), never a concrete real name. This guard
    # names nobody - it just asserts the placeholder shape.
    wikilinks = re.findall(r"\[\[(.+?)\]\]", text)
    assert wikilinks, "vault section should include at least one placeholder wikilink"
    for link in wikilinks:
        assert "<" in link, f"wikilink must be a placeholder, not a real name: [[{link}]]"


def test_skill_documents_config_yml_and_preset_dag():
    """The operator skill must teach the config.yml + preset-DAG posture."""
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "config.yml" in text
    assert "config init" in text
    assert "GDSTT_HOME" in text
    # Preset DAG vocabulary the operator needs to author/inspect presets.
    assert "depends_on" in text
    assert "artifact_type" in text
    assert "transcript-cleanup -> keypoints + meta" in text
    assert "transcript -> keypoints" not in text


def test_skill_documents_folders_not_removed_folder_ids():
    """``folder_ids`` was replaced by ``folders: [{folder_id, name, email}]``; the
    skill must teach the new shape and never send an operator back to the old key."""
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "folders" in text
    assert "folder_id, name, email" in text
    # The removed key may only appear while explaining that it is gone.
    for line in text.splitlines():
        if "folder_ids" in line:
            assert "удалён" in line, f"stale folder_ids reference in the skill: {line!r}"


def test_skill_documents_meta_preset_and_completion_webhook():
    text = SKILL_PATH.read_text(encoding="utf-8")

    # The `meta` preset: its artifact, and that its fields come from config.
    assert ".meta.md" in text
    assert "meta.entities" in text
    assert "{{entities}}" in text
    # The webhook: config keys, payload keys, and the fire-and-forget contract.
    assert "webhook.url" in text
    assert "webhook.token" in text
    assert "fire-and-forget" in text
    for key in ("file", "employee", "transcript", "artifacts"):
        assert key in text, f"skill must document the webhook payload key {key!r}"


def test_skill_documents_config_owned_posture():
    """The skill must teach prompt source priority, conflicts, batch, and the
    config-management surface earlier stages added."""
    text = SKILL_PATH.read_text(encoding="utf-8")

    # Prompt source priority: instructions > prompt_file > error, packaged assets.
    assert "instructions" in text
    assert "prompt_file" in text
    # The new config subcommands and the inline-first / file-mode auth split.
    for needle in (
        "config init",
        "config path",
        "config get",
        "config set",
        "config unset",
        "auth import-credentials",
        "auth use-files",
        "google.credentials",
        "google.token_file",
        "GDSTT_HOME",
    ):
        assert needle in text, f"skill must document {needle!r}"
    # Batch is cheaper/slower, not higher quality; batch_wait default; per-stage.
    assert "batch_wait" in text
    assert "transcript-cleanup.batch" in text or "transcript-cleanup" in text


def test_agents_doc_documents_config_yml_and_preset_dag():
    text = AGENTS_PATH.read_text(encoding="utf-8")

    assert "data/config.yml" in text
    assert "preset" in text.lower()
    assert "artifact_type" in text
    # The GDSTT_HOME directory bootstrap is the breaking change operators must know.
    assert "GDSTT_HOME" in text
    # `meta` is a second built-in preset alongside `keypoints`. Match the artifact
    # suffix, not the bare word — "meta" alone also hits "metadata" and would pass
    # even if the preset were never documented.
    assert ".meta.md" in text


def test_docs_document_the_folders_migration():
    """``folder_ids`` is removed. README/AGENTS must teach ``folders`` and mention
    the old key only to explain the migration."""
    readme = README_PATH.read_text(encoding="utf-8")
    agents = AGENTS_PATH.read_text(encoding="utf-8")

    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "folders:" in readme
    assert "folder_id: abc" in readme
    assert "folders: [{folder_id, name, email}]" in agents
    assert "is **removed**" in agents

    # Every surviving mention must sit in a paragraph that explains the removal or
    # the read-only property that replaced it — never in one that still teaches the
    # old key. Checked per paragraph so the guard survives prose re-wrapping.
    markers = ("removed", "удалён", "read-only property", "no longer")
    for name, text in (("README.md", readme), ("AGENTS.md", agents), ("SKILL.md", skill)):
        for paragraph in text.split("\n\n"):
            if "folder_ids" not in paragraph:
                continue
            assert any(marker in paragraph for marker in markers), (
                f"{name} still teaches folder_ids as a live setting:\n{paragraph}"
            )


def test_docs_document_the_completion_webhook_and_meta_preset():
    readme = README_PATH.read_text(encoding="utf-8")
    agents = AGENTS_PATH.read_text(encoding="utf-8")

    # Webhook: config keys, the payload, and that a failure never fails the file.
    assert "webhook.url" in readme
    assert "webhook.token" in readme
    assert "Authorization: Bearer" in readme
    assert "fire-and-forget" in readme.lower()
    assert "webhook" in agents.lower()

    # meta preset: the artifact, the config-driven entity list, and the
    # placeholder; the deprecated allow-list keys are still documented as the
    # migration path for a config that hasn't declared meta.entities yet.
    assert ".meta.md" in readme
    assert "meta.entities" in readme
    assert "{{entities}}" in readme
    assert "tags.allowed" in readme
    assert "tags.allowed" in agents


def test_docs_point_at_the_single_keyterms_example():
    """The three keyterms copies collapsed to one packaged example; no doc may
    still send an operator to the deleted ``config/`` copy."""
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_PATH.read_text(encoding="utf-8")
    agents = AGENTS_PATH.read_text(encoding="utf-8")

    assert "deepgram-keyterms-example.txt" in readme
    assert "data/deepgram-keyterms.txt" in readme

    for name, text in (("README.md", readme), ("SKILL.md", skill), ("AGENTS.md", agents)):
        assert "config/deepgram-keyterms.txt" not in text, (
            f"{name} still references the deleted config/ keyterms copy"
        )


def test_docs_describe_config_keys_not_env_runtime_defaults():
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_PATH.read_text(encoding="utf-8")
    agents = AGENTS_PATH.read_text(encoding="utf-8")
    combined = "\n".join([readme, skill, agents])

    assert "data_dir: ." in readme
    assert "data_dir: data" not in readme
    assert "| `data_dir` | `.` |" in readme
    assert "Manage gdstt configuration (data/config.yml)" not in combined
    assert "scripts/docker-smoke.sh my-image   # use an existing tag instead" not in readme
    assert "validates `run-once --dry-run`" not in agents

    for stale in (
        "`FOLDER_IDS`",
        "`STT_PROVIDER`",
        "`OUTPUT_TARGET`",
        "`OUTPUT_DIR`",
        "`OPENAI_KEYPOINTS`",
    ):
        assert stale not in readme


def test_agents_doc_exists_with_source_of_truth_pointer():
    assert AGENTS_PATH.exists(), f"missing AGENTS.md: {AGENTS_PATH}"
    text = AGENTS_PATH.read_text(encoding="utf-8")

    assert text.startswith("# AGENTS.md")
    assert "Source of truth layering" in text


def test_claude_doc_points_back_to_agents():
    assert CLAUDE_PATH.exists(), f"missing CLAUDE.md: {CLAUDE_PATH}"
    text = CLAUDE_PATH.read_text(encoding="utf-8")

    assert "Primary shared repo instructions live in [AGENTS.md](AGENTS.md)." in text
    assert "[AGENTS.md](AGENTS.md) is the source of truth." in text


def test_skill_documents_the_bookings_commands():
    text = Path("skills/gdstt-cli/SKILL.md").read_text(encoding="utf-8")

    assert "gdstt bookings list" in text
    assert "gdstt bookings rematch" in text
    assert "gdstt bookings restore-dates" in text


def test_agents_documents_the_call_booking_invariants():
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "call_booking" in text
    assert "planfix" in text
