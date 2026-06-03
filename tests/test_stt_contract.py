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
        telegram_bot_token="",
        telegram_chat_id="",
        data_dir=Path("data"),
        proxy_url="http://proxy:8080",
        stt_provider=provider,
        openai_api_key="",
        deepgram_api_key="dg-test" if provider == "deepgram" else "",
        google_cloud_project="",
        google_stt_gcs_bucket="",
        asr_url="",
        stt_language="ru" if provider == "deepgram" else "",
        stt_chunk_seconds=600,
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
    assert callable(provider.transcribe_chunk)


def test_get_provider_unknown_name_raises():
    with pytest.raises(UnknownProviderError, match="Unknown STT provider"):
        get_provider(_cfg("bogus"))


def test_transcribe_chunk_defaults_to_transcribe_full(tmp_path):
    class DummyProvider(STTProvider):
        def transcribe_full(self, audio_path: Path) -> str:
            return f"full:{audio_path.name}"

    provider = DummyProvider()
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"x")

    assert provider.transcribe_chunk(audio_path) == "full:audio.mp3"
