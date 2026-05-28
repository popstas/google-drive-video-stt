from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src import auth, drive
from src import main as main_module
from src.config import load_config
from src.stt.transcribe import transcribe_file

logger = logging.getLogger(__name__)


def cmd_auth(args: argparse.Namespace) -> None:
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    auth.run_interactive_flow(config.data_dir, response_url=args.response_url)
    logger.info("Token saved to %s", config.data_dir / "token.json")


def cmd_run(args: argparse.Namespace) -> None:
    main_module.main()


def cmd_run_once(args: argparse.Namespace) -> None:
    config = load_config()
    service = auth.build_drive_service(data_dir=config.data_dir)
    main_module.run_once(service, config)


def cmd_process(args: argparse.Namespace) -> None:
    config = load_config()
    service = auth.build_drive_service(data_dir=config.data_dir)
    is_folder = True if args.folder else None
    main_module.process_target(service, args.target, config, is_folder=is_folder)


def cmd_transcribe(args: argparse.Namespace) -> None:
    config = load_config()
    text = transcribe_file(Path(args.audio), config)
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(text, encoding="utf-8")
        logger.info("Transcript written to %s", out_path)
    else:
        print(text)


def cmd_list(args: argparse.Namespace) -> None:
    config = load_config()
    service = auth.build_drive_service(data_dir=config.data_dir)
    folder_ids = [args.folder] if args.folder else config.folder_ids
    if not folder_ids:
        logger.error("No folders to inspect; set FOLDER_IDS or pass --folder")
        raise SystemExit(1)
    for folder_id in folder_ids:
        items = drive.list_folder_state(service, folder_id)
        print(f"Folder {folder_id}: {len(items)} mp4 file(s)")
        for item in items:
            name = item["file"]["name"]
            mp3 = "mp3" if item.get("has_mp3") else "---"
            txt = "txt" if item.get("has_txt") else "---"
            print(f"  [{mp3}] [{txt}] {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdstt",
        description="Operator CLI for the Google Drive video STT service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="Run the interactive OAuth flow")
    p_auth.add_argument(
        "response_url",
        nargs="?",
        default=None,
        help="OAuth redirect URL for the manual flow (optional)",
    )
    p_auth.set_defaults(func=cmd_auth)

    p_run = sub.add_parser("run", help="Run the polling loop")
    p_run.set_defaults(func=cmd_run)

    p_run_once = sub.add_parser("run-once", help="Run a single polling cycle")
    p_run_once.set_defaults(func=cmd_run_once)

    p_process = sub.add_parser(
        "process", help="Process a Drive file or folder on demand"
    )
    p_process.add_argument("target", help="Drive file ID or folder ID")
    p_process.add_argument(
        "--folder",
        action="store_true",
        help="Treat the target as a folder ID (default: auto-detect)",
    )
    p_process.set_defaults(func=cmd_process)

    p_transcribe = sub.add_parser(
        "transcribe", help="Transcribe a local audio file with the configured provider"
    )
    p_transcribe.add_argument("audio", help="Path to a local audio file (e.g. an MP3)")
    p_transcribe.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write the transcript to this path instead of stdout",
    )
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_list = sub.add_parser(
        "list",
        aliases=["status"],
        help="Show folder state (sibling MP3/TXT presence) without doing work",
    )
    p_list.add_argument(
        "--folder",
        default=None,
        help="Folder ID to inspect (default: configured FOLDER_IDS)",
    )
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
