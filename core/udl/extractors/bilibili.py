"""Bilibili extractor stub (v0.3).

Planned port from ScottSloan/Bili23-Downloader (GPL-3.0).
Do not implement paywall / VIP / geo bypass.
See docs/extractor-interface.md and ROADMAP.md.
"""

from __future__ import annotations

from urllib.parse import urlparse

from udl.extractors.base import extract_result, extractor


class bilibili_extractor(extractor):
    site = "bilibili"
    label = "Bilibili"

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password
        raise NotImplementedError(
            "Bilibili extractor is planned for v0.3 — slot reserved in the registry"
        )


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return (
        "bilibili.com" in host
        or "b23.tv" in host
        or "bili2233.cn" in host
        or host.endswith("biliapi.net")
    )
