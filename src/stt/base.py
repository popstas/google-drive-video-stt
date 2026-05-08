from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class STTError(RuntimeError):
    pass


class STTProvider(ABC):
    @abstractmethod
    def transcribe_chunk(self, audio_path: Path) -> str:
        ...

    def transcribe_full(self, audio_path: Path) -> str | None:
        """Transcribe the entire audio file in one call.

        Providers that support full-file transcription (e.g. batched APIs with
        diarization) override this and return the transcript string. Returning
        None means the caller should fall back to chunked transcription.
        """
        return None
