"""unified item metadata model filled by every extractor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class item_metadata:
    """site-agnostic metadata every extractor fills when available.

    Missing fields stay None / empty — callers must tolerate partial data
    including external-tool extractors (MEGA) that only know handle/url.
    """

    site: str
    item_id: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    author: str | None = None
    upload_date: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    duration: float | None = None
    resolution: str | None = None
    thumbnail_url: str | None = None
    cover_url: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    collection: str | None = None
    collection_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, omit_empty: bool = False) -> dict[str, Any]:
        raw = asdict(self)
        if not omit_empty:
            return raw
        out: dict[str, Any] = {"site": self.site}
        for key, value in raw.items():
            if key == "site":
                continue
            if value is None:
                continue
            if value == [] or value == {}:
                continue
            out[key] = value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, site: str | None = None) -> item_metadata:
        if not data:
            return cls(site=site or "unknown")
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        extra = data.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {"value": extra}
        return cls(
            site=str(data.get("site") or site or "unknown"),
            item_id=_str_or_none(data.get("item_id")),
            canonical_url=_str_or_none(data.get("canonical_url")),
            title=_str_or_none(data.get("title")),
            author=_str_or_none(data.get("author")),
            upload_date=_str_or_none(data.get("upload_date")),
            tags=[str(t) for t in tags],
            description=_str_or_none(data.get("description")),
            duration=_float_or_none(data.get("duration")),
            resolution=_str_or_none(data.get("resolution")),
            thumbnail_url=_str_or_none(data.get("thumbnail_url")),
            cover_url=_str_or_none(data.get("cover_url")),
            view_count=_int_or_none(data.get("view_count")),
            like_count=_int_or_none(data.get("like_count")),
            collection=_str_or_none(data.get("collection")),
            collection_id=_str_or_none(data.get("collection_id")),
            extra=dict(extra),
        )

    def merge(self, other: item_metadata | None) -> item_metadata:
        """fill empty fields from other without overwriting set values."""
        if other is None:
            return self
        data = other.to_dict()
        mine = self.to_dict()
        for key, value in data.items():
            cur = mine.get(key)
            if cur is None or cur == [] or cur == {}:
                mine[key] = value
            elif key == "extra" and isinstance(cur, dict) and isinstance(value, dict):
                merged = dict(value)
                merged.update(cur)
                mine[key] = merged
        return item_metadata.from_dict(mine, site=self.site)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metadata_schema() -> dict[str, Any]:
    """machine-readable field list for udl schema / docs."""
    return {
        "site": "str",
        "item_id": "str?",
        "canonical_url": "str?",
        "title": "str?",
        "author": "str?",
        "upload_date": "str? (ISO-8601 date or datetime)",
        "tags": "list[str]",
        "description": "str?",
        "duration": "float? (seconds)",
        "resolution": "str? (e.g. 1920x1080 or quality label)",
        "thumbnail_url": "str?",
        "cover_url": "str?",
        "view_count": "int?",
        "like_count": "int?",
        "collection": "str? (album/playlist/folder title)",
        "collection_id": "str?",
        "extra": "object (site-specific keys)",
    }
