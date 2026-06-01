from __future__ import annotations

import json

import pytest

from src.pipeline_policy import parse_intent, plan_process
from src.pipeline_profile import load_pipeline_profile


def test_parse_intent_accepts_minimal_process_request():
    intent = parse_intent({"action": "process", "targets": ["file-1"]})

    assert intent.action == "process"
    assert intent.targets == ("file-1",)
    assert intent.target_type == "auto"
    assert intent.overrides == {}


def test_parse_intent_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown intent fields"):
        parse_intent({"action": "process", "targets": ["file-1"], "surprise": True})


def test_plan_process_expands_default_pipeline_steps():
    plan = plan_process(
        {"action": "process", "targets": ["file-1"], "target_type": "file"},
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
    )

    assert plan["status"] == "ready"
    assert plan["steps"] == [
        "download_mp4",
        "extract_m4a_copy",
        "deepgram_transcribe",
        "openai_refine",
        "upload_txt",
    ]
    assert plan["confirmation_required"] is False


def test_plan_process_stops_before_execution_when_openai_key_is_missing():
    plan = plan_process(
        {"action": "process", "targets": ["file-1"]},
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg"},
    )

    assert plan == {
        "status": "configuration_required",
        "missing": ["OPENAI_API_KEY"],
        "next_action": "Run `gdstt setup` or add the missing key to .env.",
        "secrets": {
            "DEEPGRAM_API_KEY": {"configured": True},
            "OPENAI_API_KEY": {"configured": False},
        },
    }
    assert "dg" not in json.dumps(plan)


def test_plan_process_requires_confirmation_for_folder_and_reprocess():
    plan = plan_process(
        {
            "action": "process",
            "targets": ["folder-1"],
            "target_type": "folder",
            "overrides": {"reprocess_txt": True},
        },
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
    )

    assert plan["confirmation_required"] is True
    assert plan["confirmation_reasons"] == ["folder_wide", "reprocess_txt"]
