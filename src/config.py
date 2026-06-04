import json
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


SUPPORTED_STT_PROVIDERS = ("", "deepgram")
OUTPUT_TARGETS = ("drive", "folder")
DEEPGRAM_DIARIZE_MODELS = ("latest", "v1")
DEEPGRAM_AUDIO_SOURCES = ("m4a_copy", "mp3_96k", "mp3_192k")
DEEPGRAM_TXT_FORMATTERS = ("word_speaker", "utterance")
DEEPGRAM_DEFAULT_KEYTERMS_FILE = Path("config/deepgram-keyterms.txt")
DEEPGRAM_MAX_KEYTERMS = 100
CHECKOUT_ROOT = Path(__file__).resolve().parent.parent


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
    deepgram_api_key: str
    stt_language: str
    stt_postprocess: bool = True
    drive_mp3_artifact: bool = True
    output_target: str = "drive"
    output_dir: Path | None = None
    openai_keypoints: bool = False
    openai_model: str = "gpt-5.4-mini"
    openai_batch: bool = False
    deepgram_model: str = "nova-3"
    deepgram_diarize_model: str = "latest"
    deepgram_audio_source: str = "m4a_copy"
    deepgram_txt_formatter: str = "word_speaker"
    deepgram_keyterms_enabled: bool = True
    deepgram_keyterms_file: Path = DEEPGRAM_DEFAULT_KEYTERMS_FILE
    deepgram_keyterms: tuple[str, ...] = ()


def _parse_folder_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _dotenv_path() -> Path:
    cwd_path = Path(".env")
    if cwd_path.exists():
        return cwd_path
    checkout_path = CHECKOUT_ROOT / ".env"
    return checkout_path if checkout_path.exists() else cwd_path


def _resolve_relative_to_dotenv(raw: str, dotenv_path: Path) -> Path:
    path = Path(raw)
    if path.is_absolute() or dotenv_path == Path(".env"):
        return path
    return dotenv_path.parent / path


def resolve_config_path(raw: str) -> Path:
    return _resolve_relative_to_dotenv(raw, _dotenv_path())


def _load_deepgram_api_key(api_key: str, api_key_file: str) -> str:
    api_key = api_key.strip()
    if api_key:
        return api_key

    api_key_file = api_key_file.strip()
    if not api_key_file:
        return ""

    path = Path(api_key_file)
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ValueError(f"DEEPGRAM_API_KEY_FILE could not be read: {path}") from exc
    if not raw:
        return ""

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, dict):
        for key in ("api_key", "deepgram_api_key", "DEEPGRAM_API_KEY"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return ""


def _parse_bool(raw: str, *, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected boolean value, got: {raw!r}")


def _load_deepgram_keyterms(enabled: bool, keyterms_file: Path) -> tuple[str, ...]:
    if not enabled:
        return ()

    try:
        raw_lines = keyterms_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"DEEPGRAM_KEYTERMS_FILE could not be read: {keyterms_file}") from exc

    keyterms = tuple(
        line.strip()
        for line in raw_lines
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(keyterms) > DEEPGRAM_MAX_KEYTERMS:
        raise ValueError(
            f"DEEPGRAM_KEYTERMS_FILE may contain at most {DEEPGRAM_MAX_KEYTERMS} "
            f"keyterms, got: {len(keyterms)}"
        )
    return keyterms


def load_config(*, validate_providers: bool = True) -> Config:
    dotenv_path = _dotenv_path()
    if load_dotenv is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False, encoding="utf-8-sig")
    folder_ids_raw = os.environ.get("FOLDER_IDS", "")
    folder_ids = _parse_folder_ids(folder_ids_raw)
    if folder_ids_raw and not folder_ids:
        raise ValueError(
            "FOLDER_IDS was set but does not contain any non-empty folder ids"
        )

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
    data_dir = _resolve_relative_to_dotenv(
        os.environ.get("DATA_DIR", "data").strip() or "data",
        dotenv_path,
    )
    proxy_url = os.environ.get("PROXY_URL", "").strip()

    stt_provider_raw = os.environ.get("STT_PROVIDER")
    if stt_provider_raw is None:
        stt_provider = "deepgram"
    else:
        stt_provider = stt_provider_raw.strip().lower()
    if stt_provider == "disabled":
        stt_provider = ""
    if stt_provider not in SUPPORTED_STT_PROVIDERS:
        raise ValueError(
            f"STT_PROVIDER must be one of {SUPPORTED_STT_PROVIDERS!r}, got: {stt_provider!r}"
        )
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    deepgram_api_key = ""
    deepgram_model = "nova-3"
    deepgram_diarize_model = "latest"
    deepgram_audio_source = "m4a_copy"
    deepgram_txt_formatter = "word_speaker"
    deepgram_keyterms_enabled = True
    deepgram_keyterms_file = DEEPGRAM_DEFAULT_KEYTERMS_FILE
    deepgram_keyterms: tuple[str, ...] = ()
    # Deepgram parsing reads files (DEEPGRAM_API_KEY_FILE) and raises on bad
    # values (DEEPGRAM_KEYTERMS_ENABLED), so it is gated like the validation
    # below: Drive-only commands (`gdstt auth`, `gdstt list`) must not be
    # blocked by unrelated Deepgram config. The parsed values are only consumed
    # when validation runs (transcription always uses validate_providers=True).
    if validate_providers and stt_provider == "deepgram":
        deepgram_api_key_file = os.environ.get("DEEPGRAM_API_KEY_FILE", "").strip()
        deepgram_api_key = _load_deepgram_api_key(
            os.environ.get("DEEPGRAM_API_KEY", ""),
            (
                str(_resolve_relative_to_dotenv(deepgram_api_key_file, dotenv_path))
                if deepgram_api_key_file
                else ""
            ),
        )
        deepgram_model = os.environ.get("DEEPGRAM_MODEL", "nova-3").strip() or "nova-3"
        deepgram_diarize_model = (
            os.environ.get("DEEPGRAM_DIARIZE_MODEL", "latest").strip().lower()
            or "latest"
        )
        deepgram_audio_source = (
            os.environ.get("DEEPGRAM_AUDIO_SOURCE", "m4a_copy").strip().lower()
            or "m4a_copy"
        )
        deepgram_txt_formatter = (
            os.environ.get("DEEPGRAM_TXT_FORMATTER", "word_speaker").strip().lower()
            or "word_speaker"
        )
        deepgram_keyterms_enabled = _parse_bool(
            os.environ.get("DEEPGRAM_KEYTERMS_ENABLED", ""),
            default=True,
        )
        deepgram_keyterms_file = _resolve_relative_to_dotenv(
            os.environ.get("DEEPGRAM_KEYTERMS_FILE", str(DEEPGRAM_DEFAULT_KEYTERMS_FILE))
            .strip()
            or str(DEEPGRAM_DEFAULT_KEYTERMS_FILE),
            dotenv_path,
        )
    stt_language = os.environ.get("STT_LANGUAGE", "").strip()
    if stt_provider == "deepgram" and not stt_language:
        stt_language = "ru"

    stt_postprocess = _parse_bool(
        os.environ.get("STT_POSTPROCESS", ""), default=True
    )
    drive_mp3_artifact_raw = os.environ.get("DRIVE_MP3_ARTIFACT", "")
    drive_mp3_artifact_default = not (
        stt_provider == "deepgram" and deepgram_audio_source == "m4a_copy"
    )
    drive_mp3_artifact = _parse_bool(
        drive_mp3_artifact_raw, default=drive_mp3_artifact_default
    )

    openai_model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini"
    openai_keypoints = _parse_bool(
        os.environ.get("OPENAI_KEYPOINTS", ""), default=False
    )
    openai_batch = _parse_bool(os.environ.get("OPENAI_BATCH", ""), default=False)

    output_target = (
        os.environ.get("OUTPUT_TARGET", "drive").strip().lower() or "drive"
    )
    if output_target not in OUTPUT_TARGETS:
        raise ValueError(
            f"OUTPUT_TARGET must be one of {OUTPUT_TARGETS!r}, got: {output_target!r}"
        )
    output_dir_raw = os.environ.get("OUTPUT_DIR", "").strip()
    output_dir = (
        _resolve_relative_to_dotenv(output_dir_raw, dotenv_path)
        if output_dir_raw
        else None
    )
    if output_target == "folder" and output_dir is None:
        raise ValueError("OUTPUT_DIR is required when OUTPUT_TARGET=folder")

    # Provider-secret validation is skipped for commands that only need Drive/
    # data-dir settings (e.g. `gdstt auth`, `gdstt list`), so an unconfigured STT
    # provider can't block bootstrap/inspection commands.
    if validate_providers:
        if openai_keypoints and not openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when OPENAI_KEYPOINTS is enabled")
        if stt_provider == "deepgram":
            if not deepgram_api_key:
                raise ValueError(
                    "DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram. "
                    "Add DEEPGRAM_API_KEY to your .env."
                )
            if deepgram_diarize_model not in DEEPGRAM_DIARIZE_MODELS:
                raise ValueError(
                    f"DEEPGRAM_DIARIZE_MODEL must be one of {DEEPGRAM_DIARIZE_MODELS!r}, "
                    f"got: {deepgram_diarize_model!r}"
                )
            if deepgram_audio_source not in DEEPGRAM_AUDIO_SOURCES:
                raise ValueError(
                    f"DEEPGRAM_AUDIO_SOURCE must be one of {DEEPGRAM_AUDIO_SOURCES!r}, "
                    f"got: {deepgram_audio_source!r}"
                )
            if deepgram_txt_formatter not in DEEPGRAM_TXT_FORMATTERS:
                raise ValueError(
                    f"DEEPGRAM_TXT_FORMATTER must be one of {DEEPGRAM_TXT_FORMATTERS!r}, "
                    f"got: {deepgram_txt_formatter!r}"
                )
            deepgram_keyterms = _load_deepgram_keyterms(
                deepgram_keyterms_enabled,
                deepgram_keyterms_file,
            )

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
        deepgram_api_key=deepgram_api_key,
        stt_language=stt_language,
        stt_postprocess=stt_postprocess,
        drive_mp3_artifact=drive_mp3_artifact,
        output_target=output_target,
        output_dir=output_dir,
        openai_keypoints=openai_keypoints,
        openai_model=openai_model,
        openai_batch=openai_batch,
        deepgram_model=deepgram_model,
        deepgram_diarize_model=deepgram_diarize_model,
        deepgram_audio_source=deepgram_audio_source,
        deepgram_txt_formatter=deepgram_txt_formatter,
        deepgram_keyterms_enabled=deepgram_keyterms_enabled,
        deepgram_keyterms_file=deepgram_keyterms_file,
        deepgram_keyterms=deepgram_keyterms,
    )
