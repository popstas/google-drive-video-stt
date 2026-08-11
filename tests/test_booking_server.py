import http.client
import json
import socket
from datetime import datetime, timezone

import pytest

from src import booking_server
from src.call_booking import load
from src.config import Config


@pytest.fixture
def server(tmp_path):
    journal = tmp_path / "call_bookings.jsonl"
    instance = booking_server.serve(
        host="127.0.0.1",
        port=0,
        token="secret-token",
        journal_path=journal,
    )
    try:
        yield instance, journal
    finally:
        instance.shutdown()


def _post(instance, body, *, token="secret-token", raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", instance.port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = raw if raw is not None else json.dumps(body)
    conn.request("POST", "/", body=payload, headers=headers)
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.status


VALID = {
    "start_time": "2026-08-11T07:00:00.000000Z",
    "task_id": "851030",
    "manager_email": "manager@example.com",
}


def test_accepts_a_valid_booking(server):
    instance, journal = server

    assert _post(instance, VALID) == 204

    stored = load(journal, now=datetime(2026, 8, 11, 12, tzinfo=timezone.utc))
    assert len(stored) == 1
    assert stored[0].task_id == "851030"
    assert stored[0].manager_email == "manager@example.com"
    assert stored[0].start_time == datetime(2026, 8, 11, 7, tzinfo=timezone.utc)


def test_rejects_a_missing_token(server):
    instance, journal = server

    assert _post(instance, VALID, token=None) == 401
    assert not journal.exists()


def test_rejects_a_wrong_token(server):
    instance, journal = server

    assert _post(instance, VALID, token="wrong-token") == 401
    assert not journal.exists()


def test_rejects_malformed_json(server):
    instance, _ = server

    assert _post(instance, None, raw="{not json") == 400


@pytest.mark.parametrize("missing", ["start_time", "task_id", "manager_email"])
def test_rejects_a_missing_field(server, missing):
    instance, _ = server
    body = {k: v for k, v in VALID.items() if k != missing}

    assert _post(instance, body) == 400


def test_rejects_an_unparseable_start_time(server):
    instance, _ = server

    assert _post(instance, {**VALID, "start_time": "yesterday"}) == 400


def test_rejects_a_non_numeric_task_id(server):
    instance, _ = server

    assert _post(instance, {**VALID, "task_id": "not-a-number"}) == 400


def test_rejects_an_oversized_body(server):
    instance, _ = server
    body = {**VALID, "manager_email": "x" * (booking_server.MAX_BODY_BYTES + 1)}

    assert _post(instance, body) == 413


def test_health_endpoint_is_open(server):
    instance, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", instance.port, timeout=5)
    conn.request("GET", "/health")
    response = conn.getresponse()
    response.read()
    conn.close()

    assert response.status == 200


def test_is_running_tracks_the_started_server(tmp_path):
    assert booking_server.is_running() is False

    instance = booking_server.serve(
        host="127.0.0.1", port=0, token="t", journal_path=tmp_path / "j.jsonl"
    )
    try:
        assert booking_server.is_running() is True
    finally:
        instance.shutdown()

    assert booking_server.is_running() is False


def test_rejects_a_negative_content_length(server):
    # int("-1") parses fine and is truthy, so a naive length check lets this through
    # and a naive rfile.read(length) reads until EOF -- i.e. forever, since the
    # client keeps the connection open waiting for a response. Give this one a short
    # timeout so a regression fails fast instead of hanging the suite.
    instance, journal = server
    conn = http.client.HTTPConnection("127.0.0.1", instance.port, timeout=2)
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret-token",
        "Content-Length": "-1",
    }
    conn.request("POST", "/", body=json.dumps(VALID), headers=headers)
    response = conn.getresponse()
    response.read()
    conn.close()

    assert response.status == 400
    assert not journal.exists()


def test_rejects_a_non_ascii_token(server):
    # hmac.compare_digest raises TypeError on non-ASCII str operands; a wrong,
    # non-ASCII token must still come back as 401, not a 500 from an unhandled
    # exception on the unauthenticated path.
    instance, journal = server

    assert _post(instance, VALID, token="sécret") == 401
    assert not journal.exists()


def test_rejects_a_non_ascii_decimal_task_id(server):
    # isdecimal() accepts non-ASCII digits (e.g. Arabic-Indic), which int() would
    # then silently normalize -- the stored task_id would disagree with what was
    # actually sent.
    instance, _ = server

    assert _post(instance, {**VALID, "task_id": "١٢٣"}) == 400


def test_serve_raises_oserror_on_bind_failure_and_stays_not_running(tmp_path):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        assert booking_server.is_running() is False
        with pytest.raises(OSError):
            booking_server.serve(
                host="127.0.0.1", port=port, token="t", journal_path=tmp_path / "j.jsonl"
            )
        assert booking_server.is_running() is False
    finally:
        blocker.close()


def test_running_flag_clears_even_if_teardown_raises(tmp_path, monkeypatch):
    # The flag must be cleared before any teardown step that could itself raise --
    # otherwise a failure in shutdown()/server_close() leaves is_running() stuck
    # True against a receiver that is no longer actually there.
    instance = booking_server.serve(
        host="127.0.0.1", port=0, token="t", journal_path=tmp_path / "j.jsonl"
    )
    original_close = instance._httpd.server_close

    def _boom():
        original_close()
        raise RuntimeError("simulated teardown failure")

    monkeypatch.setattr(instance._httpd, "server_close", _boom)

    with pytest.raises(RuntimeError):
        instance.shutdown()

    assert booking_server.is_running() is False


def _make_config(tmp_path, **overrides):
    defaults = dict(
        folders=(),
        poll_interval=60,
        bitrate="128k",
        data_dir=tmp_path,
        proxy_url="",
        stt_provider="",
        openai_api_key="",
        deepgram_api_key="",
        stt_language="en",
        call_booking_enabled=True,
        call_booking_listen_host="127.0.0.1",
        call_booking_listen_port=0,
        call_booking_token="secret-token",
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_start_returns_none_when_disabled(tmp_path):
    config = _make_config(tmp_path, call_booking_enabled=False)

    assert booking_server.start(config) is None
    assert booking_server.is_running() is False


def test_start_returns_a_bound_server_when_enabled(tmp_path):
    config = _make_config(tmp_path, call_booking_enabled=True)

    instance = booking_server.start(config)
    try:
        assert instance is not None
        assert instance.port != 0
        assert booking_server.is_running() is True
    finally:
        instance.shutdown()
