from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src import drive


def _make_list_service(pages_by_query: dict[str, list[dict]]) -> MagicMock:
    """Build a mock Drive service whose files().list().execute() returns lists matched by mimeType."""
    service = MagicMock()
    files_resource = MagicMock()
    service.files.return_value = files_resource

    def list_side_effect(**kwargs):
        q = kwargs["q"]
        request = MagicMock()
        if "video/mp4" in q:
            request.execute.return_value = {"files": pages_by_query.get("mp4", [])}
        elif "audio/mpeg" in q:
            request.execute.return_value = {"files": pages_by_query.get("mp3", [])}
        elif "text/plain" in q:
            request.execute.return_value = {"files": pages_by_query.get("txt", [])}
        elif "text/markdown" in q:
            request.execute.return_value = {"files": pages_by_query.get("md", [])}
        else:
            request.execute.return_value = {"files": []}
        return request

    files_resource.list.side_effect = list_side_effect
    return service


def test_download_writes_file_to_dest_dir(tmp_path, mocker):
    service = MagicMock()
    request = MagicMock()
    service.files.return_value.get_media.return_value = request

    chunks_done = [False, False, True]

    def make_downloader(fh, _request):
        downloader = MagicMock()
        # Write something to the file handle on each call
        state = {"i": 0}

        def next_chunk():
            i = state["i"]
            state["i"] += 1
            fh.write(b"chunk")
            status = MagicMock()
            status.progress.return_value = (i + 1) / len(chunks_done)
            return status, chunks_done[i]

        downloader.next_chunk.side_effect = next_chunk
        return downloader

    mocker.patch("src.drive.MediaIoBaseDownload", side_effect=make_downloader)

    dest = tmp_path / "downloads"
    result = drive.download(service, "fileid123", dest, "video.mp4")

    assert result == dest / "video.mp4"
    assert result.exists()
    assert result.read_bytes() == b"chunk" * 3
    service.files.return_value.get_media.assert_called_once_with(
        fileId="fileid123", supportsAllDrives=True
    )


def test_download_creates_missing_dest_dir(tmp_path, mocker):
    service = MagicMock()
    service.files.return_value.get_media.return_value = MagicMock()

    def make_downloader(fh, _request):
        downloader = MagicMock()
        downloader.next_chunk.return_value = (None, True)
        return downloader

    mocker.patch("src.drive.MediaIoBaseDownload", side_effect=make_downloader)

    dest = tmp_path / "new" / "nested" / "dir"
    result = drive.download(service, "fid", dest, "f.mp4")

    assert dest.is_dir()
    assert result.parent == dest


def test_download_raises_on_size_mismatch_and_cleans_partial_file(tmp_path, mocker):
    service = MagicMock()
    service.files.return_value.get_media.return_value = MagicMock()

    def make_downloader(fh, _request):
        downloader = MagicMock()

        def next_chunk():
            fh.write(b"short")
            return None, True

        downloader.next_chunk.side_effect = next_chunk
        return downloader

    mocker.patch("src.drive.MediaIoBaseDownload", side_effect=make_downloader)

    with pytest.raises(RuntimeError, match="size mismatch"):
        drive.download(
            service,
            "fid",
            tmp_path,
            "video.mp4",
            expected_size_bytes=10,
        )

    assert not (tmp_path / "video.mp4").exists()


def test_upload_calls_create_with_metadata_and_media(tmp_path, mocker):
    local = tmp_path / "audio.mp3"
    local.write_bytes(b"id3-data")

    service = MagicMock()
    create_request = MagicMock()
    create_request.execute.return_value = {"id": "new123", "name": "audio.mp3", "parents": ["fld"]}
    service.files.return_value.create.return_value = create_request

    media_cls = mocker.patch("src.drive.MediaFileUpload", return_value="media-obj")

    result = drive.upload(service, local, "fld")

    assert result == {"id": "new123", "name": "audio.mp3", "parents": ["fld"]}
    media_cls.assert_called_once_with(str(local), mimetype="audio/mpeg", resumable=True)
    service.files.return_value.create.assert_called_once_with(
        body={"name": "audio.mp3", "parents": ["fld"]},
        media_body="media-obj",
        fields="id, name, parents",
        supportsAllDrives=True,
    )


def test_upload_accepts_app_properties(tmp_path, mocker):
    local = tmp_path / "audio.mp3"
    local.write_bytes(b"id3-data")

    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {"id": "x"}
    mocker.patch("src.drive.MediaFileUpload", return_value="media")

    drive.upload(
        service,
        local,
        "fld",
        app_properties={"source_video_id": "v1", "artifact_type": "mp3"},
    )

    body = service.files.return_value.create.call_args.kwargs["body"]
    assert body["appProperties"] == {
        "source_video_id": "v1",
        "artifact_type": "mp3",
    }


def test_upload_accepts_custom_mime_type(tmp_path, mocker):
    local = tmp_path / "video.mp4"
    local.write_bytes(b"mp4-data")

    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {"id": "x"}

    media_cls = mocker.patch("src.drive.MediaFileUpload", return_value="media")

    drive.upload(service, local, "fld", mime_type="video/mp4")

    media_cls.assert_called_once_with(str(local), mimetype="video/mp4", resumable=True)


def test_update_file_overwrites_in_place(tmp_path, mocker):
    local = tmp_path / "video.txt"
    local.write_text("final transcript", encoding="utf-8")

    service = MagicMock()
    update_request = MagicMock()
    update_request.execute.return_value = {"id": "t1", "name": "video.txt"}
    service.files.return_value.update.return_value = update_request

    media_cls = mocker.patch("src.drive.MediaFileUpload", return_value="media-obj")

    result = drive.update_file(service, "t1", local)

    assert result == {"id": "t1", "name": "video.txt"}
    media_cls.assert_called_once_with(str(local), mimetype="text/plain", resumable=True)
    service.files.return_value.update.assert_called_once_with(
        fileId="t1",
        media_body="media-obj",
        fields="id, name",
        supportsAllDrives=True,
    )


def test_update_file_accepts_app_properties(tmp_path, mocker):
    local = tmp_path / "video.txt"
    local.write_text("final transcript", encoding="utf-8")

    service = MagicMock()
    service.files.return_value.update.return_value.execute.return_value = {"id": "t1"}
    mocker.patch("src.drive.MediaFileUpload", return_value="media-obj")

    drive.update_file(
        service,
        "t1",
        local,
        app_properties={"source_video_id": "v1", "artifact_type": "txt"},
    )

    kwargs = service.files.return_value.update.call_args.kwargs
    assert kwargs["body"]["appProperties"] == {
        "source_video_id": "v1",
        "artifact_type": "txt",
    }


def test_set_file_app_properties_updates_metadata_only():
    service = MagicMock()
    service.files.return_value.update.return_value.execute.return_value = {"id": "v1"}

    drive.set_file_app_properties(service, "v1", {"speaker_names": "[\"A\", \"B\"]"})

    service.files.return_value.update.assert_called_once_with(
        fileId="v1",
        body={"appProperties": {"speaker_names": "[\"A\", \"B\"]"}},
        fields="id, name, appProperties",
        supportsAllDrives=True,
    )


def test_list_folder_state_includes_txt_id():
    mp4 = [
        {"id": "v1", "name": "a.mp4", "mimeType": "video/mp4"},
        {"id": "v2", "name": "b.mp4", "mimeType": "video/mp4"},
    ]
    txt = [{"id": "t1", "name": "a.txt", "mimeType": "text/plain"}]
    service = _make_list_service({"mp4": mp4, "mp3": [], "txt": txt})

    items = drive.list_folder_state(service, "folder1")

    by_id = {it["file"]["id"]: it for it in items}
    assert by_id["v1"]["txt_id"] == "t1"
    assert by_id["v2"]["txt_id"] is None


def test_list_folder_state_includes_keypoints_id_by_stem():
    mp4 = [
        {"id": "v1", "name": "a.mp4", "mimeType": "video/mp4"},
        {"id": "v2", "name": "b.mp4", "mimeType": "video/mp4"},
    ]
    md = [{"id": "k1", "name": "a.keypoints.md", "mimeType": "text/markdown"}]
    service = _make_list_service({"mp4": mp4, "mp3": [], "txt": [], "md": md})

    items = drive.list_folder_state(service, "folder1")

    by_id = {it["file"]["id"]: it for it in items}
    assert by_id["v1"]["artifact_ids"] == {"keypoints": "k1"}
    assert by_id["v2"]["artifact_ids"] == {}


def test_list_folder_state_matches_keypoints_by_source_video_id_after_rename():
    mp4 = [{"id": "v1", "name": "new name.mp4", "mimeType": "video/mp4"}]
    md = [{
        "id": "k1",
        "name": "old name.keypoints.md",
        "mimeType": "text/markdown",
        "appProperties": {"source_video_id": "v1", "artifact_type": "keypoints"},
    }]
    service = _make_list_service({"mp4": mp4, "mp3": [], "txt": [], "md": md})

    items = drive.list_folder_state(service, "folder1")

    assert items[0]["artifact_ids"] == {"keypoints": "k1"}


def test_list_folder_state_keys_artifact_ids_by_artifact_type():
    # Multiple presets produce multiple sibling .md artifacts; each is keyed by its
    # own artifact_type appProperty so the OpenAI stage can skip ones already made.
    mp4 = [{"id": "v1", "name": "a.mp4", "mimeType": "video/mp4"}]
    md = [
        {
            "id": "k1",
            "name": "a.keypoints.md",
            "mimeType": "text/markdown",
            "appProperties": {"source_video_id": "v1", "artifact_type": "keypoints"},
        },
        {
            "id": "c1",
            "name": "a.transcript-cleanup.md",
            "mimeType": "text/markdown",
            "appProperties": {
                "source_video_id": "v1",
                "artifact_type": "transcript-cleanup",
            },
        },
    ]
    service = _make_list_service({"mp4": mp4, "mp3": [], "txt": [], "md": md})

    items = drive.list_folder_state(service, "folder1")

    assert items[0]["artifact_ids"] == {
        "keypoints": "k1",
        "transcript-cleanup": "c1",
    }


def test_list_folder_state_plain_user_md_not_matched_by_stem():
    # A plain "talk.md" (no ".keypoints" convention) must never match "talk.mp4".
    mp4 = [{"id": "v1", "name": "talk.mp4", "mimeType": "video/mp4"}]
    md = [{"id": "u1", "name": "talk.md", "mimeType": "text/markdown"}]
    service = _make_list_service({"mp4": mp4, "mp3": [], "txt": [], "md": md})

    items = drive.list_folder_state(service, "folder1")

    assert items[0]["artifact_ids"] == {}


def test_list_folder_state_keypoints_md_linked_to_other_video_not_stem_matched():
    # "notes.keypoints.md" is an artifact of a DIFFERENT video (source_video_id
    # points elsewhere); it must not be folded into the stem index and falsely
    # attached to "notes.mp4".
    mp4 = [{"id": "v1", "name": "notes.mp4", "mimeType": "video/mp4"}]
    md = [{
        "id": "k1",
        "name": "notes.keypoints.md",
        "mimeType": "text/markdown",
        "appProperties": {"source_video_id": "other", "artifact_type": "keypoints"},
    }]
    service = _make_list_service({"mp4": mp4, "mp3": [], "txt": [], "md": md})

    items = drive.list_folder_state(service, "folder1")

    assert items[0]["artifact_ids"] == {}


def test_get_file_metadata_requests_expected_fields():
    service = MagicMock()
    get_request = MagicMock()
    get_request.execute.return_value = {
        "id": "fid",
        "name": "video.mp4",
        "mimeType": "video/mp4",
        "parents": ["parent1"],
    }
    service.files.return_value.get.return_value = get_request

    result = drive.get_file_metadata(service, "fid")

    assert result["id"] == "fid"
    assert result["parents"] == ["parent1"]
    service.files.return_value.get.assert_called_once_with(
        fileId="fid",
        fields="id, name, mimeType, parents, size, appProperties",
        supportsAllDrives=True,
    )


def test_list_folder_state_returns_flags():
    mp4 = [
        {"id": "v1", "name": "a.mp4", "mimeType": "video/mp4"},
        {"id": "v2", "name": "b.mp4", "mimeType": "video/mp4"},
        {"id": "v3", "name": "c.mp4", "mimeType": "video/mp4"},
    ]
    mp3 = [
        {"id": "m1", "name": "a.mp3", "mimeType": "audio/mpeg"},
        {"id": "m2", "name": "b.mp3", "mimeType": "audio/mpeg"},
    ]
    txt = [{"id": "t1", "name": "a.txt", "mimeType": "text/plain"}]
    service = _make_list_service({"mp4": mp4, "mp3": mp3, "txt": txt})

    items = drive.list_folder_state(service, "folder1")

    assert len(items) == 3
    by_id = {it["file"]["id"]: it for it in items}
    assert by_id["v1"]["has_mp3"] is True
    assert by_id["v1"]["has_txt"] is True
    assert by_id["v1"]["mp3_id"] == "m1"
    assert by_id["v1"]["mp3_name"] == "a.mp3"
    assert by_id["v2"]["has_mp3"] is True
    assert by_id["v2"]["has_txt"] is False
    assert by_id["v2"]["mp3_id"] == "m2"
    assert by_id["v3"]["has_mp3"] is False
    assert by_id["v3"]["has_txt"] is False
    assert by_id["v3"]["mp3_id"] is None


def test_list_folder_state_matches_siblings_by_source_video_id_after_rename():
    mp4 = [{"id": "v1", "name": "new name.mp4", "mimeType": "video/mp4"}]
    mp3 = [{
        "id": "m1",
        "name": "old name.mp3",
        "mimeType": "audio/mpeg",
        "appProperties": {"source_video_id": "v1", "artifact_type": "mp3"},
    }]
    txt = [{
        "id": "t1",
        "name": "old name.txt",
        "mimeType": "text/plain",
        "appProperties": {"source_video_id": "v1", "artifact_type": "txt"},
    }]
    service = _make_list_service({"mp4": mp4, "mp3": mp3, "txt": txt})

    items = drive.list_folder_state(service, "folder1")

    assert items[0]["has_mp3"] is True
    assert items[0]["mp3_id"] == "m1"
    assert items[0]["has_txt"] is True
    assert items[0]["txt_id"] == "t1"


def test_drive_stem_preserves_slashes():
    name = "Call - 2026/05/28 17:27 GMT+04:00 – Recording.mp4"
    assert drive.drive_stem(name) == "Call - 2026/05/28 17:27 GMT+04:00 – Recording"


def test_drive_stem_plain_name():
    assert drive.drive_stem("video.mp4") == "video"


def test_safe_local_name_replaces_separators():
    name = "Call - 2026/05/28 – Recording.mp4"
    safe = drive.safe_local_name(name)
    assert "/" not in safe
    assert safe == "Call - 2026_05_28 – Recording.mp4"


def test_safe_local_name_replaces_windows_reserved_characters():
    name = 'Call - 2026/05/28 17:27 GMT+04:00 <final>|draft?.mp4'
    safe = drive.safe_local_name(name)

    for char in '<>:"/\\|?*\0':
        assert char not in safe
    assert safe == "Call - 2026_05_28 17_27 GMT+04_00 _final__draft_.mp4"


def test_list_folder_state_matches_siblings_with_slashes():
    mp4 = [{"id": "v1", "name": "Call 2026/05/28 Rec.mp4", "mimeType": "video/mp4"}]
    mp3 = [{"id": "m1", "name": "Call 2026/05/28 Rec.mp3", "mimeType": "audio/mpeg"}]
    txt = [{"id": "t1", "name": "Call 2026/05/28 Rec.txt", "mimeType": "text/plain"}]
    service = _make_list_service({"mp4": mp4, "mp3": mp3, "txt": txt})

    items = drive.list_folder_state(service, "folder1")

    assert len(items) == 1
    assert items[0]["has_mp3"] is True
    assert items[0]["has_txt"] is True
    assert items[0]["mp3_id"] == "m1"


def test_download_sanitizes_slash_name(tmp_path, mocker):
    service = MagicMock()
    service.files.return_value.get_media.return_value = MagicMock()

    def make_downloader(fh, _request):
        downloader = MagicMock()
        downloader.next_chunk.return_value = (None, True)
        return downloader

    mocker.patch("src.drive.MediaIoBaseDownload", side_effect=make_downloader)

    path = drive.download(service, "fid", tmp_path, "Call 2026/05/28 Rec.mp4")

    # No characters dropped, no unintended subdirectory created from the "/".
    assert path.parent == tmp_path
    assert path.name == "Call 2026_05_28 Rec.mp4"
    assert path.exists()


def test_upload_explicit_name_overrides_local_name(tmp_path, mocker):
    local = tmp_path / "Call 2026_05_28 Rec.mp3"
    local.write_bytes(b"abc")

    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {"id": "x"}
    mocker.patch("src.drive.MediaFileUpload", return_value="media")

    drive.upload(service, local, "fld", drive.MP3_MIME, name="Call 2026/05/28 Rec.mp3")

    create_kwargs = service.files.return_value.create.call_args.kwargs
    assert create_kwargs["body"]["name"] == "Call 2026/05/28 Rec.mp3"


def test_find_newest_mp4_returns_first_file():
    service = MagicMock()
    newest = {"id": "v9", "name": "newest.mp4", "mimeType": "video/mp4"}
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [newest]
    }

    result = drive.find_newest_mp4(service, "folder1")

    assert result == newest
    list_kwargs = service.files.return_value.list.call_args.kwargs
    assert list_kwargs["orderBy"] == "createdTime desc"
    assert list_kwargs["pageSize"] == 1
    assert "video/mp4" in list_kwargs["q"]
    assert "trashed = false" in list_kwargs["q"]
    assert "folder1" in list_kwargs["q"]


def test_find_newest_mp4_empty_folder_returns_none():
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {"files": []}

    assert drive.find_newest_mp4(service, "folder1") is None
