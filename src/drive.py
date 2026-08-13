from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

MP4_MIME = "video/mp4"
MP3_MIME = "audio/mpeg"
TXT_MIME = "text/plain"
MD_MIME = "text/markdown"
FOLDER_MIME = "application/vnd.google-apps.folder"
PAGE_SIZE = 1000
SOURCE_VIDEO_ID_PROPERTY = "source_video_id"
ARTIFACT_TYPE_PROPERTY = "artifact_type"
SPEAKER_NAMES_PROPERTY = "speaker_names"
BOOKING_MATCH_PROPERTY = "booking_match"
PLANFIX_COMMENT_TASK_ID_PROPERTY = "planfix_comment_task_id"
# The single value ``booking_match`` ever takes: this recording matched no booked
# call, so the polling loop must leave it alone.
BOOKING_MATCH_NONE = "none"
_LOCAL_FILENAME_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\0]')


class DownloadIntegrityError(RuntimeError):
    """Raised when a Drive download completes with an unexpected local size."""


def drive_stem(name: str) -> str:
    """Filename minus its extension, treating the Drive name as a flat string.

    Drive names may contain ``/`` (Drive allows it in file names); ``Path(...).stem``
    would treat that as a path separator and drop everything before the last ``/``.
    ``os.path.splitext`` only splits on the final dot, so the full name is preserved.
    """
    return os.path.splitext(name)[0]


def safe_local_name(name: str) -> str:
    """Sanitize a Drive name into a filesystem-safe local filename.

    Drive accepts characters that Windows does not allow in local filenames.
    Replace those characters so temp downloads/extractions work on every platform.
    """
    return _LOCAL_FILENAME_UNSAFE_RE.sub("_", name)


def get_file_metadata(service: Any, file_id: str) -> dict:
    """Return id/name/mimeType/parents/size/appProperties for a single Drive file."""
    return (
        service.files()
        .get(
            fileId=file_id,
            fields="id, name, mimeType, parents, size, appProperties",
            supportsAllDrives=True,
        )
        .execute()
    )


def _list_files_by_mime(service: Any, folder_id: str, mime_type: str) -> list[dict]:
    files: list[dict] = []
    page_token: str | None = None
    query = (
        f"'{folder_id}' in parents and mimeType = '{mime_type}' and trashed = false"
    )
    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size, appProperties)",
                pageSize=PAGE_SIZE,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def find_newest_mp4(service: Any, folder_id: str) -> dict | None:
    """Return the most recently created mp4 in a folder, or None when empty."""
    query = (
        f"'{folder_id}' in parents and mimeType = '{MP4_MIME}' and trashed = false"
    )
    response = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType, size, appProperties)",
            orderBy="createdTime desc",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = response.get("files", [])
    return files[0] if files else None


def list_folder_state(service: Any, folder_id: str) -> list[dict]:
    """Return mp4 files with sibling flags.

    Each item is ``{file, has_mp3, has_txt, mp3_id, mp3_name, txt_id, artifact_ids}``
    where ``artifact_ids`` maps each produced preset's ``artifact_type`` appProperty
    to its Drive file id (e.g. ``{"keypoints": "k1"}``). The OpenAI stage consults
    this to skip presets that already have an artifact. Legacy
    ``<video-stem>.keypoints.md`` files uploaded before the appProperty existed are
    folded onto the ``keypoints`` preset by stem.
    """
    mp4_files = _list_files_by_mime(service, folder_id, MP4_MIME)
    mp3_files = _list_files_by_mime(service, folder_id, MP3_MIME)
    txt_files = _list_files_by_mime(service, folder_id, TXT_MIME)
    md_files = _list_files_by_mime(service, folder_id, MD_MIME)

    mp3_by_stem = {drive_stem(f["name"]): f for f in mp3_files}
    txt_by_stem = {drive_stem(f["name"]): f for f in txt_files}
    # Keypoints are uploaded as ``<video-stem>.keypoints.md``; strip the
    # ``.keypoints`` suffix so they match back to the source video stem.
    #
    # The robust link is the ``source_video_id`` appProperty (see
    # ``artifacts_by_source_id``); this bare-stem fallback only exists for
    # legacy artifacts uploaded before that property was set. To avoid
    # false-matching a user-authored ``<something>.keypoints.md`` file as the
    # generated artifact of ``<something>.mp4`` (which would wrongly skip
    # regeneration), only fold a ``.md`` file into the stem index when it both
    # uses the ``.keypoints.md`` naming convention AND carries no
    # ``source_video_id`` pointing at some other video.
    keypoints_by_stem: dict[str, dict] = {}
    for f in md_files:
        stem = drive_stem(f["name"])
        if not stem.endswith(".keypoints"):
            continue
        if f.get("appProperties", {}).get(SOURCE_VIDEO_ID_PROPERTY):
            # Authoritatively linked via source_video_id; handled separately.
            continue
        keypoints_by_stem[_strip_keypoints_suffix(stem)] = f
    mp3_by_source_id = _files_by_source_video_id(mp3_files)
    txt_by_source_id = _files_by_source_video_id(txt_files)
    artifacts_by_source_id = _artifacts_by_source_video_id(md_files)

    items: list[dict] = []
    for mp4 in mp4_files:
        stem = drive_stem(mp4["name"])
        mp3 = mp3_by_source_id.get(mp4["id"]) or mp3_by_stem.get(stem)
        txt = txt_by_source_id.get(mp4["id"]) or txt_by_stem.get(stem)

        artifact_ids: dict[str, str] = {}
        # Legacy bare-stem keypoints first; an authoritative source_video_id
        # match (below) overrides it for the same artifact_type.
        legacy_keypoints = keypoints_by_stem.get(stem)
        if legacy_keypoints is not None:
            artifact_ids["keypoints"] = legacy_keypoints["id"]
        for artifact_type, f in artifacts_by_source_id.get(mp4["id"], {}).items():
            artifact_ids[artifact_type] = f["id"]

        mp4_props = mp4.get("appProperties", {}) or {}
        items.append({
            "file": mp4,
            "has_mp3": mp3 is not None,
            "has_txt": txt is not None,
            "mp3_id": mp3["id"] if mp3 else None,
            "mp3_name": mp3["name"] if mp3 else None,
            "txt_id": txt["id"] if txt else None,
            "artifact_ids": artifact_ids,
            "booking_match": mp4_props.get(BOOKING_MATCH_PROPERTY, ""),
            "planfix_comment_task_id": mp4_props.get(
                PLANFIX_COMMENT_TASK_ID_PROPERTY, ""
            ),
        })
    return items


def _strip_keypoints_suffix(stem: str) -> str:
    return stem[: -len(".keypoints")] if stem.endswith(".keypoints") else stem


def _files_by_source_video_id(files: list[dict]) -> dict[str, dict]:
    by_source_id: dict[str, dict] = {}
    for item in files:
        source_id = item.get("appProperties", {}).get(SOURCE_VIDEO_ID_PROPERTY)
        if source_id:
            by_source_id[source_id] = item
    return by_source_id


def _artifacts_by_source_video_id(files: list[dict]) -> dict[str, dict[str, dict]]:
    """Group preset artifacts by source video id, then by ``artifact_type``.

    Returns ``{source_video_id: {artifact_type: file}}``. A markdown artifact
    carrying ``source_video_id`` but no ``artifact_type`` is assumed to be a legacy
    keypoints document (the only artifact type that predates multi-preset support).
    """
    by_source: dict[str, dict[str, dict]] = {}
    for item in files:
        props = item.get("appProperties", {})
        source_id = props.get(SOURCE_VIDEO_ID_PROPERTY)
        if not source_id:
            continue
        artifact_type = props.get(ARTIFACT_TYPE_PROPERTY) or "keypoints"
        by_source.setdefault(source_id, {})[artifact_type] = item
    return by_source


def download(
    service: Any,
    file_id: str,
    dest_dir: Path,
    file_name: str,
    *,
    expected_size_bytes: int | None = None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = safe_local_name(file_name)
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError(f"Invalid file name from Drive: {file_name!r}")
    dest_path = dest_dir / safe_name

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status is not None:
                logger.debug(
                    "Downloading %s: %d%%", safe_name, int(status.progress() * 100)
                )
    if expected_size_bytes is not None:
        actual_size = dest_path.stat().st_size
        if actual_size != expected_size_bytes:
            dest_path.unlink(missing_ok=True)
            raise DownloadIntegrityError(
                f"Downloaded file size mismatch for {file_name}: expected "
                f"{expected_size_bytes} bytes, got {actual_size}"
            )
    return dest_path


def download_text(service: Any, file_id: str) -> str:
    """Download a small text/markdown Drive file's content into memory.

    Used to re-feed an existing transcript into the preset stage without
    re-running STT when a Drive ``.txt`` sibling already exists but some preset
    artifact is still missing.
    """
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return buffer.getvalue().decode("utf-8-sig")


def upload(
    service: Any,
    local_path: Path,
    folder_id: str,
    mime_type: str = MP3_MIME,
    name: str | None = None,
    app_properties: dict[str, str] | None = None,
) -> dict:
    metadata = {"name": name or local_path.name, "parents": [folder_id]}
    if app_properties:
        metadata["appProperties"] = app_properties
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    response = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id, name, parents",
            supportsAllDrives=True,
        )
        .execute()
    )
    return response


def update_file(
    service: Any,
    file_id: str,
    local_path: Path,
    mime_type: str = TXT_MIME,
    app_properties: dict[str, str] | None = None,
) -> dict:
    """Overwrite an existing Drive file's content in place (keeps id and name)."""
    metadata = {"appProperties": app_properties} if app_properties else None
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    kwargs: dict[str, Any] = {
        "fileId": file_id,
        "media_body": media,
        "fields": "id, name",
        "supportsAllDrives": True,
    }
    if metadata:
        kwargs["body"] = metadata
    response = (
        service.files()
        .update(**kwargs)
        .execute()
    )
    return response


def set_file_app_properties(
    service: Any,
    file_id: str,
    app_properties: dict[str, str | None],
) -> dict:
    """Merge appProperties onto a Drive file without changing its content.

    Drive counts every ``files.update`` as an edit: it moves ``modifiedTime``, sets
    ``lastModifyingUser`` and appends "You edited an item" to the activity feed.
    These properties are our own bookkeeping, not a user edit, and people sort these
    shared folders by "Last modified" -- so the date has to survive the write.
    Reading the current value and sending it straight back in the same request keeps
    it exactly where it was.

    Preservation is unconditional rather than opt-in: every call site writes
    bookkeeping, and a flag is something a future call site forgets to pass.
    """
    current = (
        service.files()
        .get(fileId=file_id, fields="modifiedTime", supportsAllDrives=True)
        .execute()
    )
    body: dict[str, Any] = {"appProperties": app_properties}
    modified_time = current.get("modifiedTime")
    if modified_time:
        # A blank value would clear the date rather than preserve it, so only send
        # one Drive actually gave us.
        body["modifiedTime"] = modified_time
    return (
        service.files()
        .update(
            fileId=file_id,
            body=body,
            fields="id, name, appProperties",
            supportsAllDrives=True,
        )
        .execute()
    )
