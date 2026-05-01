from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SECONDS = 3600


class ChunkError(RuntimeError):
    pass


def chunk_mp3(mp3_path: Path, chunk_seconds: int, out_dir: Path) -> list[Path]:
    """Split mp3_path into ~chunk_seconds segments via ffmpeg, return chunk paths in order."""
    mp3_path = Path(mp3_path)
    out_dir = Path(out_dir)
    if not mp3_path.exists():
        raise FileNotFoundError(f"Input file not found: {mp3_path}")
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be positive, got: {chunk_seconds}")
    if shutil.which("ffmpeg") is None:
        raise ChunkError("ffmpeg binary not found in PATH")

    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{mp3_path.stem}_chunk_%04d.mp3"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp3_path),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-c",
        "copy",
        str(pattern),
    ]

    logger.info("Running ffmpeg chunker: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ChunkError("ffmpeg binary not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ChunkError(
            f"ffmpeg chunker timed out after {FFMPEG_TIMEOUT_SECONDS}s: {mp3_path}"
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ChunkError(
            f"ffmpeg chunker failed with exit code {result.returncode}: {stderr}"
        )

    chunks = sorted(out_dir.glob(f"{mp3_path.stem}_chunk_*.mp3"))
    if not chunks:
        raise ChunkError(f"ffmpeg chunker produced no output for {mp3_path}")
    return chunks
