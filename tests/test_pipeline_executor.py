from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import drive
from src import pipeline_executor
from src.pipeline_profile import load_pipeline_profile
from tests.test_main import make_config


def test_execute_process_stops_before_building_drive_service_when_key_is_missing():
    service_builder = MagicMock()

    result = pipeline_executor.execute_process(
        {"action": "process", "targets": ["file-1"]},
        make_config(),
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg"},
        service_builder=service_builder,
    )

    assert result["status"] == "configuration_required"
    assert result["missing"] == ["OPENAI_API_KEY"]
    service_builder.assert_not_called()


def test_execute_process_requires_confirmation_before_folder_processing():
    service_builder = MagicMock()

    result = pipeline_executor.execute_process(
        {
            "action": "process",
            "targets": ["folder-1"],
            "target_type": "folder",
        },
        make_config(),
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
        service_builder=service_builder,
    )

    assert result["status"] == "confirmation_required"
    assert result["confirmation_reasons"] == ["folder_wide"]
    service_builder.assert_not_called()


def test_execute_process_requires_confirmation_when_auto_target_is_folder(mocker):
    service = MagicMock()
    service_builder = MagicMock(return_value=service)
    mocker.patch(
        "src.pipeline_executor.drive.get_file_metadata",
        return_value={"mimeType": drive.FOLDER_MIME},
    )
    process_mock = mocker.patch("src.pipeline_executor.main_module.process_target")

    result = pipeline_executor.execute_process(
        {"action": "process", "targets": ["folder-1"]},
        make_config(),
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
        service_builder=service_builder,
    )

    assert result["status"] == "confirmation_required"
    assert result["confirmation_reasons"] == ["folder_wide"]
    service_builder.assert_called_once()
    process_mock.assert_not_called()


def test_execute_process_routes_speaker_metadata_and_existing_runtime(mocker):
    service = MagicMock()
    service_builder = MagicMock(return_value=service)
    speaker_mock = mocker.patch("src.pipeline_executor.drive.set_file_app_properties")
    mocker.patch(
        "src.pipeline_executor.drive.get_file_metadata",
        return_value={"mimeType": drive.MP4_MIME},
    )
    process_mock = mocker.patch(
        "src.pipeline_executor.main_module.process_target",
        return_value=[
            SimpleNamespace(
                txt_uploaded=True,
                mp3_uploaded=False,
                cost_usd={"deepgram": 0.012345, "openai": None},
                usage={
                    "openai": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 30,
                        "reasoning_tokens": 5,
                        "total_tokens": 130,
                    }
                },
            ),
            SimpleNamespace(
                txt_uploaded=True,
                mp3_uploaded=False,
                cost_usd={"deepgram": 0.01, "openai": None},
                usage={
                    "openai": {
                        "input_tokens": 50,
                        "cached_input_tokens": 0,
                        "output_tokens": 10,
                        "reasoning_tokens": 2,
                        "total_tokens": 60,
                    }
                },
            ),
        ],
    )

    result = pipeline_executor.execute_process(
        {
            "action": "process",
            "targets": ["file-1"],
            "target_type": "file",
            "overrides": {"speakers": ["Alice", "Bob"]},
        },
        make_config(),
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
        service_builder=service_builder,
    )

    speaker_mock.assert_called_once()
    assert speaker_mock.call_args.args[:2] == (service, "file-1")
    process_mock.assert_called_once()
    runtime_cfg = process_mock.call_args.args[2]
    assert runtime_cfg.stt_provider == "deepgram"
    assert runtime_cfg.openai_postprocess is True
    assert runtime_cfg.drive_mp3_artifact is False
    assert result == {
        "status": "completed",
        "files": [
            {
                "id": "file-1",
                "txt_uploaded": True,
                "mp3_uploaded": False,
                "speakers": ["Alice", "Bob"],
                "cost_usd": {"deepgram": 0.022345, "openai": None},
                "usage": {
                    "openai": {
                        "input_tokens": 150,
                        "cached_input_tokens": 20,
                        "output_tokens": 40,
                        "reasoning_tokens": 7,
                        "total_tokens": 190,
                    }
                },
            }
        ],
    }


def test_execute_process_reports_no_uploads_when_target_is_already_complete(mocker):
    service = MagicMock()
    service_builder = MagicMock(return_value=service)
    mocker.patch("src.pipeline_executor.main_module.process_target", return_value=[])

    result = pipeline_executor.execute_process(
        {
            "action": "process",
            "targets": ["file-1"],
            "target_type": "file",
        },
        make_config(),
        load_pipeline_profile(),
        env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
        service_builder=service_builder,
    )

    assert result["files"][0]["txt_uploaded"] is False
    assert result["files"][0]["mp3_uploaded"] is False


def test_execute_process_rejects_speaker_override_when_auto_target_is_folder(mocker):
    service = MagicMock()
    service_builder = MagicMock(return_value=service)
    mocker.patch(
        "src.pipeline_executor.drive.get_file_metadata",
        return_value={"mimeType": drive.FOLDER_MIME},
    )
    speaker_mock = mocker.patch("src.pipeline_executor.drive.set_file_app_properties")
    process_mock = mocker.patch("src.pipeline_executor.main_module.process_target")

    with pytest.raises(ValueError, match="overrides.speakers requires file targets"):
        pipeline_executor.execute_process(
            {
                "action": "process",
                "targets": ["folder-1"],
                "overrides": {"speakers": ["Alice", "Bob"]},
            },
            make_config(),
            load_pipeline_profile(),
            env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
            service_builder=service_builder,
        )

    speaker_mock.assert_not_called()
    process_mock.assert_not_called()


def test_execute_process_rejects_speaker_override_for_explicit_non_mp4(mocker):
    service = MagicMock()
    service_builder = MagicMock(return_value=service)
    mocker.patch(
        "src.pipeline_executor.drive.get_file_metadata",
        return_value={"mimeType": "text/plain"},
    )
    speaker_mock = mocker.patch("src.pipeline_executor.drive.set_file_app_properties")
    process_mock = mocker.patch("src.pipeline_executor.main_module.process_target")

    with pytest.raises(ValueError, match="overrides.speakers requires Drive MP4"):
        pipeline_executor.execute_process(
            {
                "action": "process",
                "targets": ["not-an-mp4"],
                "target_type": "file",
                "overrides": {"speakers": ["Alice", "Bob"]},
            },
            make_config(),
            load_pipeline_profile(),
            env={"DEEPGRAM_API_KEY": "dg", "OPENAI_API_KEY": "sk"},
            service_builder=service_builder,
        )

    speaker_mock.assert_not_called()
    process_mock.assert_not_called()
