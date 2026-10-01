"""Where the changes feed left off, kept in one small file.

This is the service's only *discovery* state -- the booking journal and the
appProperties on each artifact are durable too -- and it is deliberately the kind
that can be thrown away. Everything about "has this recording been processed" is still
derived from what sits next to the video in Drive; the cursor only says where to look,
never what has been done. Lose it, corrupt it, delete it, run against a Drive that has
long forgotten it -- each case leads to the same branch: sweep the folders, take a
fresh cursor, carry on. That is why it can be a file with no locking and no schema.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "changes_cursor.txt"
# The folders the cursor was taken against, kept beside it. A cursor only means
# "nothing has happened since" for the folders that were already being watched:
# recordings that sat in a folder before it was added to the config were never a
# change after that cursor, so the feed will never name them. Without this the only
# way to see a newly added folder's backlog is a manual `cursor reset` -- and the
# usual way a folder gets added is onboarding an employee, not a one-off migration.
FOLDERS_FILE_NAME = "changes_folders.txt"


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


def folders_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / FOLDERS_FILE_NAME


def fingerprint(folder_ids: Iterable[str]) -> str:
    """A stable, readable identity for the set of watched folders.

    The ids themselves rather than a hash: it costs a line per employee and makes
    ``cursor show`` able to tell an operator *which* folders the cursor covers,
    which is the question they actually have.
    """
    return "\n".join(sorted({folder_id for folder_id in folder_ids if folder_id}))


def read(path: Path) -> str | None:
    """The saved cursor, or ``None`` when there is nothing usable to resume from.

    Unreadable is treated as absent rather than raised: a truncated or unreadable
    cursor file must cost one full sweep, not a crashed service.
    """
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("Could not read the changes cursor at %s; sweeping instead", path)
        return None
    return token or None


def write(path: Path, token: str) -> None:
    """Save the cursor, replacing whatever was there.

    Written through a temporary file so an interrupted write leaves the previous
    cursor intact instead of a half-written one: re-reading a few changes is free,
    while a corrupt cursor costs a full sweep.
    """
    if not token:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token, encoding="utf-8")
    tmp.replace(path)


def read_folders(path: Path) -> str | None:
    """The folder set the saved cursor was taken against, or ``None``.

    ``None`` covers both "never written" (an instance that predates this file) and
    "unreadable". Both are treated by the caller as "cannot vouch for this cursor",
    which costs one sweep and then writes the file -- the same self-healing the
    cursor itself has.
    """
    try:
        stored = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception(
            "Could not read the watched-folder set at %s; sweeping instead", path
        )
        return None
    return stored or None


def write_folders(path: Path, folders: str) -> None:
    """Record the folder set, replacing whatever was there.

    Only ever called where the cursor itself is saved, so the pair cannot drift: a
    cursor without its folder set would be treated as unvouched and sweep forever.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(folders, encoding="utf-8")
    tmp.replace(path)


def clear(path: Path) -> bool:
    """Forget the cursor. Returns whether there was one. The next cycle sweeps."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True


def clear_folders(path: Path) -> bool:
    """Forget the recorded folder set. Returns whether there was one."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True
