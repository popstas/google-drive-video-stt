from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from src import cli
from src.config import _load_deepgram_api_key

# Marked, network/credit end-to-end test. It is excluded from the default
# `uv run pytest` run: it stays skipped unless RUN_PRESET_DAG_E2E=1 is set, and it
# spends real Deepgram + OpenAI credits and reads a real Drive recording.
#
# It runs `gdstt process <file-id>` with output.target=folder pointing at a temp
# dir (no Drive writes), driving a preset DAG of
# `transcript-cleanup -> keypoints + expertizeme-managers`, and asserts the .txt
# transcript plus each enabled preset artifact were produced and that the two
# parallel branches (keypoints + expertizeme-managers) both ran.

# Drive id "Oksana and Andrei Smirnov" (~5.7 min, ~7.8 MB): a short
# two-named-speaker recording that exercises speaker-named presets cheaply.
DEFAULT_FILE_ID = "18czgPfHG3SWy8B8xCuKHBtCYqrME0sJC"

_CLEANUP_INSTRUCTIONS = (
    "Clean up the raw speaker-named transcript: fix obvious transcription typos, "
    "normalize whitespace, and keep the speaker labels. Return only the cleaned "
    "transcript in the same language, with no preamble."
)
_MANAGERS_INSTRUCTIONS = (
    "From the cleaned transcript, list concrete action items grouped per person "
    "responsible, in the transcript's own language. Return only a short Markdown "
    "list, no preamble."
)


def _require_e2e_env() -> tuple[str, Path]:
    if os.environ.get("RUN_PRESET_DAG_E2E") != "1":
        pytest.skip("set RUN_PRESET_DAG_E2E=1 to run the preset-DAG end-to-end test")
    data_dir_raw = os.environ.get("GDSTT_E2E_DATA_DIR", "").strip()
    if not data_dir_raw:
        pytest.skip("set GDSTT_E2E_DATA_DIR to a data dir with credentials.json + token.json")
    data_dir = Path(data_dir_raw)
    if not (data_dir / "token.json").exists():
        pytest.skip(f"GDSTT_E2E_DATA_DIR has no token.json: {data_dir}")
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        pytest.skip("set OPENAI_API_KEY for the preset-DAG end-to-end test")
    deepgram_key = _load_deepgram_api_key(
        os.environ.get("DEEPGRAM_API_KEY", ""),
        os.environ.get("DEEPGRAM_API_KEY_FILE", ""),
    )
    if not deepgram_key:
        pytest.skip("set DEEPGRAM_API_KEY or DEEPGRAM_API_KEY_FILE")
    return openai_key, data_dir


def test_preset_dag_end_to_end_writes_each_artifact(tmp_path, monkeypatch):
    openai_key, data_dir = _require_e2e_env()
    deepgram_key = _load_deepgram_api_key(
        os.environ.get("DEEPGRAM_API_KEY", ""),
        os.environ.get("DEEPGRAM_API_KEY_FILE", ""),
    )
    file_id = os.environ.get("GDSTT_E2E_FILE_ID", DEFAULT_FILE_ID)

    output_dir = tmp_path / "out"
    config = {
        "folder_ids": [],
        "data_dir": str(data_dir),
        "proxy_url": os.environ.get("PROXY_URL", "").strip(),
        "output": {"target": "folder", "dir": str(output_dir)},
        "stt": {
            "provider": "deepgram",
            "language": "ru",
            "postprocess": True,
            "deepgram": {"api_key": deepgram_key},
        },
        "openai": {
            "api_key": openai_key,
            "model": os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
            "max_parallel": 4,
        },
        "presets": {
            "transcript-cleanup": {"instructions": _CLEANUP_INSTRUCTIONS},
            "keypoints": {"depends_on": ["transcript-cleanup"]},
            "expertizeme-managers": {
                "depends_on": ["transcript-cleanup"],
                "instructions": _MANAGERS_INSTRUCTIONS,
            },
        },
    }
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("GDSTT_CONFIG", str(config_file))

    cli.main(["process", file_id])

    txt_files = list(output_dir.glob("*.txt"))
    assert len(txt_files) == 1, f"expected one transcript, got {txt_files}"
    assert txt_files[0].read_text(encoding="utf-8").strip()

    cleanup = list(output_dir.glob("*.transcript-cleanup.md"))
    keypoints = list(output_dir.glob("*.keypoints.md"))
    managers = list(output_dir.glob("*.expertizeme-managers.md"))

    assert len(cleanup) == 1, f"expected transcript-cleanup artifact, got {cleanup}"
    assert cleanup[0].read_text(encoding="utf-8").strip(), "transcript-cleanup output empty"
    # Both parallel branches fed by transcript-cleanup must have run.
    assert len(keypoints) == 1, f"expected keypoints artifact, got {keypoints}"
    assert len(managers) == 1, f"expected expertizeme-managers artifact, got {managers}"
