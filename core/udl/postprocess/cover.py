"""download cover / thumbnail images and optionally embed into media containers."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Event
from typing import Literal
from urllib.parse import urlparse

from udl.download.http import download_file, download_item, download_options
from udl.events import emitter
from udl.metadata import item_metadata
from udl.postprocess.ffmpeg import ffmpeg_missing_error, find_ffmpeg

# lowercase constants per project convention
cover_naming = Literal["poster", "folder"]
image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
video_exts = {".mp4", ".m4v", ".mkv", ".webm", ".mov"}
site_referers: dict[str, str] = {
    "bilibili": "https://www.bilibili.com/",
    "iwara": "https://www.iwara.tv/",
    "bunkr": "https://bunkr.cr/",
    "gofile": "https://gofile.io/",
    "mega": "https://mega.nz/",
}


class cover_error(RuntimeError):
    """raised when cover download or embed fails."""

    code = "cover_failed"


def cover_image_url(meta: item_metadata) -> str | None:
    """prefer cover_url then thumbnail_url."""
    for value in (meta.cover_url, meta.thumbnail_url):
        if value and str(value).strip():
            return str(value).strip()
    return None


def cover_headers_for_site(site: str, *, page_url: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    referer = site_referers.get(site)
    if page_url:
        headers["Referer"] = page_url
    elif referer:
        headers["Referer"] = referer
    return headers


def cover_dest_path(
    media_path: Path,
    *,
    naming: cover_naming = "poster",
    force_jpg: bool = True,
) -> Path:
    """path for on-disk cover next to media or as folder.jpg."""
    ext = ".jpg" if force_jpg else ".img"
    if naming == "folder":
        return media_path.parent / f"folder{ext}"
    return media_path.parent / f"{media_path.stem}-poster{ext}"


def _guess_ext_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in image_exts:
        return suffix
    return ".img"


def _convert_to_jpg(src: Path, dest: Path, *, ffmpeg: str | Path | None = None) -> Path:
    """convert any image (incl webp) to jpeg via FFmpeg."""
    if src.suffix.lower() in {".jpg", ".jpeg"} and src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
        return dest
    if src.suffix.lower() in {".jpg", ".jpeg"} and src.resolve() == dest.resolve():
        return dest

    binary = find_ffmpeg(ffmpeg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        "-y",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(dest),
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise cover_error(f"failed to convert cover with FFmpeg: {exc}") from exc
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if len(detail) > 400:
            detail = detail[-400:]
        raise cover_error(f"cover convert failed: {detail or 'no output'}")
    return dest


def download_cover(
    meta: item_metadata,
    media_path: Path,
    *,
    naming: cover_naming = "poster",
    ffmpeg: str | Path | None = None,
    job_id: str | None = None,
    file_id: str | None = None,
    emit: emitter | None = None,
    cancel: Event | None = None,
    headers: dict[str, str] | None = None,
) -> Path | None:
    """fetch cover/thumbnail and save as poster or folder.jpg. returns path or None."""
    _ = emit
    url = cover_image_url(meta)
    if not url:
        return None

    dest = cover_dest_path(media_path, naming=naming, force_jpg=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req_headers = cover_headers_for_site(meta.site, page_url=meta.canonical_url)
    if headers:
        req_headers.update(headers)

    tmp_dir = Path(tempfile.mkdtemp(prefix="udl-cover-"))
    try:
        raw_ext = _guess_ext_from_url(url)
        raw_path = tmp_dir / f"cover{raw_ext}"
        opts = download_options(
            output_dir=tmp_dir,
            workers=1,
            retries=3,
            cancel=cancel,
        )
        item = download_item(
            url=url,
            dest=raw_path,
            file_id=file_id or f"{job_id or 'cover'}:cover",
            name=raw_path.name,
            headers=req_headers,
        )
        # avoid emitting progress storms for tiny images unless caller wants file events
        download_file(
            item,
            opts,
            job_id=job_id or "cover",
            emit=None,
        )
        if not raw_path.is_file() or raw_path.stat().st_size == 0:
            raise cover_error(f"empty cover download from {url}")

        try:
            return _convert_to_jpg(raw_path, dest, ffmpeg=ffmpeg)
        except (ffmpeg_missing_error, cover_error):
            # fall back: keep original bytes with correct extension if convert impossible
            if raw_ext in {".jpg", ".jpeg", ".png"} and naming == "poster":
                fallback = media_path.parent / f"{media_path.stem}-poster{raw_ext}"
                shutil.copy2(raw_path, fallback)
                return fallback
            if raw_ext in {".jpg", ".jpeg", ".png"} and naming == "folder":
                fallback = media_path.parent / f"folder{raw_ext}"
                shutil.copy2(raw_path, fallback)
                return fallback
            raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def embed_cover(
    media_path: Path,
    cover_path: Path,
    *,
    ffmpeg: str | Path | None = None,
    overwrite: bool = True,
) -> Path:
    """embed cover into mp4 (attached_pic) or mkv (attachment). returns media path."""
    if not media_path.is_file():
        raise cover_error(f"media not found: {media_path}")
    if not cover_path.is_file():
        raise cover_error(f"cover not found: {cover_path}")

    suffix = media_path.suffix.lower()
    if suffix not in video_exts:
        raise cover_error(f"unsupported container for cover embed: {suffix}")

    binary = find_ffmpeg(ffmpeg)
    tmp_out = media_path.with_name(media_path.stem + ".udl-cover" + media_path.suffix)
    cmd = [str(binary)]
    if overwrite:
        cmd.append("-y")

    if suffix == ".mkv":
        # matroska attachment
        cmd.extend(
            [
                "-i",
                str(media_path),
                "-attach",
                str(cover_path),
                "-metadata:s:t",
                "mimetype=image/jpeg",
                "-metadata:s:t",
                "filename=cover.jpg",
                "-c",
                "copy",
                "-map",
                "0",
                str(tmp_out),
            ]
        )
    else:
        # mp4 / m4v / mov / webm — attached pic stream
        cmd.extend(
            [
                "-i",
                str(media_path),
                "-i",
                str(cover_path),
                "-map",
                "0",
                "-map",
                "1",
                "-c",
                "copy",
                "-c:v:1",
                "mjpeg",
                "-disposition:v:1",
                "attached_pic",
                str(tmp_out),
            ]
        )

    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise cover_error(f"failed to run FFmpeg for cover embed: {exc}") from exc

    if proc.returncode != 0 or not tmp_out.is_file() or tmp_out.stat().st_size == 0:
        tmp_out.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise cover_error(
            f"FFmpeg cover embed failed (exit {proc.returncode}): {detail or 'no output'}"
        )

    tmp_out.replace(media_path)
    return media_path
