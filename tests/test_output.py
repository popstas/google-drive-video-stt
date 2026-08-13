from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src import drive, output
from src.config import Config


def make_config(output_target="drive", output_dir=None, output_also_drive=False) -> Config:
    return Config(
        folders=["folderA"],
        poll_interval=600,
        bitrate="96k",
        data_dir=Path("data"),
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="",
        deepgram_api_key="dg-x",
        stt_language="ru",
        output_target=output_target,
        output_dir=output_dir,
        output_also_drive=output_also_drive,
    )


def test_folder_mode_also_drive_writes_both_copies(mocker, tmp_path):
    """The local file stays authoritative; Drive gets a published copy alongside it."""
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")
    out_dir = tmp_path / "results"

    output.write_artifact(
        service,
        base_name="Alice and Bob - 2026/08/13",
        suffix=".txt",
        text="привет",
        folder_id="folderA",
        config=make_config("folder", out_dir, output_also_drive=True),
        tmp_dir=tmp_path,
    )

    local = out_dir / (drive.safe_local_name("Alice and Bob - 2026/08/13") + ".txt")
    assert local.read_text(encoding="utf-8") == "привет"
    assert upload_mock.call_count == 1
    assert upload_mock.call_args.kwargs["name"] == "Alice and Bob - 2026/08/13.txt"


def test_folder_mode_without_also_drive_never_touches_drive(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")

    output.write_artifact(
        service,
        base_name="Alice",
        suffix=".txt",
        text="привет",
        folder_id="folderA",
        config=make_config("folder", tmp_path / "results"),
        tmp_dir=tmp_path,
    )

    assert upload_mock.call_count == 0


def test_a_failed_drive_publish_keeps_the_local_artifact(mocker, tmp_path):
    """The transcript already cost money; a Drive outage must not throw it away."""
    service = MagicMock()
    mocker.patch("src.output.drive.upload", side_effect=RuntimeError("drive down"))
    out_dir = tmp_path / "results"

    output.write_artifact(
        service,
        base_name="Alice",
        suffix=".txt",
        text="привет",
        folder_id="folderA",
        config=make_config("folder", out_dir, output_also_drive=True),
        tmp_dir=tmp_path,
    )

    assert (out_dir / "Alice.txt").read_text(encoding="utf-8") == "привет"


def test_write_artifact_drive_uploads_new_sibling(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")
    update_mock = mocker.patch("src.output.drive.update_file")

    output.write_artifact(
        service,
        base_name="video",
        suffix=".txt",
        text="hello",
        folder_id="folderA",
        config=make_config(),
        tmp_dir=tmp_path,
        app_properties={"source_video_id": "fid"},
    )

    update_mock.assert_not_called()
    upload_mock.assert_called_once()
    assert upload_mock.call_args.kwargs["name"] == "video.txt"
    assert upload_mock.call_args.kwargs["mime_type"] == drive.TXT_MIME
    assert upload_mock.call_args.kwargs["app_properties"] == {"source_video_id": "fid"}
    local_path = upload_mock.call_args.args[1]
    assert local_path.read_text(encoding="utf-8") == "hello"


def test_write_artifact_drive_updates_existing_sibling(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")
    update_mock = mocker.patch("src.output.drive.update_file")

    output.write_artifact(
        service,
        base_name="video",
        suffix=".txt",
        text="fresh text",
        folder_id="folderA",
        config=make_config(),
        tmp_dir=tmp_path,
        existing_id="t1",
    )

    upload_mock.assert_not_called()
    update_mock.assert_called_once()
    assert update_mock.call_args.args[1] == "t1"
    assert update_mock.call_args.args[2].read_text(encoding="utf-8") == "fresh text"


def test_write_artifact_drive_sanitizes_local_temp_name(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")
    mocker.patch("src.output.drive.update_file")

    output.write_artifact(
        service,
        base_name="Call 2026/05/28 Rec",
        suffix=".txt",
        text="x",
        folder_id="folderA",
        config=make_config(),
        tmp_dir=tmp_path,
    )

    # Drive name keeps the slashes; the local temp file is sanitized.
    assert upload_mock.call_args.kwargs["name"] == "Call 2026/05/28 Rec.txt"
    local_path = upload_mock.call_args.args[1]
    assert "/" not in local_path.name


def test_write_artifact_folder_writes_file_and_creates_dir(mocker, tmp_path):
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")
    update_mock = mocker.patch("src.output.drive.update_file")
    out_dir = tmp_path / "transcripts"

    output.write_artifact(
        service,
        base_name="video",
        suffix=".txt",
        text="folder content",
        folder_id="folderA",
        config=make_config(output_target="folder", output_dir=out_dir),
        tmp_dir=tmp_path,
        existing_id="ignored",
    )

    upload_mock.assert_not_called()
    update_mock.assert_not_called()
    written = out_dir / "video.txt"
    assert written.read_text(encoding="utf-8") == "folder content"


def test_write_artifact_folder_sanitizes_slash_name(mocker, tmp_path):
    service = MagicMock()
    mocker.patch("src.output.drive.upload")
    mocker.patch("src.output.drive.update_file")
    out_dir = tmp_path / "out"

    output.write_artifact(
        service,
        base_name="Call 2026/05/28 Rec",
        suffix=".keypoints.md",
        text="notes",
        folder_id="folderA",
        config=make_config(output_target="folder", output_dir=out_dir),
        tmp_dir=tmp_path,
    )

    children = list(out_dir.iterdir())
    assert len(children) == 1
    assert "/" not in children[0].name
    assert children[0].suffix == ".md"
    assert children[0].read_text(encoding="utf-8") == "notes"


def test_write_artifact_folder_keys_by_stem_overwrites_in_place(mocker, tmp_path):
    """Folder mode keys the artifact by sanitized stem: same name overwrites."""
    service = MagicMock()
    upload_mock = mocker.patch("src.output.drive.upload")
    update_mock = mocker.patch("src.output.drive.update_file")
    out_dir = tmp_path / "out"

    def write(text, existing_id=None):
        output.write_artifact(
            service,
            base_name="video",
            suffix=".txt",
            text=text,
            folder_id="folderA",
            config=make_config(output_target="folder", output_dir=out_dir),
            tmp_dir=tmp_path,
            existing_id=existing_id,
        )

    write("first")
    # existing_id is a Drive id and is ignored in folder mode; the deterministic
    # stem-keyed path is overwritten in place rather than duplicated.
    write("second", existing_id="drive-id-123")

    upload_mock.assert_not_called()
    update_mock.assert_not_called()
    children = list(out_dir.iterdir())
    assert len(children) == 1
    assert children[0].name == "video.txt"
    assert children[0].read_text(encoding="utf-8") == "second"


def test_write_artifact_folder_rename_orphans_old_file(mocker, tmp_path):
    """Documented caveat: renaming the source leaves the old local file behind."""
    service = MagicMock()
    mocker.patch("src.output.drive.upload")
    mocker.patch("src.output.drive.update_file")
    out_dir = tmp_path / "out"

    def write(base_name):
        output.write_artifact(
            service,
            base_name=base_name,
            suffix=".txt",
            text="x",
            folder_id="folderA",
            config=make_config(output_target="folder", output_dir=out_dir),
            tmp_dir=tmp_path,
            existing_id="some-drive-id",
        )

    write("old name")
    write("new name")

    names = sorted(p.name for p in out_dir.iterdir())
    # The rename produces a fresh file; the previous stem's file is orphaned.
    assert names == ["new name.txt", "old name.txt"]
