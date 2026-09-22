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


# --- send_message ---------------------------------------------------------------


def _post_mock(monkeypatch, *, ok=True):
    response = MagicMock()
    if ok:
        response.raise_for_status = MagicMock()
    else:
        response.raise_for_status = MagicMock(side_effect=requests.HTTPError("boom"))
    post = MagicMock(return_value=response)
    monkeypatch.setattr(notify.requests, "post", post)
    return post


def test_split_message_keeps_a_short_text_whole():
    assert notify.split_message("Задачи\n- раз") == ["Задачи\n- раз"]


def test_split_message_breaks_on_line_boundaries():
    text = "\n".join(["a" * 30] * 4)

    chunks = notify.split_message(text, limit=70)

    assert chunks == ["a" * 30 + "\n" + "a" * 30] * 2
    assert all(len(chunk) <= 70 for chunk in chunks)


def test_split_message_hard_splits_a_line_longer_than_the_limit():
    """There is nothing better to break on, and dropping the tail would lose content."""
    chunks = notify.split_message("b" * 25, limit=10)

    assert chunks == ["b" * 10, "b" * 10, "b" * 5]


def test_split_message_of_blank_text_is_empty():
    assert notify.split_message("   \n  ") == []


def test_send_message_posts_the_text(monkeypatch):
    post = _post_mock(monkeypatch)

    assert notify.send_message("Задачи", bot_token="t", chat_id="-100") is True

    post.assert_called_once()
    assert post.call_args[0][0] == "https://api.telegram.org/bott/sendMessage"
    assert post.call_args.kwargs["data"] == {"chat_id": "-100", "text": "Задачи"}


def test_send_message_sends_every_chunk_of_a_long_text(monkeypatch):
    post = _post_mock(monkeypatch)
    text = "\n".join(["c" * 200] * (MAX_MESSAGE_LENGTH // 100))

    assert notify.send_message(text, bot_token="t", chat_id="-100") is True

    assert post.call_count > 1
    sent = "\n".join(call.kwargs["data"]["text"] for call in post.call_args_list)
    assert sent == text


def test_send_message_without_credentials_does_nothing(monkeypatch):
    post = _post_mock(monkeypatch)

    assert notify.send_message("Задачи", bot_token="", chat_id="-100") is False
    assert notify.send_message("Задачи", bot_token="t", chat_id=" ") is False

    post.assert_not_called()


def test_send_message_of_blank_text_does_nothing(monkeypatch):
    post = _post_mock(monkeypatch)

    assert notify.send_message("  ", bot_token="t", chat_id="-100") is False

    post.assert_not_called()


def test_send_message_reports_a_failure(monkeypatch):
    """A False return is what keeps the caller from marking a lost summary delivered."""
    _post_mock(monkeypatch, ok=False)

    assert notify.send_message("Задачи", bot_token="t", chat_id="-100") is False


def test_send_message_passes_the_proxy(monkeypatch):
    post = _post_mock(monkeypatch)

    notify.send_message(
        "Задачи", bot_token="t", chat_id="-100", proxy_url="http://proxy:3128"
    )

    assert post.call_args.kwargs["proxies"] == {
        "http": "http://proxy:3128", "https": "http://proxy:3128",
    }
