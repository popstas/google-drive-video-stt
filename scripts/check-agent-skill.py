#!/usr/bin/env python3
"""Validate the canonical gdstt Agent Skill package and generated mirrors."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "gdstt-cli"
REGISTRY_PATH = REPO_ROOT / "docs" / "skills" / "registry.json"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-agent-skills.py"
CANONICAL_SKILL_ROOT = REPO_ROOT / "skills" / SKILL_ID
GENERATED_MIRROR_ROOTS = (
    REPO_ROOT / ".agents" / "skills" / SKILL_ID,
    REPO_ROOT / ".claude" / "skills" / SKILL_ID,
)
CANONICAL_COMPANIONS = (
    REPO_ROOT / "docs" / "skills" / "provider-notes.md",
    REPO_ROOT / "docs" / "skills" / "troubleshooting.md",
    REPO_ROOT / "docs" / "skills" / "provider-extension.md",
)
REQUIRED_REFERENCES = (
    "commands.md",
    "configuration.md",
    "provider-notes.md",
    "troubleshooting.md",
    "provider-extension.md",
)
REQUIRED_EXAMPLES = (
    "drive-only-setup.md",
    "folder-dry-run-size-guard.md",
    "google-timeout-recovery.md",
    "openai-full-pipeline.md",
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


def package_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
    }


def validate_required_files() -> None:
    required = [CANONICAL_SKILL_ROOT / "SKILL.md", SYNC_SCRIPT]
    required.extend(CANONICAL_SKILL_ROOT / "references" / name for name in REQUIRED_REFERENCES)
    required.extend(CANONICAL_SKILL_ROOT / "examples" / name for name in REQUIRED_EXAMPLES)
    for path in required:
        assert path.is_file(), f"Missing Agent Skill file: {path}"


def validate_registry_sync() -> None:
    registry = load_registry()
    entry = registry["skills"][SKILL_ID]
    skill_path = CANONICAL_SKILL_ROOT / "SKILL.md"
    frontmatter = skill_frontmatter(skill_path.read_text(encoding="utf-8"))

    assert registry["format_version"] == 1
    assert entry["path"] == "skills/gdstt-cli/SKILL.md"
    assert entry["generated_mirror_paths"] == [
        ".agents/skills/gdstt-cli/SKILL.md",
        ".claude/skills/gdstt-cli/SKILL.md",
    ]
    assert frontmatter["name"] == entry["name"] == SKILL_ID
    assert frontmatter["description"] == entry["description"]
    assert frontmatter["version"] == entry["version"]
    assert frontmatter["last_updated"] == entry["last_updated"]


def validate_package_shape() -> None:
    skill_text = (CANONICAL_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    skill_files = list(CANONICAL_SKILL_ROOT.rglob("SKILL.md"))
    assert skill_files == [CANONICAL_SKILL_ROOT / "SKILL.md"], (
        "The installable package must contain exactly one discoverable SKILL.md"
    )
    assert len(skill_text.splitlines()) <= 400, "Primary SKILL.md must stay at or below 400 lines"

    for relative_path in sorted(package_files(CANONICAL_SKILL_ROOT)):
        if relative_path == Path("SKILL.md"):
            continue
        resource = relative_path.as_posix()
        assert resource in skill_text, f"Primary skill must route agents to {resource}"


def validate_reference_sync() -> None:
    for canonical_path in CANONICAL_COMPANIONS:
        bundled_path = CANONICAL_SKILL_ROOT / "references" / canonical_path.name
        assert normalized_text(bundled_path) == normalized_text(canonical_path), (
            f"Out-of-sync bundled reference: {bundled_path}"
        )


def validate_generated_mirrors() -> None:
    canonical_files = package_files(CANONICAL_SKILL_ROOT)
    for root in GENERATED_MIRROR_ROOTS:
        assert package_files(root) == canonical_files, f"Generated mirror file list is out of sync: {root}"
        for relative_path in canonical_files:
            assert normalized_text(root / relative_path) == normalized_text(CANONICAL_SKILL_ROOT / relative_path), (
                f"Generated mirror is out of sync: {root / relative_path}"
            )


def validate_sync_script_check_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def validate_example_playbooks() -> None:
    for example_name in REQUIRED_EXAMPLES:
        text = normalized_text(CANONICAL_SKILL_ROOT / "examples" / example_name)
        for required_text in (
            "# ",
            "## When to use",
            "## Ask or confirm first",
            "## Preferred sequence",
        ):
            assert required_text in text, f"Example playbook {example_name} is missing section: {required_text}"


def gh_skill_available() -> bool:
    if shutil.which("gh") is None:
        return False
    result = subprocess.run(
        ["gh", "skill", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def validate_gh_skill_workflow() -> None:
    if not gh_skill_available():
        print("Skipping gh skill smoke test: gh skill is not installed.")
        return

    publish = subprocess.run(
        ["gh", "skill", "publish", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert publish.returncode == 0, publish.stdout + publish.stderr

    with tempfile.TemporaryDirectory(prefix="gdstt-skill-install-") as temp_dir:
        install_root = Path(temp_dir)
        install = subprocess.run(
            [
                "gh",
                "skill",
                "install",
                ".",
                SKILL_ID,
                "--from-local",
                "--dir",
                str(install_root),
                "--force",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert install.returncode == 0, install.stdout + install.stderr
        installed_skill_files = list(install_root.rglob("SKILL.md"))
        assert len(installed_skill_files) == 1, "Local install must contain exactly one SKILL.md"
        installed_root = installed_skill_files[0].parent
        assert package_files(installed_root) == package_files(CANONICAL_SKILL_ROOT), (
            "Local gh skill install must preserve every bundled resource"
        )


def main() -> int:
    validate_required_files()
    validate_registry_sync()
    validate_package_shape()
    validate_reference_sync()
    validate_generated_mirrors()
    validate_sync_script_check_mode()
    validate_example_playbooks()
    validate_gh_skill_workflow()
    print("gdstt Agent Skill package is valid; resources, mirrors, and install workflow are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
