from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Config
from src.openai_pipeline import (
    OpenAIPipeline,
    _content_to_text,
    build_prompt,
    get_pipeline,
    refine_transcript,
)
from src.stt.base import STTError


def _config(**over) -> Config:
    base = dict(
        folder_ids=[],
        poll_interval=600,
        bitrate="96k",
        telegram_bot_token="",
        telegram_chat_id="",
        data_dir=Path("data"),
        proxy_url="",
        stt_provider="",
        openai_api_key="sk-test",
        deepgram_api_key="",
        google_cloud_project="",
        google_stt_gcs_bucket="",
        asr_url="",
        stt_language="",
        stt_chunk_seconds=600,
    )
    base.update(over)
    return Config(**base)


# --- prompt ----------------------------------------------------------------

def test_build_prompt_includes_names():
    prompt = build_prompt("Speaker 1: hi", "Alice and Bob - 2026/05/28.mp4")
    assert "Alice" in prompt
    assert "Bob" in prompt
    assert "Speaker 1: hi" in prompt


def test_build_prompt_prefers_explicit_speaker_names():
    prompt = build_prompt(
        "Speaker 1: hi",
        "Wrong One and Wrong Two - 2026-05-30.mp4",
        speaker_names=["Alice", "Bob"],
    )

    assert "Alice, Bob" in prompt
    assert "Wrong One" not in prompt
    assert "Wrong Two" not in prompt


def test_build_prompt_without_names():
    # A date-only name yields no extractable interlocutor names.
    prompt = build_prompt("Speaker 1: hi", "2026-05-28.mp4")
    assert "unknown" in prompt.lower()


# --- construction ----------------------------------------------------------

def test_pipeline_requires_api_key():
    with pytest.raises(STTError, match="OPENAI_API_KEY"):
        OpenAIPipeline(api_key="")


def test_empty_transcript_skips_api_call():
    pipeline = OpenAIPipeline(api_key="sk-test")

    def _boom():
        raise AssertionError("client should not be built for empty transcript")

    pipeline._get_client = _boom  # type: ignore[assignment]
    assert pipeline.refine("   \n  ", "file.mp4") == ""


# --- sync path -------------------------------------------------------------

def test_refine_sync_uses_output_text():
    captured: dict = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text="Alice: hi\nBob: hello")

    pipeline = OpenAIPipeline(api_key="sk-test", model="gpt-5.4-mini")
    pipeline._client = SimpleNamespace(responses=SimpleNamespace(create=create))

    out = pipeline.refine("Speaker 1: hi\nSpeaker 2: hello", "Alice and Bob.mp4")
    assert out == "Alice: hi\nBob: hello"
    assert captured["model"] == "gpt-5.4-mini"
    assert "Speaker 1: hi" in captured["input"]


def test_refine_sync_walks_output_list():
    response = SimpleNamespace(
        output_text=None,
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(text="Alice: hi "),
                    SimpleNamespace(text="there"),
                ]
            )
        ],
    )
    pipeline = OpenAIPipeline(api_key="sk-test")
    pipeline._client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kw: response)
    )
    assert pipeline.refine("Speaker 1: hi there", "f.mp4") == "Alice: hi there"


def test_refine_sync_error_is_wrapped():
    def boom(**kwargs):
        raise RuntimeError("network down")

    pipeline = OpenAIPipeline(api_key="sk-test")
    pipeline._client = SimpleNamespace(responses=SimpleNamespace(create=boom))
    with pytest.raises(STTError, match="OpenAI post-processing failed"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


def test_refine_sync_unexpected_response_raises():
    pipeline = OpenAIPipeline(api_key="sk-test")
    pipeline._client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kw: SimpleNamespace())
    )
    with pytest.raises(STTError, match="unexpected response"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


# --- batch path ------------------------------------------------------------

def _fake_batch_client(output_jsonl: str, *, status: str = "completed", calls=None):
    calls = calls if calls is not None else {}

    def files_create(**kwargs):
        calls["upload"] = kwargs
        return SimpleNamespace(id="file-in")

    def batches_create(**kwargs):
        calls["batch"] = kwargs
        return SimpleNamespace(id="batch-1")

    def batches_retrieve(batch_id):
        calls.setdefault("retrieve", []).append(batch_id)
        return SimpleNamespace(status=status, output_file_id="file-out")

    def files_content(file_id):
        calls["content"] = file_id
        return output_jsonl.encode("utf-8")

    return SimpleNamespace(
        files=SimpleNamespace(create=files_create, content=files_content),
        batches=SimpleNamespace(create=batches_create, retrieve=batches_retrieve),
    )


def test_refine_batch_round_trip():
    line = json.dumps(
        {
            "custom_id": "transcript-0",
            "response": {"status_code": 200, "body": {"output_text": "Alice: hi"}},
        }
    )
    calls: dict = {}
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client(line + "\n", calls=calls)

    out = pipeline.refine("Speaker 1: hi", "Alice and Bob.mp4")
    assert out == "Alice: hi"
    assert calls["batch"]["endpoint"] == "/v1/responses"
    assert calls["content"] == "file-out"


def test_refine_batch_parses_output_list_body():
    line = json.dumps(
        {
            "custom_id": "transcript-0",
            "response": {
                "status_code": 200,
                "body": {
                    "output": [
                        {"content": [{"type": "output_text", "text": "Bob: yo"}]}
                    ]
                },
            },
        }
    )
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client(line + "\n")
    assert pipeline.refine("Speaker 2: yo", "f.mp4") == "Bob: yo"


def test_refine_batch_failed_status_raises():
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client("", status="failed")
    with pytest.raises(STTError, match="status 'failed'"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


def test_refine_batch_empty_output_raises():
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client("\n")
    with pytest.raises(STTError, match="no transcript text"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


def test_refine_batch_non_200_status_raises():
    line = json.dumps(
        {
            "custom_id": "transcript-0",
            "response": {"status_code": 500, "body": {"output_text": "leaked"}},
        }
    )
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client(line + "\n")
    with pytest.raises(STTError, match="HTTP 500"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


def test_refine_batch_line_error_raises():
    line = json.dumps({"custom_id": "transcript-0", "error": {"message": "boom"}})
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client(line + "\n")
    with pytest.raises(STTError, match="batch request failed"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


def test_refine_batch_malformed_json_raises():
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=0)
    pipeline._client = _fake_batch_client("not json\n")
    with pytest.raises(STTError, match="not valid JSON"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


def _fake_batch_client_statuses(statuses, output_jsonl, *, calls=None):
    """Batch client whose `retrieve` walks `statuses` then sticks on the last one."""
    calls = calls if calls is not None else {}
    seq = list(statuses)

    def files_create(**kwargs):
        return SimpleNamespace(id="file-in")

    def batches_create(**kwargs):
        return SimpleNamespace(id="batch-1")

    def batches_retrieve(batch_id):
        calls.setdefault("retrieve", []).append(batch_id)
        idx = min(len(calls["retrieve"]) - 1, len(seq) - 1)
        return SimpleNamespace(status=seq[idx], output_file_id="file-out")

    def files_content(file_id):
        return output_jsonl.encode("utf-8")

    return SimpleNamespace(
        files=SimpleNamespace(create=files_create, content=files_content),
        batches=SimpleNamespace(create=batches_create, retrieve=batches_retrieve),
    )


def test_refine_batch_polls_until_completed(monkeypatch):
    monkeypatch.setattr("src.openai_pipeline.time.sleep", lambda _s: None)
    line = json.dumps(
        {"response": {"status_code": 200, "body": {"output_text": "Alice: hi"}}}
    )
    calls: dict = {}
    pipeline = OpenAIPipeline(api_key="sk-test", use_batch=True, poll_interval=1)
    pipeline._client = _fake_batch_client_statuses(
        ["in_progress", "in_progress", "completed"], line + "\n", calls=calls
    )

    assert pipeline.refine("Speaker 1: hi", "f.mp4") == "Alice: hi"
    assert len(calls["retrieve"]) == 3


def test_refine_batch_times_out(monkeypatch):
    monkeypatch.setattr("src.openai_pipeline.time.sleep", lambda _s: None)
    pipeline = OpenAIPipeline(
        api_key="sk-test", use_batch=True, poll_interval=1, batch_timeout=2
    )
    pipeline._client = _fake_batch_client_statuses(["in_progress"], "")
    with pytest.raises(STTError, match="timed out"):
        pipeline.refine("Speaker 1: hi", "f.mp4")


# --- files.content shape normalization -------------------------------------

@pytest.mark.parametrize(
    "content",
    [
        b"line",
        "line",
        SimpleNamespace(text="line"),
        SimpleNamespace(read=lambda: b"line"),
        SimpleNamespace(read=lambda: "line"),
    ],
)
def test_content_to_text_handles_shapes(content):
    assert _content_to_text(content) == "line"


# --- config integration ----------------------------------------------------

def test_get_pipeline_reads_config():
    pipeline = get_pipeline(_config(openai_model="gpt-5.4", openai_batch=True))
    assert pipeline._model == "gpt-5.4"
    assert pipeline._use_batch is True


def test_refine_transcript_uses_configured_pipeline(monkeypatch):
    def fake_get_client(self):
        return SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(output_text="Alice: hi")
            )
        )

    monkeypatch.setattr(OpenAIPipeline, "_get_client", fake_get_client)
    out = refine_transcript("Speaker 1: hi", "Alice and Bob.mp4", _config())
    assert out == "Alice: hi"
