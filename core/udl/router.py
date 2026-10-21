"""URL router / site registry with lazy extractor loading.

Matching uses per-class `_VALID_URL` (yt-dlp-inspired). By default the
registry is built from generated `lazy_extractors` stubs so importing
`udl.router` does not load every site module. Instantiating a matched
extractor imports only that module.

Set `UDL_NO_LAZY_EXTRACTORS=1` to force eager imports (used by
`scripts/make_lazy_extractors.py`).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from udl.extractors.base import extractor

Matcher = Callable[[str], bool]
Factory = Callable[[], extractor]


def _load_extractor_classes() -> list[type[extractor]]:
    no_lazy = os.environ.get("UDL_NO_LAZY_EXTRACTORS", "").strip().lower()
    if no_lazy in {"1", "true", "yes", "on"}:
        from udl.extractors._extractors import extractors as eager

        return list(eager)
    try:
        from udl.extractors.lazy_extractors import extractors as lazy

        return list(lazy)
    except ImportError:
        from udl.extractors._extractors import extractors as eager

        return list(eager)


@dataclass(frozen=True)
class site_entry:
    site: str
    label: str
    match: Matcher
    factory: Factory
    status: str  # implemented | stub | external
    kind_hint: str
    cls: type[extractor] | None = None

    @classmethod
    def from_extractor(cls, ie: type[extractor]) -> site_entry:
        return cls(
            site=ie.site,
            label=ie.label,
            match=ie.suitable,
            factory=ie,
            status=ie.status,
            kind_hint=ie.kind_hint,
            cls=ie,
        )


# registry order matters — first match wins (direct last)
registry: list[site_entry] = [
    site_entry.from_extractor(ie) for ie in _load_extractor_classes()
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
            message="unsupported URL",
        )

    supported = entry.status in {"implemented", "external"}
    message = None
    if entry.status == "stub":
        message = f"{entry.label} is reserved but not implemented yet"
    elif entry.status == "external":
        if entry.site == "gallerydl":
            message = (
                f"{entry.label} downloads via an external gallery-dl CLI "
                "(subprocess only; GPL-2.0-only — not imported)"
            )
        else:
            message = (
                f"{entry.label} downloads via an external CLI "
                "(megatools preferred; MEGAcmd mega-get fallback)"
            )
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
    if info.entry.status not in {"implemented", "external"}:
        raise NotImplementedError(info.message or f"{info.site} not implemented")
    return info.entry.factory()
