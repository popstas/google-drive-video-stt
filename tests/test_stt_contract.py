from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.stt import UnknownProviderError, get_provider
from src.stt.base import STTProvider
from src.stt.deepgram_provider import DeepgramProvider


def _cfg(provider: str) -> Config:
    return Config(
        folder_ids=[],
        poll_interval=600,
        bitrate="96k",
        data_dir=Path("data"),
        proxy_url="http://proxy:8080",
        stt_provider=provider,
        openai_api_key="",
        deepgram_api_key="dg-test" if provider == "deepgram" else "",
        stt_language="ru" if provider == "deepgram" else "",
        deepgram_model="nova-3",
        deepgram_diarize_model="latest",
        deepgram_audio_source="m4a_copy",
        deepgram_txt_formatter="word_speaker",
        deepgram_keyterms=("Kubernetes",),
    )


def test_get_provider_returns_deepgram_provider():
    provider = get_provider(_cfg("deepgram"))

    assert isinstance(provider, DeepgramProvider)
    assert isinstance(provider, STTProvider)
    assert callable(provider.transcribe_full)


def test_get_provider_unknown_name_raises():
    with pytest.raises(UnknownProviderError, match="Unknown STT provider"):
        get_provider(_cfg("bogus"))
