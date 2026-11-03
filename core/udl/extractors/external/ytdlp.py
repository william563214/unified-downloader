"""yt-dlp extractor via external CLI subprocess (mere aggregation).

yt-dlp is Unlicense. Do NOT import yt_dlp into this process. Invoke only as a
separate executable (PATH / user path / managed app-data binary).

Upstream: https://github.com/yt-dlp/yt-dlp
Inspiration: OpenSelena/omniget managed yt-dlp fallback (GPL-3.0) — concepts
only; no omniget source is copied.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import IO, Any
from urllib.parse import urlparse

from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file
from udl.extractors.external.base import external_extractor
from udl.metadata import item_metadata
from udl.network import (
    active_network,
    build_proxy_url,
    default_user_agent,
    effective_job_limit_bps,
    external_env,
    format_speed_kib,
    proxy_mode_custom,
    proxy_mode_none,
)
from udl.postprocess.ffmpeg import find_ffmpeg
from udl.postprocess.ytdlp_provision import (
    default_install_dir,
    installed_ytdlp_path,
    ytdlp_tool_env,
)

# lowercase constants per project convention
ytdlp_enabled_env = "UDL_YTDLP_ENABLED"
ytdlp_cookies_env = "UDL_YTDLP_COOKIES"
ytdlp_format_env = "UDL_YTDLP_FORMAT"
ytdlp_names = ("yt-dlp", "yt_dlp")

install_guidance = (
    "Install yt-dlp then retry. "
    "GUI/CLI: udl ytdlp install (official binary + SHA2-256SUMS). "
    "pip/uv: pip install yt-dlp  or  uv tool install yt-dlp. "
    "macOS: brew install yt-dlp. "
    "Override binary with --ytdlp-tool or UDL_YTDLP."
)

# process-wide settings applied before router.match / resolve
_enabled = True
_tool_override: str | Path | None = None
_cookies_path: str | Path | None = None
_format: str | None = None
_audio_only = False
_audio_format: str | None = None
_extra_args: list[str] = []
_match_cache: dict[str, bool] = {}
_tool_cache: object = False  # False=unset; ytdlp_tool | None when resolved


class ytdlp_tool_missing_error(RuntimeError):
    """raised when yt-dlp executable cannot be found."""

    code = "ytdlp_tool_missing"


class ytdlp_download_error(RuntimeError):
    """raised when the yt-dlp subprocess fails."""

    code = "download_failed"


class ytdlp_disabled_error(RuntimeError):
    """raised when yt-dlp integration is disabled."""

    code = "ytdlp_disabled"


@dataclass(frozen=True)
class ytdlp_tool:
    kind: str  # binary | python_module
    path: Path
    prefix: tuple[str, ...] = ()


@dataclass
class ytdlp_settings:
    enabled: bool = True
    tool: str | Path | None = None
    cookies: str | Path | None = None
    format: str | None = None
    audio_only: bool = False
    audio_format: str | None = None
    extra_args: list[str] = field(default_factory=list)


def configure_ytdlp(settings: ytdlp_settings | None = None, **kwargs: object) -> None:
    """set process-wide yt-dlp integration options (never log secrets)."""
    global _enabled, _tool_override, _cookies_path, _format
    global _audio_only, _audio_format, _extra_args, _tool_cache
    if settings is not None:
        _enabled = bool(settings.enabled)
        _tool_override = settings.tool
        _cookies_path = settings.cookies
        _format = settings.format
        _audio_only = bool(settings.audio_only)
        _audio_format = settings.audio_format
        _extra_args = list(settings.extra_args or [])
    if "enabled" in kwargs and kwargs["enabled"] is not None:
        _enabled = bool(kwargs["enabled"])
    if "tool" in kwargs:
        _tool_override = kwargs["tool"]  # type: ignore[assignment]
    if "cookies" in kwargs:
        _cookies_path = kwargs["cookies"]  # type: ignore[assignment]
    if "format" in kwargs:
        _format = str(kwargs["format"]) if kwargs["format"] else None
    if "audio_only" in kwargs and kwargs["audio_only"] is not None:
        _audio_only = bool(kwargs["audio_only"])
    if "audio_format" in kwargs:
        _audio_format = str(kwargs["audio_format"]) if kwargs["audio_format"] else None
    if "extra_args" in kwargs and kwargs["extra_args"] is not None:
        raw = kwargs["extra_args"]
        if isinstance(raw, str):
            _extra_args = [line.strip() for line in raw.splitlines() if line.strip()]
        else:
            _extra_args = [str(x).strip() for x in raw if str(x).strip()]  # type: ignore[arg-type]
    if "quality" in kwargs and kwargs["quality"]:
        # map GUI/CLI quality shorthand into a format expression when no -f set
        mapped = _quality_to_format(str(kwargs["quality"]))
        if mapped and not _format:
            _format = mapped
    _tool_cache = False
    _match_cache.clear()


def is_enabled() -> bool:
    env = os.environ.get(ytdlp_enabled_env)
    if env is not None and env.strip() != "":
        return env.strip().lower() not in {"0", "false", "no", "off"}
    return _enabled


def _quality_to_format(quality: str) -> str | None:
    text = quality.strip()
    if not text:
        return None
    # already a yt-dlp format expression
    if any(ch in text for ch in "[]()+/") or text in {"best", "worst", "bestaudio", "bestvideo"}:
        return text
    lower = text.lower().rstrip("p")
    if lower.isdigit():
        height = int(lower)
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )
    return text


def find_ytdlp_tool(override: str | Path | None = None) -> ytdlp_tool:
    """locate yt-dlp binary or python -m yt_dlp."""
    global _tool_cache
    explicit = override if override is not None else _tool_override
    if explicit is None:
        env = os.environ.get(ytdlp_tool_env)
        if env:
            explicit = env
    if explicit:
        path = Path(str(explicit)).expanduser()
        if not path.is_file():
            raise ytdlp_tool_missing_error(
                f"yt-dlp not found at {path}. {install_guidance}"
            )
        return ytdlp_tool(kind="binary", path=path, prefix=(str(path),))

    if isinstance(_tool_cache, ytdlp_tool):
        return _tool_cache
    if _tool_cache is None:
        raise ytdlp_tool_missing_error(f"yt-dlp not found. {install_guidance}")

    # managed app-data install (FFmpeg-style)
    managed = installed_ytdlp_path()
    if managed.is_file() and os.access(managed, os.X_OK):
        tool = ytdlp_tool(kind="binary", path=managed, prefix=(str(managed),))
        _tool_cache = tool
        return tool

    # sibling of frozen udl sidecar
    exe = Path(sys.executable).resolve()
    siblings = [
        exe.parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"),
    ]
    for candidate in exe.parent.glob("yt-dlp*"):
        if candidate.is_file():
            siblings.append(candidate)
    for candidate in siblings:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            tool = ytdlp_tool(kind="binary", path=candidate, prefix=(str(candidate),))
            _tool_cache = tool
            return tool

    for name in ytdlp_names:
        found = shutil.which(name)
        if found:
            tool = ytdlp_tool(kind="binary", path=Path(found), prefix=(found,))
            _tool_cache = tool
            return tool

    for py in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if not py:
            continue
        try:
            proc = subprocess.run(
                [py, "-m", "yt_dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            tool = ytdlp_tool(
                kind="python_module",
                path=Path(py),
                prefix=(py, "-m", "yt_dlp"),
            )
            _tool_cache = tool
            return tool

    _tool_cache = None
    raise ytdlp_tool_missing_error(f"yt-dlp not found. {install_guidance}")


def tool_status(override: str | Path | None = None) -> dict[str, Any]:
    try:
        tool = find_ytdlp_tool(override)
    except ytdlp_tool_missing_error as exc:
        return {
            "found": False,
            "enabled": is_enabled(),
            "path": None,
            "kind": None,
            "version": None,
            "managed_dir": str(default_install_dir()),
            "message": str(exc),
        }
    version = None
    try:
        proc = subprocess.run(
            [*tool.prefix, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0:
            version = (proc.stdout or proc.stderr or "").strip().splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {
        "found": True,
        "enabled": is_enabled(),
        "path": str(tool.path),
        "kind": tool.kind,
        "version": version,
        "managed_dir": str(default_install_dir()),
        "message": "ok",
    }


def matches(url: str) -> bool:
    """true when yt-dlp is enabled, available, and recognizes the URL."""
    if not is_enabled():
        return False
    text = (url or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    cache_key = text
    if cache_key in _match_cache:
        return _match_cache[cache_key]
    try:
        tool = find_ytdlp_tool()
    except ytdlp_tool_missing_error:
        _match_cache[cache_key] = False
        return False
    try:
        proc = subprocess.run(
            [
                *tool.prefix,
                "--skip-download",
                "--no-playlist",
                "--no-warnings",
                "--print",
                "%(extractor)s",
                text,
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            env=_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _match_cache[cache_key] = False
        return False
    out = f"{proc.stdout or ''}{proc.stderr or ''}"
    ok = (
        proc.returncode == 0
        and bool((proc.stdout or "").strip())
        and "Unsupported URL" not in out
        and "ERROR:" not in (proc.stderr or "")
    )
    _match_cache[cache_key] = ok
    return ok


def _subprocess_env() -> dict[str, str]:
    return external_env()


def _cookies_arg() -> str | None:
    """only pass cookies when the user explicitly configured a cookies file.

    Never pull credentials from OS keychain / other site sessions.
    """
    if _cookies_path:
        path = Path(str(_cookies_path)).expanduser()
        if path.is_file():
            return str(path)
    env = os.environ.get(ytdlp_cookies_env)
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return str(path)
    return None


def _common_cli_args(*, for_download: bool = False) -> list[str]:
    args: list[str] = [
        "--no-warnings",
        # refuse DRM / unplayable formats — no paywall/DRM bypass
        "--no-allow-unplayable-formats",
        # Cloudflare / browser impersonation stays off by default
        # (do not pass --impersonate)
    ]
    net = active_network()
    if net.user_agent and net.user_agent.strip() != default_user_agent:
        args.extend(["--user-agent", net.user_agent])
    mode = (net.proxy_mode or proxy_mode_none).lower()
    if mode == proxy_mode_custom:
        proxy = build_proxy_url(net)
        if proxy:
            args.extend(["--proxy", proxy])
    elif mode == proxy_mode_none:
        # avoid inheriting ambient proxy env for consistency with gallery-dl
        args.extend(["--proxy", ""])
    limit = effective_job_limit_bps()
    kib = format_speed_kib(limit)
    if kib:
        args.extend(["--limit-rate", f"{kib}K"])
    cookies = _cookies_arg()
    if cookies:
        args.extend(["--cookies", cookies])
    # never pass --username/--password from keychain
    fmt = _format or os.environ.get(ytdlp_format_env)
    audio_only = _audio_only
    if audio_only:
        args.append("--extract-audio")
        if _audio_format:
            args.extend(["--audio-format", _audio_format])
        elif not fmt:
            args.extend(["--format", "bestaudio/best"])
    elif fmt:
        args.extend(["--format", fmt])
    # prefer project / user ffmpeg when available
    if for_download:
        try:
            ff = find_ffmpeg(None)
            args.extend(["--ffmpeg-location", str(Path(ff).parent)])
        except Exception:  # noqa: BLE001 — optional
            pass
    for extra in _extra_args:
        if not extra:
            continue
        # split simple tokens; keep KEY=VALUE as one arg after --
        if extra.startswith("-"):
            parts = extra.split(None, 1)
            args.extend(parts)
        else:
            args.append(extra)
    return args


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "download"


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _meta_from_info(data: dict[str, Any], *, canonical_url: str) -> item_metadata:
    extractor = str(data.get("extractor_key") or data.get("extractor") or "ytdlp")
    video_id = data.get("id")
    item_id = f"{extractor}:{video_id}" if video_id is not None else None
    title = data.get("title") or data.get("fulltitle") or extractor
    author = (
        data.get("uploader")
        or data.get("channel")
        or data.get("creator")
        or data.get("artist")
    )
    tags_raw = data.get("tags") or []
    tags: list[str] = []
    if isinstance(tags_raw, str):
        tags = [t for t in re.split(r"[\s,]+", tags_raw) if t]
    elif isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw if t]
    width = data.get("width")
    height = data.get("height")
    resolution = data.get("resolution")
    if not resolution and width and height:
        resolution = f"{width}x{height}"
    date = data.get("upload_date") or data.get("release_date")
    if isinstance(date, str) and len(date) == 8 and date.isdigit():
        date = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
    thumb = data.get("thumbnail")
    if not thumb and isinstance(data.get("thumbnails"), list) and data["thumbnails"]:
        last = data["thumbnails"][-1]
        if isinstance(last, dict):
            thumb = last.get("url")
    return item_metadata(
        site="ytdlp",
        item_id=str(item_id) if item_id is not None else None,
        canonical_url=str(data.get("webpage_url") or data.get("original_url") or canonical_url),
        title=str(title) if title is not None else None,
        author=str(author) if author is not None else None,
        upload_date=str(date) if date is not None else None,
        tags=tags,
        description=_str_or_none(data.get("description")),
        duration=_float_or_none(data.get("duration")),
        resolution=_str_or_none(resolution),
        thumbnail_url=_str_or_none(thumb),
        cover_url=_str_or_none(thumb),
        view_count=_int_or_none(data.get("view_count")),
        like_count=_int_or_none(data.get("like_count")),
        collection=_str_or_none(data.get("playlist_title") or data.get("album")),
        collection_id=_str_or_none(data.get("playlist_id")),
        extra={
            "tool": "yt-dlp",
            "extractor": extractor,
            "extractor_key": data.get("extractor_key"),
            "format_id": data.get("format_id"),
            "ext": data.get("ext"),
            "ytdlp": {
                k: data[k]
                for k in ("id", "ext", "format_id", "filesize", "filesize_approx")
                if k in data
            },
        },
    )


def _file_from_info(data: dict[str, Any], *, canonical_url: str) -> extracted_file | None:
    # playlist wrappers without a concrete entry
    if data.get("_type") == "playlist" and not data.get("id") and not data.get("entries"):
        return None
    video_id = data.get("id")
    title = data.get("title") or data.get("fulltitle") or video_id or "download"
    ext = data.get("ext") or "mp4"
    name = _safe_name(f"{title}.{ext}" if not str(title).endswith(f".{ext}") else str(title))
    size = _int_or_none(data.get("filesize") or data.get("filesize_approx"))
    meta = _meta_from_info(data, canonical_url=canonical_url)
    webpage = str(data.get("webpage_url") or data.get("original_url") or canonical_url)
    extractor = str(data.get("extractor_key") or data.get("extractor") or "ytdlp")
    return extracted_file(
        url=webpage,
        name=name,
        relative_dir=_safe_name(extractor),
        size=size,
        meta={
            "extractor": extractor,
            "ytdlp_id": video_id,
            "ext": ext,
            "format_id": data.get("format_id"),
        },
        metadata=meta,
    )


def _iter_info_dicts(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if payload.get("_type") == "playlist" and isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    # flat playlist entries may be shallow
                    if entry.get("_type") == "url" or entry.get("url") or entry.get("id"):
                        out.append(entry)
            if out:
                return out
        out.append(payload)
        return out
    if isinstance(payload, list):
        for item in payload:
            out.extend(_iter_info_dicts(item))
    return out


def _parse_dump_json(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        items: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            items.extend(_iter_info_dicts(obj))
        return items
    return _iter_info_dicts(payload)


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass


def _reader(stream: IO[str], sink: list[str], on_chunk) -> None:
    try:
        for line in stream:
            sink.append(line.rstrip("\n"))
            on_chunk(line.rstrip("\n"))
    except OSError:
        return


class ytdlp_extractor(external_extractor):
    site = "ytdlp"
    label = "yt-dlp"
    status = "external"
    kind_hint = "video|playlist|audio"
    # matching needs yt-dlp subprocess; override suitable()
    _VALID_URL = False

    @classmethod
    def suitable(cls, url: str) -> bool:
        return matches(url)

    def __init__(self, *, tool: str | Path | None = None) -> None:
        self.tool_override = tool

    def configure(self, **kwargs: object) -> None:
        if "ytdlp_tool" in kwargs and kwargs["ytdlp_tool"] is not None:
            self.tool_override = str(kwargs["ytdlp_tool"])
        if "ytdlp_enabled" in kwargs and kwargs["ytdlp_enabled"] is not None:
            configure_ytdlp(enabled=bool(kwargs["ytdlp_enabled"]))
        if "ytdlp_cookies" in kwargs:
            configure_ytdlp(cookies=kwargs["ytdlp_cookies"])
        if "ytdlp_format" in kwargs:
            configure_ytdlp(format=kwargs["ytdlp_format"])
        if "ytdlp_audio_only" in kwargs and kwargs["ytdlp_audio_only"] is not None:
            configure_ytdlp(audio_only=bool(kwargs["ytdlp_audio_only"]))
        if "ytdlp_audio_format" in kwargs:
            configure_ytdlp(audio_format=kwargs["ytdlp_audio_format"])
        if "ytdlp_extra" in kwargs and kwargs["ytdlp_extra"] is not None:
            configure_ytdlp(extra_args=kwargs["ytdlp_extra"])
        if "quality" in kwargs and kwargs["quality"] is not None:
            configure_ytdlp(quality=kwargs["quality"])

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        if not is_enabled():
            raise ytdlp_disabled_error("yt-dlp integration is disabled")
        del password  # never use keychain / site passwords for yt-dlp
        tool = find_ytdlp_tool(self.tool_override)
        cmd = [
            *tool.prefix,
            "-J",
            "--skip-download",
            "--flat-playlist",
            *_common_cli_args(for_download=False),
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env=_subprocess_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ytdlp_download_error("yt-dlp resolve timed out") from exc
        except OSError as exc:
            raise ytdlp_download_error(f"failed to start yt-dlp: {exc}") from exc

        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            if "Unsupported URL" in stderr or "Unsupported URL" in (proc.stdout or ""):
                raise ValueError(f"yt-dlp unsupported URL: {url}")
            detail = stderr or (proc.stdout or "")
            if len(detail) > 1200:
                detail = detail[-1200:]
            raise ytdlp_download_error(
                f"yt-dlp resolve failed (exit {proc.returncode}): "
                f"{detail or 'no output'}"
            )

        infos = _parse_dump_json(proc.stdout or "")
        files: list[extracted_file] = []
        seen: set[str] = set()
        for data in infos:
            if data.get("unavailable") or data.get("error"):
                continue
            f = _file_from_info(data, canonical_url=url)
            if f is None:
                continue
            dedupe_key = (
                f.metadata.item_id
                if f.metadata and f.metadata.item_id
                else f.url or f.name
            )
            if dedupe_key in seen:
                continue
            seen.add(str(dedupe_key))
            files.append(f)

        if not files:
            meta = item_metadata(
                site=self.site,
                item_id=None,
                canonical_url=url,
                title="yt-dlp",
                extra={"tool": "yt-dlp"},
            )
            files = [
                extracted_file(
                    url=url,
                    name="download",
                    relative_dir="",
                    metadata=meta,
                    meta={"kind": "url", "tool": "yt-dlp"},
                )
            ]
            return extract_result(
                site=self.site,
                title="yt-dlp",
                files=files,
                kind="video",
                meta={"tool": "yt-dlp"},
                metadata=meta,
            )

        job_meta = files[0].metadata or item_metadata(site=self.site, canonical_url=url)
        title = job_meta.title or "yt-dlp"
        kind = "playlist" if len(files) > 1 else "video"
        return extract_result(
            site=self.site,
            title=str(title),
            files=files,
            kind=kind,
            meta={"tool": "yt-dlp", "count": len(files)},
            metadata=job_meta,
        )

    def download(
        self,
        result: extract_result,
        output_dir: Path,
        *,
        emit: emitter | None = None,
        cancel: Event | None = None,
        workers: int = 3,
        retries: int = 5,
        job_id: str | None = None,
        file_dest_overrides: dict[int, Path] | None = None,
        skip_indexes: set[int] | None = None,
    ) -> tuple[int, int]:
        del workers
        if not is_enabled():
            raise ytdlp_disabled_error("yt-dlp integration is disabled")
        job = job_id or result.site
        tool = find_ytdlp_tool(self.tool_override)
        output_dir.mkdir(parents=True, exist_ok=True)

        skipped = 0
        if skip_indexes:
            for idx in sorted(skip_indexes):
                if idx < 0 or idx >= len(result.files):
                    continue
                f = result.files[idx]
                if file_dest_overrides and idx in file_dest_overrides:
                    dest = file_dest_overrides[idx]
                elif f.relative_dir:
                    dest = output_dir / f.relative_dir / f.name
                else:
                    dest = output_dir / f.name
                meta_payload = (
                    f.metadata.to_dict(omit_empty=True)
                    if f.metadata
                    else (
                        result.metadata.to_dict(omit_empty=True)
                        if result.metadata
                        else None
                    )
                )
                if emit:
                    emit.file(
                        job=job,
                        id=f"{job}:{idx}",
                        name=f.name,
                        total=f.size,
                        path=str(dest),
                        index=idx,
                        metadata=meta_payload,
                        already_downloaded=True,
                    )
                    emit.done(
                        job=job,
                        file=f"{job}:{idx}",
                        path=str(dest),
                        skipped=True,
                        already_downloaded=True,
                        metadata=meta_payload,
                    )
                skipped += 1
            if skipped == len(result.files):
                return skipped, 0

        url = result.metadata.canonical_url if result.metadata else None
        if not url and result.files:
            url = result.files[0].url
        if not url:
            raise ytdlp_download_error("missing URL for yt-dlp download")

        # output template under stage dir; relocate into overrides after
        outtmpl = str(output_dir / "%(extractor)s" / "%(title).200B [%(id)s].%(ext)s")
        progress_template = (
            "download:"
            '{"udl":"progress","downloaded":%(progress.downloaded_bytes|0)d,'
            '"total":%(progress.total_bytes|progress.total_bytes_estimate|0)d,'
            '"speed":%(progress.speed|0)f,'
            '"eta":%(progress.eta|0)d,'
            '"filename":"%(progress.filename|)s"}'
        )
        done_print = (
            "after_move:"
            '{"udl":"done","path":%(filepath|webpage_url)j,'
            '"title":%(title|)j,"id":%(id|)j,"ext":%(ext|)j}'
        )
        cmd = [
            *tool.prefix,
            "-o",
            outtmpl,
            "--no-mtime",
            "--newline",
            "--retries",
            str(max(0, int(retries))),
            "--progress-template",
            progress_template,
            "--print",
            done_print,
            *_common_cli_args(for_download=True),
            url,
        ]

        file_ids: dict[str, str] = {}
        completed: set[str] = set()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        last_emit = 0.0
        state_lock = Lock()
        active_fid: str | None = None

        for idx, f in enumerate(result.files):
            if skip_indexes and idx in skip_indexes:
                continue
            fid = f"{job}:{idx}"
            file_ids[f.name] = fid
            if f.meta.get("ytdlp_id") is not None:
                file_ids[str(f.meta.get("ytdlp_id"))] = fid
            if file_dest_overrides and idx in file_dest_overrides:
                dest = file_dest_overrides[idx]
            elif f.relative_dir:
                dest = output_dir / f.relative_dir / f.name
            else:
                dest = output_dir / f.name
            meta_payload = (
                f.metadata.to_dict(omit_empty=True)
                if f.metadata
                else (
                    result.metadata.to_dict(omit_empty=True)
                    if result.metadata
                    else None
                )
            )
            if emit:
                emit.file(
                    job=job,
                    id=fid,
                    name=f.name,
                    total=f.size,
                    path=str(dest),
                    index=idx,
                    metadata=meta_payload,
                )
            active_fid = fid

        def ensure_file(name: str, path: str | None = None, total: int | None = None) -> str:
            nonlocal active_fid
            key = name
            if key not in file_ids:
                idx = len(file_ids)
                fid = f"{job}:{idx}"
                file_ids[key] = fid
                if emit:
                    emit.file(
                        job=job,
                        id=fid,
                        name=name,
                        total=total,
                        path=path,
                        index=idx,
                        metadata=(
                            result.metadata.to_dict(omit_empty=True)
                            if result.metadata
                            else None
                        ),
                    )
            active_fid = file_ids[key]
            return file_ids[key]

        def handle_line(line: str) -> None:
            nonlocal last_emit
            if cancel is not None and cancel.is_set():
                return
            stripped = line.strip()
            if not stripped:
                return
            # strip optional progress-template prefix
            payload_text = stripped
            if payload_text.startswith("download:"):
                payload_text = payload_text[len("download:") :]
            if payload_text.startswith("after_move:"):
                payload_text = payload_text[len("after_move:") :]
            with state_lock:
                if payload_text.startswith("{") and '"udl"' in payload_text:
                    try:
                        obj = json.loads(payload_text)
                    except json.JSONDecodeError:
                        obj = None
                    if isinstance(obj, dict):
                        kind = obj.get("udl")
                        if kind == "progress" and emit and active_fid:
                            now = time.monotonic()
                            if now - last_emit >= 0.25:
                                last_emit = now
                                downloaded = _int_or_none(obj.get("downloaded")) or 0
                                total = _int_or_none(obj.get("total"))
                                speed = _float_or_none(obj.get("speed"))
                                emit.progress(
                                    job=job,
                                    file=active_fid,
                                    bytes=downloaded,
                                    total=total,
                                    bps=int(speed) if speed else None,
                                )
                                fname = obj.get("filename")
                                if fname:
                                    ensure_file(Path(str(fname)).name, path=str(fname), total=total)
                            return
                        if kind == "done":
                            path = obj.get("path")
                            if not path:
                                return
                            path_s = str(path)
                            name = Path(path_s).name
                            vid = obj.get("id")
                            fid = None
                            if vid is not None and str(vid) in file_ids:
                                fid = file_ids[str(vid)]
                            else:
                                fid = ensure_file(name, path=path_s)
                            final_path = _maybe_relocate(
                                Path(path_s),
                                result,
                                file_dest_overrides,
                                name=name,
                            )
                            if path_s not in completed:
                                completed.add(path_s)
                                completed.add(str(final_path))
                                if emit:
                                    emit.done(job=job, file=fid, path=str(final_path))
                            return

        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
            "env": _subprocess_env(),
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
        except OSError as exc:
            raise ytdlp_download_error(f"failed to start yt-dlp: {exc}") from exc

        assert proc.stdout is not None
        assert proc.stderr is not None
        t_out = Thread(
            target=_reader,
            args=(proc.stdout, stdout_lines, handle_line),
            daemon=True,
        )
        t_err = Thread(
            target=_reader,
            args=(proc.stderr, stderr_lines, handle_line),
            daemon=True,
        )
        t_out.start()
        t_err.start()

        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    _terminate_process(proc)
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)
                    raise InterruptedError("cancelled")
                time.sleep(0.05)
        finally:
            t_out.join(timeout=5)
            t_err.join(timeout=5)

        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")

        if proc.returncode != 0:
            detail = "\n".join(stderr_lines[-20:] + stdout_lines[-10:]).strip()
            detail = re.sub(r"(--password\s+)\S+", r"\1***", detail, flags=re.I)
            detail = re.sub(r"(--cookies\s+)\S+", r"\1***", detail, flags=re.I)
            if len(detail) > 1200:
                detail = detail[-1200:]
            raise ytdlp_download_error(
                f"yt-dlp failed (exit {proc.returncode}): {detail or 'no output'}"
            )

        done_paths = [p for p in completed if Path(p).suffix or Path(p).is_file()]
        ok = skipped + max(len(done_paths), 1 if proc.returncode == 0 else 0)
        if ok == skipped and proc.returncode == 0:
            found = [p for p in output_dir.rglob("*") if p.is_file()]
            for path in found:
                fid = ensure_file(path.name, path=str(path), total=path.stat().st_size)
                if emit:
                    emit.done(job=job, file=fid, path=str(path))
            ok = skipped + len(found)
        failed = 0 if ok > skipped or proc.returncode == 0 else 1
        return max(ok, skipped), failed


def _maybe_relocate(
    path: Path,
    result: extract_result,
    overrides: dict[int, Path] | None,
    *,
    name: str,
) -> Path:
    if not overrides:
        return path
    for idx, f in enumerate(result.files):
        if idx not in overrides:
            continue
        match = f.name == name or (
            f.meta.get("ytdlp_id") is not None
            and str(f.meta.get("ytdlp_id")) in name
        )
        if not match and path.name != f.name:
            continue
        dest = overrides[idx]
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.resolve() != dest.resolve() and path.is_file():
                if dest.exists():
                    dest.unlink()
                shutil.move(str(path), str(dest))
                return dest
        except OSError:
            return path
    return path
