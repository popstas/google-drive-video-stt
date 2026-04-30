from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

MP4_MIME = "video/mp4"
MP3_MIME = "audio/mpeg"
PAGE_SIZE = 1000


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
                fields="nextPageToken, files(id, name, mimeType, size)",
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

    mp3_basenames = {Path(f["name"]).stem for f in mp3_files}

    unprocessed = [
        f for f in mp4_files if Path(f["name"]).stem not in mp3_basenames
    ]
    return unprocessed


def download(service: Any, file_id: str, dest_dir: Path, file_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_name

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status is not None:
                logger.debug(
                    "Downloading %s: %d%%", file_name, int(status.progress() * 100)
                )
    return dest_path


def upload(service: Any, local_path: Path, folder_id: str, mime_type: str = MP3_MIME) -> dict:
    metadata = {"name": local_path.name, "parents": [folder_id]}
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
