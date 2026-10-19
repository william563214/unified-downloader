"""local filters for Iwara browse / subscription batch results.

Adapted from Moeary/IwaraTool MIT search filtering patterns
https://github.com/Moeary/IwaraTool
commit 1e98c96c422a169699c0d16bcab49b8a7f5238d5
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal


@dataclass
class browse_filters:
    """batch filters for likes views date tags and title keywords."""

    min_likes: int | None = None
    max_likes: int | None = None
    min_views: int | None = None
    max_views: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    title_keywords: tuple[str, ...] = ()
    title_mode: Literal["all", "any"] = "all"
    rating: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_likes": self.min_likes,
            "max_likes": self.max_likes,
            "min_views": self.min_views,
            "max_views": self.max_views,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "include_tags": list(self.include_tags),
            "exclude_tags": list(self.exclude_tags),
            "title_keywords": list(self.title_keywords),
            "title_mode": self.title_mode,
            "rating": self.rating,
        }


def split_terms(value: str | None) -> tuple[str, ...]:
    parts = re.split(r"[,，;；|\s]+", str(value or ""))
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = part.strip().lstrip("#")
        if not term:
            continue
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def filters_from_args(
    *,
    min_likes: int | None = None,
    max_likes: int | None = None,
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_tags: str | None = None,
    exclude_tags: str | None = None,
    title: str | None = None,
    title_mode: str = "all",
    rating: str | None = None,
) -> browse_filters:
    mode = title_mode if title_mode in {"all", "any"} else "all"
    return browse_filters(
        min_likes=min_likes,
        max_likes=max_likes,
        min_views=min_views,
        max_views=max_views,
        date_from=parse_date(date_from),
        date_to=parse_date(date_to),
        include_tags=split_terms(include_tags),
        exclude_tags=split_terms(exclude_tags),
        title_keywords=split_terms(title),
        title_mode=mode,  # type: ignore[arg-type]
        rating=(rating or "").strip().lower() or None,
    )


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return default


def item_passes(item: dict[str, Any], filters: browse_filters | None) -> bool:
    if filters is None:
        return True
    rating = str(item.get("rating") or "").strip().lower()
    wanted = (filters.rating or "").strip().lower()
    if wanted and wanted not in {"all", "*"} and rating and rating != wanted:
        return False

    likes = _as_int(item.get("like_count", item.get("numLikes", item.get("likes", 0))))
    views = _as_int(item.get("view_count", item.get("numViews", item.get("views", 0))))
    if filters.min_likes is not None and likes < filters.min_likes:
        return False
    if filters.max_likes is not None and likes > filters.max_likes:
        return False
    if filters.min_views is not None and views < filters.min_views:
        return False
    if filters.max_views is not None and views > filters.max_views:
        return False

    published = parse_date(
        item.get("upload_date") or item.get("createdAt") or item.get("published_at")
    )
    if filters.date_from and (published is None or published < filters.date_from):
        return False
    if filters.date_to and (published is None or published > filters.date_to):
        return False

    raw_tags = item.get("tags") or []
    tags: set[str] = set()
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, dict):
                label = tag.get("id") or tag.get("name") or tag.get("label")
                if label:
                    tags.add(str(label).casefold())
            elif tag:
                tags.add(str(tag).casefold())
    elif isinstance(raw_tags, str):
        tags.update(t.casefold() for t in split_terms(raw_tags))

    include = {t.casefold() for t in filters.include_tags}
    exclude = {t.casefold() for t in filters.exclude_tags}
    if include and not all(any(term in tag for tag in tags) for term in include):
        return False
    if any(term in tag for term in exclude for tag in tags):
        return False

    keywords = filters.title_keywords
    if keywords:
        title = str(item.get("title") or "").casefold()
        checks = [term.casefold() in title for term in keywords]
        if filters.title_mode == "any":
            if not any(checks):
                return False
        elif not all(checks):
            return False
    return True


def apply_filters(
    items: list[dict[str, Any]],
    filters: browse_filters | None,
) -> list[dict[str, Any]]:
    if filters is None:
        return items
    return [item for item in items if item_passes(item, filters)]
