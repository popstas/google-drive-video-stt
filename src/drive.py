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
FOLDER_MIME = "application/vnd.google-apps.folder"
PAGE_SIZE = 1000
SOURCE_VIDEO_ID_PROPERTY = "source_video_id"
ARTIFACT_TYPE_PROPERTY = "artifact_type"
SPEAKER_NAMES_PROPERTY = "speaker_names"
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


def list_unprocessed_mp4(service: Any, folder_id: str) -> list[dict]:
    mp4_files = _list_files_by_mime(service, folder_id, MP4_MIME)
    mp3_files = _list_files_by_mime(service, folder_id, MP3_MIME)

    mp3_basenames = {drive_stem(f["name"]) for f in mp3_files}
    mp3_source_ids = {
        f.get("appProperties", {}).get(SOURCE_VIDEO_ID_PROPERTY)
        for f in mp3_files
        if f.get("appProperties", {}).get(SOURCE_VIDEO_ID_PROPERTY)
    }

    unprocessed = [
        f
        for f in mp4_files
        if f["id"] not in mp3_source_ids and drive_stem(f["name"]) not in mp3_basenames
    ]
    return unprocessed


def list_folder_state(service: Any, folder_id: str) -> list[dict]:
    """Return mp4 files with sibling flags: {file, has_mp3, has_txt, mp3_id}."""
    mp4_files = _list_files_by_mime(service, folder_id, MP4_MIME)
    mp3_files = _list_files_by_mime(service, folder_id, MP3_MIME)
    txt_files = _list_files_by_mime(service, folder_id, TXT_MIME)

    mp3_by_stem = {drive_stem(f["name"]): f for f in mp3_files}
    txt_by_stem = {drive_stem(f["name"]): f for f in txt_files}
    mp3_by_source_id = _files_by_source_video_id(mp3_files)
    txt_by_source_id = _files_by_source_video_id(txt_files)

    items: list[dict] = []
    for mp4 in mp4_files:
        stem = drive_stem(mp4["name"])
        mp3 = mp3_by_source_id.get(mp4["id"]) or mp3_by_stem.get(stem)
        txt = txt_by_source_id.get(mp4["id"]) or txt_by_stem.get(stem)
        items.append({
            "file": mp4,
            "has_mp3": mp3 is not None,
            "has_txt": txt is not None,
            "mp3_id": mp3["id"] if mp3 else None,
            "mp3_name": mp3["name"] if mp3 else None,
            "mp3_size": mp3.get("size") if mp3 else None,
            "txt_id": txt["id"] if txt else None,
        })
    return items


def _files_by_source_video_id(files: list[dict]) -> dict[str, dict]:
    by_source_id: dict[str, dict] = {}
    for item in files:
        source_id = item.get("appProperties", {}).get(SOURCE_VIDEO_ID_PROPERTY)
        if source_id:
            by_source_id[source_id] = item
    return by_source_id


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
    app_properties: dict[str, str],
) -> dict:
    """Merge appProperties onto a Drive file without changing its content."""
    return (
        service.files()
        .update(
            fileId=file_id,
            body={"appProperties": app_properties},
            fields="id, name, appProperties",
            supportsAllDrives=True,
        )
        .execute()
    )


def rename_file(service: Any, file_id: str, name: str) -> dict:
    """Rename a Drive file without changing its content."""
    return (
        service.files()
        .update(
            fileId=file_id,
            body={"name": name},
            fields="id, name",
            supportsAllDrives=True,
        )
        .execute()
    )
