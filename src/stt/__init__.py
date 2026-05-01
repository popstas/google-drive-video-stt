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
            language=config.stt_language,
        )
    raise UnknownProviderError(f"Unknown STT provider: {name!r}")


__all__ = ["STTProvider", "UnknownProviderError", "get_provider"]
