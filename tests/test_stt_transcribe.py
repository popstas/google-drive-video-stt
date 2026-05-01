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
        google_cloud_project="p" if provider == "google" else "",
        google_application_credentials="/tmp/sa.json" if provider == "google" else "",
        stt_language="",
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
    provider.transcribe_chunk.side_effect = ["", "later"]
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    assert transcribe_mod.transcribe_file(mp3, _cfg()) == "later"


def test_transcribe_file_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_mod.transcribe_file(tmp_path / "missing.mp3", _cfg())
