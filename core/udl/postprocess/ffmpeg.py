"""Locate and run FFmpeg for DASH video+audio merge.

Lookup order:
1. explicit path (--ffmpeg)
2. UDL_FFMPEG env
3. app-managed install dir (UDL_FFMPEG_DIR or platform default)
4. PATH
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# lowercase constants per project convention
ffmpeg_env = "UDL_FFMPEG"
ffmpeg_dir_env = "UDL_FFMPEG_DIR"
default_ffmpeg_name = "ffmpeg"


class ffmpeg_missing_error(RuntimeError):
    """raised when no usable FFmpeg binary can be found."""

    code = "ffmpeg_missing"


class merge_error(RuntimeError):
    """raised when FFmpeg merge fails."""

    code = "merge_failed"


def _is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return path.suffix.lower() in {".exe", ""} or path.name.lower().startswith("ffmpeg")
    return os.access(path, os.X_OK)


def managed_ffmpeg_candidates() -> list[Path]:
    """paths where the GUI / provisioner may install FFmpeg."""
    from udl.postprocess.ffmpeg_provision import default_install_dir, installed_ffmpeg_path

    return [installed_ffmpeg_path(default_install_dir())]


def find_ffmpeg(explicit: str | Path | None = None) -> Path:
    """return path to ffmpeg or raise ffmpeg_missing_error.

    An explicit path or UDL_FFMPEG must resolve; they do not fall back to PATH.
    """
    if explicit:
        path = Path(explicit)
        if _is_executable(path):
            return path.resolve()
        raise ffmpeg_missing_error(f"FFmpeg not found at {path}")

    env = os.environ.get(ffmpeg_env)
    if env:
        path = Path(env)
        if _is_executable(path):
            return path.resolve()
        raise ffmpeg_missing_error(f"FFmpeg not found at {ffmpeg_env}={env}")

    for candidate in managed_ffmpeg_candidates():
        if _is_executable(candidate):
            return candidate.resolve()

    which = shutil.which(default_ffmpeg_name)
    if which:
        path = Path(which)
        if _is_executable(path):
            return path.resolve()

    raise ffmpeg_missing_error(
        "FFmpeg not found — install it on PATH, set UDL_FFMPEG, "
        "pass --ffmpeg, or use the app first-run download"
    )


def merge_dash(
    video_path: Path,
    audio_path: Path | None,
    output_path: Path,
    *,
    ffmpeg: str | Path | None = None,
    overwrite: bool = True,
    chapters_path: Path | None = None,
    subtitle_paths: list[tuple[Path, str | None]] | None = None,
) -> Path:
    """merge DASH video (+ optional audio) into output with stream copy.

    Uses `-c copy -strict unofficial` so Dolby Vision / unusual boxes
    remux without re-encode (same pattern as Bili23-Downloader).

    Optional chapters (ffmetadata) and ASS subtitle tracks for mkv/mp4.
    """
    binary = find_ffmpeg(ffmpeg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(binary)]
    if overwrite:
        cmd.append("-y")
    cmd.extend(["-i", str(video_path)])
    input_count = 1
    if audio_path is not None:
        cmd.extend(["-i", str(audio_path)])
        input_count += 1
    if chapters_path is not None and chapters_path.is_file():
        cmd.extend(["-i", str(chapters_path)])
        input_count += 1
    subtitle_paths = subtitle_paths or []
    sub_start = input_count
    for sub_path, _lang in subtitle_paths:
        cmd.extend(["-i", str(sub_path)])
        input_count += 1

    # map streams
    cmd.extend(["-map", "0:v:0?"])
    if audio_path is not None:
        cmd.extend(["-map", "1:a:0?"])
    for i, (_path, _lang) in enumerate(subtitle_paths):
        cmd.extend(["-map", f"{sub_start + i}:0?"])

    cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", "copy", "-strict", "unofficial"])
    if chapters_path is not None and chapters_path.is_file():
        # chapters input is the one after audio (or after video if no audio)
        chap_idx = 2 if audio_path is not None else 1
        cmd.extend(["-map_metadata", str(chap_idx)])
    for i, (_path, lang) in enumerate(subtitle_paths):
        if lang:
            cmd.extend([f"-metadata:s:s:{i}", f"language={lang}"])
    # mp4 soft-subs need mov_text; mkv keeps ass
    if output_path.suffix.lower() == ".mp4" and subtitle_paths:
        cmd.extend(["-c:s", "mov_text"])
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
        # retry without subtitle embedding if soft-sub failed
        if subtitle_paths:
            return merge_dash(
                video_path,
                audio_path,
                output_path,
                ffmpeg=ffmpeg,
                overwrite=overwrite,
                chapters_path=chapters_path,
                subtitle_paths=None,
            )
        detail = (proc.stderr or proc.stdout or "").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise merge_error(
            f"FFmpeg merge failed (exit {proc.returncode}): {detail or 'no output'}"
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise merge_error(f"FFmpeg produced empty output: {output_path}")
    return output_path
