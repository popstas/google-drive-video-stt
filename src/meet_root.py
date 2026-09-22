"""Which folder Google Meet is writing an employee's recordings into right now.

A configured folder id is a photograph of where Meet wrote on the day the config was
written. Meet abandons a recordings root as soon as it is shared with anyone and opens
a new one of the same name beside it, keeping the old one readable and unchanged --
so a pinned id keeps resolving, the cycle keeps reporting success, and no recording is
ever found again. `docs/meet-recordings-folder.md` records the measurements.

Resolving the root at the start of every cycle is what turns that failure into a
delay of one cycle. This module does only that, for one already-impersonated Drive
client, and knows nothing about folders, employees or configuration.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class MeetRoot:
    """The live recordings root, and how many roots were there to choose from."""

    folder_id: str
    name: str
    created_time: str
    # More than one means earlier roots were abandoned after being shared. The live
    # one is still unambiguous; the count is what an operator is told, because those
    # folders hold calls nothing will ever process.
    candidates: int = 1


def _escape(name: str) -> str:
    """Quote a folder name for a Drive query, where `'` is the string delimiter."""
    return name.replace("\\", "\\\\").replace("'", "\\'")


def resolve(service, names: Sequence[str]) -> MeetRoot | None:
    """The newest folder the impersonated user owns at the top of their My Drive.

    Every filter matters. Ownership, because colleagues share their own Meet folders
    around and those carry the same name. The My Drive root as the parent, because
    Meet creates its root at the top level and anything deeper is somebody's copy.
    The name, because a Drive holds plenty of other folders.

    ``None`` when the account has no such folder: the caller skips that employee and
    says so, rather than guessing at another folder.
    """
    root_id = service.files().get(fileId="root", fields="id").execute().get("id")
    clauses = " or ".join(f"name = '{_escape(name)}'" for name in names)
    query = (
        f"mimeType = '{FOLDER_MIME}' and trashed = false and 'me' in owners "
        f"and '{root_id}' in parents and ({clauses})"
    )
    found: list[dict] = []
    page_token = None
    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id,name,createdTime)",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        found.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    if not found:
        return None
    found.sort(key=lambda item: item.get("createdTime", ""))
    live = found[-1]
    if len(found) > 1:
        logger.info(
            "Found %d Meet folders; the newest (%s, created %s) is the live one and "
            "the rest were abandoned after being shared",
            len(found),
            live.get("id"),
            live.get("createdTime"),
        )
    return MeetRoot(
        folder_id=live["id"],
        name=live.get("name", ""),
        created_time=live.get("createdTime", ""),
        candidates=len(found),
    )


def owner_name(service) -> str:
    """The impersonated user's own display name, or an empty string.

    Worth a request because the name is not decoration: `speaker_roles` decides which
    speaker is the manager from the folder owner's name, and a config that carries
    only an address would otherwise leave it blank.
    """
    about = service.about().get(fields="user(displayName,emailAddress)").execute()
    return (about.get("user") or {}).get("displayName", "") or ""
