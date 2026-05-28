from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src import extractor


def test_extract_mp3_success(tmp_path, mocker):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake mp4 data")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        output = Path(cmd[-1])
        output.write_bytes(b"id3 fake mp3")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    run_mock = mocker.patch("src.extractor.subprocess.run", side_effect=fake_run)

    result = extractor.extract_mp3(mp4)

    assert result == mp4.with_suffix(".mp3")
    assert result.exists()

    cmd = run_mock.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "-vn" in cmd
    assert "libmp3lame" in cmd
    assert "96k" in cmd
    assert str(mp4) in cmd
    assert str(result) in cmd


def test_extract_mp3_uses_custom_bitrate(tmp_path, mocker):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"mp3")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    run_mock = mocker.patch("src.extractor.subprocess.run", side_effect=fake_run)

    extractor.extract_mp3(mp4, bitrate="128k")

    assert "128k" in run_mock.call_args.args[0]


def test_extract_m4a_copy_success(tmp_path, mocker):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake mp4 data")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        output = Path(cmd[-1])
        output.write_bytes(b"m4a")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    run_mock = mocker.patch("src.extractor.subprocess.run", side_effect=fake_run)

    result = extractor.extract_m4a_copy(mp4)

    assert result == mp4.with_suffix(".m4a")
    cmd = run_mock.call_args.args[0]
    assert "-vn" in cmd
    assert "-c:a" in cmd
    assert "copy" in cmd


def test_extract_mp3_missing_input(tmp_path):
    missing = tmp_path / "nope.mp4"

    with pytest.raises(FileNotFoundError):
        extractor.extract_mp3(missing)


def test_extract_mp3_missing_ffmpeg_binary(tmp_path, mocker):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")

    mocker.patch("src.extractor.shutil.which", return_value=None)

    with pytest.raises(extractor.FFmpegError, match="ffmpeg"):
        extractor.extract_mp3(mp4)


def test_extract_mp3_ffmpeg_nonzero_exit(tmp_path, mocker):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch(
        "src.extractor.subprocess.run",
        return_value=subprocess.CompletedProcess(["ffmpeg"], 1, "", "Invalid data"),
    )

    with pytest.raises(extractor.FFmpegError, match="exit code 1"):
        extractor.extract_mp3(mp4)


def test_extract_mp3_subprocess_filenotfound_raises_ffmpeg_error(tmp_path, mocker):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch(
        "src.extractor.subprocess.run",
        side_effect=FileNotFoundError("no ffmpeg"),
    )

    with pytest.raises(extractor.FFmpegError, match="ffmpeg binary not found"):
        extractor.extract_mp3(mp4)


def test_extract_mp3_subprocess_timeout_raises_ffmpeg_error(tmp_path, mocker):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch(
        "src.extractor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1),
    )

    with pytest.raises(extractor.FFmpegError, match="timed out"):
        extractor.extract_mp3(mp4)


def test_extract_mp3_output_missing_after_success(tmp_path, mocker):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x")

    mocker.patch("src.extractor.shutil.which", return_value="/usr/bin/ffmpeg")
    mocker.patch(
        "src.extractor.subprocess.run",
        return_value=subprocess.CompletedProcess(["ffmpeg"], 0, "", ""),
    )

    with pytest.raises(extractor.FFmpegError, match="output not found"):
        extractor.extract_mp3(mp4)
