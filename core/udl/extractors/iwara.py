"""Iwara extractor stub (v0.2).

Planned port from Moeary/IwaraTool (MIT).
Do not implement Cloudflare challenge bypass by default.
See docs/extractor-interface.md and ROADMAP.md.
"""

from __future__ import annotations

from urllib.parse import urlparse

from udl.extractors.base import extract_result, extractor


class iwara_extractor(extractor):
    site = "iwara"
    label = "Iwara"

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password
        raise NotImplementedError(
            "Iwara extractor is planned for v0.2 — slot reserved in the registry"
        )


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "iwara.tv" in host or "iwara.ai" in host
