"""Quality labels codec selection and DASH stream picking for Bilibili."""

from __future__ import annotations

from typing import Any

# qn → display label (anonymous ceiling is usually ≤64 / 720P without login)
video_quality_labels: dict[int, str] = {
    127: "8K",
    126: "Dolby Vision",
    125: "HDR",
    122: "4K SDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    100: "AI",
    80: "1080P",
    64: "720P",
    32: "480P",
    16: "360P",
}

# codecid → label (AVC / HEVC / AV1)
codec_labels: dict[int, str] = {
    7: "AVC",
    12: "HEVC",
    13: "AV1",
}

# prefer AVC then HEVC then AV1 (same default as Bili23)
codec_preference = (7, 12, 13)

# audio id preference high→low (Hi-Res / Dolby / 192k / 132k / 64k)
audio_preference = (30251, 30250, 30280, 30232, 30216)

audio_quality_labels: dict[int, str] = {
    30251: "Hi-Res FLAC",
    30250: "Dolby Audio",
    30280: "192K",
    30232: "132K",
    30216: "64K",
}

# codec name prefixes in support_formats
codec_prefix_map: dict[str, int] = {
    "avc1": 7,
    "avc": 7,
    "hev1": 12,
    "hvc1": 12,
    "hevc": 12,
    "av01": 13,
    "av1": 13,
}


def label_for_qn(qn: int) -> str:
    return video_quality_labels.get(qn, f"qn{qn}")


def label_for_codec(codecid: int) -> str:
    return codec_labels.get(codecid, f"codec{codecid}")


def label_for_audio(aid: int) -> str:
    return audio_quality_labels.get(aid, f"audio{aid}")


def stream_urls(entry: dict[str, Any]) -> list[str]:
    """collect base/backup URLs from a dash entry."""
    urls: list[str] = []
    for key in ("baseUrl", "base_url", "backupUrl", "backup_url", "url"):
        val = entry.get(key)
        if isinstance(val, str) and val:
            urls.append(val)
        elif isinstance(val, list):
            urls.extend(u for u in val if isinstance(u, str) and u)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def available_qualities(dash: dict[str, Any]) -> list[dict[str, Any]]:
    """list distinct video qn values present in dash (highest first)."""
    videos = dash.get("video") or []
    by_qn: dict[int, dict[str, Any]] = {}
    for v in videos:
        qn = int(v.get("id") or 0)
        if qn <= 0:
            continue
        codecid = int(v.get("codecid") or 0)
        if qn not in by_qn:
            by_qn[qn] = {
                "qn": qn,
                "label": label_for_qn(qn),
                "codecs": [],
            }
        if codecid and codecid not in by_qn[qn]["codecs"]:
            by_qn[qn]["codecs"].append(codecid)
    return [by_qn[k] for k in sorted(by_qn.keys(), reverse=True)]


def available_codecs(
    dash: dict[str, Any],
    *,
    quality: int | None = None,
    play: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """list codecs available for a quality (or union across qualities)."""
    declared = _declared_codec_map(play) if play else None
    videos: list[dict[str, Any]] = list(dash.get("video") or [])
    codec_ids: set[int] = set()
    if declared and quality is not None and quality in declared:
        codec_ids.update(declared[quality])
    for v in videos:
        qn = int(v.get("id") or 0)
        if quality is not None and qn != quality:
            continue
        cid = int(v.get("codecid") or 0)
        if cid:
            codec_ids.add(cid)
    ordered = [c for c in codec_preference if c in codec_ids]
    ordered.extend(sorted(c for c in codec_ids if c not in codec_preference))
    return [{"codecid": c, "label": label_for_codec(c)} for c in ordered]


def available_audio_qualities(dash: dict[str, Any]) -> list[dict[str, Any]]:
    """list audio qualities including Hi-Res / Dolby when present."""
    by_id: dict[int, dict[str, Any]] = {}
    flac = dash.get("flac") or {}
    if isinstance(flac, dict) and isinstance(flac.get("audio"), dict):
        by_id[30251] = {"id": 30251, "label": label_for_audio(30251), "kind": "flac"}
    dolby = dash.get("dolby") or {}
    if isinstance(dolby, dict):
        arr = dolby.get("audio") or []
        if isinstance(arr, list) and arr:
            by_id[30250] = {
                "id": 30250,
                "label": label_for_audio(30250),
                "kind": "dolby",
            }
    for a in dash.get("audio") or []:
        if not isinstance(a, dict):
            continue
        aid = int(a.get("id") or 0)
        if aid <= 0 or aid in by_id:
            continue
        by_id[aid] = {"id": aid, "label": label_for_audio(aid), "kind": "aac"}
    ordered = [by_id[i] for i in audio_preference if i in by_id]
    ordered.extend(
        by_id[i] for i in sorted(by_id.keys(), reverse=True) if i not in audio_preference
    )
    return ordered


def pick_video_stream(
    dash: dict[str, Any],
    *,
    quality: int | None = None,
    codecid: int | None = None,
) -> tuple[dict[str, Any], int]:
    """pick best available video stream.

    quality=None means best available without login/VIP bypass —
    i.e. the highest qn actually returned in dash.video.
    Never fabricate streams above what the session received.
    """
    videos: list[dict[str, Any]] = list(dash.get("video") or [])
    if not videos:
        raise RuntimeError("no DASH video streams in playurl response")

    qns = sorted(
        {int(v["id"]) for v in videos if v.get("id") is not None}, reverse=True
    )
    if quality is None:
        target = qns[0]
    else:
        if quality in qns:
            target = quality
        else:
            lower = [q for q in qns if q <= quality]
            target = lower[0] if lower else qns[0]

    candidates = [v for v in videos if int(v.get("id") or 0) == target]
    if codecid is not None:
        for v in candidates:
            if int(v.get("codecid") or 0) == codecid:
                return v, target
    for codec in codec_preference:
        for v in candidates:
            if int(v.get("codecid") or 0) == codec:
                return v, target
    return candidates[0], target


def pick_audio_stream(
    dash: dict[str, Any],
    *,
    audio_quality: int | None = None,
) -> dict[str, Any] | None:
    """pick audio stream by id preference or explicit quality."""
    pool: list[dict[str, Any]] = []
    flac = dash.get("flac") or {}
    if isinstance(flac, dict) and isinstance(flac.get("audio"), dict):
        a = dict(flac["audio"])
        a.setdefault("id", 30251)
        pool.append(a)
    dolby = dash.get("dolby") or {}
    if isinstance(dolby, dict):
        arr = dolby.get("audio") or []
        if isinstance(arr, list) and arr:
            a = dict(arr[0])
            a.setdefault("id", 30250)
            pool.append(a)
    for a in dash.get("audio") or []:
        if isinstance(a, dict):
            pool.append(a)
    if not pool:
        return None

    if audio_quality is not None:
        for entry in pool:
            if int(entry.get("id") or 0) == audio_quality:
                return entry
        # fall through to best available for this session

    def rank(entry: dict[str, Any]) -> tuple[int, int]:
        aid = int(entry.get("id") or 0)
        try:
            pref = audio_preference.index(aid)
        except ValueError:
            pref = 100
        return (pref, -int(entry.get("bandwidth") or 0))

    pool.sort(key=rank)
    return pool[0]


def _declared_codec_map(play: dict[str, Any]) -> dict[int, list[int]] | None:
    """parse support_formats → qn → codecids capped by session max dash qn."""
    support = play.get("support_formats")
    dash = play.get("dash") or {}
    video_list = dash.get("video") or []
    if not support or not video_list:
        return None
    highest = max(
        (int(v.get("id") or 0) for v in video_list if v.get("id") is not None),
        default=0,
    )
    declared: dict[int, list[int]] = {}
    for entry in support:
        if not isinstance(entry, dict):
            continue
        qn = entry.get("quality")
        if not isinstance(qn, int) or qn > highest:
            continue
        codec_ids: list[int] = []
        for codecs in entry.get("codecs") or []:
            prefix = str(codecs).split(".", 1)[0].lower()
            cid = codec_prefix_map.get(prefix)
            if cid is not None and cid not in codec_ids:
                codec_ids.append(cid)
        if codec_ids:
            declared[qn] = codec_ids
    return declared or None
