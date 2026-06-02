from pathlib import Path

import pytest
from dotenv import load_dotenv as real_load_dotenv

from src.config import load_config

ENV_VARS = [
    "FOLDER_IDS",
    "POLL_INTERVAL",
    "BITRATE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DATA_DIR",
    "PROXY_URL",
    "STT_PROVIDER",
    "STT_LANGUAGE",
    "STT_CHUNK_SECONDS",
    "STT_POSTPROCESS",
    "DRIVE_MP3_ARTIFACT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_POSTPROCESS",
    "OPENAI_BATCH",
    "DEEPGRAM_API_KEY",
    "DEEPGRAM_API_KEY_FILE",
    "DEEPGRAM_MODEL",
    "DEEPGRAM_DIARIZE_MODEL",
    "DEEPGRAM_AUDIO_SOURCE",
    "DEEPGRAM_TXT_FORMATTER",
    "DEEPGRAM_KEYTERMS_ENABLED",
    "DEEPGRAM_KEYTERMS_FILE",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_STT_GCS_BUCKET",
    "ASR_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("src.config.load_dotenv", lambda *a, **kw: False)
    yield


def _load_drive_only_config():
    return load_config(validate_providers=False)


def test_defaults_when_no_env(monkeypatch):
    cfg = _load_drive_only_config()
    assert cfg.folder_ids == []
    assert cfg.poll_interval == 600
    assert cfg.bitrate == "96k"
    assert cfg.telegram_bot_token == ""
    assert cfg.telegram_chat_id == ""
    assert cfg.data_dir == Path("data")
    assert cfg.stt_provider == "deepgram"
    assert cfg.stt_chunk_seconds == 600
    assert cfg.stt_language == "ru"
    assert cfg.stt_postprocess is True


def test_default_deepgram_processing_requires_key_and_suggests_setup(monkeypatch):
    with pytest.raises(ValueError, match="gdstt setup"):
        load_config()


def test_stt_provider_disabled_explicitly_turns_transcription_off(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "disabled")

    cfg = load_config()

    assert cfg.stt_provider == ""
    assert cfg.stt_language == ""


def test_stt_postprocess_can_be_disabled(monkeypatch):
    monkeypatch.setenv("STT_POSTPROCESS", "false")
    cfg = _load_drive_only_config()
    assert cfg.stt_postprocess is False


def test_drive_mp3_artifact_defaults_to_true_when_transcription_disabled(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "disabled")

    cfg = load_config()

    assert cfg.drive_mp3_artifact is True


def test_drive_mp3_artifact_defaults_to_false_for_deepgram_m4a(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")

    cfg = load_config()

    assert cfg.deepgram_audio_source == "m4a_copy"
    assert cfg.drive_mp3_artifact is False


def test_drive_mp3_artifact_can_be_enabled_for_deepgram_m4a(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DRIVE_MP3_ARTIFACT", "true")

    cfg = load_config()

    assert cfg.drive_mp3_artifact is True


def test_openai_pipeline_defaults(monkeypatch):
    cfg = _load_drive_only_config()
    assert cfg.openai_model == "gpt-5.4-mini"
    assert cfg.openai_postprocess is False
    assert cfg.openai_batch is False


def test_openai_postprocess_requires_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_POSTPROCESS", "true")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_config()


def test_validate_providers_false_skips_openai_secret(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "openai")
    cfg = load_config(validate_providers=False)
    assert cfg.stt_provider == "openai"
    assert cfg.openai_api_key == ""


def test_validate_providers_false_skips_provider_secrets(monkeypatch):
    # A partially configured provider must not block Drive-only commands.
    monkeypatch.setenv("STT_PROVIDER", "google")
    cfg = load_config(validate_providers=False)
    assert cfg.stt_provider == "google"


def test_validate_providers_true_is_default(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "openai")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_config()


def test_openai_postprocess_with_api_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "disabled")
    monkeypatch.setenv("OPENAI_POSTPROCESS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")
    monkeypatch.setenv("OPENAI_BATCH", "true")
    cfg = load_config()
    assert cfg.openai_postprocess is True
    assert cfg.openai_api_key == "sk-test"
    assert cfg.openai_model == "gpt-5.4"
    assert cfg.openai_batch is True


def test_openai_postprocess_enabled_without_stt_provider(monkeypatch):
    # OPENAI_POSTPROCESS is independent of STT_PROVIDER selection.
    monkeypatch.setenv("STT_PROVIDER", "disabled")
    monkeypatch.setenv("OPENAI_POSTPROCESS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = load_config()
    assert cfg.stt_provider == ""
    assert cfg.openai_postprocess is True


def test_parses_single_folder_id(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "abc123")
    cfg = _load_drive_only_config()
    assert cfg.folder_ids == ["abc123"]


def test_load_config_accepts_dotenv_with_utf8_bom(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.config.load_dotenv", real_load_dotenv)
    (tmp_path / ".env").write_text("FOLDER_IDS=abc123\n", encoding="utf-8-sig")

    cfg = _load_drive_only_config()

    assert cfg.folder_ids == ["abc123"]


def test_load_config_falls_back_to_checkout_env_outside_repo(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    elsewhere = tmp_path / "elsewhere"
    checkout.mkdir()
    elsewhere.mkdir()
    (checkout / ".env").write_text(
        "FOLDER_IDS=folder-1\nDATA_DIR=data\nSTT_PROVIDER=disabled\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("src.config.CHECKOUT_ROOT", checkout, raising=False)
    monkeypatch.setattr("src.config.load_dotenv", real_load_dotenv)

    cfg = load_config()

    assert cfg.folder_ids == ["folder-1"]
    assert cfg.data_dir == checkout / "data"


def test_parses_multiple_folder_ids(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "id1,id2,id3")
    cfg = _load_drive_only_config()
    assert cfg.folder_ids == ["id1", "id2", "id3"]


def test_strips_whitespace_in_folder_ids(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", " id1 , id2 ,  ,id3 ")
    cfg = _load_drive_only_config()
    assert cfg.folder_ids == ["id1", "id2", "id3"]


def test_empty_folder_ids_returns_empty_list(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "")
    cfg = _load_drive_only_config()
    assert cfg.folder_ids == []


def test_folder_ids_only_commas(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", " , , ")
    with pytest.raises(ValueError, match="FOLDER_IDS"):
        load_config()


def test_custom_poll_interval(monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL", "120")
    cfg = _load_drive_only_config()
    assert cfg.poll_interval == 120


def test_invalid_poll_interval_raises(monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL", "not-a-number")
    with pytest.raises(ValueError, match="POLL_INTERVAL"):
        load_config()


def test_non_positive_poll_interval_raises(monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL", "0")
    with pytest.raises(ValueError, match="positive"):
        load_config()


def test_custom_bitrate(monkeypatch):
    monkeypatch.setenv("BITRATE", "128k")
    cfg = _load_drive_only_config()
    assert cfg.bitrate == "128k"


def test_blank_bitrate_uses_default(monkeypatch):
    monkeypatch.setenv("BITRATE", "")
    cfg = _load_drive_only_config()
    assert cfg.bitrate == "96k"


def test_telegram_credentials(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    cfg = _load_drive_only_config()
    assert cfg.telegram_bot_token == "token-xyz"
    assert cfg.telegram_chat_id == "12345"


def test_custom_data_dir(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "/var/lib/stt")
    cfg = _load_drive_only_config()
    assert cfg.data_dir == Path("/var/lib/stt")


def test_blank_data_dir_uses_default(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "")
    cfg = _load_drive_only_config()
    assert cfg.data_dir == Path("data")


def test_stt_openai_requires_api_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "openai")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_config()


def test_stt_openai_with_api_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = load_config()
    assert cfg.stt_provider == "openai"
    assert cfg.openai_api_key == "sk-test"


def test_stt_deepgram_requires_api_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        load_config()


def test_stt_deepgram_defaults_language_and_options(monkeypatch, tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text(
        "# tech terms\nKubernetes\n\nRuby on Rails\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", str(keyterms))

    cfg = load_config()

    assert cfg.stt_language == "ru"
    assert cfg.deepgram_model == "nova-3"
    assert cfg.deepgram_diarize_model == "latest"
    assert cfg.deepgram_audio_source == "m4a_copy"
    assert cfg.deepgram_txt_formatter == "word_speaker"
    assert cfg.deepgram_keyterms_enabled is True
    assert cfg.deepgram_keyterms_file == keyterms
    assert cfg.deepgram_keyterms == ("Kubernetes", "Ruby on Rails")


def test_stt_deepgram_with_api_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "  dg-test  ")
    cfg = load_config()
    assert cfg.stt_provider == "deepgram"
    assert cfg.stt_language == "ru"
    assert cfg.deepgram_api_key == "dg-test"


def test_stt_deepgram_accepts_custom_options(monkeypatch, tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("React\nTypeScript\n", encoding="utf-8")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("STT_LANGUAGE", "multi")
    monkeypatch.setenv("DEEPGRAM_MODEL", "nova-2")
    monkeypatch.setenv("DEEPGRAM_DIARIZE_MODEL", "v1")
    monkeypatch.setenv("DEEPGRAM_AUDIO_SOURCE", "mp3_96k")
    monkeypatch.setenv("DEEPGRAM_TXT_FORMATTER", "utterance")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", str(keyterms))

    cfg = load_config()

    assert cfg.stt_language == "multi"
    assert cfg.deepgram_model == "nova-2"
    assert cfg.deepgram_diarize_model == "v1"
    assert cfg.deepgram_audio_source == "mp3_96k"
    assert cfg.deepgram_txt_formatter == "utterance"
    assert cfg.deepgram_keyterms == ("React", "TypeScript")


def test_stt_deepgram_accepts_mp3_192k_audio_source(monkeypatch, tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("React\n", encoding="utf-8")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DEEPGRAM_AUDIO_SOURCE", "mp3_192k")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", str(keyterms))

    cfg = load_config()

    assert cfg.deepgram_audio_source == "mp3_192k"


def test_stt_deepgram_rejects_invalid_options(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")

    monkeypatch.setenv("DEEPGRAM_DIARIZE_MODEL", "bad")
    with pytest.raises(ValueError, match="DEEPGRAM_DIARIZE_MODEL"):
        load_config()
    monkeypatch.setenv("DEEPGRAM_DIARIZE_MODEL", "latest")

    monkeypatch.setenv("DEEPGRAM_AUDIO_SOURCE", "wav")
    with pytest.raises(ValueError, match="DEEPGRAM_AUDIO_SOURCE"):
        load_config()
    monkeypatch.setenv("DEEPGRAM_AUDIO_SOURCE", "m4a_copy")

    monkeypatch.setenv("DEEPGRAM_TXT_FORMATTER", "plain")
    with pytest.raises(ValueError, match="DEEPGRAM_TXT_FORMATTER"):
        load_config()


def test_stt_deepgram_can_disable_keyterms(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_ENABLED", "false")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", "missing.txt")

    cfg = load_config()

    assert cfg.deepgram_keyterms_enabled is False
    assert cfg.deepgram_keyterms == ()


def test_stt_deepgram_rejects_missing_keyterms_file_when_enabled(monkeypatch, tmp_path):
    missing_keyterms = tmp_path / "missing-keyterms.txt"
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_ENABLED", "true")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", str(missing_keyterms))

    with pytest.raises(ValueError, match="could not be read"):
        load_config()


def test_stt_deepgram_rejects_too_many_keyterms(monkeypatch, tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("\n".join(f"term-{i}" for i in range(101)), encoding="utf-8")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", str(keyterms))

    with pytest.raises(ValueError, match="100"):
        load_config()


def test_stt_deepgram_prefers_env_key_over_file(monkeypatch, tmp_path):
    key_file = tmp_path / "deepgram.json"
    key_file.write_text("file-key", encoding="utf-8")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "env-key")
    monkeypatch.setenv("DEEPGRAM_API_KEY_FILE", str(key_file))

    cfg = load_config()

    assert cfg.deepgram_api_key == "env-key"


def test_stt_deepgram_reads_raw_key_file(monkeypatch, tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text("  raw-file-key  \n", encoding="utf-8")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    monkeypatch.setenv("DEEPGRAM_API_KEY_FILE", str(key_file))

    cfg = load_config()

    assert cfg.deepgram_api_key == "raw-file-key"


def test_stt_deepgram_reads_raw_key_file_with_utf8_bom(monkeypatch, tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text("raw-file-key\n", encoding="utf-8-sig")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    monkeypatch.setenv("DEEPGRAM_API_KEY_FILE", str(key_file))

    cfg = load_config()

    assert cfg.deepgram_api_key == "raw-file-key"


def test_stt_deepgram_reads_json_key_file(monkeypatch, tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text('{"deepgram_api_key": "json-file-key"}', encoding="utf-8")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    monkeypatch.setenv("DEEPGRAM_API_KEY_FILE", str(key_file))

    cfg = load_config()

    assert cfg.deepgram_api_key == "json-file-key"


def test_stt_deepgram_reads_json_key_file_with_utf8_bom(monkeypatch, tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text('{"deepgram_api_key": "json-file-key"}', encoding="utf-8-sig")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("STT_LANGUAGE", "ru")
    monkeypatch.setenv("DEEPGRAM_API_KEY_FILE", str(key_file))

    cfg = load_config()

    assert cfg.deepgram_api_key == "json-file-key"


def test_stt_deepgram_reads_keyterms_file_with_utf8_bom(monkeypatch, tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("# header\nKubernetes\n", encoding="utf-8-sig")
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("DEEPGRAM_KEYTERMS_FILE", str(keyterms))

    cfg = load_config()

    assert cfg.deepgram_keyterms == ("Kubernetes",)


def test_stt_deepgram_key_file_is_ignored_for_other_providers(monkeypatch, tmp_path):
    missing_key_file = tmp_path / "missing.json"
    monkeypatch.setenv("STT_PROVIDER", "asr")
    monkeypatch.setenv("ASR_URL", "http://localhost:9000")
    monkeypatch.setenv("DEEPGRAM_API_KEY_FILE", str(missing_key_file))

    cfg = load_config()

    assert cfg.stt_provider == "asr"
    assert cfg.deepgram_api_key == ""


def test_stt_google_requires_project_and_bucket(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "google")
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        load_config()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-1")
    with pytest.raises(ValueError, match="GOOGLE_STT_GCS_BUCKET"):
        load_config()
    monkeypatch.setenv("GOOGLE_STT_GCS_BUCKET", "my-stt-bucket")
    with pytest.raises(ValueError, match="STT_LANGUAGE"):
        load_config()
    monkeypatch.setenv("STT_LANGUAGE", "en-US")
    cfg = load_config()
    assert cfg.stt_provider == "google"
    assert cfg.google_cloud_project == "proj-1"
    assert cfg.google_stt_gcs_bucket == "my-stt-bucket"
    assert cfg.stt_language == "en-US"


def test_stt_google_rejects_empty_language(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-1")
    monkeypatch.setenv("GOOGLE_STT_GCS_BUCKET", "my-stt-bucket")
    monkeypatch.setenv("STT_LANGUAGE", "")
    with pytest.raises(ValueError, match="STT_LANGUAGE"):
        load_config()


def test_stt_asr_requires_url(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "asr")
    with pytest.raises(ValueError, match="ASR_URL"):
        load_config()


def test_stt_asr_with_url(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "asr")
    monkeypatch.setenv("ASR_URL", "http://localhost:9000")
    cfg = load_config()
    assert cfg.stt_provider == "asr"
    assert cfg.asr_url == "http://localhost:9000"


def test_stt_unsupported_provider_raises(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "azure")
    with pytest.raises(ValueError, match="STT_PROVIDER"):
        load_config()


def test_stt_chunk_seconds_invalid(monkeypatch):
    monkeypatch.setenv("STT_CHUNK_SECONDS", "bad")
    with pytest.raises(ValueError, match="STT_CHUNK_SECONDS"):
        load_config()


def test_stt_chunk_seconds_non_positive(monkeypatch):
    monkeypatch.setenv("STT_CHUNK_SECONDS", "0")
    with pytest.raises(ValueError, match="positive"):
        load_config()


def test_full_env_combination(monkeypatch):
    monkeypatch.setenv("FOLDER_IDS", "f1,f2")
    monkeypatch.setenv("POLL_INTERVAL", "300")
    monkeypatch.setenv("BITRATE", "192k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")
    monkeypatch.setenv("DATA_DIR", "mydata")
    cfg = _load_drive_only_config()
    assert cfg.folder_ids == ["f1", "f2"]
    assert cfg.poll_interval == 300
    assert cfg.bitrate == "192k"
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "cid"
    assert cfg.data_dir == Path("mydata")
