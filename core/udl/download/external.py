"""External fragment downloaders with the same call surface as native.

aria2c and ffmpeg are invoked as subprocesses (not linked). Optional aria2
JSON-RPC remains in ``udl.download.aria2`` for whole-file downloads.

Adapted from yt-dlp ``yt_dlp/downloader/external.py`` (Unlicense
commit 8973412). Supports only unencrypted http(s) fragment lists and
plain m3u8 / dash URLs via ffmpeg — no DRM or encrypted-stream bypass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from udl.download.fragment import (
    encrypted_stream_error,
    fragment_error,
    fragment_options,
    media_fragment,
    reject_encrypted_markers,
)
from udl.events import emitter
from udl.network import active_network, build_proxy_url, external_env
from udl.postprocess.ffmpeg import ffmpeg_missing_error, find_ffmpeg

# lowercase constants
external_backends = ("native", "aria2c", "ffmpeg")
default_aria2c_name = "aria2c"
default_ffmpeg_name = "ffmpeg"


class external_downloader_error(fragment_error):
    """external downloader failure."""

    code = "external_downloader_failed"


@dataclass
class external_downloader_info:
    """availability probe result."""

    name: str
    available: bool
    path: str | None = None
    detail: str | None = None


class external_downloader(ABC):
    """common interface for native-compatible external backends."""

    name: str = "external"

    @classmethod
    @abstractmethod
    def available(cls, path: str | None = None) -> str | None:
        """return resolved executable path or None."""

    @classmethod
    def supports_fragments(cls) -> bool:
        return True

    @classmethod
    def supports_manifest_url(cls) -> bool:
        return False

    @abstractmethod
    def download(
        self,
        fragments: list[media_fragment] | None,
        dest: Path,
        *,
        job_id: str,
        file_id: str,
        opts: fragment_options,
        emit: emitter | None = None,
        source: str = "",
        manifest_url: str | None = None,
    ) -> Path:
        """download fragments or a manifest URL into dest."""


def _which(name: str, explicit: str | None = None) -> str | None:
    if explicit:
        path = Path(explicit)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        return None
    found = shutil.which(name)
    return found


def _aria2c_filename(fn: str) -> str:
    # aria2c strips leading/trailing spaces; mirror yt-dlp workaround
    return fn if os.path.isabs(fn) else f".{os.path.sep}{fn}"


class aria2c_downloader(external_downloader):
    """subprocess aria2c for single URLs or fragment URL lists."""

    name = "aria2c"

    def __init__(self, exe: str | None = None) -> None:
        resolved = self.available(exe)
        if not resolved:
            raise external_downloader_error(
                "aria2c not found on PATH; install aria2 or set UDL_ARIA2C"
            )
        self.exe = resolved

    @classmethod
    def available(cls, path: str | None = None) -> str | None:
        explicit = path or os.environ.get("UDL_ARIA2C")
        return _which(default_aria2c_name, explicit)

    def download(
        self,
        fragments: list[media_fragment] | None,
        dest: Path,
        *,
        job_id: str,
        file_id: str,
        opts: fragment_options,
        emit: emitter | None = None,
        source: str = "",
        manifest_url: str | None = None,
    ) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            if emit:
                emit.done(job=job_id, file=file_id, path=str(dest))
            return dest

        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists():
            tmp.unlink()

        headers: dict[str, str] = {}
        if opts.headers:
            headers.update(opts.headers)
        net = active_network()
        if net.user_agent and "User-Agent" not in headers and "user-agent" not in {
            k.lower() for k in headers
        }:
            headers["User-Agent"] = net.user_agent

        cmd = [
            self.exe,
            "--no-conf",
            "--auto-save-interval=10",
            "--console-log-level=warn",
            "--summary-interval=0",
            "--download-result=hide",
            "--http-accept-gzip=true",
            "--file-allocation=none",
            "-x16",
            "-j16",
            "-s16",
            "--allow-overwrite=true",
            "--always-resume=false",
            "--auto-file-renaming=false",
            "--force-save=false",
            "--show-console-readout=false",
        ]
        for key, val in headers.items():
            cmd.extend(["--header", f"{key}: {val}"])
        if opts.speed_limit_bps and opts.speed_limit_bps > 0:
            cmd.extend(["--max-overall-download-limit", str(opts.speed_limit_bps)])
        proxy = build_proxy_url(net)
        if proxy:
            cmd.extend(["--all-proxy", proxy])

        dn = str(tmp.parent.resolve())
        cmd.extend(["--dir", _aria2c_filename(dn) + os.path.sep])

        url_list_file: Path | None = None
        if fragments:
            cmd.extend(
                [
                    "--min-split-size=1M",
                    "--allow-piece-length-change=true",
                    "--uri-selector=inorder",
                ]
            )
            url_list_file = tmp.with_name(tmp.name + ".frag.urls")
            lines: list[str] = []
            for frag in fragments:
                if frag.meta.get("encrypted") or frag.meta.get("decrypt_info"):
                    raise encrypted_stream_error(
                        "encrypted fragment refused; udl does not decrypt streams"
                    )
                frag_name = f"{tmp.name}-Frag{frag.index}"
                lines.append(f"{frag.url}\n\tout={_aria2c_filename(frag_name)}")
            url_list_file.write_bytes("\n".join(lines).encode())
            cmd.extend(["-i", _aria2c_filename(str(url_list_file))])
        else:
            url = manifest_url or source
            if not url:
                raise fragment_error("aria2c requires fragments or a URL")
            cmd.extend(
                [
                    "--min-split-size",
                    "1M",
                    "--out",
                    _aria2c_filename(tmp.name),
                    "--",
                    url,
                ]
            )

        if emit:
            emit.progress(job=job_id, file=file_id, bytes=0, total=None, bps=0.0)

        env = external_env(net)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                cwd=str(tmp.parent),
            )
        except OSError as exc:
            raise external_downloader_error(f"failed to run aria2c: {exc}") from exc
        finally:
            if url_list_file is not None:
                url_list_file.unlink(missing_ok=True)

        if opts.cancel is not None and opts.cancel.is_set():
            raise InterruptedError("cancelled")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            if len(detail) > 600:
                detail = detail[-600:]
            raise external_downloader_error(
                f"aria2c exited {proc.returncode}: {detail or 'no output'}"
            )

        if fragments:
            # merge Frag files in order (same as yt-dlp ExternalFD)
            with tmp.open("wb") as dest_stream:
                for frag in fragments:
                    frag_path = tmp.with_name(f"{tmp.name}-Frag{frag.index}")
                    if not frag_path.is_file():
                        if opts.skip_unavailable and frag.index > 0:
                            continue
                        raise external_downloader_error(
                            f"aria2c missing fragment file: {frag_path.name}"
                        )
                    data = frag_path.read_bytes()
                    dest_stream.write(data)
                    if not opts.keep_fragments:
                        frag_path.unlink(missing_ok=True)

        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise external_downloader_error("aria2c produced an empty file")
        tmp.rename(dest)
        if emit:
            size = dest.stat().st_size
            emit.progress(
                job=job_id,
                file=file_id,
                bytes=size,
                total=size,
                bps=0.0,
                fragment_count=len(fragments) if fragments else None,
            )
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest


class ffmpeg_downloader(external_downloader):
    """subprocess ffmpeg for m3u8 / dash / http URLs (stream copy)."""

    name = "ffmpeg"

    def __init__(self, exe: str | None = None) -> None:
        try:
            self.exe = str(find_ffmpeg(exe))
        except ffmpeg_missing_error as exc:
            raise external_downloader_error(str(exc)) from exc

    @classmethod
    def available(cls, path: str | None = None) -> str | None:
        try:
            return str(find_ffmpeg(path))
        except ffmpeg_missing_error:
            return None

    @classmethod
    def supports_manifest_url(cls) -> bool:
        return True

    def download(
        self,
        fragments: list[media_fragment] | None,
        dest: Path,
        *,
        job_id: str,
        file_id: str,
        opts: fragment_options,
        emit: emitter | None = None,
        source: str = "",
        manifest_url: str | None = None,
    ) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            if emit:
                emit.done(job=job_id, file=file_id, path=str(dest))
            return dest

        # ffmpeg prefers a single manifest / media URL; fragment lists use native/aria2c
        url = manifest_url or source
        if not url and fragments and len(fragments) == 1:
            url = fragments[0].url
        if not url:
            raise fragment_error(
                "ffmpeg backend needs a manifest URL (m3u8/dash) or a single fragment"
            )

        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists():
            tmp.unlink()

        headers: dict[str, str] = {}
        if opts.headers:
            headers.update(opts.headers)
        net = active_network()
        if net.user_agent and "User-Agent" not in headers:
            headers["User-Agent"] = net.user_agent

        cmd = [self.exe, "-y", "-hide_banner", "-loglevel", "error"]
        if headers:
            header_blob = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            cmd.extend(["-headers", header_blob])
        cmd.extend(["-i", url, "-c", "copy", "-f", "mp4", str(tmp)])

        if emit:
            emit.merging(
                job=job_id,
                file=file_id,
                path=str(dest),
                message="downloading via ffmpeg external downloader",
            )

        env = external_env(net)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except OSError as exc:
            raise external_downloader_error(f"failed to run ffmpeg: {exc}") from exc

        if opts.cancel is not None and opts.cancel.is_set():
            raise InterruptedError("cancelled")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            # detect encryption hints from ffmpeg errors
            if detail:
                try:
                    reject_encrypted_markers(detail, where="ffmpeg output")
                except encrypted_stream_error:
                    raise
            if len(detail) > 600:
                detail = detail[-600:]
            raise external_downloader_error(
                f"ffmpeg exited {proc.returncode}: {detail or 'no output'}"
            )
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise external_downloader_error("ffmpeg produced an empty file")
        tmp.rename(dest)
        if emit:
            size = dest.stat().st_size
            emit.progress(job=job_id, file=file_id, bytes=size, total=size, bps=0.0)
            emit.done(job=job_id, file=file_id, path=str(dest))
        return dest


_by_name: dict[str, type[external_downloader]] = {
    "aria2c": aria2c_downloader,
    "ffmpeg": ffmpeg_downloader,
}


def list_external_downloaders() -> list[str]:
    return sorted(_by_name.keys())


def get_external_downloader(
    name: str,
    *,
    path: str | None = None,
) -> external_downloader:
    key = (name or "").strip().lower()
    if key in {"native", "http", ""}:
        raise fragment_error("native backend is not an external downloader")
    cls = _by_name.get(key)
    if cls is None:
        raise fragment_error(
            f"unknown external downloader: {name} "
            f"(supported: {', '.join(list_external_downloaders())})"
        )
    return cls(path)


def probe_external_downloaders() -> list[external_downloader_info]:
    out: list[external_downloader_info] = []
    for name, cls in _by_name.items():
        resolved = cls.available()
        out.append(
            external_downloader_info(
                name=name,
                available=resolved is not None,
                path=resolved,
            )
        )
    return out


def download_fragments_external(
    fragments: list[media_fragment],
    dest: Path,
    *,
    backend: str,
    job_id: str,
    file_id: str,
    opts: fragment_options | None = None,
    emit: emitter | None = None,
    source: str = "",
    manifest_url: str | None = None,
) -> Path:
    """download via aria2c or ffmpeg using the shared fragment options."""
    options = opts or fragment_options()
    started = time.time()
    downloader = get_external_downloader(backend)
    path = downloader.download(
        fragments,
        dest,
        job_id=job_id,
        file_id=file_id,
        opts=options,
        emit=emit,
        source=source,
        manifest_url=manifest_url,
    )
    # ensure elapsed accounted for callers that only watch progress
    if emit and path.is_file():
        _ = time.time() - started
    return path


def resolve_fragment_backend(
    requested: str | None = None,
    *,
    prefer_external: str | None = None,
) -> str:
    """pick effective backend: native | aria2c | ffmpeg.

    ``prefer_external`` comes from network settings when ``requested`` is None.
    Falls back to native when the chosen external binary is missing.
    """
    net = active_network()
    choice = (requested or prefer_external or getattr(net, "external_downloader", None) or "native")
    choice = str(choice).strip().lower() or "native"
    if choice in {"native", "http"}:
        return "native"
    if choice not in _by_name:
        return "native"
    if _by_name[choice].available() is None:
        return "native"
    return choice
