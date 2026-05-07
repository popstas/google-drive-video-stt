from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.stt.base import STTError
from src.stt.google_provider import GoogleProvider


def _install_fake_google_modules(mocker, batch_response, *, raise_on_recognize=None):
    fake_speech_client = MagicMock()
    operation = MagicMock()
    if raise_on_recognize is not None:
        fake_speech_client.batch_recognize.side_effect = raise_on_recognize
    else:
        operation.result.return_value = batch_response
        fake_speech_client.batch_recognize.return_value = operation
    speech_client_cls = MagicMock(return_value=fake_speech_client)
    speech_v2_module = MagicMock(SpeechClient=speech_client_cls)

    types_module = MagicMock()
    for name in (
        "AutoDetectDecodingConfig",
        "BatchRecognizeFileMetadata",
        "BatchRecognizeRequest",
        "InlineOutputConfig",
        "RecognitionConfig",
        "RecognitionFeatures",
        "RecognitionOutputConfig",
        "SpeakerDiarizationConfig",
    ):
        setattr(types_module, name, MagicMock(name=name))

    fake_blob = MagicMock()
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_storage_client = MagicMock()
    fake_storage_client.bucket.return_value = fake_bucket
    storage_client_cls = MagicMock(return_value=fake_storage_client)
    storage_module = MagicMock(Client=storage_client_cls)
    google_cloud_pkg = MagicMock(storage=storage_module)
    google_pkg = MagicMock()

    mocker.patch.dict(
        "sys.modules",
        {
            "google.cloud.speech_v2": speech_v2_module,
            "google.cloud.speech_v2.types": types_module,
            "google.cloud.storage": storage_module,
            "google.cloud": google_cloud_pkg,
            "google": google_pkg,
        },
    )
    return {
        "speech_client": fake_speech_client,
        "speech_v2": speech_v2_module,
        "types": types_module,
        "storage_client": fake_storage_client,
        "bucket": fake_bucket,
        "blob": fake_blob,
    }


def _word(text, speaker, start_seconds):
    w = MagicMock()
    w.word = text
    w.speaker_label = speaker
    w.start_offset = timedelta(seconds=start_seconds)
    w.end_offset = timedelta(seconds=start_seconds + 1)
    return w


def _make_response(words_per_alt, gcs_uri):
    file_result = MagicMock()
    transcript = MagicMock()
    alt = MagicMock()
    alt.words = words_per_alt
    inner_result = MagicMock()
    inner_result.alternatives = [alt]
    transcript.results = [inner_result]
    # Real v2 API populates inline_result.transcript when InlineOutputConfig is set;
    # the top-level `transcript` field is deprecated and arrives default-constructed.
    file_result.inline_result.transcript = transcript
    deprecated = MagicMock()
    deprecated.results = []
    file_result.transcript = deprecated
    file_result.error.code = 0
    response = MagicMock()
    response.results = {gcs_uri: file_result}
    return response


def test_requires_project():
    with pytest.raises(STTError, match="GOOGLE_CLOUD_PROJECT"):
        GoogleProvider(project="", bucket="b", data_dir=Path("/tmp"))


def test_requires_bucket():
    with pytest.raises(STTError, match="GOOGLE_STT_GCS_BUCKET"):
        GoogleProvider(project="p", bucket="", data_dir=Path("/tmp"))


def test_transcribe_full_diarized(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"audio")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))

    words = [
        _word("hello", 1, 0),
        _word("world", 1, 1),
        _word("hi", 2, 5),
        _word("there", 2, 6),
    ]

    mocks = _install_fake_google_modules(mocker, None)
    provider = GoogleProvider(
        project="proj-1", bucket="my-bucket", data_dir=tmp_path, language="en"
    )
    # capture URI used to keyed response after blob_name is generated
    fake_response = MagicMock()
    fake_response.results = {}

    def _on_batch(request=None, **kwargs):
        op = MagicMock()
        # Build response from any URI: just rebuild keyed by what _format_diarized expects
        # We set up by capturing the actual uri the provider used:
        op.result.return_value = _make_response(words, _on_batch.captured_uri)
        return op

    # Hook: capture URI when blob() is called
    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://my-bucket/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    text = provider.transcribe_full(audio)

    assert text == "[00:00:00] Speaker 1: hello world\n[00:00:05] Speaker 2: hi there"
    mocks["blob"].upload_from_filename.assert_called_once_with(str(audio))
    mocks["blob"].delete.assert_called_once()
    # batch_recognize called with model=long
    call = mocks["speech_client"].batch_recognize.call_args
    assert call is not None
    # RecognitionConfig was constructed with model="long"
    rc_call = mocks["types"].RecognitionConfig.call_args
    assert rc_call.kwargs.get("model") == "long"
    # diarization features were built
    mocks["types"].SpeakerDiarizationConfig.assert_called_once()
    feat_call = mocks["types"].RecognitionFeatures.call_args
    assert feat_call.kwargs.get("enable_word_time_offsets") is True


def test_blob_deleted_on_error(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))
    mocks = _install_fake_google_modules(
        mocker, None, raise_on_recognize=RuntimeError("quota")
    )

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    with pytest.raises(STTError, match="quota"):
        provider.transcribe_full(audio)

    mocks["blob"].delete.assert_called_once()


def test_auth_error_wrapped_as_stt_error(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    from src.auth import AuthError

    mocker.patch("src.auth.load_credentials", side_effect=AuthError("missing token"))
    _install_fake_google_modules(mocker, None)

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    with pytest.raises(STTError, match="missing token"):
        provider.transcribe_full(audio)


def test_transcribe_full_missing_file(tmp_path):
    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    with pytest.raises(FileNotFoundError):
        provider.transcribe_full(tmp_path / "missing.mp3")


def test_processing_strategy_is_dynamic_batching(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))

    words = [_word("hi", 1, 0)]
    mocks = _install_fake_google_modules(mocker, None)

    sentinel = object()
    mocks["types"].BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING = sentinel

    def _on_batch(request=None, **kwargs):
        op = MagicMock()
        op.result.return_value = _make_response(words, _on_batch.captured_uri)
        return op

    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://b/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    provider.transcribe_full(audio)

    req_call = mocks["types"].BatchRecognizeRequest.call_args
    assert req_call.kwargs.get("processing_strategy") is sentinel


def test_operation_result_called_with_timeout(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))

    words = [_word("hi", 1, 0)]
    mocks = _install_fake_google_modules(mocker, None)

    captured_op = MagicMock()
    captured_op.result.return_value = _make_response(words, "gs://b/x")

    def _on_batch(request=None, **kwargs):
        captured_op.result.return_value = _make_response(words, _on_batch.captured_uri)
        return captured_op

    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://b/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    provider.transcribe_full(audio)

    captured_op.result.assert_called_once()
    assert captured_op.result.call_args.kwargs.get("timeout") is not None


def test_missing_speaker_label_does_not_emit_speaker_zero(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))

    # Mix of words: first has no label, then valid speaker 1, then a None label.
    words = [
        _word("um", None, 0),
        _word("hello", 1, 1),
        _word("world", None, 2),
        _word("hi", 2, 5),
    ]
    mocks = _install_fake_google_modules(mocker, None)

    def _on_batch(request=None, **kwargs):
        op = MagicMock()
        op.result.return_value = _make_response(words, _on_batch.captured_uri)
        return op

    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://b/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    text = provider.transcribe_full(audio)

    assert "Speaker 0" not in text
    # Missing-label word before any valid speaker defaults to Speaker 1; subsequent
    # missing-label words merge into the current turn.
    assert text.startswith("[00:00:00] Speaker 1: um hello world")
    assert "[00:00:05] Speaker 2: hi" in text


def test_reads_inline_result_transcript_not_deprecated_field(mocker, tmp_path):
    """Real v2 API populates inline_result.transcript; reading the deprecated
    top-level transcript field returns an empty default-constructed message."""
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))
    mocks = _install_fake_google_modules(mocker, None)

    words = [_word("hi", 1, 0)]

    def _on_batch(request=None, **kwargs):
        op = MagicMock()
        # Build a response where ONLY inline_result.transcript carries data;
        # the deprecated top-level transcript is empty (mirrors real protos).
        file_result = MagicMock()
        transcript = MagicMock()
        alt = MagicMock()
        alt.words = words
        inner = MagicMock()
        inner.alternatives = [alt]
        transcript.results = [inner]
        file_result.inline_result.transcript = transcript
        empty = MagicMock()
        empty.results = []
        file_result.transcript = empty
        file_result.error.code = 0
        response = MagicMock()
        response.results = {_on_batch.captured_uri: file_result}
        op.result.return_value = response
        return op

    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://b/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    assert provider.transcribe_full(audio) == "[00:00:00] Speaker 1: hi"


def test_file_result_error_code_raises_stt_error(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))
    mocks = _install_fake_google_modules(mocker, None)

    def _on_batch(request=None, **kwargs):
        op = MagicMock()
        file_result = MagicMock()
        file_result.error.code = 7  # PERMISSION_DENIED
        file_result.error.message = "denied"
        # No usable transcript on either field
        empty = MagicMock()
        empty.results = []
        file_result.inline_result.transcript = empty
        file_result.transcript = empty
        response = MagicMock()
        response.results = {_on_batch.captured_uri: file_result}
        op.result.return_value = response
        return op

    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://b/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    with pytest.raises(STTError, match="denied"):
        provider.transcribe_full(audio)
    # Blob still cleaned up on file-level error.
    mocks["blob"].delete.assert_called_once()


def test_transcribe_chunk_routes_to_full(mocker, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")

    mocker.patch("src.auth.load_credentials", return_value=MagicMock(name="creds"))
    mocks = _install_fake_google_modules(mocker, None)

    words = [_word("hello", 1, 0)]

    def _on_batch(request=None, **kwargs):
        op = MagicMock()
        op.result.return_value = _make_response(words, _on_batch.captured_uri)
        return op

    captured_blob = mocks["blob"]

    def _capture_blob(name):
        _on_batch.captured_uri = f"gs://b/{name}"
        return captured_blob

    mocks["bucket"].blob = MagicMock(side_effect=_capture_blob)
    mocks["speech_client"].batch_recognize.side_effect = _on_batch

    provider = GoogleProvider(
        project="p", bucket="b", data_dir=tmp_path, language="en"
    )
    text = provider.transcribe_chunk(audio)
    assert text == "[00:00:00] Speaker 1: hello"
