import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


SUPPORTED_STT_PROVIDERS = ("", "openai", "google", "asr")


@dataclass(frozen=True)
class Config:
    folder_ids: list[str]
    poll_interval: int
    bitrate: str
    telegram_bot_token: str
    telegram_chat_id: str
    data_dir: Path
    proxy_url: str
    stt_provider: str
    openai_api_key: str
    google_cloud_project: str
    google_application_credentials: str
    asr_url: str
    stt_language: str
    stt_chunk_seconds: int


def _parse_folder_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_config() -> Config:
    if load_dotenv is not None:
        load_dotenv(override=False)
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
    proxy_url = os.environ.get("PROXY_URL", "").strip()

    stt_provider = os.environ.get("STT_PROVIDER", "").strip().lower()
    if stt_provider not in SUPPORTED_STT_PROVIDERS:
        raise ValueError(
            f"STT_PROVIDER must be one of {SUPPORTED_STT_PROVIDERS!r}, got: {stt_provider!r}"
        )
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    google_cloud_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    google_application_credentials = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS", ""
    ).strip()
    asr_url = os.environ.get("ASR_URL", "").strip()
    stt_language = os.environ.get("STT_LANGUAGE", "").strip()

    chunk_raw = os.environ.get("STT_CHUNK_SECONDS", "600").strip() or "600"
    try:
        stt_chunk_seconds = int(chunk_raw)
    except ValueError as exc:
        raise ValueError(
            f"STT_CHUNK_SECONDS must be an integer, got: {chunk_raw!r}"
        ) from exc
    if stt_chunk_seconds <= 0:
        raise ValueError(
            f"STT_CHUNK_SECONDS must be positive, got: {stt_chunk_seconds}"
        )

    if stt_provider == "openai" and not openai_api_key:
        raise ValueError("OPENAI_API_KEY is required when STT_PROVIDER=openai")
    if stt_provider == "google":
        if not google_cloud_project:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT is required when STT_PROVIDER=google"
            )
        if not google_application_credentials:
            raise ValueError(
                "GOOGLE_APPLICATION_CREDENTIALS is required when STT_PROVIDER=google"
            )
    if stt_provider == "asr" and not asr_url:
        raise ValueError("ASR_URL is required when STT_PROVIDER=asr")

    return Config(
        folder_ids=folder_ids,
        poll_interval=poll_interval,
        bitrate=bitrate,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        data_dir=data_dir,
        proxy_url=proxy_url,
        stt_provider=stt_provider,
        openai_api_key=openai_api_key,
        google_cloud_project=google_cloud_project,
        google_application_credentials=google_application_credentials,
        asr_url=asr_url,
        stt_language=stt_language,
        stt_chunk_seconds=stt_chunk_seconds,
    )
