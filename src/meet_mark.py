"""How far Meet discovery has finished, kept in one small file.

Not a cursor: a readable moment, because Meet filters conferences by their start
time and the same moment serves every employee. It says "everything that started
before this has been dealt with", never "I looked at this time" -- a mark that
advanced on looking would lose exactly the recordings that were slow to appear.

Like the changes cursor, this is discovery state and nothing else: whether a
recording has been processed is still decided by what sits beside it in Drive. Losing
this file costs one longer listing, never a lost recording and never a re-transcribed
one.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "meet_checked_at.txt"


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


def read(path: Path) -> dt.datetime | None:
    """The saved moment as an aware UTC datetime, or ``None``.

    Missing, empty and unparsable all answer ``None``, which the caller reads as
    "start from the first-look window". A corrupt file must cost one long listing,
    not a crashed service -- and certainly not a mark silently set to the epoch,
    which would mean transcribing the entire archive.
    """
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("Could not read the Meet mark at %s; looking back instead", path)
        return None
    if not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(
            "The Meet mark at %s is not a moment I can read; looking back instead", path
        )
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def write(path: Path, moment: dt.datetime) -> None:
    """Save the moment in UTC, replacing whatever was there.

    Through a temporary file, so an interrupted write leaves the previous mark
    intact: re-reading a few conferences is free, while a half-written mark would be
    unreadable and cost the long listing.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        encoding="utf-8",
    )
    tmp.replace(path)


def clear(path: Path) -> bool:
    """Forget the mark. Returns whether there was one. The next cycle looks back."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True
