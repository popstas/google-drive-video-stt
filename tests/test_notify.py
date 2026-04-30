from unittest.mock import MagicMock

import pytest
import requests

from src import notify
from src.notify import MAX_MESSAGE_LENGTH, notify_error

ENV_VARS = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_sends_message_when_credentials_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello world")

    post_mock.assert_called_once()
    args, kwargs = post_mock.call_args
    assert args[0] == "https://api.telegram.org/bottoken-xyz/sendMessage"
    assert kwargs["data"] == {"chat_id": "12345", "text": "hello world"}
    assert kwargs["timeout"] == 10


def test_skips_when_token_missing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello")

    post_mock.assert_not_called()


def test_skips_when_chat_id_missing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")

    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello")

    post_mock.assert_not_called()


def test_skips_when_both_missing(monkeypatch):
    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello")

    post_mock.assert_not_called()


def test_skips_when_credentials_blank(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "   ")

    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello")

    post_mock.assert_not_called()


def test_truncates_long_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    long_text = "x" * (MAX_MESSAGE_LENGTH + 500)
    notify_error(long_text)

    _, kwargs = post_mock.call_args
    assert len(kwargs["data"]["text"]) == MAX_MESSAGE_LENGTH
    assert kwargs["data"]["text"] == "x" * MAX_MESSAGE_LENGTH


def test_message_at_limit_not_truncated(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    text = "y" * MAX_MESSAGE_LENGTH
    notify_error(text)

    _, kwargs = post_mock.call_args
    assert kwargs["data"]["text"] == text


def test_does_not_raise_on_http_error(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=requests.HTTPError("400 Bad Request")
    )
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    with caplog.at_level("WARNING"):
        notify_error("hello")

    assert any("Failed to send Telegram notification" in rec.message for rec in caplog.records)


def test_does_not_raise_on_connection_error(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    post_mock = MagicMock(side_effect=requests.ConnectionError("network down"))
    monkeypatch.setattr(notify.requests, "post", post_mock)

    with caplog.at_level("WARNING"):
        notify_error("hello")

    assert any("Failed to send Telegram notification" in rec.message for rec in caplog.records)


def test_does_not_raise_on_timeout(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    post_mock = MagicMock(side_effect=requests.Timeout("timed out"))
    monkeypatch.setattr(notify.requests, "post", post_mock)

    with caplog.at_level("WARNING"):
        notify_error("hello")

    assert any("Failed to send Telegram notification" in rec.message for rec in caplog.records)
