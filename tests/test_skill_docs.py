from __future__ import annotations

import argparse
import re
from pathlib import Path

from src import cli

SKILL_PATH = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "gdstt-cli" / "SKILL.md"


def _registered_commands() -> set[str]:
    """All subcommand names (including aliases) registered on the CLI parser."""
    parser = cli.build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(subparsers_action.choices.keys())


def _documented_commands(text: str) -> set[str]:
    r"""Command names documented as ``### `<name> ...` `` headers in the skill."""
    documented: set[str] = set()
    for match in re.finditer(r"^###\s+`([a-z-]+)", text, flags=re.MULTILINE):
        documented.add(match.group(1))
    # `list` documents its `status` alias inline as "(alias `status`)".
    for match in re.finditer(r"alias[^`]*`([a-z-]+)`", text):
        documented.add(match.group(1))
    return documented


def test_skill_file_exists_with_frontmatter():
    assert SKILL_PATH.exists(), f"missing skill file: {SKILL_PATH}"
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---"), "skill must start with YAML frontmatter"
    assert "name: gdstt-cli" in text
    assert "description:" in text


def test_documented_commands_match_registered_subcommands():
    text = SKILL_PATH.read_text(encoding="utf-8")
    documented = _documented_commands(text)
    registered = _registered_commands()

    missing = registered - documented
    extra = documented - registered
    assert not missing, f"CLI commands not documented in the skill: {sorted(missing)}"
    assert not extra, f"skill documents commands that are not registered: {sorted(extra)}"


def test_skill_documents_provider_env_vars():
    text = SKILL_PATH.read_text(encoding="utf-8")
    for var in ("STT_PROVIDER", "OPENAI_API_KEY", "DEEPGRAM_API_KEY", "ASR_URL", "FOLDER_IDS"):
        assert var in text, f"skill should document {var}"
