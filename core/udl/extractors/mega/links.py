"""Parse MEGA public file/folder links (modern + legacy)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# modern + legacy public link shapes
re_file_modern = re.compile(
    r"(?:^|/)(?:file|embed)/(?P<handle>[A-Za-z0-9_-]+)(?:#(?P<key>[A-Za-z0-9_-]+))?",
    re.I,
)
re_folder_modern = re.compile(
    r"(?:^|/)folder/(?P<handle>[A-Za-z0-9_-]+)(?:#(?P<key>[A-Za-z0-9_-]+))?",
    re.I,
)
re_file_legacy = re.compile(
    r"#!?(?P<handle>[A-Za-z0-9_-]+)!(?P<key>[A-Za-z0-9_-]+)",
    re.I,
)
re_folder_legacy = re.compile(
    r"#F!(?P<handle>[A-Za-z0-9_-]+)!(?P<key>[A-Za-z0-9_-]+)",
    re.I,
)
# optional subpath after folder key: /folder/H#K/folder/sub or #F!H!K!sub
re_folder_sub = re.compile(
    r"(?:/folder/(?P<sub>[A-Za-z0-9_-]+)|!(?P<sub_legacy>[A-Za-z0-9_-]+))\s*$",
    re.I,
)


@dataclass(frozen=True)
class link_info:
    kind: str  # file | folder
    handle: str
    key: str | None
    url: str
    sub_handle: str | None = None


re_cloud_node = re.compile(
    r"^(?:mega://node/|mega:node:)(?P<handle>[A-Za-z0-9_-]+)\s*$",
    re.I,
)


def matches(url: str) -> bool:
    try:
        raw = url.strip()
        if re_cloud_node.match(raw):
            return True
        return parse_link(url) is not None
    except ValueError:
        return False


def parse_cloud_node(url: str) -> str | None:
    m = re_cloud_node.match(url.strip())
    return m.group("handle") if m else None


def parse_link(url: str) -> link_info | None:
    raw = url.strip()
    if not raw:
        return None
    if parse_cloud_node(raw):
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"mega.nz", "mega.co.nz", "mega.io"}:
        return None

    path_q = unquote(parsed.path or "")
    fragment = unquote(parsed.fragment or "")
    combined = f"{path_q}#{fragment}" if fragment else path_q
    canonical = raw if "://" in raw else f"https://{raw}"

    m = re_folder_modern.search(combined)
    if m:
        sub = _folder_sub(combined, m.end())
        return link_info(
            kind="folder",
            handle=m.group("handle"),
            key=m.group("key"),
            url=canonical,
            sub_handle=sub,
        )
    m = re_file_modern.search(combined)
    if m:
        return link_info(
            kind="file",
            handle=m.group("handle"),
            key=m.group("key"),
            url=canonical,
        )
    m = re_folder_legacy.search(combined)
    if m:
        sub = None
        rest = combined[m.end() :]
        if rest.startswith("!") and len(rest) > 1:
            sub = rest[1:].split("!")[0].split("/")[0] or None
        return link_info(
            kind="folder",
            handle=m.group("handle"),
            key=m.group("key"),
            url=canonical,
            sub_handle=sub,
        )
    m = re_file_legacy.search(combined)
    if m:
        return link_info(
            kind="file",
            handle=m.group("handle"),
            key=m.group("key"),
            url=canonical,
        )
    return None


def _folder_sub(combined: str, after: int) -> str | None:
    rest = combined[after:]
    m = re.search(r"/folder/([A-Za-z0-9_-]+)", rest, re.I)
    if m:
        return m.group(1)
    if rest.startswith("!"):
        part = rest[1:].split("!")[0].split("/")[0]
        return part or None
    return None


def canonical_file_url(handle: str, key: str) -> str:
    return f"https://mega.nz/file/{handle}#{key}"


def canonical_folder_url(handle: str, key: str, sub: str | None = None) -> str:
    base = f"https://mega.nz/folder/{handle}#{key}"
    if sub:
        return f"{base}/folder/{sub}"
    return base
