from __future__ import annotations

import logging
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError

from src import drive, notify
from src.auth import AuthError, build_drive_service
from src.config import Config, load_config
from src.extractor import extract_mp3

logger = logging.getLogger(__name__)


def process_file(
    service: Any,
    file_info: dict,
    folder_id: str,
    bitrate: str = "96k",
) -> None:
    file_id = file_info["id"]
    file_name = file_info["name"]
    logger.info("Processing %s (id=%s) in folder %s", file_name, file_id, folder_id)

    with tempfile.TemporaryDirectory(prefix="gd-stt-") as tmp:
        tmp_dir = Path(tmp)
        mp4_path = drive.download(service, file_id, tmp_dir, file_name)
        mp3_path = extract_mp3(mp4_path, bitrate=bitrate)
        drive.upload(service, mp3_path, folder_id, mime_type=drive.MP3_MIME)
        logger.info("Uploaded %s to folder %s", mp3_path.name, folder_id)


def run_once(service: Any, config: Config) -> None:
    for folder_id in config.folder_ids:
        try:
            files = drive.list_unprocessed_mp4(service, folder_id)
        except RefreshError:
            raise
        except Exception as exc:
            logger.exception("Failed to list folder %s", folder_id)
            notify.notify_error(
                f"Failed to list folder {folder_id}: {exc}\n{traceback.format_exc()}"
            )
            continue

        logger.info("Folder %s: %d unprocessed file(s)", folder_id, len(files))
        for file_info in files:
            try:
                process_file(service, file_info, folder_id, bitrate=config.bitrate)
            except RefreshError:
                raise
            except Exception as exc:
                logger.exception(
                    "Failed to process %s in folder %s", file_info.get("name"), folder_id
                )
                notify.notify_error(
                    f"Failed to process {file_info.get('name')} in {folder_id}: {exc}\n"
                    f"{traceback.format_exc()}"
                )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    if not config.folder_ids:
        logger.error("FOLDER_IDS is empty; set it in the environment to start polling")
        raise SystemExit(1)

    service = build_drive_service(data_dir=config.data_dir)

    while True:
        try:
            run_once(service, config)
        except (RefreshError, AuthError) as exc:
            logger.exception("OAuth refresh failed; exiting for restart")
            notify.notify_error(
                f"OAuth refresh failed; container will exit so it can be restarted "
                f"after re-running `python -m src.auth`: {exc}"
            )
            raise SystemExit(1) from exc
        except Exception as exc:
            logger.exception("Cycle failed")
            notify.notify_error(f"Cycle failed: {exc}\n{traceback.format_exc()}")
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    main()
