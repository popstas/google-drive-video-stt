from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src import cli
from tests.test_main import make_config


def _normalized_help(text: str) -> str:
    return " ".join(text.split())


def test_build_parser_requires_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        cli.main(["bogus"])


def test_configure_console_encoding_uses_utf8_for_text_streams():
    stdout = MagicMock()
    stderr = MagicMock()

    cli._configure_console_encoding(stdout=stdout, stderr=stderr)

    stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
    stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100", 100),
        ("50MB", 50_000_000),
        ("1.5GiB", 1_610_612_736),
    ],
)
def test_parse_size(raw, expected):
    assert cli._parse_size(raw) == expected


def test_parse_size_rejects_unknown_unit():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._parse_size("50xb")


def test_auth_dispatch(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    flow_mock = mocker.patch("src.cli.auth.run_interactive_flow")

    cli.main(["auth"])

    flow_mock.assert_called_once_with(tmp_path, manual=False, response_url=None)


def test_auth_skips_provider_validation(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    mocker.patch("src.cli.auth.run_interactive_flow")

    cli.main(["auth"])

    load_mock.assert_called_once_with(validate_providers=False)


def test_auth_passes_response_url(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    flow_mock = mocker.patch("src.cli.auth.run_interactive_flow")

    cli.main(["auth", "http://localhost/?code=abc"])

    flow_mock.assert_called_once_with(
        tmp_path,
        manual=False,
        response_url="http://localhost/?code=abc",
    )


def test_auth_manual_dispatch(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    flow_mock = mocker.patch("src.cli.auth.run_interactive_flow")

    cli.main(["auth", "--manual"])

    flow_mock.assert_called_once_with(tmp_path, manual=True, response_url=None)


def test_doctor_uses_drive_only_config_and_skips_auth_by_default(
    mocker,
    capsys,
    tmp_path,
):
    cfg = make_config(folder_ids=["f1"], data_dir=tmp_path, stt_provider="deepgram")
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    build_mock = mocker.patch("src.cli.auth.build_drive_service")

    cli.main(["doctor"])

    load_mock.assert_called_once_with(validate_providers=False)
    build_mock.assert_not_called()
    out = capsys.readouterr().out
    assert "credentials.json: OK" in out
    assert "token.json: missing" in out
    assert "FOLDER_IDS: 1 configured" in out


def test_doctor_drive_check_lists_configured_folders(mocker, capsys, tmp_path):
    cfg = make_config(folder_ids=["f1"], data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    list_mock = mocker.patch("src.cli.drive.list_folder_state", return_value=[])

    cli.main(["doctor", "--drive"])

    list_mock.assert_called_once_with(service, "f1")
    out = capsys.readouterr().out
    assert "Drive auth: OK" in out
    assert "Folder f1: OK, 0 mp4 file(s)" in out


def test_doctor_reports_stt_provider_without_pipeline_readiness(mocker, capsys, tmp_path):
    cfg = make_config(folder_ids=["f1"], data_dir=tmp_path, stt_provider="deepgram")
    mocker.patch("src.cli.load_config", return_value=cfg)
    build_mock = mocker.patch("src.cli.auth.build_drive_service")

    cli.main(["doctor"])

    build_mock.assert_not_called()
    out = capsys.readouterr().out
    assert "STT_PROVIDER: deepgram" in out


def test_run_dispatch_calls_main(mocker):
    main_mock = mocker.patch("src.cli.main_module.main")

    cli.main(["run"])

    main_mock.assert_called_once_with()


def test_top_level_help_recommends_safe_operator_flow(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    out = _normalized_help(capsys.readouterr().out)
    assert "doctor -> list -> process <file-id> --dry-run -> process <file-id>" in out
    assert "run and folder-wide processing can spend STT credits across pending files" in out


def test_run_help_warns_about_continuous_processing(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])

    out = _normalized_help(capsys.readouterr().out)
    assert "Run the polling loop continuously." in out
    assert "can process every pending configured folder and spend STT credits repeatedly" in out


def test_run_once_help_warns_and_points_to_dry_run(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run-once", "--help"])

    out = _normalized_help(capsys.readouterr().out)
    assert "Run a single polling cycle across the configured folders." in out
    assert "can spend STT credits across multiple pending files" in out
    assert "Use --dry-run first" in out


def test_process_help_warns_about_folder_scope_and_reprocess(capsys):
    with pytest.raises(SystemExit):
        cli.main(["process", "--help"])

    out = _normalized_help(capsys.readouterr().out)
    assert "Process one Drive file or folder on demand." in out
    assert "use --dry-run first" in out
    assert "can process many files and spend STT credits" in out
    assert "--reprocess-txt intentionally reruns STT and overwrites the linked .txt" in out


def test_run_once_dispatch(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    build_mock = mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    run_once_mock = mocker.patch("src.cli.main_module.run_once")

    cli.main(["run-once"])

    load_mock.assert_called_once_with()
    build_mock.assert_called_once_with(data_dir=tmp_path)
    run_once_mock.assert_called_once_with(
        service,
        cfg,
        dry_run=False,
        max_size_bytes=None,
        confirm_large=False,
    )


def test_run_once_dispatches_safety_flags(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    run_once_mock = mocker.patch("src.cli.main_module.run_once")

    cli.main(["run-once", "--dry-run", "--max-size", "50MB", "--confirm-large"])

    run_once_mock.assert_called_once_with(
        service,
        cfg,
        dry_run=True,
        max_size_bytes=50_000_000,
        confirm_large=True,
    )


def test_process_dispatch_autodetect(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["process", "file123"])

    load_mock.assert_called_once_with()
    target_mock.assert_called_once_with(
        service,
        "file123",
        cfg,
        is_folder=None,
        reprocess_txt=False,
        dry_run=False,
        max_size_bytes=None,
        confirm_large=False,
    )


def test_process_dispatch_folder_flag(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["process", "folder123", "--folder"])

    target_mock.assert_called_once_with(
        service,
        "folder123",
        cfg,
        is_folder=True,
        reprocess_txt=False,
        dry_run=False,
        max_size_bytes=None,
        confirm_large=False,
    )


def test_process_dispatch_reprocess_txt_flag(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["process", "file123", "--reprocess-txt"])

    target_mock.assert_called_once_with(
        service,
        "file123",
        cfg,
        is_folder=None,
        reprocess_txt=True,
        dry_run=False,
        max_size_bytes=None,
        confirm_large=False,
    )


def test_process_dispatches_safety_flags(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["process", "folder123", "--folder", "--dry-run", "--max-size", "1.5GiB"])

    target_mock.assert_called_once_with(
        service,
        "folder123",
        cfg,
        is_folder=True,
        reprocess_txt=False,
        dry_run=True,
        max_size_bytes=1_610_612_736,
        confirm_large=False,
    )


def test_latest_dispatch_uses_first_folder(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path, folder_ids=["folderA", "folderB"])
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    build_mock = mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    newest = {"id": "v9", "name": "newest.mp4"}
    find_mock = mocker.patch("src.cli.drive.find_newest_mp4", return_value=newest)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["latest"])

    load_mock.assert_called_once_with()
    build_mock.assert_called_once_with(data_dir=tmp_path)
    find_mock.assert_called_once_with(service, "folderA")
    target_mock.assert_called_once_with(
        service,
        "v9",
        cfg,
        is_folder=False,
        dry_run=False,
    )


def test_latest_dispatch_honors_folder_and_dry_run(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    newest = {"id": "v1", "name": "x.mp4"}
    find_mock = mocker.patch("src.cli.drive.find_newest_mp4", return_value=newest)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["latest", "--folder", "folderZ", "--dry-run"])

    find_mock.assert_called_once_with(service, "folderZ")
    target_mock.assert_called_once_with(
        service,
        "v1",
        cfg,
        is_folder=False,
        dry_run=True,
    )


def test_latest_no_mp4_skips_processing(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    mocker.patch("src.cli.drive.find_newest_mp4", return_value=None)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["latest"])

    target_mock.assert_not_called()


def test_latest_without_folder_config_errors(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path, folder_ids=[])
    mocker.patch("src.cli.load_config", return_value=cfg)

    with pytest.raises(SystemExit):
        cli.main(["latest"])


def test_speakers_set_writes_drive_app_property(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    set_mock = mocker.patch("src.cli.drive.set_file_app_properties")

    cli.main(["speakers", "set", "file123", "Alice", "Bob"])

    load_mock.assert_called_once_with(validate_providers=False)
    set_mock.assert_called_once_with(
        service,
        "file123",
        {"speaker_names": "[\"Alice\", \"Bob\"]"},
    )


def test_transcribe_prints_to_stdout(mocker, capsys, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    transcribe_mock = mocker.patch(
        "src.cli.transcribe_file", return_value="hello world"
    )

    cli.main(["transcribe", "audio.mp3"])

    load_mock.assert_called_once_with()
    transcribe_mock.assert_called_once()
    args, _ = transcribe_mock.call_args
    assert args[0] == Path("audio.mp3")
    assert args[1] is cfg
    out = capsys.readouterr().out
    assert "hello world" in out


def test_transcribe_writes_to_output_file(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    mocker.patch("src.cli.transcribe_file", return_value="the transcript")
    out_path = tmp_path / "out.txt"

    cli.main(["transcribe", "audio.mp3", "-o", str(out_path)])

    assert out_path.read_text(encoding="utf-8") == "the transcript"


def test_relabel_dispatch_reads_map_and_writes_output(mocker, tmp_path):
    src_path = tmp_path / "src.md"
    src_path.write_text("[00:00:01] Speaker 1: hi\n", encoding="utf-8")
    map_path = tmp_path / "map.json"
    map_path.write_text('{"default": {"Speaker 1": "Alice"}}', encoding="utf-8")
    out_path = tmp_path / "out.md"
    relabel_mock = mocker.patch(
        "src.cli.relabel_transcript.relabel", return_value="rendered"
    )

    cli.main(["relabel", "--in", str(src_path), "--out", str(out_path), "--map", str(map_path)])

    relabel_mock.assert_called_once_with(
        "[00:00:01] Speaker 1: hi\n",
        {"default": {"Speaker 1": "Alice"}},
        include_header=True,
    )
    assert out_path.read_text(encoding="utf-8") == "rendered"


def test_relabel_dispatch_no_header_flag(mocker, tmp_path):
    src_path = tmp_path / "src.md"
    src_path.write_text("[00:00:01] Speaker 1: hi\n", encoding="utf-8")
    map_path = tmp_path / "map.json"
    map_path.write_text('{"default": {"Speaker 1": "Alice"}}', encoding="utf-8")
    out_path = tmp_path / "out.md"
    relabel_mock = mocker.patch(
        "src.cli.relabel_transcript.relabel", return_value="rendered"
    )

    cli.main(
        [
            "relabel",
            "--in",
            str(src_path),
            "--out",
            str(out_path),
            "--map",
            str(map_path),
            "--no-header",
        ]
    )

    _, kwargs = relabel_mock.call_args
    assert kwargs["include_header"] is False


def test_list_dispatch_uses_configured_folders(mocker, capsys, tmp_path):
    cfg = make_config(folder_ids=["f1"], data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    items = [
        {"file": {"id": "v1", "name": "a.mp4"}, "has_mp3": True, "has_txt": False},
        {"file": {"id": "v2", "name": "b.mp4"}, "has_mp3": False, "has_txt": False},
    ]
    list_mock = mocker.patch(
        "src.cli.drive.list_folder_state", return_value=items
    )

    cli.main(["list"])

    list_mock.assert_called_once_with(service, "f1")
    out = capsys.readouterr().out
    assert "a.mp4" in out
    assert "b.mp4" in out
    assert "[mp3]" in out


def test_status_alias_with_explicit_folder(mocker, capsys, tmp_path):
    cfg = make_config(folder_ids=[], data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    list_mock = mocker.patch("src.cli.drive.list_folder_state", return_value=[])

    cli.main(["status", "--folder", "explicit"])

    list_mock.assert_called_once_with(service, "explicit")


def test_list_no_folders_exits(mocker, tmp_path):
    cfg = make_config(folder_ids=[], data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    mocker.patch("src.cli.auth.build_drive_service", return_value=MagicMock())
    mocker.patch("src.cli.drive.list_folder_state")

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["list"])

    assert excinfo.value.code == 1


def test_list_no_folders_skips_authentication(mocker, tmp_path):
    # The empty-folder check must short-circuit before authenticating, so a
    # missing/expired token can't mask the intended local error.
    cfg = make_config(folder_ids=[], data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    build_mock = mocker.patch("src.cli.auth.build_drive_service")

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["list"])

    assert excinfo.value.code == 1
    build_mock.assert_not_called()


def test_list_skips_provider_validation(mocker, tmp_path):
    cfg = make_config(folder_ids=["f1"], data_dir=tmp_path)
    load_mock = mocker.patch("src.cli.load_config", return_value=cfg)
    mocker.patch("src.cli.auth.build_drive_service", return_value=MagicMock())
    mocker.patch("src.cli.drive.list_folder_state", return_value=[])

    cli.main(["list"])

    load_mock.assert_called_once_with(validate_providers=False)
