from __future__ import annotations

import requests

from src.stt.deepgram_usage import (
    _usd_from_request_payload,
    fetch_request_cost_usd,
)


def _response(payload):
    class FakeResponse:
        def json(self):
            return payload

        def raise_for_status(self):
            return None

    return FakeResponse()


def _http_error(status_code):
    """A response whose raise_for_status() raises like a 4xx/5xx would."""

    class FakeResponse:
        def json(self):  # pragma: no cover - never reached after raise
            return {}

        def raise_for_status(self):
            raise requests.HTTPError(f"{status_code} error")

    return FakeResponse()


def test_extracts_usd_from_request_payload():
    payload = {"response": {"details": {"usd": "0.012345"}}}

    assert _usd_from_request_payload(payload) == 0.012345


def test_fetch_request_cost_uses_direct_request_detail(mocker):
    get_mock = mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=[
            _response({"projects": [{"project_id": "project-1"}]}),
            _response(
                {
                    "request": {
                        "response": {
                            "details": {
                                "usd": 0.02,
                            }
                        }
                    }
                }
            ),
        ],
    )

    assert fetch_request_cost_usd("dg-key", "request-1") == 0.02
    assert get_mock.call_args_list[0].args[0] == "https://api.deepgram.com/v1/projects"
    assert get_mock.call_args_list[0].kwargs["headers"] == {
        "Authorization": "Token dg-key"
    }
    assert get_mock.call_args_list[1].args[0] == (
        "https://api.deepgram.com/v1/projects/project-1/requests/request-1"
    )


def test_fetch_request_cost_searches_all_projects(mocker):
    """A key scoped to several projects must not assume the first one owns it."""
    get_mock = mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=[
            _response(
                {
                    "projects": [
                        {"project_id": "project-1"},
                        {"project_id": "project-2"},
                    ]
                }
            ),
            # project-1 does not own the request -> 404-style error, skipped.
            _http_error(404),
            # project-2 owns it and returns the cost.
            _response({"request": {"response": {"details": {"usd": "0.03"}}}}),
        ],
    )

    assert fetch_request_cost_usd("dg-key", "request-1") == 0.03
    assert get_mock.call_args_list[1].args[0] == (
        "https://api.deepgram.com/v1/projects/project-1/requests/request-1"
    )
    assert get_mock.call_args_list[2].args[0] == (
        "https://api.deepgram.com/v1/projects/project-2/requests/request-1"
    )


def test_fetch_request_cost_skips_projects_without_id(mocker):
    """Malformed project entries are ignored, not crashed on."""
    mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=[
            _response(
                {
                    "projects": [
                        {"name": "no id here"},
                        {"project_id": "project-2"},
                    ]
                }
            ),
            _response({"request": {"response": {"details": {"usd": 0.05}}}}),
        ],
    )

    assert fetch_request_cost_usd("dg-key", "request-1") == 0.05


def test_fetch_request_cost_returns_none_when_no_project_owns_it(mocker):
    """If every project errors/misses, the lookup stays best-effort (None)."""
    mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=[
            _response(
                {
                    "projects": [
                        {"project_id": "project-1"},
                        {"project_id": "project-2"},
                    ]
                }
            ),
            _http_error(404),
            _http_error(500),
        ],
    )

    assert fetch_request_cost_usd("dg-key", "request-1") is None


def test_malformed_usd_value_yields_none_without_raising():
    """A non-numeric usd stays best-effort (None) instead of raising."""
    payload = {"response": {"details": {"usd": "abc"}}}

    assert _usd_from_request_payload(payload) is None


def test_fetch_request_cost_returns_none_when_project_listing_fails(mocker):
    """A failing /projects listing is swallowed and returns None."""
    mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=requests.ConnectionError("boom"),
    )

    assert fetch_request_cost_usd("dg-key", "request-1") is None


def test_fetch_request_cost_uses_proxy(mocker):
    get_mock = mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=[
            _response({"projects": []}),
        ],
    )

    assert (
        fetch_request_cost_usd(
            "dg-key",
            "request-1",
            proxy_url="http://proxy:8080",
        )
        is None
    )
    assert get_mock.call_args.kwargs["proxies"] == {
        "http": "http://proxy:8080",
        "https": "http://proxy:8080",
    }
