from __future__ import annotations

from unittest.mock import MagicMock

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
        else:
            request.execute.return_value = {"files": []}
        return request

    files_resource.list.side_effect = list_side_effect
    return service


def test_list_unprocessed_mp4_no_files():
    service = _make_list_service({"mp4": [], "mp3": []})

    result = drive.list_unprocessed_mp4(service, "folder1")

    assert result == []


def test_list_unprocessed_mp4_all_processed():
    mp4 = [
        {"id": "v1", "name": "video1.mp4", "mimeType": "video/mp4"},
        {"id": "v2", "name": "video2.mp4", "mimeType": "video/mp4"},
    ]
    mp3 = [
        {"id": "a1", "name": "video1.mp3", "mimeType": "audio/mpeg"},
        {"id": "a2", "name": "video2.mp3", "mimeType": "audio/mpeg"},
    ]
    service = _make_list_service({"mp4": mp4, "mp3": mp3})

    result = drive.list_unprocessed_mp4(service, "folder1")

    assert result == []


def test_list_unprocessed_mp4_some_unprocessed():
    mp4 = [
        {"id": "v1", "name": "video1.mp4", "mimeType": "video/mp4"},
        {"id": "v2", "name": "video2.mp4", "mimeType": "video/mp4"},
        {"id": "v3", "name": "video3.mp4", "mimeType": "video/mp4"},
    ]
    mp3 = [{"id": "a1", "name": "video1.mp3", "mimeType": "audio/mpeg"}]
    service = _make_list_service({"mp4": mp4, "mp3": mp3})

    result = drive.list_unprocessed_mp4(service, "folder1")

    assert [f["id"] for f in result] == ["v2", "v3"]


def test_list_unprocessed_mp4_query_includes_folder_and_mime():
    mp4 = [{"id": "v1", "name": "video1.mp4", "mimeType": "video/mp4"}]
    service = _make_list_service({"mp4": mp4, "mp3": []})

    drive.list_unprocessed_mp4(service, "myfolder")

    calls = service.files.return_value.list.call_args_list
    assert len(calls) == 2
    queries = [call.kwargs["q"] for call in calls]
    assert any("'myfolder' in parents" in q and "video/mp4" in q for q in queries)
    assert any("'myfolder' in parents" in q and "audio/mpeg" in q for q in queries)
    for call in calls:
        assert call.kwargs["supportsAllDrives"] is True
        assert call.kwargs["includeItemsFromAllDrives"] is True


def test_list_unprocessed_mp4_handles_pagination():
    service = MagicMock()
    files_resource = MagicMock()
    service.files.return_value = files_resource

    page1 = {
        "files": [{"id": "v1", "name": "a.mp4", "mimeType": "video/mp4"}],
        "nextPageToken": "tok",
    }
    page2 = {
        "files": [{"id": "v2", "name": "b.mp4", "mimeType": "video/mp4"}],
    }
    mp3_response = {"files": []}

    responses = {"mp4_seen": False}

    def list_side_effect(**kwargs):
        q = kwargs["q"]
        token = kwargs.get("pageToken")
        request = MagicMock()
        if "video/mp4" in q:
            if not responses["mp4_seen"]:
                responses["mp4_seen"] = True
                request.execute.return_value = page1
            else:
                assert token == "tok"
                request.execute.return_value = page2
        else:
            request.execute.return_value = mp3_response
        return request

    files_resource.list.side_effect = list_side_effect

    result = drive.list_unprocessed_mp4(service, "folder1")

    assert [f["id"] for f in result] == ["v1", "v2"]


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


def test_rename_file_updates_name_only():
    service = MagicMock()
    service.files.return_value.update.return_value.execute.return_value = {
        "id": "t1",
        "name": "new.txt",
    }

    drive.rename_file(service, "t1", "new.txt")

    service.files.return_value.update.assert_called_once_with(
        fileId="t1",
        body={"name": "new.txt"},
        fields="id, name",
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


def test_list_unprocessed_mp4_matches_mp3_by_source_video_id_after_rename():
    mp4 = [{"id": "v1", "name": "new name.mp4", "mimeType": "video/mp4"}]
    mp3 = [{
        "id": "m1",
        "name": "old name.mp3",
        "mimeType": "audio/mpeg",
        "appProperties": {"source_video_id": "v1", "artifact_type": "mp3"},
    }]
    service = _make_list_service({"mp4": mp4, "mp3": mp3})

    result = drive.list_unprocessed_mp4(service, "folder1")

    assert result == []


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


def test_list_unprocessed_mp4_matches_siblings_with_slashes():
    mp4 = [
        {"id": "a", "name": "A 2026/01/01.mp4", "mimeType": "video/mp4"},
        {"id": "b", "name": "B 2026/02/02.mp4", "mimeType": "video/mp4"},
    ]
    mp3 = [{"id": "c", "name": "A 2026/01/01.mp3", "mimeType": "audio/mpeg"}]
    service = _make_list_service({"mp4": mp4, "mp3": mp3})

    result = drive.list_unprocessed_mp4(service, "folder1")

    assert [f["id"] for f in result] == ["b"]


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
