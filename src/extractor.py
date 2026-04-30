from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


FFMPEG_TIMEOUT_SECONDS = 3600


class FFmpegError(RuntimeError):
    pass


def extract_mp3(mp4_path: Path, bitrate: str = "96k") -> Path:
    mp4_path = Path(mp4_path)
    if not mp4_path.exists():
        raise FileNotFoundError(f"Input file not found: {mp4_path}")

    if shutil.which("ffmpeg") is None:
        raise FFmpegError("ffmpeg binary not found in PATH")

    output_path = mp4_path.with_suffix(".mp3")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp4_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(output_path),
    ]

    logger.info("Running ffmpeg: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise FFmpegError("ffmpeg binary not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS}s: {mp4_path}"
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise FFmpegError(
            f"ffmpeg failed with exit code {result.returncode}: {stderr}"
        )

    if not output_path.exists():
        raise FFmpegError(f"ffmpeg succeeded but output not found: {output_path}")

    return output_path
