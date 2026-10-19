"""Danmaku export (xml / ass / json) adapted from Bili23-Downloader patterns.

Uses the classic XML comment API so we do not require protobuf deps.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

danmaku_formats = ("xml", "ass", "json")


@dataclass
class ass_style:
    font: str = "Microsoft YaHei"
    font_size: int = 36
    alpha: float = 0.8  # 0..1 text opacity
    outline: float = 1.0
    shadow: float = 0.0
    scroll_seconds: float = 8.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "font": self.font,
            "font_size": self.font_size,
            "alpha": self.alpha,
            "outline": self.outline,
            "shadow": self.shadow,
            "scroll_seconds": self.scroll_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ass_style:
        data = data or {}
        return cls(
            font=str(data.get("font") or "Microsoft YaHei"),
            font_size=int(data.get("font_size") or 36),
            alpha=float(data.get("alpha") if data.get("alpha") is not None else 0.8),
            outline=float(data.get("outline") if data.get("outline") is not None else 1.0),
            shadow=float(data.get("shadow") if data.get("shadow") is not None else 0.0),
            scroll_seconds=float(
                data.get("scroll_seconds")
                if data.get("scroll_seconds") is not None
                else 8.0
            ),
        )


def parse_xml_danmaku(xml_text: str) -> list[dict[str, Any]]:
    """parse classic bilibili danmaku XML into dict entries."""
    entries: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return entries
    for node in root.findall("d"):
        p = node.get("p") or ""
        text = node.text or ""
        parts = p.split(",")
        if len(parts) < 8:
            continue
        try:
            stime = float(parts[0])
            mode = int(parts[1])
            size = int(float(parts[2]))
            color = int(parts[3])
            date = int(parts[4])
            uhash = parts[6]
            dmid = parts[7]
        except (TypeError, ValueError):
            continue
        entries.append(
            {
                "stime": int(stime * 1000),
                "mode": mode,
                "size": size,
                "color": color,
                "date": date,
                "uhash": uhash,
                "dmid": dmid,
                "text": text,
            }
        )
    return entries


def to_xml(entries: list[dict[str, Any]], *, cid: int) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<i>",
        "    <chatserver>chat.bilibili.com</chatserver>",
        f"    <chatid>{cid}</chatid>",
        "    <mission>0</mission>",
        "    <maxlimit>1500</maxlimit>",
        "    <state>0</state>",
        "    <real_name>0</real_name>",
        "    <source>k-v</source>",
    ]
    for entry in entries:
        stime = float(entry.get("stime", 0)) / 1000.0
        mode = int(entry.get("mode", 1))
        size = int(entry.get("size", 25))
        color = int(entry.get("color", 16777215))
        date = int(entry.get("date", 0))
        uhash = entry.get("uhash", 0)
        dmid = entry.get("dmid", 0)
        text = _xml_text(str(entry.get("text", "")))
        lines.append(
            f'    <d p="{stime:.5f},{mode},{size},{color},{date},0,{uhash},{dmid}">'
            f"{text}</d>"
        )
    lines.append("</i>")
    return "\n".join(lines)


def to_json(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, ensure_ascii=False, indent=2)


def to_ass(
    entries: list[dict[str, Any]],
    *,
    title: str = "danmaku",
    style: ass_style | None = None,
    play_res_x: int = 1920,
    play_res_y: int = 1080,
) -> str:
    style = style or ass_style()
    alpha_hex = f"{int(max(0.0, min(1.0, 1.0 - style.alpha)) * 255):02X}"
    style_fmt = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    style_line = (
        f"Style: Default,{style.font},{style.font_size},"
        f"&H{alpha_hex}FFFFFF,&H{alpha_hex}FFFFFF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},2,20,20,20,1"
    )
    header = f"""[Script Info]
Title: {title}
ScriptType: v4.00+
Collisions: Normal
PlayResX: {play_res_x}
PlayResY: {play_res_y}
Timer: 100.0000

[V4+ Styles]
{style_fmt}
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip()]
    row = 0
    for entry in entries:
        text = _ass_text(str(entry.get("text", "")))
        if not text:
            continue
        start_ms = int(entry.get("stime", 0))
        end_ms = start_ms + int(style.scroll_seconds * 1000)
        mode = int(entry.get("mode", 1))
        color = int(entry.get("color", 16777215))
        bgr = _bgr_ass(color)
        y = 60 + (row % 12) * max(24, style.font_size)
        row += 1
        if mode in {4, 5}:  # bottom / top static
            align = 8 if mode == 5 else 2
            lines.append(
                f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},Default,,"
                f"0,0,0,,{{\\an{align}\\c{bgr}}}{text}"
            )
        else:
            # scroll right → left
            lines.append(
                f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},Default,,"
                f"0,0,0,,{{\\move({play_res_x + 40},{y},{-len(text) * style.font_size},{y})"
                f"\\c{bgr}}}{text}"
            )
    return "\n".join(lines) + "\n"


def write_danmaku(
    entries: list[dict[str, Any]],
    dest: Path,
    *,
    fmt: str,
    cid: int,
    title: str = "danmaku",
    style: ass_style | None = None,
) -> Path:
    fmt = fmt.lower().strip()
    if fmt not in danmaku_formats:
        raise ValueError(f"unsupported danmaku format: {fmt}")
    dest = dest.with_suffix(f".{fmt}" if not dest.suffix else dest.suffix)
    if fmt == "xml":
        content = to_xml(entries, cid=cid)
    elif fmt == "ass":
        content = to_ass(entries, title=title, style=style)
    else:
        content = to_json(entries)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def _xml_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return escape(text)


def _ass_text(text: str) -> str:
    text = text.replace("\n", " ").replace("\r", "")
    text = text.replace("{", "(").replace("}", ")")
    return text


def _ass_time(ms: int) -> str:
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    cs = (ms % 1000) // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _bgr_ass(rgb: int) -> str:
    r = (rgb >> 16) & 0xFF
    g = (rgb >> 8) & 0xFF
    b = rgb & 0xFF
    return f"&H{b:02X}{g:02X}{r:02X}&"
