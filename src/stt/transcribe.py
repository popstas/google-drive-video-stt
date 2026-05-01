from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from src.config import Config
from src.stt import get_provider
from src.stt.base import STTError
from src.stt.chunker import chunk_mp3

logger = logging.getLogger(__name__)


def transcribe_file(mp3_path: Path, config: Config) -> str:
    """Chunk mp3, transcribe each part with the configured provider, return merged text."""
    if not config.stt_provider:
        raise STTError("STT_PROVIDER is not configured")

    mp3_path = Path(mp3_path)
    if not mp3_path.exists():
        raise FileNotFoundError(f"Input mp3 not found: {mp3_path}")

    provider = get_provider(config)

    with tempfile.TemporaryDirectory(prefix="stt-chunks-") as tmp:
        chunks = chunk_mp3(mp3_path, config.stt_chunk_seconds, Path(tmp))
        logger.info("Transcribing %d chunk(s) of %s", len(chunks), mp3_path.name)
        parts: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            logger.info("Transcribing chunk %d/%d: %s", idx, len(chunks), chunk.name)
            text = provider.transcribe_chunk(chunk)
            parts.append(text)

    return "\n\n".join(p for p in parts if p)
