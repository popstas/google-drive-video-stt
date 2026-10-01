from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock

from src import meet_root

FOLDER = "application/vnd.google-apps.folder"
MY_DRIVE = "root-id"

_PARENT_RE = re.compile(r"'([^']+)' in parents")
_NAME_RE = re.compile(r"name = '([^']*)'")


def _service(files: list[dict], *, user_name: str = "Someone") -> MagicMock:
    """A Drive whose ``files().list`` honours the filters ``resolve`` relies on.

    Parent, owner, trashed and the name alternatives are all applied for real. A fake
    that ignored any of them would pass a resolver that forgot the same filter, and
    forgetting the owner filter is exactly how a folder shared *to* the employee ends
    up being read as theirs.
    """
    service = MagicMock()
    files_resource = MagicMock()
    service.files.return_value = files_resource

    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        parent = _PARENT_RE.search(q)
        names = set(_NAME_RE.findall(q))
        found = []
        for item in files:
            if "trashed = false" in q and item.get("trashed"):
                continue
            if f"mimeType = '{FOLDER}'" in q and item.get("mimeType") != FOLDER:
                continue
            if "'me' in owners" in q and not item.get("ownedByMe", True):
                continue
            if parent and parent.group(1) not in item.get("parents", []):
                continue
            if names and item.get("name") not in names:
                continue
            found.append(item)
        request = MagicMock()
        request.execute.return_value = {"files": found}
        return request

    files_resource.list.side_effect = list_side_effect

    def get_side_effect(**kwargs):
        request = MagicMock()
        request.execute.return_value = (
            {"id": MY_DRIVE} if kwargs.get("fileId") == "root" else {}
        )
        return request

    files_resource.get.side_effect = get_side_effect

    about = MagicMock()
    service.about.return_value = about
    about.get.return_value.execute.return_value = {
        "user": {"displayName": user_name, "emailAddress": "someone@example.com"}
    }
    return service


def _folder(folder_id, created, *, name="Google Meet", parents=(MY_DRIVE,), **extra):
    return {
        "id": folder_id,
        "name": name,
        "mimeType": FOLDER,
        "createdTime": created,
        "parents": list(parents),
        "ownedByMe": True,
        **extra,
    }


def test_one_folder_is_the_folder():
    service = _service([_folder("a", "2026-08-01T10:00:00Z")])

    resolved = meet_root.resolve(service, ("Google Meet",))

    assert resolved.folder_id == "a"
    assert resolved.candidates == 1


def test_the_newest_of_several_wins():
    """Meet abandons a shared root and opens a new one, so the newest is the live one."""
    service = _service(
        [
            _folder("old", "2026-08-01T10:00:00Z"),
            _folder("live", "2026-09-16T09:00:00Z"),
            _folder("middle", "2026-09-01T09:00:00Z"),
        ]
    )

    resolved = meet_root.resolve(service, ("Google Meet",))

    assert resolved.folder_id == "live"
    assert resolved.candidates == 3


def test_several_candidates_are_reported(caplog):
    """An operator needs to know a root was abandoned, not just which one is live."""
    service = _service(
        [_folder("old", "2026-08-01T10:00:00Z"), _folder("live", "2026-09-16T09:00:00Z")]
    )

    with caplog.at_level(logging.INFO):
        meet_root.resolve(service, ("Google Meet",))

    assert any("Found 2 Meet folders" in record.getMessage() for record in caplog.records)


def test_no_folder_at_all_resolves_to_nothing():
    service = _service([])

    assert meet_root.resolve(service, ("Google Meet",)) is None


def test_a_folder_the_employee_does_not_own_is_not_theirs():
    """Colleagues share their Meet folders around; those are not this employee's."""
    service = _service(
        [_folder("theirs", "2026-09-16T09:00:00Z", ownedByMe=False)]
    )

    assert meet_root.resolve(service, ("Google Meet",)) is None


def test_a_folder_below_my_drive_root_is_not_a_meet_root():
    """Meet creates its root at the top level; anything deeper is somebody's copy."""
    service = _service(
        [_folder("nested", "2026-09-16T09:00:00Z", parents=("some-other-folder",))]
    )

    assert meet_root.resolve(service, ("Google Meet",)) is None


def test_a_trashed_folder_is_not_a_candidate():
    service = _service([_folder("gone", "2026-09-16T09:00:00Z", trashed=True)])

    assert meet_root.resolve(service, ("Google Meet",)) is None


def test_every_configured_name_is_looked_for():
    service = _service([_folder("legacy", "2026-06-01T10:00:00Z", name="Meet Recordings")])

    resolved = meet_root.resolve(service, ("Google Meet", "Meet Recordings"))

    assert resolved.folder_id == "legacy"
    assert resolved.name == "Meet Recordings"


def test_the_owners_own_name_is_readable():
    """`speaker_roles` decides who the manager is from this name, so it must be real."""
    service = _service([], user_name="Real Name")

    assert meet_root.owner_name(service) == "Real Name"


def test_a_drive_without_a_name_gives_an_empty_one():
    service = _service([])
    service.about.return_value.get.return_value.execute.return_value = {}

    assert meet_root.owner_name(service) == ""
