"""Generic multi-connection segmented download planner.

MEGA built-in uses fixed／protocol chunk layouts via this module.
HTTP IDM-style dynamic segmentation lives in ``udl.download.segmented``
(separate resume file ``.udl-seg.json`` with ETag／Last-Modified validators).
MEGA-specific decryption stays in the MEGA downloader; this module only
plans byte ranges and persists completed chunk ids.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

# lowercase constants
default_segment_size = 4 * 1024 * 1024
state_suffix = ".udl.segments.json"


@dataclass(frozen=True)
class segment_range:
    index: int
    start: int
    end: int  # exclusive

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass
class segment_plan:
    total_size: int
    segments: list[segment_range]
    # optional protocol hint for engines that need named chunk ids
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class segment_state:
    """persisted resume state next to the output file."""

    total_size: int
    completed: set[int]
    path: Path
    updated_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def mark_done(self, index: int) -> None:
        with self._lock:
            self.completed.add(index)
            self.updated_at = time.time()
            self.save()

    def is_done(self, index: int) -> bool:
        with self._lock:
            return index in self.completed

    def save(self) -> None:
        payload = {
            "version": 1,
            "total_size": self.total_size,
            "completed": sorted(self.completed),
            "updated_at": self.updated_at,
            "meta": self.meta,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        *,
        total_size: int,
        meta: dict[str, Any] | None = None,
    ) -> segment_state:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if int(data.get("total_size") or -1) == total_size:
                    completed = {int(x) for x in (data.get("completed") or [])}
                    return cls(
                        total_size=total_size,
                        completed=completed,
                        path=path,
                        updated_at=float(data.get("updated_at") or time.time()),
                        meta=dict(data.get("meta") or meta or {}),
                    )
            except (OSError, ValueError, TypeError):
                pass
        state = cls(
            total_size=total_size,
            completed=set(),
            path=path,
            meta=dict(meta or {}),
        )
        state.save()
        return state

    def clear(self) -> None:
        with self._lock:
            if self.path.is_file():
                try:
                    self.path.unlink()
                except OSError:
                    pass


def plan_fixed_segments(
    total_size: int,
    *,
    segment_size: int = default_segment_size,
) -> segment_plan:
    """split [0, total) into fixed-size ranges (last may be shorter)."""
    if total_size < 0:
        raise ValueError("total_size must be >= 0")
    if total_size == 0:
        return segment_plan(total_size=0, segments=[])
    size = max(int(segment_size), 1)
    segments: list[segment_range] = []
    start = 0
    index = 0
    while start < total_size:
        end = min(start + size, total_size)
        segments.append(segment_range(index=index, start=start, end=end))
        start = end
        index += 1
    return segment_plan(total_size=total_size, segments=segments)


def plan_mega_transfer_chunks(
    total_size: int,
    *,
    size_multi: int = 20,
) -> segment_plan:
    """MegaBasterd-style transfer chunks (size_multi=20 → 20 MiB after ramp).

    CBC-MAC still uses protocol chunks (size_multi=1); callers must not reuse
    these ranges for integrity verification.
    """
    if total_size <= 0:
        return segment_plan(total_size=total_size, segments=[], meta={"kind": "mega"})
    segments: list[segment_range] = []
    chunk_id = 1
    index = 0
    while True:
        offset = _mega_chunk_offset(chunk_id, size_multi)
        if offset >= total_size:
            break
        chunk_size = _mega_chunk_size(chunk_id, total_size, offset, size_multi)
        if chunk_size <= 0:
            break
        segments.append(
            segment_range(index=index, start=offset, end=offset + chunk_size)
        )
        index += 1
        chunk_id += 1
    return segment_plan(
        total_size=total_size,
        segments=segments,
        meta={"kind": "mega", "size_multi": size_multi},
    )


def state_path_for(dest: Path) -> Path:
    return dest.with_name(dest.name + state_suffix)


def _mega_chunk_offset(chunk_id: int, size_multi: int) -> int:
    offs = [0, 128, 384, 768, 1280, 1920, 2688]
    if chunk_id <= 7:
        return offs[chunk_id - 1] * 1024
    return (3584 + (chunk_id - 8) * 1024 * size_multi) * 1024


def _mega_chunk_size(
    chunk_id: int, file_size: int, offset: int, size_multi: int
) -> int:
    if 1 <= chunk_id <= 7:
        chunk_size = chunk_id * 128 * 1024
    else:
        chunk_size = 1024 * 1024 * size_multi
    if offset + chunk_size > file_size:
        chunk_size = file_size - offset
    return chunk_size
