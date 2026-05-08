from __future__ import annotations

import logging
from pathlib import Path

import requests

from src.stt.base import STTError, STTProvider

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 3600


class ASRProvider(STTProvider):
    """Self-hosted whisper-asr-webservice (https://github.com/ahmetoner/whisper-asr-webservice)."""

    def __init__(self, *, url: str, language: str = "", proxy_url: str = "") -> None:
        if not url:
            raise STTError("ASR_URL is required for whisper-asr-webservice provider")
        self._url = url.rstrip("/")
        self._language = language
        self._proxy_url = proxy_url

    def transcribe_chunk(self, audio_path: Path) -> str:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio chunk not found: {audio_path}")

        endpoint = f"{self._url}/asr"
        params = {"output": "text", "task": "transcribe"}
        if self._language:
            params["language"] = self._language
        proxies = (
            {"http": self._proxy_url, "https": self._proxy_url}
            if self._proxy_url
            else None
        )

        with audio_path.open("rb") as fh:
            try:
                response = requests.post(
                    endpoint,
                    params=params,
                    files={"audio_file": (audio_path.name, fh, "audio/mpeg")},
                    proxies=proxies,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except Exception as exc:
                raise STTError(f"whisper-asr-webservice transcription failed: {exc}") from exc

        return response.text.strip()
