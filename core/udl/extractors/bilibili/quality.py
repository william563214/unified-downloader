"""Quality labels and DASH stream selection for Bilibili."""

from __future__ import annotations

from typing import Any

# qn → display label (anonymous ceiling is usually ≤64 / 720P)
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

# prefer AVC then HEVC then AV1 (same default as Bili23)
codec_preference = (7, 12, 13)

# audio id preference high→low
audio_preference = (30251, 30250, 30280, 30232, 30216)


def label_for_qn(qn: int) -> str:
    return video_quality_labels.get(qn, f"qn{qn}")


def stream_urls(entry: dict[str, Any]) -> list[str]:
    """collect base/backup URLs from a dash entry."""
    urls: list[str] = []
    for key in ("baseUrl", "base_url", "backupUrl", "backup_url", "url"):
        val = entry.get(key)
        if isinstance(val, str) and val:
            urls.append(val)
        elif isinstance(val, list):
            urls.extend(u for u in val if isinstance(u, str) and u)
    # dedupe preserve order
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
        if qn not in by_qn:
            by_qn[qn] = {
                "qn": qn,
                "label": label_for_qn(qn),
            }
    return [by_qn[k] for k in sorted(by_qn.keys(), reverse=True)]


def pick_video_stream(
    dash: dict[str, Any],
    *,
    quality: int | None = None,
) -> tuple[dict[str, Any], int]:
    """pick best available video stream.

    quality=None means best available without login/VIP bypass —
    i.e. the highest qn actually returned in dash.video.
    """
    videos: list[dict[str, Any]] = list(dash.get("video") or [])
    if not videos:
        raise RuntimeError("no DASH video streams in playurl response")

    qns = sorted({int(v["id"]) for v in videos if v.get("id") is not None}, reverse=True)
    if quality is None:
        target = qns[0]
    else:
        if quality in qns:
            target = quality
        else:
            # fall back to best not exceeding requested, else best available
            lower = [q for q in qns if q <= quality]
            target = lower[0] if lower else qns[0]

    candidates = [v for v in videos if int(v.get("id") or 0) == target]
    for codec in codec_preference:
        for v in candidates:
            if int(v.get("codecid") or 0) == codec:
                return v, target
    return candidates[0], target


def pick_audio_stream(dash: dict[str, Any]) -> dict[str, Any] | None:
    """pick best regular / flac / dolby audio stream if present."""
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

    def rank(entry: dict[str, Any]) -> tuple[int, int]:
        aid = int(entry.get("id") or 0)
        try:
            pref = audio_preference.index(aid)
        except ValueError:
            pref = 100
        return (pref, -int(entry.get("bandwidth") or 0))

    pool.sort(key=rank)
    return pool[0]
