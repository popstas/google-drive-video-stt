"""Turning "an employee" into "a folder, read as that employee".

Everything downstream of discovery is keyed on ``folder_id``: ``folder_by_id``,
``since_for``, the folder's Telegram chat, the booking gate, the completion webhook.
Teaching all of that about addresses and impersonation would spread delegation across
the whole service, so it is resolved here instead, once, before a cycle starts: each
delegated entry becomes an ordinary entry carrying the id of the folder Meet is
writing into right now, and the Drive client that may read it.

The resolution is redone every cycle on purpose. That is what makes Meet abandoning a
recordings root (see ``docs/meet-recordings-folder.md``) cost one cycle instead of a
support ticket.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from src import auth, meet_root
from src.config import Config, EmployeeFolder

logger = logging.getLogger(__name__)

# One client and one display name per employee, kept for the life of the process.
# Both are answers that do not change between cycles -- the credentials refresh
# themselves, and nobody renames themselves hourly -- while rebuilding them would
# cost a token request and an `about.get` per employee per cycle. The folder id is
# deliberately *not* cached: that is the one answer that goes stale, and re-asking
# for it every cycle is the whole point of resolving at all.
_CLIENTS: dict[tuple[str, str], Any] = {}
_NAMES: dict[tuple[str, str], str] = {}


def forget_clients() -> None:
    """Drop the cached clients and names (tests, and a config reload)."""
    _CLIENTS.clear()
    _NAMES.clear()


def _key(config: Config, subject: str) -> tuple[str, str]:
    """Cache key: which key is acting, and for whom.

    The key's identity is part of it so that rotating the service account, or
    pointing the config at a different one, does not keep serving clients built
    from the old one.
    """
    account = config.google_service_account or {}
    source = account.get("client_email") or str(config.google_service_account_file or "")
    return (source, subject)


@dataclass(frozen=True)
class Fleet:
    """The effective config, plus the client each folder must be read with."""

    config: Config
    fallback: Any
    services: dict[str, Any] = field(default_factory=dict)
    # Employees that could not be resolved at all. Counted as folder errors by the
    # caller, which is what keeps a cycle from looking like it drained everything.
    errors: int = 0

    def service_for(self, folder_id: str) -> Any:
        """The client that owns ``folder_id``, or the shared one for a pinned folder."""
        return self.services.get(folder_id, self.fallback)


def resolve(
    config: Config,
    fallback: Any,
    *,
    build: Callable[..., Any] | None = None,
    on_error: Callable[[EmployeeFolder, Exception], None] | None = None,
) -> Fleet:
    """Resolve every delegated entry into a folder id and a client that may read it.

    Without a service account this does nothing at all -- no requests, no changes --
    so a deployment that has not adopted delegation keeps behaving exactly as before.

    One employee failing must not cost the rest their cycle: an address the domain
    refuses, or an account with no Meet folder, is reported and dropped, and the
    others are resolved as usual. Dropping rather than keeping an unresolved entry is
    deliberate: an entry with no folder id would reach code that assumes one and fail
    far from the cause.
    """
    if not config.uses_delegation:
        return Fleet(config=config, fallback=fallback)
    # Looked up here rather than bound as a default, so that a caller -- or a test --
    # replacing ``auth.build_drive_service`` is actually the thing that runs.
    build = build or auth.build_drive_service

    resolved: list[EmployeeFolder] = []
    services: dict[str, Any] = {}
    errors = 0
    for folder in config.folders:
        if not folder.email:
            # A folder pinned by id with nobody attached: read it the old way.
            resolved.append(folder)
            continue
        cache_key = _key(config, folder.email)
        try:
            service = _CLIENTS.get(cache_key)
            if service is None:
                service = build(config=config, subject=folder.email)
                _CLIENTS[cache_key] = service
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            errors += 1
            logger.error("Could not act as %s: %s", folder.email, exc)
            if on_error is not None:
                on_error(folder, exc)
            continue
        try:
            folder_id, candidates = _folder_for(folder, service, config)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            errors += 1
            logger.exception("Could not find the Meet folder of %s", folder.email)
            if on_error is not None:
                on_error(folder, exc)
            continue
        if folder_id is None:
            errors += 1
            logger.error(
                "%s owns no folder named %s at the top of their Drive; skipping them "
                "this cycle",
                folder.email,
                " or ".join(config.meet_folder_names),
            )
            if on_error is not None:
                on_error(folder, LookupError("no Meet folder"))
            continue
        name = folder.name or _cached_owner_name(service, cache_key, folder.email)
        if candidates > 1:
            logger.warning(
                "%s owns %d Meet folders; reading the newest (%s). The others were "
                "abandoned after being shared and nothing will process what is in "
                "them",
                folder.email,
                candidates,
                folder_id,
            )
        services[folder_id] = service
        resolved.append(replace(folder, folder_id=folder_id, name=name))
    return Fleet(
        config=replace(config, folders=tuple(resolved)),
        fallback=fallback,
        services=services,
        errors=errors,
    )


def _folder_for(
    folder: EmployeeFolder, service: Any, config: Config
) -> tuple[str | None, int]:
    """The folder this entry means: the pinned id, or the live Meet root."""
    if folder.folder_id:
        return folder.folder_id, 1
    root = meet_root.resolve(service, config.meet_folder_names)
    if root is None:
        return None, 0
    return root.folder_id, root.candidates


def _cached_owner_name(service: Any, cache_key: tuple[str, str], email: str) -> str:
    """The owner's name, asked for once per process rather than once per cycle."""
    if cache_key not in _NAMES:
        _NAMES[cache_key] = _owner_name(service, email)
    return _NAMES[cache_key]


def _owner_name(service: Any, email: str) -> str:
    """The employee's own name, which `speaker_roles` needs to tell them from a client.

    A failure here is not worth losing the folder over: the name is a hint, the
    recordings are the work.
    """
    try:
        return meet_root.owner_name(service)
    except Exception:  # noqa: BLE001 - a missing name is a worse cycle, not a lost one
        logger.warning("Could not read the display name of %s", email)
        return ""


def shared_service(config: Config, *, build: Callable[..., Any] | None = None) -> Any:
    """The client for folders nobody owns, or ``None`` when there are none.

    A deployment that delegates every folder has no user token at all, and building
    one would fail before anything could explain why. So the shared client is built
    only when something still needs it: a folder with no employee attached, or no
    delegation in the first place.
    """
    build = build or auth.build_drive_service
    if config.uses_delegation and config.folders and all(
        folder.email for folder in config.folders
    ):
        return None
    return build(config=config)


def service_for_file(fleet: Fleet, file_id: str) -> Any:
    """The client that can see ``file_id``, or ``None`` when nobody can.

    Commands like ``process`` and ``speakers set`` are given an id, not a folder, so
    under delegation there is no single account to ask. Trying each employee costs at
    most one request per employee on a manual command, and the first one that can
    open the file is the account whose Drive it lives in -- which is also the account
    whose artifacts should sit beside it.
    """
    seen: list[Any] = []
    for service in fleet.services.values():
        if any(service is other for other in seen):
            continue
        seen.append(service)
        try:
            service.files().get(fileId=file_id, fields="id").execute()
        except Exception:  # noqa: BLE001 - "cannot see it" is the answer, not an error
            continue
        return service
    return fleet.fallback
