from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src import meta_doc, meta_entity
from src.config import Config, EmployeeFolder

TRANSCRIPT = "[00:00:05] Angelica Munkueva: Здравствуйте\n[00:31:42] Mels: Спасибо\n"
NAME = (
    "30-минутная онлайн-встреча Angelica Munkueva(ExpertizeMe) и Mels "
    "- 2026/08/13 14:29 CEST - Recording.mp4"
)


@pytest.fixture
def config_with_folder():
    return Config(
        folders=(EmployeeFolder("FOLDER1", "Анжелика Мункуева", "angelica@expertizeme.org"),),
        poll_interval=600,
        bitrate="96k",
        data_dir=Path("data"),
        proxy_url="",
        stt_provider="deepgram",
        openai_api_key="",
        deepgram_api_key="",
        stt_language="ru-RU",
    )


ENTITIES = meta_entity.default_entities()


def _document(config, **overrides):
    kwargs = {
        "values": {
            "subject": "Обсудили кейс",
            "tags": ["O-1"],
            "referral": "рекомендация",
            "referral_note": "Посоветовала знакомая",
        },
        "file_id": "FILE1",
        "file_name": NAME,
        "folder_id": "FOLDER1",
        "config": config,
        "transcript": TRANSCRIPT,
        "planfix_task_id": "918659",
        "processed_at": datetime(2026, 8, 13, 18, 52, 10, tzinfo=timezone.utc),
    }
    kwargs.update(overrides)
    return meta_doc.build(**kwargs)


def test_build_merges_model_fields_with_known_facts(config_with_folder):
    document = _document(config_with_folder)
    assert document["subject"] == "Обсудили кейс"
    assert document["tags"] == ["O-1"]
    assert document["referral"] == "рекомендация"
    assert document["manager"] == "Анжелика Мункуева"
    assert document["manager_email"] == "angelica@expertizeme.org"
    assert document["client"] == "Mels"
    assert document["planfix_task_id"] == "918659"
    assert document["video_url"] == "https://drive.google.com/file/d/FILE1/view"


def test_build_leaves_the_client_empty_without_a_manager_marker(config_with_folder):
    document = _document(
        config_with_folder, file_name="Alice and Bob - 2026/07/02 21:56 CEST.mp4"
    )
    assert document["client"] == ""


def test_build_reads_the_date_from_the_recording_name(config_with_folder):
    assert _document(config_with_folder)["date"] == "2026-08-13T12:29:00+00:00"


def test_build_leaves_the_date_empty_when_the_name_has_no_timestamp(config_with_folder):
    assert _document(config_with_folder, file_name="Recording.mp4")["date"] == ""


def test_build_takes_the_duration_from_the_last_timestamp(config_with_folder):
    assert _document(config_with_folder)["duration"] == "00:31:42"


def test_build_leaves_the_duration_empty_without_timestamps(config_with_folder):
    assert _document(config_with_folder, transcript="Speaker 1: привет")["duration"] == ""


def test_build_keeps_every_field_present_when_the_model_returned_nothing(config_with_folder):
    document = _document(
        config_with_folder,
        values={"subject": "", "tags": [], "referral": "", "referral_note": ""},
    )
    assert set(document) == set(meta_doc.field_order(ENTITIES))
    assert document["subject"] == ""
    assert document["tags"] == []


def test_to_yaml_round_trips_and_keeps_the_declared_order(config_with_folder):
    text = meta_doc.to_yaml(_document(config_with_folder), ENTITIES)
    assert list(yaml.safe_load(text)) == list(meta_doc.field_order(ENTITIES))
    assert "Обсудили кейс" in text


def test_field_order_puts_entities_first_then_the_code_fields():
    entities = meta_entity.parse_entities(
        [{"name": "target_filing", "prompt": "Подача."}]
    )
    order = meta_doc.field_order(entities)
    assert order[0] == "target_filing"
    assert order[1:] == meta_entity.CODE_FIELDS


def test_to_yaml_keeps_every_entity_present_even_when_empty():
    entities = meta_entity.parse_entities(
        [
            {"name": "subject", "prompt": "Тема.", "label": ""},
            {"name": "deadlines", "prompt": "Сроки.", "multiple": True},
        ]
    )
    text = meta_doc.to_yaml({"subject": "", "deadlines": []}, entities)
    assert "subject: ''" in text
    assert "deadlines: []" in text


def test_task_url_substitutes_the_placeholder():
    assert (
        meta_doc.task_url("https://tagilcity.planfix.com/task/<task-id>", "918659")
        == "https://tagilcity.planfix.com/task/918659"
    )


def test_task_url_appends_to_a_template_without_a_placeholder():
    """An operator who writes just the base gets the obvious result, not a dead link."""
    assert (
        meta_doc.task_url("https://tagilcity.planfix.com/task/", "918659")
        == "https://tagilcity.planfix.com/task/918659"
    )


def test_task_url_is_empty_without_a_template_or_a_task():
    assert meta_doc.task_url("", "918659") == ""
    assert meta_doc.task_url("https://x/task/<task-id>", "") == ""


def test_build_carries_the_task_url(config_with_folder):
    config = replace(
        config_with_folder, planfix_task_url="https://tagilcity.planfix.com/task/<task-id>"
    )
    document = _document(config)
    assert document["planfix_task_url"] == "https://tagilcity.planfix.com/task/918659"


def test_build_leaves_the_task_url_empty_when_unconfigured(config_with_folder):
    assert _document(config_with_folder)["planfix_task_url"] == ""


def test_video_url_is_the_browser_link():
    assert (
        meta_doc.video_url("FILE1") == "https://drive.google.com/file/d/FILE1/view"
    )


def test_video_url_is_empty_without_a_file():
    assert meta_doc.video_url("") == ""


def test_build_uses_the_shared_video_url(config_with_folder):
    """The document and the CLI must show the same link for the same recording."""
    assert _document(config_with_folder)["video_url"] == meta_doc.video_url("FILE1")
