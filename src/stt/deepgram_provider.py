from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from src.stt.base import STTError, STTProvider

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
REQUEST_TIMEOUT_SECONDS = 3600


class DeepgramProvider(STTProvider):
    def __init__(
        self,
        *,
        api_key: str,
        language: str,
        model: str = "nova-3",
        diarize_model: str = "latest",
        txt_formatter: str = "word_speaker",
        keyterms: tuple[str, ...] = (),
        proxy_url: str = "",
    ) -> None:
        if not api_key:
            raise STTError("DEEPGRAM_API_KEY is required for Deepgram STT provider")
        if not language:
            raise STTError("STT_LANGUAGE is required for Deepgram STT provider")
        self._api_key = api_key
        self._language = language
        self._model = model
        self._diarize_model = diarize_model
        self._txt_formatter = txt_formatter
        self._keyterms = keyterms
        self._proxy_url = proxy_url
        self._last_request_id: str | None = None
        self._last_duration_seconds: float | None = None

    @property
    def language(self) -> str:
        return self._language

    @property
    def proxy_url(self) -> str:
        return self._proxy_url

    @property
    def model(self) -> str:
        return self._model

    @property
    def last_request_id(self) -> str | None:
        return self._last_request_id

    @property
    def last_duration_seconds(self) -> float | None:
        return self._last_duration_seconds

    def transcribe_chunk(self, audio_path: Path) -> str:
        return self.transcribe_full(audio_path)

    def transcribe_full(self, audio_path: Path) -> str:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": _content_type_for(audio_path),
        }
        params = {
            "model": self._model,
            "language": self._language,
            "diarize_model": self._diarize_model,
            "utterances": "true",
            "punctuate": "true",
            "smart_format": "true",
        }
        if self._model == "nova-3" and self._keyterms:
            params["keyterm"] = self._keyterms
        proxies = (
            {"http": self._proxy_url, "https": self._proxy_url}
            if self._proxy_url
            else None
        )

        with audio_path.open("rb") as fh:
            try:
                response = requests.post(
                    DEEPGRAM_LISTEN_URL,
                    params=params,
                    headers=headers,
                    data=fh,
                    proxies=proxies,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise STTError(f"Deepgram transcription failed: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise STTError(
                f"Deepgram transcription failed: {_format_error_response(response)}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise STTError("Deepgram returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise STTError(f"Deepgram returned unexpected response: {payload!r}")

        metadata = payload.get("metadata", {})
        if isinstance(metadata, dict):
            request_id = metadata.get("request_id")
            self._last_request_id = str(request_id) if request_id else None
            duration = metadata.get("duration")
            try:
                self._last_duration_seconds = (
                    float(duration) if duration is not None else None
                )
            except (TypeError, ValueError):
                self._last_duration_seconds = None

        return _format_diarized(payload, formatter=self._txt_formatter)


def _format_error_response(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(getattr(response, "text", "") or response)
    if not isinstance(payload, dict):
        return str(payload)
    message = (
        payload.get("err_msg")
        or payload.get("message")
        or payload.get("details")
        or getattr(response, "text", "")
    )
    request_id = payload.get("request_id")
    if request_id:
        return f"{message} (request_id={request_id})"
    return str(message)


def _content_type_for(audio_path: Path) -> str:
    if audio_path.suffix.lower() in {".m4a", ".mp4"}:
        return "audio/mp4"
    return "audio/mpeg"


def _format_diarized(payload: dict[str, Any], *, formatter: str = "word_speaker") -> str:
    results = payload.get("results", {})
    if not isinstance(results, dict):
        raise STTError("Deepgram returned no results object")

    utterances = results.get("utterances")
    if isinstance(utterances, list) and utterances:
        if formatter == "utterance":
            return _format_utterances(utterances)
        return _format_word_speaker_utterances(utterances)

    return _format_words(results)


def _format_utterances(utterances: list[Any]) -> str:
    lines: list[str] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        speaker = _speaker_number(utterance.get("speaker"), "utterance")
        transcript = str(utterance.get("transcript", "") or "").strip()
        if not transcript:
            transcript = _join_words(utterance.get("words", []))
        if not transcript:
            continue
        start = utterance.get("start", 0)
        lines.append(f"[{_format_offset(start)}] Speaker {speaker}: {transcript}")
    return "\n".join(lines)


def _format_word_speaker_utterances(utterances: list[Any]) -> str:
    lines: list[str] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        words = utterance.get("words", [])
        if not isinstance(words, list) or not words:
            speaker = _speaker_number(utterance.get("speaker"), "utterance")
            transcript = str(utterance.get("transcript", "") or "").strip()
            if transcript:
                lines.append(
                    f"[{_format_offset(utterance.get('start', 0))}] "
                    f"Speaker {speaker}: {transcript}"
                )
            continue

        current_speaker: int | None = None
        current_start: Any = utterance.get("start", 0)
        current_words: list[str] = []

        def emit() -> None:
            if current_speaker is None or not current_words:
                return
            lines.append(
                f"[{_format_offset(current_start)}] "
                f"Speaker {current_speaker}: {' '.join(current_words)}"
            )

        for word in words:
            if not isinstance(word, dict):
                continue
            speaker = _speaker_number(word.get("speaker"), "word")
            text = str(word.get("punctuated_word") or word.get("word") or "").strip()
            if speaker != current_speaker:
                emit()
                current_speaker = speaker
                current_start = word.get("start", utterance.get("start", 0))
                current_words = [text] if text else []
            elif text:
                current_words.append(text)
        emit()
    return "\n".join(lines)


def _format_words(results: dict[str, Any]) -> str:
    words = _extract_words(results)
    if not words:
        transcript = _extract_transcript(results)
        if transcript:
            raise STTError("Deepgram returned transcript without speaker labels")
        return ""

    lines: list[str] = []
    current_speaker: int | None = None
    current_start: Any = 0
    current_words: list[str] = []

    def emit() -> None:
        if current_speaker is None or not current_words:
            return
        lines.append(
            f"[{_format_offset(current_start)}] "
            f"Speaker {current_speaker}: {' '.join(current_words)}"
        )

    for word in words:
        if not isinstance(word, dict):
            continue
        speaker = _speaker_number(word.get("speaker"), "word")
        text = str(word.get("punctuated_word") or word.get("word") or "").strip()
        if speaker != current_speaker:
            emit()
            current_speaker = speaker
            current_start = word.get("start", 0)
            current_words = [text] if text else []
        elif text:
            current_words.append(text)

    emit()
    return "\n".join(lines)


def _extract_words(results: dict[str, Any]) -> list[Any]:
    channels = results.get("channels", [])
    if not isinstance(channels, list) or not channels:
        return []
    first_channel = channels[0]
    if not isinstance(first_channel, dict):
        return []
    alternatives = first_channel.get("alternatives", [])
    if not isinstance(alternatives, list) or not alternatives:
        return []
    first_alternative = alternatives[0]
    if not isinstance(first_alternative, dict):
        return []
    words = first_alternative.get("words", [])
    return words if isinstance(words, list) else []


def _extract_transcript(results: dict[str, Any]) -> str:
    channels = results.get("channels", [])
    if not isinstance(channels, list) or not channels:
        return ""
    first_channel = channels[0]
    if not isinstance(first_channel, dict):
        return ""
    alternatives = first_channel.get("alternatives", [])
    if not isinstance(alternatives, list) or not alternatives:
        return ""
    first_alternative = alternatives[0]
    if not isinstance(first_alternative, dict):
        return ""
    return str(first_alternative.get("transcript", "") or "").strip()


def _join_words(words: Any) -> str:
    if not isinstance(words, list):
        return ""
    parts = []
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("punctuated_word") or word.get("word") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _speaker_number(raw: Any, source: str) -> int:
    if raw is None:
        raise STTError(f"Deepgram returned {source} without speaker label")
    try:
        return int(raw) + 1
    except (TypeError, ValueError) as exc:
        raise STTError(f"Deepgram returned invalid {source} speaker label: {raw!r}") from exc


def _format_offset(seconds: Any) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        total = 0
    hh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"
