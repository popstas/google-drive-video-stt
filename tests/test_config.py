import json
import os
from pathlib import Path

import pytest
import yaml

from src.config import (
    CONFIG_FILE_NAME,
    _config_to_yaml_dict,
    config_get,
    config_set,
    config_unset,
    copy_prompt_assets,
    import_google_credentials,
    init_config,
    is_run_enabled,
    load_config,
    set_run_enabled,
    resolve_config_file_path,
    use_google_files,
)
from src.presets import PACKAGED_PROMPT_ASSETS

@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    # Point the config-home resolver at a throwaway per-test directory so tests never
    # read or write the repo's real data/config.yml. Resolver/init tests override or
    # delete GDSTT_HOME as needed. The runtime reads no other environment variables.
    monkeypatch.setenv("GDSTT_HOME", str(tmp_path))
    yield


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _load_config(tmp_path, data, *, validate_providers=False):
    """Write ``data`` to ``<tmp_path>/config.yml`` and load it via the file override."""
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, data)
    return load_config(config_path=config_file, validate_providers=validate_providers)


def _deepgram_data(deepgram=None, *, stt_extra=None):
    """Build a deepgram-only config dict (keypoints preset disabled, keyterms off)."""
    dg = {"api_key": "dg-test", "keyterms_enabled": False}
    if deepgram:
        dg.update(deepgram)
    stt = {"provider": "deepgram", "deepgram": dg}
    if stt_extra:
        stt.update(stt_extra)
    return {"stt": stt, "presets": {"keypoints": {"enabled": False}}}


def test_defaults_when_config_minimal(tmp_path):
    cfg = _load_config(tmp_path, {"folder_ids": []})
    assert cfg.folder_ids == []
    assert cfg.poll_interval == 600
    assert cfg.bitrate == "96k"
    # No data_dir key -> defaults to "." (the config home), matching the
    # `config init` template and the GDSTT_HOME instance-dir architecture.
    assert cfg.data_dir == tmp_path
    assert cfg.stt_provider == "deepgram"
    assert cfg.stt_language == "ru"
    assert cfg.stt_postprocess is True
    assert cfg.output_target == "drive"
    assert cfg.output_dir is None
    assert cfg.openai_keypoints is False


def test_stt_postprocess_can_be_disabled(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled", "postprocess": False}})
    assert cfg.stt_postprocess is False


def test_drive_mp3_artifact_defaults_to_true_when_transcription_disabled(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled"}})
    assert cfg.drive_mp3_artifact is True


def test_drive_mp3_artifact_defaults_to_false_for_deepgram_m4a(tmp_path):
    cfg = _load_config(tmp_path, _deepgram_data(), validate_providers=True)
    assert cfg.deepgram_audio_source == "m4a_copy"
    assert cfg.drive_mp3_artifact is False


def test_drive_mp3_artifact_can_be_enabled_for_deepgram_m4a(tmp_path):
    cfg = _load_config(
        tmp_path,
        _deepgram_data(stt_extra={"drive_mp3_artifact": True}),
        validate_providers=True,
    )
    assert cfg.drive_mp3_artifact is True


def test_openai_pipeline_defaults(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled"}})
    assert cfg.openai_model == "gpt-5.4-mini"
    assert cfg.openai_keypoints is False
    assert cfg.openai_batch is False


def test_openai_keypoints_with_api_key(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "openai": {
                "keypoints": True,
                "api_key": "sk-test",
                "model": "gpt-5.4",
                "batch": True,
            },
        },
        validate_providers=True,
    )
    assert cfg.openai_keypoints is True
    assert cfg.openai_api_key == "sk-test"
    assert cfg.openai_model == "gpt-5.4"
    assert cfg.openai_batch is True


def test_openai_keypoints_enabled_without_stt_provider(tmp_path):
    # openai.keypoints is independent of the stt.provider selection.
    cfg = _load_config(
        tmp_path,
        {"stt": {"provider": "disabled"}, "openai": {"keypoints": True, "api_key": "sk-test"}},
        validate_providers=True,
    )
    assert cfg.stt_provider == ""
    assert cfg.openai_keypoints is True


def test_output_target_defaults_to_drive(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled"}})
    assert cfg.output_target == "drive"
    assert cfg.output_dir is None


def test_output_target_folder_with_output_dir(tmp_path):
    output_dir = tmp_path / "transcripts"
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "output": {"target": "folder", "dir": str(output_dir)},
        },
    )
    assert cfg.output_target == "folder"
    assert cfg.output_dir == output_dir


def test_parses_single_folder_id(tmp_path):
    cfg = _load_config(tmp_path, {"folder_ids": "abc123", "stt": {"provider": "disabled"}})
    assert cfg.folder_ids == ["abc123"]


def test_parses_multiple_folder_ids(tmp_path):
    cfg = _load_config(tmp_path, {"folder_ids": "id1,id2,id3", "stt": {"provider": "disabled"}})
    assert cfg.folder_ids == ["id1", "id2", "id3"]


def test_strips_whitespace_in_folder_ids(tmp_path):
    cfg = _load_config(
        tmp_path, {"folder_ids": " id1 , id2 ,  ,id3 ", "stt": {"provider": "disabled"}}
    )
    assert cfg.folder_ids == ["id1", "id2", "id3"]


def test_empty_folder_ids_returns_empty_list(tmp_path):
    cfg = _load_config(tmp_path, {"folder_ids": "", "stt": {"provider": "disabled"}})
    assert cfg.folder_ids == []


def test_folder_ids_only_commas_returns_empty_list(tmp_path):
    cfg = _load_config(tmp_path, {"folder_ids": " , , ", "stt": {"provider": "disabled"}})
    assert cfg.folder_ids == []


def test_custom_poll_interval(tmp_path):
    cfg = _load_config(tmp_path, {"poll_interval": 120, "stt": {"provider": "disabled"}})
    assert cfg.poll_interval == 120


def test_invalid_poll_interval_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"poll_interval": "not-a-number"})
    with pytest.raises(ValueError, match="poll_interval"):
        load_config(config_path=config_file, validate_providers=False)


def test_non_positive_poll_interval_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"poll_interval": 0})
    with pytest.raises(ValueError, match="positive"):
        load_config(config_path=config_file, validate_providers=False)


def test_custom_bitrate(tmp_path):
    cfg = _load_config(tmp_path, {"bitrate": "128k", "stt": {"provider": "disabled"}})
    assert cfg.bitrate == "128k"


def test_blank_bitrate_uses_default(tmp_path):
    cfg = _load_config(tmp_path, {"bitrate": "", "stt": {"provider": "disabled"}})
    assert cfg.bitrate == "96k"


def test_custom_data_dir(tmp_path):
    data_dir = tmp_path / "stt"
    cfg = _load_config(tmp_path, {"data_dir": str(data_dir), "stt": {"provider": "disabled"}})
    assert cfg.data_dir == data_dir


def test_blank_data_dir_uses_default(tmp_path):
    cfg = _load_config(tmp_path, {"data_dir": "", "stt": {"provider": "disabled"}})
    # Blank data_dir falls back to "." (the config home), not a nested data/ subdir.
    assert cfg.data_dir == tmp_path


def test_stt_deepgram_defaults_language_and_options(tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text(
        "# tech terms\nKubernetes\n\nRuby on Rails\n",
        encoding="utf-8",
    )
    cfg = _load_config(
        tmp_path,
        _deepgram_data({"keyterms_enabled": True, "keyterms_file": str(keyterms)}),
        validate_providers=True,
    )

    assert cfg.stt_language == "ru"
    assert cfg.deepgram_model == "nova-3"
    assert cfg.deepgram_diarize_model == "latest"
    assert cfg.deepgram_audio_source == "m4a_copy"
    assert cfg.deepgram_txt_formatter == "word_speaker"
    assert cfg.deepgram_keyterms_enabled is True
    assert cfg.deepgram_keyterms_file == keyterms
    assert cfg.deepgram_keyterms == ("Kubernetes", "Ruby on Rails")


def test_stt_deepgram_with_api_key(tmp_path):
    cfg = _load_config(
        tmp_path,
        _deepgram_data({"api_key": "  dg-test  "}, stt_extra={"language": "ru"}),
        validate_providers=True,
    )
    assert cfg.stt_provider == "deepgram"
    assert cfg.stt_language == "ru"
    assert cfg.deepgram_api_key == "dg-test"


def test_stt_deepgram_accepts_custom_options(tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("React\nTypeScript\n", encoding="utf-8")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {
                "model": "nova-2",
                "diarize_model": "v1",
                "audio_source": "mp3_96k",
                "txt_formatter": "utterance",
                "keyterms_enabled": True,
                "keyterms_file": str(keyterms),
            },
            stt_extra={"language": "multi"},
        ),
        validate_providers=True,
    )

    assert cfg.stt_language == "multi"
    assert cfg.deepgram_model == "nova-2"
    assert cfg.deepgram_diarize_model == "v1"
    assert cfg.deepgram_audio_source == "mp3_96k"
    assert cfg.deepgram_txt_formatter == "utterance"
    assert cfg.deepgram_keyterms == ("React", "TypeScript")


def test_stt_deepgram_accepts_mp3_192k_audio_source(tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("React\n", encoding="utf-8")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {"audio_source": "mp3_192k", "keyterms_enabled": True, "keyterms_file": str(keyterms)}
        ),
        validate_providers=True,
    )

    assert cfg.deepgram_audio_source == "mp3_192k"


def test_stt_deepgram_rejects_invalid_options(tmp_path):
    config_file = tmp_path / "config.yml"

    _write_yaml(config_file, _deepgram_data({"diarize_model": "bad"}))
    with pytest.raises(ValueError, match="diarize_model"):
        load_config(config_path=config_file)

    _write_yaml(config_file, _deepgram_data({"audio_source": "wav"}))
    with pytest.raises(ValueError, match="audio_source"):
        load_config(config_path=config_file)

    _write_yaml(config_file, _deepgram_data({"txt_formatter": "plain"}))
    with pytest.raises(ValueError, match="txt_formatter"):
        load_config(config_path=config_file)


def test_stt_deepgram_can_disable_keyterms(tmp_path):
    cfg = _load_config(
        tmp_path,
        _deepgram_data({"keyterms_enabled": False, "keyterms_file": "missing.txt"}),
        validate_providers=True,
    )

    assert cfg.deepgram_keyterms_enabled is False
    assert cfg.deepgram_keyterms == ()


def test_stt_deepgram_rejects_missing_keyterms_file_when_enabled(tmp_path):
    missing_keyterms = tmp_path / "missing-keyterms.txt"
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        _deepgram_data({"keyterms_enabled": True, "keyterms_file": str(missing_keyterms)}),
    )

    with pytest.raises(ValueError, match="could not be read"):
        load_config(config_path=config_file)


def test_stt_deepgram_rejects_too_many_keyterms(tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("\n".join(f"term-{i}" for i in range(101)), encoding="utf-8")
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        _deepgram_data({"keyterms_enabled": True, "keyterms_file": str(keyterms)}),
    )

    with pytest.raises(ValueError, match="100"):
        load_config(config_path=config_file)


def test_stt_deepgram_api_key_wins_over_api_key_file(tmp_path):
    key_file = tmp_path / "deepgram.json"
    key_file.write_text("file-key", encoding="utf-8")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {"api_key": "env-key", "api_key_file": str(key_file)},
            stt_extra={"language": "ru"},
        ),
        validate_providers=True,
    )

    assert cfg.deepgram_api_key == "env-key"


def test_stt_deepgram_reads_raw_key_file(tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text("  raw-file-key  \n", encoding="utf-8")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {"api_key": "", "api_key_file": str(key_file)}, stt_extra={"language": "ru"}
        ),
        validate_providers=True,
    )

    assert cfg.deepgram_api_key == "raw-file-key"


def test_stt_deepgram_reads_raw_key_file_with_utf8_bom(tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text("raw-file-key\n", encoding="utf-8-sig")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {"api_key": "", "api_key_file": str(key_file)}, stt_extra={"language": "ru"}
        ),
        validate_providers=True,
    )

    assert cfg.deepgram_api_key == "raw-file-key"


def test_stt_deepgram_reads_json_key_file(tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text('{"deepgram_api_key": "json-file-key"}', encoding="utf-8")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {"api_key": "", "api_key_file": str(key_file)}, stt_extra={"language": "ru"}
        ),
        validate_providers=True,
    )

    assert cfg.deepgram_api_key == "json-file-key"


def test_stt_deepgram_reads_json_key_file_with_utf8_bom(tmp_path):
    key_file = tmp_path / "deepgram_api_secret.json"
    key_file.write_text('{"deepgram_api_key": "json-file-key"}', encoding="utf-8-sig")
    cfg = _load_config(
        tmp_path,
        _deepgram_data(
            {"api_key": "", "api_key_file": str(key_file)}, stt_extra={"language": "ru"}
        ),
        validate_providers=True,
    )

    assert cfg.deepgram_api_key == "json-file-key"


def test_stt_deepgram_reads_keyterms_file_with_utf8_bom(tmp_path):
    keyterms = tmp_path / "keyterms.txt"
    keyterms.write_text("# header\nKubernetes\n", encoding="utf-8-sig")
    cfg = _load_config(
        tmp_path,
        _deepgram_data({"keyterms_enabled": True, "keyterms_file": str(keyterms)}),
        validate_providers=True,
    )

    assert cfg.deepgram_keyterms == ("Kubernetes",)


def test_stt_deepgram_key_file_is_ignored_when_transcription_disabled(tmp_path):
    missing_key_file = tmp_path / "missing.json"
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled", "deepgram": {"api_key_file": str(missing_key_file)}},
            "presets": {"keypoints": {"enabled": False}},
        },
        validate_providers=True,
    )

    assert cfg.stt_provider == ""
    assert cfg.deepgram_api_key == ""


def test_stt_unsupported_provider_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"stt": {"provider": "azure"}})
    with pytest.raises(ValueError, match="stt.provider"):
        load_config(config_path=config_file, validate_providers=False)


def test_full_yaml_combination(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "folder_ids": "f1,f2",
            "poll_interval": 300,
            "bitrate": "192k",
            "data_dir": "mydata",
            "stt": {"provider": "disabled"},
        },
    )
    assert cfg.folder_ids == ["f1", "f2"]
    assert cfg.poll_interval == 300
    assert cfg.bitrate == "192k"
    assert cfg.data_dir == tmp_path / "mydata"


# --- config.yml loading -----------------------------------------------------


def test_loads_grouped_yaml(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "folder_ids": ["abc", "def"],
            "poll_interval": 300,
            "bitrate": "128k",
            "data_dir": "mydata",
            "proxy_url": "socks5://proxy:9050",
            "output": {"target": "drive", "dir": None},
            "stt": {
                "provider": "deepgram",
                "language": "ru",
                "postprocess": False,
                "deepgram": {
                    "api_key": "dg-yaml",
                    "model": "nova-2",
                    "keyterms_enabled": False,
                },
            },
            "openai": {"api_key": "sk-yaml", "model": "gpt-5.4", "batch": True},
        },
    )

    cfg = load_config(config_path=config_file)

    assert cfg.folder_ids == ["abc", "def"]
    assert cfg.poll_interval == 300
    assert cfg.bitrate == "128k"
    assert cfg.data_dir == tmp_path / "mydata"
    assert cfg.proxy_url == "socks5://proxy:9050"
    assert cfg.stt_provider == "deepgram"
    assert cfg.deepgram_api_key == "dg-yaml"
    assert cfg.deepgram_model == "nova-2"
    assert cfg.stt_postprocess is False
    assert cfg.openai_api_key == "sk-yaml"
    assert cfg.openai_model == "gpt-5.4"
    assert cfg.openai_batch is True


def test_openai_batch_wait_defaults_true(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "openai": {"api_key": "sk", "model": "gpt-5.4"},
            "presets": {"keypoints": {"enabled": False}},
        },
    )
    cfg = load_config(config_path=config_file)
    assert cfg.openai_batch_wait is True


def test_openai_batch_wait_parsed_from_yaml(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "openai": {"api_key": "sk", "batch_wait": False},
            "presets": {"keypoints": {"enabled": False}},
        },
    )
    cfg = load_config(config_path=config_file)
    assert cfg.openai_batch_wait is False


def test_generated_yaml_omits_batch_wait_by_default(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "openai": {"api_key": "sk"},
            "presets": {"keypoints": {"enabled": False}},
        },
    )
    cfg = load_config(config_path=config_file)
    serialized = _config_to_yaml_dict(cfg, config_file)
    # batch_wait defaults to true; the serialized YAML omits it (no hidden setting).
    assert "batch_wait" not in serialized["openai"]


def test_generated_yaml_emits_batch_wait_when_disabled(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "openai": {"api_key": "sk", "batch_wait": False},
            "presets": {"keypoints": {"enabled": False}},
        },
    )
    cfg = load_config(config_path=config_file)
    serialized = _config_to_yaml_dict(cfg, config_file)
    assert serialized["openai"]["batch_wait"] is False


def test_yaml_disabled_provider_is_mp3_only(tmp_path):
    config_file = tmp_path / "config.yml"
    # Disabling the built-in keypoints preset keeps this a pure mp3-only setup; with
    # any enabled preset, an openai.api_key would be required.
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "presets": {"keypoints": {"enabled": False}}},
    )

    cfg = load_config(config_path=config_file)

    assert cfg.stt_provider == ""
    assert cfg.stt_language == ""


def test_yaml_deepgram_requires_api_key(tmp_path):
    config_file = tmp_path / "config.yml"
    # Supply an openai.api_key so the preset gate passes and the deepgram-specific
    # validation is what surfaces.
    _write_yaml(
        config_file,
        {"stt": {"provider": "deepgram"}, "openai": {"api_key": "sk-test"}},
    )

    with pytest.raises(ValueError, match="deepgram.api_key is required"):
        load_config(config_path=config_file)


def test_yaml_enabled_preset_requires_openai_api_key(tmp_path):
    config_file = tmp_path / "config.yml"
    # A preset enabled via the presets map (not the legacy openai.keypoints flag) must
    # still require an openai.api_key at load time.
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "keypoints": {"enabled": False},
                "summary": {"instructions": "summarize"},
            },
        },
    )

    with pytest.raises(ValueError, match="openai.api_key is required"):
        load_config(config_path=config_file)


def test_yaml_openai_keypoints_requires_api_key(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "openai": {"keypoints": True}},
    )

    with pytest.raises(ValueError, match="openai.api_key is required"):
        load_config(config_path=config_file)


def test_yaml_invalid_output_target_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"stt": {"provider": "disabled"}, "output": {"target": "s3"}})

    with pytest.raises(ValueError, match="output.target"):
        load_config(config_path=config_file)


def test_yaml_folder_target_requires_dir(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "output": {"target": "folder"}},
    )

    with pytest.raises(ValueError, match="output.dir is required"):
        load_config(config_path=config_file)


def test_yaml_rejects_non_mapping_document(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a YAML mapping"):
        load_config(config_path=config_file)


def test_yaml_validate_providers_false_skips_secrets(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "deepgram"}, "openai": {"keypoints": True}},
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert cfg.stt_provider == "deepgram"
    assert cfg.openai_keypoints is True
    assert cfg.deepgram_api_key == ""


# --- resolver semantics -----------------------------------------------------


def test_resolve_config_file_path_defaults_to_cwd_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GDSTT_HOME", raising=False)

    assert resolve_config_file_path() == Path("data") / CONFIG_FILE_NAME


def test_resolve_config_file_path_honors_gdstt_home(monkeypatch, tmp_path):
    home = tmp_path / "instance"
    monkeypatch.setenv("GDSTT_HOME", str(home))

    assert resolve_config_file_path() == home / CONFIG_FILE_NAME


def test_resolve_config_file_path_expands_home_and_envvars(monkeypatch, tmp_path):
    root = tmp_path / "root"
    monkeypatch.setenv("GDSTT_ROOT", str(root))
    monkeypatch.setenv("GDSTT_HOME", "$GDSTT_ROOT/instance")

    assert resolve_config_file_path() == root / "instance" / CONFIG_FILE_NAME


def test_resolve_config_file_path_prefers_explicit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GDSTT_HOME", str(tmp_path / "instance"))
    explicit = tmp_path / "custom.yml"

    assert resolve_config_file_path(explicit) == explicit


# --- missing / empty config -------------------------------------------------


def test_load_config_missing_file_tells_operator_to_init(tmp_path):
    config_file = tmp_path / "missing.yml"

    with pytest.raises(ValueError, match="gdstt config init"):
        load_config(config_path=config_file, validate_providers=False)

    assert not config_file.exists()


def test_load_config_empty_file_tells_operator_to_init(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("  \n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        load_config(config_path=config_file, validate_providers=False)


def test_config_set_missing_config_requires_init(tmp_path):
    with pytest.raises(ValueError, match="gdstt config init"):
        config_set("run.enabled", "false", config_path=tmp_path / "missing.yml")


def test_config_get_missing_config_requires_init(tmp_path):
    with pytest.raises(ValueError, match="gdstt config init"):
        config_get(config_path=tmp_path / "missing.yml")


def test_config_to_yaml_dict_round_trips(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "folder_ids": "rt1",
            "stt": {"provider": "disabled"},
            "openai": {"api_key": "sk-rt", "keypoints": True},
        },
        validate_providers=False,
    )

    data = _config_to_yaml_dict(cfg)
    config_file = tmp_path / "roundtrip.yml"
    _write_yaml(config_file, data)
    reloaded = load_config(config_path=config_file, validate_providers=False)

    assert reloaded.folder_ids == ["rt1"]
    assert reloaded.openai_api_key == "sk-rt"
    assert reloaded.openai_keypoints is True
    assert reloaded.stt_provider == ""


# --- presets DAG wiring -----------------------------------------------------


def test_yaml_presets_merge_over_builtins(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "transcript-cleanup": {"instructions": "clean it"},
                "keypoints": {"depends_on": ["transcript-cleanup"]},
            },
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    by_name = {p.name: p for p in cfg.presets}
    assert set(by_name) == {"keypoints", "transcript-cleanup"}
    assert by_name["keypoints"].depends_on == ("transcript-cleanup",)
    # The built-in instructions are preserved when only depends_on is overridden.
    assert by_name["keypoints"].artifact_suffix == ".keypoints.md"


def test_yaml_presets_default_to_builtins_when_absent(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"stt": {"provider": "disabled"}})

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert {p.name for p in cfg.presets} == {"keypoints"}


def test_yaml_presets_can_disable_builtin(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "presets": {"keypoints": {"enabled": False}}},
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert cfg.presets == ()


def test_yaml_presets_invalid_dag_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"summary": {"instructions": "s", "depends_on": ["ghost"]}},
        },
    )

    with pytest.raises(ValueError, match="unknown or disabled"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_preset_prompt_file_resolves_relative_to_config(tmp_path):
    config_file = tmp_path / "config.yml"
    (tmp_path / "managers.md").write_text("manager prompt body", encoding="utf-8")
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"managers": {"prompt_file": "managers.md"}},
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    by_name = {p.name: p for p in cfg.presets}
    assert by_name["managers"].instructions == "manager prompt body"
    assert by_name["managers"].prompt_file == "managers.md"


def test_yaml_preset_prompt_file_absolute_path(tmp_path):
    config_file = tmp_path / "config.yml"
    prompt = tmp_path / "elsewhere" / "p.md"
    prompt.parent.mkdir()
    prompt.write_text("absolute prompt", encoding="utf-8")
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"abs": {"prompt_file": str(prompt)}},
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    by_name = {p.name: p for p in cfg.presets}
    assert by_name["abs"].instructions == "absolute prompt"


def test_yaml_preset_prompt_file_falls_back_to_packaged_asset(tmp_path):
    config_file = tmp_path / "config.yml"
    # The file exists only as a packaged asset, not next to the config.
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"extra": {"prompt_file": "keypoints.md"}},
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    by_name = {p.name: p for p in cfg.presets}
    assert "## Задачи" in by_name["extra"].instructions


def test_yaml_preset_instructions_win_over_prompt_file(tmp_path):
    config_file = tmp_path / "config.yml"
    (tmp_path / "managers.md").write_text("from file", encoding="utf-8")
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "managers": {"instructions": "inline wins", "prompt_file": "managers.md"}
            },
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    by_name = {p.name: p for p in cfg.presets}
    assert by_name["managers"].instructions == "inline wins"


def test_yaml_preset_empty_prompt_file_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    (tmp_path / "blank.md").write_text("   \n", encoding="utf-8")
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"blank": {"prompt_file": "blank.md"}},
        },
    )

    with pytest.raises(ValueError, match="empty"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_preset_missing_prompt_file_raises(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"gone": {"prompt_file": "no-such-prompt.md"}},
        },
    )

    with pytest.raises(ValueError, match="could not be resolved"):
        load_config(config_path=config_file, validate_providers=False)


# --- strict YAML validation -------------------------------------------------


def test_yaml_rejects_duplicate_top_level_key(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "stt:\n  provider: disabled\nbitrate: 96k\nbitrate: 128k\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_rejects_duplicate_preset_key(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "stt:\n"
        "  provider: disabled\n"
        "presets:\n"
        "  managers:\n"
        "    instructions: first\n"
        "  managers:\n"
        "    instructions: second\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_rejects_duplicate_artifact_suffix_among_enabled(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "first": {"instructions": "a", "artifact_suffix": ".shared.md"},
                "second": {"instructions": "b", "artifact_suffix": ".shared.md"},
            },
        },
    )

    with pytest.raises(ValueError, match="artifact_suffix"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_duplicate_artifact_suffix_ignored_when_disabled(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "first": {"instructions": "a", "artifact_suffix": ".shared.md"},
                "second": {
                    "instructions": "b",
                    "artifact_suffix": ".shared.md",
                    "enabled": False,
                },
            },
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert {p.name for p in cfg.presets} == {"keypoints", "first"}


def test_yaml_shared_prompt_file_distinct_names_and_suffixes_allowed(tmp_path):
    config_file = tmp_path / "config.yml"
    (tmp_path / "shared.md").write_text("shared prompt body", encoding="utf-8")
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "first": {"prompt_file": "shared.md", "artifact_suffix": ".one.md"},
                "second": {"prompt_file": "shared.md", "artifact_suffix": ".two.md"},
            },
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    by_name = {p.name: p for p in cfg.presets}
    assert by_name["first"].instructions == "shared prompt body"
    assert by_name["second"].instructions == "shared prompt body"
    assert by_name["first"].artifact_suffix == ".one.md"
    assert by_name["second"].artifact_suffix == ".two.md"


# --- openai.max_parallel ----------------------------------------------------


def test_max_parallel_defaults_to_four(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"stt": {"provider": "disabled"}})

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert cfg.openai_max_parallel == 4


def test_yaml_max_parallel_is_read(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "openai": {"max_parallel": 8}},
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert cfg.openai_max_parallel == 8


def test_yaml_max_parallel_must_be_positive(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "openai": {"max_parallel": 0}},
    )

    with pytest.raises(ValueError, match="max_parallel must be positive"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_max_parallel_rejects_bool(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "openai": {"max_parallel": True}},
    )

    with pytest.raises(ValueError, match="max_parallel must be an integer"):
        load_config(config_path=config_file, validate_providers=False)


def test_yaml_max_parallel_rejects_non_integer_float(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "openai": {"max_parallel": 2.9}},
    )

    with pytest.raises(ValueError, match="max_parallel must be an integer"):
        load_config(config_path=config_file, validate_providers=False)


def test_max_parallel_round_trips(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "openai": {"max_parallel": 9}},
    )
    cfg = load_config(config_path=config_file, validate_providers=False)

    data = _config_to_yaml_dict(cfg)
    out_file = tmp_path / "out.yml"
    _write_yaml(out_file, data)
    reloaded = load_config(config_path=out_file, validate_providers=False)

    assert reloaded.openai_max_parallel == 9


# --- prompt assets & config generation --------------------------------------


def test_copy_prompt_assets_copies_all_packaged_prompts(tmp_path):
    target = tmp_path / "prompts"
    written = copy_prompt_assets(target)

    assert {p.name for p in written} == set(PACKAGED_PROMPT_ASSETS)
    for name in PACKAGED_PROMPT_ASSETS:
        assert (target / name).read_text(encoding="utf-8").strip()


def test_copy_prompt_assets_skips_existing_without_overwrite(tmp_path):
    target = tmp_path / "prompts"
    target.mkdir()
    (target / "keypoints.md").write_text("custom", encoding="utf-8")

    written = copy_prompt_assets(target)

    assert target / "keypoints.md" not in written
    assert (target / "keypoints.md").read_text(encoding="utf-8") == "custom"


def test_init_creates_config_with_prompts_and_relative_paths(tmp_path):
    config_file = tmp_path / "config.yml"

    path = init_config(config_path=config_file)

    assert path == config_file
    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["data_dir"] == "."
    assert data["stt"]["deepgram"]["keyterms_file"] == "config/deepgram-keyterms.txt"
    assert data["presets"]["keypoints"]["enabled"] is True
    assert data["presets"]["keypoints"]["prompt_file"] == "prompts/keypoints.md"
    # The full default chain is enabled out of the box, with transcript-cleanup
    # written above keypoints and both downstream presets depending on it.
    assert list(data["presets"]) == ["transcript-cleanup", "keypoints", "action-items"]
    assert [name for name, p in data["presets"].items() if p["enabled"]] == [
        "transcript-cleanup",
        "keypoints",
        "action-items",
    ]
    assert data["presets"]["keypoints"]["depends_on"] == ["transcript-cleanup"]
    assert data["presets"]["action-items"]["depends_on"] == ["transcript-cleanup"]
    # Batch and the run flag are on by default in a generated config.
    assert data["openai"]["batch"] is True
    assert data["run"]["enabled"] is True
    # The packaged prompt assets are copied beside the config.
    assert (tmp_path / "prompts" / "keypoints.md").is_file()
    assert (tmp_path / "prompts" / "transcript-cleanup.md").is_file()
    assert (tmp_path / "prompts" / "action-items.md").is_file()
    assert (tmp_path / "config" / "deepgram-keyterms.txt").is_file()
    # The generated config loads back without provider secrets and yields the chain.
    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.data_dir == tmp_path
    assert {p.name for p in cfg.presets} == {
        "transcript-cleanup",
        "keypoints",
        "action-items",
    }


def test_init_config_validates_with_copied_keyterms(tmp_path):
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)
    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    data["stt"]["deepgram"]["api_key"] = "dg-test"
    data["openai"]["api_key"] = "sk-test"
    _write_yaml(config_file, data)

    cfg = load_config(config_path=config_file)

    assert cfg.deepgram_keyterms_file == tmp_path / "config" / "deepgram-keyterms.txt"
    assert "Kubernetes" in cfg.deepgram_keyterms


def test_init_default_writes_to_gdstt_home(monkeypatch, tmp_path):
    home = tmp_path / "instance"
    monkeypatch.setenv("GDSTT_HOME", str(home))

    path = init_config()

    assert path == home / CONFIG_FILE_NAME
    assert path.is_file()
    assert (home / "prompts" / "keypoints.md").is_file()
    assert (home / "config" / "deepgram-keyterms.txt").is_file()


def test_init_default_falls_back_to_cwd_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GDSTT_HOME", raising=False)

    path = init_config()

    assert path == Path("data") / CONFIG_FILE_NAME
    assert (tmp_path / "data" / CONFIG_FILE_NAME).is_file()
    assert (tmp_path / "data" / "prompts" / "keypoints.md").is_file()


def test_init_explicit_config_path_overrides_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GDSTT_HOME", str(tmp_path / "instance"))
    explicit = tmp_path / "explicit" / "config.yml"

    assert init_config(config_path=explicit) == explicit
    assert explicit.is_file()


def test_init_output_dir_sets_folder_target(tmp_path):
    config_file = tmp_path / "config.yml"
    out = tmp_path / "artifacts"

    init_config(config_path=config_file, output_dir=out)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["output"]["target"] == "folder"
    assert data["output"]["dir"] == "artifacts"
    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.output_target == "folder"
    assert cfg.output_dir == out


def test_init_data_dir_writes_relative_path(tmp_path):
    config_file = tmp_path / "config.yml"

    init_config(config_path=config_file, data_dir=tmp_path / "store")

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["data_dir"] == "store"


def test_init_prompt_dir_points_prompt_files(tmp_path):
    config_file = tmp_path / "config.yml"
    prompt_dir = tmp_path / "shared-prompts"

    init_config(config_path=config_file, prompt_dir=prompt_dir)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["presets"]["keypoints"]["prompt_file"] == "shared-prompts/keypoints.md"
    assert (prompt_dir / "keypoints.md").is_file()


def test_init_refuses_existing_without_force(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("folder_ids: [keep]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        init_config(config_path=config_file)

    assert config_file.read_text(encoding="utf-8") == "folder_ids: [keep]\n"


def test_init_force_overwrites(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("folder_ids: [stale]\n", encoding="utf-8")

    init_config(config_path=config_file, force=True)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["folder_ids"] == []


# --- config get / set / unset -----------------------------------------------


def _base_config_file(tmp_path: Path) -> Path:
    """Create a valid full config (provider disabled) for get/set/unset tests."""
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)
    # The default init writes provider=deepgram; disable it so loads don't require
    # a Deepgram key and so openai.api_key isn't required (keypoints stays enabled).
    config_set("stt.provider", "disabled", config_path=config_file)
    config_set("openai.api_key", "sk-base", config_path=config_file)
    return config_file


def test_config_set_openai_api_key_and_model(tmp_path):
    config_file = _base_config_file(tmp_path)

    config_set("openai.api_key", "sk-new", config_path=config_file)
    config_set("openai.model", "gpt-5.4", config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["openai"]["api_key"] == "sk-new"
    assert data["openai"]["model"] == "gpt-5.4"
    assert config_get("openai.model", config_path=config_file) == "gpt-5.4"


def test_config_set_output_dir_sets_folder_target(tmp_path):
    config_file = _base_config_file(tmp_path)

    config_set("output.dir", "out", config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["output"]["dir"] == "out"
    assert data["output"]["target"] == "folder"


def test_config_set_output_drive_true_sets_drive_target(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("output.dir", "out", config_path=config_file)

    config_set("output.drive", "true", config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["output"]["target"] == "drive"


def test_config_set_output_drive_false_requires_dir(tmp_path):
    config_file = _base_config_file(tmp_path)
    before = config_file.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="config set output.dir"):
        config_set("output.drive", "false", config_path=config_file)

    assert config_file.read_text(encoding="utf-8") == before


def test_config_set_preset_prompt_file(tmp_path):
    config_file = _base_config_file(tmp_path)

    config_set("presets.keypoints.prompt_file", "prompts/keypoints.md", config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["presets"]["keypoints"]["prompt_file"] == "prompts/keypoints.md"


def test_config_set_preset_depends_on_parses_list(tmp_path):
    config_file = _base_config_file(tmp_path)
    # Make transcript-cleanup a real enabled dependency target.
    config_set(
        "presets.transcript-cleanup.prompt_file",
        "prompts/transcript-cleanup.md",
        config_path=config_file,
    )
    config_set("presets.transcript-cleanup.enabled", "true", config_path=config_file)

    config_set(
        "presets.keypoints.depends_on", "transcript-cleanup", config_path=config_file
    )

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["presets"]["keypoints"]["depends_on"] == ["transcript-cleanup"]


def test_config_set_preset_depends_on_accepts_json_list(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set(
        "presets.transcript-cleanup.prompt_file",
        "prompts/transcript-cleanup.md",
        config_path=config_file,
    )
    config_set("presets.transcript-cleanup.enabled", "true", config_path=config_file)

    config_set(
        "presets.keypoints.depends_on",
        '["transcript-cleanup"]',
        config_path=config_file,
    )

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["presets"]["keypoints"]["depends_on"] == ["transcript-cleanup"]


def test_config_unset_removes_key(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("proxy_url", "http://proxy", config_path=config_file)

    config_unset("proxy_url", config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert "proxy_url" not in data


def test_config_unset_missing_key_raises(tmp_path):
    config_file = _base_config_file(tmp_path)

    with pytest.raises(ValueError, match="not set"):
        config_unset("nope.missing", config_path=config_file)


def test_config_get_whole_masks_secrets(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("openai.api_key", "sk-secret", config_path=config_file)

    output = config_get(config_path=config_file)

    assert "sk-secret" not in output
    assert "***" in output


def test_config_get_single_secret_is_masked_by_default(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("openai.api_key", "sk-secret", config_path=config_file)

    # A single-key get of a secret leaf must not leak the value to stdout/logs.
    assert config_get("openai.api_key", config_path=config_file) == "***"


def test_config_get_masks_telegram_bot_token(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set(
        "notifications.telegram.bot_token", "123456:SECRET", config_path=config_file
    )

    # The bot token grants full control of the bot and must be masked in both the
    # whole-config dump and a single-key lookup.
    whole = config_get(config_path=config_file)
    assert "123456:SECRET" not in whole
    assert "***" in whole
    assert (
        config_get("notifications.telegram.bot_token", config_path=config_file)
        == "***"
    )
    # chat_id is not a secret and stays visible.
    config_set("notifications.telegram.chat_id", "98765", config_path=config_file)
    assert (
        config_get("notifications.telegram.chat_id", config_path=config_file)
        == "98765"
    )


def test_config_get_single_secret_revealed_with_show_secrets(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("openai.api_key", "sk-secret", config_path=config_file)

    assert (
        config_get("openai.api_key", config_path=config_file, show_secrets=True)
        == "sk-secret"
    )


def test_config_get_single_nonsecret_is_plain(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("openai.model", "gpt-x", config_path=config_file)

    assert config_get("openai.model", config_path=config_file) == "gpt-x"


def test_config_get_missing_key_raises(tmp_path):
    config_file = _base_config_file(tmp_path)

    with pytest.raises(ValueError, match="not set"):
        config_get("openai.nope", config_path=config_file)


def test_config_set_invalid_leaves_file_unchanged(tmp_path):
    config_file = _base_config_file(tmp_path)
    before = config_file.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        config_set("output.target", "s3", config_path=config_file)

    assert config_file.read_text(encoding="utf-8") == before


# --- google auth config ------------------------------------------------------


def test_yaml_google_inline_credentials_and_token(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"keypoints": {"enabled": False}},
            "google": {
                "credentials": {"installed": {"client_id": "cid", "client_secret": "x"}},
                "token": {"token": "t", "refresh_token": "r"},
            },
        },
    )

    cfg = load_config(config_path=config_file)

    assert cfg.google_credentials == {
        "installed": {"client_id": "cid", "client_secret": "x"}
    }
    assert cfg.google_token == {"token": "t", "refresh_token": "r"}
    assert cfg.google_credentials_file is None
    assert cfg.google_token_file is None
    assert cfg.config_file == config_file


def test_yaml_google_file_paths_resolve_relative(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"keypoints": {"enabled": False}},
            "google": {
                "credentials_file": "secrets/creds.json",
                "token_file": "/abs/token.json",
            },
        },
    )

    cfg = load_config(config_path=config_file)

    assert cfg.google_credentials is None
    assert cfg.google_credentials_file == tmp_path / "secrets" / "creds.json"
    expected_token = Path("/abs/token.json")
    if not expected_token.is_absolute():
        expected_token = config_file.parent / expected_token
    assert cfg.google_token_file == expected_token


def test_yaml_google_back_compat_data_dir_fallback(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "presets": {"keypoints": {"enabled": False}}},
    )

    cfg = load_config(config_path=config_file)

    assert cfg.google_credentials is None
    assert cfg.google_token is None
    assert cfg.google_credentials_file is None
    assert cfg.google_token_file is None


def test_yaml_google_credentials_both_inline_and_file_fails(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"keypoints": {"enabled": False}},
            "google": {
                "credentials": {"installed": {"client_id": "cid"}},
                "credentials_file": "creds.json",
            },
        },
    )

    with pytest.raises(ValueError, match="both set"):
        load_config(config_path=config_file)


def test_yaml_google_token_both_inline_and_file_fails(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "presets": {"keypoints": {"enabled": False}},
            "google": {
                "token": {"token": "t"},
                "token_file": "token.json",
            },
        },
    )

    with pytest.raises(ValueError, match="both set"):
        load_config(config_path=config_file)


def test_import_google_credentials_writes_inline(tmp_path):
    config_file = _base_config_file(tmp_path)
    creds_src = tmp_path / "download.json"
    creds_src.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "sek"}})
    )

    import_google_credentials(creds_src, config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["google"]["credentials"] == {
        "installed": {"client_id": "cid", "client_secret": "sek"}
    }
    assert "credentials_file" not in data["google"]


def test_import_google_credentials_clears_file_pointer(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("google.credentials_file", "old/creds.json", config_path=config_file)
    creds_src = tmp_path / "download.json"
    creds_src.write_text(json.dumps({"installed": {"client_id": "cid"}}))

    import_google_credentials(creds_src, config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert "credentials_file" not in data["google"]
    assert data["google"]["credentials"] == {"installed": {"client_id": "cid"}}


def test_use_google_files_switches_and_clears_inline(tmp_path):
    config_file = _base_config_file(tmp_path)
    creds_src = tmp_path / "download.json"
    creds_src.write_text(json.dumps({"installed": {"client_id": "cid"}}))
    import_google_credentials(creds_src, config_path=config_file)
    config_set("google.token.token", "inline-tok", config_path=config_file)

    creds_file = tmp_path / "creds" / "client.json"
    use_google_files(creds_file, config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert "credentials" not in data["google"]
    assert "token" not in data["google"]
    assert data["google"]["credentials_file"] == "creds/client.json"
    assert data["google"]["token_file"] == "creds/token.json"


def test_use_google_files_resolves_cli_relative_paths_before_serializing(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    data_dir = app_dir / "data"
    config_file = data_dir / "config.yml"
    init_config(config_path=config_file)
    (data_dir / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(app_dir)

    use_google_files("data/credentials.json", config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["google"]["credentials_file"] == "credentials.json"
    assert data["google"]["token_file"] == "token.json"
    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.google_credentials_file == data_dir / "credentials.json"
    assert cfg.google_token_file == data_dir / "token.json"


def test_use_google_files_honors_explicit_token_file(tmp_path):
    config_file = _base_config_file(tmp_path)
    creds_file = tmp_path / "client.json"
    token_file = tmp_path / "elsewhere" / "tok.json"

    use_google_files(creds_file, token_file=token_file, config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["google"]["token_file"] == "elsewhere/tok.json"


def test_config_get_masks_inline_google_secrets(tmp_path):
    config_file = _base_config_file(tmp_path)
    creds_src = tmp_path / "download.json"
    creds_src.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "sup3rsecret"}})
    )
    import_google_credentials(creds_src, config_path=config_file)
    config_set("google.token.refresh_token", "rt-secret", config_path=config_file)
    config_set("google.token.token", "tok-secret", config_path=config_file)

    output = config_get(config_path=config_file)

    assert "sup3rsecret" not in output
    assert "rt-secret" not in output
    assert "tok-secret" not in output
    assert "***" in output


def test_generated_config_prefers_inline_and_omits_file_keys(tmp_path):
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    # The generated config ships an (empty) google block without file pointers.
    assert "google" in data
    assert "credentials_file" not in data["google"]
    assert "token_file" not in data["google"]


# --- run.enabled (gdstt run/stop) -------------------------------------------

def test_run_enabled_defaults_true_and_round_trips(tmp_path):
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)

    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.run_enabled is True
    assert is_run_enabled(config_path=config_file) is True
    # Serialization keeps the flag.
    assert _config_to_yaml_dict(cfg, config_file)["run"]["enabled"] is True


def test_set_run_enabled_false_is_observed(tmp_path):
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)

    path = set_run_enabled(False, config_path=config_file)
    assert path == config_file
    assert is_run_enabled(config_path=config_file) is False
    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.run_enabled is False

    set_run_enabled(True, config_path=config_file)
    assert is_run_enabled(config_path=config_file) is True


def test_is_run_enabled_missing_file_defaults_true(tmp_path):
    # No config file at all -> default True (never stop a healthy loop on a hiccup).
    assert is_run_enabled(config_path=tmp_path / "absent.yml") is True


# --- telegram notification settings -----------------------------------------


def test_telegram_notification_defaults_blank(tmp_path):
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)

    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.telegram_bot_token == ""
    assert cfg.telegram_chat_id == ""


def test_telegram_notification_parsed_from_config(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "notifications": {
                "telegram": {"bot_token": "token-abc", "chat_id": "98765"},
            },
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.telegram_bot_token == "token-abc"
    assert cfg.telegram_chat_id == "98765"


def test_telegram_notification_round_trips(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "notifications": {
                "telegram": {"bot_token": "token-abc", "chat_id": "98765"},
            },
        },
    )
    cfg = load_config(config_path=config_file, validate_providers=False)

    data = _config_to_yaml_dict(cfg, config_file)
    assert data["notifications"]["telegram"] == {
        "bot_token": "token-abc",
        "chat_id": "98765",
    }


# --- field-test regression fixes --------------------------------------------

def test_config_prompt_file_overrides_builtin_keypoints_instructions(tmp_path):
    """An explicit prompt_file on the keypoints preset must win over the built-in
    inline instructions (field bug: edits to prompts/keypoints.md were ignored)."""
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)
    custom = "CUSTOM KEYPOINTS PROMPT BODY (field edit)"
    (tmp_path / "prompts" / "keypoints.md").write_text(custom, encoding="utf-8")

    cfg = load_config(config_path=config_file, validate_providers=False)
    kp = next(p for p in cfg.presets if p.name == "keypoints")
    assert kp.instructions.strip() == custom


def test_fresh_config_is_owner_only(tmp_path):
    import stat

    if os.name == "nt":
        pytest.skip("Windows does not expose POSIX owner-only mode bits reliably")
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)  # brand-new file
    mode = stat.S_IMODE(config_file.stat().st_mode)
    assert mode == 0o600
