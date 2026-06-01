from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.stt import UnknownProviderError, get_provider
from src.stt.asr_provider import ASRProvider
from src.stt.base import STTProvider
from src.stt.deepgram_provider import DeepgramProvider
from src.stt.google_provider import GoogleProvider
from src.stt.openai_provider import OpenAIProvider


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
        openai_api_key="sk-test" if provider == "openai" else "",
        deepgram_api_key="dg-test" if provider == "deepgram" else "",
        google_cloud_project="proj-1" if provider == "google" else "",
        google_stt_gcs_bucket="bucket-1" if provider == "google" else "",
        asr_url="http://localhost:9000" if provider == "asr" else "",
        stt_language="ru" if provider in {"deepgram", "google"} else "",
        stt_chunk_seconds=600,
        deepgram_model="nova-3",
        deepgram_diarize_model="latest",
        deepgram_audio_source="m4a_copy",
        deepgram_txt_formatter="word_speaker",
        deepgram_keyterms=("Kubernetes",),
    )


@pytest.mark.parametrize(
    ("provider_name", "expected_class"),
    [
        ("openai", OpenAIProvider),
        ("google", GoogleProvider),
        ("deepgram", DeepgramProvider),
        ("asr", ASRProvider),
    ],
)
def test_get_provider_returns_expected_provider_class(provider_name, expected_class):
    provider = get_provider(_cfg(provider_name))

    assert isinstance(provider, expected_class)
    assert isinstance(provider, STTProvider)
    assert callable(provider.transcribe_chunk)
    assert callable(provider.transcribe_full)


def test_get_provider_unknown_name_raises():
    cfg = _cfg("openai")
    cfg = Config(
        folder_ids=cfg.folder_ids,
        poll_interval=cfg.poll_interval,
        bitrate=cfg.bitrate,
        telegram_bot_token=cfg.telegram_bot_token,
        telegram_chat_id=cfg.telegram_chat_id,
        data_dir=cfg.data_dir,
        proxy_url=cfg.proxy_url,
        stt_provider="bogus",
        openai_api_key=cfg.openai_api_key,
        deepgram_api_key=cfg.deepgram_api_key,
        google_cloud_project=cfg.google_cloud_project,
        google_stt_gcs_bucket=cfg.google_stt_gcs_bucket,
        asr_url=cfg.asr_url,
        stt_language=cfg.stt_language,
        stt_chunk_seconds=cfg.stt_chunk_seconds,
        deepgram_model=cfg.deepgram_model,
        deepgram_diarize_model=cfg.deepgram_diarize_model,
        deepgram_audio_source=cfg.deepgram_audio_source,
        deepgram_txt_formatter=cfg.deepgram_txt_formatter,
        deepgram_keyterms=cfg.deepgram_keyterms,
    )

    with pytest.raises(UnknownProviderError, match="Unknown STT provider"):
        get_provider(cfg)


def test_base_provider_transcribe_full_defaults_to_none(tmp_path):
    class DummyProvider(STTProvider):
        def transcribe_chunk(self, audio_path: Path) -> str:
            return "ok"

    provider = DummyProvider()
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"x")

    assert provider.transcribe_full(audio_path) is None