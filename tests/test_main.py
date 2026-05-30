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
    deepgram_api_key="",
    google_cloud_project="",
    google_stt_gcs_bucket="",
    asr_url="",
    stt_language="",
    stt_chunk_seconds=600,
    deepgram_audio_source="m4a_copy",
    drive_mp3_artifact=True,
    stt_postprocess=False,
    openai_postprocess=False,
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
        deepgram_api_key=deepgram_api_key,
        google_cloud_project=google_cloud_project,
        google_stt_gcs_bucket=google_stt_gcs_bucket,
        asr_url=asr_url,
        stt_language=stt_language,
        stt_chunk_seconds=stt_chunk_seconds,
        stt_postprocess=stt_postprocess,
        openai_postprocess=openai_postprocess,
        deepgram_audio_source=deepgram_audio_source,
        drive_mp3_artifact=drive_mp3_artifact,
    )


def _item(
    file_id="fid", name="video.mp4", *, has_mp3=False, has_txt=False,
    mp3_id=None, mp3_name=None, txt_id=None, size=None,
):
    file_info = {"id": file_id, "name": name}
    if size is not None:
        file_info["size"] = str(size)
    return {
        "file": file_info,
        "has_mp3": has_mp3,
        "has_txt": has_txt,
        "mp3_id": mp3_id,
        "mp3_name": mp3_name,
        "txt_id": txt_id,
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
        service,
        mp3_path,
        "folderA",
        mime_type="audio/mpeg",
        name="video.mp3",
        app_properties={"source_video_id": "fid1", "artifact_type": "mp3"},
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


def test_process_item_deepgram_m4a_downloads_mp4_even_when_mp3_exists(
    mocker,
    tmp_path,
):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name):
        path = dest_dir / name
        path.write_bytes(b"x")
        return path

    download_mock = mocker.patch("src.main.drive.download", side_effect=fake_download)
    extract_mock = mocker.patch("src.main.extract_mp3")
    m4a_path = tmp_path / "video.m4a"
    m4a_mock = mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch("src.main.transcribe_file", return_value="text")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        stt_language="ru",
        deepgram_audio_source="m4a_copy",
    )
    item = _item(
        "fid",
        "video.mp4",
        has_mp3=True,
        mp3_id="mp3id",
        mp3_name="video.mp3",
    )
    main.process_item(service, item, "folderX", cfg)

    assert download_mock.call_args.args[1] == "fid"
    assert download_mock.call_args.args[3] == "video.mp4"
    extract_mock.assert_not_called()
    m4a_mock.assert_called_once()
    transcribe_mock.assert_called_once_with(m4a_path, cfg)
    upload_mock.assert_called_once()
    assert upload_mock.call_args.args[1].name == "video.txt"


def test_process_item_deepgram_m4a_does_not_upload_mp3_by_default(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    m4a_path = tmp_path / "video.m4a"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    extract_mp3_mock = mocker.patch("src.main.extract_mp3")
    mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hello")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        stt_language="ru",
        deepgram_audio_source="m4a_copy",
        drive_mp3_artifact=False,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    extract_mp3_mock.assert_not_called()
    assert upload_mock.call_count == 1
    assert upload_mock.call_args.kwargs["mime_type"] == "text/plain"


def test_process_item_deepgram_m4a_uploads_mp3_when_artifact_enabled(
    mocker,
    tmp_path,
):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"
    m4a_path = tmp_path / "video.m4a"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    extract_mp3_mock = mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hello")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        stt_language="ru",
        deepgram_audio_source="m4a_copy",
        drive_mp3_artifact=True,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    extract_mp3_mock.assert_called_once_with(mp4_path, bitrate="96k")
    assert upload_mock.call_count == 2
    assert upload_mock.call_args_list[0].kwargs["mime_type"] == "audio/mpeg"
    assert upload_mock.call_args_list[1].kwargs["mime_type"] == "text/plain"


def test_process_item_deepgram_mp3_96k_extracts_mp4_for_stt(mocker, tmp_path):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name):
        path = dest_dir / name
        path.write_bytes(b"x")
        return path

    download_mock = mocker.patch("src.main.drive.download", side_effect=fake_download)
    mp3_path = tmp_path / "video.mp3"
    extract_mock = mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch("src.main.transcribe_file", return_value="text")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        stt_language="ru",
        deepgram_audio_source="mp3_96k",
    )
    item = _item(
        "fid",
        "video.mp4",
        has_mp3=True,
        mp3_id="mp3id",
        mp3_name="video.mp3",
    )
    main.process_item(service, item, "folderX", cfg)

    assert download_mock.call_args.args[1] == "fid"
    extract_mock.assert_called_once()
    assert extract_mock.call_args.kwargs["bitrate"] == "96k"
    transcribe_mock.assert_called_once_with(mp3_path, cfg)
    upload_mock.assert_called_once()


def test_process_item_deepgram_mp3_192k_extracts_mp4_for_stt(mocker, tmp_path):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name):
        path = dest_dir / name
        path.write_bytes(b"x")
        return path

    download_mock = mocker.patch("src.main.drive.download", side_effect=fake_download)
    mp3_path = tmp_path / "video.mp3"
    extract_mock = mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch("src.main.transcribe_file", return_value="text")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        stt_language="ru",
        deepgram_audio_source="mp3_192k",
    )
    item = _item(
        "fid",
        "video.mp4",
        has_mp3=True,
        mp3_id="mp3id",
        mp3_name="video.mp3",
    )
    main.process_item(service, item, "folderX", cfg)

    assert download_mock.call_args.args[1] == "fid"
    extract_mock.assert_called_once()
    assert extract_mock.call_args.kwargs["bitrate"] == "192k"
    transcribe_mock.assert_called_once_with(mp3_path, cfg)
    upload_mock.assert_called_once()


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


def test_process_item_reprocess_txt_overwrites_existing_txt(mocker, tmp_path):
    service = MagicMock()
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp3_path)
    mocker.patch("src.main.extract_mp3")
    mocker.patch("src.main.drive.upload")
    update_mock = mocker.patch("src.main.drive.update_file")
    transcribe_mock = mocker.patch("src.main.transcribe_file", return_value="fresh")

    cfg = make_config(stt_provider="openai", openai_api_key="sk-x")
    item = _item(
        "fid",
        "video.mp4",
        has_mp3=True,
        has_txt=True,
        mp3_id="m1",
        mp3_name="video.mp3",
        txt_id="t1",
    )
    main.process_item(service, item, "folderX", cfg, reprocess_txt=True)

    transcribe_mock.assert_called_once()
    update_mock.assert_called_once()
    assert update_mock.call_args.args[1] == "t1"


def test_process_target_single_file_resolves_parent(mocker):
    service = MagicMock()
    cfg = make_config()

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={
            "id": "v1",
            "name": "a.mp4",
            "mimeType": "video/mp4",
            "parents": ["folderA"],
        },
    )
    items = [_item("v1", "a.mp4"), _item("v2", "b.mp4")]
    list_mock = mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    main.process_target(service, "v1", cfg)

    list_mock.assert_called_once_with(service, "folderA")
    process_mock.assert_called_once()
    assert process_mock.call_args.args[1]["file"]["id"] == "v1"
    assert process_mock.call_args.args[2] == "folderA"


def test_process_target_folder_dry_run_does_not_process_items(mocker, caplog):
    service = MagicMock()
    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={"id": "folderA", "mimeType": main.drive.FOLDER_MIME},
    )
    items = [_item("v1", "pending.mp4", size=5_000_000)]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    with caplog.at_level("INFO"):
        main.process_target(service, "folderA", cfg, is_folder=True, dry_run=True)

    process_mock.assert_not_called()
    assert "DRY RUN" in caplog.text
    assert "pending.mp4" in caplog.text


def test_process_target_skips_large_file_without_confirmation(mocker, caplog):
    service = MagicMock()
    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={
            "id": "v1",
            "name": "large.mp4",
            "mimeType": "video/mp4",
            "parents": ["folderA"],
            "size": "200000000",
        },
    )
    items = [_item("v1", "large.mp4", size=200_000_000)]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    with caplog.at_level("WARNING"):
        main.process_target(
            service,
            "v1",
            cfg,
            max_size_bytes=50_000_000,
            confirm_large=False,
        )

    process_mock.assert_not_called()
    assert "exceeds --max-size" in caplog.text


def test_process_target_processes_large_file_with_confirmation(mocker):
    service = MagicMock()
    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={
            "id": "v1",
            "name": "large.mp4",
            "mimeType": "video/mp4",
            "parents": ["folderA"],
            "size": "200000000",
        },
    )
    item = _item("v1", "large.mp4", size=200_000_000)
    mocker.patch("src.main.drive.list_folder_state", return_value=[item])
    process_mock = mocker.patch("src.main.process_item")

    main.process_target(
        service,
        "v1",
        cfg,
        max_size_bytes=50_000_000,
        confirm_large=True,
    )

    process_mock.assert_called_once()
    assert process_mock.call_args.args[1]["file"]["id"] == "v1"


def test_process_target_autodetects_folder(mocker):
    service = MagicMock()
    cfg = make_config()

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={
            "id": "folderX",
            "name": "My Folder",
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    items = [_item("v1", "a.mp4"), _item("v2", "b.mp4", has_mp3=True)]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    main.process_target(service, "folderX", cfg)

    assert process_mock.call_count == 1
    assert process_mock.call_args.args[1]["file"]["id"] == "v1"
    assert process_mock.call_args.args[2] == "folderX"


def test_process_target_force_folder_flag(mocker):
    service = MagicMock()
    cfg = make_config()

    meta_mock = mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={"id": "folderX", "name": "f", "mimeType": "video/mp4"},
    )
    items = [_item("v1", "a.mp4")]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    main.process_target(service, "folderX", cfg, is_folder=True)

    meta_mock.assert_called_once()
    process_mock.assert_called_once()
    assert process_mock.call_args.args[2] == "folderX"


def test_process_target_file_not_found_raises(mocker):
    service = MagicMock()
    cfg = make_config()

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={
            "id": "v9",
            "name": "missing.mp4",
            "mimeType": "video/mp4",
            "parents": ["folderA"],
        },
    )
    mocker.patch("src.main.drive.list_folder_state", return_value=[_item("v1", "a.mp4")])
    mocker.patch("src.main.process_item")

    with pytest.raises(RuntimeError):
        main.process_target(service, "v9", cfg)


def test_process_target_file_without_parent_raises(mocker):
    service = MagicMock()
    cfg = make_config()

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={"id": "v1", "name": "a.mp4", "mimeType": "video/mp4"},
    )

    with pytest.raises(RuntimeError):
        main.process_target(service, "v1", cfg)


def test_refresh_artifact_names_renames_linked_artifacts(mocker):
    service = MagicMock()
    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={
            "id": "v1",
            "name": "New name.mp4",
            "mimeType": "video/mp4",
            "parents": ["folderA"],
        },
    )
    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[
            {
                "file": {"id": "v1", "name": "New name.mp4"},
                "has_mp3": True,
                "has_txt": True,
                "mp3_id": "m1",
                "txt_id": "t1",
            }
        ],
    )
    rename_mock = mocker.patch("src.main.drive.rename_file")

    main.refresh_artifact_names(service, "v1")

    assert rename_mock.call_args_list[0].args == (service, "m1", "New name.mp3")
    assert rename_mock.call_args_list[1].args == (service, "t1", "New name.txt")


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


def test_run_once_dry_run_does_not_process_items(mocker, caplog):
    service = MagicMock()
    cfg = make_config(
        folder_ids=["folderA"],
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
    )
    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "pending.mp4", size=5_000_000)],
    )
    process_mock = mocker.patch("src.main.process_item")

    with caplog.at_level("INFO"):
        main.run_once(service, cfg, dry_run=True)

    process_mock.assert_not_called()
    assert "DRY RUN" in caplog.text
    assert "pending.mp4" in caplog.text


def test_run_once_skips_large_pending_items_without_confirmation(mocker, caplog):
    service = MagicMock()
    cfg = make_config(
        folder_ids=["folderA"],
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
    )
    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "large.mp4", size=200_000_000)],
    )
    process_mock = mocker.patch("src.main.process_item")

    with caplog.at_level("WARNING"):
        main.run_once(service, cfg, max_size_bytes=50_000_000)

    process_mock.assert_not_called()
    assert "exceeds --max-size" in caplog.text


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


def test_process_item_preserves_slash_name_on_upload(mocker, tmp_path):
    service = MagicMock()
    cfg = make_config(stt_provider="openai", openai_api_key="sk-x")

    captured = {}

    def fake_download(svc, file_id, dest_dir, name):
        # Drive name with "/" must become a filesystem-safe local temp file.
        path = dest_dir / main.drive.safe_local_name(name)
        path.write_bytes(b"x")
        return path

    def fake_extract(mp4_path, bitrate):
        return mp4_path.with_suffix(".mp3")

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        captured.setdefault("uploads", []).append((name, local_path.name, mime_type))

    mocker.patch("src.main.drive.download", side_effect=fake_download)
    mocker.patch("src.main.extract_mp3", side_effect=fake_extract)
    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch("src.main.transcribe_file", return_value="text")

    item = _item("fid", "Call 2026/05/28 Rec.mp4")
    main.process_item(service, item, "folderX", cfg)

    uploads = dict((name, local) for name, local, _ in captured["uploads"])
    # Drive upload names keep the original "/"; local temp names are sanitized.
    assert "Call 2026/05/28 Rec.mp3" in uploads
    assert "Call 2026/05/28 Rec.txt" in uploads
    for drive_name, local_name in uploads.items():
        assert "/" not in local_name


def test_save_and_upload_txt_creates_when_no_txt_id(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.main.drive.upload")
    update_mock = mocker.patch("src.main.drive.update_file")

    main._save_and_upload_txt(service, "fid", "video.mp4", "hello", "folderA", tmp_path)

    update_mock.assert_not_called()
    upload_mock.assert_called_once()
    assert upload_mock.call_args.kwargs["name"] == "video.txt"


def test_save_and_upload_txt_overwrites_existing(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.main.drive.upload")
    update_mock = mocker.patch("src.main.drive.update_file")

    main._save_and_upload_txt(
        service, "fid", "video.mp4", "final text", "folderA", tmp_path, txt_id="t1",
    )

    upload_mock.assert_not_called()
    update_mock.assert_called_once()
    args = update_mock.call_args.args
    assert args[1] == "t1"
    assert args[2].read_text(encoding="utf-8") == "final text"


def test_process_item_postprocesses_transcript_before_upload(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        if mime_type == "text/plain":
            captured["txt"] = local_path.read_text(encoding="utf-8")

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch(
        "src.main.transcribe_file",
        return_value="Speaker 1: hi there\nSpeaker 2: hello back",
    )

    cfg = make_config(stt_provider="openai", openai_api_key="sk-x", stt_postprocess=True)
    main.process_item(service, _item("fid", "Alice and Bob.mp4"), "f", cfg)

    assert captured["txt"] == "Alice: hi there\nBob: hello back"


def test_process_item_uses_speaker_names_from_drive_properties(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        if mime_type == "text/plain":
            captured["txt"] = local_path.read_text(encoding="utf-8")

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch(
        "src.main.transcribe_file",
        return_value="Speaker 1: hi there\nSpeaker 2: hello back",
    )

    cfg = make_config(stt_provider="openai", openai_api_key="sk-x", stt_postprocess=True)
    item = _item("fid", "Unhelpful file name.mp4")
    item["file"]["appProperties"] = {"speaker_names": "[\"Alice\", \"Bob\"]"}
    main.process_item(service, item, "f", cfg)

    assert captured["txt"] == "Alice: hi there\nBob: hello back"


def test_process_item_openai_postprocess_replaces_deterministic(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        if mime_type == "text/plain":
            captured["txt"] = local_path.read_text(encoding="utf-8")

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    refine_mock = mocker.patch(
        "src.main.openai_pipeline.refine_transcript", return_value="Alice: hi"
    )
    pp_mock = mocker.patch("src.main.postprocess.postprocess_transcript")

    cfg = make_config(
        stt_provider="openai",
        openai_api_key="sk-x",
        stt_postprocess=True,
        openai_postprocess=True,
    )
    main.process_item(service, _item("fid", "Alice and Bob.mp4"), "f", cfg)

    assert captured["txt"] == "Alice: hi"
    refine_mock.assert_called_once()
    pp_mock.assert_not_called()


def test_process_item_openai_postprocess_uses_speaker_names_from_drive_properties(
    mocker,
    tmp_path,
):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    refine_mock = mocker.patch(
        "src.main.openai_pipeline.refine_transcript", return_value="Alice: hi"
    )

    cfg = make_config(
        stt_provider="openai",
        openai_api_key="sk-x",
        openai_postprocess=True,
    )
    item = _item("fid", "Wrong One and Wrong Two.mp4")
    item["file"]["appProperties"] = {"speaker_names": "[\"Alice\", \"Bob\"]"}
    main.process_item(service, item, "f", cfg)

    assert refine_mock.call_args.kwargs["speaker_names"] == ["Alice", "Bob"]
