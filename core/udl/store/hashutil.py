"""file hashing helpers for history duplicate detection and checksum verify."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

hash_chunk = 1024 * 1024
# files at or above this size use fast partial hash unless full is forced
large_file_bytes = 64 * 1024 * 1024
partial_head_bytes = 1024 * 1024
partial_tail_bytes = 1024 * 1024
hash_mode_name = Literal["full", "fast", "auto", "off"]
hash_mode_env = "UDL_HASH_MODE"
hash_algo = Literal["md5", "sha1", "sha256"]
supported_algos: tuple[str, ...] = ("md5", "sha1", "sha256")


def resolve_hash_mode(override: str | None = None) -> hash_mode_name:
    raw = (override or os.environ.get(hash_mode_env) or "auto").strip().lower()
    if raw in {"full", "fast", "auto", "off", "none"}:
        if raw == "none":
            return "off"
        return raw  # type: ignore[return-value]
    return "auto"


def hash_file(path: str | Path, algo: str = "sha256") -> str | None:
    """return full-file hex digest for path or None if missing / unknown algo."""
    name = (algo or "sha256").strip().lower()
    if name not in supported_algos:
        return None
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.new(name)
    with target.open("rb") as fh:
        while True:
            chunk = fh.read(hash_chunk)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str | Path) -> str | None:
    """full-file sha256 hex digest."""
    return hash_file(path, "sha256")


def md5_file(path: str | Path) -> str | None:
    return hash_file(path, "md5")


def sha1_file(path: str | Path) -> str | None:
    return hash_file(path, "sha1")


def sha256_file_fast(path: str | Path) -> str | None:
    """partial hash for large files size plus head and tail bytes.

    format prefix ``partial:`` so callers can distinguish from full digests.
    """
    target = Path(path)
    if not target.is_file():
        return None
    size = target.stat().st_size
    digest = hashlib.sha256()
    digest.update(f"size:{size}\n".encode())
    with target.open("rb") as fh:
        head = fh.read(partial_head_bytes)
        digest.update(head)
        if size > partial_head_bytes + partial_tail_bytes:
            fh.seek(max(0, size - partial_tail_bytes))
            digest.update(fh.read(partial_tail_bytes))
        elif size > partial_head_bytes:
            digest.update(fh.read())
    return f"partial:{digest.hexdigest()}"


def hash_file_for_history(
    path: str | Path,
    *,
    mode: str | None = None,
) -> str | None:
    """choose full or fast hash based on size and UDL_HASH_MODE."""
    resolved = resolve_hash_mode(mode)
    if resolved == "off":
        return None
    target = Path(path)
    if not target.is_file():
        return None
    size = target.stat().st_size
    if resolved == "fast":
        return sha256_file_fast(target)
    if resolved == "full":
        return sha256_file(target)
    # auto: full for small files, fast partial for large
    if size >= large_file_bytes:
        return sha256_file_fast(target)
    return sha256_file(target)


def hash_file_background(
    path: str | Path,
    *,
    on_done: Callable[[str | None], None],
    mode: str | None = None,
) -> threading.Thread:
    """compute history hash on a daemon thread then invoke on_done."""

    def worker() -> None:
        try:
            digest = hash_file_for_history(path, mode=mode)
        except OSError:
            digest = None
        on_done(digest)

    thread = threading.Thread(target=worker, name="udl-hash", daemon=True)
    thread.start()
    return thread


def normalize_hex(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    # allow "hash  filename" sidecar lines
    if " " in text or "\t" in text:
        text = text.split()[0]
    if not text or any(c not in "0123456789abcdef" for c in text):
        return None
    return text


def detect_algo_from_hex(value: str) -> str | None:
    length = len(value)
    if length == 32:
        return "md5"
    if length == 40:
        return "sha1"
    if length == 64:
        return "sha256"
    return None
