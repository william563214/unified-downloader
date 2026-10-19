"""Download and install a static FFmpeg build for the current OS/arch.

Sources (checksum verified before install):
- macOS arm64: Martin Riedl static builds (ffmpeg.martin-riedl.de)
- Windows x64: BtbN FFmpeg-Builds LGPL static zip + checksums.sha256

License of each build is documented in NOTICE (LGPL vs GPL variants).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# lowercase constants per project convention
ffmpeg_dir_env = "UDL_FFMPEG_DIR"
user_agent = "unified-downloader/0.3 (ffmpeg-provision)"
chunk_size = 1024 * 256

btbn_latest_base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
btbn_win64_lgpl_name = "ffmpeg-master-latest-win64-lgpl.zip"
btbn_checksums_name = "checksums.sha256"

martin_macos_arm64_zip = (
    "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"
)


class ffmpeg_provision_error(RuntimeError):
    """raised when FFmpeg cannot be downloaded or verified."""

    code = "ffmpeg_provision_failed"


@dataclass(frozen=True)
class ffmpeg_status:
    found: bool
    path: str | None
    source: str | None
    platform_key: str
    message: str


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


# keep in sync with app/src-tauri/tauri.conf.json identifier
app_data_name = "io.github.william563214.unified-downloader"


def default_install_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ffmpeg_dir_env)
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
    return (base / "ffmpeg").resolve()


def installed_ffmpeg_path(install_dir: str | Path | None = None) -> Path:
    directory = default_install_dir(install_dir)
    name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    return directory / name


def probe_ffmpeg(explicit: str | Path | None = None) -> ffmpeg_status:
    """locate ffmpeg without raising (for GUI status)."""
    from udl.postprocess.ffmpeg import ffmpeg_missing_error, find_ffmpeg

    key = platform_key()
    try:
        path = find_ffmpeg(explicit)
    except ffmpeg_missing_error as exc:
        return ffmpeg_status(
            found=False,
            path=None,
            source=None,
            platform_key=key,
            message=str(exc),
        )
    source = _classify_source(path)
    return ffmpeg_status(
        found=True,
        path=str(path),
        source=source,
        platform_key=key,
        message=f"FFmpeg ready ({source})",
    )


def _classify_source(path: Path) -> str:
    text = str(path)
    if "unified-downloader" in text and f"{os.sep}ffmpeg{os.sep}" in text.lower():
        return "app-data"
    if os.environ.get("UDL_FFMPEG") and text.startswith(
        str(Path(os.environ["UDL_FFMPEG"]).expanduser())
    ):
        return "UDL_FFMPEG"
    return "path"


def install_ffmpeg(
    dest_dir: str | Path | None = None,
    *,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """download a static build for this OS/arch into dest_dir and return ffmpeg path."""
    key = platform_key()
    directory = default_install_dir(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)

    if key == "darwin-arm64":
        return _install_martin_macos_arm64(directory, progress=progress)
    if key == "windows-x86_64":
        return _install_btbn_windows_x64(directory, progress=progress)

    raise ffmpeg_provision_error(
        f"automatic FFmpeg install is only supported on macOS arm64 and "
        f"Windows x64 (this host is {key})"
    )


def _http_get(url: str, *, binary: bool = True) -> bytes:
    req = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(req, timeout=120) as resp:  # noqa: S310 — pinned reputable hosts
            data = resp.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ffmpeg_provision_error(f"download failed: {url}: {exc}") from exc
    if not data:
        raise ffmpeg_provision_error(f"empty download: {url}")
    if not binary:
        return data
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
        with urlopen(req, timeout=120) as resp, dest.open("wb") as out:  # noqa: S310
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
        raise ffmpeg_provision_error(f"download failed: {url}: {exc}") from exc

    digest = hasher.hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise ffmpeg_provision_error(
            f"checksum mismatch for {url}: expected {expected_sha256}, got {digest}"
        )
    return digest


def _parse_sha256_line(text: str, filename: str | None = None) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 1:
            continue
        digest = parts[0]
        if len(digest) != 64:
            continue
        if filename is None:
            return digest
        # formats: "hash  name" or "hash *name"
        name = parts[1].lstrip("*") if len(parts) > 1 else ""
        if name.endswith(filename) or name == filename:
            return digest
    if filename is None:
        # single-hash files
        for raw in text.splitlines():
            token = raw.strip().split()[0] if raw.strip() else ""
            if len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
                return token
    raise ffmpeg_provision_error(f"could not parse sha256 for {filename or 'archive'}")


def _resolve_redirect(url: str) -> str:
    req = Request(url, headers={"User-Agent": user_agent}, method="HEAD")
    try:
        with urlopen(req, timeout=60) as resp:  # noqa: S310
            return resp.geturl()
    except (HTTPError, URLError, TimeoutError, OSError):
        # some hosts dislike HEAD; fall back to GET range via full GET of tiny body
        req = Request(url, headers={"User-Agent": user_agent})
        try:
            with urlopen(req, timeout=60) as resp:  # noqa: S310
                return resp.geturl()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ffmpeg_provision_error(f"resolve failed: {url}: {exc}") from exc


def _install_martin_macos_arm64(
    directory: Path,
    *,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    final_url = _resolve_redirect(martin_macos_arm64_zip)
    checksum_url = f"{final_url}.sha256"
    checksum_text = _http_get(checksum_url, binary=False).decode("utf-8", errors="replace")
    expected = _parse_sha256_line(checksum_text, "ffmpeg.zip")

    with tempfile.TemporaryDirectory(prefix="udl-ffmpeg-") as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        _http_download_to(final_url, archive, progress=progress, expected_sha256=expected)
        with zipfile.ZipFile(archive) as zf:
            member = _pick_zip_member(zf, names=("ffmpeg",))
            target = directory / "ffmpeg"
            with zf.open(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
        target.chmod(0o755)
    if not target.is_file():
        raise ffmpeg_provision_error("extracted ffmpeg binary missing")
    return target.resolve()


def _install_btbn_windows_x64(
    directory: Path,
    *,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    archive_url = f"{btbn_latest_base}/{btbn_win64_lgpl_name}"
    checksums_url = f"{btbn_latest_base}/{btbn_checksums_name}"
    checksum_text = _http_get(checksums_url, binary=False).decode("utf-8", errors="replace")
    expected = _parse_sha256_line(checksum_text, btbn_win64_lgpl_name)

    with tempfile.TemporaryDirectory(prefix="udl-ffmpeg-") as tmp:
        archive = Path(tmp) / btbn_win64_lgpl_name
        _http_download_to(archive_url, archive, progress=progress, expected_sha256=expected)
        with zipfile.ZipFile(archive) as zf:
            member = _pick_zip_member(zf, names=("ffmpeg.exe",))
            target = directory / "ffmpeg.exe"
            with zf.open(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    if not target.is_file():
        raise ffmpeg_provision_error("extracted ffmpeg.exe missing")
    return target.resolve()


def _pick_zip_member(zf: zipfile.ZipFile, *, names: tuple[str, ...]) -> str:
    candidates: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        base = Path(info.filename).name.lower()
        if base in {n.lower() for n in names}:
            candidates.append(info.filename)
    if not candidates:
        raise ffmpeg_provision_error(f"archive missing {names}")
    # prefer .../bin/ffmpeg
    candidates.sort(key=lambda p: (0 if "/bin/" in p.replace("\\", "/").lower() else 1, len(p)))
    return candidates[0]


def provision_supported() -> bool:
    return platform_key() in {"darwin-arm64", "windows-x86_64"}
