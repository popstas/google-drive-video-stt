from __future__ import annotations

import logging
from pathlib import Path

from src.stt.base import STTError, STTProvider

logger = logging.getLogger(__name__)


class GoogleProvider(STTProvider):
    def __init__(self, *, project: str, language: str = "") -> None:
        if not project:
            raise STTError("GOOGLE_CLOUD_PROJECT is required for Google STT provider")
        self._project = project
        self._language = language or "auto"
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud.speech_v2 import SpeechClient
        except ImportError as exc:
            raise STTError(
                "google-cloud-speech not installed; install with `uv add google-cloud-speech`"
            ) from exc
        self._client = SpeechClient()
        return self._client

    def transcribe_chunk(self, audio_path: Path) -> str:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio chunk not found: {audio_path}")

        try:
            from google.cloud.speech_v2.types import (
                AutoDetectDecodingConfig,
                RecognitionConfig,
                RecognizeRequest,
            )
        except ImportError as exc:
            raise STTError(
                "google-cloud-speech not installed; install with `uv add google-cloud-speech`"
            ) from exc

        client = self._get_client()
        config = RecognitionConfig(
            auto_decoding_config=AutoDetectDecodingConfig(),
            language_codes=[self._language],
            model="chirp_2",
        )
        recognizer = f"projects/{self._project}/locations/global/recognizers/_"

        content = audio_path.read_bytes()
        request = RecognizeRequest(
            recognizer=recognizer,
            config=config,
            content=content,
        )

        try:
            response = client.recognize(request=request)
        except Exception as exc:
            raise STTError(f"Google Cloud STT failed: {exc}") from exc

        parts: list[str] = []
        for result in getattr(response, "results", []):
            alternatives = getattr(result, "alternatives", [])
            if alternatives:
                transcript = getattr(alternatives[0], "transcript", "")
                if transcript:
                    parts.append(transcript.strip())
        return " ".join(parts).strip()
