from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.stt import get_provider
from src.stt.deepgram_provider import DeepgramProvider


def test_get_provider_creates_deepgram_provider():
    cfg = Config(
        folder_ids=[],
        poll_interval=600,
        bitrate="96k",
        telegram_bot_token="",
        telegram_chat_id="",
        data_dir=Path("data"),
        proxy_url="http://proxy:8080",
        stt_provider="deepgram",
        openai_api_key="",
        deepgram_api_key="dg-key",
        google_cloud_project="",
        google_stt_gcs_bucket="",
        asr_url="",
        stt_language="ru",
        stt_chunk_seconds=600,
        deepgram_model="nova-3",
        deepgram_diarize_model="latest",
        deepgram_txt_formatter="word_speaker",
        deepgram_keyterms=("Kubernetes",),
    )

    provider = get_provider(cfg)

    assert isinstance(provider, DeepgramProvider)
    assert provider.language == "ru"
    assert provider.proxy_url == "http://proxy:8080"
    assert provider.model == "nova-3"
