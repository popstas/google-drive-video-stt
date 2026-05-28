from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.config import _load_deepgram_api_key
from src.stt.deepgram_provider import DeepgramProvider
from src.stt.deepgram_usage import fetch_request_cost_usd


def _require_live_env() -> tuple[str, Path]:
    if os.environ.get("RUN_DEEPGRAM_LIVE_TESTS") != "1":
        pytest.skip("set RUN_DEEPGRAM_LIVE_TESTS=1 to run Deepgram live smoke test")
    api_key = _load_deepgram_api_key(
        os.environ.get("DEEPGRAM_API_KEY", ""),
        os.environ.get("DEEPGRAM_API_KEY_FILE", ""),
    )
    if not api_key:
        pytest.skip("set DEEPGRAM_API_KEY or DEEPGRAM_API_KEY_FILE")
    audio_raw = os.environ.get("DEEPGRAM_LIVE_AUDIO_PATH", "").strip()
    if not audio_raw:
        pytest.skip("set DEEPGRAM_LIVE_AUDIO_PATH to a short audio/video file")
    audio_path = Path(audio_raw)
    if not audio_path.exists():
        pytest.skip(f"DEEPGRAM_LIVE_AUDIO_PATH does not exist: {audio_path}")
    return api_key, audio_path


def _short_audio_path(source: Path, tmp_dir: Path) -> Path:
    if source.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        return source
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to trim video input for the live smoke test")
    out = tmp_dir / f"{source.stem}-deepgram-smoke.mp3"
    cmd = [
        "ffmpeg",
        "-y",
        "-t",
        os.environ.get("DEEPGRAM_LIVE_SECONDS", "30"),
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        "96k",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    return out


def test_deepgram_live_smoke_prints_cost_estimate():
    api_key, source = _require_live_env()
    language = os.environ.get("DEEPGRAM_LIVE_LANGUAGE", "ru")
    proxy_url = os.environ.get("PROXY_URL", "").strip()

    with tempfile.TemporaryDirectory(prefix="deepgram-live-") as tmp:
        audio_path = _short_audio_path(source, Path(tmp))
        provider = DeepgramProvider(
            api_key=api_key,
            language=language,
            proxy_url=proxy_url,
        )

        transcript = provider.transcribe_full(audio_path)

    request_id = provider.last_request_id
    duration = provider.last_duration_seconds
    print(f"Deepgram request_id: {request_id or 'unknown'}")
    print(f"Deepgram duration seconds: {duration if duration is not None else 'unknown'}")
    preview = transcript[:500].replace("\n", " | ")
    print(f"Deepgram transcript preview: {preview}")
    if request_id:
        try:
            usd = fetch_request_cost_usd(
                api_key,
                request_id,
                proxy_url=proxy_url,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Deepgram cost unavailable yet for {request_id}: {exc}")
        else:
            if usd is None:
                print(f"Deepgram cost unavailable yet for {request_id}")
            else:
                print(f"Deepgram estimated request cost USD: {usd:.6f}")

    assert transcript
