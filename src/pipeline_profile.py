from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.config import Config, _load_deepgram_api_key, resolve_config_path

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
    if refine_enabled and refine_provider != "openai":
        raise ValueError(f"Unsupported refinement provider: {refine_provider!r}")
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
    env = env or os.environ
    status: dict[str, dict[str, bool]] = {}
    if profile.stt_provider == "deepgram":
        api_key = env.get("DEEPGRAM_API_KEY", "").strip()
        api_key_file = env.get("DEEPGRAM_API_KEY_FILE", "").strip()
        configured = bool(api_key)
        if not configured and api_key_file:
            configured = resolve_config_path(api_key_file).is_file()
        status["DEEPGRAM_API_KEY"] = {"configured": configured}
    if profile.stt_provider == "openai" or (
        profile.refine_enabled and profile.refine_provider == "openai"
    ):
        status["OPENAI_API_KEY"] = {
            "configured": bool(env.get("OPENAI_API_KEY", "").strip())
        }
    return status


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
    if not isinstance(refine_enabled, bool):
        raise ValueError("overrides.refine must be a boolean")
    if not isinstance(drive_mp3, bool):
        raise ValueError("overrides.drive_mp3_artifact must be a boolean")
    return replace(
        profile,
        stt_provider=stt_provider.strip(),
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


def apply_profile(
    config: Config,
    profile: PipelineProfile,
    *,
    overrides: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    env = env or os.environ
    resolved = resolve_profile(profile, overrides=overrides)
    deepgram_api_key = config.deepgram_api_key
    if resolved.stt_provider == "deepgram":
        api_key_file = env.get("DEEPGRAM_API_KEY_FILE", "").strip()
        deepgram_api_key = _load_deepgram_api_key(
            env.get("DEEPGRAM_API_KEY", ""),
            str(resolve_config_path(api_key_file)) if api_key_file else "",
        )
    stt_language = config.stt_language
    if resolved.stt_provider == "deepgram" and not stt_language:
        stt_language = "ru"
    return replace(
        config,
        stt_provider=resolved.stt_provider,
        stt_language=stt_language,
        deepgram_api_key=deepgram_api_key,
        deepgram_audio_source=resolved.audio_source,
        drive_mp3_artifact=resolved.drive_mp3,
        openai_api_key=env.get("OPENAI_API_KEY", config.openai_api_key).strip(),
        openai_postprocess=(
            resolved.refine_enabled and resolved.refine_provider == "openai"
        ),
    )
