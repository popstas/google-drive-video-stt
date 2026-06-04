from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class STTError(RuntimeError):
    pass


class STTProvider(ABC):
    @abstractmethod
    def transcribe_full(self, audio_path: Path) -> str:
        """Transcribe the entire audio file in one call.

        Deepgram (the only supported provider) transcribes the full file with
        diarization in a single request and returns the formatted transcript.
        """
        ...
