from __future__ import annotations

import logging
from pathlib import Path

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


def load_credentials(data_dir: Path) -> Credentials:
    token_file = _token_path(data_dir)
    if not token_file.exists():
        raise AuthError(
            f"Token file not found at {token_file}. "
            "Run `python -m src.auth` to perform interactive OAuth."
        )

    try:
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    except ValueError as exc:
        raise AuthError(
            f"Token at {token_file} is malformed: {exc}. "
            "Re-run `python -m src.auth` to re-authorize."
        ) from exc

    granted = set(creds.scopes or [])
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise AuthError(
            f"Token at {token_file} is missing required scopes: {missing}. "
            "Re-run `python -m src.auth` to re-authorize."
        )

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
        data_dir = load_config().data_dir
    creds = load_credentials(data_dir)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def run_interactive_flow(data_dir: Path, response_url: str | None = None) -> Credentials:
    import os

    data_dir.mkdir(parents=True, exist_ok=True)
    creds_file = _credentials_path(data_dir)
    if not creds_file.exists():
        raise AuthError(
            f"Missing {creds_file}. Download OAuth client credentials from Google Cloud "
            "Console and place them at this path."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)

    if response_url is None:
        response_url = os.environ.get("OAUTH_RESPONSE_URL")

    if os.environ.get("OAUTH_MANUAL") or response_url:
        flow.redirect_uri = "http://localhost"
        flow.autogenerate_code_verifier = False
        flow.code_verifier = None
        if response_url:
            flow.fetch_token(authorization_response=response_url)
            creds = flow.credentials
        else:
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            print(f"Open this URL in a browser:\n\n{auth_url}\n")
            print(
                "Then re-run with OAUTH_RESPONSE_URL=<paste-redirect-url> "
                "or pass it as the first CLI argument."
            )
            raise SystemExit(0)
    else:
        creds = flow.run_local_server(port=0, open_browser=False)

    _write_token(_token_path(data_dir), creds.to_json())
    return creds


def main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_dir = load_config().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    response_url = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        run_interactive_flow(data_dir, response_url=response_url)
    except AuthError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    logger.info("Token saved to %s", _token_path(data_dir))


if __name__ == "__main__":
    main()
