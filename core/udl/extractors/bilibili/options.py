"""Download / probe options for Bilibili extractor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from udl.extractors.bilibili.danmaku import ass_style
from udl.extractors.bilibili.filter import select_filter


@dataclass
class bili_options:
    quality: int | None = None
    codecid: int | None = None  # 7=AVC 12=HEVC 13=AV1
    audio_quality: int | None = None
    part: int | str | None = None
    container: str = "mp4"  # mp4 | mkv
    danmaku_format: str | None = None  # xml|ass|json
    subtitle_format: str | None = None  # srt|lrc|txt|ass|json
    subtitle_languages: list[str] = field(default_factory=list)
    embed_danmaku: bool = False
    embed_subtitle: bool = False
    write_chapters: bool = True
    ass_style: ass_style = field(default_factory=ass_style)
    select: select_filter = field(default_factory=select_filter)
    checked_pages: list[int] | None = None
    page_num: int = 1  # for paginated lists
    expand_all: bool = False  # paginate + expand nested lists
    # optional interactive node title overrides: edge_id or page -> name
    node_names: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] | None = None
    # progress callback for expand_all: (phase, current, total, message)
    on_progress: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality": self.quality,
            "codecid": self.codecid,
            "audio_quality": self.audio_quality,
            "part": self.part,
            "container": self.container,
            "danmaku_format": self.danmaku_format,
            "subtitle_format": self.subtitle_format,
            "subtitle_languages": list(self.subtitle_languages),
            "embed_danmaku": self.embed_danmaku,
            "embed_subtitle": self.embed_subtitle,
            "write_chapters": self.write_chapters,
            "ass_style": self.ass_style.to_dict(),
            "select": self.select.to_dict(),
            "checked_pages": list(self.checked_pages or []),
            "page_num": self.page_num,
            "expand_all": self.expand_all,
            "node_names": dict(self.node_names),
        }
