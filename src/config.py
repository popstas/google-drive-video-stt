import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    folder_ids: list[str]
    poll_interval: int
    bitrate: str
    telegram_bot_token: str
    telegram_chat_id: str
    data_dir: Path


def _parse_folder_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_config() -> Config:
    folder_ids = _parse_folder_ids(os.environ.get("FOLDER_IDS", ""))

    poll_raw = os.environ.get("POLL_INTERVAL", "600").strip() or "600"
    try:
        poll_interval = int(poll_raw)
    except ValueError as exc:
        raise ValueError(f"POLL_INTERVAL must be an integer, got: {poll_raw!r}") from exc
    if poll_interval <= 0:
        raise ValueError(f"POLL_INTERVAL must be positive, got: {poll_interval}")

    bitrate = os.environ.get("BITRATE", "96k").strip() or "96k"
    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    data_dir = Path(os.environ.get("DATA_DIR", "data").strip() or "data")

    return Config(
        folder_ids=folder_ids,
        poll_interval=poll_interval,
        bitrate=bitrate,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        data_dir=data_dir,
    )
