from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from src import main
from src.auth import AuthError
from src.config import Config


def make_config(
    folder_ids=None,
    bitrate="96k",
    poll_interval=600,
    data_dir=Path("data"),
    stt_provider="",
    openai_api_key="",
    google_cloud_project="",
    google_stt_gcs_bucket="",
    asr_url="",
    stt_language="",
    stt_chunk_seconds=600,
) -> Config:
    return Config(
        folder_ids=folder_ids if folder_ids is not None else ["folderA"],
        poll_interval=poll_interval,
        bitrate=bitrate,
        telegram_bot_token="",
        telegram_chat_id="",
        data_dir=data_dir,
        proxy_url="",
        stt_provider=stt_provider,
        openai_api_key=openai_api_key,
        google_cloud_project=google_cloud_project,
        google_stt_gcs_bucket=google_stt_gcs_bucket,
        asr_url=asr_url,
        stt_language=stt_language,
        stt_chunk_seconds=stt_chunk_seconds,
    )


def _item(file_id="fid", name="video.mp4", *, has_mp3=False, has_txt=False, mp3_id=None, mp3_name=None):
    return {
        "file": {"id": file_id, "name": name},
        "has_mp3": has_mp3,
        "has_txt": has_txt,
        "mp3_id": mp3_id,
        "mp3_name": mp3_name,
    }


def test_process_item_downloads_extracts_uploads(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    download_mock = mocker.patch("src.main.drive.download", return_value=mp4_path)
    extract_mock = mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload", return_value={"id": "uploaded"})

    cfg = make_config(bitrate="128k")
    main.process_item(service, _item("fid1", "video.mp4"), "folderA", cfg)

    download_mock.assert_called_once()
    args, _ = download_mock.call_args
    assert args[0] is service
    assert args[1] == "fid1"
    assert isinstance(args[2], Path)
    assert args[3] == "video.mp4"

    extract_mock.assert_called_once_with(mp4_path, bitrate="128k")
    upload_mock.assert_called_once_with(
        service, mp3_path, "folderA", mime_type="audio/mpeg"
    )


def test_process_item_skips_when_already_done(mocker):
    service = MagicMock()
    download = mocker.patch("src.main.drive.download")
    extract = mocker.patch("src.main.extract_mp3")
    upload = mocker.patch("src.main.drive.upload")

    cfg = make_config()
    main.process_item(
        service, _item("fid", "v.mp4", has_mp3=True), "folder", cfg,
    )
    download.assert_not_called()
    extract.assert_not_called()
    upload.assert_not_called()


def test_process_item_temp_dir_is_cleaned_up(mocker):
    service = MagicMock()
    captured = {}

    def fake_download(service_arg, file_id, dest_dir, name):
        captured["dest_dir"] = dest_dir
        path = dest_dir / name
        path.write_bytes(b"data")
        return path

    mocker.patch("src.main.drive.download", side_effect=fake_download)
    mocker.patch("src.main.extract_mp3", side_effect=lambda p, bitrate: p.with_suffix(".mp3"))
    mocker.patch("src.main.drive.upload")

    cfg = make_config()
    main.process_item(service, _item("fid", "v.mp4"), "f", cfg)

    assert "dest_dir" in captured
    assert not captured["dest_dir"].exists(), "temp dir should be cleaned up"


def test_process_item_runs_stt_when_enabled(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch(
        "src.main.transcribe_file", return_value="hello world"
    )

    cfg = make_config(stt_provider="openai", openai_api_key="sk-x")
    main.process_item(service, _item("fid", "video.mp4"), "f", cfg)

    transcribe_mock.assert_called_once_with(mp3_path, cfg)
    # Two uploads: mp3 and txt
    assert upload_mock.call_count == 2
    second_call = upload_mock.call_args_list[1]
    assert second_call.kwargs["mime_type"] == "text/plain"
    txt_path = second_call.args[1]
    assert txt_path.name == "video.txt"


def test_process_item_only_stt_when_mp3_already_exists(mocker, tmp_path):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name):
        path = dest_dir / name
        path.write_bytes(b"x")
        return path

    download_mock = mocker.patch("src.main.drive.download", side_effect=fake_download)
    extract_mock = mocker.patch("src.main.extract_mp3")
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch(
        "src.main.transcribe_file", return_value="text"
    )

    cfg = make_config(stt_provider="openai", openai_api_key="sk-x")
    item = _item(
        "fid", "video.mp4", has_mp3=True, mp3_id="mp3id", mp3_name="video.mp3",
    )
    main.process_item(service, item, "folderX", cfg)

    extract_mock.assert_not_called()
    download_mock.assert_called_once()
    args, _ = download_mock.call_args
    assert args[1] == "mp3id"
    assert args[3] == "video.mp3"
    transcribe_mock.assert_called_once()
    upload_mock.assert_called_once()
    assert upload_mock.call_args.kwargs["mime_type"] == "text/plain"


def test_process_item_skips_completely_when_mp3_and_txt_present(mocker):
    service = MagicMock()
    download = mocker.patch("src.main.drive.download")
    extract = mocker.patch("src.main.extract_mp3")
    upload = mocker.patch("src.main.drive.upload")
    transcribe = mocker.patch("src.main.transcribe_file")

    cfg = make_config(stt_provider="openai", openai_api_key="sk-x")
    item = _item("fid", "v.mp4", has_mp3=True, has_txt=True,
                 mp3_id="m", mp3_name="v.mp3")
    main.process_item(service, item, "f", cfg)

    download.assert_not_called()
    extract.assert_not_called()
    upload.assert_not_called()
    transcribe.assert_not_called()


def test_run_once_iterates_all_folders_and_files(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1", "f2"])

    listings = {
        "f1": [_item("v1", "a.mp4")],
        "f2": [_item("v2", "b.mp4"), _item("v3", "c.mp4")],
    }
    mocker.patch(
        "src.main.drive.list_folder_state",
        side_effect=lambda svc, fid: listings[fid],
    )
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    assert process_mock.call_count == 3
    calls = [(c.args[2], c.args[1]["file"]["id"]) for c in process_mock.call_args_list]
    assert ("f1", "v1") in calls
    assert ("f2", "v2") in calls
    assert ("f2", "v3") in calls


def test_run_once_filters_already_processed(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1"])

    items = [
        _item("v1", "a.mp4", has_mp3=False),
        _item("v2", "b.mp4", has_mp3=True),
    ]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    assert process_mock.call_count == 1
    assert process_mock.call_args.args[1]["file"]["id"] == "v1"


def test_run_once_with_stt_includes_files_missing_txt(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1"], stt_provider="openai", openai_api_key="sk-x")

    items = [
        _item("v1", "a.mp4", has_mp3=True, has_txt=True, mp3_id="m1", mp3_name="a.mp3"),
        _item("v2", "b.mp4", has_mp3=True, has_txt=False, mp3_id="m2", mp3_name="b.mp3"),
    ]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    assert process_mock.call_count == 1
    assert process_mock.call_args.args[1]["file"]["id"] == "v2"


def test_run_once_continues_on_per_file_error(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1"])

    items = [
        _item("good1", "ok1.mp4"),
        _item("bad", "fail.mp4"),
        _item("good2", "ok2.mp4"),
    ]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)

    processed_ids = []

    def fake_process(svc, item, folder, c):
        if item["file"]["id"] == "bad":
            raise RuntimeError("ffmpeg failed")
        processed_ids.append(item["file"]["id"])

    mocker.patch("src.main.process_item", side_effect=fake_process)
    notify_mock = mocker.patch("src.main.notify.notify_error")

    main.run_once(service, cfg)

    assert processed_ids == ["good1", "good2"]
    notify_mock.assert_called_once()
    assert "fail.mp4" in notify_mock.call_args.args[0]


def test_run_once_continues_on_listing_error(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["bad_folder", "good_folder"])

    def fake_list(svc, folder_id):
        if folder_id == "bad_folder":
            raise RuntimeError("api error")
        return [_item("v1", "a.mp4")]

    mocker.patch("src.main.drive.list_folder_state", side_effect=fake_list)
    process_mock = mocker.patch("src.main.process_item")
    notify_mock = mocker.patch("src.main.notify.notify_error")

    main.run_once(service, cfg)

    assert process_mock.call_count == 1
    assert process_mock.call_args.args[2] == "good_folder"
    notify_mock.assert_called_once()
    assert "bad_folder" in notify_mock.call_args.args[0]


def test_run_once_no_folders_does_nothing(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=[])

    list_mock = mocker.patch("src.main.drive.list_folder_state")
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    list_mock.assert_not_called()
    process_mock.assert_not_called()


def test_run_once_passes_config_to_process(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1"], bitrate="192k")

    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "a.mp4")],
    )
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    assert process_mock.call_args.args[3] is cfg


def test_main_runs_loop_and_sleeps(mocker):
    cfg = make_config(folder_ids=["f1"], poll_interval=42)
    mocker.patch("src.main.load_config", return_value=cfg)
    service = MagicMock()
    mocker.patch("src.main.build_drive_service", return_value=service)

    run_calls = {"n": 0}

    def fake_run_once(svc, c):
        run_calls["n"] += 1
        if run_calls["n"] >= 2:
            raise KeyboardInterrupt

    mocker.patch("src.main.run_once", side_effect=fake_run_once)
    sleep_mock = mocker.patch("src.main.time.sleep")

    with pytest.raises(KeyboardInterrupt):
        main.main()

    assert run_calls["n"] == 2
    sleep_mock.assert_called_with(42)


def test_run_once_propagates_refresh_error(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1"])

    mocker.patch(
        "src.main.drive.list_folder_state",
        side_effect=RefreshError("token revoked"),
    )
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with pytest.raises(RefreshError):
        main.run_once(service, cfg)

    notify_mock.assert_not_called()


def test_run_once_propagates_refresh_error_from_process(mocker):
    service = MagicMock()
    cfg = make_config(folder_ids=["f1"])

    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "a.mp4")],
    )
    mocker.patch("src.main.process_item", side_effect=RefreshError("token revoked"))
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with pytest.raises(RefreshError):
        main.run_once(service, cfg)

    notify_mock.assert_not_called()


def test_main_exits_on_bootstrap_auth_error(mocker):
    cfg = make_config(folder_ids=["f1"], poll_interval=1)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch(
        "src.main.build_drive_service", side_effect=AuthError("malformed token")
    )
    notify_mock = mocker.patch("src.main.notify.notify_error")
    sleep_mock = mocker.patch("src.main.time.sleep")

    with pytest.raises(SystemExit) as excinfo:
        main.main()

    assert excinfo.value.code == 1
    notify_mock.assert_called_once()
    assert "bootstrap" in notify_mock.call_args.args[0]
    sleep_mock.assert_not_called()


def test_main_exits_on_bootstrap_refresh_error(mocker):
    cfg = make_config(folder_ids=["f1"], poll_interval=1)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch(
        "src.main.build_drive_service", side_effect=RefreshError("revoked")
    )
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with pytest.raises(SystemExit) as excinfo:
        main.main()

    assert excinfo.value.code == 1
    notify_mock.assert_called_once()


def test_main_exits_on_refresh_error(mocker):
    cfg = make_config(folder_ids=["f1"], poll_interval=1)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())
    mocker.patch("src.main.run_once", side_effect=RefreshError("revoked"))
    notify_mock = mocker.patch("src.main.notify.notify_error")
    sleep_mock = mocker.patch("src.main.time.sleep")

    with pytest.raises(SystemExit) as excinfo:
        main.main()

    assert excinfo.value.code == 1
    notify_mock.assert_called_once()
    assert "OAuth" in notify_mock.call_args.args[0]
    sleep_mock.assert_not_called()


def test_main_exits_on_auth_error(mocker):
    cfg = make_config(folder_ids=["f1"], poll_interval=1)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())
    mocker.patch("src.main.run_once", side_effect=AuthError("token gone"))
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with pytest.raises(SystemExit) as excinfo:
        main.main()

    assert excinfo.value.code == 1
    notify_mock.assert_called_once()


def test_main_notifies_on_cycle_exception(mocker):
    cfg = make_config(folder_ids=["f1"], poll_interval=1)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())

    call_count = {"n": 0}

    def fake_run_once(svc, c):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        raise KeyboardInterrupt

    mocker.patch("src.main.run_once", side_effect=fake_run_once)
    notify_mock = mocker.patch("src.main.notify.notify_error")
    mocker.patch("src.main.time.sleep")

    with pytest.raises(KeyboardInterrupt):
        main.main()

    notify_mock.assert_called_once()
    assert "boom" in notify_mock.call_args.args[0]
