"""Minimal protobuf wire decoder for bilibili DmSegMobileReply.

Avoids a full protobuf dependency; only parses DanmakuElem fields we need.
Wire layout matches bilibili.community.service.dm.v1 (seg.so).
"""

from __future__ import annotations

from typing import Any


def parse_dm_seg(data: bytes) -> list[dict[str, Any]]:
    """parse DmSegMobileReply bytes into danmaku entry dicts."""
    entries: list[dict[str, Any]] = []
    for field, value in _iter_fields(data):
        if field != 1 or not isinstance(value, (bytes, bytearray)):
            continue
        entry = _parse_danmaku_elem(bytes(value))
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_danmaku_elem(data: bytes) -> dict[str, Any] | None:
    fields: dict[int, Any] = {}
    for field, value in _iter_fields(data):
        fields[field] = value
    text = _as_str(fields.get(7))
    if not text:
        return None
    progress = int(fields.get(2) or 0)
    mode = int(fields.get(3) or 1)
    size = int(fields.get(4) or 25)
    color = int(fields.get(5) or 16777215)
    date = int(fields.get(8) or 0)
    uhash = _as_str(fields.get(6)) or "0"
    dmid = _as_str(fields.get(12)) or str(fields.get(1) or 0)
    return {
        "stime": progress,
        "mode": mode,
        "size": size,
        "color": color,
        "date": date,
        "uhash": uhash,
        "dmid": dmid,
        "text": text,
    }


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _iter_fields(data: bytes):
    i = 0
    n = len(data)
    while i < n:
        key, i = _read_varint(data, i)
        if i > n:
            break
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, i = _read_varint(data, i)
            yield field, value
        elif wire == 1:
            if i + 8 > n:
                break
            i += 8
        elif wire == 2:
            length, i = _read_varint(data, i)
            end = i + int(length)
            if end > n:
                break
            chunk = data[i:end]
            i = end
            yield field, chunk
        elif wire == 5:
            if i + 4 > n:
                break
            i += 4
        else:
            break


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    n = len(data)
    while i < n:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            break
    return result, i
