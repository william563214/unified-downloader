"""Locate and run FFmpeg for DASH video+audio merge.

FFmpeg is expected on PATH or via an explicit path / UDL_FFMPEG env.
First-run app download of a bundled binary is deferred — emit a clear
ffmpeg_missing error until then.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# lowercase constants per project convention
ffmpeg_env = "UDL_FFMPEG"
default_ffmpeg_name = "ffmpeg"


class ffmpeg_missing_error(RuntimeError):
    """raised when no usable FFmpeg binary can be found."""

    code = "ffmpeg_missing"


class merge_error(RuntimeError):
    """raised when FFmpeg merge fails."""

    code = "merge_failed"


def find_ffmpeg(explicit: str | Path | None = None) -> Path:
    """return path to ffmpeg or raise ffmpeg_missing_error.

    An explicit path or UDL_FFMPEG must resolve; they do not fall back to PATH.
    """
    if explicit:
        path = Path(explicit)
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        raise ffmpeg_missing_error(f"FFmpeg not found at {path}")

    env = os.environ.get(ffmpeg_env)
    if env:
        path = Path(env)
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        raise ffmpeg_missing_error(f"FFmpeg not found at {ffmpeg_env}={env}")

    which = shutil.which(default_ffmpeg_name)
    if which:
        path = Path(which)
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()

    raise ffmpeg_missing_error(
        "FFmpeg not found — install it on PATH, set UDL_FFMPEG, "
        "or pass --ffmpeg (bundled download planned for a later release)"
    )


def merge_dash(
    video_path: Path,
    audio_path: Path | None,
    output_path: Path,
    *,
    ffmpeg: str | Path | None = None,
    overwrite: bool = True,
) -> Path:
    """merge DASH video (+ optional audio) into output with stream copy.

    Uses `-c copy -strict unofficial` so Dolby Vision / unusual boxes
    remux without re-encode (same pattern as Bili23-Downloader).
    """
    binary = find_ffmpeg(ffmpeg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(binary)]
    if overwrite:
        cmd.append("-y")
    cmd.extend(["-i", str(video_path)])
    if audio_path is not None:
        cmd.extend(["-i", str(audio_path)])
        cmd.extend(["-c:v", "copy", "-c:a", "copy", "-strict", "unofficial"])
    else:
        cmd.extend(["-c", "copy", "-strict", "unofficial"])
    cmd.append(str(output_path))

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise merge_error(f"failed to run FFmpeg: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise merge_error(
            f"FFmpeg merge failed (exit {proc.returncode}): {detail or 'no output'}"
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise merge_error(f"FFmpeg produced empty output: {output_path}")
    return output_path
