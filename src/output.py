from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src import drive
from src.config import Config

logger = logging.getLogger(__name__)


def write_artifact(
    service: Any,
    *,
    base_name: str,
    suffix: str,
    text: str,
    folder_id: str,
    config: Config,
    tmp_dir: Path,
    existing_id: str | None = None,
    app_properties: dict[str, str] | None = None,
    mime_type: str = drive.TXT_MIME,
) -> None:
    """Write a generated artifact to the configured destination.

    For ``OUTPUT_TARGET=folder`` the artifact is written to
    ``<output_dir>/<base_name><suffix>`` (creating ``output_dir`` if missing).
    For ``OUTPUT_TARGET=drive`` it is written to a temp file under ``tmp_dir`` and
    uploaded as a sibling in ``folder_id`` (or, when ``existing_id`` is given,
    its content is overwritten in place).
    """
    if config.output_target == "folder":
        _write_to_folder(base_name, suffix, text, config)
        return
    _write_to_drive(
        service,
        base_name=base_name,
        suffix=suffix,
        text=text,
        folder_id=folder_id,
        tmp_dir=tmp_dir,
        existing_id=existing_id,
        app_properties=app_properties,
        mime_type=mime_type,
    )


def _write_to_folder(base_name: str, suffix: str, text: str, config: Config) -> Path:
    output_dir = config.output_dir
    if output_dir is None:
        raise ValueError("OUTPUT_DIR is required when OUTPUT_TARGET=folder")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Drive names may contain "/"; sanitize so the local filename stays safe.
    file_name = drive.safe_local_name(base_name) + suffix
    dest_path = output_dir / file_name
    dest_path.write_text(text, encoding="utf-8")
    logger.info("Wrote %s to %s", file_name, output_dir)
    return dest_path


def _write_to_drive(
    service: Any,
    *,
    base_name: str,
    suffix: str,
    text: str,
    folder_id: str,
    tmp_dir: Path,
    existing_id: str | None,
    app_properties: dict[str, str] | None,
    mime_type: str,
) -> None:
    # Keep the original stem for the uploaded Drive name but sanitize the local
    # temp filename so it stays filesystem-safe on every platform.
    drive_name = base_name + suffix
    local_path = tmp_dir / (drive.safe_local_name(base_name) + suffix)
    local_path.write_text(text, encoding="utf-8")
    if existing_id:
        # Overwrite the existing sibling in place rather than creating a duplicate.
        drive.update_file(
            service,
            existing_id,
            local_path,
            mime_type=mime_type,
            app_properties=app_properties,
        )
        logger.info("Overwrote %s (id=%s) in folder %s", drive_name, existing_id, folder_id)
    else:
        drive.upload(
            service,
            local_path,
            folder_id,
            mime_type=mime_type,
            name=drive_name,
            app_properties=app_properties,
        )
        logger.info("Uploaded %s to folder %s", drive_name, folder_id)
