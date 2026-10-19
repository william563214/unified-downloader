"""Bilibili URL / id helpers adapted from Bili23-Downloader."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# aid→bvid alphabet from upstream VideoParser.aid_to_bvid
aid_xor_code = 23442827791579
aid_max = 1 << 51
bvid_alphabet = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
bvid_encode_map = (8, 7, 0, 5, 1, 3, 2, 4, 6)

# kinds returned by classify_url
public_kinds = frozenset(
    {
        "video",
        "bangumi",
        "cheese",
        "audio",
        "audio_menu",
        "space",
        "favlist",
        "popular",
        "collection",
        "series",
        "b23",
    }
)
login_kinds = frozenset(
    {
        "watchlater",
        "history",
        "followed_bangumi",
        "subscribed_collections",
        "favorite_folders",
    }
)


@dataclass(frozen=True)
class url_target:
    """classified bilibili URL target."""

    kind: str
    bvid: str | None = None
    aid: int | None = None
    page: int | None = None
    ep_id: int | None = None
    season_id: int | None = None
    media_id: int | None = None
    mid: int | None = None
    fid: int | None = None
    audio_id: int | None = None
    series_id: int | None = None
    collection_id: int | None = None
    weekly_number: int | None = None
    keyword: str | None = None
    short_url: str | None = None


def aid_to_bvid(aid: int) -> str:
    bvid = [""] * 9
    tmp = (aid_max | aid) ^ aid_xor_code
    for i in range(len(bvid_encode_map)):
        bvid[bvid_encode_map[i]] = bvid_alphabet[tmp % len(bvid_alphabet)]
        tmp //= len(bvid_alphabet)
    return "BV1" + "".join(bvid)


def safe_filename(name: str, *, max_len: int = 120) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not cleaned:
        cleaned = "bilibili"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def split_batch_urls(text: str) -> list[str]:
    """split pasted multi-link input into individual URLs / ids."""
    parts: list[str] = []
    for line in text.replace(",", "\n").splitlines():
        for token in line.split():
            token = token.strip()
            if token:
                parts.append(token)
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def classify_url(url: str) -> url_target:
    """classify a bilibili URL without network I/O."""
    raw = url.strip()
    parsed = urlparse(raw if "://" in raw else f"https://www.bilibili.com/{raw.lstrip('/')}")
    host = parsed.netloc.lower()
    path = parsed.path or ""
    qs = parse_qs(parsed.query)
    full = raw

    keyword = None
    if "keyword" in qs and qs["keyword"]:
        keyword = qs["keyword"][0]
    elif "key" in qs and qs["key"]:
        keyword = qs["key"][0]

    if "b23.tv" in host or "bili2233.cn" in host:
        return url_target(kind="b23", short_url=full)

    # account-only surfaces (stub — raise requires_login later)
    if "/watchlater" in path or "watchlater" in path:
        return url_target(kind="watchlater", keyword=keyword)
    if "/account/history" in path or path.rstrip("/").endswith("/history"):
        if "bilibili.com" in host:
            return url_target(kind="history", keyword=keyword)
    if "bangumi/list" in path or "pgc/mine" in path:
        return url_target(kind="followed_bangumi", keyword=keyword)
    if "/favlist" in path and "fid" not in qs and re.search(r"/favlist/?$", path):
        # own favorite folder index without fid
        mid_m = re.search(r"space\.bilibili\.com/(\d+)", host + path)
        mid = int(mid_m.group(1)) if mid_m else None
        if mid is None:
            mid_m = re.search(r"/(\d+)/favlist", path)
            mid = int(mid_m.group(1)) if mid_m else None
        return url_target(kind="favorite_folders", mid=mid, keyword=keyword)

    # cheese / courses
    if "/cheese/" in path or "cheese.bilibili.com" in host:
        m = re.search(r"(?:^|/)(ep|ss)(\d+)", path, re.I)
        if m:
            prefix, num = m.group(1).lower(), int(m.group(2))
            if prefix == "ep":
                return url_target(kind="cheese", ep_id=num)
            return url_target(kind="cheese", season_id=num)

    # audio au / am
    m = re.search(r"(?:^|/)au(\d+)", path, re.I)
    if m and ("bilibili.com" in host or "audio" in path):
        return url_target(kind="audio", audio_id=int(m.group(1)))
    m = re.search(r"(?:^|/)am(\d+)", path, re.I)
    if m and ("bilibili.com" in host or "audio" in path):
        return url_target(kind="audio_menu", audio_id=int(m.group(1)))
    m = re.fullmatch(r"au(\d+)", path.strip("/"), re.I)
    if m:
        return url_target(kind="audio", audio_id=int(m.group(1)))
    m = re.fullmatch(r"am(\d+)", path.strip("/"), re.I)
    if m:
        return url_target(kind="audio_menu", audio_id=int(m.group(1)))

    # weekly must-watch
    if "/popular/weekly" in path or "/v/popular/weekly" in path:
        num = None
        if "num" in qs and qs["num"]:
            try:
                num = int(qs["num"][0])
            except ValueError:
                num = None
        return url_target(kind="popular", weekly_number=num)

    # UP space videos
    m = re.search(r"(?:space\.bilibili\.com)/(\d+)", host + path)
    if not m:
        m = re.search(r"^/(\d+)(?:/(?:video|upload|upload/video))?", path)
        if m and "space.bilibili.com" in host:
            pass
        elif "space.bilibili.com" in host:
            m = re.search(r"/(\d+)", path)
    if "space.bilibili.com" in host:
        mid_m = re.search(r"/(\d+)", path)
        if mid_m:
            mid = int(mid_m.group(1))
            # collections / series under space
            list_m = re.search(r"/lists?/(\d+)", path)
            if list_m:
                lid = int(list_m.group(1))
                typ = (qs.get("type") or [""])[0].lower()
                if typ == "series":
                    return url_target(
                        kind="series", mid=mid, series_id=lid, keyword=keyword
                    )
                return url_target(
                    kind="collection", mid=mid, collection_id=lid, keyword=keyword
                )
            # public favlist with fid
            if "/favlist" in path:
                fid = None
                if "fid" in qs and qs["fid"]:
                    try:
                        fid = int(qs["fid"][0])
                    except ValueError:
                        fid = None
                if fid is not None:
                    return url_target(kind="favlist", mid=mid, fid=fid, keyword=keyword)
                return url_target(kind="favorite_folders", mid=mid, keyword=keyword)
            # subscribed collections index (login)
            if "/lists" in path and not list_m:
                if "type" in qs and qs["type"] and qs["type"][0] == "collect":
                    return url_target(kind="subscribed_collections", mid=mid)
            return url_target(kind="space", mid=mid, keyword=keyword)

    # list play page https://www.bilibili.com/list/{mid}?sid=...
    m = re.search(r"/list/(\d+)", path)
    if m and "bilibili.com" in host:
        mid = int(m.group(1))
        sid = None
        if "sid" in qs and qs["sid"]:
            try:
                sid = int(qs["sid"][0])
            except ValueError:
                sid = None
        typ = (qs.get("type") or [""])[0].lower()
        bvid = None
        if "bvid" in qs and qs["bvid"]:
            bvid = qs["bvid"][0]
        if typ == "series" and sid is not None:
            return url_target(
                kind="series", mid=mid, series_id=sid, bvid=bvid, keyword=keyword
            )
        if sid is not None:
            return url_target(
                kind="collection",
                mid=mid,
                collection_id=sid,
                bvid=bvid,
                keyword=keyword,
            )
        if bvid:
            return url_target(kind="video", bvid=bvid, mid=mid)

    # bangumi play / media
    m = re.search(r"/bangumi/(?:play|media)/(ep|ss|md)(\d+)", path, re.I)
    if m:
        prefix, num = m.group(1).lower(), int(m.group(2))
        if prefix == "ep":
            return url_target(kind="bangumi", ep_id=num)
        if prefix == "ss":
            return url_target(kind="bangumi", season_id=num)
        return url_target(kind="bangumi", media_id=num)

    m = re.search(r"(?:^|/)(ep|ss|md)(\d+)(?:/|$)", path, re.I)
    if m and "bilibili.com" in host and "/cheese/" not in path:
        prefix, num = m.group(1).lower(), int(m.group(2))
        if prefix == "ep":
            return url_target(kind="bangumi", ep_id=num)
        if prefix == "ss":
            return url_target(kind="bangumi", season_id=num)
        return url_target(kind="bangumi", media_id=num)

    # video path
    page = None
    if "p" in qs and qs["p"]:
        try:
            page = int(qs["p"][0])
        except ValueError:
            page = None

    m = re.search(r"/video/(BV[\w]+)", path, re.I)
    if m:
        return url_target(kind="video", bvid=m.group(1), page=page)

    m = re.search(r"/video/[aA][vV](\d+)", path)
    if m:
        aid = int(m.group(1))
        return url_target(kind="video", aid=aid, bvid=aid_to_bvid(aid), page=page)

    m = re.search(r"(BV[\w]+)", full, re.I)
    if m and ("bilibili.com" in host or host == "" or "://" not in raw):
        return url_target(kind="video", bvid=m.group(1), page=page)

    m = re.search(r"[aA][vV](\d+)", full)
    if m and ("bilibili.com" in host or host == "" or "://" not in raw):
        aid = int(m.group(1))
        return url_target(kind="video", aid=aid, bvid=aid_to_bvid(aid), page=page)

    # bare ep/ss/md without host
    m = re.fullmatch(r"(ep|ss|md)(\d+)", path.strip("/"), re.I)
    if m:
        prefix, num = m.group(1).lower(), int(m.group(2))
        if prefix == "ep":
            return url_target(kind="bangumi", ep_id=num)
        if prefix == "ss":
            return url_target(kind="bangumi", season_id=num)
        return url_target(kind="bangumi", media_id=num)

    # ml{fid} favlist shortcut
    m = re.search(r"(?:^|/)ml(\d+)", path, re.I)
    if m:
        return url_target(kind="favlist", fid=int(m.group(1)), keyword=keyword)

    return url_target(kind="unsupported")
