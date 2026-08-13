from __future__ import annotations

import logging
import ssl
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from src import main
from src.auth import AuthError
from src.booking_gate import BookingDecision
from src.call_booking import CallBooking
from src.call_booking import append as append_booking
from src.config import Config, EmployeeFolder
from src.presets import BUILTIN_PRESETS, Preset
from src.preset_pipeline import PresetResult
from src.stt.base import STTError

_KEYPOINTS_BUILTIN = next(p for p in BUILTIN_PRESETS if p.name == "keypoints")


def _as_folders(entries) -> tuple[EmployeeFolder, ...]:
    """Accept ids or EmployeeFolders, so folder-agnostic tests stay short."""
    return tuple(
        entry if isinstance(entry, EmployeeFolder) else EmployeeFolder(entry)
        for entry in entries
    )


def make_config(
    folders=None,
    bitrate="96k",
    poll_interval=600,
    data_dir=Path("data"),
    stt_provider="",
    openai_api_key="",
    deepgram_api_key="",
    stt_language="",
    deepgram_audio_source="m4a_copy",
    drive_mp3_artifact=True,
    stt_postprocess=False,
    output_target="drive",
    output_dir=None,
    output_also_drive=False,
    stt_presets=("keypoints",),
    openai_keypoints=False,
    presets=None,
    tags_allowed=(),
    referrals_allowed=(),
    webhook_url="",
    webhook_token="",
    proxy_url="",
) -> Config:
    if presets is None:
        # Mirror the legacy keypoints gate: the built-in keypoints pass is the only
        # enabled preset when requested, and none otherwise. Deliberately not the
        # whole BUILTIN_PRESETS tuple — `meta` is a built-in too, and pulling it in
        # here would silently widen every openai_keypoints=True test to two passes.
        presets = (_KEYPOINTS_BUILTIN,) if openai_keypoints else ()
    return Config(
        folders=_as_folders(folders if folders is not None else ["folderA"]),
        poll_interval=poll_interval,
        bitrate=bitrate,
        data_dir=data_dir,
        proxy_url=proxy_url,
        stt_provider=stt_provider,
        openai_api_key=openai_api_key,
        deepgram_api_key=deepgram_api_key,
        stt_language=stt_language,
        stt_postprocess=stt_postprocess,
        output_target=output_target,
        output_dir=output_dir,
        output_also_drive=output_also_drive,
        stt_presets=tuple(stt_presets),
        openai_keypoints=openai_keypoints,
        deepgram_audio_source=deepgram_audio_source,
        drive_mp3_artifact=drive_mp3_artifact,
        tags_allowed=tuple(tags_allowed),
        referrals_allowed=tuple(referrals_allowed),
        webhook_url=webhook_url,
        webhook_token=webhook_token,
        presets=tuple(presets),
    )


def _item(
    file_id="fid", name="video.mp4", *, has_mp3=False, has_txt=False,
    mp3_id=None, mp3_name=None, txt_id=None, keypoints_id=None,
    artifact_ids=None, size=None, stt_id=None, meta_yml_id=None,
):
    file_info = {"id": file_id, "name": name}
    if size is not None:
        file_info["size"] = str(size)
    ids = dict(artifact_ids or {})
    if keypoints_id is not None:
        ids.setdefault("keypoints", keypoints_id)
    return {
        "file": file_info,
        "has_mp3": has_mp3,
        "has_txt": has_txt,
        "mp3_id": mp3_id,
        "mp3_name": mp3_name,
        "txt_id": txt_id,
        "stt_id": stt_id,
        "meta_yml_id": meta_yml_id,
        "artifact_ids": ids,
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

    def fake_download(service_arg, file_id, dest_dir, name, *, expected_size_bytes=None):
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
    m4a_path = tmp_path / "video.m4a"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch(
        "src.main.transcribe_file", return_value="hello world"
    )

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")
    main.process_item(service, _item("fid", "video.mp4"), "f", cfg)

    transcribe_mock.assert_called_once_with(m4a_path, cfg, cost_usd={})
    # Four uploads: mp3, txt, and the meta.yml/.stt written for every processed
    # recording.
    assert upload_mock.call_count == 4
    second_call = upload_mock.call_args_list[1]
    assert second_call.kwargs["mime_type"] == "text/plain"
    txt_path = second_call.args[1]
    assert txt_path.name == "video.txt"


def test_process_item_does_not_upload_blank_txt_when_transcript_is_empty(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.extract_m4a_copy", return_value=tmp_path / "video.m4a")
    upload_mock = mocker.patch("src.main.drive.upload")
    mocker.patch(
        "src.main.transcribe_file",
        side_effect=STTError("deepgram returned an empty transcript for video.m4a"),
    )

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")

    with pytest.raises(STTError, match="empty transcript"):
        main.process_item(service, _item("fid", "video.mp4"), "f", cfg)

    assert upload_mock.call_count == 1
    assert upload_mock.call_args.kwargs["mime_type"] == "audio/mpeg"


def test_process_item_only_stt_when_mp3_already_exists(mocker, tmp_path):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name, *, expected_size_bytes=None):
        path = dest_dir / name
        path.write_bytes(b"x")
        return path

    download_mock = mocker.patch("src.main.drive.download", side_effect=fake_download)
    m4a_path = tmp_path / "video.m4a"
    m4a_mock = mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    extract_mock = mocker.patch("src.main.extract_mp3")
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch(
        "src.main.transcribe_file", return_value="text"
    )

    # mp3 artifact already exists, but Deepgram never reuses it: it re-derives
    # audio from the source mp4 and uploads only the new .txt (plus the
    # meta.yml/.stt written for every processed recording).
    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")
    item = _item(
        "fid", "video.mp4", has_mp3=True, mp3_id="mp3id", mp3_name="video.mp3",
    )
    main.process_item(service, item, "folderX", cfg)

    extract_mock.assert_not_called()
    m4a_mock.assert_called_once()
    download_mock.assert_called_once()
    args, _ = download_mock.call_args
    assert args[1] == "fid"
    assert args[3] == "video.mp4"
    transcribe_mock.assert_called_once_with(m4a_path, cfg, cost_usd={})
    assert upload_mock.call_count == 3
    first_call = upload_mock.call_args_list[0]
    assert first_call.kwargs["mime_type"] == "text/plain"
    assert first_call.kwargs["name"] == "video.txt"


def test_process_item_passes_expected_source_size_to_download(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    m4a_path = tmp_path / "video.m4a"

    download_mock = mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="text")

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x", drive_mp3_artifact=False)
    item = _item(
        "fid",
        "video.mp4",
        has_mp3=True,
        has_txt=False,
        mp3_id="mp3id",
        mp3_name="video.mp3",
        size=456,
    )

    main.process_item(service, item, "folderX", cfg)

    assert download_mock.call_args.args[:4] == (
        service,
        "fid",
        download_mock.call_args.args[2],
        "video.mp4",
    )
    assert download_mock.call_args.kwargs == {"expected_size_bytes": 456}


def test_process_item_extracts_temporary_audio_when_artifact_upload_is_disabled(
    mocker,
    tmp_path,
):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    download_mock = mocker.patch("src.main.drive.download", return_value=mp4_path)
    extract_mock = mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    transcribe_mock = mocker.patch("src.main.transcribe_file", return_value="text")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        drive_mp3_artifact=False,
    )

    telemetry = main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    download_mock.assert_called_once()
    assert download_mock.call_args.args[1] == "fid"
    extract_mock.assert_called_once_with(mp4_path, bitrate="96k")
    transcribe_mock.assert_called_once_with(mp3_path, cfg, cost_usd={})
    # txt, plus the meta.yml/.stt written for every processed recording.
    assert upload_mock.call_count == 3
    first_call = upload_mock.call_args_list[0]
    assert first_call.kwargs["mime_type"] == "text/plain"
    assert first_call.kwargs["name"] == "video.txt"
    assert telemetry.mp3_uploaded is False
    assert telemetry.txt_uploaded is True


def test_process_item_deepgram_m4a_downloads_mp4_even_when_mp3_exists(
    mocker,
    tmp_path,
):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name, *, expected_size_bytes=None):
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
    transcribe_mock.assert_called_once_with(m4a_path, cfg, cost_usd={})
    # txt, plus the meta.yml/.stt written for every processed recording.
    assert upload_mock.call_count == 3
    assert upload_mock.call_args_list[0].args[1].name == "video.txt"


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
    # txt, plus the meta.yml/.stt written for every processed recording.
    assert upload_mock.call_count == 3
    assert upload_mock.call_args_list[0].kwargs["mime_type"] == "text/plain"


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
    # mp3, txt, plus the meta.yml/.stt written for every processed recording.
    assert upload_mock.call_count == 4
    assert upload_mock.call_args_list[0].kwargs["mime_type"] == "audio/mpeg"
    assert upload_mock.call_args_list[1].kwargs["mime_type"] == "text/plain"


def test_process_item_deepgram_mp3_96k_extracts_mp4_for_stt(mocker, tmp_path):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name, *, expected_size_bytes=None):
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
    transcribe_mock.assert_called_once_with(mp3_path, cfg, cost_usd={})
    # txt, plus the meta.yml/.stt written for every processed recording.
    assert upload_mock.call_count == 3


def test_process_item_deepgram_mp3_192k_extracts_mp4_for_stt(mocker, tmp_path):
    service = MagicMock()

    def fake_download(svc, file_id, dest_dir, name, *, expected_size_bytes=None):
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
    transcribe_mock.assert_called_once_with(mp3_path, cfg, cost_usd={})
    # txt, plus the meta.yml/.stt written for every processed recording.
    assert upload_mock.call_count == 3


def test_process_item_skips_completely_when_mp3_and_txt_present(mocker):
    service = MagicMock()
    download = mocker.patch("src.main.drive.download")
    extract = mocker.patch("src.main.extract_mp3")
    upload = mocker.patch("src.main.drive.upload")
    transcribe = mocker.patch("src.main.transcribe_file")

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")
    item = _item("fid", "v.mp4", has_mp3=True, has_txt=True,
                 mp3_id="m", mp3_name="v.mp3")
    main.process_item(service, item, "f", cfg)

    download.assert_not_called()
    extract.assert_not_called()
    upload.assert_not_called()
    transcribe.assert_not_called()


def test_process_item_reprocess_txt_overwrites_existing_txt(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    m4a_path = tmp_path / "video.m4a"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3")
    mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    mocker.patch("src.main.drive.upload")
    update_mock = mocker.patch("src.main.drive.update_file")
    transcribe_mock = mocker.patch("src.main.transcribe_file", return_value="fresh")

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")
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


def test_process_target_retries_transient_metadata_error(mocker):
    service = MagicMock()
    cfg = make_config()

    meta_mock = mocker.patch(
        "src.main.drive.get_file_metadata",
        side_effect=[
            TimeoutError("temporary metadata timeout"),
            {
                "id": "v1",
                "name": "a.mp4",
                "mimeType": "video/mp4",
                "parents": ["folderA"],
            },
        ],
    )
    mocker.patch("src.main.time.sleep")
    items = [_item("v1", "a.mp4")]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    process_mock = mocker.patch("src.main.process_item")

    main.process_target(service, "v1", cfg)

    assert meta_mock.call_count == 2
    process_mock.assert_called_once()


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


def test_dry_run_surfaces_preset_only_work(mocker, caplog):
    service = MagicMock()
    cfg = _two_preset_config()

    mocker.patch(
        "src.main.drive.get_file_metadata",
        return_value={"id": "folderA", "mimeType": main.drive.FOLDER_MIME},
    )
    # mp3+txt already done; only the expertizeme-managers preset is missing, so a
    # real run would spend OpenAI credits even though mp3/txt are skipped.
    item = _item(
        "v1",
        "done.mp4",
        has_mp3=True,
        has_txt=True,
        txt_id="t1",
        artifact_ids={"transcript-cleanup": "c1", "keypoints": "k1"},
    )
    mocker.patch("src.main.drive.list_folder_state", return_value=[item])
    mocker.patch("src.main.process_item")

    with caplog.at_level("INFO"):
        main.process_target(service, "folderA", cfg, is_folder=True, dry_run=True)

    assert "DRY RUN" in caplog.text
    assert "mp3=skip, txt=skip" in caplog.text
    assert "presets=expertizeme-managers" in caplog.text


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


def test_run_once_iterates_all_folders_and_files(mocker):
    service = MagicMock()
    cfg = make_config(folders=["f1", "f2"])

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


def test_run_once_retries_transient_listing_error(mocker, caplog):
    service = MagicMock()
    cfg = make_config(folders=["f1"])

    list_mock = mocker.patch(
        "src.main.drive.list_folder_state",
        side_effect=[TimeoutError("temporary api timeout"), [_item("v1", "a.mp4")]],
    )
    sleep_mock = mocker.patch("src.main.time.sleep")
    process_mock = mocker.patch("src.main.process_item")
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with caplog.at_level("INFO"):
        main.run_once(service, cfg)

    assert list_mock.call_count == 2
    sleep_mock.assert_called_once()
    process_mock.assert_called_once()
    notify_mock.assert_not_called()
    assert "retry_total=1" in caplog.text


@pytest.mark.parametrize(
    "exc",
    [
        # httplib2 (what the Google API client uses) surfaces a dropped connection as a
        # builtin BrokenPipeError, not as one of requests' exceptions. Observed in
        # production: Drive closed a reused keep-alive socket and the whole cycle failed.
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(104, "Connection reset by peer"),
        ConnectionAbortedError(103, "Software caused connection abort"),
        ssl.SSLError("record layer failure"),
    ],
)
def test_transient_classifier_accepts_socket_level_errors(exc):
    assert main._is_transient_runtime_error(exc) is True


@pytest.mark.parametrize("exc", [RefreshError("token gone"), AuthError("token gone")])
def test_transient_classifier_still_rejects_auth_errors(exc):
    assert main._is_transient_runtime_error(exc) is False


def test_run_once_retries_broken_pipe_from_listing(mocker, caplog):
    service = MagicMock()
    cfg = make_config(folders=["f1"])

    list_mock = mocker.patch(
        "src.main.drive.list_folder_state",
        side_effect=[BrokenPipeError(32, "Broken pipe"), [_item("v1", "a.mp4")]],
    )
    sleep_mock = mocker.patch("src.main.time.sleep")
    process_mock = mocker.patch("src.main.process_item")
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with caplog.at_level("INFO"):
        main.run_once(service, cfg)

    assert list_mock.call_count == 2
    sleep_mock.assert_called_once()
    process_mock.assert_called_once()
    # A retried socket drop must stay silent: alerting on it trains the operator to
    # ignore the channel that carries the real failures.
    notify_mock.assert_not_called()
    assert "retry_total=1" in caplog.text


def test_run_once_propagates_auth_error_from_listing(mocker):
    service = MagicMock()
    cfg = make_config(folders=["f1"])

    mocker.patch(
        "src.main.drive.list_folder_state",
        side_effect=AuthError("token gone"),
    )

    with pytest.raises(AuthError, match="token gone"):
        main.run_once(service, cfg)


def test_run_once_propagates_auth_error_from_processing(mocker):
    service = MagicMock()
    cfg = make_config(folders=["f1"])

    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "a.mp4")],
    )
    mocker.patch(
        "src.main.process_item",
        side_effect=AuthError("token gone"),
    )

    with pytest.raises(AuthError, match="token gone"):
        main.run_once(service, cfg)


def test_run_once_dry_run_does_not_process_items(mocker, caplog):
    service = MagicMock()
    cfg = make_config(
        folders=["folderA"],
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
    assert "Cycle summary" in caplog.text


def test_run_once_skips_large_pending_items_without_confirmation(mocker, caplog):
    service = MagicMock()
    cfg = make_config(
        folders=["folderA"],
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
    cfg = make_config(folders=["f1"])

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
    cfg = make_config(folders=["f1"], stt_provider="deepgram", deepgram_api_key="dg-x")

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
    cfg = make_config(folders=["f1"])

    items = [
        _item("good1", "ok1.mp4"),
        _item("bad", "fail.mp4"),
        _item("good2", "ok2.mp4"),
    ]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)

    processed_ids = []

    def fake_process(svc, item, folder, c, *, booking_decision=None):
        if item["file"]["id"] == "bad":
            raise RuntimeError("ffmpeg failed")
        processed_ids.append(item["file"]["id"])

    mocker.patch("src.main.process_item", side_effect=fake_process)
    notify_mock = mocker.patch("src.main.notify.notify_error")

    main.run_once(service, cfg)

    assert processed_ids == ["good1", "good2"]
    notify_mock.assert_called_once()
    assert "fail.mp4" in notify_mock.call_args.args[0]


def test_process_item_retries_transient_download_error(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    download_mock = mocker.patch(
        "src.main.drive.download",
        side_effect=[TimeoutError("temporary download timeout"), mp4_path],
    )
    sleep_mock = mocker.patch("src.main.time.sleep")
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload", return_value={"id": "uploaded"})

    cfg = make_config(bitrate="128k")
    telemetry = main.process_item(service, _item("fid1", "video.mp4"), "folderA", cfg)

    assert download_mock.call_count == 2
    sleep_mock.assert_called_once()
    upload_mock.assert_called_once()
    assert telemetry.processing_mode == "artifact-only"
    assert telemetry.retry_count == 1


def test_process_item_retries_download_size_mismatch(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    download_mock = mocker.patch(
        "src.main.drive.download",
        side_effect=[
            main.drive.DownloadIntegrityError("Downloaded file size mismatch"),
            mp4_path,
        ],
    )
    sleep_mock = mocker.patch("src.main.time.sleep")
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload", return_value={"id": "uploaded"})

    cfg = make_config(bitrate="128k")
    main.process_item(service, _item("fid1", "video.mp4", size=123), "folderA", cfg)

    assert download_mock.call_count == 2
    assert download_mock.call_args_list[0].kwargs == {"expected_size_bytes": 123}
    sleep_mock.assert_called_once()
    upload_mock.assert_called_once()


def test_process_item_logs_process_summary(mocker, tmp_path, caplog):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload", return_value={"id": "uploaded"})
    mocker.patch("src.main.time.monotonic", side_effect=[10.0, 11.5])

    cfg = make_config(stt_provider="", drive_mp3_artifact=True)

    with caplog.at_level("INFO"):
        main.process_item(service, _item("fid1", "video.mp4"), "folderA", cfg)

    assert (
        "Process summary [file=video.mp4, file_id=fid1, folder=folderA, "
        "provider=artifact-only, processing_mode=artifact-only, outcome=success, "
        "retry_count=0, duration_s=1.500, cost_usd={}, usage={}]"
    ) in caplog.text


def test_process_item_logs_failed_summary(mocker, tmp_path, caplog):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.extract_m4a_copy", return_value=tmp_path / "video.m4a")
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", side_effect=STTError("provider failed"))
    mocker.patch("src.main.time.monotonic", side_effect=[20.0, 21.0])

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x")

    with caplog.at_level("INFO"):
        with pytest.raises(STTError, match="provider failed"):
            main.process_item(service, _item("fid1", "video.mp4"), "folderA", cfg)

    assert (
        "Process summary [file=video.mp4, file_id=fid1, folder=folderA, "
        "provider=deepgram, processing_mode=artifact-and-txt, outcome=failed, "
        "retry_count=0, duration_s=1.000, cost_usd={}, usage={}]"
    ) in caplog.text


def test_process_item_logs_txt_only_processing_mode(mocker, tmp_path, caplog):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    m4a_path = tmp_path / "video.m4a"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_m4a_copy", return_value=m4a_path)
    mocker.patch("src.main.transcribe_file", return_value="hello")
    mocker.patch("src.main.drive.upload", return_value={"id": "uploaded"})
    mocker.patch("src.main.time.monotonic", side_effect=[30.0, 31.25])

    cfg = make_config(stt_provider="deepgram", deepgram_api_key="dg-x", drive_mp3_artifact=True)

    with caplog.at_level("INFO"):
        telemetry = main.process_item(
            service,
            _item("fid1", "video.mp4", has_mp3=True, has_txt=False, mp3_id="m1", mp3_name="video.mp3"),
            "folderA",
            cfg,
        )

    assert telemetry.processing_mode == "txt-only"
    assert (
        "Process summary [file=video.mp4, file_id=fid1, folder=folderA, "
        "provider=deepgram, processing_mode=txt-only, outcome=success, "
        "retry_count=0, duration_s=1.250, cost_usd={}, usage={}]"
    ) in caplog.text


def test_process_item_summary_surfaces_cost_and_keypoints_usage(mocker, tmp_path, caplog):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")

    def fake_transcribe(path, config, *, cost_usd):
        cost_usd["deepgram"] = 0.0123
        return "Speaker 1: hi"

    mocker.patch("src.main.transcribe_file", side_effect=fake_transcribe)
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "keypoints": PresetResult(
                name="keypoints",
                text="## Задачи\n- [ ] do it",
                usage={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
            )
        },
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
    )

    with caplog.at_level("INFO"):
        telemetry = main.process_item(
            service, _item("fid1", "video.mp4"), "folderA", cfg
        )

    # Telemetry carries the computed Deepgram spend and OpenAI keypoints usage.
    assert telemetry.cost_usd == {"deepgram": 0.0123}
    assert telemetry.usage == {
        "openai_keypoints": {
            "input_tokens": 100,
            "output_tokens": 40,
            "total_tokens": 140,
        }
    }
    # The summary log line surfaces them instead of discarding the spend.
    assert "cost_usd={'deepgram': 0.0123}" in caplog.text
    assert "'openai_keypoints': {'input_tokens': 100" in caplog.text


def test_run_once_continues_on_listing_error(mocker):
    service = MagicMock()
    cfg = make_config(folders=["bad_folder", "good_folder"])

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
    cfg = make_config(folders=[])

    list_mock = mocker.patch("src.main.drive.list_folder_state")
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    list_mock.assert_not_called()
    process_mock.assert_not_called()


def test_run_once_passes_config_to_process(mocker):
    service = MagicMock()
    cfg = make_config(folders=["f1"], bitrate="192k")

    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "a.mp4")],
    )
    process_mock = mocker.patch("src.main.process_item")

    main.run_once(service, cfg)

    assert process_mock.call_args.args[3] is cfg


def test_run_once_logs_folder_and_cycle_summary(mocker, caplog):
    service = MagicMock()
    cfg = make_config(folders=["f1"], stt_provider="deepgram", deepgram_api_key="dg-x")

    items = [
        _item("v1", "a.mp4", has_mp3=True, has_txt=False, mp3_id="m1", mp3_name="a.mp3"),
        _item("v2", "b.mp4", has_mp3=True, has_txt=True, mp3_id="m2", mp3_name="b.mp3"),
    ]
    mocker.patch("src.main.drive.list_folder_state", return_value=items)
    mocker.patch("src.main.process_item")
    mocker.patch("src.main.time.monotonic", side_effect=[100.0, 101.25])

    with caplog.at_level("INFO"):
        main.run_once(service, cfg)

    assert "Folder f1 summary [total=2, pending=1, skipped_size=0, dry_run=False]" in caplog.text
    assert (
        "Cycle summary [provider=deepgram, outcome=success, folders=1, pending=1, "
        "processed=1, failed=0, retry_total=0, skipped_size=0, skipped_unmatched=0, "
        "folder_errors=0, dry_run=False, duration_s=1.250]"
    ) in caplog.text


def test_run_once_aggregates_retry_total_from_process_telemetry(mocker, caplog):
    service = MagicMock()
    cfg = make_config(folders=["f1"], stt_provider="deepgram", deepgram_api_key="dg-x")

    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("v1", "a.mp4", has_mp3=True, has_txt=False, mp3_id="m1", mp3_name="a.mp3")],
    )
    mocker.patch(
        "src.main.process_item",
        return_value=main._ProcessTelemetry(
            provider="openai",
            processing_mode="txt-only",
            retry_count=2,
            duration_s=0.5,
        ),
    )
    mocker.patch("src.main.time.monotonic", side_effect=[200.0, 201.0])

    with caplog.at_level("INFO"):
        main.run_once(service, cfg)

    assert "retry_total=2" in caplog.text


def test_main_runs_loop_and_sleeps(mocker):
    cfg = make_config(folders=["f1"], poll_interval=42)
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
    cfg = make_config(folders=["f1"])

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
    cfg = make_config(folders=["f1"])

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
    cfg = make_config(folders=["f1"], poll_interval=1)
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
    cfg = make_config(folders=["f1"], poll_interval=1)
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
    cfg = make_config(folders=["f1"], poll_interval=1)
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
    cfg = make_config(folders=["f1"], poll_interval=1)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())
    mocker.patch("src.main.run_once", side_effect=AuthError("token gone"))
    notify_mock = mocker.patch("src.main.notify.notify_error")

    with pytest.raises(SystemExit) as excinfo:
        main.main()

    assert excinfo.value.code == 1
    notify_mock.assert_called_once()


def test_main_notifies_on_cycle_exception(mocker):
    cfg = make_config(folders=["f1"], poll_interval=1)
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
    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
    )

    captured = {}

    def fake_download(svc, file_id, dest_dir, name, *, expected_size_bytes=None):
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

    main._save_and_upload_txt(
        service, "fid", "video.mp4", "hello", "folderA", tmp_path, make_config(),
    )

    update_mock.assert_not_called()
    upload_mock.assert_called_once()
    assert upload_mock.call_args.kwargs["name"] == "video.txt"


def test_save_and_upload_txt_overwrites_existing(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.main.drive.upload")
    update_mock = mocker.patch("src.main.drive.update_file")

    main._save_and_upload_txt(
        service, "fid", "video.mp4", "final text", "folderA", tmp_path, make_config(),
        txt_id="t1",
    )

    upload_mock.assert_not_called()
    update_mock.assert_called_once()
    args = update_mock.call_args.args
    assert args[1] == "t1"
    assert args[2].read_text(encoding="utf-8") == "final text"


def test_process_item_writes_txt_to_local_folder_when_output_target_folder(
    mocker,
    tmp_path,
):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"
    out_dir = tmp_path / "transcripts"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        drive_mp3_artifact=False,
        output_target="folder",
        output_dir=out_dir,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    # No txt upload to Drive; the transcript landed in the local folder instead.
    upload_mock.assert_not_called()
    assert (out_dir / "video.txt").read_text(encoding="utf-8") == "Speaker 1: hi"


def test_process_item_postprocesses_transcript_before_upload(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        # `.meta.yml`/`.stt` share this mime type too, so match on the `.txt`
        # suffix specifically rather than on mime_type alone.
        if name and name.endswith(".txt"):
            captured["txt"] = local_path.read_text(encoding="utf-8")

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch(
        "src.main.transcribe_file",
        return_value="Speaker 1: hi there\nSpeaker 2: hello back",
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        stt_postprocess=True,
    )
    main.process_item(service, _item("fid", "Alice and Bob.mp4"), "f", cfg)

    assert captured["txt"] == "Alice: hi there\nBob: hello back"


def test_process_item_generates_keypoints_when_enabled(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured: dict = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        captured.setdefault("uploads", []).append(
            (name, local_path.read_text(encoding="utf-8"), mime_type, app_properties)
        )

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    kp_mock = mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "keypoints": PresetResult(
                name="keypoints",
                text="## Задачи\n\n## Тезисы\n- point\n\n## Открытые вопросы",
            )
        },
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    kp_mock.assert_called_once()
    # The preset DAG runs on the produced transcript, restricted to the missing
    # keypoints preset.
    assert kp_mock.call_args.args[0] == "Speaker 1: hi"
    assert kp_mock.call_args.kwargs["only"] == ["keypoints"]
    uploads = {
        name: (text, mime, props) for name, text, mime, props in captured["uploads"]
    }
    assert "video.keypoints.md" in uploads
    text, mime, props = uploads["video.keypoints.md"]
    assert "## Задачи" in text
    assert mime == "text/markdown"
    assert props == {"source_video_id": "fid", "artifact_type": "keypoints"}


def test_process_item_overwrites_existing_keypoints_on_reprocess(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    update_mock = mocker.patch("src.main.drive.update_file")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "keypoints": PresetResult(
                name="keypoints", text="## Задачи\n- [ ] do it"
            )
        },
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
    )
    item = _item(
        "fid", "video.mp4", has_txt=True, txt_id="t1", keypoints_id="k1",
        stt_id="s1", meta_yml_id="y1",
    )
    main.process_item(service, item, "folderX", cfg, reprocess_txt=True)

    # The .txt, the .keypoints.md, the .stt, and the .meta.yml siblings are all
    # overwritten in place, not re-uploaded as duplicates.
    update_ids = [call.args[1] for call in update_mock.call_args_list]
    assert "t1" in update_ids
    assert "k1" in update_ids
    assert "s1" in update_ids
    assert "y1" in update_ids
    upload_mock.assert_not_called()


def test_process_item_skips_keypoints_when_disabled(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    kp_mock = mocker.patch("src.main.preset_pipeline.run_presets")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=False,
        drive_mp3_artifact=False,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    kp_mock.assert_not_called()


def test_process_item_writes_keypoints_to_local_folder(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"
    out_dir = tmp_path / "transcripts"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    upload_mock = mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "keypoints": PresetResult(
                name="keypoints", text="## Задачи\n- [ ] do it"
            )
        },
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
        output_target="folder",
        output_dir=out_dir,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    upload_mock.assert_not_called()
    assert (out_dir / "video.txt").read_text(encoding="utf-8") == "Speaker 1: hi"
    assert (out_dir / "video.keypoints.md").read_text(encoding="utf-8") == (
        "## Задачи\n- [ ] do it"
    )


def test_apply_local_output_state_marks_has_txt_from_local_file(tmp_path):
    out_dir = tmp_path / "transcripts"
    out_dir.mkdir()
    (out_dir / "video.txt").write_text("done", encoding="utf-8")
    cfg = make_config(output_target="folder", output_dir=out_dir)

    items = [_item("fid", "video.mp4"), _item("gid", "other.mp4")]
    main._apply_local_output_state(items, cfg)

    assert items[0]["has_txt"] is True
    assert items[1]["has_txt"] is False


def test_apply_local_output_state_noop_for_drive_target(tmp_path):
    out_dir = tmp_path / "transcripts"
    out_dir.mkdir()
    (out_dir / "video.txt").write_text("done", encoding="utf-8")
    cfg = make_config(output_target="drive", output_dir=out_dir)

    items = [_item("fid", "video.mp4")]
    main._apply_local_output_state(items, cfg)

    assert items[0]["has_txt"] is False


def test_run_once_skips_already_transcribed_local_file_in_folder_mode(
    mocker, tmp_path
):
    out_dir = tmp_path / "transcripts"
    out_dir.mkdir()
    # A prior run already wrote the transcript locally.
    (out_dir / "video.txt").write_text("Speaker 1: hi", encoding="utf-8")

    service = MagicMock()
    # Drive has no .txt sibling because folder mode wrote it locally.
    mocker.patch(
        "src.main.drive.list_folder_state",
        return_value=[_item("fid", "video.mp4")],
    )
    process_mock = mocker.patch("src.main.process_item")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg",
        deepgram_audio_source="m4a_copy",
        drive_mp3_artifact=False,
        output_target="folder",
        output_dir=out_dir,
    )
    main.run_once(service, cfg)

    process_mock.assert_not_called()


def test_process_item_does_not_write_empty_keypoints(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured: dict = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        captured.setdefault("names", []).append(name)

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={"keypoints": PresetResult(name="keypoints", text="   \n  ")},
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
    )
    main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    # A blank keypoints doc is not uploaded, but the .txt and the meta.yml/.stt
    # written for every processed recording still are.
    assert captured["names"] == ["video.txt", "video.meta.yml", "video.stt"]


def test_process_item_uses_speaker_names_from_drive_properties(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        # `.meta.yml`/`.stt` share this mime type too, so match on the `.txt`
        # suffix specifically rather than on mime_type alone.
        if name and name.endswith(".txt"):
            captured["txt"] = local_path.read_text(encoding="utf-8")

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch(
        "src.main.transcribe_file",
        return_value="Speaker 1: hi there\nSpeaker 2: hello back",
    )

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        stt_postprocess=True,
    )
    item = _item("fid", "Unhelpful file name.mp4")
    item["file"]["appProperties"] = {"speaker_names": "[\"Alice\", \"Bob\"]"}
    main.process_item(service, item, "f", cfg)

    assert captured["txt"] == "Alice: hi there\nBob: hello back"


def _two_preset_config(**overrides):
    presets = (
        Preset(name="transcript-cleanup", instructions="clean it"),
        Preset(
            name="keypoints",
            instructions="summarize",
            artifact_suffix=".keypoints.md",
            depends_on=("transcript-cleanup",),
        ),
        Preset(
            name="expertizeme-managers",
            instructions="managers",
            depends_on=("transcript-cleanup",),
        ),
    )
    base = dict(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
        presets=presets,
    )
    base.update(overrides)
    return make_config(**base)


def test_process_item_writes_one_artifact_per_produced_preset(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured: dict = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        captured.setdefault("uploads", []).append(
            (name, local_path.read_text(encoding="utf-8"), mime_type, app_properties)
        )

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="cleaned transcript"
            ),
            "keypoints": PresetResult(name="keypoints", text="## Задачи\n- [ ] do it"),
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", text="manager notes"
            ),
        },
    )

    main.process_item(service, _item("fid", "video.mp4"), "folderX", _two_preset_config())

    uploads = {
        name: (text, mime, props) for name, text, mime, props in captured["uploads"]
    }
    # The .txt plus one sibling per produced preset, each tagged with its own
    # artifact_type and using its own suffix.
    assert "video.txt" in uploads
    assert uploads["video.transcript-cleanup.md"][2] == {
        "source_video_id": "fid",
        "artifact_type": "transcript-cleanup",
    }
    assert uploads["video.keypoints.md"][2] == {
        "source_video_id": "fid",
        "artifact_type": "keypoints",
    }
    assert uploads["video.expertizeme-managers.md"][2] == {
        "source_video_id": "fid",
        "artifact_type": "expertizeme-managers",
    }


def test_process_item_skips_presets_with_existing_artifacts(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    download_text = mocker.patch(
        "src.main.drive.download_text", return_value="cleaned"
    )
    run_mock = mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", text="notes"
            ),
        },
    )

    # transcript-cleanup and keypoints already have artifacts; only
    # expertizeme-managers is missing and must be requested. Its dependency
    # transcript-cleanup is reused from its persisted artifact (download_text)
    # instead of being re-run.
    item = _item(
        "fid",
        "video.mp4",
        artifact_ids={"transcript-cleanup": "c1", "keypoints": "k1"},
    )
    config = _two_preset_config(webhook_url="https://hook.example/x")
    main.process_item(service, item, "folderX", config)

    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["only"] == ["expertizeme-managers"]
    assert run_mock.call_args.kwargs["precomputed"] == {"transcript-cleanup": "cleaned"}
    # c1 feeds the dependency; k1 is read back only so the webhook payload carries
    # the keypoints produced on an earlier cycle. Neither preset is re-run.
    assert download_text.call_count == 2
    download_text.assert_any_call(service, "c1")
    download_text.assert_any_call(service, "k1")


def test_process_item_skips_webhook_backfill_when_no_webhook_configured(
    mocker, tmp_path
):
    """Without a receiver, don't pay a Drive read per earlier-cycle artifact.

    The backfill's only consumer is the completion webhook, which no-ops on a blank
    URL — so reading ``k1`` back would be a round-trip whose result is discarded.
    The dependency read (``c1``) still happens: it feeds the preset that is re-run.
    """
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    download_text = mocker.patch(
        "src.main.drive.download_text", return_value="cleaned"
    )
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", text="notes"
            ),
        },
    )

    item = _item(
        "fid",
        "video.mp4",
        artifact_ids={"transcript-cleanup": "c1", "keypoints": "k1"},
    )
    main.process_item(service, item, "folderX", _two_preset_config(webhook_url=""))

    download_text.assert_called_once_with(service, "c1")


def test_process_item_skips_preset_stage_when_all_present(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    run_mock = mocker.patch("src.main.preset_pipeline.run_presets")

    item = _item(
        "fid",
        "video.mp4",
        artifact_ids={
            "transcript-cleanup": "c1",
            "keypoints": "k1",
            "expertizeme-managers": "e1",
        },
    )
    main.process_item(service, item, "folderX", _two_preset_config())

    run_mock.assert_not_called()


def test_process_item_backfills_webhook_when_every_preset_already_present(
    mocker, tmp_path
):
    """A regenerated `.txt` still ships the earlier cycle's artifacts to the receiver.

    When a file's `.txt` sibling is deleted but its preset artifacts survive, the
    file is re-selected and re-transcribed, yet no preset is missing — so the stage
    runs nothing. The webhook fires regardless (the `.txt` was uploaded), so it must
    still carry the artifacts sitting on Drive rather than an empty map.
    """
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch("src.main.drive.download_text", side_effect=lambda svc, fid: fid)
    run_mock = mocker.patch("src.main.preset_pipeline.run_presets")

    item = _item(
        "fid",
        "video.mp4",
        artifact_ids={
            "transcript-cleanup": "c1",
            "keypoints": "k1",
            "expertizeme-managers": "e1",
        },
    )
    config = _two_preset_config(webhook_url="https://hook.example/x")
    telemetry = main.process_item(service, item, "folderX", config)

    run_mock.assert_not_called()
    assert telemetry is not None
    assert telemetry.artifacts == {
        "transcript-cleanup": "c1",
        "keypoints": "k1",
        "expertizeme-managers": "e1",
    }


def test_process_item_survives_backfill_read_failure(mocker, tmp_path):
    """A failed backfill read degrades the payload instead of failing the file.

    The backfill's reads exist only to enrich the webhook, and they run after every
    artifact is already persisted. If a Drive read raised out of the stage, a file
    that fully succeeded would be counted failed and alerted on — and it would never
    reach the webhook at all, since the next cycle finds no preset missing.
    """
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")

    def flaky_download(svc, fid):
        if fid == "k1":
            raise RuntimeError("drive 404")
        return fid

    mocker.patch("src.main.drive.download_text", side_effect=flaky_download)
    notify = mocker.patch("src.main.webhook.notify_complete")

    item = _item(
        "fid",
        "video.mp4",
        artifact_ids={
            "transcript-cleanup": "c1",
            "keypoints": "k1",
            "expertizeme-managers": "e1",
        },
    )
    config = _two_preset_config(webhook_url="https://hook.example/x")
    telemetry = main.process_item(service, item, "folderX", config)

    # The unreadable preset drops out; the rest still reach the receiver.
    assert telemetry is not None
    assert telemetry.artifacts == {
        "transcript-cleanup": "c1",
        "expertizeme-managers": "e1",
    }
    notify.assert_called_once()


def test_process_item_raises_aggregated_error_but_persists_successes(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    captured: dict = {}

    def fake_upload(svc, local_path, folder, mime_type, name=None, app_properties=None):
        captured.setdefault("names", []).append(name)

    mocker.patch("src.main.drive.upload", side_effect=fake_upload)
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="cleaned"
            ),
            "keypoints": PresetResult(name="keypoints", text="## Задачи"),
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", error="boom"
            ),
        },
    )

    with pytest.raises(RuntimeError, match="preset DAG had failures"):
        main.process_item(service, _item("fid", "video.mp4"), "folderX", _two_preset_config())

    # The successful presets' artifacts were written before the error surfaced.
    assert "video.transcript-cleanup.md" in captured["names"]
    assert "video.keypoints.md" in captured["names"]
    assert "video.expertizeme-managers.md" not in captured["names"]


def test_process_item_reprocesses_missing_presets_from_existing_drive_txt(mocker, tmp_path):
    service = MagicMock()
    download_text = mocker.patch(
        "src.main.drive.download_text", return_value="existing transcript"
    )
    transcribe = mocker.patch("src.main.transcribe_file")
    mocker.patch("src.main.drive.upload")
    run_mock = mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", text="notes"
            ),
        },
    )

    # The .txt plus transcript-cleanup and keypoints already exist on Drive; only
    # expertizeme-managers is missing. It must be regenerated by re-feeding the
    # existing transcript, without re-running STT, and its dependency
    # transcript-cleanup is reused from its artifact rather than re-run.
    item = _item(
        "fid",
        "video.mp4",
        has_txt=True,
        txt_id="t1",
        artifact_ids={"transcript-cleanup": "c1", "keypoints": "k1"},
    )
    config = _two_preset_config(webhook_url="https://hook.example/x")
    main.process_item(service, item, "folderX", config)

    transcribe.assert_not_called()
    # t1 = the existing transcript; c1 = the reused transcript-cleanup artifact;
    # k1 = the earlier keypoints, read back so the webhook payload is complete
    # (the k1 read is why this config carries a webhook.url).
    assert download_text.call_count == 3
    download_text.assert_any_call(service, "t1")
    download_text.assert_any_call(service, "c1")
    download_text.assert_any_call(service, "k1")
    run_mock.assert_called_once()
    assert run_mock.call_args.args[0] == "existing transcript"
    assert run_mock.call_args.kwargs["only"] == ["expertizeme-managers"]
    assert run_mock.call_args.kwargs["precomputed"] == {
        "transcript-cleanup": "existing transcript"
    }


def test_process_item_skips_preset_reprocess_without_drive_txt(mocker):
    service = MagicMock()
    download_text = mocker.patch("src.main.drive.download_text")
    run_mock = mocker.patch("src.main.preset_pipeline.run_presets")
    transcribe = mocker.patch("src.main.transcribe_file")

    # Folder-mode style: the transcript exists locally (has_txt) but there is no
    # Drive .txt sibling (txt_id is None), so the preset stage must not reprocess
    # (artifact_ids is not tracked for local files and would loop forever).
    item = _item("fid", "video.mp4", has_txt=True, txt_id=None, artifact_ids={})
    result = main.process_item(service, item, "folderX", _two_preset_config())

    assert result is None
    download_text.assert_not_called()
    run_mock.assert_not_called()
    transcribe.assert_not_called()


def test_preset_only_reprocess_without_transcript_does_not_run_stt(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"
    mp3_path = tmp_path / "video.mp3"
    download = mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=mp3_path)
    transcribe = mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    run_mock = mocker.patch("src.main.preset_pipeline.run_presets")

    result = main.process_item(
        service,
        _item("fid", "video.mp4", has_txt=False, txt_id=None),
        "folderX",
        _two_preset_config(drive_mp3_artifact=False),
        reprocess_presets=["keypoints"],
    )

    assert result is None
    download.assert_not_called()
    transcribe.assert_not_called()
    run_mock.assert_not_called()


def test_apply_local_output_state_tracks_local_txt_and_preset_artifacts(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "video.txt").write_text("local transcript", encoding="utf-8")
    (out / "video.transcript-cleanup.md").write_text("cleaned", encoding="utf-8")
    cfg = _two_preset_config(output_target="folder", output_dir=out)
    item = _item("fid", "video.mp4", has_txt=False, txt_id=None, artifact_ids={})

    main._apply_local_output_state([item], cfg)

    assert item["has_txt"] is True
    assert item["local_txt_path"] == out / "video.txt"
    assert item["local_artifact_paths"] == {
        "transcript-cleanup": out / "video.transcript-cleanup.md"
    }


def test_process_item_reprocesses_presets_from_local_folder_transcript(mocker, tmp_path):
    service = MagicMock()
    out = tmp_path / "out"
    out.mkdir()
    local_txt = out / "video.txt"
    local_txt.write_text("existing local transcript", encoding="utf-8")
    mocker.patch("src.main.transcribe_file")
    mocker.patch("src.main.drive.download_text")
    run_mock = mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="cleaned"
            ),
            "keypoints": PresetResult(name="keypoints", text="notes"),
        },
    )

    item = _item("fid", "video.mp4", has_txt=True, txt_id=None, artifact_ids={})
    item["local_txt_path"] = local_txt
    cfg = _two_preset_config(output_target="folder", output_dir=out)
    result = main.process_item(
        service,
        item,
        "folderX",
        cfg,
        reprocess_presets=["keypoints"],
    )

    assert result is not None
    assert run_mock.call_args.args[0] == "existing local transcript"
    assert (out / "video.transcript-cleanup.md").read_text(encoding="utf-8") == "cleaned"
    assert (out / "video.keypoints.md").read_text(encoding="utf-8") == "notes"


def test_dry_run_preset_names_expands_missing_dependencies_for_reprocess():
    cfg = _two_preset_config()
    item = _item("fid", "video.mp4", has_txt=True, txt_id="txt-1", artifact_ids={})

    names = main._dry_run_preset_names(
        item,
        cfg,
        needs_txt=False,
        reprocess_txt=False,
        reprocess_presets=["keypoints"],
    )

    assert names == ["transcript-cleanup", "keypoints"]


def test_pending_items_includes_drive_txt_with_missing_preset():
    cfg = _two_preset_config()
    done = _item(
        "v1", "a.mp4", has_txt=True, txt_id="t1",
        artifact_ids={
            "transcript-cleanup": "c1", "keypoints": "k1", "expertizeme-managers": "e1",
        },
    )
    missing = _item(
        "v2", "b.mp4", has_txt=True, txt_id="t2",
        artifact_ids={"transcript-cleanup": "c2"},
    )
    folder_local = _item("v3", "c.mp4", has_txt=True, txt_id=None, artifact_ids={})

    pending = main._pending_items([done, missing, folder_local], cfg)

    assert [item["file"]["id"] for item in pending] == ["v2"]


# --- run loop stop flag (gdstt stop) ----------------------------------------

class _LoopStop(Exception):
    """Sentinel raised from a patched time.sleep to break the polling loop."""


def test_main_loop_idles_while_run_disabled_without_running(mocker):
    # `gdstt stop` keeps the loop alive but idle: run_once is never called while
    # run.enabled is false. The container stays up (no break/exit) so a Docker
    # `restart: unless-stopped` policy does not auto-resume processing.
    cfg = make_config(folders=["f1"], poll_interval=7)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())
    mocker.patch("src.main.is_run_enabled", return_value=False)
    once = mocker.patch("src.main.run_once")
    # Break the otherwise-infinite idle loop after a couple of sleeps.
    sleep = mocker.patch("src.main.time.sleep", side_effect=[None, _LoopStop()])

    with pytest.raises(_LoopStop):
        main.main()

    once.assert_not_called()
    assert sleep.call_args_list == [mocker.call(7), mocker.call(7)]


def test_main_loop_runs_while_enabled_and_idles_when_disabled(mocker):
    # Enabled twice (two cycles), then disabled (idle). run_once is called only
    # while enabled; once disabled the loop idles instead of exiting.
    cfg = make_config(folders=["f1"], poll_interval=5)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())
    mocker.patch("src.main.is_run_enabled", side_effect=[True, True, False])
    once = mocker.patch("src.main.run_once")
    sleep = mocker.patch("src.main.time.sleep", side_effect=[None, None, _LoopStop()])

    with pytest.raises(_LoopStop):
        main.main()

    assert once.call_count == 2
    assert sleep.call_count == 3


def test_main_does_not_enable_run_on_startup(mocker):
    # main() must not auto-enable run.enabled, so a sticky `gdstt stop` survives a
    # container restart instead of resuming on the next boot.
    import dataclasses

    cfg = dataclasses.replace(make_config(folders=["f1"]), run_enabled=False)
    mocker.patch("src.main.load_config", return_value=cfg)
    mocker.patch("src.main.build_drive_service", return_value=MagicMock())
    mocker.patch("src.main.is_run_enabled", return_value=False)
    mocker.patch("src.main.run_once")
    mocker.patch("src.main.time.sleep", side_effect=_LoopStop())
    set_enabled = mocker.patch(
        "src.config.set_run_enabled", side_effect=AssertionError("must not be called")
    )

    with pytest.raises(_LoopStop):
        main.main()

    set_enabled.assert_not_called()


def test_run_preset_stage_forces_only_selected(mocker):
    presets = (
        Preset(name="transcript-cleanup", instructions="c"),
        Preset(name="keypoints", instructions="k", depends_on=("transcript-cleanup",)),
        Preset(name="action-items", instructions="a", depends_on=("transcript-cleanup",)),
    )
    cfg = make_config(presets=presets)
    run_presets = mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "action-items": PresetResult(name="action-items", text="out"),
        },
    )
    mocker.patch("src.main._save_and_upload_preset")
    # transcript-cleanup artifact already exists -> reused as a precomputed dependency.
    mocker.patch("src.main._call_with_transient_retries", return_value="cleanup text")

    main._run_preset_stage(
        MagicMock(),
        "file-1",
        "Alice and Bob.mp4",
        "Speaker 1: hi",
        "folderA",
        Path("/tmp"),
        cfg,
        speaker_names=None,
        artifact_ids={"transcript-cleanup": "tc-id"},
        reprocess=False,
        only_presets=["action-items"],
        usage={},
        unproduced=set(),
    )

    kwargs = run_presets.call_args.kwargs
    assert kwargs["only"] == ["action-items"]
    assert kwargs["precomputed"] == {"transcript-cleanup": "cleanup text"}


def test_process_item_telemetry_carries_preset_artifacts(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="Alice: hi"
            ),
            "keypoints": PresetResult(name="keypoints", text="## Задачи"),
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", text="notes"
            ),
        },
    )

    telemetry = main.process_item(
        service, _item("fid", "video.mp4"), "folderX", _two_preset_config()
    )

    assert telemetry is not None
    assert telemetry.transcript == "Speaker 1: hi"
    assert telemetry.artifacts == {
        "transcript-cleanup": "Alice: hi",
        "keypoints": "## Задачи",
        "expertizeme-managers": "notes",
    }


def test_process_item_telemetry_omits_empty_preset_artifacts(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="Alice: hi"
            ),
            "keypoints": PresetResult(name="keypoints", text="   "),
        },
    )

    telemetry = main.process_item(
        service, _item("fid", "video.mp4"), "folderX", _two_preset_config()
    )

    assert telemetry is not None
    assert telemetry.artifacts == {"transcript-cleanup": "Alice: hi"}


def test_process_item_telemetry_artifacts_empty_without_presets(mocker, tmp_path):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")

    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        drive_mp3_artifact=False,
    )
    telemetry = main.process_item(service, _item("fid", "video.mp4"), "folderX", cfg)

    assert telemetry is not None
    assert telemetry.artifacts == {}
    assert telemetry.transcript == "Speaker 1: hi"


def test_process_item_telemetry_carries_artifacts_on_preset_refeed(mocker, tmp_path):
    """Only ``expertizeme-managers`` is missing, so it alone is re-run — but the
    webhook fires once per file, so the presets that succeeded on an earlier cycle
    must be read back from their artifacts and reach the receiver too."""
    service = MagicMock()
    mocker.patch(
        "src.main.drive.download_text",
        side_effect=lambda svc, file_id: {
            "t1": "existing transcript",
            "c1": "Alice: hi",
            "k1": "earlier keypoints",
        }[file_id],
    )
    mocker.patch("src.main.transcribe_file")
    mocker.patch("src.main.drive.upload")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="Alice: hi"
            ),
            "expertizeme-managers": PresetResult(
                name="expertizeme-managers", text="notes"
            ),
        },
    )

    item = _item(
        "fid",
        "video.mp4",
        has_txt=True,
        txt_id="t1",
        artifact_ids={"transcript-cleanup": "c1", "keypoints": "k1"},
    )
    # The backfill exists only to feed the receiver, so it is gated on a configured
    # webhook — this test asserts the backfill, hence the URL.
    config = _two_preset_config(webhook_url="https://hook.example/x")
    telemetry = main.process_item(service, item, "folderX", config)

    assert telemetry is not None
    # The re-fed transcript, not a fresh STT pass.
    assert telemetry.transcript == "existing transcript"
    assert telemetry.artifacts == {
        "transcript-cleanup": "Alice: hi",
        "expertizeme-managers": "notes",
        "keypoints": "earlier keypoints",
    }


# --- completion webhook ------------------------------------------------------


_META_ARTIFACT = (
    "---\n"
    "subject: Консультация по визе O-1\n"
    "tags: [O-1, клиентская-консультация, invented-tag]\n"
    "referral: рекомендация\n"
    "referral_note: Посоветовала знакомая\n"
    "---\n"
)


def _webhook_config(**overrides):
    """A two-preset config whose DAG also produces a `meta` artifact."""
    presets = (
        Preset(name="transcript-cleanup", instructions="clean it"),
        Preset(
            name="keypoints",
            instructions="summarize",
            artifact_suffix=".keypoints.md",
            depends_on=("transcript-cleanup",),
        ),
        Preset(
            name="meta",
            instructions="subject, tags, and referral",
            artifact_suffix=".meta.md",
            depends_on=("transcript-cleanup",),
        ),
    )
    base = dict(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        deepgram_audio_source="mp3_96k",
        openai_api_key="sk-x",
        openai_keypoints=True,
        drive_mp3_artifact=False,
        presets=presets,
        folders=[
            EmployeeFolder("folderX", name="Олег Иванов", email="oleg@expertizeme.org")
        ],
        tags_allowed=("O-1", "клиентская-консультация"),
        referrals_allowed=("рекомендация",),
        webhook_url="https://example.com/hooks/gdstt",
        webhook_token="secret",
    )
    base.update(overrides)
    return make_config(**base)


def _mock_successful_run(mocker, tmp_path, *, meta_text=_META_ARTIFACT):
    mocker.patch("src.main.drive.download", return_value=tmp_path / "video.mp4")
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: hi")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="Ольга: привет"
            ),
            "keypoints": PresetResult(name="keypoints", text="## Задачи"),
            "meta": PresetResult(name="meta", text=meta_text),
        },
    )
    return mocker.patch("src.main.webhook.notify_complete")


def test_webhook_fired_once_with_employee_and_artifacts(mocker, tmp_path):
    notify = _mock_successful_run(mocker, tmp_path)

    main.process_item(
        MagicMock(), _item("fid", "video.mp4"), "folderX", _webhook_config()
    )

    notify.assert_called_once()
    kwargs = notify.call_args.kwargs
    assert kwargs["url"] == "https://example.com/hooks/gdstt"
    assert kwargs["token"] == "secret"
    assert kwargs["payload"] == {
        "file": {"id": "fid", "name": "video.mp4", "folder_id": "folderX"},
        "employee": {"name": "Олег Иванов", "email": "oleg@expertizeme.org"},
        "transcript": "Speaker 1: hi",
        "artifacts": {
            "transcript-cleanup": "Ольга: привет",
            "keypoints": "## Задачи",
            # `meta` is parsed into structured fields; `invented-tag` is outside the
            # configured allow-list and must not reach the receiver.
            "meta": {
                "subject": "Консультация по визе O-1",
                "tags": ["O-1", "клиентская-консультация"],
                "referral": "рекомендация",
                "referral_note": "Посоветовала знакомая",
            },
        },
    }


def test_webhook_withheld_while_a_preset_produced_no_artifact(mocker, tmp_path, caplog):
    """A blank `ok` preset writes no artifact, so the file stays pending and is
    re-selected every cycle. Firing here would re-POST the transcript forever — the
    receiver gets no retry and has no dedupe key — so the webhook waits."""
    notify = _mock_successful_run(mocker, tmp_path, meta_text="   ")

    with caplog.at_level(logging.WARNING, logger="src.main"):
        main.process_item(
            MagicMock(), _item("fid", "video.mp4"), "folderX", _webhook_config()
        )

    notify.assert_not_called()
    assert "Completion webhook withheld" in caplog.text


def test_webhook_unknown_employee_sends_empty_strings(mocker, tmp_path):
    notify = _mock_successful_run(mocker, tmp_path)

    # The file's folder isn't in `folders` at all — the payload keeps the key.
    main.process_item(
        MagicMock(), _item("fid", "video.mp4"), "otherFolder", _webhook_config()
    )

    payload = notify.call_args.kwargs["payload"]
    assert payload["employee"] == {"name": "", "email": ""}
    assert payload["file"]["folder_id"] == "otherFolder"


def test_webhook_malformed_meta_degrades_to_empty_fields(mocker, tmp_path):
    notify = _mock_successful_run(mocker, tmp_path, meta_text="not frontmatter at all")

    main.process_item(
        MagicMock(), _item("fid", "video.mp4"), "folderX", _webhook_config()
    )

    artifacts = notify.call_args.kwargs["payload"]["artifacts"]
    assert artifacts["meta"] == {
        "subject": "",
        "tags": [],
        "referral": "",
        "referral_note": "",
    }


def test_webhook_not_fired_when_file_skipped(mocker, tmp_path):
    notify = mocker.patch("src.main.webhook.notify_complete")

    # Nothing to do: mp3 and txt exist and no preset is configured.
    cfg = make_config(
        stt_provider="deepgram",
        deepgram_api_key="dg-x",
        webhook_url="https://example.com/hooks/gdstt",
    )
    telemetry = main.process_item(
        MagicMock(),
        _item("fid", "video.mp4", has_mp3=True, has_txt=True),
        "folderA",
        cfg,
    )

    assert telemetry is None
    notify.assert_not_called()


def test_webhook_receives_the_configured_proxy(mocker, tmp_path):
    """The proxy is honoured inside notify_complete; this pins the wiring. Without
    it, a proxied deployment silently stops delivering — notify_complete swallows the
    connection error and the file still processes."""
    notify = _mock_successful_run(mocker, tmp_path)

    main.process_item(
        MagicMock(),
        _item("fid", "video.mp4"),
        "folderX",
        _webhook_config(proxy_url="http://proxy:3128"),
    )

    assert notify.call_args.kwargs["proxy_url"] == "http://proxy:3128"


def test_webhook_not_fired_for_an_mp3_only_pass(mocker, tmp_path):
    """STT disabled and only the mp3 artifact wanted: there is no transcript and no
    preset output, so POSTing blanks would overwrite a good record on the receiver."""
    notify = mocker.patch("src.main.webhook.notify_complete")
    mocker.patch("src.main.drive.download", return_value=tmp_path / "video.mp4")
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.drive.upload")

    cfg = _webhook_config(stt_provider="", presets=(), drive_mp3_artifact=True)
    telemetry = main.process_item(
        MagicMock(), _item("fid", "video.mp4"), "folderX", cfg
    )

    assert telemetry is not None
    assert telemetry.mp3_uploaded is True
    notify.assert_not_called()


def test_webhook_not_refired_when_only_a_late_mp3_is_added(mocker, tmp_path):
    """Enabling drive_mp3_artifact after transcripts already exist backfills the mp3
    only; the file's webhook already fired on the cycle that produced the transcript."""
    notify = mocker.patch("src.main.webhook.notify_complete")
    mocker.patch("src.main.drive.download", return_value=tmp_path / "video.mp4")
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.drive.upload")

    cfg = _webhook_config(presets=(), drive_mp3_artifact=True)
    telemetry = main.process_item(
        MagicMock(),
        _item("fid", "video.mp4", has_txt=True, txt_id="t1", has_mp3=False),
        "folderX",
        cfg,
    )

    assert telemetry is not None
    notify.assert_not_called()


def test_webhook_not_fired_on_failure(mocker, tmp_path):
    notify = _mock_successful_run(mocker, tmp_path)
    mocker.patch("src.main.transcribe_file", side_effect=STTError("deepgram down"))

    with pytest.raises(STTError):
        main.process_item(
            MagicMock(), _item("fid", "video.mp4"), "folderX", _webhook_config()
        )

    notify.assert_not_called()


def test_webhook_exception_does_not_fail_the_file(mocker, tmp_path):
    notify = _mock_successful_run(mocker, tmp_path)
    notify.side_effect = RuntimeError("receiver exploded")

    # notify_complete swallows its own errors, but a bug there must not undo a file
    # that already transcribed and uploaded every artifact.
    telemetry = main.process_item(
        MagicMock(), _item("fid", "video.mp4"), "folderX", _webhook_config()
    )

    assert telemetry is not None
    assert telemetry.txt_uploaded is True


def test_process_summary_log_omits_artifact_text(mocker, tmp_path, caplog):
    service = MagicMock()
    mp4_path = tmp_path / "video.mp4"

    mocker.patch("src.main.drive.download", return_value=mp4_path)
    mocker.patch("src.main.drive.upload")
    mocker.patch("src.main.extract_mp3", return_value=tmp_path / "video.mp3")
    mocker.patch("src.main.transcribe_file", return_value="Speaker 1: secret words")
    mocker.patch(
        "src.main.preset_pipeline.run_presets",
        return_value={
            "transcript-cleanup": PresetResult(
                name="transcript-cleanup", text="Alice: confidential"
            ),
        },
    )

    with caplog.at_level("INFO"):
        main.process_item(
            service, _item("fid", "video.mp4"), "folderX", _two_preset_config()
        )

    summary = [r.getMessage() for r in caplog.records if "Process summary" in r.msg]
    assert len(summary) == 1
    assert "confidential" not in summary[0]
    assert "secret words" not in summary[0]


# --- call-booking gate helpers -------------------------------------------------

GATE_FOLDER_ID = "f1"


@pytest.fixture
def gate_config(tmp_path):
    """A config with the receiver enabled and the gate armed."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("", encoding="utf-8")
    return Config(
        folders=(
            EmployeeFolder(
                folder_id=GATE_FOLDER_ID, name="Kate", email="kate@example.com"
            ),
        ),
        poll_interval=600,
        bitrate="96k",
        data_dir=tmp_path,
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="sk",
        deepgram_api_key="dg",
        stt_language="ru",
        call_booking_enabled=True,
        call_booking_disable_recognition=True,
        call_booking_threshold_minutes=15,
        config_file=config_file,
    )


def gate_item(file_id="v1", *, booking_match="", planfix_comment_task_id=""):
    """One `list_folder_state` item for an mp4 that still needs a transcript."""
    return {
        "file": {"id": file_id, "name": f"{file_id}.mp4", "mimeType": "video/mp4"},
        "has_mp3": True,
        "has_txt": False,
        "mp3_id": None,
        "mp3_name": None,
        "txt_id": None,
        "artifact_ids": {},
        "booking_match": booking_match,
        "planfix_comment_task_id": planfix_comment_task_id,
    }


def patch_folder_items(monkeypatch, items):
    monkeypatch.setattr(main.drive, "list_folder_state", lambda service, fid: items)


def patch_decision(monkeypatch, decision):
    monkeypatch.setattr(
        main.booking_gate, "resolve", lambda file_info, folder_id, config: decision
    )


UNMATCHED_DECISION = BookingDecision(state="unmatched", reason="no-booking")
MATCHED_DECISION = BookingDecision(state="matched", task_id="851030")


def test_run_once_skips_and_marks_an_unmatched_recording(monkeypatch, gate_config):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    assert marked == ["v1"]


def test_run_once_survives_a_drive_failure_while_marking(monkeypatch, gate_config, caplog):
    """A transient Drive error from mark_unmatched must not kill the polling loop.

    Carried from the Task 6 review: mark_unmatched/clear_mark do not catch Drive
    API exceptions themselves, so run_once must contain the failure.
    """
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])

    def raise_http_error(svc, fid):
        raise HttpError(MagicMock(status=503), b"unavailable")

    monkeypatch.setattr(main.booking_gate, "mark_unmatched", raise_http_error)
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    with caplog.at_level(logging.INFO):
        main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    assert "Failed to mark" in caplog.text
    assert "skipped_unmatched=1" in caplog.text


def test_run_once_does_not_mark_when_the_receiver_is_down(
    monkeypatch, gate_config, caplog
):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: False)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    with caplog.at_level(logging.WARNING):
        main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    assert marked == []
    assert "not listening" in caplog.text


def test_run_once_counts_skipped_unmatched_separately(monkeypatch, gate_config, caplog):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    monkeypatch.setattr(main.booking_gate, "mark_unmatched", lambda svc, fid: None)

    with caplog.at_level(logging.INFO):
        main.run_once(MagicMock(), gate_config)

    assert "skipped_unmatched=1" in caplog.text
    assert "processed=0" in caplog.text


def test_run_once_never_revisits_an_already_marked_recording(monkeypatch, gate_config):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    resolve = MagicMock()
    monkeypatch.setattr(main.booking_gate, "resolve", resolve)
    patch_folder_items(monkeypatch, [gate_item("v1", booking_match="none")])
    process_item = MagicMock()
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_not_called()
    resolve.assert_not_called()


def test_run_once_processes_a_matched_recording(monkeypatch, gate_config):
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, MATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_called_once()
    assert process_item.call_args.kwargs["booking_decision"] == MATCHED_DECISION


def test_run_once_processes_when_disable_recognition_is_off(monkeypatch, gate_config):
    permissive = replace(gate_config, call_booking_disable_recognition=False)
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_decision(monkeypatch, UNMATCHED_DECISION)
    patch_folder_items(monkeypatch, [gate_item("v1")])
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), permissive)

    process_item.assert_called_once()
    assert marked == []


def test_process_target_ignores_the_mark_and_the_gate(monkeypatch, gate_config):
    """Manual processing is the supported way to undo a mark."""
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_folder_items(monkeypatch, [gate_item("v1", booking_match="none")])
    monkeypatch.setattr(
        main.drive,
        "get_file_metadata",
        lambda service, fid: {
            "id": "v1", "name": "v1.mp4", "mimeType": "video/mp4",
            "parents": [GATE_FOLDER_ID],
        },
    )
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.process_target(MagicMock(), "v1", gate_config, is_folder=False)

    process_item.assert_called_once()
    assert marked == []
    assert process_item.call_args.kwargs.get("booking_decision") is None


def test_process_target_folder_branch_ignores_the_mark_and_the_gate(
    monkeypatch, gate_config
):
    """The folder branch shares ``_pending_items`` with ``run_once`` -- the one place
    a regression could leak the gate into manual processing. `process_target` must
    still process a marked item without ever consulting the gate.
    """
    monkeypatch.setattr(main.booking_server, "is_running", lambda: True)
    patch_folder_items(monkeypatch, [gate_item("v1", booking_match="none")])
    resolve = MagicMock()
    monkeypatch.setattr(main.booking_gate, "resolve", resolve)
    marked = []
    monkeypatch.setattr(
        main.booking_gate, "mark_unmatched", lambda svc, fid: marked.append(fid)
    )
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.process_target(MagicMock(), GATE_FOLDER_ID, gate_config, is_folder=True)

    process_item.assert_called_once()
    resolve.assert_not_called()
    assert marked == []


def test_run_once_matches_a_real_booking_through_the_real_gate(monkeypatch, gate_config):
    """Integration coverage for the seam every other gate test mocks around: Drive
    file name -> meeting_time.parse_meeting_start -> journal load -> match ->
    decision. Only the Drive listing and process_item are mocked; a real Config and
    a real ``booking_gate.resolve`` run against a journal seeded with
    ``call_booking.append``.
    """
    # "2026/08/08 09:00 GMT+04:00" is one of the formats parse_meeting_start
    # accepts (see src/meeting_time.py); it resolves to 2026-08-08T05:00:00Z.
    file_name = "Call with Kate - 2026/08/08 09:00 GMT+04:00 – Recording.mp4"
    video_start_utc = datetime(2026, 8, 8, 5, 0, tzinfo=timezone.utc)

    append_booking(
        gate_config.call_bookings_file,
        CallBooking(
            task_id="851030",
            manager_email="kate@example.com",
            start_time=video_start_utc + timedelta(minutes=5),
        ),
    )
    patch_folder_items(
        monkeypatch,
        [
            {
                "file": {"id": "v1", "name": file_name, "mimeType": "video/mp4"},
                "has_mp3": True,
                "has_txt": False,
                "mp3_id": None,
                "mp3_name": None,
                "txt_id": None,
                "artifact_ids": {},
                "booking_match": "",
                "planfix_comment_task_id": "",
            }
        ],
    )
    process_item = MagicMock(return_value=None)
    monkeypatch.setattr(main, "process_item", process_item)

    main.run_once(MagicMock(), gate_config)

    process_item.assert_called_once()
    decision = process_item.call_args.kwargs["booking_decision"]
    assert decision.is_matched is True
    assert decision.task_id == "851030"


# --- Planfix comment ------------------------------------------------------------


@pytest.fixture
def planfix_config(gate_config):
    return replace(
        gate_config,
        planfix_create_comment_url="https://crm.example.com/planfix_create_comment",
        planfix_token="planfix-token",
        planfix_presets=("keypoints",),
    )


def test_resolve_speaker_names_hands_the_folder_owner_over_as_the_manager(monkeypatch):
    """The one identity we know for certain is the folder's owner."""
    seen = {}

    class FakePipeline:
        last_usage = {"input_tokens": 11, "output_tokens": 3}

        def __init__(self, **kwargs):
            seen["model"] = kwargs.get("model")

        def run(self, instructions, input_text):
            seen["input"] = input_text
            return '{"1": "Mels", "2": "Angelica Munkueva"}', {}

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(main, "OpenAIPipeline", FakePipeline)
    config = make_config(
        folders=[EmployeeFolder("f1", name="Анжелика Мункуева", email="a@e.org")],
        openai_api_key="key",
    )
    transcript = (
        "[00:00:01] Speaker 1: Здравствуйте, я по заявке.\n"
        "[00:00:04] Speaker 2: Добрый день, я из ExpertizeMe.\n"
    )
    usage: dict[str, dict[str, int]] = {}

    names = main._resolve_speaker_names(
        transcript,
        "Angelica Munkueva(ExpertizeMe) и Mels - 2026/08/13 14:29 CEST - Recording",
        "f1",
        config,
        usage=usage,
    )

    assert names == ["Mels", "Angelica Munkueva"]
    assert "Анжелика Мункуева" in seen["input"]
    assert seen["closed"] is True
    assert usage["openai_speaker_roles"] == {"input_tokens": 11, "output_tokens": 3}


def test_resolve_speaker_names_is_skipped_without_an_openai_key(monkeypatch):
    def explode(**kwargs):
        raise AssertionError("must not build a pipeline without a key")

    monkeypatch.setattr(main, "OpenAIPipeline", explode)
    config = make_config(folders=[EmployeeFolder("f1", name="Анжелика")], openai_api_key="")

    assert (
        main._resolve_speaker_names(
            "[00:00:01] Speaker 1: раз\n[00:00:04] Speaker 2: два",
            "Alice and Bob - 2026/08/13 14:29 CEST - Recording",
            "f1",
            config,
        )
        is None
    )


def test_resolve_speaker_names_returns_none_when_the_name_has_one_participant(monkeypatch):
    def explode(**kwargs):
        raise AssertionError("nothing to disambiguate, must not spend a call")

    monkeypatch.setattr(main, "OpenAIPipeline", explode)
    config = make_config(folders=[EmployeeFolder("f1", name="Анжелика")], openai_api_key="key")

    assert (
        main._resolve_speaker_names(
            "[00:00:01] Speaker 1: раз\n[00:00:04] Speaker 2: два",
            "Планёрка - 2026/08/13 14:29 CEST - Recording",
            "f1",
            config,
        )
        is None
    )


def test_planfix_description_concatenates_presets_in_order():
    description = main._planfix_description(
        {"keypoints": "Задачи: раз", "action-items": "Сделать два", "meta": "topic: x"},
        ("keypoints", "action-items"),
    )

    assert description == (
        "<p><b>keypoints</b></p><p>Задачи: раз</p>"
        "<p><b>action-items</b></p><p>Сделать два</p>"
    )


def test_planfix_description_skips_presets_without_an_artifact():
    description = main._planfix_description(
        {"keypoints": "Задачи: раз"}, ("keypoints", "action-items")
    )

    assert description == "<p><b>keypoints</b></p><p>Задачи: раз</p>"


def test_planfix_description_renders_markdown_as_html():
    """Planfix shows Markdown as literal characters, so the body must arrive as HTML."""
    description = main._planfix_description(
        {"keypoints": "## Задачи\n\n- [ ] позвонить\n- написать"}, ("keypoints",)
    )

    assert description == (
        "<p><b>keypoints</b></p><p><b>Задачи</b></p>"
        "<ul><li>позвонить</li><li>написать</li></ul>"
    )
    assert "\n" not in description


def test_planfix_description_is_blank_when_nothing_matched():
    assert main._planfix_description({"meta": "topic: x"}, ("keypoints",)) == ""


def test_comment_is_sent_for_a_matched_recording(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)
    marked = MagicMock()
    monkeypatch.setattr(main.drive, "set_file_app_properties", marked)

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", planfix_config,
        {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_called_once()
    assert send.call_args.kwargs["task_id"] == "851030"
    assert send.call_args.kwargs["description"] == (
        "<p><b>keypoints</b></p><p>Задачи: раз</p>"
    )
    marked.assert_called_once()
    assert marked.call_args[0][2] == {"planfix_comment_task_id": "851030"}


def test_comment_is_not_sent_twice(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)

    main._send_planfix_comment(
        MagicMock(),
        gate_item("v1", planfix_comment_task_id="851030"),
        "v1", planfix_config, {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_not_called()


def test_comment_is_not_sent_for_an_unmatched_recording(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", planfix_config,
        {"keypoints": "Задачи: раз"}, UNMATCHED_DECISION,
    )

    send.assert_not_called()


def test_comment_is_not_sent_when_the_url_is_blank(monkeypatch, planfix_config):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(main.planfix, "send_comment", send)
    unconfigured = replace(planfix_config, planfix_create_comment_url="")

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", unconfigured,
        {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_not_called()


def test_failed_comment_notifies_telegram_and_leaves_no_marker(
    monkeypatch, planfix_config
):
    send = MagicMock(return_value=False)
    monkeypatch.setattr(main.planfix, "send_comment", send)
    marked = MagicMock()
    monkeypatch.setattr(main.drive, "set_file_app_properties", marked)
    notified = MagicMock()
    monkeypatch.setattr(main.notify, "notify_error", notified)

    main._send_planfix_comment(
        MagicMock(), gate_item("v1"), "v1", planfix_config,
        {"keypoints": "Задачи: раз"}, MATCHED_DECISION,
    )

    send.assert_called_once()
    # No marker means `gdstt reprocess` can resend it.
    marked.assert_not_called()
    notified.assert_called_once()


def test_process_item_sends_the_comment_on_the_success_path(mocker, tmp_path):
    """Copy of ``test_webhook_fired_once_with_employee_and_artifacts``: same arrange,
    with `_send_planfix_comment` mocked and `booking_decision` passed through."""
    _mock_successful_run(mocker, tmp_path)
    sent = MagicMock()
    mocker.patch.object(main, "_send_planfix_comment", sent)

    main.process_item(
        MagicMock(),
        _item("fid", "video.mp4"),
        "folderX",
        _webhook_config(),
        booking_decision=MATCHED_DECISION,
    )

    sent.assert_called_once()


def test_process_item_withholds_the_comment_when_a_preset_produced_nothing(
    mocker, tmp_path
):
    """Copy of ``test_webhook_withheld_while_a_preset_produced_no_artifact``: same
    arrange (a blank ``meta`` preset), with `_send_planfix_comment` mocked and
    `booking_decision` passed through."""
    _mock_successful_run(mocker, tmp_path, meta_text="   ")
    sent = MagicMock()
    mocker.patch.object(main, "_send_planfix_comment", sent)

    main.process_item(
        MagicMock(),
        _item("fid", "video.mp4"),
        "folderX",
        _webhook_config(),
        booking_decision=MATCHED_DECISION,
    )

    sent.assert_not_called()


# --- start the receiver from the polling loop ----------------------------------


class _StopLoop(Exception):
    """Raised from a patched time.sleep to break main()'s infinite loop."""


def stop_after_one_cycle(monkeypatch, config):
    """Patch main() down to one cycle. Returns the run_once mock."""
    monkeypatch.setattr(main, "load_config", lambda **kwargs: config)
    monkeypatch.setattr(main, "build_drive_service", lambda **kwargs: MagicMock())
    monkeypatch.setattr(main, "is_run_enabled", lambda **kwargs: True)
    run_once = MagicMock()
    monkeypatch.setattr(main, "run_once", run_once)

    def _sleep(_seconds):
        raise _StopLoop

    monkeypatch.setattr(main.time, "sleep", _sleep)
    return run_once


def test_main_starts_the_receiver_when_enabled(monkeypatch, gate_config):
    start = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(main.booking_server, "start", start)
    stop_after_one_cycle(monkeypatch, gate_config)

    with pytest.raises(_StopLoop):
        main.main()

    start.assert_called_once_with(gate_config)


def test_main_survives_a_receiver_bind_failure(monkeypatch, gate_config, caplog):
    monkeypatch.setattr(
        main.booking_server, "start", MagicMock(side_effect=OSError("port in use"))
    )
    notified = MagicMock()
    monkeypatch.setattr(main.notify, "notify_error", notified)
    run_once = stop_after_one_cycle(monkeypatch, gate_config)

    with caplog.at_level(logging.ERROR), pytest.raises(_StopLoop):
        main.main()

    # The polling loop is the primary job; a dead receiver degrades the gate but must
    # not stop transcription.
    run_once.assert_called_once()
    notified.assert_called_once()
    assert "booking receiver" in caplog.text.lower()


# --- write .meta.yml and .stt for every processed recording -------------------


_STT_NAME = "Angelica Munkueva(ExpertizeMe) и Mels - 2026/08/13 14:29 CEST.mp4"
_STT_STEM = "Angelica Munkueva(ExpertizeMe) и Mels - 2026_08_13 14_29 CEST"
_STT_TRANSCRIPT = "[00:00:05] Angelica Munkueva: Здравствуйте\n[00:31:42] Mels: Спасибо"
# Named distinctly from the webhook tests' own `_META_ARTIFACT` (line 2537) so this
# block does not shadow that constant's binding for readers scanning the file.
_STT_META_ARTIFACT = "---\nsubject: Обсудили кейс\ntags: []\n---"


def _stt_config(tmp_path, **overrides):
    out = tmp_path / "results"
    out.mkdir(exist_ok=True)
    return make_config(
        # `_as_folders` (used by `make_config`) only accepts bare ids or
        # `EmployeeFolder` instances, not the {"folder_id": ...} mapping form the
        # brief predicted -- that form silently binds the whole dict as `folder_id`,
        # so `config.folder_by_id("folderA")` would never match and the meta
        # document's manager/manager_email would go empty.
        folders=[EmployeeFolder(
            "folderA", name="Анжелика Мункуева", email="angelica@expertizeme.org"
        )],
        presets=(_KEYPOINTS_BUILTIN,),
        output_target="folder",
        output_dir=out,
        **overrides,
    )


def _write_documents(cfg, tmp_path, artifacts, transcript=_STT_TRANSCRIPT):
    return main._write_call_documents(
        MagicMock(),
        "fid1",
        _STT_NAME,
        "folderA",
        transcript,
        artifacts,
        cfg,
        tmp_path,
        item={},
        booking_decision=MATCHED_DECISION,
    )


def test_write_call_documents_writes_the_stt_and_the_meta_yml(tmp_path):
    cfg = _stt_config(tmp_path)
    document = _write_documents(
        cfg, tmp_path, {"keypoints": "## Задачи\n- [ ] x", "meta": _STT_META_ARTIFACT}
    )
    stt = (cfg.output_dir / f"{_STT_STEM}.stt").read_text(encoding="utf-8")
    assert stt.index("## Задачи") < stt.index("## Мета") < stt.index("## Расшифровка")
    assert (cfg.output_dir / f"{_STT_STEM}.meta.yml").exists()
    assert document["subject"] == "Обсудили кейс"
    assert document["planfix_task_id"] == "851030"


def test_write_call_documents_reads_a_preset_from_its_local_artifact(tmp_path):
    """A cycle that re-ran only `meta` must still get keypoints into the .stt."""
    cfg = _stt_config(tmp_path)
    (cfg.output_dir / f"{_STT_STEM}.keypoints.md").write_text(
        "## Задачи\n- [ ] x", encoding="utf-8"
    )
    _write_documents(cfg, tmp_path, {"meta": _STT_META_ARTIFACT})
    stt = (cfg.output_dir / f"{_STT_STEM}.stt").read_text(encoding="utf-8")
    assert "## Задачи" in stt


def test_write_call_documents_falls_back_to_the_raw_transcript(tmp_path):
    """No transcript-cleanup artifact means the .stt carries the raw text."""
    cfg = _stt_config(tmp_path)
    _write_documents(cfg, tmp_path, {})
    stt = (cfg.output_dir / f"{_STT_STEM}.stt").read_text(encoding="utf-8")
    assert "[00:00:05] Angelica Munkueva: Здравствуйте" in stt


def test_write_call_documents_writes_the_meta_yml_when_the_preset_produced_nothing(tmp_path):
    cfg = _stt_config(tmp_path)
    document = _write_documents(cfg, tmp_path, {"keypoints": "## Задачи"})
    assert (cfg.output_dir / f"{_STT_STEM}.meta.yml").exists()
    assert document["subject"] == ""
    assert document["client"] == "Mels"


def test_process_item_survives_a_failed_stt_meta_write(mocker, tmp_path, caplog):
    """A `.stt`/`.meta.yml` write failure must not cost the recording its webhook or
    Planfix comment: by the time `_write_call_documents` runs, the `.txt` and every
    preset artifact are already persisted, so a re-raise here would leave the next
    cycle seeing a fully-processed recording (has_txt, no missing presets) that
    never got notified and never will be retried.
    """
    notify = _mock_successful_run(mocker, tmp_path)
    mocker.patch(
        "src.main._write_call_documents", side_effect=RuntimeError("disk full")
    )

    with caplog.at_level(logging.WARNING):
        telemetry = main.process_item(
            MagicMock(), _item("fid", "video.mp4"), "folderX", _webhook_config()
        )

    assert telemetry is not None
    assert telemetry.meta_document is None
    notify.assert_called_once()
    assert any(
        "stt" in record.message.lower() and "video.mp4" in record.message
        for record in caplog.records
    )


def test_apply_local_output_state_stt_and_meta_yml_do_not_mark_a_recording_processed(
    tmp_path,
):
    """The load-bearing invariant: a `.stt`/`.meta.yml` sitting in `output_dir` with
    no `.txt` sibling must not make `_apply_local_output_state`/`_pending_items`
    think the recording is done. Only `.txt` and the preset artifacts may do that
    (see `_write_call_documents`'s docstring) -- getting this wrong would silently
    stop re-selecting recordings that were never actually transcribed.
    """
    out_dir = tmp_path / "results"
    out_dir.mkdir()
    (out_dir / "video.stt").write_text("assembled", encoding="utf-8")
    (out_dir / "video.meta.yml").write_text("subject: ''\n", encoding="utf-8")

    cfg = make_config(
        stt_provider="deepgram",
        drive_mp3_artifact=False,
        output_target="folder",
        output_dir=out_dir,
    )
    items = [_item("fid", "video.mp4")]
    main._apply_local_output_state(items, cfg)

    assert items[0]["has_txt"] is False
    assert main._pending_items(items, cfg) == items


def test_apply_local_output_state_stt_and_meta_yml_do_not_block_a_processed_recording(
    tmp_path,
):
    """The converse of the test above: once the real `.txt` is present (and every
    preset artifact, none configured here), a `.stt`/`.meta.yml` sitting alongside it
    must not make the recording look pending again.
    """
    out_dir = tmp_path / "results"
    out_dir.mkdir()
    (out_dir / "video.txt").write_text("Speaker 1: hi", encoding="utf-8")
    (out_dir / "video.stt").write_text("assembled", encoding="utf-8")
    (out_dir / "video.meta.yml").write_text("subject: ''\n", encoding="utf-8")

    cfg = make_config(
        stt_provider="deepgram",
        drive_mp3_artifact=False,
        output_target="folder",
        output_dir=out_dir,
    )
    items = [_item("fid", "video.mp4")]
    main._apply_local_output_state(items, cfg)

    assert items[0]["has_txt"] is True
    assert main._pending_items(items, cfg) == []
