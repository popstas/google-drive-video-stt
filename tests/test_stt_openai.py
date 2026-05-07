from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.stt.base import STTError
from src.stt.openai_provider import OpenAIProvider


def test_requires_api_key():
    with pytest.raises(STTError, match="OPENAI_API_KEY"):
        OpenAIProvider(api_key="")


def test_transcribe_chunk_calls_openai_with_text_response(mocker, tmp_path):
    chunk = tmp_path / "chunk.mp3"
    chunk.write_bytes(b"id3")

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = "  hello  "
    mocker.patch.dict("sys.modules", {})
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    mocker.patch.dict("sys.modules", {"openai": fake_openai})

    provider = OpenAIProvider(api_key="sk-x", language="en")
    text = provider.transcribe_chunk(chunk)

    assert text == "hello"
    fake_openai.OpenAI.assert_called_once_with(api_key="sk-x")
    call = fake_client.audio.transcriptions.create.call_args
    assert call.kwargs["model"] == "whisper-1"
    assert call.kwargs["response_format"] == "text"
    assert call.kwargs["language"] == "en"


def test_transcribe_chunk_handles_object_response(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")

    fake_client = MagicMock()
    response = MagicMock()
    response.text = "world"
    fake_client.audio.transcriptions.create.return_value = response
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    mocker.patch.dict("sys.modules", {"openai": fake_openai})

    provider = OpenAIProvider(api_key="sk-x")
    assert provider.transcribe_chunk(chunk) == "world"


def test_transcribe_chunk_propagates_errors_as_stt_error(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.side_effect = RuntimeError("rate-limited")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    mocker.patch.dict("sys.modules", {"openai": fake_openai})

    provider = OpenAIProvider(api_key="sk-x")
    with pytest.raises(STTError, match="rate-limited"):
        provider.transcribe_chunk(chunk)


def test_transcribe_chunk_uses_proxy_http_client(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = "ok"
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    fake_httpx = MagicMock()
    fake_httpx.Client.return_value = "http-client-instance"
    mocker.patch.dict("sys.modules", {"openai": fake_openai, "httpx": fake_httpx})

    provider = OpenAIProvider(api_key="sk-x", proxy_url="http://proxy:8080")
    provider.transcribe_chunk(chunk)

    fake_httpx.Client.assert_called_once_with(proxy="http://proxy:8080")
    fake_openai.OpenAI.assert_called_once_with(
        api_key="sk-x", http_client="http-client-instance"
    )


def test_transcribe_chunk_missing_file(tmp_path):
    provider = OpenAIProvider(api_key="sk-x")
    with pytest.raises(FileNotFoundError):
        provider.transcribe_chunk(tmp_path / "missing.mp3")
