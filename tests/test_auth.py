from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from src import auth


def _write_token(path: Path, payload: dict | None = None) -> None:
    payload = payload or {
        "token": "access",
        "refresh_token": "refresh",
        "client_id": "cid",
        "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    path.write_text(json.dumps(payload))


def test_load_credentials_missing_token_raises(tmp_path):
    with pytest.raises(auth.AuthError, match="Token file not found"):
        auth.load_credentials(tmp_path)


def test_load_credentials_returns_valid_creds(tmp_path, mocker):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    fake_creds = MagicMock()
    fake_creds.valid = True
    fake_creds.expired = False
    fake_creds.refresh_token = "refresh"

    from_file = mocker.patch(
        "src.auth.Credentials.from_authorized_user_file", return_value=fake_creds
    )

    result = auth.load_credentials(tmp_path)

    assert result is fake_creds
    from_file.assert_called_once_with(str(token_file), auth.SCOPES)


def test_load_credentials_refreshes_expired(tmp_path, mocker):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "refresh"
    fake_creds.to_json.return_value = '{"refreshed": true}'

    mocker.patch("src.auth.Credentials.from_authorized_user_file", return_value=fake_creds)
    request_cls = mocker.patch("src.auth.Request")

    result = auth.load_credentials(tmp_path)

    fake_creds.refresh.assert_called_once_with(request_cls.return_value)
    assert result is fake_creds
    assert token_file.read_text() == '{"refreshed": true}'


def test_load_credentials_refresh_error_raises(tmp_path, mocker):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "refresh"
    fake_creds.refresh.side_effect = RefreshError("boom")

    mocker.patch("src.auth.Credentials.from_authorized_user_file", return_value=fake_creds)
    mocker.patch("src.auth.Request")

    with pytest.raises(auth.AuthError, match="could not be refreshed"):
        auth.load_credentials(tmp_path)


def test_load_credentials_invalid_no_refresh_raises(tmp_path, mocker):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = False
    fake_creds.refresh_token = None

    mocker.patch("src.auth.Credentials.from_authorized_user_file", return_value=fake_creds)

    with pytest.raises(auth.AuthError, match="invalid"):
        auth.load_credentials(tmp_path)


def test_build_drive_service_uses_credentials(tmp_path, mocker):
    fake_creds = MagicMock()
    mocker.patch("src.auth.load_credentials", return_value=fake_creds)
    build_mock = mocker.patch("src.auth.build", return_value="service")

    result = auth.build_drive_service(tmp_path)

    assert result == "service"
    build_mock.assert_called_once_with(
        "drive", "v3", credentials=fake_creds, cache_discovery=False
    )


def test_build_drive_service_uses_config_when_no_dir(mocker, tmp_path):
    fake_cfg = MagicMock()
    fake_cfg.data_dir = tmp_path
    mocker.patch("src.auth.load_config", return_value=fake_cfg)
    fake_creds = MagicMock()
    load_mock = mocker.patch("src.auth.load_credentials", return_value=fake_creds)
    mocker.patch("src.auth.build", return_value="service")

    auth.build_drive_service()

    load_mock.assert_called_once_with(tmp_path)


def test_run_interactive_flow_missing_credentials(tmp_path):
    with pytest.raises(auth.AuthError, match="Missing"):
        auth.run_interactive_flow(tmp_path)


def test_run_interactive_flow_writes_token(tmp_path, mocker):
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text("{}")

    fake_creds = MagicMock()
    fake_creds.to_json.return_value = '{"new": "token"}'

    flow = MagicMock()
    flow.run_local_server.return_value = fake_creds
    flow_cls = mocker.patch(
        "src.auth.InstalledAppFlow.from_client_secrets_file", return_value=flow
    )

    auth.run_interactive_flow(tmp_path)

    flow_cls.assert_called_once_with(str(creds_file), auth.SCOPES)
    flow.run_local_server.assert_called_once_with(port=0)
    token_file = tmp_path / "token.json"
    assert token_file.read_text() == '{"new": "token"}'
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_load_credentials_refresh_writes_token_with_secure_mode(tmp_path, mocker):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    fake_creds = MagicMock()
    fake_creds.valid = False
    fake_creds.expired = True
    fake_creds.refresh_token = "refresh"
    fake_creds.to_json.return_value = '{"refreshed": true}'

    mocker.patch("src.auth.Credentials.from_authorized_user_file", return_value=fake_creds)
    mocker.patch("src.auth.Request")

    auth.load_credentials(tmp_path)

    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
