from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.config import load_config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-platform",
]


class AuthError(Exception):
    pass


def _credentials_path(data_dir: Path) -> Path:
    return data_dir / "credentials.json"


def _token_path(data_dir: Path) -> Path:
    return data_dir / "token.json"


def _write_token(path: Path, payload: str) -> None:
    path.write_text(payload)
    path.chmod(0o600)


def _fetch_manual_token(flow, response_url: str) -> None:
    parsed = urlparse(response_url)
    allow_loopback_http = (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    previous = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
    if allow_loopback_http:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    try:
        flow.fetch_token(authorization_response=response_url)
    finally:
        if allow_loopback_http:
            if previous is None:
                os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
            else:
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = previous


def _load_client_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AuthError(
            f"OAuth client credentials at {path} are malformed: {exc}. "
            "Download a fresh Desktop app credentials JSON from Google Cloud Console."
        ) from exc
    if not isinstance(config, dict):
        raise AuthError(
            f"OAuth client credentials at {path} are malformed: expected a JSON object."
        )
    return config


def load_credentials(data_dir: Path) -> Credentials:
    token_file = _token_path(data_dir)
    if not token_file.exists():
        raise AuthError(
            f"Token file not found at {token_file}. "
            "Run `python -m src.auth` to perform interactive OAuth."
        )

    # Inspect the token file directly: Credentials.from_authorized_user_file
    # overwrites creds.scopes with whatever scopes argument we pass, so checking
    # creds.scopes after the fact only echoes our request — it does not reveal
    # what the saved token was actually authorized for.
    try:
        token_data = json.loads(token_file.read_text())
    except (OSError, ValueError) as exc:
        raise AuthError(
            f"Token at {token_file} is malformed: {exc}. "
            "Re-run `python -m src.auth` to re-authorize."
        ) from exc

    if not isinstance(token_data, dict):
        raise AuthError(
            f"Token at {token_file} is malformed: expected a JSON object, "
            f"got {type(token_data).__name__}. "
            "Re-run `python -m src.auth` to re-authorize."
        )

    saved_scopes = token_data.get("scopes")
    if isinstance(saved_scopes, str):
        saved_scopes = saved_scopes.split(" ")
    elif saved_scopes is not None and not isinstance(saved_scopes, list):
        raise AuthError(
            f"Token at {token_file} is malformed: 'scopes' must be a list or "
            f"string, got {type(saved_scopes).__name__}. "
            "Re-run `python -m src.auth` to re-authorize."
        )
    if saved_scopes and not all(isinstance(s, str) for s in saved_scopes):
        raise AuthError(
            f"Token at {token_file} is malformed: 'scopes' must contain only "
            "strings. Re-run `python -m src.auth` to re-authorize."
        )
    granted = set(saved_scopes or [])
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise AuthError(
            f"Token at {token_file} is missing required scopes: {missing}. "
            "Re-run `python -m src.auth` to re-authorize."
        )

    try:
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    except ValueError as exc:
        raise AuthError(
            f"Token at {token_file} is malformed: {exc}. "
            "Re-run `python -m src.auth` to re-authorize."
        ) from exc

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(
                f"Token at {token_file} could not be refreshed: {exc}. "
                "Re-run `python -m src.auth` to re-authorize."
            ) from exc
        _write_token(token_file, creds.to_json())
        return creds

    raise AuthError(
        f"Token at {token_file} is invalid and has no refresh token. "
        "Re-run `python -m src.auth` to re-authorize."
    )


def build_drive_service(data_dir: Path | None = None):
    if data_dir is None:
        data_dir = load_config(validate_providers=False).data_dir
    creds = load_credentials(data_dir)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def run_interactive_flow(
    data_dir: Path,
    *,
    manual: bool = False,
    response_url: str | None = None,
) -> Credentials:
    data_dir.mkdir(parents=True, exist_ok=True)
    creds_file = _credentials_path(data_dir)
    if not creds_file.exists():
        raise AuthError(
            f"Missing {creds_file}. Download OAuth client credentials from Google Cloud "
            "Console and place them at this path."
        )

    flow = InstalledAppFlow.from_client_config(_load_client_config(creds_file), SCOPES)

    if manual or response_url:
        flow.redirect_uri = "http://localhost"
        flow.autogenerate_code_verifier = False
        flow.code_verifier = None
        if response_url:
            _fetch_manual_token(flow, response_url)
            creds = flow.credentials
        else:
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            print(f"Open this URL in a browser:\n\n{auth_url}\n")
            print(
                "Then re-run with `gdstt auth <paste-redirect-url>` or "
                "`gdstt auth --manual <paste-redirect-url>`."
            )
            raise SystemExit(0)
    else:
        creds = flow.run_local_server(port=0, open_browser=True)

    _write_token(_token_path(data_dir), creds.to_json())
    return creds


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_dir = load_config(validate_providers=False).data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(prog="python -m src.auth")
    parser.add_argument("--manual", action="store_true", help="Print the auth URL instead of opening a browser")
    parser.add_argument("response_url", nargs="?", default=None, help="Redirect URL pasted from the manual flow")
    args = parser.parse_args()
    try:
        run_interactive_flow(
            data_dir,
            manual=args.manual,
            response_url=args.response_url,
        )
    except AuthError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    logger.info("Token saved to %s", _token_path(data_dir))


if __name__ == "__main__":
    main()
