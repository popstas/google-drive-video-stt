from unittest.mock import MagicMock

import requests

from src import notify
from src.notify import MAX_MESSAGE_LENGTH, notify_error

CREDS = {"telegram_bot_token": "token-xyz", "telegram_chat_id": "12345"}


def test_sends_message_when_credentials_set(monkeypatch):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello world", **CREDS)

    post_mock.assert_called_once()
    args, kwargs = post_mock.call_args
    assert args[0] == "https://api.telegram.org/bottoken-xyz/sendMessage"
    assert kwargs["data"] == {"chat_id": "12345", "text": "hello world"}
    assert kwargs["timeout"] == 10


def test_skips_when_token_missing(monkeypatch):
    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello", telegram_chat_id="12345")

    post_mock.assert_not_called()


def test_skips_when_chat_id_missing(monkeypatch):
    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello", telegram_bot_token="token-xyz")

    post_mock.assert_not_called()


def test_skips_when_both_missing(monkeypatch):
    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello")

    post_mock.assert_not_called()


def test_skips_when_credentials_blank(monkeypatch):
    post_mock = MagicMock()
    monkeypatch.setattr(notify.requests, "post", post_mock)

    notify_error("hello", telegram_bot_token="   ", telegram_chat_id="   ")

    post_mock.assert_not_called()


def test_truncates_long_message(monkeypatch):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    long_text = "x" * (MAX_MESSAGE_LENGTH + 500)
    notify_error(long_text, **CREDS)

    _, kwargs = post_mock.call_args
    assert len(kwargs["data"]["text"]) == MAX_MESSAGE_LENGTH
    assert kwargs["data"]["text"] == "x" * MAX_MESSAGE_LENGTH


def test_message_at_limit_not_truncated(monkeypatch):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    text = "y" * MAX_MESSAGE_LENGTH
    notify_error(text, **CREDS)

    _, kwargs = post_mock.call_args
    assert kwargs["data"]["text"] == text


def test_does_not_raise_on_http_error(monkeypatch, caplog):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=requests.HTTPError("400 Bad Request")
    )
    post_mock = MagicMock(return_value=mock_response)
    monkeypatch.setattr(notify.requests, "post", post_mock)

    with caplog.at_level("WARNING"):
        notify_error("hello", **CREDS)

    assert any("Failed to send Telegram notification" in rec.message for rec in caplog.records)


def test_does_not_raise_on_connection_error(monkeypatch, caplog):
    post_mock = MagicMock(side_effect=requests.ConnectionError("network down"))
    monkeypatch.setattr(notify.requests, "post", post_mock)

    with caplog.at_level("WARNING"):
        notify_error("hello", **CREDS)

    assert any("Failed to send Telegram notification" in rec.message for rec in caplog.records)


def test_does_not_raise_on_timeout(monkeypatch, caplog):
    post_mock = MagicMock(side_effect=requests.Timeout("timed out"))
    monkeypatch.setattr(notify.requests, "post", post_mock)

    with caplog.at_level("WARNING"):
        notify_error("hello", **CREDS)

    assert any("Failed to send Telegram notification" in rec.message for rec in caplog.records)
