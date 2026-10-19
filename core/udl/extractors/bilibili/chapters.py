"""Chapter metadata → FFmpeg ffmetadata (mp4 / mkv)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def view_points_from_player(player_data: dict[str, Any]) -> list[dict[str, Any]]:
    points = player_data.get("view_points") or []
    return [p for p in points if isinstance(p, dict)]


def to_ffmetadata(
    view_points: list[dict[str, Any]],
    *,
    duration_sec: float | None = None,
) -> str | None:
    if not view_points:
        return None
    lines = [";FFMETADATA1", ""]
    for index, entry in enumerate(view_points):
        start = int(entry.get("from") or 0)
        end = int(entry.get("to") or 0)
        if end <= start:
            if index + 1 < len(view_points):
                end = int(view_points[index + 1].get("from") or 0)
            elif duration_sec is not None:
                end = int(duration_sec)
            if end <= start:
                continue
        title = _escape(str(entry.get("content") or entry.get("title") or f"Chapter {index + 1}"))
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start * 1000}",
                f"END={end * 1000}",
                f"title={title}",
                "",
            ]
        )
    if len(lines) <= 2:
        return None
    return "\n".join(lines)


def write_ffmetadata(content: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def _escape(content: str) -> str:
    return "".join("\\" + ch if ch in "=;#\\\n" else ch for ch in content)
