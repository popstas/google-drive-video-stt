from __future__ import annotations

from pathlib import Path

import pytest

from src.stt.chunker import ChunkError, chunk_mp3


def test_chunk_mp3_invokes_ffmpeg_segment(mocker, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"id3")
    out_dir = tmp_path / "chunks"

    mocker.patch("src.stt.chunker.shutil.which", return_value="/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        # Simulate ffmpeg producing chunks based on the pattern
        pattern = cmd[-1]
        # last arg is /tmp/.../chunks/a_chunk_%04d.mp3 → produce two chunks
        out_dir_path = Path(pattern).parent
        out_dir_path.mkdir(parents=True, exist_ok=True)
        (out_dir_path / "a_chunk_0000.mp3").write_bytes(b"c1")
        (out_dir_path / "a_chunk_0001.mp3").write_bytes(b"c2")
        result = mocker.MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    run_mock = mocker.patch("src.stt.chunker.subprocess.run", side_effect=fake_run)

    chunks = chunk_mp3(src, 600, out_dir)

    assert len(chunks) == 2
    assert all(c.exists() for c in chunks)
    cmd = run_mock.call_args.args[0]
    assert "ffmpeg" in cmd[0]
    assert "-segment_time" in cmd
    assert "600" in cmd


def test_chunk_mp3_missing_input_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        chunk_mp3(tmp_path / "missing.mp3", 600, tmp_path / "out")


def test_chunk_mp3_invalid_chunk_seconds(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        chunk_mp3(src, 0, tmp_path / "out")


def test_chunk_mp3_no_ffmpeg(mocker, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    mocker.patch("src.stt.chunker.shutil.which", return_value=None)
    with pytest.raises(ChunkError, match="ffmpeg binary not found"):
        chunk_mp3(src, 600, tmp_path / "out")


def test_chunk_mp3_ffmpeg_failure(mocker, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    mocker.patch("src.stt.chunker.shutil.which", return_value="/usr/bin/ffmpeg")
    fake_result = mocker.MagicMock()
    fake_result.returncode = 1
    fake_result.stderr = "boom"
    mocker.patch("src.stt.chunker.subprocess.run", return_value=fake_result)
    with pytest.raises(ChunkError, match="exit code 1"):
        chunk_mp3(src, 600, tmp_path / "out")


def test_chunk_mp3_no_output_files(mocker, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out_dir = tmp_path / "out"
    mocker.patch("src.stt.chunker.shutil.which", return_value="/usr/bin/ffmpeg")
    fake_result = mocker.MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""
    mocker.patch("src.stt.chunker.subprocess.run", return_value=fake_result)
    with pytest.raises(ChunkError, match="produced no output"):
        chunk_mp3(src, 600, out_dir)
