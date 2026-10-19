"""file hashing helpers for history and duplicate detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

hash_chunk = 1024 * 1024


def sha256_file(path: str | Path) -> str | None:
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as fh:
        while True:
            chunk = fh.read(hash_chunk)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
