import logging
from unittest.mock import MagicMock

import requests

from src import planfix
from src.planfix import send_comment

URL = "https://bot.example.com/agent/leads/tool/planfix_create_comment"


def _post_mock(monkeypatch, *, raises=None):
    response = MagicMock()
    if raises is None:
        response.raise_for_status = MagicMock()
        post = MagicMock(return_value=response)
    else:
        post = MagicMock(side_effect=raises)
    monkeypatch.setattr(planfix.requests, "post", post)
    return post


def test_posts_task_id_and_description(monkeypatch):
    post = _post_mock(monkeypatch)

    sent = send_comment(url=URL, task_id="861300", description="## keypoints\nтезис")

    assert sent is True
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == URL
    assert kwargs["json"] == {"taskId": 861300, "description": "## keypoints\nтезис"}
    assert kwargs["timeout"] == 10


def test_sends_bearer_token(monkeypatch):
    post = _post_mock(monkeypatch)

    send_comment(url=URL, token="planfix-secret", task_id="1", description="x")

    _, kwargs = post.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer planfix-secret"}


def test_blank_url_is_a_no_op(monkeypatch):
    post = _post_mock(monkeypatch)

    assert send_comment(url="   ", task_id="1", description="x") is False
    post.assert_not_called()


def test_blank_description_is_a_no_op(monkeypatch):
    post = _post_mock(monkeypatch)

    assert send_comment(url=URL, task_id="1", description="  ") is False
    post.assert_not_called()


def test_non_numeric_task_id_is_refused(monkeypatch):
    post = _post_mock(monkeypatch)

    assert send_comment(url=URL, task_id="not-a-number", description="x") is False
    post.assert_not_called()


def test_failure_returns_false_and_leaks_nothing(monkeypatch, caplog):
    _post_mock(monkeypatch, raises=requests.ConnectionError("connect to secret-host"))

    with caplog.at_level(logging.WARNING):
        sent = send_comment(
            url=URL,
            token="planfix-secret",
            task_id="861300",
            description="конфиденциальные тезисы встречи",
        )

    assert sent is False
    logged = caplog.text
    assert "ConnectionError" in logged
    assert "planfix-secret" not in logged
    assert "конфиденциальные" not in logged


def test_proxy_is_used_when_set(monkeypatch):
    post = _post_mock(monkeypatch)

    send_comment(url=URL, proxy_url="http://proxy:3128", task_id="1", description="x")

    _, kwargs = post.call_args
    assert kwargs["proxies"] == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
