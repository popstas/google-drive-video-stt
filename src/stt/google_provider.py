from __future__ import annotations

import logging
import uuid
from pathlib import Path

from src.stt.base import STTError, STTProvider

logger = logging.getLogger(__name__)


class GoogleProvider(STTProvider):
    def __init__(
        self,
        *,
        project: str,
        bucket: str,
        data_dir: Path,
        language: str = "",
    ) -> None:
        if not project:
            raise STTError("GOOGLE_CLOUD_PROJECT is required for Google STT provider")
        if not bucket:
            raise STTError("GOOGLE_STT_GCS_BUCKET is required for Google STT provider")
        self._project = project
        self._bucket = bucket
        self._data_dir = Path(data_dir)
        self._language = language or "auto"
        self._credentials = None
        self._speech_client = None
        self._storage_client = None

    def _get_credentials(self):
        if self._credentials is not None:
            return self._credentials
        try:
            from src.auth import AuthError, load_credentials
        except ImportError as exc:
            raise STTError(f"Failed to import auth module: {exc}") from exc
        try:
            self._credentials = load_credentials(self._data_dir)
        except AuthError as exc:
            raise STTError(
                f"Google OAuth credentials unavailable: {exc} "
                "Re-run `python -m src.auth` to re-authorize."
            ) from exc
        return self._credentials

    def _get_speech_client(self):
        if self._speech_client is not None:
            return self._speech_client
        try:
            from google.cloud.speech_v2 import SpeechClient
        except ImportError as exc:
            raise STTError(
                "google-cloud-speech not installed; install with `uv add google-cloud-speech`"
            ) from exc
        creds = self._get_credentials()
        self._speech_client = SpeechClient(credentials=creds)
        return self._speech_client

    def _get_storage_client(self):
        if self._storage_client is not None:
            return self._storage_client
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise STTError(
                "google-cloud-storage not installed; install with `uv add google-cloud-storage`"
            ) from exc
        creds = self._get_credentials()
        self._storage_client = storage.Client(project=self._project, credentials=creds)
        return self._storage_client

    def transcribe_chunk(self, audio_path: Path) -> str:
        return self.transcribe_full(audio_path) or ""

    def transcribe_full(self, audio_path: Path) -> str:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            from google.cloud.speech_v2.types import (
                BatchRecognizeFileMetadata,
                BatchRecognizeRequest,
                InlineOutputConfig,
                RecognitionConfig,
                RecognitionFeatures,
                RecognitionOutputConfig,
                SpeakerDiarizationConfig,
                AutoDetectDecodingConfig,
            )
        except ImportError as exc:
            raise STTError(
                "google-cloud-speech not installed; install with `uv add google-cloud-speech`"
            ) from exc

        storage_client = self._get_storage_client()
        bucket = storage_client.bucket(self._bucket)
        blob_name = f"stt-{uuid.uuid4().hex}-{audio_path.name}"
        blob = bucket.blob(blob_name)
        gcs_uri = f"gs://{self._bucket}/{blob_name}"

        try:
            logger.info("Uploading %s to %s", audio_path.name, gcs_uri)
            blob.upload_from_filename(str(audio_path))

            speech_client = self._get_speech_client()
            config = RecognitionConfig(
                auto_decoding_config=AutoDetectDecodingConfig(),
                language_codes=[self._language],
                model="long",
                features=RecognitionFeatures(
                    enable_word_time_offsets=True,
                    enable_word_confidence=False,
                    diarization_config=SpeakerDiarizationConfig(
                        min_speaker_count=2,
                        max_speaker_count=6,
                    ),
                ),
            )
            recognizer = f"projects/{self._project}/locations/global/recognizers/_"
            request = BatchRecognizeRequest(
                recognizer=recognizer,
                config=config,
                files=[BatchRecognizeFileMetadata(uri=gcs_uri)],
                recognition_output_config=RecognitionOutputConfig(
                    inline_response_config=InlineOutputConfig(),
                ),
            )

            try:
                operation = speech_client.batch_recognize(request=request)
                response = operation.result()
            except Exception as exc:
                raise STTError(f"Google Cloud STT batch_recognize failed: {exc}") from exc

            return self._format_diarized(response, gcs_uri)
        finally:
            try:
                blob.delete()
                logger.info("Deleted %s", gcs_uri)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to delete %s: %s", gcs_uri, exc)

    def _format_diarized(self, response, gcs_uri: str) -> str:
        results_map = getattr(response, "results", {}) or {}
        file_result = None
        if hasattr(results_map, "get"):
            file_result = results_map.get(gcs_uri)
        if file_result is None and isinstance(results_map, dict):
            for value in results_map.values():
                file_result = value
                break

        if file_result is None:
            return ""

        transcript = getattr(file_result, "transcript", None)
        if transcript is None:
            return ""

        words: list = []
        for result in getattr(transcript, "results", []) or []:
            alternatives = getattr(result, "alternatives", []) or []
            if not alternatives:
                continue
            for word in getattr(alternatives[0], "words", []) or []:
                words.append(word)

        if not words:
            return ""

        lines: list[str] = []
        current_speaker: int | None = None
        current_words: list[str] = []
        current_start = None

        def _emit() -> None:
            if current_speaker is None or not current_words:
                return
            ts = _format_offset(current_start)
            lines.append(f"[{ts}] Speaker {current_speaker}: {' '.join(current_words)}")

        for w in words:
            speaker = getattr(w, "speaker_label", None)
            try:
                speaker_int = int(speaker) if speaker is not None and speaker != "" else 0
            except (TypeError, ValueError):
                speaker_int = 0
            text = getattr(w, "word", "") or ""
            start = getattr(w, "start_offset", None)

            if speaker_int != current_speaker:
                _emit()
                current_speaker = speaker_int
                current_words = [text] if text else []
                current_start = start
            else:
                if text:
                    current_words.append(text)

        _emit()
        return "\n".join(lines)


def _format_offset(offset) -> str:
    total = 0
    if offset is None:
        total = 0
    elif hasattr(offset, "total_seconds"):
        total = int(offset.total_seconds())
    elif hasattr(offset, "seconds"):
        total = int(getattr(offset, "seconds", 0) or 0)
    else:
        try:
            total = int(offset)
        except (TypeError, ValueError):
            total = 0
    hh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"
