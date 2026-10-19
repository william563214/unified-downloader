"""Per-type naming template defaults for Bilibili content kinds."""

from __future__ import annotations

# type_templates keys used with udl.config type_templates
default_type_templates: dict[str, str] = {
    "bilibili.video": "{author}/{title}/P{page:02d}_{part_title}.{ext}",
    "bilibili.bangumi": "bangumi/{collection}/E{page:02d}_{title}.{ext}",
    "bilibili.cheese": "cheese/{collection}/E{page:02d}_{title}.{ext}",
    "bilibili.interactive": "{author}/{title}/node{page:02d}_{part_title}.{ext}",
    "bilibili.audio": "audio/{author}/{title}.{ext}",
    "bilibili.audio_menu": "audio/{collection}/{title}.{ext}",
    "bilibili.space": "{author}/{title}.{ext}",
    "bilibili.favlist": "fav/{collection}/{title}.{ext}",
    "bilibili.popular": "weekly/{collection}/{title}.{ext}",
    "bilibili.collection": "collection/{collection}/{title}.{ext}",
    "bilibili.series": "series/{collection}/{title}.{ext}",
}

# simpler single-file defaults when only one part
default_single_templates: dict[str, str] = {
    "bilibili.video": "{author}/{title}.{ext}",
    "bilibili.bangumi": "bangumi/{collection}/{title}.{ext}",
    "bilibili.cheese": "cheese/{collection}/{title}.{ext}",
    "bilibili.interactive": "{author}/{title}.{ext}",
    "bilibili.audio": "audio/{author}/{title}.{ext}",
    "bilibili.audio_menu": "audio/{collection}/{title}.{ext}",
    "bilibili.space": "{author}/{title}.{ext}",
    "bilibili.favlist": "fav/{collection}/{title}.{ext}",
    "bilibili.popular": "weekly/{title}.{ext}",
    "bilibili.collection": "collection/{collection}/{title}.{ext}",
    "bilibili.series": "series/{collection}/{title}.{ext}",
}


def type_template_key(kind: str) -> str:
    return f"bilibili.{kind}"


def default_template_for(kind: str, *, multi: bool = False) -> str:
    key = type_template_key(kind)
    if multi:
        return default_type_templates.get(key, "{title}.{ext}")
    return default_single_templates.get(key, "{title}.{ext}")
