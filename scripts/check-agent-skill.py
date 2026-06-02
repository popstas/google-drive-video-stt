#!/usr/bin/env python3
"""Validate the canonical gdstt Agent Skill package and install workflow."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "gdstt-cli"
CANONICAL_SKILL_ROOT = REPO_ROOT / "skills" / SKILL_ID
REQUIRED_REFERENCES = (
    "commands.md",
    "configuration.md",
    "provider-extension.md",
    "provider-notes.md",
    "troubleshooting.md",
)
REQUIRED_EXAMPLES = (
    "drive-only-setup.md",
    "folder-dry-run-size-guard.md",
    "google-timeout-recovery.md",
    "openai-full-pipeline.md",
)


def normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")


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
    required = [CANONICAL_SKILL_ROOT / "SKILL.md"]
    required.extend(CANONICAL_SKILL_ROOT / "references" / name for name in REQUIRED_REFERENCES)
    required.extend(CANONICAL_SKILL_ROOT / "examples" / name for name in REQUIRED_EXAMPLES)
    for path in required:
        assert path.is_file(), f"Missing Agent Skill file: {path}"


def validate_package_shape() -> None:
    skill_path = CANONICAL_SKILL_ROOT / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    skill_files = list(CANONICAL_SKILL_ROOT.rglob("SKILL.md"))
    frontmatter = skill_frontmatter(skill_text)

    assert skill_files == [skill_path], (
        "The installable package must contain exactly one discoverable SKILL.md"
    )
    assert frontmatter["name"] == SKILL_ID
    assert "description" in frontmatter
    assert re.fullmatch(r"\d+\.\d+\.\d+", frontmatter["version"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", frontmatter["last_updated"])
    assert len(skill_text.splitlines()) <= 400, "Primary SKILL.md must stay at or below 400 lines"

    for relative_path in sorted(package_files(CANONICAL_SKILL_ROOT)):
        if relative_path == Path("SKILL.md"):
            continue
        resource = relative_path.as_posix()
        assert resource in skill_text, f"Primary skill must route agents to {resource}"


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
    validate_package_shape()
    validate_example_playbooks()
    validate_gh_skill_workflow()
    print("gdstt Agent Skill package is valid; canonical resources and install workflow are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
