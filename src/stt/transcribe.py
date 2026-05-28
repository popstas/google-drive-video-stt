from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from src.config import Config
from src.stt import get_provider
from src.stt.base import STTError
from src.stt.chunker import chunk_mp3
from src.stt.deepgram_usage import fetch_request_cost_usd

logger = logging.getLogger(__name__)


def transcribe_file(mp3_path: Path, config: Config) -> str:
    """Transcribe the input audio path with the configured provider."""
    if not config.stt_provider:
        raise STTError("STT_PROVIDER is not configured")

    mp3_path = Path(mp3_path)
    if not mp3_path.exists():
        raise FileNotFoundError(f"Input mp3 not found: {mp3_path}")

    provider = get_provider(config)

    full_text = provider.transcribe_full(mp3_path)
    if full_text is not None:
        logger.info("Transcribed full file: %s", mp3_path.name)
        _log_deepgram_cost(provider, config, mp3_path.name)
        return full_text

    with tempfile.TemporaryDirectory(prefix="stt-chunks-") as tmp:
        chunks = chunk_mp3(mp3_path, config.stt_chunk_seconds, Path(tmp))
        logger.info("Transcribing %d chunk(s) of %s", len(chunks), mp3_path.name)
        parts: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            logger.info("Transcribing chunk %d/%d: %s", idx, len(chunks), chunk.name)
            text = provider.transcribe_chunk(chunk)
            parts.append(text)

    return "\n\n".join(p for p in parts if p)


def _log_deepgram_cost(provider: object, config: Config, audio_name: str) -> None:
    if config.stt_provider != "deepgram":
        return

    request_id = getattr(provider, "last_request_id", None)
    if not request_id:
        logger.info("Deepgram cost unavailable for %s: missing request_id", audio_name)
        return

    try:
        usd = fetch_request_cost_usd(
            config.deepgram_api_key,
            str(request_id),
            proxy_url=config.proxy_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Deepgram cost unavailable yet for %s (request_id=%s): %s",
            audio_name,
            request_id,
            exc,
        )
        return

    duration = getattr(provider, "last_duration_seconds", None)
    duration_part = ""
    if duration is not None:
        try:
            duration_part = f", duration={float(duration):.2f}s"
        except (TypeError, ValueError):
            duration_part = ""

    if usd is None:
        logger.info(
            "Deepgram cost unavailable yet for %s (request_id=%s%s)",
            audio_name,
            request_id,
            duration_part,
        )
        return

    logger.info(
        "Deepgram cost for %s: $%.6f (request_id=%s%s)",
        audio_name,
        usd,
        request_id,
        duration_part,
    )
