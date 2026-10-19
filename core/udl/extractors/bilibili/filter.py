"""Batch auto-select filters for Bilibili probe parts / list items."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass
class select_filter:
    """conditions for auto-selecting parts from a probe tree."""

    title_keyword: str | None = None
    date_from: str | None = None  # YYYY-MM-DD
    date_to: str | None = None
    duration_min: float | None = None  # seconds
    duration_max: float | None = None
    page_from: int | None = None  # inclusive 1-based
    page_to: int | None = None
    checked_only: bool = False

    def is_empty(self) -> bool:
        return not any(
            [
                self.title_keyword,
                self.date_from,
                self.date_to,
                self.duration_min is not None,
                self.duration_max is not None,
                self.page_from is not None,
                self.page_to is not None,
                self.checked_only,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_keyword": self.title_keyword,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "duration_min": self.duration_min,
            "duration_max": self.duration_max,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "checked_only": self.checked_only,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> select_filter:
        data = data or {}
        return cls(
            title_keyword=_str_or_none(data.get("title_keyword") or data.get("keyword")),
            date_from=_str_or_none(data.get("date_from")),
            date_to=_str_or_none(data.get("date_to")),
            duration_min=_float_or_none(data.get("duration_min")),
            duration_max=_float_or_none(data.get("duration_max")),
            page_from=_int_or_none(data.get("page_from") or data.get("part_from")),
            page_to=_int_or_none(data.get("page_to") or data.get("part_to")),
            checked_only=bool(data.get("checked_only") or False),
        )


def apply_filter(
    parts: list[dict[str, Any]],
    filt: select_filter | None,
    *,
    checked_pages: set[int] | None = None,
) -> list[dict[str, Any]]:
    """return parts matching filter conditions (all if filt empty)."""
    if filt is None or filt.is_empty():
        if checked_pages is not None:
            return [p for p in parts if int(p.get("page") or 0) in checked_pages]
        return list(parts)

    keyword = (filt.title_keyword or "").strip().lower()
    date_from = _parse_date(filt.date_from)
    date_to = _parse_date(filt.date_to)
    out: list[dict[str, Any]] = []
    for part in parts:
        page = int(part.get("page") or 0)
        if filt.checked_only and checked_pages is not None and page not in checked_pages:
            continue
        if filt.page_from is not None and page < filt.page_from:
            continue
        if filt.page_to is not None and page > filt.page_to:
            continue
        title = str(part.get("title") or "")
        if keyword and keyword not in title.lower():
            continue
        duration = _float_or_none(part.get("duration"))
        if filt.duration_min is not None:
            if duration is None or duration < filt.duration_min:
                continue
        if filt.duration_max is not None:
            if duration is None or duration > filt.duration_max:
                continue
        upload = _parse_date(
            part.get("upload_date") or part.get("pubdate") or part.get("ctime")
        )
        if date_from is not None:
            if upload is None or upload < date_from:
                continue
        if date_to is not None:
            if upload is None or upload > date_to:
                continue
        out.append(part)
    return out


def parse_part_range(spec: str | None) -> tuple[int | None, int | None]:
    """parse '1-5' / '3' / 'all' into (from, to)."""
    if not spec or str(spec).lower() in {"all", ""}:
        return None, None
    text = str(spec).strip()
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    if text.isdigit():
        n = int(text)
        return n, n
    raise ValueError(f"invalid part range: {spec}")


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # unix timestamp
        try:
            return datetime.utcfromtimestamp(int(value)).date()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        try:
            return datetime.utcfromtimestamp(int(text)).date()
        except (OSError, OverflowError, ValueError):
            return None
    try:
        return date.fromisoformat(text[:10].replace("/", "-"))
    except ValueError:
        return None


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
