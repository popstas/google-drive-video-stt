from unittest.mock import MagicMock

import pytest
import requests

from src import webhook
from src.webhook import notify_complete

PAYLOAD = {
    "file": {"id": "1a2b", "name": "call.mp4", "folder_id": "f1"},
    "employee": {"name": "Олег Иванов", "email": "oleg@expertizeme.org"},
    "transcript": "Ольга: привет",
    "artifacts": {"meta": {"topic": "O-1", "tags": ["O-1"]}},
}


def _post_mock(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    post = MagicMock(return_value=response)
    monkeypatch.setattr(webhook.requests, "post", post)
    return post


def test_posts_payload_when_url_set(monkeypatch):
    post = _post_mock(monkeypatch)

    notify_complete(url="https://example.com/hooks/gdstt", payload=PAYLOAD)

    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://example.com/hooks/gdstt"
    assert kwargs["json"] == PAYLOAD
    assert kwargs["timeout"] == 10
    assert kwargs["proxies"] is None


def test_skips_when_url_missing(monkeypatch):
    post = _post_mock(monkeypatch)

    notify_complete(url="", payload=PAYLOAD)

    post.assert_not_called()


def test_skips_when_url_blank(monkeypatch):
    post = _post_mock(monkeypatch)

    notify_complete(url="   ", payload=PAYLOAD)

    post.assert_not_called()


def test_sends_bearer_token_when_set(monkeypatch):
    post = _post_mock(monkeypatch)

    notify_complete(url="https://example.com/h", token="secret-xyz", payload=PAYLOAD)

    _, kwargs = post.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer secret-xyz"}


def test_sends_no_auth_header_when_token_blank(monkeypatch):
    post = _post_mock(monkeypatch)

    notify_complete(url="https://example.com/h", token="   ", payload=PAYLOAD)

    _, kwargs = post.call_args
    assert kwargs["headers"] is None


def test_uses_proxy_when_set(monkeypatch):
    post = _post_mock(monkeypatch)

    notify_complete(
        url="https://example.com/h", proxy_url="http://proxy:3128", payload=PAYLOAD
    )

    _, kwargs = post.call_args
    assert kwargs["proxies"] == {"http": "http://proxy:3128", "https": "http://proxy:3128"}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/h",
        "http://127.0.0.1:8080/h",
        "http://[::1]:8080/h",
    ],
)
def test_bypasses_proxy_for_loopback_receiver(monkeypatch, url):
    post = _post_mock(monkeypatch)

    # A loopback receiver is unreachable through an egress proxy, and routing it there
    # would push the token and transcript off-host — which is exactly what the
    # loopback exemption in the plaintext warning promises will not happen.
    notify_complete(url=url, proxy_url="http://proxy:3128", payload=PAYLOAD)

    _, kwargs = post.call_args
    assert kwargs["proxies"] == {"http": None, "https": None}


def test_does_not_raise_on_http_error(monkeypatch, caplog):
    response = MagicMock()
    response.raise_for_status = MagicMock(
        side_effect=requests.HTTPError("400 Bad Request")
    )
    monkeypatch.setattr(webhook.requests, "post", MagicMock(return_value=response))

    with caplog.at_level("WARNING"):
        notify_complete(url="https://example.com/h", payload=PAYLOAD)

    assert any("Failed to send completion webhook" in rec.message for rec in caplog.records)


def test_does_not_raise_on_connection_error(monkeypatch, caplog):
    monkeypatch.setattr(
        webhook.requests, "post", MagicMock(side_effect=requests.ConnectionError("down"))
    )

    with caplog.at_level("WARNING"):
        notify_complete(url="https://example.com/h", payload=PAYLOAD)

    assert any("Failed to send completion webhook" in rec.message for rec in caplog.records)


def test_does_not_raise_on_timeout(monkeypatch, caplog):
    monkeypatch.setattr(
        webhook.requests, "post", MagicMock(side_effect=requests.Timeout("timed out"))
    )

    with caplog.at_level("WARNING"):
        notify_complete(url="https://example.com/h", payload=PAYLOAD)

    assert any("Failed to send completion webhook" in rec.message for rec in caplog.records)


def test_failure_log_omits_token_and_payload(monkeypatch, caplog):
    monkeypatch.setattr(
        webhook.requests, "post", MagicMock(side_effect=requests.HTTPError("401 secret-xyz"))
    )

    with caplog.at_level("WARNING"):
        notify_complete(url="https://example.com/h", token="secret-xyz", payload=PAYLOAD)

    logged = "\n".join(rec.message for rec in caplog.records)
    assert "secret-xyz" not in logged
    assert "Ольга: привет" not in logged
    assert "HTTPError" in logged
