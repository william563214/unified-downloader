"""Download and install the official yt-dlp release binary.

Sources (SHA-256 verified against release SHA2-256SUMS before install):
- macOS arm64: yt-dlp_macos from yt-dlp/yt-dlp releases
- Windows x64: yt-dlp.exe from yt-dlp/yt-dlp releases

yt-dlp itself is Unlicense. Managed install is optional user-initiated
(same pattern as FFmpeg provision). Users may instead point Settings /
UDL_YTDLP / PATH at a system binary.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# lowercase constants per project convention
ytdlp_dir_env = "UDL_YTDLP_DIR"
ytdlp_tool_env = "UDL_YTDLP"
user_agent = "unified-downloader/0.10 (ytdlp-provision)"
chunk_size = 1024 * 256
github_api_latest = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
github_release_base = "https://github.com/yt-dlp/yt-dlp/releases/download"
sha256sums_name = "SHA2-256SUMS"

# keep in sync with app/src-tauri/tauri.conf.json identifier
app_data_name = "io.github.william563214.unified-downloader"


class ytdlp_provision_error(RuntimeError):
    """raised when yt-dlp cannot be downloaded or verified."""

    code = "ytdlp_provision_failed"


@dataclass(frozen=True)
class ytdlp_status:
    found: bool
    path: str | None
    source: str | None
    version: str | None
    platform_key: str
    provision_supported: bool
    message: str
    latest_version: str | None = None
    update_available: bool | None = None


def platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    return f"{system}-{machine}"


def provision_supported() -> bool:
    return platform_key() in {"darwin-arm64", "windows-x86_64"}


def asset_name_for_platform(key: str | None = None) -> str:
    """official release asset filename for managed install."""
    pk = key or platform_key()
    if pk == "darwin-arm64":
        return "yt-dlp_macos"
    if pk == "windows-x86_64":
        return "yt-dlp.exe"
    raise ytdlp_provision_error(
        f"automatic yt-dlp install is only supported on macOS arm64 and "
        f"Windows x64 (this host is {pk})"
    )


def binary_name() -> str:
    return "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp"


def default_install_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ytdlp_dir_env)
    if env:
        return Path(env).expanduser().resolve()
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / app_data_name
    elif platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        base = Path(base) / app_data_name
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        base = Path(base) / app_data_name
    return (base / "yt-dlp").resolve()


def installed_ytdlp_path(install_dir: str | Path | None = None) -> Path:
    return default_install_dir(install_dir) / binary_name()


def _classify_source(path: Path) -> str:
    text = str(path)
    if "unified-downloader" in text and f"{os.sep}yt-dlp{os.sep}" in text.lower():
        return "app-data"
    env = os.environ.get(ytdlp_tool_env)
    if env and text.startswith(str(Path(env).expanduser())):
        return "UDL_YTDLP"
    return "path"


def _run_version(path: Path) -> str | None:
    import subprocess

    try:
        proc = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    line = (proc.stdout or proc.stderr or "").strip().splitlines()
    return line[0].strip() if line else None


def probe_ytdlp(explicit: str | Path | None = None) -> ytdlp_status:
    """locate yt-dlp without raising (for GUI status)."""
    from udl.extractors.external import ytdlp as ytdlp_mod

    key = platform_key()
    supported = provision_supported()
    try:
        tool = ytdlp_mod.find_ytdlp_tool(explicit)
    except ytdlp_mod.ytdlp_tool_missing_error as exc:
        return ytdlp_status(
            found=False,
            path=None,
            source=None,
            version=None,
            platform_key=key,
            provision_supported=supported,
            message=str(exc),
        )
    version = _run_version(tool.path)
    source = _classify_source(tool.path)
    return ytdlp_status(
        found=True,
        path=str(tool.path),
        source=source,
        version=version,
        platform_key=key,
        provision_supported=supported,
        message=f"yt-dlp ready ({source})",
    )


def _http_get(url: str, *, binary: bool = True, timeout: int = 120) -> bytes:
    req = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — pinned reputable hosts
            data = resp.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ytdlp_provision_error(f"download failed: {url}: {exc}") from exc
    if not data:
        raise ytdlp_provision_error(f"empty download: {url}")
    return data


def _http_download_to(
    url: str,
    dest: Path,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    expected_sha256: str | None = None,
) -> str:
    req = Request(url, headers={"User-Agent": user_agent})
    hasher = hashlib.sha256()
    total = 0
    try:
        with urlopen(req, timeout=180) as resp, dest.open("wb") as out:  # noqa: S310
            length_hdr = resp.headers.get("Content-Length")
            expected_total = int(length_hdr) if length_hdr and length_hdr.isdigit() else None
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                hasher.update(chunk)
                total += len(chunk)
                if progress:
                    progress(total, expected_total)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        dest.unlink(missing_ok=True)
        raise ytdlp_provision_error(f"download failed: {url}: {exc}") from exc

    digest = hasher.hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise ytdlp_provision_error(
            f"checksum mismatch for {url}: expected {expected_sha256}, got {digest}"
        )
    return digest


def _parse_sha256_line(text: str, filename: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0]
        if len(digest) != 64:
            continue
        name = parts[1].lstrip("*")
        if name.endswith(filename) or name == filename or Path(name).name == filename:
            return digest
    raise ytdlp_provision_error(f"could not parse sha256 for {filename} in {sha256sums_name}")


def fetch_latest_release() -> dict[str, str]:
    """return tag_name and html/browser download base for the latest release."""
    raw = _http_get(github_api_latest, binary=False, timeout=60)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ytdlp_provision_error("invalid GitHub releases/latest JSON") from exc
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise ytdlp_provision_error("latest yt-dlp release has no tag_name")
    return {"tag_name": tag, "base": f"{github_release_base}/{tag}"}


def normalize_version(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    # strip leading "yt-dlp " prefixes
    text = re.sub(r"^yt-dlp\s+", "", text, flags=re.I)
    return text.lstrip("vV").strip()


def check_update(explicit: str | Path | None = None) -> ytdlp_status:
    """probe installed binary and compare with GitHub latest tag."""
    status = probe_ytdlp(explicit)
    try:
        latest = fetch_latest_release()["tag_name"]
    except ytdlp_provision_error as exc:
        return ytdlp_status(
            found=status.found,
            path=status.path,
            source=status.source,
            version=status.version,
            platform_key=status.platform_key,
            provision_supported=status.provision_supported,
            message=f"{status.message}; update check failed: {exc}",
            latest_version=None,
            update_available=None,
        )
    current = normalize_version(status.version)
    remote = normalize_version(latest)
    update = bool(status.found and current and remote and current != remote)
    msg = status.message
    if status.found and update:
        msg = f"update available: {current} -> {remote}"
    elif status.found and current and remote and current == remote:
        msg = f"up to date ({current})"
    return ytdlp_status(
        found=status.found,
        path=status.path,
        source=status.source,
        version=status.version,
        platform_key=status.platform_key,
        provision_supported=status.provision_supported,
        message=msg,
        latest_version=latest,
        update_available=update if status.found else None,
    )


def install_ytdlp(
    dest_dir: str | Path | None = None,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    tag: str | None = None,
) -> Path:
    """download official binary for this OS/arch into dest_dir and return path."""
    key = platform_key()
    asset = asset_name_for_platform(key)
    directory = default_install_dir(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)

    if tag:
        release_tag = tag.strip()
        base = f"{github_release_base}/{release_tag}"
    else:
        info = fetch_latest_release()
        release_tag = info["tag_name"]
        base = info["base"]

    sums_url = f"{base}/{sha256sums_name}"
    asset_url = f"{base}/{asset}"
    sums_text = _http_get(sums_url, binary=False).decode("utf-8", errors="replace")
    expected = _parse_sha256_line(sums_text, asset)

    target = directory / binary_name()
    with tempfile.TemporaryDirectory(prefix="udl-ytdlp-") as tmp:
        archive = Path(tmp) / asset
        _http_download_to(asset_url, archive, progress=progress, expected_sha256=expected)
        # re-hash from disk before promoting (defense in depth)
        disk_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if disk_digest.lower() != expected.lower():
            raise ytdlp_provision_error(
                f"checksum mismatch after download: expected {expected}, got {disk_digest}"
            )
        tmp_target = Path(tmp) / binary_name()
        shutil.copyfile(archive, tmp_target)
        if platform.system() != "Windows":
            tmp_target.chmod(0o755)
        # atomic-ish replace into install dir
        final_tmp = directory / f".{binary_name()}.tmp"
        shutil.copyfile(tmp_target, final_tmp)
        if platform.system() != "Windows":
            final_tmp.chmod(0o755)
        final_tmp.replace(target)

    if not target.is_file():
        raise ytdlp_provision_error("installed yt-dlp binary missing")
    # record installed tag for update UI
    meta = directory / "version.txt"
    try:
        meta.write_text(f"{release_tag}\n", encoding="utf-8")
    except OSError:
        pass
    return target.resolve()
