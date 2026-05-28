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
from src.extractor import extract_m4a_copy, extract_mp3
from src.stt.transcribe import transcribe_file

logger = logging.getLogger(__name__)


def _save_and_upload_txt(
    service: Any, mp4_name: str, text: str, folder_id: str, tmp_dir: Path,
) -> None:
    txt_name = Path(mp4_name).stem + ".txt"
    txt_path = tmp_dir / txt_name
    txt_path.write_text(text, encoding="utf-8")
    drive.upload(service, txt_path, folder_id, mime_type=drive.TXT_MIME)
    logger.info("Uploaded %s to folder %s", txt_name, folder_id)


def _prepare_deepgram_audio(mp4_path: Path, config: Config) -> Path:
    if config.deepgram_audio_source == "m4a_copy":
        return extract_m4a_copy(mp4_path)
    if config.deepgram_audio_source == "mp3_96k":
        return extract_mp3(mp4_path, bitrate="96k")
    if config.deepgram_audio_source == "mp3_192k":
        return extract_mp3(mp4_path, bitrate="192k")
    raise RuntimeError(f"Unknown Deepgram audio source: {config.deepgram_audio_source}")


def process_item(service: Any, item: dict, folder_id: str, config: Config) -> None:
    file_info = item["file"]
    file_id = file_info["id"]
    file_name = file_info["name"]
    has_mp3 = item.get("has_mp3", False)
    has_txt = item.get("has_txt", False)
    mp3_id = item.get("mp3_id")
    mp3_name = item.get("mp3_name")

    stt_enabled = bool(config.stt_provider)
    needs_mp3 = not has_mp3
    needs_txt = stt_enabled and not has_txt

    if not needs_mp3 and not needs_txt:
        return

    logger.info(
        "Processing %s (id=%s) in folder %s [mp3=%s, txt=%s]",
        file_name, file_id, folder_id, "make" if needs_mp3 else "skip",
        "make" if needs_txt else "skip",
    )

    with tempfile.TemporaryDirectory(prefix="gd-stt-") as tmp:
        tmp_dir = Path(tmp)
        mp4_path: Path | None = None
        mp3_path: Path | None = None

        if needs_mp3:
            mp4_path = drive.download(service, file_id, tmp_dir, file_name)
            mp3_path = extract_mp3(mp4_path, bitrate=config.bitrate)
            drive.upload(service, mp3_path, folder_id, mime_type=drive.MP3_MIME)
            logger.info("Uploaded %s to folder %s", mp3_path.name, folder_id)

        if needs_txt:
            if config.stt_provider == "deepgram":
                if mp4_path is None:
                    mp4_path = drive.download(service, file_id, tmp_dir, file_name)
                stt_audio_path = _prepare_deepgram_audio(mp4_path, config)
            elif mp3_path is None:
                if not mp3_id or not mp3_name:
                    raise RuntimeError(
                        f"mp3 marked present for {file_name} but id/name missing"
                    )
                mp3_path = drive.download(service, mp3_id, tmp_dir, mp3_name)
                stt_audio_path = mp3_path
            else:
                stt_audio_path = mp3_path
            text = transcribe_file(stt_audio_path, config)
            _save_and_upload_txt(service, file_name, text, folder_id, tmp_dir)


def run_once(service: Any, config: Config) -> None:
    for folder_id in config.folder_ids:
        try:
            items = drive.list_folder_state(service, folder_id)
        except RefreshError:
            raise
        except Exception as exc:
            logger.exception("Failed to list folder %s", folder_id)
            notify.notify_error(
                f"Failed to list folder {folder_id}: {exc}\n{traceback.format_exc()}",
                proxy_url=config.proxy_url,
            )
            continue

        stt_enabled = bool(config.stt_provider)
        pending = [
            item for item in items
            if not item.get("has_mp3")
            or (stt_enabled and not item.get("has_txt"))
        ]
        logger.info("Folder %s: %d pending file(s)", folder_id, len(pending))
        for item in pending:
            try:
                process_item(service, item, folder_id, config)
            except RefreshError:
                raise
            except Exception as exc:
                file_name = item.get("file", {}).get("name")
                logger.exception(
                    "Failed to process %s in folder %s", file_name, folder_id
                )
                notify.notify_error(
                    f"Failed to process {file_name} in {folder_id}: {exc}\n"
                    f"{traceback.format_exc()}",
                    proxy_url=config.proxy_url,
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

    try:
        service = build_drive_service(data_dir=config.data_dir)
    except (RefreshError, AuthError) as exc:
        logger.exception("OAuth bootstrap failed; exiting for restart")
        notify.notify_error(
            f"OAuth bootstrap failed; container will exit so it can be restarted "
            f"after re-running `python -m src.auth`: {exc}",
            proxy_url=config.proxy_url,
        )
        raise SystemExit(1) from exc

    while True:
        try:
            run_once(service, config)
        except (RefreshError, AuthError) as exc:
            logger.exception("OAuth refresh failed; exiting for restart")
            notify.notify_error(
                f"OAuth refresh failed; container will exit so it can be restarted "
                f"after re-running `python -m src.auth`: {exc}",
                proxy_url=config.proxy_url,
            )
            raise SystemExit(1) from exc
        except Exception as exc:
            logger.exception("Cycle failed")
            notify.notify_error(
                f"Cycle failed: {exc}\n{traceback.format_exc()}",
                proxy_url=config.proxy_url,
            )
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    main()
