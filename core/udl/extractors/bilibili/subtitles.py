"""Subtitle export (srt / lrc / txt / ass / json) for Bilibili player tracks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from udl.extractors.bilibili.danmaku import _ass_text, _ass_time, ass_style

subtitle_formats = ("srt", "lrc", "txt", "ass", "json")

ai_language_prefix = "ai-"

language_code_map = {
    "zh": "chi",
    "zh-cn": "chi",
    "zh-hans": "chi",
    "zh-hant": "chi",
    "zh-hk": "chi",
    "zh-tw": "chi",
    "en": "eng",
    "en-us": "eng",
    "en-gb": "eng",
    "ja": "jpn",
    "jp": "jpn",
    "ko": "kor",
    "es": "spa",
    "ar": "ara",
    "pt": "por",
    "fr": "fre",
    "de": "ger",
    "ru": "rus",
    "it": "ita",
    "vi": "vie",
    "th": "tha",
    "id": "ind",
    "ms": "may",
    "hi": "hin",
    "tr": "tur",
    "pl": "pol",
    "nl": "dut",
    "tl": "tgl",
}


@dataclass
class subtitle_track:
    language: str
    language_doc: str
    url: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "language_doc": self.language_doc,
            "url": self.url,
        }


def tracks_from_player(player_data: dict[str, Any]) -> list[subtitle_track]:
    subtitle = player_data.get("subtitle") or {}
    raw = subtitle.get("subtitles") or []
    out: list[subtitle_track] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        lan = str(entry.get("lan") or entry.get("language") or "und")
        doc = str(entry.get("lan_doc") or entry.get("language_doc") or lan)
        url = str(entry.get("subtitle_url") or entry.get("url") or "")
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            continue
        out.append(subtitle_track(language=lan, language_doc=doc, url=url))
    return out


def filter_tracks(
    tracks: list[subtitle_track],
    *,
    languages: list[str] | None = None,
) -> list[subtitle_track]:
    if not languages:
        return list(tracks)
    wanted = {lan.lower() for lan in languages}
    return [
        t
        for t in tracks
        if t.language.lower() in wanted
        or t.language.lower().removeprefix(ai_language_prefix) in wanted
        or "all" in wanted
    ]


def body_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    body = data.get("body")
    if isinstance(body, list):
        return [b for b in body if isinstance(b, dict)]
    return []


def to_srt(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for i, item in enumerate(body_list(data), start=1):
        start = float(item.get("from") or 0)
        end = float(item.get("to") or start)
        content = str(item.get("content") or "").replace("\r", "")
        lines.append(str(i))
        lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def to_lrc(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in body_list(data):
        start = float(item.get("from") or 0)
        content = str(item.get("content") or "").replace("\n", " ")
        lines.append(f"[{_lrc_time(start)}]{content}")
    return "\n".join(lines) + ("\n" if lines else "")


def to_txt(data: dict[str, Any]) -> str:
    return "\n".join(str(item.get("content") or "") for item in body_list(data)) + (
        "\n" if body_list(data) else ""
    )


def to_ass(data: dict[str, Any], *, title: str = "subtitle", style: ass_style | None = None) -> str:
    style = style or ass_style(font_size=48, scroll_seconds=0)
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
        f"0,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},2,40,40,40,1"
    )
    header = f"""[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
{style_fmt}
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip()]
    for item in body_list(data):
        start_ms = int(float(item.get("from") or 0) * 1000)
        end_ms = int(float(item.get("to") or 0) * 1000)
        text = _ass_text(str(item.get("content") or ""))
        if not text:
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_subtitle(
    data: dict[str, Any],
    dest: Path,
    *,
    fmt: str,
    title: str = "subtitle",
    style: ass_style | None = None,
) -> Path:
    fmt = fmt.lower().strip()
    if fmt not in subtitle_formats:
        raise ValueError(f"unsupported subtitle format: {fmt}")
    if fmt == "srt":
        content = to_srt(data)
    elif fmt == "lrc":
        content = to_lrc(data)
    elif fmt == "txt":
        content = to_txt(data)
    elif fmt == "ass":
        content = to_ass(data, title=title, style=style)
    else:
        content = to_json(data)
    dest = dest.with_suffix(f".{fmt}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def to_iso_639_2(language: str) -> str:
    key = language.lower()
    if key in language_code_map:
        return language_code_map[key]
    if key.startswith(ai_language_prefix):
        base = key[len(ai_language_prefix) :]
        if base in language_code_map:
            return language_code_map[base]
    return language


def _srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _lrc_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"
