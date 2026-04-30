from pathlib import Path

import pytest

from src.config import load_config

ENV_VARS = [
    "FOLDER_IDS",
    "POLL_INTERVAL",
    "BITRATE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DATA_DIR",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_defaults_when_no_env(monkeypatch):
    cfg = load_config()
    assert cfg.folder_ids == []
    assert cfg.poll_interval == 600
    assert cfg.bitrate == "96k"
    assert cfg.telegram_bot_token == ""
    assert cfg.telegram_chat_id == ""
    assert cfg.data_dir == Path("data")


def test_parses_single_folder_id(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "abc123")
    cfg = load_config()
    assert cfg.folder_ids == ["abc123"]


def test_parses_multiple_folder_ids(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "id1,id2,id3")
    cfg = load_config()
    assert cfg.folder_ids == ["id1", "id2", "id3"]


def test_strips_whitespace_in_folder_ids(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", " id1 , id2 ,  ,id3 ")
    cfg = load_config()
    assert cfg.folder_ids == ["id1", "id2", "id3"]


def test_empty_folder_ids_returns_empty_list(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "")
    cfg = load_config()
    assert cfg.folder_ids == []


def test_folder_ids_only_commas(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", " , , ")
    cfg = load_config()
    assert cfg.folder_ids == []


def test_custom_poll_interval(monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL", "120")
    cfg = load_config()
    assert cfg.poll_interval == 120


def test_invalid_poll_interval_raises(monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL", "not-a-number")
    with pytest.raises(ValueError, match="POLL_INTERVAL"):
        load_config()


def test_non_positive_poll_interval_raises(monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL", "0")
    with pytest.raises(ValueError, match="positive"):
        load_config()


def test_custom_bitrate(monkeypatch):
    monkeypatch.setenv("BITRATE", "128k")
    cfg = load_config()
    assert cfg.bitrate == "128k"


def test_blank_bitrate_uses_default(monkeypatch):
    monkeypatch.setenv("BITRATE", "")
    cfg = load_config()
    assert cfg.bitrate == "96k"


def test_telegram_credentials(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    cfg = load_config()
    assert cfg.telegram_bot_token == "token-xyz"
    assert cfg.telegram_chat_id == "12345"


def test_custom_data_dir(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "/var/lib/stt")
    cfg = load_config()
    assert cfg.data_dir == Path("/var/lib/stt")


def test_blank_data_dir_uses_default(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "")
    cfg = load_config()
    assert cfg.data_dir == Path("data")


def test_full_env_combination(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "f1,f2")
    monkeypatch.setenv("POLL_INTERVAL", "300")
    monkeypatch.setenv("BITRATE", "192k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")
    monkeypatch.setenv("DATA_DIR", "mydata")
    cfg = load_config()
    assert cfg.folder_ids == ["f1", "f2"]
    assert cfg.poll_interval == 300
    assert cfg.bitrate == "192k"
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "cid"
    assert cfg.data_dir == Path("mydata")
