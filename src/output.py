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

    The two output targets have deliberately different overwrite semantics:

    * ``output.target=folder`` keys each artifact by its *sanitized current
      stem*: the file is written to
      ``<output_dir>/<safe_local_name(base_name)><suffix>`` (creating
      ``output_dir`` if missing). Re-running with the same source name
      overwrites that deterministic path in place. ``existing_id`` is a Drive
      file id and is therefore ignored in folder mode. Caveat: because the
      filename derives from the current stem, renaming the Drive source produces
      a new local file and leaves the old one orphaned (the previous stem is not
      tracked locally, so the stale file is not removed).

    * ``output.target=drive`` writes the text to a temp file under ``tmp_dir``
      and uploads it as a sibling in ``folder_id``. When ``existing_id`` is
      given, the existing Drive file's content is overwritten in place (no
      duplicate is created), which keeps the artifact correct across renames.

    ``output.also_drive`` adds the Drive sibling to folder mode without giving up
    the local copy. It is a separate flag rather than a target because the local
    file is what marks a recording as processed; switching the target instead
    would make the whole backlog look unprocessed and re-transcribe it.
    """
    if config.output_target == "folder":
        _write_to_folder(base_name, suffix, text, config)
        if not config.output_also_drive:
            return
        # The local copy already succeeded, so a Drive outage must not fail the run
        # and cost the recording its transcript.
        try:
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
        except Exception as exc:
            logger.warning(
                "Kept the local %s but could not publish it to Drive: %s",
                base_name + suffix,
                type(exc).__name__,
            )
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
    """Write the artifact to a deterministic path keyed by the sanitized stem.

    Folder mode has no Drive file id to key on, so the destination is derived
    purely from ``base_name``: ``<output_dir>/<safe_local_name(base_name)><suffix>``.
    Writing to this fixed path overwrites the previous artifact for the same
    source name in place. Renaming the source yields a new path and leaves the
    old file orphaned (see ``write_artifact`` for the full caveat). ``existing_id``
    is a Drive id and is intentionally not consulted here.
    """
    output_dir = config.output_dir
    if output_dir is None:
        raise ValueError("output.dir is required when output.target=folder")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Drive names may contain "/" (and other Windows-unsafe chars); sanitize so the
    # local filename stays valid and the path remains inside output_dir.
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
