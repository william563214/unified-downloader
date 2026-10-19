"""URL router / site registry (gallery-dl-like design concept only).

Do NOT import or copy gallery-dl (GPL-2.0-only). Patterns and extractors
live here so the CLI and GUI share one source of truth.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from udl.extractors import bilibili as bilibili_mod
from udl.extractors import bunkr as bunkr_mod
from udl.extractors import gofile as gofile_mod
from udl.extractors import iwara as iwara_mod
from udl.extractors.base import extractor

Matcher = Callable[[str], bool]
Factory = Callable[[], extractor]


@dataclass(frozen=True)
class site_entry:
    site: str
    label: str
    match: Matcher
    factory: Factory
    status: str  # implemented | stub | external
    kind_hint: str


# registry order matters — first match wins
registry: list[site_entry] = [
    site_entry(
        site="gofile",
        label="Gofile",
        match=gofile_mod.matches,
        factory=gofile_mod.gofile_extractor,
        status="implemented",
        kind_hint="folder|file",
    ),
    site_entry(
        site="bunkr",
        label="Bunkr",
        match=bunkr_mod.matches,
        factory=bunkr_mod.bunkr_extractor,
        status="implemented",
        kind_hint="album|file",
    ),
    site_entry(
        site="iwara",
        label="Iwara",
        match=iwara_mod.matches,
        factory=iwara_mod.iwara_extractor,
        status="stub",
        kind_hint="video|playlist|profile",
    ),
    site_entry(
        site="bilibili",
        label="Bilibili",
        match=bilibili_mod.matches,
        factory=bilibili_mod.bilibili_extractor,
        status="stub",
        kind_hint="video",
    ),
]


@dataclass
class resolve_info:
    site: str | None
    label: str | None
    kind: str | None
    url: str
    supported: bool
    status: str | None
    message: str | None = None
    entry: site_entry | None = None


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("empty URL")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        if "." in url:
            url = f"https://{url}"
            parsed = urlparse(url)
        else:
            raise ValueError(f"not an HTTP URL: {url}")
    if not parsed.netloc:
        raise ValueError(f"missing host: {url}")
    return url


def match(url: str) -> site_entry | None:
    url = normalize_url(url)
    for entry in registry:
        if entry.match(url):
            return entry
    return None


def resolve(url: str) -> resolve_info:
    """map URL to a site without fetching remote content."""
    try:
        url = normalize_url(url)
    except ValueError as exc:
        return resolve_info(
            site=None,
            label=None,
            kind=None,
            url=url if url else "",
            supported=False,
            status=None,
            message=str(exc),
        )

    entry = match(url)
    if entry is None:
        return resolve_info(
            site=None,
            label=None,
            kind=None,
            url=url,
            supported=False,
            status=None,
            message="unsupported site — no generic fallback in v0.1",
        )

    supported = entry.status == "implemented"
    message = None
    if entry.status == "stub":
        message = f"{entry.label} is reserved but not implemented yet"
    return resolve_info(
        site=entry.site,
        label=entry.label,
        kind=entry.kind_hint,
        url=url,
        supported=supported,
        status=entry.status,
        message=message,
        entry=entry,
    )


def get_extractor(url: str) -> extractor:
    info = resolve(url)
    if info.entry is None:
        raise ValueError(info.message or "unsupported URL")
    if info.entry.status != "implemented":
        raise NotImplementedError(info.message or f"{info.site} not implemented")
    return info.entry.factory()
