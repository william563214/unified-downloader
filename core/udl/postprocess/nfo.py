"""generate Kodi / Jellyfin / Emby compatible .nfo sidecars from item_metadata."""

from __future__ import annotations

import xml.etree.ElementTree as et
from pathlib import Path
from xml.dom import minidom

from udl.metadata import item_metadata

# lowercase constants per project convention
nfo_kinds = ("movie", "episodedetails", "musicvideo")
default_nfo_kind = "movie"


def choose_nfo_kind(meta: item_metadata, *, kind_hint: str | None = None) -> str:
    """pick movie / episodedetails / musicvideo from metadata and optional hint."""
    explicit = meta.extra.get("nfo_type") or meta.extra.get("nfo_kind")
    if isinstance(explicit, str) and explicit.strip().lower() in nfo_kinds:
        return explicit.strip().lower()

    hint = (kind_hint or "").strip().lower()
    if hint in {"bangumi", "episode", "season", "ep", "anime"}:
        return "episodedetails"
    if hint in {"music", "musicvideo", "mv", "audio"}:
        return "musicvideo"

    if meta.extra.get("ep_id") or meta.extra.get("season_id") or meta.extra.get("episode"):
        return "episodedetails"
    return default_nfo_kind


def _text(parent: et.Element, tag: str, value: object | None) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    child = et.SubElement(parent, tag)
    child.text = text


def _rating_value(meta: item_metadata) -> str | None:
    raw = meta.extra.get("rating") or meta.extra.get("score")
    if raw is None:
        return None
    try:
        return f"{float(raw):.1f}"
    except (TypeError, ValueError):
        text = str(raw).strip()
        return text or None


def build_nfo_xml(
    meta: item_metadata,
    *,
    kind: str | None = None,
    kind_hint: str | None = None,
    media_path: Path | None = None,
) -> str:
    """return pretty-printed XML string for the chosen nfo root element.

    Missing metadata fields are omitted rather than written empty so parsers
    stay happy when extractors only fill a subset (e.g. MEGA).
    """
    root_name = choose_nfo_kind(meta, kind_hint=kind_hint) if kind is None else kind
    if root_name not in nfo_kinds:
        root_name = default_nfo_kind

    root = et.Element(root_name)
    title = meta.title or (media_path.stem if media_path else None) or meta.item_id
    _text(root, "title", title)
    _text(root, "plot", meta.description)
    _text(root, "outline", meta.description)
    _text(root, "premiered", meta.upload_date)
    if meta.upload_date and len(meta.upload_date) >= 4 and meta.upload_date[:4].isdigit():
        _text(root, "year", meta.upload_date[:4])

    if meta.author:
        if root_name == "musicvideo":
            _text(root, "artist", meta.author)
        else:
            _text(root, "director", meta.author)
            _text(root, "credits", meta.author)

    rating = _rating_value(meta)
    if rating is not None:
        _text(root, "rating", rating)
        _text(root, "userrating", rating)

    if meta.duration is not None:
        try:
            seconds = int(round(float(meta.duration)))
            if seconds > 0:
                # Kodi runtime is minutes
                _text(root, "runtime", str(max(1, (seconds + 59) // 60)))
        except (TypeError, ValueError):
            pass

    seen_tags: set[str] = set()
    for tag in meta.tags:
        cleaned = str(tag).strip()
        if not cleaned or cleaned.lower() in seen_tags:
            continue
        seen_tags.add(cleaned.lower())
        _text(root, "tag", cleaned)
        _text(root, "genre", cleaned)

    uid_value = meta.item_id or meta.canonical_url
    if uid_value:
        uniqueid = et.SubElement(root, "uniqueid", {"type": meta.site, "default": "true"})
        uniqueid.text = str(uid_value)

    if meta.canonical_url:
        _text(root, "website", meta.canonical_url)

    _text(root, "studio", meta.site)
    if meta.item_id:
        _text(root, "code", meta.item_id)

    if meta.view_count is not None:
        _text(root, "votes", str(meta.view_count))
    if meta.like_count is not None:
        # non-standard but harmless for Jellyfin/Emby custom scrapers
        _text(root, "likes", str(meta.like_count))

    if meta.resolution:
        fileinfo = et.SubElement(root, "fileinfo")
        streamdetails = et.SubElement(fileinfo, "streamdetails")
        video = et.SubElement(streamdetails, "video")
        _text(video, "width", _res_part(meta.resolution, 0))
        _text(video, "height", _res_part(meta.resolution, 1))

    if root_name == "episodedetails":
        season = meta.extra.get("season") or meta.extra.get("season_number") or 1
        episode = (
            meta.extra.get("episode")
            or meta.extra.get("episode_number")
            or meta.extra.get("page")
            or 1
        )
        _text(root, "season", season)
        _text(root, "episode", episode)
        show = meta.collection or meta.extra.get("season_title") or meta.extra.get("showtitle")
        _text(root, "showtitle", show)

    if meta.thumbnail_url or meta.cover_url:
        thumb = meta.cover_url or meta.thumbnail_url
        _text(root, "thumb", thumb)

    rough = et.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(rough)
    pretty = parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    lines = [ln for ln in pretty.splitlines() if ln.strip()]
    if lines and lines[0].startswith("<?xml"):
        return "\n".join(lines) + "\n"
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines) + "\n"


def _res_part(resolution: str, index: int) -> str | None:
    text = resolution.lower().replace("×", "x")
    if "x" in text:
        parts = text.split("x", 1)
        if index < len(parts) and parts[index].strip().isdigit():
            return parts[index].strip()
    return None


def nfo_path_for_media(media_path: Path) -> Path:
    """sidecar path next to media: video.mp4 -> video.nfo."""
    return media_path.with_suffix(".nfo")


def write_nfo(
    meta: item_metadata,
    media_path: Path,
    *,
    kind: str | None = None,
    kind_hint: str | None = None,
    dest: Path | None = None,
) -> Path:
    """write .nfo beside media (or dest). returns path written."""
    out = dest or nfo_path_for_media(media_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    xml = build_nfo_xml(meta, kind=kind, kind_hint=kind_hint, media_path=media_path)
    out.write_text(xml, encoding="utf-8")
    return out
