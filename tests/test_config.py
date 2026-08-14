import json
import logging
import os
from pathlib import Path

import pytest
import yaml

from src.config import (
    CONFIG_FILE_NAME,
    EmployeeFolder,
    _config_to_yaml_dict,
    _default_config_dict,
    config_get,
    config_set,
    config_unset,
    copy_prompt_assets,
    import_google_credentials,
    init_config,
    load_packaged_keyterms,
    is_run_enabled,
    load_config,
    set_run_enabled,
    resolve_config_file_path,
    use_google_files,
)
from src.presets import BUILTIN_PRESETS, PACKAGED_PROMPT_ASSETS

def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _load_config(tmp_path, data, *, validate_providers=False):
    """Write ``data`` to ``<tmp_path>/config.yml`` and load it via the file override."""
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, data)
    return load_config(config_path=config_file, validate_providers=validate_providers)


def write_config(tmp_path, mapping):
    """Write ``mapping`` to ``<tmp_path>/config.yml`` and return its path."""
    path = tmp_path / "config.yml"
    _write_yaml(path, mapping)
    return path


# Only the enabled built-ins: `merge_presets` drops disabled ones, so opt-in
# built-ins like `meta` are absent from a loaded config until it turns them on.
_BUILTIN_NAMES = {preset.name for preset in BUILTIN_PRESETS if preset.enabled}


def _disabled_builtins():
    """Disable every built-in preset.

    Derived from the registry rather than spelled out, so adding a built-in doesn't
    silently re-enable OpenAI in the deepgram-only tests (which carry no
    openai.api_key and would fail provider validation).
    """
    return {preset.name: {"enabled": False} for preset in BUILTIN_PRESETS}


def _deepgram_data(deepgram=None, *, stt_extra=None):
    """Build a deepgram-only config dict (built-in presets disabled, keyterms off)."""
    dg = {"api_key": "dg-test", "keyterms_enabled": False}
    if deepgram:
        dg.update(deepgram)
    stt = {"provider": "deepgram", "deepgram": dg}
    if stt_extra:
        stt.update(stt_extra)
    return {"stt": stt, "presets": _disabled_builtins()}


def test_defaults_when_config_minimal(tmp_path):
    cfg = _load_config(tmp_path, {"folders": []})
    assert cfg.folders == ()
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


def _folders_config(folders, **extra):
    return {"folders": folders, "stt": {"provider": "disabled"}, **extra}


def test_parses_single_folder(tmp_path):
    cfg = _load_config(
        tmp_path,
        _folders_config(
            [{"folder_id": "abc123", "name": "Олег Иванов", "email": "oleg@example.org"}]
        ),
    )
    assert cfg.folders == (
        EmployeeFolder(folder_id="abc123", name="Олег Иванов", email="oleg@example.org"),
    )


def test_parses_multiple_folders(tmp_path):
    cfg = _load_config(
        tmp_path,
        _folders_config(
            [
                {"folder_id": "id1", "name": "One", "email": "one@example.org"},
                {"folder_id": "id2", "name": "Two", "email": "two@example.org"},
            ]
        ),
    )
    assert [f.folder_id for f in cfg.folders] == ["id1", "id2"]
    assert [f.name for f in cfg.folders] == ["One", "Two"]


def test_folder_name_and_email_default_to_blank(tmp_path):
    cfg = _load_config(tmp_path, _folders_config([{"folder_id": "id1"}]))
    assert cfg.folders == (EmployeeFolder(folder_id="id1", name="", email=""),)


def test_strips_whitespace_in_folder_fields(tmp_path):
    cfg = _load_config(
        tmp_path,
        _folders_config([{"folder_id": " id1 ", "name": " One ", "email": " one@x.org "}]),
    )
    assert cfg.folders == (
        EmployeeFolder(folder_id="id1", name="One", email="one@x.org"),
    )


def test_missing_folders_key_returns_empty_tuple(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled"}})
    assert cfg.folders == ()


def test_bare_string_folder_entry_rejected(tmp_path):
    with pytest.raises(ValueError, match="folders"):
        _load_config(tmp_path, _folders_config(["abc123"]))


def test_folder_without_folder_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="folder_id"):
        _load_config(tmp_path, _folders_config([{"name": "No Id"}]))


def test_folder_with_blank_folder_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="folder_id"):
        _load_config(tmp_path, _folders_config([{"folder_id": "  "}]))


def test_folders_non_list_rejected(tmp_path):
    with pytest.raises(ValueError, match="folders"):
        _load_config(tmp_path, _folders_config("abc123"))


def test_legacy_folder_ids_raises_migration_error(tmp_path):
    with pytest.raises(ValueError, match="folder_ids is no longer supported") as exc:
        _load_config(tmp_path, {"folder_ids": ["abc"], "stt": {"provider": "disabled"}})
    # The message must show the operator the replacement shape.
    assert "folders:" in str(exc.value)
    assert "folder_id:" in str(exc.value)


def test_empty_folder_ids_list_still_raises_migration_error(tmp_path):
    # Present-but-empty is still a stale config: fail loudly rather than start with
    # no folders to poll.
    with pytest.raises(ValueError, match="folder_ids is no longer supported"):
        _load_config(tmp_path, {"folder_ids": [], "stt": {"provider": "disabled"}})


def test_folders_load_in_config_order(tmp_path):
    cfg = _load_config(
        tmp_path,
        _folders_config([{"folder_id": "id1"}, {"folder_id": "id2", "name": "Two"}]),
    )
    assert [f.folder_id for f in cfg.folders] == ["id1", "id2"]


def test_duplicate_folder_id_raises(tmp_path):
    with pytest.raises(ValueError, match="repeats folder_id"):
        _load_config(
            tmp_path,
            _folders_config(
                [
                    {"folder_id": "dup", "name": "First"},
                    {"folder_id": "dup", "name": "Second"},
                ]
            ),
        )


def test_folder_by_id_hit(tmp_path):
    cfg = _load_config(
        tmp_path,
        _folders_config([{"folder_id": "id1", "name": "One", "email": "one@x.org"}]),
    )
    found = cfg.folder_by_id("id1")
    assert found == EmployeeFolder(folder_id="id1", name="One", email="one@x.org")


def test_folder_by_id_miss_returns_none(tmp_path):
    cfg = _load_config(tmp_path, _folders_config([{"folder_id": "id1"}]))
    assert cfg.folder_by_id("nope") is None


# --- tags.allowed ------------------------------------------------------------


def test_tags_allowed_parses_into_tuple(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["клиентская-консультация", "O-1"]},
        },
    )
    assert cfg.tags_allowed == ("клиентская-консультация", "O-1")


def test_missing_tags_block_yields_empty_tuple(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled"}})
    assert cfg.tags_allowed == ()


def test_tags_allowed_strips_whitespace_and_drops_blanks(tmp_path):
    cfg = _load_config(
        tmp_path,
        {"stt": {"provider": "disabled"}, "tags": {"allowed": [" O-1 ", "", "  "]}},
    )
    assert cfg.tags_allowed == ("O-1",)


def test_tags_allowed_non_list_rejected(tmp_path):
    with pytest.raises(ValueError, match="tags.allowed"):
        _load_config(
            tmp_path, {"stt": {"provider": "disabled"}, "tags": {"allowed": "O-1"}}
        )


def test_tags_non_mapping_rejected(tmp_path):
    with pytest.raises(ValueError, match="tags"):
        _load_config(tmp_path, {"stt": {"provider": "disabled"}, "tags": ["O-1"]})


def test_config_to_yaml_dict_round_trips_tags_allowed(tmp_path):
    # Regression: `tags.allowed` used to be absent from the serializer, so any
    # whole-Config rewrite (`gdstt config set`, token refresh) silently dropped the
    # operator's tag list. The allow-list now lives inside the `tags` entity under
    # `meta.entities`, which is where the rewrite must carry it instead.
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["клиентская-консультация", "EB-1"]},
        },
    )

    data = _config_to_yaml_dict(cfg)
    tags_entity = next(e for e in data["meta"]["entities"] if e["name"] == "tags")
    assert tags_entity["allowed"] == ["клиентская-консультация", "EB-1"]

    config_file = tmp_path / "roundtrip-tags.yml"
    _write_yaml(config_file, data)
    reloaded = load_config(config_path=config_file, validate_providers=False)
    reloaded_tags_entity = next(e for e in reloaded.meta_entities if e.name == "tags")
    assert reloaded_tags_entity.allowed == ("клиентская-консультация", "EB-1")


# --- webhook -----------------------------------------------------------------


def test_webhook_parses_url_and_token(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "webhook": {"url": "https://example.com/hooks/gdstt", "token": "secret"},
        },
    )
    assert cfg.webhook_url == "https://example.com/hooks/gdstt"
    assert cfg.webhook_token == "secret"


def test_missing_webhook_block_yields_blank_fields(tmp_path):
    cfg = _load_config(tmp_path, {"stt": {"provider": "disabled"}})
    assert cfg.webhook_url == ""
    assert cfg.webhook_token == ""


def test_plaintext_webhook_url_warns(tmp_path, caplog):
    """An http:// receiver leaks the bearer token and the transcript PII."""
    with caplog.at_level(logging.WARNING, logger="src.config"):
        cfg = _load_config(
            tmp_path,
            {
                "stt": {"provider": "disabled"},
                "webhook": {"url": "http://example.com/hook", "token": "secret"},
            },
        )
    # Warn, don't raise: a plaintext receiver is unwise, not a config error.
    assert cfg.webhook_url == "http://example.com/hook"
    assert "clear text" in caplog.text
    # The warning must not itself leak the token.
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/hook",
        "http://localhost:8080/hook",
        "http://127.0.0.1:8080/hook",
        "",
    ],
)
def test_safe_webhook_url_does_not_warn(tmp_path, caplog, url):
    """https and loopback never cross the network in clear; neither does an unset URL."""
    with caplog.at_level(logging.WARNING, logger="src.config"):
        _load_config(
            tmp_path,
            {"stt": {"provider": "disabled"}, "webhook": {"url": url}},
        )
    assert "clear text" not in caplog.text


@pytest.mark.parametrize("url", ["example.com/hook", "ftp://example.com/hook", "https://"])
def test_undeliverable_webhook_url_rejected(tmp_path, url):
    """Delivery is fire-and-forget and swallows errors, so a typo must fail at load."""
    with pytest.raises(ValueError, match="absolute http"):
        _load_config(
            tmp_path, {"stt": {"provider": "disabled"}, "webhook": {"url": url}}
        )


def test_webhook_non_mapping_rejected(tmp_path):
    with pytest.raises(ValueError, match="webhook"):
        _load_config(
            tmp_path, {"stt": {"provider": "disabled"}, "webhook": ["https://x"]}
        )


def test_webhook_round_trips(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "webhook": {"url": "https://example.com/hooks/gdstt", "token": "secret"},
        },
    )

    data = _config_to_yaml_dict(cfg)
    assert data["webhook"] == {
        "url": "https://example.com/hooks/gdstt",
        "token": "secret",
    }

    config_file = tmp_path / "roundtrip-webhook.yml"
    _write_yaml(config_file, data)
    reloaded = load_config(config_path=config_file, validate_providers=False)
    assert reloaded.webhook_url == "https://example.com/hooks/gdstt"
    assert reloaded.webhook_token == "secret"


def test_default_config_dict_seeds_empty_webhook_block():
    assert _default_config_dict()["webhook"] == {"url": "", "token": ""}


def test_default_chain_keeps_meta_and_drops_action_items():
    presets = _default_config_dict()["presets"]
    assert presets["meta"]["enabled"] is True
    assert presets["meta"]["depends_on"] == ["transcript-cleanup"]
    assert "action-items" not in presets


def test_default_config_seeds_the_referral_allow_list():
    entities = _default_config_dict()["meta"]["entities"]
    referral = next(e for e in entities if e["name"] == "referral")
    assert "рекомендация" in referral["allowed"]


def test_default_config_writes_the_new_output_and_planfix_keys():
    data = _default_config_dict()
    assert data["output"]["stt_presets"] == ["keypoints"]
    assert data["planfix"]["meta_fields"] == [
        "subject", "tags", "referral", "referral_note",
        "case_deadline", "deadlines", "target_filing",
        "duration", "video_url",
    ]


# --- {{entities}} prompt rendering --------------------------------------------


def _preset_by_name(cfg, name):
    return next(p for p in cfg.presets if p.name == name)


def test_entities_placeholder_rendered_at_load(tmp_path):
    prompt = tmp_path / "tagged.md"
    prompt.write_text("Fields:\n\n{{entities}}\n", encoding="utf-8")
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["клиентская-консультация", "O-1"]},
            "presets": {
                "keypoints": {"enabled": False},
                "tagger": {"prompt_file": str(prompt)},
            },
        },
    )
    instructions = _preset_by_name(cfg, "tagger").instructions
    assert "{{entities}}" not in instructions
    assert "- клиентская-консультация" in instructions
    assert "- O-1" in instructions


def test_entities_placeholder_rendered_in_inline_instructions(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["O-1"]},
            "presets": {
                "keypoints": {"enabled": False},
                "tagger": {"instructions": "Fields:\n{{entities}}"},
            },
        },
    )
    instructions = _preset_by_name(cfg, "tagger").instructions
    assert "{{entities}}" not in instructions
    assert "- O-1" in instructions


def test_prompt_without_placeholder_is_untouched(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["O-1"]},
            "presets": {
                "keypoints": {"enabled": False},
                "plain": {"instructions": "No placeholder here."},
            },
        },
    )
    assert _preset_by_name(cfg, "plain").instructions == "No placeholder here."


def test_empty_allow_list_renders_explicit_none(tmp_path):
    # With no tags configured the model must be told to return the field empty
    # rather than being handed a blank section it might fill by inventing a tag.
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "presets": {
                "keypoints": {"enabled": False},
                "tagger": {"instructions": "Fields:\n{{entities}}"},
            },
        },
    )
    instructions = _preset_by_name(cfg, "tagger").instructions
    assert "{{entities}}" not in instructions
    assert "no values are configured" in instructions.lower()


def test_entities_rendered_when_prompt_file_falls_back_to_packaged_asset(tmp_path):
    """A prompt_file resolved from the packaged assets (not from disk) must still be
    rendered — an unrendered placeholder leaves the model free to invent a tag."""
    config_file = tmp_path / "config.yml"
    # meta.md carries {{entities}} and exists only as a packaged asset here.
    _write_yaml(
        config_file,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["EB-1"]},
            "presets": {"tagger": {"prompt_file": "meta.md"}},
        },
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    instructions = _preset_by_name(cfg, "tagger").instructions
    assert "{{entities}}" not in instructions
    assert "- EB-1" in instructions


def test_meta_builtin_prompt_renders_entities(tmp_path):
    cfg = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "tags": {"allowed": ["клиентская-консультация", "EB-1"]},
            # `meta` is an opt-in built-in, so a config must enable it explicitly —
            # as the generated config.yml does.
            "presets": {"meta": {"enabled": True}},
        },
    )
    instructions = _preset_by_name(cfg, "meta").instructions
    assert "{{entities}}" not in instructions
    assert "- клиентская-консультация" in instructions
    assert "- EB-1" in instructions
    # The empty-scalar literal must survive rendering, pinned explicitly rather
    # than left for the model to guess (bare colon vs `null` vs `''`).
    assert "`''`" in instructions


def test_meta_prompt_is_rendered_with_the_configured_entities(tmp_path):
    config = _load_config(
        tmp_path,
        {
            "stt": {"provider": "disabled"},
            "meta": {
                "entities": [
                    {
                        "name": "target_filing",
                        "prompt": "На какую подачу целится клиент.",
                    }
                ]
            },
            "presets": {"meta": {"enabled": True}},
        },
    )
    meta_preset = next(p for p in config.presets if p.name == "meta")
    assert "{{entities}}" not in meta_preset.instructions
    assert "target_filing: <value>" in meta_preset.instructions
    assert "На какую подачу целится клиент." in meta_preset.instructions
    # The entity list replaced the old fixed fields entirely.
    assert "referral_note" not in meta_preset.instructions


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


def test_stt_deepgram_missing_default_keyterms_file_degrades(tmp_path, caplog):
    """The default file only exists once `config init` copies it, so a config that
    predates it (or an operator who deleted the sample) must still start."""
    with caplog.at_level(logging.WARNING, logger="src.config"):
        cfg = _load_config(
            tmp_path,
            _deepgram_data({"keyterms_enabled": True}),
            validate_providers=True,
        )

    assert cfg.deepgram_keyterms == ()
    assert "continuing without keyterm prompting" in caplog.text


def test_stt_deepgram_missing_default_keyterms_path_written_by_init_degrades(
    tmp_path, caplog
):
    """`config init` writes the default path verbatim, so a config carrying it is not
    making an explicit choice: a deleted sample must warn rather than hard-fail."""
    with caplog.at_level(logging.WARNING, logger="src.config"):
        cfg = _load_config(
            tmp_path,
            _deepgram_data(
                {
                    "keyterms_enabled": True,
                    "keyterms_file": "deepgram-keyterms-example.txt",
                }
            ),
            validate_providers=True,
        )

    assert cfg.deepgram_keyterms == ()
    assert "continuing without keyterm prompting" in caplog.text


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
            "presets": _disabled_builtins(),
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
            "folders": [{"folder_id": "f1"}, {"folder_id": "f2"}],
            "poll_interval": 300,
            "bitrate": "192k",
            "data_dir": "mydata",
            "stt": {"provider": "disabled"},
        },
    )
    assert [f.folder_id for f in cfg.folders] == ["f1", "f2"]
    assert cfg.poll_interval == 300
    assert cfg.bitrate == "192k"
    assert cfg.data_dir == tmp_path / "mydata"


# --- config.yml loading -----------------------------------------------------


def test_loads_grouped_yaml(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {
            "folders": [{"folder_id": "abc"}, {"folder_id": "def"}],
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

    assert [f.folder_id for f in cfg.folders] == ["abc", "def"]
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
    # Disabling the built-in presets keeps this a pure mp3-only setup; with any
    # enabled preset, an openai.api_key would be required.
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "presets": _disabled_builtins()},
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
            "folders": [{"folder_id": "rt1", "name": "One", "email": "one@x.org"}],
            "stt": {"provider": "disabled"},
            "openai": {"api_key": "sk-rt", "keypoints": True},
        },
        validate_providers=False,
    )

    data = _config_to_yaml_dict(cfg)
    config_file = tmp_path / "roundtrip.yml"
    _write_yaml(config_file, data)
    reloaded = load_config(config_path=config_file, validate_providers=False)

    assert reloaded.folders == (
        EmployeeFolder(folder_id="rt1", name="One", email="one@x.org"),
    )
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
    assert set(by_name) == _BUILTIN_NAMES | {"transcript-cleanup"}
    assert by_name["keypoints"].depends_on == ("transcript-cleanup",)
    # The built-in instructions are preserved when only depends_on is overridden.
    assert by_name["keypoints"].artifact_suffix == ".keypoints.md"


def test_yaml_presets_default_to_builtins_when_absent(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(config_file, {"stt": {"provider": "disabled"}})

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert {p.name for p in cfg.presets} == _BUILTIN_NAMES


def test_yaml_presets_can_disable_builtin(tmp_path):
    config_file = tmp_path / "config.yml"
    _write_yaml(
        config_file,
        {"stt": {"provider": "disabled"}, "presets": {"keypoints": {"enabled": False}}},
    )

    cfg = load_config(config_path=config_file, validate_providers=False)

    assert "keypoints" not in {p.name for p in cfg.presets}


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

    assert {p.name for p in cfg.presets} == _BUILTIN_NAMES | {"first"}


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
    assert data["stt"]["deepgram"]["keyterms_file"] == "deepgram-keyterms-example.txt"
    assert data["presets"]["keypoints"]["enabled"] is True
    assert data["presets"]["keypoints"]["prompt_file"] == "prompts/keypoints.md"
    # The full default chain is enabled out of the box, with transcript-cleanup
    # written above keypoints and every downstream preset depending on it.
    # `action-items` is retired from the generated chain (its output duplicates
    # `keypoints`' `## Задачи` section); its prompt asset still ships (see below).
    assert list(data["presets"]) == [
        "transcript-cleanup",
        "keypoints",
        "meta",
    ]
    assert [name for name, p in data["presets"].items() if p["enabled"]] == [
        "transcript-cleanup",
        "keypoints",
        "meta",
    ]
    assert data["presets"]["keypoints"]["depends_on"] == ["transcript-cleanup"]
    assert data["presets"]["meta"]["depends_on"] == ["transcript-cleanup"]
    assert data["presets"]["meta"]["prompt_file"] == "prompts/meta.md"
    # Batch and the run flag are on by default in a generated config.
    assert data["openai"]["batch"] is True
    assert data["run"]["enabled"] is True
    # An empty tag allow-list is seeded so the operator has the block to fill in.
    tags_entity = next(e for e in data["meta"]["entities"] if e["name"] == "tags")
    assert tags_entity["allowed"] == []
    # The packaged prompt assets are copied beside the config, including
    # action-items.md: the preset is retired, not deleted, so re-enabling it is a
    # config edit, not a code change.
    assert (tmp_path / "prompts" / "keypoints.md").is_file()
    assert (tmp_path / "prompts" / "transcript-cleanup.md").is_file()
    assert (tmp_path / "prompts" / "action-items.md").is_file()
    assert (tmp_path / "prompts" / "meta.md").is_file()
    assert (tmp_path / "deepgram-keyterms-example.txt").is_file()
    # The generated config loads back without provider secrets and yields the chain.
    cfg = load_config(config_path=config_file, validate_providers=False)
    assert cfg.data_dir == tmp_path
    assert {p.name for p in cfg.presets} == {
        "transcript-cleanup",
        "keypoints",
        "meta",
    }


def test_init_config_validates_with_copied_keyterms(tmp_path):
    config_file = tmp_path / "config.yml"
    init_config(config_path=config_file)
    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    data["stt"]["deepgram"]["api_key"] = "dg-test"
    data["openai"]["api_key"] = "sk-test"
    _write_yaml(config_file, data)

    cfg = load_config(config_path=config_file)

    assert cfg.deepgram_keyterms_file == tmp_path / "deepgram-keyterms-example.txt"
    # The copied example carries no active terms, so a fresh install prompts
    # Deepgram with nothing until the operator supplies their own list.
    assert cfg.deepgram_keyterms == ()


def test_packaged_keyterms_example_loads_and_points_at_the_live_list(tmp_path):
    text = load_packaged_keyterms()

    # The operator's real terms belong in the gitignored data/deepgram-keyterms.txt,
    # and the example says so.
    assert "data/deepgram-keyterms.txt" in text
    # Illustration only: every term is commented out, so copying this file beside a
    # config cannot bias transcription with sample terms the operator never chose.
    terms = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert terms == []


def test_init_default_writes_to_gdstt_home(monkeypatch, tmp_path):
    home = tmp_path / "instance"
    monkeypatch.setenv("GDSTT_HOME", str(home))

    path = init_config()

    assert path == home / CONFIG_FILE_NAME
    assert path.is_file()
    assert (home / "prompts" / "keypoints.md").is_file()
    assert (home / "deepgram-keyterms-example.txt").is_file()


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
    config_file.write_text("folders: [{folder_id: keep}]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        init_config(config_path=config_file)

    assert config_file.read_text(encoding="utf-8") == "folders: [{folder_id: keep}]\n"


def test_init_force_overwrites(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("folders: [{folder_id: stale}]\n", encoding="utf-8")

    init_config(config_path=config_file, force=True)

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["folders"] == []


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


def test_config_get_masks_webhook_token(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set("webhook.token", "hook-SECRET", config_path=config_file)

    # The webhook token authenticates against the receiver and must be masked in
    # both the whole-config dump and a single-key lookup.
    whole = config_get(config_path=config_file)
    assert "hook-SECRET" not in whole
    assert "***" in whole
    assert config_get("webhook.token", config_path=config_file) == "***"

    # The URL may itself carry the credential, so only its host stays visible.
    config_set("webhook.url", "https://example.com/hooks/gdstt", config_path=config_file)
    assert config_get("webhook.url", config_path=config_file) == "https://example.com/***"


def test_config_get_masks_call_booking_authorization_token(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set(
        "call_booking.authorization_token", "call-SECRET", config_path=config_file
    )

    # The receiver authenticates bookings with this bearer token; it must be masked
    # in both the whole-config dump and a single-key lookup, like webhook.token.
    whole = config_get(config_path=config_file)
    assert "call-SECRET" not in whole
    assert "***" in whole
    assert (
        config_get("call_booking.authorization_token", config_path=config_file)
        == "***"
    )

    # --show-secrets reveals it, same as any other masked leaf.
    assert (
        config_get(
            "call_booking.authorization_token",
            config_path=config_file,
            show_secrets=True,
        )
        == "call-SECRET"
    )


def test_config_get_redacts_webhook_url_query_and_credentials(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set(
        "webhook.url",
        "https://user:pw@example.com/hooks/gdstt?token=url-SECRET",
        config_path=config_file,
    )

    # A receiver often authenticates via a token in the query string, so the URL is
    # as sensitive as webhook.token; the host stays visible for confirmation.
    whole = config_get(config_path=config_file)
    assert "url-SECRET" not in whole
    assert "pw" not in whole
    assert "example.com" in whole

    single = config_get("webhook.url", config_path=config_file)
    assert single == "https://***@example.com/***?***"

    block = config_get("webhook", config_path=config_file)
    assert "url-SECRET" not in block

    assert (
        config_get("webhook.url", config_path=config_file, show_secrets=True)
        == "https://user:pw@example.com/hooks/gdstt?token=url-SECRET"
    )


def test_config_get_redacts_webhook_url_path_secret(tmp_path):
    config_file = _base_config_file(tmp_path)
    config_set(
        "webhook.url",
        "https://hooks.slack.com/services/T00/B00/path-SECRET",
        config_path=config_file,
    )

    # Slack/Discord/Teams put the whole credential in the path, with no query string
    # and no userinfo — the shape that used to print verbatim.
    assert "path-SECRET" not in config_get(config_path=config_file)
    assert (
        config_get("webhook.url", config_path=config_file)
        == "https://hooks.slack.com/***"
    )
    assert (
        config_get("webhook.url", config_path=config_file, show_secrets=True)
        == "https://hooks.slack.com/services/T00/B00/path-SECRET"
    )


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
            "presets": _disabled_builtins(),
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
            "presets": _disabled_builtins(),
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
        {"stt": {"provider": "disabled"}, "presets": _disabled_builtins()},
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
            "presets": _disabled_builtins(),
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
            "presets": _disabled_builtins(),
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


# --- call_booking / planfix --------------------------------------------------


CALL_BOOKING_BASE = {
    "folders": [{"folder_id": "f1", "name": "Ekaterina", "email": "kate@example.com"}],
    "stt": {"provider": "deepgram", "deepgram": {"api_key": "dg"}},
    "openai": {"api_key": "sk-test"},
}


def test_call_booking_defaults_to_disabled(tmp_path):
    config = load_config(config_path=write_config(tmp_path, CALL_BOOKING_BASE))

    assert config.call_booking_enabled is False
    assert config.call_booking_listen_host == "0.0.0.0"
    assert config.call_booking_listen_port == 8080
    assert config.call_booking_token == ""
    assert config.call_booking_threshold_minutes == 15
    assert config.call_booking_disable_recognition is False


def test_planfix_defaults(tmp_path):
    config = load_config(config_path=write_config(tmp_path, CALL_BOOKING_BASE))

    assert config.planfix_create_comment_url == ""
    assert config.planfix_token == ""
    assert config.planfix_presets == ("keypoints",)


def test_call_booking_and_planfix_are_parsed(tmp_path):
    raw = {
        **CALL_BOOKING_BASE,
        "call_booking": {
            "enabled": True,
            "listen_host": "127.0.0.1",
            "listen_port": 9100,
            "authorization_token": "secret-token",
            "threshold_minutes": 20,
            "disable_recognition": True,
        },
        "planfix": {
            "create_comment_url": "https://crm.example.com/planfix_create_comment",
            "token": "planfix-token",
            "presets": ["keypoints", "action-items"],
        },
    }

    config = load_config(config_path=write_config(tmp_path, raw))

    assert config.call_booking_enabled is True
    assert config.call_booking_listen_host == "127.0.0.1"
    assert config.call_booking_listen_port == 9100
    assert config.call_booking_token == "secret-token"
    assert config.call_booking_threshold_minutes == 20
    assert config.call_booking_disable_recognition is True
    assert config.planfix_create_comment_url == (
        "https://crm.example.com/planfix_create_comment"
    )
    assert config.planfix_token == "planfix-token"
    assert config.planfix_presets == ("keypoints", "action-items")


def test_planfix_meta_fields_has_a_default(tmp_path):
    assert _load_config(tmp_path, {}).planfix_meta_fields == (
        "subject", "tags", "referral", "referral_note",
        "case_deadline", "deadlines", "target_filing",
        "duration", "video_url",
    )


def test_planfix_meta_fields_default_includes_the_new_entities(tmp_path):
    config = _load_config(tmp_path, {})
    assert config.planfix_meta_fields == (
        "subject",
        "tags",
        "referral",
        "referral_note",
        "case_deadline",
        "deadlines",
        "target_filing",
        "duration",
        "video_url",
    )


def test_planfix_meta_fields_is_read_from_config(tmp_path):
    config = _load_config(tmp_path, {"planfix": {"meta_fields": ["subject"]}})
    assert config.planfix_meta_fields == ("subject",)


def test_planfix_meta_fields_can_be_emptied(tmp_path):
    assert _load_config(tmp_path, {"planfix": {"meta_fields": []}}).planfix_meta_fields == ()


def test_enabled_receiver_without_token_is_rejected(tmp_path):
    raw = {**CALL_BOOKING_BASE, "call_booking": {"enabled": True}}

    with pytest.raises(ValueError, match="authorization_token"):
        load_config(config_path=write_config(tmp_path, raw))


def test_disable_recognition_with_an_emailless_folder_is_rejected(tmp_path):
    raw = {
        **CALL_BOOKING_BASE,
        "folders": [
            {"folder_id": "f1", "name": "Ekaterina", "email": "kate@example.com"},
            {"folder_id": "f2", "name": "Nameless"},
        ],
        "call_booking": {
            "enabled": True,
            "authorization_token": "t",
            "disable_recognition": True,
        },
    }

    with pytest.raises(ValueError, match="f2"):
        load_config(config_path=write_config(tmp_path, raw))


def test_threshold_minutes_must_be_positive(tmp_path):
    raw = {
        **CALL_BOOKING_BASE,
        "call_booking": {"threshold_minutes": 0},
    }

    with pytest.raises(ValueError, match="threshold_minutes"):
        load_config(config_path=write_config(tmp_path, raw))


def test_call_bookings_file_sits_next_to_the_config(tmp_path):
    config_path = write_config(tmp_path, CALL_BOOKING_BASE)

    config = load_config(config_path=config_path)

    assert config.call_bookings_file == config_path.parent / "call_bookings.jsonl"


def test_generated_config_ships_the_new_sections(tmp_path):
    init_config(config_path=tmp_path / "config.yml")

    raw = yaml.safe_load((tmp_path / "config.yml").read_text(encoding="utf-8"))

    assert raw["call_booking"] == {
        "enabled": False,
        "listen_host": "0.0.0.0",
        "listen_port": 8080,
        "authorization_token": "",
        "threshold_minutes": 15,
        "disable_recognition": False,
    }
    assert raw["planfix"] == {
        "create_comment_url": "",
        "token": "",
        "presets": ["keypoints"],
        "meta_fields": [
            "subject", "tags", "referral", "referral_note",
            "case_deadline", "deadlines", "target_filing",
            "duration", "video_url",
        ],
        "task_url": "",
    }


def test_output_also_drive_defaults_to_off(tmp_path):
    config = _load_config(tmp_path, {"output": {"target": "folder", "dir": "results"}})

    assert config.output_also_drive is False


def test_output_also_drive_is_read(tmp_path):
    config = _load_config(
        tmp_path, {"output": {"target": "folder", "dir": "results", "also_drive": True}}
    )

    assert config.output_also_drive is True


def test_output_also_drive_is_rejected_with_target_drive(tmp_path):
    """target=drive already writes to Drive; the combination would only confuse."""
    with pytest.raises(ValueError, match="only applies when output.target=folder"):
        _load_config(tmp_path, {"output": {"target": "drive", "also_drive": True}})


def test_stt_presets_defaults_to_keypoints(tmp_path):
    assert _load_config(tmp_path, {}).stt_presets == ("keypoints",)


def test_stt_presets_is_read_from_output(tmp_path):
    config = _load_config(tmp_path, {"output": {"stt_presets": ["keypoints", "action-items"]}})
    assert config.stt_presets == ("keypoints", "action-items")


def test_stt_presets_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="output.stt_presets"):
        _load_config(tmp_path, {"output": {"stt_presets": "keypoints"}})


# --- referrals.allowed --------------------------------------------------------


def test_referrals_allowed_is_parsed(tmp_path):
    config = _load_config(tmp_path, {"referrals": {"allowed": ["рекомендация", "instagram"]}})
    assert config.referrals_allowed == ("рекомендация", "instagram")


def test_referrals_allowed_renders_into_the_meta_prompt(tmp_path):
    config = _load_config(
        tmp_path,
        {
            "openai": {"api_key": "k"},
            "referrals": {"allowed": ["instagram"]},
            "presets": {"meta": {"enabled": True}},
        },
    )
    meta_preset = next(p for p in config.presets if p.name == "meta")
    assert "{{entities}}" not in meta_preset.instructions
    assert "- instagram" in meta_preset.instructions


def test_referrals_allowed_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="referrals.allowed"):
        _load_config(tmp_path, {"referrals": {"allowed": "instagram"}})


def test_planfix_task_url_is_read_from_config(tmp_path):
    config = _load_config(
        tmp_path, {"planfix": {"task_url": "https://tagilcity.planfix.com/task/<task-id>"}}
    )
    assert config.planfix_task_url == "https://tagilcity.planfix.com/task/<task-id>"


def test_planfix_task_url_defaults_to_empty(tmp_path):
    assert _load_config(tmp_path, {}).planfix_task_url == ""


def test_default_config_writes_the_planfix_task_url_key():
    """The key must be visible in a generated config, or nobody knows it exists."""
    assert _default_config_dict()["planfix"]["task_url"] == ""


# --- meta.entities -------------------------------------------------------------


def test_meta_entities_default_to_the_builtins_wired_to_legacy_allow_lists(tmp_path):
    config = _load_config(
        tmp_path,
        {
            "tags": {"allowed": ["O-1"]},
            "referrals": {"allowed": ["telegram"]},
        },
    )
    by_name = {entity.name: entity for entity in config.meta_entities}
    assert set(by_name) == {"subject", "tags", "referral", "referral_note"}
    assert by_name["tags"].allowed == ("O-1",)
    assert by_name["referral"].allowed == ("telegram",)


def test_declared_meta_entities_replace_the_builtins(tmp_path):
    config = _load_config(
        tmp_path,
        {
            "meta": {
                "entities": [
                    {
                        "name": "target_filing",
                        "prompt": "На какую подачу целится клиент.",
                        "label": "Целевая подача",
                    }
                ]
            }
        },
    )
    assert [entity.name for entity in config.meta_entities] == ["target_filing"]


def test_declared_entities_make_the_legacy_allow_lists_a_logged_deprecation(
    tmp_path, caplog
):
    with caplog.at_level(logging.WARNING):
        config = _load_config(
            tmp_path,
            {
                "tags": {"allowed": ["O-1"]},
                "meta": {"entities": [{"name": "subject", "prompt": "Тема."}]},
            },
        )
    assert [entity.name for entity in config.meta_entities] == ["subject"]
    assert "tags.allowed" in caplog.text


def test_invalid_meta_entities_fail_the_load_with_the_entity_named(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        _load_config(
            tmp_path,
            {"meta": {"entities": [{"name": "manager", "prompt": "Кто."}]}},
        )
    assert "manager" in str(excinfo.value)


def test_whole_config_rewrite_round_trips_entities_with_their_prompts(tmp_path):
    original = _load_config(
        tmp_path,
        {
            "meta": {
                "entities": [
                    {
                        "name": "deadlines",
                        "prompt": "Сроки, названные на звонке.",
                        "label": "Дедлайны",
                        "multiple": True,
                    },
                    {
                        "name": "referral",
                        "prompt": "Откуда узнал.",
                        "type": "enum",
                        "allowed": ["telegram"],
                    },
                ]
            }
        },
    )
    rewritten = _load_config(tmp_path, _config_to_yaml_dict(original))
    assert rewritten.meta_entities == original.meta_entities


def test_generated_default_config_ships_the_seven_entities():
    generated = _default_config_dict()
    names = [entity["name"] for entity in generated["meta"]["entities"]]
    assert names == [
        "subject",
        "tags",
        "referral",
        "referral_note",
        "case_deadline",
        "deadlines",
        "target_filing",
    ]
    # The allow-lists moved inside the entities; the old top-level homes are gone.
    assert "tags" not in generated
    assert "referrals" not in generated
    referral = next(e for e in generated["meta"]["entities"] if e["name"] == "referral")
    assert "рекомендация" in referral["allowed"]
