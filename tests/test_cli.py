from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src import cli
from tests.test_main import make_config


def test_build_parser_requires_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        cli.main(["bogus"])


def test_auth_dispatch(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    flow_mock = mocker.patch("src.cli.auth.run_interactive_flow")

    cli.main(["auth"])

    flow_mock.assert_called_once_with(tmp_path, response_url=None)


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

    flow_mock.assert_called_once_with(tmp_path, response_url="http://localhost/?code=abc")


def test_run_dispatch_calls_main(mocker):
    main_mock = mocker.patch("src.cli.main_module.main")

    cli.main(["run"])

    main_mock.assert_called_once_with()


def test_run_once_dispatch(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    build_mock = mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    run_once_mock = mocker.patch("src.cli.main_module.run_once")

    cli.main(["run-once"])

    build_mock.assert_called_once_with(data_dir=tmp_path)
    run_once_mock.assert_called_once_with(service, cfg)


def test_process_dispatch_autodetect(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["process", "file123"])

    target_mock.assert_called_once_with(service, "file123", cfg, is_folder=None)


def test_process_dispatch_folder_flag(mocker, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.cli.auth.build_drive_service", return_value=service)
    target_mock = mocker.patch("src.cli.main_module.process_target")

    cli.main(["process", "folder123", "--folder"])

    target_mock.assert_called_once_with(service, "folder123", cfg, is_folder=True)


def test_transcribe_prints_to_stdout(mocker, capsys, tmp_path):
    cfg = make_config(data_dir=tmp_path)
    mocker.patch("src.cli.load_config", return_value=cfg)
    transcribe_mock = mocker.patch(
        "src.cli.transcribe_file", return_value="hello world"
    )

    cli.main(["transcribe", "audio.mp3"])

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
