from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src import meta_doc
from src.config import Config, EmployeeFolder
from src.meta import Meta

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


def _document(config, **overrides):
    kwargs = {
        "meta": Meta(subject="Обсудили кейс", tags=("O-1",), referral="рекомендация",
                     referral_note="Посоветовала знакомая"),
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


def test_build_reads_the_date_from_the_recording_name(config_with_folder):
    assert _document(config_with_folder)["date"] == "2026-08-13T12:29:00+00:00"


def test_build_leaves_the_date_empty_when_the_name_has_no_timestamp(config_with_folder):
    assert _document(config_with_folder, file_name="Recording.mp4")["date"] == ""


def test_build_takes_the_duration_from_the_last_timestamp(config_with_folder):
    assert _document(config_with_folder)["duration"] == "00:31:42"


def test_build_leaves_the_duration_empty_without_timestamps(config_with_folder):
    assert _document(config_with_folder, transcript="Speaker 1: привет")["duration"] == ""


def test_build_keeps_every_field_present_when_the_model_returned_nothing(config_with_folder):
    document = _document(config_with_folder, meta=Meta())
    assert set(document) == set(meta_doc.FIELD_ORDER)
    assert document["subject"] == ""
    assert document["tags"] == []


def test_to_yaml_round_trips_and_keeps_the_declared_order(config_with_folder):
    text = meta_doc.to_yaml(_document(config_with_folder))
    assert list(yaml.safe_load(text)) == list(meta_doc.FIELD_ORDER)
    assert "Обсудили кейс" in text
