from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from src import drive
from src import main as main_module
from src.auth import build_drive_service
from src.config import Config
from src.pipeline_policy import ProcessIntent, parse_intent, plan_process
from src.pipeline_profile import PipelineProfile, apply_profile

ServiceBuilder = Callable[[], Any]


def _default_service_builder(config: Config) -> ServiceBuilder:
    return lambda: build_drive_service(data_dir=config.data_dir)


def _cost_report(telemetry: object, config: Config) -> dict[str, float | None]:
    providers = {config.stt_provider}
    if config.openai_postprocess:
        providers.add("openai")
    report = {provider: None for provider in providers if provider}
    if not isinstance(telemetry, list):
        return report
    for item in telemetry:
        costs = getattr(item, "cost_usd", {})
        if not isinstance(costs, dict):
            continue
        for provider, value in costs.items():
            if provider not in report or not isinstance(value, (int, float)):
                continue
            report[provider] = (report[provider] or 0.0) + float(value)
    return report


def _usage_report(telemetry: object) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    if not isinstance(telemetry, list):
        return report
    for item in telemetry:
        usage = getattr(item, "usage", {})
        if not isinstance(usage, dict):
            continue
        for provider, counters in usage.items():
            if not isinstance(counters, dict):
                continue
            provider_report = report.setdefault(provider, {})
            for counter, value in counters.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    provider_report[counter] = provider_report.get(counter, 0) + value
    return report


def _uploaded_report(telemetry: object, field: str) -> bool:
    if not isinstance(telemetry, list):
        return False
    return any(getattr(item, field, False) is True for item in telemetry)


def execute_process(
    payload: dict[str, Any] | ProcessIntent,
    config: Config,
    profile: PipelineProfile,
    *,
    confirmed: bool = False,
    env: Mapping[str, str] | None = None,
    service_builder: ServiceBuilder | None = None,
) -> dict[str, object]:
    env = os.environ if env is None else env
    intent = payload if isinstance(payload, ProcessIntent) else parse_intent(payload)
    plan = plan_process(intent, profile, env=env)
    if plan["status"] != "ready":
        return plan
    if plan["confirmation_required"] and not confirmed:
        return {
            "status": "confirmation_required",
            "confirmation_reasons": plan["confirmation_reasons"],
            "plan": plan,
        }

    runtime_config = apply_profile(
        config,
        profile,
        overrides=intent.overrides,
        env=env,
    )
    builder = service_builder or _default_service_builder(runtime_config)
    service = builder()
    speakers = intent.overrides.get("speakers")
    reprocess_txt = intent.overrides.get("reprocess_txt") is True
    detected_target_types: dict[str, bool] = {}
    target_metadata: dict[str, dict] = {}
    if intent.target_type == "auto":
        for target in intent.targets:
            metadata = drive.get_file_metadata(service, target)
            target_metadata[target] = metadata
            detected_target_types[target] = metadata.get("mimeType") == drive.FOLDER_MIME
        detected_folders = [
            target for target, is_folder in detected_target_types.items() if is_folder
        ]
        if detected_folders and speakers:
            raise ValueError("overrides.speakers requires file targets")
        if detected_folders and not confirmed:
            return {
                "status": "confirmation_required",
                "confirmation_reasons": ["folder_wide"],
                "detected_folder_targets": detected_folders,
                "plan": plan,
            }
    if intent.target_type == "folder":
        is_folder = True
    elif intent.target_type == "file":
        is_folder = False
    else:
        is_folder = None

    results: list[dict[str, object]] = []
    for target in intent.targets:
        if speakers:
            metadata = target_metadata.get(target) or drive.get_file_metadata(service, target)
            if metadata.get("mimeType") != drive.MP4_MIME:
                raise ValueError("overrides.speakers requires Drive MP4 file targets")
            drive.set_file_app_properties(
                service,
                target,
                {drive.SPEAKER_NAMES_PROPERTY: json.dumps(speakers, ensure_ascii=False)},
            )
        telemetry = main_module.process_target(
            service,
            target,
            runtime_config,
            is_folder=detected_target_types.get(target, is_folder),
            reprocess_txt=reprocess_txt,
        )
        results.append(
            {
                "id": target,
                "txt_uploaded": _uploaded_report(telemetry, "txt_uploaded"),
                "mp3_uploaded": _uploaded_report(telemetry, "mp3_uploaded"),
                "speakers": speakers or [],
                "cost_usd": _cost_report(telemetry, runtime_config),
                "usage": _usage_report(telemetry),
            }
        )
    return {"status": "completed", "files": results}
