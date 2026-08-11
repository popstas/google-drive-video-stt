import http.client
import json
from datetime import datetime, timezone

import pytest

from src import booking_server
from src.call_booking import load


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
