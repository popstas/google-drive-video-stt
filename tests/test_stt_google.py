from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.stt.base import STTError
from src.stt.google_provider import GoogleProvider


def _install_fake_google_modules(mocker, recognize_response):
    fake_client = MagicMock()
    fake_client.recognize.return_value = recognize_response
    speech_client_cls = MagicMock(return_value=fake_client)
    speech_v2_module = MagicMock(SpeechClient=speech_client_cls)

    types_module = MagicMock()
    types_module.RecognitionConfig = MagicMock(name="RecognitionConfig")
    types_module.AutoDetectDecodingConfig = MagicMock(name="AutoDetectDecodingConfig")
    types_module.RecognizeRequest = MagicMock(name="RecognizeRequest")

    google_pkg = MagicMock()
    google_cloud_pkg = MagicMock()
    mocker.patch.dict(
        "sys.modules",
        {
            "google.cloud.speech_v2": speech_v2_module,
            "google.cloud.speech_v2.types": types_module,
            "google.cloud": google_cloud_pkg,
            "google": google_pkg,
        },
    )
    return fake_client, speech_v2_module, types_module


def test_requires_project():
    with pytest.raises(STTError, match="GOOGLE_CLOUD_PROJECT"):
        GoogleProvider(project="")


def test_transcribe_chunk_concatenates_alternatives(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"audio")

    response = MagicMock()
    alt_a = MagicMock()
    alt_a.transcript = "hello"
    alt_b = MagicMock()
    alt_b.transcript = "world"
    result_a = MagicMock(alternatives=[alt_a])
    result_b = MagicMock(alternatives=[alt_b])
    response.results = [result_a, result_b]

    fake_client, _, types_module = _install_fake_google_modules(mocker, response)

    provider = GoogleProvider(project="proj-1", language="en")
    text = provider.transcribe_chunk(chunk)

    assert text == "hello world"
    fake_client.recognize.assert_called_once()
    types_module.RecognitionConfig.assert_called_once()
    types_module.RecognizeRequest.assert_called_once()


def test_transcribe_chunk_propagates_errors(mocker, tmp_path):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")

    response = MagicMock()
    fake_client, _, _ = _install_fake_google_modules(mocker, response)
    fake_client.recognize.side_effect = RuntimeError("quota")

    provider = GoogleProvider(project="p")
    with pytest.raises(STTError, match="quota"):
        provider.transcribe_chunk(chunk)


def test_transcribe_chunk_missing_file(tmp_path):
    provider = GoogleProvider(project="p")
    with pytest.raises(FileNotFoundError):
        provider.transcribe_chunk(tmp_path / "missing.mp3")
