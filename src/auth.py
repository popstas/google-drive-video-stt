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

SCOPES = ["https://www.googleapis.com/auth/drive"]


class AuthError(Exception):
    pass


def _credentials_path(data_dir: Path) -> Path:
    return data_dir / "credentials.json"


def _token_path(data_dir: Path) -> Path:
    return data_dir / "token.json"


def load_credentials(data_dir: Path) -> Credentials:
    token_file = _token_path(data_dir)
    if not token_file.exists():
        raise AuthError(
            f"Token file not found at {token_file}. "
            "Run `python -m src.auth` to perform interactive OAuth."
        )

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

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
        token_file.write_text(creds.to_json())
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


def run_interactive_flow(data_dir: Path) -> Credentials:
    creds_file = _credentials_path(data_dir)
    if not creds_file.exists():
        raise AuthError(
            f"Missing {creds_file}. Download OAuth client credentials from Google Cloud "
            "Console and place them at this path."
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0)
    _token_path(data_dir).write_text(creds.to_json())
    return creds


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_dir = load_config().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_interactive_flow(data_dir)
    except AuthError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    logger.info("Token saved to %s", _token_path(data_dir))


if __name__ == "__main__":
    main()
