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


@dataclass(frozen=True)
class url_target:
    """classified bilibili URL target."""

    kind: str  # video | bangumi | b23 | unsupported
    bvid: str | None = None
    aid: int | None = None
    page: int | None = None
    ep_id: int | None = None
    season_id: int | None = None
    media_id: int | None = None
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


def classify_url(url: str) -> url_target:
    """classify a bilibili URL without network I/O."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path or ""
    full = url.strip()

    if "b23.tv" in host or "bili2233.cn" in host:
        return url_target(kind="b23", short_url=full)

    # bangumi play / media
    m = re.search(r"/bangumi/(?:play|media)/(ep|ss|md)(\d+)", path, re.I)
    if m:
        prefix, num = m.group(1).lower(), int(m.group(2))
        if prefix == "ep":
            return url_target(kind="bangumi", ep_id=num)
        if prefix == "ss":
            return url_target(kind="bangumi", season_id=num)
        return url_target(kind="bangumi", media_id=num)

    # bare ep/ss/md in path or query-less string
    m = re.search(r"(?:^|/)(ep|ss|md)(\d+)(?:/|$)", path, re.I)
    if m and "bilibili.com" in host:
        prefix, num = m.group(1).lower(), int(m.group(2))
        if prefix == "ep":
            return url_target(kind="bangumi", ep_id=num)
        if prefix == "ss":
            return url_target(kind="bangumi", season_id=num)
        return url_target(kind="bangumi", media_id=num)

    # video path
    page = None
    qs = parse_qs(parsed.query)
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
    if m and ("bilibili.com" in host or host == ""):
        return url_target(kind="video", bvid=m.group(1), page=page)

    m = re.search(r"[aA][vV](\d+)", full)
    if m and ("bilibili.com" in host or host == ""):
        aid = int(m.group(1))
        return url_target(kind="video", aid=aid, bvid=aid_to_bvid(aid), page=page)

    # bare ep/ss/md without host (paste shortcuts)
    m = re.fullmatch(r"(ep|ss|md)(\d+)", path.strip("/"), re.I)
    if m:
        prefix, num = m.group(1).lower(), int(m.group(2))
        if prefix == "ep":
            return url_target(kind="bangumi", ep_id=num)
        if prefix == "ss":
            return url_target(kind="bangumi", season_id=num)
        return url_target(kind="bangumi", media_id=num)

    return url_target(kind="unsupported")
