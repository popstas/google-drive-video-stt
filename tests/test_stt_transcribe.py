from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.stt import transcribe as transcribe_mod
from src.stt.base import STTError


def _cfg(provider="deepgram") -> Config:
    return Config(
        folder_ids=[],
        poll_interval=600,
        bitrate="96k",
        telegram_bot_token="",
        telegram_chat_id="",
        data_dir=Path("data"),
        proxy_url="",
        stt_provider=provider,
        openai_api_key="",
        deepgram_api_key="dg-x" if provider == "deepgram" else "",
        stt_language="ru" if provider == "deepgram" else "",
    )


def test_transcribe_file_disabled_raises(tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    with pytest.raises(STTError, match="STT_PROVIDER"):
        transcribe_mod.transcribe_file(mp3, _cfg(provider=""))


def test_transcribe_file_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_mod.transcribe_file(tmp_path / "missing.mp3", _cfg())


def test_transcribe_file_deepgram_full_file(mocker, tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    provider = MagicMock()
    provider.transcribe_full.return_value = "[00:00:00] Speaker 1: привет"
    provider.last_request_id = None
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    result = transcribe_mod.transcribe_file(mp3, _cfg(provider="deepgram"))

    assert result == "[00:00:00] Speaker 1: привет"
    provider.transcribe_full.assert_called_once_with(mp3)
    provider.transcribe_chunk.assert_not_called()


def test_transcribe_full_empty_string_raises(mocker, tmp_path):
    """Empty transcript should fail instead of uploading a blank TXT later."""
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")

    provider = MagicMock()
    provider.transcribe_full.return_value = ""
    provider.last_request_id = None
    mocker.patch("src.stt.transcribe.get_provider", return_value=provider)

    with pytest.raises(STTError, match="empty transcript"):
        transcribe_mod.transcribe_file(mp3, _cfg(provider="deepgram"))

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

    cost_usd = {}
    with caplog.at_level("INFO"):
        result = transcribe_mod.transcribe_file(
            mp3,
            _cfg(provider="deepgram"),
            cost_usd=cost_usd,
        )

    assert result == "[00:00:00] Speaker 1: text"
    fetch_mock.assert_called_once_with(
        "dg-x",
        "request-1",
        proxy_url="",
    )
    assert "Deepgram cost for a.mp3: $0.012345" in caplog.text
    assert "duration=12.50s" in caplog.text
    assert cost_usd == {"deepgram": 0.012345}


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
