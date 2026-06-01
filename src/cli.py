from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import TextIO

from src import auth, drive, pipeline_executor, pipeline_policy, pipeline_profile, setup
from src import main as main_module
from src.config import load_config
from src.stt.transcribe import transcribe_file

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?)?\s*$", re.IGNORECASE)
_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "m": 1000**2,
    "mb": 1000**2,
    "g": 1000**3,
    "gb": 1000**3,
    "t": 1000**4,
    "tb": 1000**4,
    "ki": 1024,
    "kib": 1024,
    "mi": 1024**2,
    "mib": 1024**2,
    "gi": 1024**3,
    "gib": 1024**3,
    "ti": 1024**4,
    "tib": 1024**4,
}


def _parse_size(raw: str) -> int:
    match = _SIZE_RE.match(raw)
    if not match:
        raise argparse.ArgumentTypeError(
            "expected a byte size like 50000000, 50MB, or 1.5GiB"
        )
    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit not in _SIZE_UNITS:
        raise argparse.ArgumentTypeError(f"unknown size unit: {unit}")
    return int(amount * _SIZE_UNITS[unit])


def _configure_console_encoding(
    *, stdout: TextIO | None = None, stderr: TextIO | None = None
) -> None:
    for stream in (stdout or sys.stdout, stderr or sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def cmd_auth(args: argparse.Namespace) -> None:
    config = load_config(validate_providers=False)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    auth.run_interactive_flow(
        config.data_dir,
        manual=args.manual,
        response_url=args.response_url,
    )
    logger.info("Token saved to %s", config.data_dir / "token.json")


def cmd_setup(args: argparse.Namespace) -> None:
    setup.run_setup()


def _parse_json_argument(raw: str) -> dict:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("intent JSON must be an object")
    return payload


def _load_intent_argument(args: argparse.Namespace) -> dict:
    if args.intent_json_file:
        raw = Path(args.intent_json_file).read_text(encoding="utf-8-sig")
    else:
        raw = args.intent_json
    return _parse_json_argument(raw)


def _add_intent_input(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", dest="intent_json", help="Intent JSON object")
    group.add_argument(
        "--json-file",
        dest="intent_json_file",
        metavar="PATH",
        help="Read the intent JSON object from a file",
    )


def cmd_plan(args: argparse.Namespace) -> None:
    load_config(validate_providers=False)
    profile = pipeline_profile.load_pipeline_profile()
    plan = pipeline_policy.plan_process(_load_intent_argument(args), profile)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def cmd_execute(args: argparse.Namespace) -> None:
    config = load_config(validate_providers=False)
    profile = pipeline_profile.load_pipeline_profile()
    result = pipeline_executor.execute_process(
        _load_intent_argument(args),
        config,
        profile,
        confirmed=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    main_module.main()


def cmd_run_once(args: argparse.Namespace) -> None:
    config = load_config()
    service = auth.build_drive_service(data_dir=config.data_dir)
    main_module.run_once(
        service,
        config,
        dry_run=args.dry_run,
        max_size_bytes=args.max_size,
        confirm_large=args.confirm_large,
    )


def cmd_process(args: argparse.Namespace) -> None:
    config = load_config()
    service = auth.build_drive_service(data_dir=config.data_dir)
    is_folder = True if args.folder else None
    main_module.process_target(
        service,
        args.target,
        config,
        is_folder=is_folder,
        reprocess_txt=args.reprocess_txt,
        dry_run=args.dry_run,
        max_size_bytes=args.max_size,
        confirm_large=args.confirm_large,
    )


def cmd_doctor(args: argparse.Namespace) -> None:
    config = load_config(validate_providers=False)
    credentials_path = config.data_dir / "credentials.json"
    token_path = config.data_dir / "token.json"

    print(f"DATA_DIR: {config.data_dir}")
    print(f"credentials.json: {'OK' if credentials_path.exists() else 'missing'}")
    print(f"token.json: {'OK' if token_path.exists() else 'missing'}")
    print(f"FOLDER_IDS: {len(config.folder_ids)} configured")
    print(f"STT_PROVIDER: {config.stt_provider or 'not configured'}")
    profile = pipeline_profile.load_pipeline_profile()
    for key, state in pipeline_profile.required_secret_status(profile).items():
        print(f"{key}: {'configured' if state['configured'] else 'missing'}")

    if not args.drive:
        print("Drive auth: not checked (use --drive)")
        return

    service = auth.build_drive_service(data_dir=config.data_dir)
    print("Drive auth: OK")
    for folder_id in config.folder_ids:
        items = drive.list_folder_state(service, folder_id)
        print(f"Folder {folder_id}: OK, {len(items)} mp4 file(s)")


def cmd_speakers_set(args: argparse.Namespace) -> None:
    config = load_config(validate_providers=False)
    service = auth.build_drive_service(data_dir=config.data_dir)
    names = json.dumps(args.names, ensure_ascii=False)
    drive.set_file_app_properties(
        service,
        args.target,
        {drive.SPEAKER_NAMES_PROPERTY: names},
    )
    logger.info("Speaker names saved on %s", args.target)


def cmd_refresh_names(args: argparse.Namespace) -> None:
    config = load_config(validate_providers=False)
    service = auth.build_drive_service(data_dir=config.data_dir)
    main_module.refresh_artifact_names(service, args.target)


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
    config = load_config(validate_providers=False)
    folder_ids = [args.folder] if args.folder else config.folder_ids
    if not folder_ids:
        logger.error("No folders to inspect; set FOLDER_IDS or pass --folder")
        raise SystemExit(1)
    service = auth.build_drive_service(data_dir=config.data_dir)
    for folder_id in folder_ids:
        items = drive.list_folder_state(service, folder_id)
        print(f"Folder {folder_id}: {len(items)} mp4 file(s)")
        for item in items:
            name = item["file"]["name"]
            mp3 = "mp3" if item.get("has_mp3") else "---"
            txt = "txt" if item.get("has_txt") else "---"
            print(f"  [{mp3}] [{txt}] {name}")


def _add_processing_safety_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without downloading, transcribing, or uploading",
    )
    parser.add_argument(
        "--max-size",
        type=_parse_size,
        default=None,
        metavar="SIZE",
        help="Skip Drive videos larger than SIZE unless --confirm-large is passed",
    )
    parser.add_argument(
        "--confirm-large",
        action="store_true",
        help="Allow processing files that exceed --max-size",
    )


def _set_parser_safety_description(
    parser: argparse.ArgumentParser,
    *,
    summary: str,
    safety_note: str,
) -> None:
    parser.description = summary
    parser.epilog = f"Safety: {safety_note}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdstt",
        description=(
            "Operator CLI for the Google Drive video STT service. Prefer "
            "setup for first-time local setup, then doctor -> list -> process <file-id> "
            "--dry-run -> process <file-id> "
            "before folder-wide run-once or run."
        ),
        epilog=(
            "Safety: run and folder-wide processing can spend STT credits across pending "
            "files. Start with --dry-run when the command supports it."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="Run the browser or manual OAuth flow")
    p_auth.add_argument(
        "--manual",
        action="store_true",
        help="Print the authorization URL instead of opening a browser",
    )
    p_auth.add_argument(
        "response_url",
        nargs="?",
        default=None,
        help="OAuth redirect URL for the manual flow (optional)",
    )
    p_auth.set_defaults(func=cmd_auth)

    p_setup = sub.add_parser(
        "setup",
        help="Guide first-time local setup for Drive auth and the default provider",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_plan = sub.add_parser(
        "plan",
        help="Build a read-only JSON execution plan from an agent intent",
    )
    _add_intent_input(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_execute = sub.add_parser(
        "execute",
        help="Execute an agent intent through the configured pipeline profile",
    )
    _add_intent_input(p_execute)
    p_execute.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm a plan that requires explicit approval",
    )
    p_execute.set_defaults(func=cmd_execute)

    p_run = sub.add_parser(
        "run",
        help="Run the polling loop for all pending configured folders",
    )
    _set_parser_safety_description(
        p_run,
        summary="Run the polling loop continuously.",
        safety_note=(
            "this command can process every pending configured folder and spend STT "
            "credits repeatedly. Prefer run-once --dry-run or process <file-id> --dry-run "
            "before using it."
        ),
    )
    p_run.set_defaults(func=cmd_run)

    p_run_once = sub.add_parser(
        "run-once",
        help="Run one polling cycle across pending configured folders",
    )
    _set_parser_safety_description(
        p_run_once,
        summary="Run a single polling cycle across the configured folders.",
        safety_note=(
            "this command can spend STT credits across multiple pending files. Use --dry-run "
            "first and add --max-size only as an optional manual limit for larger folder runs."
        ),
    )
    _add_processing_safety_args(p_run_once)
    p_run_once.set_defaults(func=cmd_run_once)

    p_process = sub.add_parser(
        "process",
        help="Process one Drive file or folder on demand",
    )
    _set_parser_safety_description(
        p_process,
        summary="Process one Drive file or folder on demand.",
        safety_note=(
            "use --dry-run first. When the target is a folder, this command can process many "
            "files and spend STT credits. --reprocess-txt intentionally reruns STT and overwrites "
            "the linked .txt."
        ),
    )
    p_process.add_argument("target", help="Drive file ID or folder ID")
    p_process.add_argument(
        "--folder",
        action="store_true",
        help="Treat the target as a folder ID (default: auto-detect)",
    )
    p_process.add_argument(
        "--reprocess-txt",
        action="store_true",
        help="Run STT again and overwrite the existing TXT instead of skipping it",
    )
    _add_processing_safety_args(p_process)
    p_process.set_defaults(func=cmd_process)

    p_doctor = sub.add_parser(
        "doctor",
        help="Check local Drive/OAuth configuration without changing anything",
    )
    p_doctor.add_argument(
        "--drive",
        action="store_true",
        help="Also authenticate and list configured Drive folders",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_speakers = sub.add_parser(
        "speakers",
        help="Manage explicit speaker names stored on a Drive MP4",
    )
    speakers_sub = p_speakers.add_subparsers(dest="speakers_command", required=True)
    p_speakers_set = speakers_sub.add_parser(
        "set",
        help="Set speaker names for future post-processing of a Drive MP4",
    )
    p_speakers_set.add_argument("target", help="Drive MP4 file ID")
    p_speakers_set.add_argument("names", nargs="+", help="Speaker names in order")
    p_speakers_set.set_defaults(func=cmd_speakers_set)

    p_refresh_names = sub.add_parser(
        "refresh-names",
        help="Rename linked MP3/TXT artifacts to match the current MP4 name",
    )
    p_refresh_names.add_argument("target", help="Drive MP4 file ID")
    p_refresh_names.set_defaults(func=cmd_refresh_names)

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
    _configure_console_encoding()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
