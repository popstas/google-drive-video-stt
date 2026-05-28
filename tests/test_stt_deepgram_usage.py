from __future__ import annotations

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


def test_fetch_request_cost_falls_back_to_recent_requests(mocker):
    mocker.patch(
        "src.stt.deepgram_usage.requests.get",
        side_effect=[
            _response({"projects": [{"project_id": "project-1"}]}),
            _response({"request": {}}),
            _response(
                {
                    "requests": [
                        {"request_id": "other", "response": {"details": {"usd": 1}}},
                        {
                            "request_id": "request-1",
                            "response": {"details": {"usd": "0.03"}},
                        },
                    ]
                }
            ),
        ],
    )

    assert fetch_request_cost_usd("dg-key", "request-1") == 0.03


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
