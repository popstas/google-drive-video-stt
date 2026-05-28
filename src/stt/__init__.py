from __future__ import annotations

from src.config import Config
from src.stt.base import STTProvider


class UnknownProviderError(ValueError):
    pass


def get_provider(config: Config) -> STTProvider:
    name = config.stt_provider
    if name == "openai":
        from src.stt.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=config.openai_api_key,
            language=config.stt_language,
            proxy_url=config.proxy_url,
        )
    if name == "google":
        from src.stt.google_provider import GoogleProvider

        return GoogleProvider(
            project=config.google_cloud_project,
            bucket=config.google_stt_gcs_bucket,
            data_dir=config.data_dir,
            language=config.stt_language,
        )
    if name == "deepgram":
        from src.stt.deepgram_provider import DeepgramProvider

        return DeepgramProvider(
            api_key=config.deepgram_api_key,
            language=config.stt_language,
            model=config.deepgram_model,
            diarize_model=config.deepgram_diarize_model,
            txt_formatter=config.deepgram_txt_formatter,
            keyterms=config.deepgram_keyterms,
            proxy_url=config.proxy_url,
        )
    if name == "asr":
        from src.stt.asr_provider import ASRProvider

        return ASRProvider(
            url=config.asr_url,
            language=config.stt_language,
            proxy_url=config.proxy_url,
        )
    raise UnknownProviderError(f"Unknown STT provider: {name!r}")


__all__ = ["STTProvider", "UnknownProviderError", "get_provider"]
