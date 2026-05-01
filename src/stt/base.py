from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class STTError(RuntimeError):
    pass


class STTProvider(ABC):
    @abstractmethod
    def transcribe_chunk(self, audio_path: Path) -> str:
        ...
