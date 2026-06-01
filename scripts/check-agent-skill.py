#!/usr/bin/env python3
"""Validate the bundled gdstt Agent Skill and synchronized compatibility mirror."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "gdstt-cli"
REGISTRY_PATH = REPO_ROOT / "docs" / "skills" / "registry.json"
PORTABLE_SKILL_ROOT = REPO_ROOT / ".agents" / "skills" / SKILL_ID
COMPATIBILITY_SKILL_ROOTS = (
    REPO_ROOT / ".claude" / "skills" / SKILL_ID,
)
CANONICAL_COMPANIONS = (
    REPO_ROOT / "docs" / "skills" / "provider-notes.md",
    REPO_ROOT / "docs" / "skills" / "troubleshooting.md",
    REPO_ROOT / "docs" / "skills" / "provider-extension.md",
)
REQUIRED_EXAMPLE_FILES = (
    "drive-only-setup.md",
    "folder-dry-run-size-guard.md",
    "google-timeout-recovery.md",
)


def normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def skill_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None, "SKILL.md must start with YAML frontmatter"

    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        assert sep, f"invalid frontmatter line: {line!r}"
        data[key.strip()] = value.strip().strip('"')
    return data


def validate_required_files() -> None:
    required = [PORTABLE_SKILL_ROOT / "SKILL.md"]
    for companion in CANONICAL_COMPANIONS:
        required.append(PORTABLE_SKILL_ROOT / "references" / companion.name)
    for example_name in REQUIRED_EXAMPLE_FILES:
        required.append(PORTABLE_SKILL_ROOT / "examples" / example_name)
    for root in COMPATIBILITY_SKILL_ROOTS:
        required.append(root / "SKILL.md")
        for companion in CANONICAL_COMPANIONS:
            required.append(root / "references" / companion.name)
        for example_name in REQUIRED_EXAMPLE_FILES:
            required.append(root / "examples" / example_name)

    for path in required:
        assert path.is_file(), f"Missing Agent Skill file: {path}"


def validate_registry_sync() -> None:
    registry = load_registry()
    entry = registry["skills"][SKILL_ID]
    skill_path = PORTABLE_SKILL_ROOT / "SKILL.md"
    frontmatter = skill_frontmatter(skill_path.read_text(encoding="utf-8"))

    assert registry["format_version"] == 1
    assert entry["path"] == ".agents/skills/gdstt-cli/SKILL.md"
    assert entry["compatibility_skill_paths"] == [".claude/skills/gdstt-cli/SKILL.md"]
    assert frontmatter["name"] == entry["name"] == SKILL_ID
    assert frontmatter["description"] == entry["description"]
    assert frontmatter["version"] == entry["version"]
    assert frontmatter["last_updated"] == entry["last_updated"]


def validate_reference_sync(root: Path) -> None:
    for canonical_path in CANONICAL_COMPANIONS:
        bundled_path = root / "references" / canonical_path.name
        assert normalized_text(bundled_path) == normalized_text(canonical_path), (
            f"Out-of-sync bundled reference: {bundled_path}"
        )


def validate_compatibility_mirror_sync() -> None:
    portable_files = {
        path.relative_to(PORTABLE_SKILL_ROOT)
        for path in PORTABLE_SKILL_ROOT.rglob("*")
        if path.is_file()
    }
    for root in COMPATIBILITY_SKILL_ROOTS:
        mirror_files = {
            path.relative_to(root)
            for path in root.rglob("*")
            if path.is_file()
        }
        assert mirror_files == portable_files, f"Compatibility mirror file list is out of sync: {root}"
        for relative_path in portable_files:
            assert normalized_text(root / relative_path) == normalized_text(PORTABLE_SKILL_ROOT / relative_path), (
                f"Compatibility mirror is out of sync: {root / relative_path}"
            )


def validate_portable_skill_text() -> None:
    skill_text = (PORTABLE_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for required_text in (
        ".agents/skills/gdstt-cli/",
        "references/provider-notes.md",
        "references/troubleshooting.md",
        "references/provider-extension.md",
        "### Supporting Playbooks",
        "Ordinary project use should stay in the main skill flow",
        "examples/drive-only-setup.md",
        "examples/folder-dry-run-size-guard.md",
        "examples/google-timeout-recovery.md",
        "Repo maintainer note: when editing this repository, the canonical docs live at:",
        "docs/skills/provider-notes.md",
        "docs/skills/troubleshooting.md",
        "docs/skills/provider-extension.md",
    ):
        assert required_text in skill_text, f"Missing portable skill guidance: {required_text}"


def validate_example_playbooks() -> None:
    for example_name in REQUIRED_EXAMPLE_FILES:
        text = normalized_text(PORTABLE_SKILL_ROOT / "examples" / example_name)
        for required_text in (
            "# ",
            "## When to use",
            "## Ask or confirm first",
            "## Preferred sequence",
        ):
            assert required_text in text, f"Example playbook {example_name} is missing section: {required_text}"


def main() -> int:
    validate_required_files()
    validate_registry_sync()
    validate_reference_sync(PORTABLE_SKILL_ROOT)
    for root in COMPATIBILITY_SKILL_ROOTS:
        validate_reference_sync(root)
    validate_compatibility_mirror_sync()
    validate_portable_skill_text()
    validate_example_playbooks()
    print("gdstt Agent Skill bundle is valid; portable bundle, references, and compatibility mirror are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
