from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.pipeline_profile import (
    PipelineProfile,
    missing_required_secrets,
    missing_required_settings,
    resolve_profile,
    required_secret_status,
    required_setting_status,
)

_INTENT_FIELDS = {"action", "targets", "target_type", "overrides"}
_OVERRIDE_FIELDS = {
    "speakers",
    "stt_provider",
    "refine",
    "drive_mp3_artifact",
    "reprocess_txt",
}
_TARGET_TYPES = {"auto", "file", "folder"}


@dataclass(frozen=True)
class ProcessIntent:
    action: str
    targets: tuple[str, ...]
    target_type: str
    overrides: dict[str, object]


def parse_intent(payload: dict[str, Any]) -> ProcessIntent:
    if not isinstance(payload, dict):
        raise ValueError("intent must be a JSON object")
    unknown = sorted(set(payload) - _INTENT_FIELDS)
    if unknown:
        raise ValueError(f"unknown intent fields: {unknown}")
    action = payload.get("action")
    if action != "process":
        raise ValueError("intent.action must be 'process'")
    targets = payload.get("targets")
    if (
        not isinstance(targets, list)
        or not targets
        or not all(isinstance(item, str) and item.strip() for item in targets)
    ):
        raise ValueError("intent.targets must be a non-empty list of strings")
    target_type = payload.get("target_type", "auto")
    if target_type not in _TARGET_TYPES:
        raise ValueError(f"intent.target_type must be one of {sorted(_TARGET_TYPES)}")
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("intent.overrides must be an object")
    unknown = sorted(set(overrides) - _OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(f"unknown override fields: {unknown}")
    if "speakers" in overrides:
        speakers = overrides["speakers"]
        if (
            not isinstance(speakers, list)
            or not speakers
            or not all(isinstance(item, str) and item.strip() for item in speakers)
        ):
            raise ValueError("overrides.speakers must be a non-empty list of strings")
    for key in ("refine", "drive_mp3_artifact", "reprocess_txt"):
        if key in overrides and not isinstance(overrides[key], bool):
            raise ValueError(f"overrides.{key} must be a boolean")
    return ProcessIntent(
        action=action,
        targets=tuple(item.strip() for item in targets),
        target_type=target_type,
        overrides=dict(overrides),
    )


def _steps(profile: PipelineProfile) -> list[str]:
    provider = profile.stt_provider
    audio_source = profile.audio_source
    steps = ["download_mp4"]
    if provider == "deepgram":
        steps.append(f"extract_{audio_source}")
    else:
        steps.append("extract_mp3")
    steps.append(f"{provider}_transcribe")
    if profile.refine_enabled and profile.refine_provider == "openai":
        steps.append("openai_refine")
    if profile.drive_mp3:
        steps.append("upload_mp3")
    if profile.drive_txt:
        steps.append("upload_txt")
    return steps


def plan_process(
    payload: dict[str, Any] | ProcessIntent,
    profile: PipelineProfile,
    *,
    env=None,
) -> dict[str, object]:
    intent = payload if isinstance(payload, ProcessIntent) else parse_intent(payload)
    if intent.target_type == "folder" and intent.overrides.get("speakers"):
        raise ValueError("overrides.speakers requires file targets")
    resolved = resolve_profile(profile, overrides=intent.overrides)
    secrets = required_secret_status(resolved, env=env)
    settings = required_setting_status(resolved, env=env)
    missing = [
        *missing_required_secrets(resolved, env=env),
        *missing_required_settings(resolved, env=env),
    ]
    if missing:
        result = {
            "status": "configuration_required",
            "missing": missing,
            "next_action": (
                "Run `gdstt setup` for default API keys or add the missing "
                "configuration to .env."
            ),
            "secrets": secrets,
        }
        if settings:
            result["settings"] = settings
        return result
    reasons: list[str] = []
    if intent.target_type == "folder":
        reasons.append("folder_wide")
    if intent.overrides.get("reprocess_txt") is True:
        reasons.append("reprocess_txt")
    result = {
        "status": "ready",
        "action": intent.action,
        "targets": list(intent.targets),
        "target_type": intent.target_type,
        "steps": _steps(resolved),
        "confirmation_required": bool(reasons),
        "confirmation_reasons": reasons,
        "secrets": secrets,
    }
    if settings:
        result["settings"] = settings
    return result
