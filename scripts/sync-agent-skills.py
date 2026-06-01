#!/usr/bin/env python3
"""Synchronize the canonical gdstt Agent Skill package and workspace mirrors."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "gdstt-cli"
CANONICAL_ROOT = REPO_ROOT / "skills" / SKILL_ID
MIRROR_ROOTS = (
    REPO_ROOT / ".agents" / "skills" / SKILL_ID,
    REPO_ROOT / ".claude" / "skills" / SKILL_ID,
)
CANONICAL_DOC_REFERENCES = (
    REPO_ROOT / "docs" / "skills" / "provider-notes.md",
    REPO_ROOT / "docs" / "skills" / "troubleshooting.md",
    REPO_ROOT / "docs" / "skills" / "provider-extension.md",
)


def _normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")


def _assert_within(path: Path, root: Path) -> None:
    path.resolve().relative_to(root.resolve())


def _copy_if_needed(source: Path, destination: Path, *, write: bool, drift: list[str]) -> None:
    if destination.is_file() and _normalized_text(source) == _normalized_text(destination):
        return
    drift.append(f"out of sync: {destination.relative_to(REPO_ROOT)}")
    if write:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _sync_reference_docs(*, write: bool, drift: list[str]) -> None:
    for source in CANONICAL_DOC_REFERENCES:
        destination = CANONICAL_ROOT / "references" / source.name
        _copy_if_needed(source, destination, write=write, drift=drift)


def _sync_tree(source_root: Path, destination_root: Path, *, write: bool, drift: list[str]) -> None:
    source_files = {
        path.relative_to(source_root): path
        for path in source_root.rglob("*")
        if path.is_file()
    }
    destination_files = {
        path.relative_to(destination_root): path
        for path in destination_root.rglob("*")
        if path.is_file()
    } if destination_root.is_dir() else {}

    for relative_path, source in source_files.items():
        destination = destination_root / relative_path
        _copy_if_needed(source, destination, write=write, drift=drift)

    for relative_path in sorted(destination_files.keys() - source_files.keys()):
        destination = destination_root / relative_path
        _assert_within(destination, destination_root)
        drift.append(f"stale mirror file: {destination.relative_to(REPO_ROOT)}")
        if write:
            destination.unlink()

    if write and destination_root.is_dir():
        for directory in sorted(
            (path for path in destination_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            _assert_within(directory, destination_root)
            if not any(directory.iterdir()):
                directory.rmdir()


def sync(*, write: bool) -> list[str]:
    if not (CANONICAL_ROOT / "SKILL.md").is_file():
        raise SystemExit(f"Missing canonical Agent Skill: {CANONICAL_ROOT / 'SKILL.md'}")

    drift: list[str] = []
    _sync_reference_docs(write=write, drift=drift)
    for mirror_root in MIRROR_ROOTS:
        _sync_tree(CANONICAL_ROOT, mirror_root, write=write, drift=drift)
    return drift


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Update canonical references and mirrors")
    mode.add_argument("--check", action="store_true", help="Report drift without modifying files")
    args = parser.parse_args()

    drift = sync(write=args.write)
    if args.write:
        print(f"Synchronized {SKILL_ID} canonical references and workspace mirrors.")
        return 0
    if drift:
        for message in drift:
            print(message)
        return 1
    print(f"{SKILL_ID} canonical references and workspace mirrors are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
