"""postprocess toggles: NFO / cover / embed with global + per-site switches."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# lowercase constants per project convention
known_sites = ("gofile", "bunkr", "iwara", "bilibili", "mega")
cover_naming = Literal["poster", "folder"]


def _default_site_flags() -> dict[str, bool]:
    return {site: True for site in known_sites}


@dataclass
class postprocess_options:
    """runtime options for NFO / cover postprocessors."""

    write_nfo: bool = False
    write_cover: bool = False
    embed_cover: bool = False
    cover_naming: cover_naming = "poster"
    # per-site toggles; missing keys inherit True when global is on
    nfo_sites: dict[str, bool] = field(default_factory=_default_site_flags)
    cover_sites: dict[str, bool] = field(default_factory=_default_site_flags)
    ffmpeg: str | None = None

    def nfo_enabled_for(self, site: str) -> bool:
        if not self.write_nfo:
            return False
        return bool(self.nfo_sites.get(site, True))

    def cover_enabled_for(self, site: str) -> bool:
        if not self.write_cover:
            return False
        return bool(self.cover_sites.get(site, True))

    def any_enabled(self) -> bool:
        return self.write_nfo or self.write_cover or self.embed_cover

    def to_dict(self) -> dict[str, Any]:
        return {
            "write_nfo": self.write_nfo,
            "write_cover": self.write_cover,
            "embed_cover": self.embed_cover,
            "cover_naming": self.cover_naming,
            "nfo_sites": dict(self.nfo_sites),
            "cover_sites": dict(self.cover_sites),
            "ffmpeg": self.ffmpeg,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> postprocess_options:
        if not data:
            return cls()
        naming = str(data.get("cover_naming") or "poster").strip().lower()
        if naming not in {"poster", "folder"}:
            naming = "poster"
        nfo_sites = _default_site_flags()
        raw_nfo = data.get("nfo_sites") or data.get("nfoSites") or {}
        if isinstance(raw_nfo, dict):
            for key, value in raw_nfo.items():
                nfo_sites[str(key)] = bool(value)
        cover_sites = _default_site_flags()
        raw_cover = data.get("cover_sites") or data.get("coverSites") or {}
        if isinstance(raw_cover, dict):
            for key, value in raw_cover.items():
                cover_sites[str(key)] = bool(value)
        return cls(
            write_nfo=bool(data.get("write_nfo") or data.get("writeNfo")),
            write_cover=bool(data.get("write_cover") or data.get("writeCover")),
            embed_cover=bool(data.get("embed_cover") or data.get("embedCover")),
            cover_naming=naming,  # type: ignore[arg-type]
            nfo_sites=nfo_sites,
            cover_sites=cover_sites,
            ffmpeg=(str(data["ffmpeg"]) if data.get("ffmpeg") else None),
        )


def parse_site_toggle_list(raw: str | None, *, default_on: bool = True) -> dict[str, bool]:
    """parse CLI --nfo-sites gofile,iwara or -mega style lists.

    Formats:
      - comma list of enabled sites (others off): gofile,bunkr
      - prefixed minus to disable: all,-mega
      - bare 'all' / 'none'
    """
    flags = {site: default_on for site in known_sites}
    if raw is None or not str(raw).strip():
        return flags
    text = str(raw).strip().lower()
    if text in {"all", "*"}:
        return {site: True for site in known_sites}
    if text in {"none", "off", "-"}:
        return {site: False for site in known_sites}

    tokens = [t.strip() for t in text.split(",") if t.strip()]
    has_positive = any(not t.startswith("-") and t not in {"all", "*"} for t in tokens)
    if has_positive:
        flags = {site: False for site in known_sites}
    for token in tokens:
        if token in {"all", "*"}:
            flags = {site: True for site in known_sites}
            continue
        if token.startswith("-"):
            site = token[1:]
            if site in flags:
                flags[site] = False
            continue
        if token in flags:
            flags[token] = True
    return flags
