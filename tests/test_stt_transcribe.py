from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.stt import transcribe as transcribe_mod
from src.stt.base import STTError


def _cfg(provider="openai") -> Config:
    return Config(
        folder_ids=[],
        poll_interval=600,
        bitrate="96k",
        telegram_bot_token="",
        telegram_chat_id="",
        data_dir=Path("data"),
        proxy_url="",
        stt_provider=provider,
        openai_api_key="sk-x" if provider == "openai" else "",
        deepgram_api_key="dg-x" if provider == "deepgram" else "",
        google_cloud_project="p" if provider == "google" else "",
        google_stt_gcs_bucket="b" if provider == "google" else "",
        asr_url="http://localhost:9000" if provider == "asr" else "",
        stt_language="ru" if provider == "deepgram" else "",
        stt_chunk_seconds=600,
    )


def test_transcribe_file_disabled_raises(tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    with pytest.raises(STTError, match="STT_PROVIDER"):
        transcribe_mod.transcribe_file(mp3, _cfg(provider=""))


def test_transcribe_file_chunks_and_merges(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"audio-data")

    chunk_paths = [tmp_path / "c1.mp3", tmp_path / "c2.mp3", tmp_path / "c3.mp3"]
    for c in chunk_paths:
        c.write_bytes(b"x")

    mocker.patch("src.stt.transcribe.chunk_mp3", return_value=chunk_paths)

    provider = MagicMock()
    provider.transcribe_full.return_value = None
    provider.transcribe_chunk.side_effect = ["one", "two", "three"]
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    text = transcribe_mod.transcribe_file(mp3, _cfg())

    assert text == "one\n\ntwo\n\nthree"
    assert provider.transcribe_chunk.call_count == 3


def test_transcribe_file_skips_empty_parts(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    chunks = [tmp_path / "c1.mp3", tmp_path / "c2.mp3"]
    for c in chunks:
        c.write_bytes(b"x")

    mocker.patch("src.stt.transcribe.chunk_mp3", return_value=chunks)
    provider = MagicMock()
    provider.transcribe_full.return_value = None
    provider.transcribe_chunk.side_effect = ["", "later"]
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    assert transcribe_mod.transcribe_file(mp3, _cfg()) == "later"


def test_transcribe_file_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_mod.transcribe_file(tmp_path / "missing.mp3", _cfg())


def test_transcribe_full_returning_none_falls_back_to_chunking(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    chunks = [tmp_path / "c1.mp3"]
    chunks[0].write_bytes(b"x")

    chunk_mock = mocker.patch("src.stt.transcribe.chunk_mp3", return_value=chunks)
    provider = MagicMock()
    provider.transcribe_full.return_value = None
    provider.transcribe_chunk.return_value = "chunked"
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    result = transcribe_mod.transcribe_file(mp3, _cfg())

    assert result == "chunked"
    provider.transcribe_full.assert_called_once_with(mp3)
    assert chunk_mock.called
    assert provider.transcribe_chunk.call_count == 1


def test_transcribe_full_returning_string_skips_chunking(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    chunk_mock = mocker.patch("src.stt.transcribe.chunk_mp3")
    provider = MagicMock()
    provider.transcribe_full.return_value = "[00:00:00] Speaker 1: hello"
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    result = transcribe_mod.transcribe_file(mp3, _cfg(provider="google"))

    assert result == "[00:00:00] Speaker 1: hello"
    provider.transcribe_full.assert_called_once_with(mp3)
    chunk_mock.assert_not_called()
    provider.transcribe_chunk.assert_not_called()


def test_transcribe_full_empty_string_raises(mocker, tmp_path):
    """Empty transcript should fail instead of uploading a blank TXT later."""
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    chunk_mock = mocker.patch("src.stt.transcribe.chunk_mp3")
    provider = MagicMock()
    provider.transcribe_full.return_value = ""
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    with pytest.raises(STTError, match="empty transcript"):
        transcribe_mod.transcribe_file(mp3, _cfg(provider="google"))

    chunk_mock.assert_not_called()
    provider.transcribe_chunk.assert_not_called()


def test_transcribe_chunks_all_empty_raises(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    chunks = [tmp_path / "c1.mp3", tmp_path / "c2.mp3"]
    for chunk in chunks:
        chunk.write_bytes(b"x")

    mocker.patch("src.stt.transcribe.chunk_mp3", return_value=chunks)
    provider = MagicMock()
    provider.transcribe_full.return_value = None
    provider.transcribe_chunk.side_effect = ["", ""]
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    with pytest.raises(STTError, match="empty transcript"):
        transcribe_mod.transcribe_file(mp3, _cfg(provider="openai"))


def test_transcribe_file_deepgram_full_file_skips_chunking(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    chunk_mock = mocker.patch("src.stt.transcribe.chunk_mp3")
    provider = MagicMock()
    provider.transcribe_full.return_value = "[00:00:00] Speaker 1: привет"
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    result = transcribe_mod.transcribe_file(mp3, _cfg(provider="deepgram"))

    assert result == "[00:00:00] Speaker 1: привет"
    provider.transcribe_full.assert_called_once_with(mp3)
    chunk_mock.assert_not_called()
    provider.transcribe_chunk.assert_not_called()


def test_transcribe_file_logs_deepgram_cost(mocker, tmp_path, caplog):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    provider = MagicMock()
    provider.transcribe_full.return_value = "[00:00:00] Speaker 1: text"
    provider.last_request_id = "request-1"
    provider.last_duration_seconds = 12.5
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)
    fetch_mock = mocker.patch(
        "src.stt.transcribe.fetch_request_cost_usd",
        return_value=0.012345,
    )

    with caplog.at_level("INFO"):
        result = transcribe_mod.transcribe_file(mp3, _cfg(provider="deepgram"))

    assert result == "[00:00:00] Speaker 1: text"
    fetch_mock.assert_called_once_with(
        "dg-x",
        "request-1",
        proxy_url="",
    )
    assert "Deepgram cost for a.mp3: $0.012345" in caplog.text
    assert "duration=12.50s" in caplog.text


def test_transcribe_file_logs_deepgram_cost_unavailable(mocker, tmp_path, caplog):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    provider = MagicMock()
    provider.transcribe_full.return_value = "[00:00:00] Speaker 1: text"
    provider.last_request_id = "request-1"
    provider.last_duration_seconds = None
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)
    mocker.patch("src.stt.transcribe.fetch_request_cost_usd", return_value=None)

    with caplog.at_level("INFO"):
        transcribe_mod.transcribe_file(mp3, _cfg(provider="deepgram"))

    assert "Deepgram cost unavailable yet for a.mp3" in caplog.text
    assert "request_id=request-1" in caplog.text
