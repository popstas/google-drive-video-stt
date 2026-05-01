from __future__ import annotations

from pathlib import Path

import pytest
import requests

from src.stt.asr_provider import ASRProvider
from src.stt.base import STTError


def test_requires_url():
    with pytest.raises(STTError, match="ASR_URL"):
        ASRProvider(url="")


def test_transcribe_chunk_posts_audio_and_returns_text(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"id3-data")

    fake_response = mocker.MagicMock()
    fake_response.text = "  hello world  "
    fake_response.raise_for_status = mocker.MagicMock()
    post_mock = mocker.patch(
        "src.stt.asr_provider.requests.post", return_value=fake_response
    )

    provider = ASRProvider(url="http://localhost:9000/", language="en")
    text = provider.transcribe_chunk(chunk)

    assert text == "hello world"
    call = post_mock.call_args
    assert call.args[0] == "http://localhost:9000/asr"
    assert call.kwargs["params"] == {
        "output": "text",
        "task": "transcribe",
        "language": "en",
    }
    files = call.kwargs["files"]
    assert "audio_file" in files
    assert files["audio_file"][0] == "c.mp3"
    assert call.kwargs["proxies"] is None


def test_transcribe_chunk_omits_language_when_blank(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")
    fake_response = mocker.MagicMock(text="hi")
    post_mock = mocker.patch(
        "src.stt.asr_provider.requests.post", return_value=fake_response
    )

    provider = ASRProvider(url="http://localhost:9000")
    provider.transcribe_chunk(chunk)

    assert "language" not in post_mock.call_args.kwargs["params"]


def test_transcribe_chunk_passes_proxy(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")
    fake_response = mocker.MagicMock(text="ok")
    post_mock = mocker.patch(
        "src.stt.asr_provider.requests.post", return_value=fake_response
    )

    provider = ASRProvider(url="http://asr:9000", proxy_url="http://proxy:8080")
    provider.transcribe_chunk(chunk)

    assert post_mock.call_args.kwargs["proxies"] == {
        "http": "http://proxy:8080",
        "https": "http://proxy:8080",
    }


def test_transcribe_chunk_propagates_http_errors(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")
    mocker.patch(
        "src.stt.asr_provider.requests.post",
        side_effect=requests.ConnectionError("refused"),
    )

    provider = ASRProvider(url="http://localhost:9000")
    with pytest.raises(STTError, match="refused"):
        provider.transcribe_chunk(chunk)


def test_transcribe_chunk_raises_on_non_2xx(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")
    fake_response = mocker.MagicMock()
    fake_response.raise_for_status.side_effect = requests.HTTPError("500")
    mocker.patch(
        "src.stt.asr_provider.requests.post", return_value=fake_response
    )

    provider = ASRProvider(url="http://localhost:9000")
    with pytest.raises(STTError, match="500"):
        provider.transcribe_chunk(chunk)


def test_transcribe_chunk_missing_file(tmp_path):
    provider = ASRProvider(url="http://localhost:9000")
    with pytest.raises(FileNotFoundError):
        provider.transcribe_chunk(tmp_path / "missing.mp3")
