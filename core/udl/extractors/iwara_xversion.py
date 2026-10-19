"""Iwara X-Version request signature (isolated module).

Ported from Moeary/IwaraTool ``app/core/api/auth_tokens.py`` and
``IwaraAPI.compute_x_version`` (MIT).

The site signs file-source requests with ``X-Version`` =
``sha1("{file_uuid}_{expires}_{salt}")``. Salts are reverse-engineered
shared secrets that rotate on the website side.

If every known salt is rejected raise ``iwara_xversion_error`` so callers
can surface a clear maintenance message instead of a generic HTTP failure.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator, Sequence
from urllib.parse import parse_qs, urlparse

# documented constants — keep in sync with Moeary/IwaraTool auth_tokens.py
# newer salt first then legacy fallback
x_version_salts: tuple[str, ...] = (
    "mSvL05GfEmeEmsEYfGCnVpEjYgTJraJN",
    "5nFp9kmbNnHdAFhaqMvt",
)

x_version_rotate_hint = (
    "Iwara X-Version signature rejected for all known salts. "
    "The site may have rotated the shared secret. "
    "Update udl.extractors.iwara_xversion.x_version_salts "
    "(see NOTICE / upstream Moeary/IwaraTool) and retry."
)


class iwara_xversion_error(RuntimeError):
    """raised when no known salt produces a valid file-source response."""


def compute_x_version(file_url: str, salt: str) -> str:
    """sha1 of ``{uuid}_{expires}_{salt}`` as required by Iwara file API."""
    match = re.search(r"/file/([^?#]+)", file_url)
    filename = match.group(1) if match else ""
    expires = parse_qs(urlparse(file_url).query).get("expires", [""])[0]
    raw = f"{filename}_{expires}_{salt}"
    return hashlib.sha1(raw.encode()).hexdigest()


def iter_x_versions(
    file_url: str,
    salts: Sequence[str] | None = None,
) -> Iterator[tuple[str, str]]:
    """yield ``(salt, x_version)`` for each salt to try."""
    for salt in salts if salts is not None else x_version_salts:
        if not salt:
            continue
        yield salt, compute_x_version(file_url, salt)


def merge_salts(extra: Iterable[str] | None = None) -> tuple[str, ...]:
    """user extras first then built-in salts without duplicates."""
    merged: list[str] = []
    for salt in (*(extra or ()), *x_version_salts):
        text = str(salt).strip()
        if text and text not in merged:
            merged.append(text)
    return tuple(merged)
