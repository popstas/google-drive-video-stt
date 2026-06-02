from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.config import (
    DEEPGRAM_AUDIO_SOURCES,
    DEEPGRAM_DEFAULT_KEYTERMS_FILE,
    DEEPGRAM_DIARIZE_MODELS,
    DEEPGRAM_TXT_FORMATTERS,
    Config,
    _load_deepgram_api_key,
    _load_deepgram_keyterms,
    _parse_bool,
    resolve_config_path,
)

DEFAULT_PROFILE_PATH = Path("config/pipelines/default.json")
LOCAL_PROFILE_PATH = Path("config/pipelines/local.json")
CHECKOUT_ROOT = Path(__file__).resolve().parent.parent

_PROFILE_FIELDS = {"version", "stt", "refine", "artifacts", "speakers"}
_SECTION_FIELDS = {
    "stt": {"provider", "audio_source"},
    "refine": {"enabled", "provider"},
    "artifacts": {"drive_mp3", "drive_txt"},
    "speakers": {"mode"},
}
_SUPPORTED_PROFILE_STT_PROVIDERS = {"deepgram", "openai", "google", "asr"}
_SUPPORTED_SPEAKERS_MODES = {"filename_or_metadata"}
_REQUIRED_PROVIDER_SETTINGS = {
    "google": ("GOOGLE_CLOUD_PROJECT", "GOOGLE_STT_GCS_BUCKET", "STT_LANGUAGE"),
    "asr": ("ASR_URL",),
}


@dataclass(frozen=True)
class PipelineProfile:
    version: int
    stt_provider: str
    audio_source: str
    refine_enabled: bool
    refine_provider: str
    drive_mp3: bool
    drive_txt: bool
    speakers_mode: str


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Pipeline profile at {path} could not be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Pipeline profile at {path} must be a JSON object")
    return payload


def _recursive_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _recursive_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate_fields(payload: dict[str, Any]) -> None:
    unknown = sorted(set(payload) - _PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"unknown profile fields: {unknown}")
    for section, allowed in _SECTION_FIELDS.items():
        value = payload.get(section)
        if not isinstance(value, dict):
            raise ValueError(f"Pipeline profile section {section!r} must be an object")
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown profile fields in {section}: {unknown}")


def _require_string(payload: dict[str, Any], section: str, key: str) -> str:
    value = payload[section].get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pipeline profile {section}.{key} must be a non-empty string")
    return value.strip()


def _require_bool(payload: dict[str, Any], section: str, key: str) -> bool:
    value = payload[section].get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Pipeline profile {section}.{key} must be a boolean")
    return value


def _parse_profile(payload: dict[str, Any]) -> PipelineProfile:
    _validate_fields(payload)
    version = payload.get("version")
    if version != 1:
        raise ValueError(f"Unsupported pipeline profile version: {version!r}")
    stt_provider = _require_string(payload, "stt", "provider")
    audio_source = _require_string(payload, "stt", "audio_source")
    refine_enabled = _require_bool(payload, "refine", "enabled")
    refine_provider = _require_string(payload, "refine", "provider")
    drive_mp3 = _require_bool(payload, "artifacts", "drive_mp3")
    drive_txt = _require_bool(payload, "artifacts", "drive_txt")
    speakers_mode = _require_string(payload, "speakers", "mode")
    if stt_provider not in _SUPPORTED_PROFILE_STT_PROVIDERS:
        raise ValueError(f"Unsupported STT provider: {stt_provider!r}")
    if audio_source not in DEEPGRAM_AUDIO_SOURCES:
        raise ValueError(f"Unsupported Deepgram audio source: {audio_source!r}")
    if refine_enabled and refine_provider != "openai":
        raise ValueError(f"Unsupported refinement provider: {refine_provider!r}")
    if not drive_txt:
        raise ValueError("Pipeline profile artifacts.drive_txt=false is not supported")
    if speakers_mode not in _SUPPORTED_SPEAKERS_MODES:
        raise ValueError(f"Unsupported speakers mode: {speakers_mode!r}")
    return PipelineProfile(
        version=version,
        stt_provider=stt_provider,
        audio_source=audio_source,
        refine_enabled=refine_enabled,
        refine_provider=refine_provider,
        drive_mp3=drive_mp3,
        drive_txt=drive_txt,
        speakers_mode=speakers_mode,
    )


def load_pipeline_profile(*, repo_root: Path | None = None) -> PipelineProfile:
    repo_root = repo_root or CHECKOUT_ROOT
    default_path = repo_root / DEFAULT_PROFILE_PATH
    local_path = repo_root / LOCAL_PROFILE_PATH
    payload = _read_json_object(default_path)
    if local_path.exists():
        payload = _recursive_merge(payload, _read_json_object(local_path))
    return _parse_profile(payload)


def required_secret_status(
    profile: PipelineProfile,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, dict[str, bool]]:
    env = os.environ if env is None else env
    status: dict[str, dict[str, bool]] = {}
    if profile.stt_provider == "deepgram":
        api_key = env.get("DEEPGRAM_API_KEY", "").strip()
        api_key_file = env.get("DEEPGRAM_API_KEY_FILE", "").strip()
        configured = bool(api_key)
        if not configured and api_key_file:
            try:
                configured = bool(
                    _load_deepgram_api_key("", str(resolve_config_path(api_key_file)))
                )
            except ValueError:
                configured = False
        status["DEEPGRAM_API_KEY"] = {"configured": configured}
    if profile.stt_provider == "openai" or (
        profile.refine_enabled and profile.refine_provider == "openai"
    ):
        status["OPENAI_API_KEY"] = {
            "configured": bool(env.get("OPENAI_API_KEY", "").strip())
        }
    return status


def required_setting_status(
    profile: PipelineProfile,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, dict[str, bool]]:
    env = os.environ if env is None else env
    return {
        key: {"configured": bool(env.get(key, "").strip())}
        for key in _REQUIRED_PROVIDER_SETTINGS.get(profile.stt_provider, ())
    }


def resolve_profile(
    profile: PipelineProfile,
    *,
    overrides: Mapping[str, object] | None = None,
) -> PipelineProfile:
    overrides = overrides or {}
    stt_provider = overrides.get("stt_provider", profile.stt_provider)
    refine_enabled = overrides.get("refine", profile.refine_enabled)
    drive_mp3 = overrides.get("drive_mp3_artifact", profile.drive_mp3)
    if not isinstance(stt_provider, str) or not stt_provider.strip():
        raise ValueError("overrides.stt_provider must be a non-empty string")
    stt_provider = stt_provider.strip()
    if stt_provider not in _SUPPORTED_PROFILE_STT_PROVIDERS:
        raise ValueError(f"Unsupported STT provider: {stt_provider!r}")
    if not isinstance(refine_enabled, bool):
        raise ValueError("overrides.refine must be a boolean")
    if not isinstance(drive_mp3, bool):
        raise ValueError("overrides.drive_mp3_artifact must be a boolean")
    return replace(
        profile,
        stt_provider=stt_provider,
        refine_enabled=refine_enabled,
        drive_mp3=drive_mp3,
    )


def missing_required_secrets(
    profile: PipelineProfile,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    return [
        key
        for key, state in required_secret_status(profile, env=env).items()
        if not state["configured"]
    ]


def missing_required_settings(
    profile: PipelineProfile,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    return [
        key
        for key, state in required_setting_status(profile, env=env).items()
        if not state["configured"]
    ]


def apply_profile(
    config: Config,
    profile: PipelineProfile,
    *,
    overrides: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    env = os.environ if env is None else env
    resolved = resolve_profile(profile, overrides=overrides)
    deepgram_api_key = config.deepgram_api_key
    deepgram_model = config.deepgram_model
    deepgram_diarize_model = config.deepgram_diarize_model
    deepgram_txt_formatter = config.deepgram_txt_formatter
    deepgram_keyterms_enabled = config.deepgram_keyterms_enabled
    deepgram_keyterms_file = config.deepgram_keyterms_file
    deepgram_keyterms = config.deepgram_keyterms
    if resolved.stt_provider == "deepgram":
        api_key_file = env.get("DEEPGRAM_API_KEY_FILE", "").strip()
        deepgram_api_key = _load_deepgram_api_key(
            env.get("DEEPGRAM_API_KEY", ""),
            str(resolve_config_path(api_key_file)) if api_key_file else "",
        )
        if not deepgram_api_key:
            raise ValueError("DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram")
        deepgram_model = env.get("DEEPGRAM_MODEL", "nova-3").strip() or "nova-3"
        deepgram_diarize_model = (
            env.get("DEEPGRAM_DIARIZE_MODEL", "latest").strip().lower() or "latest"
        )
        deepgram_txt_formatter = (
            env.get("DEEPGRAM_TXT_FORMATTER", "word_speaker").strip().lower()
            or "word_speaker"
        )
        deepgram_keyterms_enabled = _parse_bool(
            env.get("DEEPGRAM_KEYTERMS_ENABLED", ""),
            default=True,
        )
        deepgram_keyterms_file = resolve_config_path(
            env.get("DEEPGRAM_KEYTERMS_FILE", str(DEEPGRAM_DEFAULT_KEYTERMS_FILE))
            .strip()
            or str(DEEPGRAM_DEFAULT_KEYTERMS_FILE)
        )
        if deepgram_diarize_model not in DEEPGRAM_DIARIZE_MODELS:
            raise ValueError(
                f"DEEPGRAM_DIARIZE_MODEL must be one of {DEEPGRAM_DIARIZE_MODELS!r}, "
                f"got: {deepgram_diarize_model!r}"
            )
        if deepgram_txt_formatter not in DEEPGRAM_TXT_FORMATTERS:
            raise ValueError(
                f"DEEPGRAM_TXT_FORMATTER must be one of {DEEPGRAM_TXT_FORMATTERS!r}, "
                f"got: {deepgram_txt_formatter!r}"
            )
        deepgram_keyterms = _load_deepgram_keyterms(
            deepgram_keyterms_enabled,
            deepgram_keyterms_file,
        )
    stt_language = env.get("STT_LANGUAGE", config.stt_language).strip()
    if resolved.stt_provider == "deepgram" and not stt_language:
        stt_language = "ru"
    openai_api_key = env.get("OPENAI_API_KEY", config.openai_api_key).strip()
    google_cloud_project = env.get(
        "GOOGLE_CLOUD_PROJECT",
        config.google_cloud_project,
    ).strip()
    google_stt_gcs_bucket = env.get(
        "GOOGLE_STT_GCS_BUCKET",
        config.google_stt_gcs_bucket,
    ).strip()
    asr_url = env.get("ASR_URL", config.asr_url).strip()
    if resolved.stt_provider == "openai" and not openai_api_key:
        raise ValueError("OPENAI_API_KEY is required when STT_PROVIDER=openai")
    if resolved.refine_enabled and not openai_api_key:
        raise ValueError("OPENAI_API_KEY is required when OpenAI refinement is enabled")
    if resolved.stt_provider == "google":
        if not google_cloud_project:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required when STT_PROVIDER=google")
        if not google_stt_gcs_bucket:
            raise ValueError("GOOGLE_STT_GCS_BUCKET is required when STT_PROVIDER=google")
        if not stt_language:
            raise ValueError("STT_LANGUAGE is required when STT_PROVIDER=google")
    if resolved.stt_provider == "asr" and not asr_url:
        raise ValueError("ASR_URL is required when STT_PROVIDER=asr")
    return replace(
        config,
        stt_provider=resolved.stt_provider,
        stt_language=stt_language,
        deepgram_api_key=deepgram_api_key,
        google_cloud_project=google_cloud_project,
        google_stt_gcs_bucket=google_stt_gcs_bucket,
        asr_url=asr_url,
        deepgram_audio_source=resolved.audio_source,
        deepgram_model=deepgram_model,
        deepgram_diarize_model=deepgram_diarize_model,
        deepgram_txt_formatter=deepgram_txt_formatter,
        deepgram_keyterms_enabled=deepgram_keyterms_enabled,
        deepgram_keyterms_file=deepgram_keyterms_file,
        deepgram_keyterms=deepgram_keyterms,
        drive_mp3_artifact=resolved.drive_mp3,
        openai_api_key=openai_api_key,
        openai_postprocess=(
            resolved.refine_enabled and resolved.refine_provider == "openai"
        ),
    )
