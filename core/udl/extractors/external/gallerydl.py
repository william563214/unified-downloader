"""gallery-dl extractor via external CLI subprocess (mere aggregation).

gallery-dl is GPL-2.0-only. Do NOT import gallery_dl or vendor its source into
this process. Invoke only as a separate executable / `python -m gallery_dl`.

Upstream: https://github.com/mikf/gallery-dl
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
from udl.paths import app_data_dir
from udl.secrets import get_secret

# lowercase constants per project convention
gallerydl_tool_env = "UDL_GALLERY_DL"
gallerydl_enabled_env = "UDL_GALLERY_DL_ENABLED"
gallerydl_cookies_env = "UDL_GALLERY_DL_COOKIES"
gallerydl_archive_env = "UDL_GALLERY_DL_ARCHIVE"
gallerydl_secret_user_prefix = "gallerydl.username."
gallerydl_secret_pass_prefix = "gallerydl.password."
gallerydl_secret_user = "gallerydl.username"
gallerydl_secret_pass = "gallerydl.password"

gallerydl_names = ("gallery-dl", "gallery_dl")

install_guidance = (
    "Install gallery-dl then retry. "
    "pip/uv: pip install gallery-dl  or  uv tool install gallery-dl. "
    "macOS: brew install gallery-dl. "
    "Windows: pip install gallery-dl  or scoop (if available). "
    "Override binary with --gallerydl-tool or UDL_GALLERY_DL. "
    "Release builds may bundle a separate gallery-dl executable "
    "(mere aggregation; GPL-2.0 — see NOTICE)."
)

# process-wide settings applied before router.match / resolve
_enabled = True
_tool_override: str | Path | None = None
_cookies_path: str | Path | None = None
_extra_options: list[str] = []
_username: str | None = None
_password: str | None = None
_use_archive = False
_archive_path: str | Path | None = None
_match_cache: dict[str, bool] = {}
_tool_cache: object = False  # False=unset; gallerydl_tool | None when resolved


class gallerydl_tool_missing_error(RuntimeError):
    """raised when gallery-dl executable cannot be found."""

    code = "gallerydl_tool_missing"


class gallerydl_download_error(RuntimeError):
    """raised when the gallery-dl subprocess fails."""

    code = "download_failed"


class gallerydl_disabled_error(RuntimeError):
    """raised when gallery-dl integration is disabled."""

    code = "gallerydl_disabled"


@dataclass(frozen=True)
class gallerydl_tool:
    kind: str  # binary | python_module
    path: Path
    prefix: tuple[str, ...] = ()


@dataclass
class gallerydl_settings:
    enabled: bool = True
    tool: str | Path | None = None
    cookies: str | Path | None = None
    options: list[str] = field(default_factory=list)
    username: str | None = None
    password: str | None = None
    use_archive: bool = False
    archive: str | Path | None = None


def configure_gallerydl(settings: gallerydl_settings | None = None, **kwargs: object) -> None:
    """set process-wide gallery-dl integration options (never log secrets)."""
    global _enabled, _tool_override, _cookies_path, _extra_options
    global _username, _password, _use_archive, _archive_path, _tool_cache
    if settings is not None:
        _enabled = bool(settings.enabled)
        _tool_override = settings.tool
        _cookies_path = settings.cookies
        _extra_options = list(settings.options or [])
        _username = settings.username
        _password = settings.password
        _use_archive = bool(settings.use_archive)
        _archive_path = settings.archive
    if "enabled" in kwargs and kwargs["enabled"] is not None:
        _enabled = bool(kwargs["enabled"])
    if "tool" in kwargs:
        _tool_override = kwargs["tool"]  # type: ignore[assignment]
    if "cookies" in kwargs:
        _cookies_path = kwargs["cookies"]  # type: ignore[assignment]
    if "options" in kwargs and kwargs["options"] is not None:
        raw = kwargs["options"]
        if isinstance(raw, str):
            _extra_options = [line.strip() for line in raw.splitlines() if line.strip()]
        else:
            _extra_options = [str(x).strip() for x in raw if str(x).strip()]  # type: ignore[arg-type]
    if "username" in kwargs:
        _username = str(kwargs["username"]) if kwargs["username"] else None
    if "password" in kwargs:
        _password = str(kwargs["password"]) if kwargs["password"] else None
    if "use_archive" in kwargs and kwargs["use_archive"] is not None:
        _use_archive = bool(kwargs["use_archive"])
    if "archive" in kwargs:
        _archive_path = kwargs["archive"]  # type: ignore[assignment]
    _tool_cache = False
    _match_cache.clear()


def is_enabled() -> bool:
    env = os.environ.get(gallerydl_enabled_env)
    if env is not None and env.strip() != "":
        return env.strip().lower() not in {"0", "false", "no", "off"}
    return _enabled


def default_archive_path() -> Path:
    return app_data_dir() / "gallery-dl-archive.sqlite3"


def find_gallerydl_tool(override: str | Path | None = None) -> gallerydl_tool:
    """locate gallery-dl binary or python -m gallery_dl."""
    global _tool_cache
    explicit = override if override is not None else _tool_override
    if explicit is None:
        env = os.environ.get(gallerydl_tool_env)
        if env:
            explicit = env
    if explicit:
        path = Path(str(explicit)).expanduser()
        if not path.is_file():
            raise gallerydl_tool_missing_error(
                f"gallery-dl not found at {path}. {install_guidance}"
            )
        return gallerydl_tool(kind="binary", path=path, prefix=(str(path),))

    if isinstance(_tool_cache, gallerydl_tool):
        return _tool_cache
    if _tool_cache is None:
        raise gallerydl_tool_missing_error(f"gallery-dl not found. {install_guidance}")

    # sibling of frozen udl sidecar (Tauri externalBin mere aggregation)
    exe = Path(sys.executable).resolve()
    siblings = [
        exe.parent / ("gallery-dl.exe" if sys.platform == "win32" else "gallery-dl"),
    ]
    # tauri renames externalBin with target triple prefix pattern gallery-dl-<triple>
    for candidate in exe.parent.glob("gallery-dl-*"):
        if candidate.is_file():
            siblings.append(candidate)
    for candidate in siblings:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            tool = gallerydl_tool(kind="binary", path=candidate, prefix=(str(candidate),))
            _tool_cache = tool
            return tool

    for name in gallerydl_names:
        found = shutil.which(name)
        if found:
            tool = gallerydl_tool(kind="binary", path=Path(found), prefix=(found,))
            _tool_cache = tool
            return tool

    # python -m gallery_dl (separate interpreter; still not imported into udl)
    for py in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if not py:
            continue
        try:
            proc = subprocess.run(
                [py, "-m", "gallery_dl", "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            tool = gallerydl_tool(
                kind="python_module",
                path=Path(py),
                prefix=(py, "-m", "gallery_dl"),
            )
            _tool_cache = tool
            return tool

    _tool_cache = None
    raise gallerydl_tool_missing_error(f"gallery-dl not found. {install_guidance}")


def tool_status(override: str | Path | None = None) -> dict[str, Any]:
    try:
        tool = find_gallerydl_tool(override)
    except gallerydl_tool_missing_error as exc:
        return {
            "found": False,
            "enabled": is_enabled(),
            "path": None,
            "kind": None,
            "version": None,
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
        "message": "ok",
    }


def list_modules(override: str | Path | None = None) -> list[str]:
    tool = find_gallerydl_tool(override)
    proc = subprocess.run(
        [*tool.prefix, "--list-modules"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise gallerydl_download_error(
            f"gallery-dl --list-modules failed: {detail or proc.returncode}"
        )
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def list_extractors(override: str | Path | None = None) -> list[dict[str, str]]:
    """parse gallery-dl --list-extractors into compact rows for GUI."""
    tool = find_gallerydl_tool(override)
    proc = subprocess.run(
        [*tool.prefix, "--list-extractors"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise gallerydl_download_error(
            f"gallery-dl --list-extractors failed: {detail or proc.returncode}"
        )
    rows: list[dict[str, str]] = []
    name = ""
    category = ""
    subcategory = ""
    example = ""
    description = ""
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if name:
                rows.append(
                    {
                        "name": name,
                        "category": category,
                        "subcategory": subcategory,
                        "example": example,
                        "description": description,
                    }
                )
                name = category = subcategory = example = description = ""
            continue
        if stripped.endswith("Extractor") and " " not in stripped:
            if name:
                rows.append(
                    {
                        "name": name,
                        "category": category,
                        "subcategory": subcategory,
                        "example": example,
                        "description": description,
                    }
                )
            name = stripped
            category = subcategory = example = description = ""
            continue
        if stripped.startswith("Category:"):
            # Category: foo - Subcategory: bar
            m = re.match(
                r"Category:\s*(.+?)\s*-\s*Subcategory:\s*(.+)\s*$",
                stripped,
                re.I,
            )
            if m:
                category = m.group(1).strip()
                subcategory = m.group(2).strip()
            else:
                category = stripped.split(":", 1)[-1].strip()
        elif stripped.startswith("Example"):
            example = stripped.split(":", 1)[-1].strip()
        elif stripped.startswith("Extractor for") or (
            name and not description and not stripped.startswith("Category")
        ):
            if not description:
                description = stripped
    if name:
        rows.append(
            {
                "name": name,
                "category": category,
                "subcategory": subcategory,
                "example": example,
                "description": description,
            }
        )
    return rows


def matches(url: str) -> bool:
    """true when gallery-dl is enabled, available, and recognizes the URL."""
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
        tool = find_gallerydl_tool()
    except gallerydl_tool_missing_error:
        _match_cache[cache_key] = False
        return False
    # -E loads extractor config for URL without downloading
    try:
        proc = subprocess.run(
            [*tool.prefix, "-E", "--config-ignore", text],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _match_cache[cache_key] = False
        return False
    out = f"{proc.stdout or ''}{proc.stderr or ''}"
    ok = proc.returncode == 0 and "Unsupported URL" not in out
    _match_cache[cache_key] = ok
    return ok


def _subprocess_env() -> dict[str, str]:
    return external_env()


def _auth_for_category(category: str | None) -> tuple[str | None, str | None]:
    user = _username
    password = _password
    if not user:
        user = get_secret(gallerydl_secret_user)
    if not password:
        password = get_secret(gallerydl_secret_pass)
    if category:
        cat = category.strip().lower()
        if cat:
            site_user = get_secret(f"{gallerydl_secret_user_prefix}{cat}")
            site_pass = get_secret(f"{gallerydl_secret_pass_prefix}{cat}")
            if site_user:
                user = site_user
            if site_pass:
                password = site_pass
    return user, password


def _cookies_arg() -> str | None:
    if _cookies_path:
        path = Path(str(_cookies_path)).expanduser()
        if path.is_file():
            return str(path)
    env = os.environ.get(gallerydl_cookies_env)
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return str(path)
    return None


def _archive_arg() -> str | None:
    if not _use_archive and not _archive_path and not os.environ.get(gallerydl_archive_env):
        return None
    if _archive_path:
        return str(Path(str(_archive_path)).expanduser())
    env = os.environ.get(gallerydl_archive_env)
    if env:
        return str(Path(env).expanduser())
    return str(default_archive_path())


def _common_cli_args(*, category: str | None = None) -> list[str]:
    args: list[str] = ["--config-ignore"]
    net = active_network()
    # only pass UA when user customized it — gallery-dl default UA avoids
    # needless Cloudflare trips on sites that fingerprint browsers
    if net.user_agent and net.user_agent.strip() != default_user_agent:
        args.extend(["--user-agent", net.user_agent])
    mode = (net.proxy_mode or proxy_mode_none).lower()
    if mode == proxy_mode_custom:
        proxy = build_proxy_url(net)
        if proxy:
            args.extend(["--proxy", proxy])
    elif mode == proxy_mode_none:
        args.extend(["-o", "extractor.proxy-env=false"])
    limit = effective_job_limit_bps()
    kib = format_speed_kib(limit)
    if kib:
        # gallery-dl --limit-rate accepts e.g. 500k / 2.5M
        args.extend(["--limit-rate", f"{kib}k"])
    cookies = _cookies_arg()
    if cookies:
        args.extend(["--cookies", cookies])
    user, password = _auth_for_category(category)
    if user:
        args.extend(["--username", user])
    if password:
        args.extend(["--password", password])
    archive = _archive_arg()
    if archive:
        args.extend(["--download-archive", archive])
    for opt in _extra_options:
        if not opt:
            continue
        if "=" in opt:
            args.extend(["--option", opt])
        else:
            args.extend(["--option", opt])
    return args


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "download"


def _meta_from_gdl(data: dict[str, Any], *, canonical_url: str) -> item_metadata:
    category = str(data.get("category") or data.get("basecategory") or "gallerydl")
    item_id_raw = data.get("id") or data.get("token") or data.get("filename")
    item_id = f"{category}:{item_id_raw}" if item_id_raw is not None else None
    title = (
        data.get("title")
        or data.get("name")
        or data.get("filename")
        or (f"{category} {item_id_raw}" if item_id_raw is not None else category)
    )
    author = (
        data.get("user")
        or data.get("username")
        or data.get("artist")
        or data.get("author")
        or data.get("uploader")
    )
    if isinstance(author, dict):
        author = author.get("name") or author.get("username") or author.get("id")
    tags_raw = (
        data.get("tags")
        or data.get("tag_string")
        or data.get("tag_string_general")
        or []
    )
    tags: list[str] = []
    if isinstance(tags_raw, str):
        tags = [t for t in re.split(r"[\s,]+", tags_raw) if t]
    elif isinstance(tags_raw, list):
        for t in tags_raw:
            if isinstance(t, dict):
                name = t.get("name") or t.get("tag")
                if name:
                    tags.append(str(name))
            else:
                tags.append(str(t))
    width = data.get("width") or data.get("image_width")
    height = data.get("height") or data.get("image_height")
    resolution = None
    if width and height:
        resolution = f"{width}x{height}"
    date = data.get("date") or data.get("created_at") or data.get("upload_date")
    if hasattr(date, "isoformat"):
        date = date.isoformat()
    thumb = (
        data.get("thumbnail_url")
        or data.get("preview_file_url")
        or data.get("preview_url")
    )
    cover = data.get("cover_url") or data.get("large_file_url") or thumb
    return item_metadata(
        site="gallerydl",
        item_id=str(item_id) if item_id is not None else None,
        canonical_url=str(data.get("webpage_url") or canonical_url),
        title=str(title) if title is not None else None,
        author=str(author) if author is not None else None,
        upload_date=str(date) if date is not None else None,
        tags=tags,
        description=_str_or_none(data.get("description") or data.get("content")),
        duration=_float_or_none(data.get("duration")),
        resolution=resolution,
        thumbnail_url=_str_or_none(thumb),
        cover_url=_str_or_none(cover),
        view_count=_int_or_none(data.get("view_count") or data.get("views")),
        like_count=_int_or_none(
            data.get("like_count") or data.get("score") or data.get("fav_count")
        ),
        collection=_str_or_none(data.get("gallery_id") or data.get("album")),
        collection_id=_str_or_none(data.get("gallery_id")),
        extra={
            "tool": "gallery-dl",
            "category": category,
            "subcategory": data.get("subcategory"),
            "gdl": {
                k: data[k]
                for k in ("id", "filename", "extension", "file_url", "md5")
                if k in data
            },
        },
    )


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


def _file_from_gdl(data: dict[str, Any], *, canonical_url: str) -> extracted_file | None:
    file_url = (
        data.get("file_url")
        or data.get("url")
        or data.get("display_url")
        or data.get("image_url")
    )
    filename = data.get("filename")
    extension = data.get("extension") or data.get("file_ext") or ""
    if filename and extension and not str(filename).endswith(f".{extension}"):
        name = f"{filename}.{extension}"
    elif filename:
        name = str(filename)
    elif file_url:
        name = Path(str(file_url).split("?", 1)[0]).name or "download"
    else:
        return None
    name = _safe_name(str(name))
    category = str(data.get("category") or "gallerydl")
    size = _int_or_none(data.get("file_size") or data.get("filesize") or data.get("size"))
    meta = _meta_from_gdl(data, canonical_url=canonical_url)
    return extracted_file(
        url=str(file_url or canonical_url),
        name=name,
        relative_dir=_safe_name(category) if category else "",
        size=size,
        meta={
            "category": category,
            "gdl_id": data.get("id"),
            "file_url": file_url,
        },
        metadata=meta,
    )


def _parse_dump_json(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if not text:
        return []
    items: list[dict[str, Any]] = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # maybe concatenated JSON values / lines
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            items.extend(_extract_meta_dicts(obj))
        return items
    return _extract_meta_dicts(payload)


def _extract_meta_dicts(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        out.append(payload)
        return out
    if not isinstance(payload, list):
        return out
    for entry in payload:
        if isinstance(entry, dict):
            out.append(entry)
            continue
        if isinstance(entry, list) and len(entry) >= 2:
            # gallery-dl message: [type, data]
            data = entry[1]
            if isinstance(data, dict):
                out.append(data)
            elif isinstance(data, str) and len(entry) >= 3 and isinstance(entry[2], dict):
                # [Url, url, metadata] style
                meta = dict(entry[2])
                meta.setdefault("file_url", data)
                out.append(meta)
    return out


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


# progress lines vary; accept common "xx% of" / "Downloaded" patterns
re_progress = re.compile(
    r"(?P<pct>[\d.]+)\s*%|#(?P<bytes>[\d,]+)|(?P<done>[\d.]+)\s*(?P<unit>[KMG]i?B)",
    re.I,
)


class gallerydl_extractor(external_extractor):
    site = "gallerydl"
    label = "gallery-dl"

    def __init__(self, *, tool: str | Path | None = None) -> None:
        self.tool_override = tool

    def configure(self, **kwargs: object) -> None:
        if "gallerydl_tool" in kwargs and kwargs["gallerydl_tool"] is not None:
            self.tool_override = str(kwargs["gallerydl_tool"])
        if "gallerydl_enabled" in kwargs and kwargs["gallerydl_enabled"] is not None:
            configure_gallerydl(enabled=bool(kwargs["gallerydl_enabled"]))
        if "gallerydl_cookies" in kwargs:
            configure_gallerydl(cookies=kwargs["gallerydl_cookies"])
        if "gallerydl_options" in kwargs and kwargs["gallerydl_options"] is not None:
            configure_gallerydl(options=kwargs["gallerydl_options"])
        if "gallerydl_username" in kwargs:
            configure_gallerydl(username=kwargs["gallerydl_username"])
        if "gallerydl_password" in kwargs:
            configure_gallerydl(password=kwargs["gallerydl_password"])
        if "gallerydl_archive" in kwargs:
            configure_gallerydl(archive=kwargs["gallerydl_archive"], use_archive=True)
        if "gallerydl_use_archive" in kwargs and kwargs["gallerydl_use_archive"] is not None:
            configure_gallerydl(use_archive=bool(kwargs["gallerydl_use_archive"]))

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        if not is_enabled():
            raise gallerydl_disabled_error("gallery-dl integration is disabled")
        del password  # site passwords via secrets / --username; folder pwd unused
        tool = find_gallerydl_tool(self.tool_override)
        cmd = [
            *tool.prefix,
            "-j",
            "-s",
            *_common_cli_args(),
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
            raise gallerydl_download_error("gallery-dl resolve timed out") from exc
        except OSError as exc:
            raise gallerydl_download_error(f"failed to start gallery-dl: {exc}") from exc

        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            if "Unsupported URL" in stderr or "Unsupported URL" in (proc.stdout or ""):
                raise ValueError(f"gallery-dl unsupported URL: {url}")
            detail = stderr or (proc.stdout or "")
            if len(detail) > 1200:
                detail = detail[-1200:]
            # do not echo command (may contain --password)
            raise gallerydl_download_error(
                f"gallery-dl resolve failed (exit {proc.returncode}): "
                f"{detail or 'no output'}"
            )

        metas = _parse_dump_json(proc.stdout or "")
        files: list[extracted_file] = []
        seen: set[str] = set()
        for data in metas:
            if data.get("error") and not any(
                k in data for k in ("file_url", "filename", "id", "md5")
            ):
                msg = data.get("message") or data.get("error")
                raise gallerydl_download_error(f"gallery-dl resolve error: {msg}")
            # skip pure directory markers without file target
            if not any(
                k in data
                for k in ("file_url", "filename", "extension", "url", "id", "md5")
            ):
                continue
            f = _file_from_gdl(data, canonical_url=url)
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
            # still recognized — placeholder so download() can run the tool
            category = "gallerydl"
            if metas:
                category = str(metas[0].get("category") or category)
            meta = item_metadata(
                site=self.site,
                item_id=None,
                canonical_url=url,
                title=category,
                extra={"tool": "gallery-dl", "category": category},
            )
            files = [
                extracted_file(
                    url=url,
                    name=_safe_name(category),
                    relative_dir="",
                    metadata=meta,
                    meta={"kind": "url", "tool": "gallery-dl"},
                )
            ]
            return extract_result(
                site=self.site,
                title=category,
                files=files,
                kind="gallery",
                meta={"tool": "gallery-dl", "category": category},
                metadata=meta,
            )

        job_meta = files[0].metadata or item_metadata(site=self.site, canonical_url=url)
        title = job_meta.title or job_meta.extra.get("category") or "gallery-dl"
        category = str(job_meta.extra.get("category") or "gallery")
        return extract_result(
            site=self.site,
            title=str(title),
            files=files,
            kind=str(job_meta.extra.get("subcategory") or "gallery"),
            meta={"tool": "gallery-dl", "category": category, "count": len(files)},
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
            raise gallerydl_disabled_error("gallery-dl integration is disabled")
        job = job_id or result.site
        tool = find_gallerydl_tool(self.tool_override)
        output_dir.mkdir(parents=True, exist_ok=True)

        # emit skip events for deduped indexes
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

        category = None
        if result.meta:
            category = result.meta.get("category")
        if not category and result.metadata and result.metadata.extra:
            category = result.metadata.extra.get("category")

        url = result.metadata.canonical_url if result.metadata else None
        if not url and result.files:
            url = result.files[0].url
        if not url:
            raise gallerydl_download_error("missing URL for gallery-dl download")

        # stage under output; rename into overrides after
        stage = output_dir
        cmd = [
            *tool.prefix,
            "-D",
            str(stage),
            *_common_cli_args(category=str(category) if category else None),
            "--retries",
            str(max(0, int(retries))),
            # emit machine-readable lines for completed files
            "--Print",
            "file:{category}\t{id}\t{_path}\t{filename}.{extension}\t{file_size!s}",
            "--Print",
            "after:{_path}",
            url,
        ]

        file_ids: dict[str, str] = {}
        completed: set[str] = set()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        last_emit = 0.0
        state_lock = Lock()
        active_fid: str | None = None

        # pre-announce known files from resolve
        for idx, f in enumerate(result.files):
            if skip_indexes and idx in skip_indexes:
                continue
            fid = f"{job}:{idx}"
            file_ids[f.name] = fid
            if f.meta.get("gdl_id") is not None:
                file_ids[f"{f.meta.get('category')}:{f.meta.get('gdl_id')}"] = fid
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
            with state_lock:
                if "\t" in stripped and not stripped.startswith("["):
                    parts = stripped.split("\t")
                    if len(parts) >= 3:
                        cat, gid, path = parts[0], parts[1], parts[2]
                        name = parts[3] if len(parts) > 3 else Path(path).name
                        size = _int_or_none(parts[4]) if len(parts) > 4 else None
                        fid = ensure_file(name, path=path, total=size)
                        file_ids[f"{cat}:{gid}"] = fid
                        if path and path not in completed:
                            final_path = _maybe_relocate(
                                Path(path),
                                result,
                                file_dest_overrides,
                                name=name,
                            )
                            completed.add(path)
                            completed.add(str(final_path))
                            if emit:
                                emit.done(job=job, file=fid, path=str(final_path))
                        return
                # after:path
                if stripped.startswith("/") or (len(stripped) > 2 and stripped[1] == ":"):
                    path = stripped
                    if path in completed:
                        return
                    name = Path(path).name
                    fid = ensure_file(name, path=path)
                    final_path = _maybe_relocate(
                        Path(path), result, file_dest_overrides, name=name
                    )
                    completed.add(path)
                    if emit:
                        emit.done(job=job, file=fid, path=str(final_path))
                    return
                # rough progress from stderr
                if emit and active_fid and re_progress.search(stripped):
                    now = time.monotonic()
                    if now - last_emit >= 0.5:
                        last_emit = now
                        emit.progress(job=job, file=active_fid, bytes=0, total=None)

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
            raise gallerydl_download_error(f"failed to start gallery-dl: {exc}") from exc

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
            # redact password-looking fragments
            detail = re.sub(r"(--password\s+)\S+", r"\1***", detail, flags=re.I)
            if len(detail) > 1200:
                detail = detail[-1200:]
            raise gallerydl_download_error(
                f"gallery-dl failed (exit {proc.returncode}): {detail or 'no output'}"
            )

        ok = skipped + len({p for p in completed if Path(p).name})
        # count unique done file ids
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
            f.meta.get("gdl_id") is not None and name.startswith(str(f.meta.get("gdl_id")))
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
