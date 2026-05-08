from __future__ import annotations

import logging
from pathlib import Path

from src.stt.base import STTError, STTProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(STTProvider):
    def __init__(self, *, api_key: str, language: str = "", proxy_url: str = "") -> None:
        if not api_key:
            raise STTError("OPENAI_API_KEY is required for OpenAI STT provider")
        self._api_key = api_key
        self._language = language
        self._proxy_url = proxy_url
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise STTError(
                "openai package not installed; install with `uv add openai`"
            ) from exc

        kwargs = {"api_key": self._api_key}
        if self._proxy_url:
            try:
                import httpx
            except ImportError as exc:
                raise STTError(
                    "httpx required for proxy support; install with `uv add httpx`"
                ) from exc
            kwargs["http_client"] = httpx.Client(proxy=self._proxy_url)
        self._client = OpenAI(**kwargs)
        return self._client

    def transcribe_chunk(self, audio_path: Path) -> str:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio chunk not found: {audio_path}")

        client = self._get_client()
        kwargs: dict = {
            "model": "whisper-1",
            "response_format": "text",
        }
        if self._language:
            kwargs["language"] = self._language

        with audio_path.open("rb") as fh:
            try:
                response = client.audio.transcriptions.create(file=fh, **kwargs)
            except Exception as exc:
                raise STTError(f"OpenAI transcription failed: {exc}") from exc

        if isinstance(response, str):
            return response.strip()
        text = getattr(response, "text", None)
        if text is None:
            raise STTError(f"OpenAI returned unexpected response: {response!r}")
        return str(text).strip()
