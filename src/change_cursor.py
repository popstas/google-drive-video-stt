"""Where the changes feed left off, kept in one small file.

This is the service's only piece of durable state, and it is deliberately the kind
that can be thrown away. Everything about "has this recording been processed" is still
derived from what sits next to the video in Drive; the cursor only says where to look,
never what has been done. Lose it, corrupt it, delete it, run against a Drive that has
long forgotten it -- each case leads to the same branch: sweep the folders, take a
fresh cursor, carry on. That is why it can be a file with no locking and no schema.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "changes_cursor.txt"


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


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


def clear(path: Path) -> bool:
    """Forget the cursor. Returns whether there was one. The next cycle sweeps."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True
