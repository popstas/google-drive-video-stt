from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline_profile import (
    apply_profile,
    load_pipeline_profile,
    required_secret_status,
)
from tests.test_main import make_config


def _write_profile(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_pipeline_profile_reads_default_profile(tmp_path):
    _write_profile(
        tmp_path / "config" / "pipelines" / "default.json",
        {
            "version": 1,
            "stt": {"provider": "deepgram", "audio_source": "m4a_copy"},
            "refine": {"enabled": True, "provider": "openai"},
            "artifacts": {"drive_mp3": False, "drive_txt": True},
            "speakers": {"mode": "filename_or_metadata"},
        },
    )

    profile = load_pipeline_profile(repo_root=tmp_path)

    assert profile.stt_provider == "deepgram"
    assert profile.audio_source == "m4a_copy"
    assert profile.refine_enabled is True
    assert profile.refine_provider == "openai"
    assert profile.drive_mp3 is False
    assert profile.drive_txt is True


def test_load_pipeline_profile_defaults_to_checkout_when_cwd_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    profile = load_pipeline_profile()

    assert profile.stt_provider == "deepgram"
    assert profile.refine_enabled is True


def test_load_pipeline_profile_recursively_merges_local_override(tmp_path):
    _write_profile(
        tmp_path / "config" / "pipelines" / "default.json",
        {
            "version": 1,
            "stt": {"provider": "deepgram", "audio_source": "m4a_copy"},
            "refine": {"enabled": True, "provider": "openai"},
            "artifacts": {"drive_mp3": False, "drive_txt": True},
            "speakers": {"mode": "filename_or_metadata"},
        },
    )
    _write_profile(
        tmp_path / "config" / "pipelines" / "local.json",
        {"artifacts": {"drive_mp3": True}},
    )

    profile = load_pipeline_profile(repo_root=tmp_path)

    assert profile.drive_mp3 is True
    assert profile.drive_txt is True
    assert profile.refine_enabled is True


def test_load_pipeline_profile_rejects_unknown_fields(tmp_path):
    _write_profile(
        tmp_path / "config" / "pipelines" / "default.json",
        {
            "version": 1,
            "stt": {"provider": "deepgram", "audio_source": "m4a_copy"},
            "refine": {"enabled": True, "provider": "openai"},
            "artifacts": {"drive_mp3": False, "drive_txt": True},
            "speakers": {"mode": "filename_or_metadata"},
            "surprise": True,
        },
    )

    with pytest.raises(ValueError, match="unknown profile fields"):
        load_pipeline_profile(repo_root=tmp_path)


def test_required_secret_status_never_exposes_values():
    profile = load_pipeline_profile()

    status = required_secret_status(
        profile,
        env={
            "DEEPGRAM_API_KEY": "dg-secret",
            "OPENAI_API_KEY": "sk-secret",
        },
    )

    assert status == {
        "DEEPGRAM_API_KEY": {"configured": True},
        "OPENAI_API_KEY": {"configured": True},
    }
    assert "secret" not in json.dumps(status)


def test_required_secret_status_resolves_checkout_relative_key_file(
    tmp_path,
    monkeypatch,
):
    checkout = tmp_path / "checkout"
    elsewhere = tmp_path / "elsewhere"
    key_path = checkout / "data" / "deepgram.key"
    key_path.parent.mkdir(parents=True)
    elsewhere.mkdir()
    (checkout / ".env").write_text("", encoding="utf-8")
    key_path.write_text("dg-secret", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("src.config.CHECKOUT_ROOT", checkout)

    status = required_secret_status(
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY_FILE": "data/deepgram.key", "OPENAI_API_KEY": "sk"},
    )

    assert status["DEEPGRAM_API_KEY"] == {"configured": True}


def test_apply_profile_returns_config_copy_with_profile_defaults():
    cfg = make_config(
        stt_provider="",
        deepgram_audio_source="mp3_96k",
        drive_mp3_artifact=True,
        openai_postprocess=False,
    )

    applied = apply_profile(cfg, load_pipeline_profile())

    assert applied is not cfg
    assert applied.stt_provider == "deepgram"
    assert applied.deepgram_audio_source == "m4a_copy"
    assert applied.drive_mp3_artifact is False
    assert applied.openai_postprocess is True
