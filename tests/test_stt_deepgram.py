from __future__ import annotations

import requests
import pytest

from src.stt.base import STTError
from src.stt.deepgram_provider import DeepgramProvider


def _response(payload, *, status_error=None):
    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.text = "response text"

        def json(self):
            return payload

        def raise_for_status(self):
            if status_error is not None:
                raise status_error

    return FakeResponse()


def _payload_with_utterances():
    return {
        "metadata": {
            "request_id": "req-1",
            "duration": 8.5,
        },
        "results": {
            "utterances": [
                {
                    "start": 0.12,
                    "speaker": 0,
                    "transcript": "Привет, коллеги.",
                },
                {
                    "start": 5.7,
                    "speaker": 1,
                    "transcript": "Добрый день.",
                },
            ]
        },
    }


def _payload_with_mixed_speaker_words():
    return {
        "metadata": {"request_id": "req-mixed", "duration": 12.0},
        "results": {
            "utterances": [
                {
                    "start": 10.0,
                    "end": 14.0,
                    "speaker": 0,
                    "transcript": "Question? Yes, answer.",
                    "words": [
                        {
                            "start": 10.0,
                            "speaker": 0,
                            "word": "question",
                            "punctuated_word": "Question?",
                        },
                        {
                            "start": 11.5,
                            "speaker": 1,
                            "word": "yes",
                            "punctuated_word": "Yes,",
                        },
                        {
                            "start": 11.8,
                            "speaker": 1,
                            "word": "answer",
                            "punctuated_word": "answer.",
                        },
                    ],
                }
            ]
        },
    }


def _payload_with_words():
    return {
        "metadata": {"request_id": "req-words", "duration": 4.0},
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "words": [
                                {
                                    "start": 0.0,
                                    "speaker": 0,
                                    "word": "privet",
                                    "punctuated_word": "Привет,",
                                },
                                {
                                    "start": 0.4,
                                    "speaker": 0,
                                    "word": "anna",
                                    "punctuated_word": "Анна.",
                                },
                                {
                                    "start": 2.2,
                                    "speaker": 1,
                                    "word": "zdravstvuite",
                                    "punctuated_word": "Здравствуйте.",
                                },
                            ]
                        }
                    ]
                }
            ]
        },
    }


def test_requires_api_key():
    with pytest.raises(STTError, match="DEEPGRAM_API_KEY"):
        DeepgramProvider(api_key="", language="ru")


def test_requires_language():
    with pytest.raises(STTError, match="STT_LANGUAGE"):
        DeepgramProvider(api_key="dg-key", language="")


def test_transcribe_full_posts_audio_and_formats_utterances(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    post_mock = mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_utterances()),
    )

    provider = DeepgramProvider(
        api_key="dg-key",
        language="ru",
        model="nova-3",
        diarize_model="latest",
        txt_formatter="utterance",
        keyterms=("Kubernetes", "Docker"),
    )
    text = provider.transcribe_full(audio)

    assert text == (
        "[00:00:00] Speaker 1: Привет, коллеги.\n"
        "[00:00:05] Speaker 2: Добрый день."
    )
    assert provider.last_request_id == "req-1"
    assert provider.last_duration_seconds == 8.5
    call = post_mock.call_args
    assert call.args[0] == "https://api.deepgram.com/v1/listen"
    assert call.kwargs["params"] == {
        "model": "nova-3",
        "language": "ru",
        "diarize_model": "latest",
        "utterances": "true",
        "punctuate": "true",
        "smart_format": "true",
        "keyterm": ("Kubernetes", "Docker"),
    }
    assert call.kwargs["headers"]["Authorization"] == "Token dg-key"
    assert call.kwargs["headers"]["Content-Type"] == "audio/mpeg"
    assert call.kwargs["proxies"] is None


def test_transcribe_full_uses_m4a_content_type(mocker, tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"m4a")
    post_mock = mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_utterances()),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")
    provider.transcribe_full(audio)

    assert post_mock.call_args.kwargs["headers"]["Content-Type"] == "audio/mp4"


def test_transcribe_full_does_not_send_keyterms_for_non_nova3(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    post_mock = mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_utterances()),
    )

    provider = DeepgramProvider(
        api_key="dg-key",
        language="ru",
        model="nova-2",
        keyterms=("Kubernetes",),
    )
    provider.transcribe_full(audio)

    assert "keyterm" not in post_mock.call_args.kwargs["params"]


def test_word_speaker_formatter_splits_utterance_on_word_speaker_change(
    mocker,
    tmp_path,
):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_mixed_speaker_words()),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")

    assert provider.transcribe_full(audio) == (
        "[00:00:10] Speaker 1: Question?\n"
        "[00:00:11] Speaker 2: Yes, answer."
    )


def test_utterance_formatter_keeps_old_utterance_output(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_mixed_speaker_words()),
    )

    provider = DeepgramProvider(
        api_key="dg-key",
        language="ru",
        txt_formatter="utterance",
    )

    assert provider.transcribe_full(audio) == (
        "[00:00:10] Speaker 1: Question? Yes, answer."
    )


def test_transcribe_full_uses_proxy(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    post_mock = mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_utterances()),
    )

    provider = DeepgramProvider(
        api_key="dg-key",
        language="ru",
        proxy_url="http://proxy:8080",
    )
    provider.transcribe_full(audio)

    assert post_mock.call_args.kwargs["proxies"] == {
        "http": "http://proxy:8080",
        "https": "http://proxy:8080",
    }


def test_transcribe_chunk_routes_to_full(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_utterances()),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")

    assert provider.transcribe_chunk(audio).startswith("[00:00:00] Speaker 1:")


def test_transcribe_full_falls_back_to_word_grouping(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(_payload_with_words()),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")

    assert provider.transcribe_full(audio) == (
        "[00:00:00] Speaker 1: Привет, Анна.\n"
        "[00:00:02] Speaker 2: Здравствуйте."
    )


def test_transcribe_full_raises_when_utterance_has_no_speaker(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    payload = {
        "results": {
            "utterances": [
                {"start": 0.0, "transcript": "hello"},
            ]
        }
    }
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(payload),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")
    with pytest.raises(STTError, match="speaker"):
        provider.transcribe_full(audio)


def test_transcribe_full_raises_when_words_have_no_speaker(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    payload = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "hello",
                            "words": [{"start": 0.0, "word": "hello"}],
                        }
                    ]
                }
            ]
        }
    }
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        return_value=_response(payload),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")
    with pytest.raises(STTError, match="speaker"):
        provider.transcribe_full(audio)


def test_transcribe_full_wraps_http_errors(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    mocker.patch(
        "src.stt.deepgram_provider.requests.post",
        side_effect=requests.ConnectionError("network down"),
    )

    provider = DeepgramProvider(api_key="dg-key", language="ru")
    with pytest.raises(STTError, match="network down"):
        provider.transcribe_full(audio)


def test_transcribe_full_wraps_bad_status_with_request_id(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    response = _response(
        {"err_msg": "Project does not have enough credits", "request_id": "req-err"},
        status_error=requests.HTTPError("402 Client Error"),
    )
    mocker.patch("src.stt.deepgram_provider.requests.post", return_value=response)

    provider = DeepgramProvider(api_key="dg-key", language="ru")
    with pytest.raises(STTError, match="req-err"):
        provider.transcribe_full(audio)


def test_transcribe_full_wraps_invalid_json(mocker, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"id3")
    response = _response(_payload_with_utterances())
    response.json = mocker.MagicMock(side_effect=ValueError("not json"))
    mocker.patch("src.stt.deepgram_provider.requests.post", return_value=response)

    provider = DeepgramProvider(api_key="dg-key", language="ru")
    with pytest.raises(STTError, match="invalid JSON"):
        provider.transcribe_full(audio)


def test_transcribe_full_missing_file(tmp_path):
    provider = DeepgramProvider(api_key="dg-key", language="ru")
    with pytest.raises(FileNotFoundError):
        provider.transcribe_full(tmp_path / "missing.mp3")
