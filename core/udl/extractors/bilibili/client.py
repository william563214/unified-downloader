"""Bilibili HTTP client (no VIP/paywall/region bypass).

Session cookies load from udl.secrets (bilibili.session) or set_session_cookies.
UA / proxy via udl.network; CDN host preference via udl.extractors.bilibili.cdn.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode

import requests
from requests import Response, Session

from udl.download.http import default_timeout
from udl.extractors.bilibili.danmaku import (
    merge_entries,
    parse_protobuf_danmaku,
    parse_xml_danmaku,
    segment_count,
)
from udl.extractors.bilibili.login import requires_login, session_cookies_from_store
from udl.extractors.bilibili.wbi import enc_wbi, store_wbi_keys
from udl.network import active_network, apply_to_session, default_user_agent

api_base = "https://api.bilibili.com"
nav_api = f"{api_base}/x/web-interface/nav"
spi_api = f"{api_base}/x/frontend/finger/spi"
view_api = f"{api_base}/x/web-interface/wbi/view"
playurl_api = f"{api_base}/x/player/wbi/playurl"
player_v2_api = f"{api_base}/x/player/wbi/v2"
bangumi_season_api = f"{api_base}/pgc/view/web/season"
bangumi_media_api = f"{api_base}/pgc/review/user"
bangumi_playurl_api = f"{api_base}/pgc/player/web/playurl"
cheese_season_api = f"{api_base}/pugv/view/web/season/v2"
cheese_playurl_api = f"{api_base}/pugv/player/web/playurl"
space_search_api = f"{api_base}/x/space/wbi/arc/search"
space_card_api = f"{api_base}/x/web-interface/card"
fav_list_api = f"{api_base}/x/v3/fav/resource/list"
popular_weekly_api = f"{api_base}/x/web-interface/popular/series/one"
seasons_archives_api = f"{api_base}/x/polymer/web-space/seasons_archives_list"
series_archives_api = f"{api_base}/x/series/archives"
series_meta_api = f"{api_base}/x/series/series"
stein_edge_api = f"{api_base}/x/stein/edgeinfo_v2"
danmaku_xml_api = "https://comment.bilibili.com/{cid}.xml"
danmaku_seg_api = f"{api_base}/x/v2/dm/web/seg.so"
audio_info_api = "https://www.bilibili.com/audio/music-service-c/web/song/info"
audio_menu_info_api = "https://www.bilibili.com/audio/music-service-c/web/menu/info"
audio_menu_list_api = "https://www.bilibili.com/audio/music-service-c/web/song/of-menu"
audio_play_api = "https://www.bilibili.com/audio/music-service-c/web/url"
referer = "https://www.bilibili.com/"

# playurl fnval flags (DASH + extras) from Bili23
video_fnval = 4048
bangumi_fnval = 143312

code_region_lock = -10403
code_paywall = 87008


class bilibili_api_error(RuntimeError):
    """remote API error with numeric code when available."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class bilibili_client:
    """session-backed client for view / playurl / bangumi / lists."""

    def __init__(
        self,
        *,
        user_agent: str = default_user_agent,
        timeout: float = default_timeout,
        session: Session | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self._own_session = session is None
        self.session = session or Session()
        ua = user_agent or active_network().user_agent or default_user_agent
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Referer": referer,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://www.bilibili.com",
            }
        )
        apply_to_session(self.session)
        self.session.headers["User-Agent"] = ua
        self._bootstrapped = False
        self._logged_in = False
        self._mid: int | None = None
        if cookies:
            self.set_session_cookies(cookies)
        elif cookies is None:
            stored = session_cookies_from_store()
            if stored:
                self.set_session_cookies(stored)

    def set_session_cookies(self, cookies: dict[str, str]) -> None:
        """inject session cookies (SESSDATA / bili_jct / DedeUserID…)."""
        for name, value in cookies.items():
            if value:
                self.session.cookies.set(str(name), str(value), domain=".bilibili.com")
        if cookies.get("SESSDATA"):
            self._logged_in = True
        mid = cookies.get("DedeUserID")
        if mid and str(mid).isdigit():
            self._mid = int(mid)

    def set_user_agent(self, user_agent: str) -> None:
        """update UA from networking settings / CLI."""
        self.session.headers["User-Agent"] = user_agent

    @property
    def has_session(self) -> bool:
        return self._logged_in or bool(self.session.cookies.get("SESSDATA"))

    @property
    def mid(self) -> int | None:
        if self._mid is not None:
            return self._mid
        raw = self.session.cookies.get("DedeUserID")
        if raw and str(raw).isdigit():
            self._mid = int(raw)
            return self._mid
        return None

    def require_session(self, kind: str) -> None:
        if not self.has_session:
            raise requires_login(kind)

    def close(self) -> None:
        if self._own_session:
            self.session.close()

    def __enter__(self) -> bilibili_client:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def bootstrap(self) -> None:
        """mint guest buvid cookies and refresh WBI keys."""
        if self._bootstrapped:
            return
        try:
            spi = self._get_json(spi_api)
            data = spi.get("data") or {}
            if data.get("b_3"):
                self.session.cookies.set(
                    "buvid3", str(data["b_3"]), domain=".bilibili.com"
                )
            if data.get("b_4"):
                self.session.cookies.set(
                    "buvid4", str(data["b_4"]), domain=".bilibili.com"
                )
        except bilibili_api_error:
            pass
        self.refresh_wbi()
        self._bootstrapped = True

    def refresh_wbi(self) -> None:
        payload = self._get_json(nav_api, allow_nonzero=True)
        data = payload.get("data") or {}
        wbi = data.get("wbi_img") or {}
        img = PurePosixPath(str(wbi.get("img_url") or "")).stem
        sub = PurePosixPath(str(wbi.get("sub_url") or "")).stem
        if not store_wbi_keys(img, sub):
            raise bilibili_api_error(
                "failed to obtain WBI signing keys from nav",
                code=payload.get("code"),
            )
        if data.get("isLogin"):
            self._logged_in = True
            mid = data.get("mid")
            if mid is not None:
                self._mid = int(mid)

    def resolve_short_link(self, url: str) -> str:
        """follow b23.tv / bili2233.cn redirects to the final URL."""
        try:
            resp = self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise bilibili_api_error(f"short link resolve failed: {exc}") from exc
        final = str(resp.url)
        if not final or final.rstrip("/") == url.rstrip("/"):
            raise bilibili_api_error("short link expired or invalid")
        return final

    def view(self, bvid: str | None = None, *, aid: int | None = None) -> dict[str, Any]:
        self.bootstrap()
        params: dict[str, object] = {}
        if bvid:
            params["bvid"] = bvid
        elif aid is not None:
            params["aid"] = aid
        else:
            raise ValueError("view requires bvid or aid")
        query = enc_wbi(params)
        payload = self._get_json(f"{view_api}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("view response missing data")
        return data

    def playurl_video(self, *, bvid: str, cid: int, qn: int = 127) -> dict[str, Any]:
        self.bootstrap()
        query = enc_wbi(
            {
                "bvid": bvid,
                "cid": cid,
                "qn": qn,
                "fnver": 0,
                "fnval": video_fnval,
                "fourk": 1,
            }
        )
        payload = self._get_json(f"{playurl_api}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("playurl response missing data")
        if data.get("is_drm"):
            raise bilibili_api_error("DRM-protected content is not supported")
        return data

    def player_v2(
        self,
        *,
        cid: int,
        bvid: str | None = None,
        aid: int | None = None,
    ) -> dict[str, Any]:
        """player info — subtitles + chapters (view_points)."""
        self.bootstrap()
        params: dict[str, object] = {
            "cid": cid,
            "dm_img_list": "[]",
            "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
            "dm_cover_img_str": (
                "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDQwNjAg"
                "TGFwdG9wIEdQVSAoMHgwMDAwMjhFMCkgRGlyZWN0M0QxMSB2c181"
                "XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ"
            ),
            "dm_img_inter": '{"ds":[],"wh":[5231,6067,75],"of":[475,950,475]}',
        }
        if bvid:
            params["bvid"] = bvid
        elif aid is not None:
            params["aid"] = aid
        else:
            raise ValueError("player_v2 requires bvid or aid")
        query = enc_wbi(params)
        payload = self._get_json(f"{player_v2_api}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("player v2 response missing data")
        return data

    def bangumi_season(
        self,
        *,
        ep_id: int | None = None,
        season_id: int | None = None,
        media_id: int | None = None,
    ) -> dict[str, Any]:
        self.bootstrap()
        if media_id is not None and season_id is None and ep_id is None:
            media = self._get_json(f"{bangumi_media_api}?media_id={media_id}")
            season_id = int(
                ((media.get("result") or {}).get("media") or {}).get("season_id")
            )
        if ep_id is not None:
            param = f"ep_id={ep_id}"
        elif season_id is not None:
            param = f"season_id={season_id}"
        else:
            raise ValueError("bangumi_season requires ep_id, season_id, or media_id")
        payload = self._get_json(f"{bangumi_season_api}?{param}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise bilibili_api_error("bangumi season response missing result")
        if ep_id is not None:
            result = dict(result)
            result["current_ep_id"] = ep_id
        return result

    def playurl_bangumi(self, *, bvid: str, cid: int, qn: int = 80) -> dict[str, Any]:
        self.bootstrap()
        params = {
            "bvid": bvid,
            "cid": cid,
            "qn": qn,
            "fnver": 0,
            "fnval": bangumi_fnval,
            "fourk": 1,
        }
        payload = self._get_json(f"{bangumi_playurl_api}?{urlencode(params)}")
        result = payload.get("result") or payload.get("data")
        if not isinstance(result, dict):
            raise bilibili_api_error("bangumi playurl missing result")
        if result.get("is_drm"):
            raise bilibili_api_error("DRM-protected content is not supported")
        return result

    def cheese_season(
        self,
        *,
        ep_id: int | None = None,
        season_id: int | None = None,
    ) -> dict[str, Any]:
        self.bootstrap()
        if ep_id is not None:
            param = f"ep_id={ep_id}"
        elif season_id is not None:
            param = f"season_id={season_id}"
        else:
            raise ValueError("cheese_season requires ep_id or season_id")
        payload = self._get_json(f"{cheese_season_api}?{param}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("cheese season response missing data")
        if ep_id is not None:
            data = dict(data)
            data["current_ep_id"] = ep_id
        return data

    def playurl_cheese(
        self,
        *,
        avid: int,
        ep_id: int,
        cid: int,
        qn: int = 80,
    ) -> dict[str, Any]:
        self.bootstrap()
        params = {
            "avid": avid,
            "ep_id": ep_id,
            "cid": cid,
            "qn": qn,
            "fnver": 0,
            "fnval": video_fnval,
            "fourk": 1,
        }
        payload = self._get_json(f"{cheese_playurl_api}?{urlencode(params)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("cheese playurl missing data")
        return data

    def space_videos(
        self,
        mid: int,
        *,
        pn: int = 1,
        ps: int = 40,
        keyword: str = "",
    ) -> dict[str, Any]:
        self.bootstrap()
        params: dict[str, object] = {
            "pn": pn,
            "ps": ps,
            "tid": 0,
            "special_type": "",
            "order": "pubdate",
            "mid": mid,
            "index": 0,
            "keyword": keyword or "",
            "order_avoided": "true",
            "platform": "web",
            "web_location": "333.1387",
            "dm_img_list": "[]",
            "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
            "dm_cover_img_str": (
                "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgUlRYIDQwNjAg"
                "TGFwdG9wIEdQVSAoMHgwMDAwMjhFMCkgRGlyZWN0M0QxMSB2c181"
                "XzAgcHNfNV8wLCBEM0QxMSlHb29nbGUgSW5jLiAoTlZJRElBKQ"
            ),
            "dm_img_inter": '{"ds":[],"wh":[3688,4546,12],"of":[119,238,119]}',
        }
        query = enc_wbi(params)
        payload = self._get_json(f"{space_search_api}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("space search missing data")
        return data

    def space_card(self, mid: int) -> dict[str, Any]:
        self.bootstrap()
        payload = self._get_json(f"{space_card_api}?mid={mid}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("space card missing data")
        return data

    def favlist(
        self,
        media_id: int,
        *,
        pn: int = 1,
        ps: int = 40,
        keyword: str = "",
    ) -> dict[str, Any]:
        self.bootstrap()
        params = {
            "media_id": media_id,
            "pn": pn,
            "ps": ps,
            "keyword": keyword or "",
            "order": "mtime",
            "type": 0,
            "tid": 0,
            "platform": "web",
            "web_location": "333.1387",
        }
        payload = self._get_json(f"{fav_list_api}?{urlencode(params)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("favlist missing data")
        return data

    def popular_weekly(self, number: int | None = None) -> dict[str, Any]:
        self.bootstrap()
        params: dict[str, object] = {"web_location": "333.934"}
        if number is not None:
            params["number"] = number
        query = enc_wbi(params)
        payload = self._get_json(f"{popular_weekly_api}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("popular weekly missing data")
        return data

    def collection_archives(
        self,
        *,
        mid: int,
        season_id: int,
        pn: int = 1,
        ps: int = 30,
    ) -> dict[str, Any]:
        self.bootstrap()
        params = {
            "mid": mid,
            "season_id": season_id,
            "sort_reverse": "false",
            "page_size": ps,
            "page_num": pn,
            "web_location": "333.1387",
        }
        payload = self._get_json(f"{seasons_archives_api}?{urlencode(params)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("collection archives missing data")
        return data

    def series_archives(
        self,
        *,
        mid: int,
        series_id: int,
        pn: int = 1,
        ps: int = 30,
    ) -> dict[str, Any]:
        self.bootstrap()
        params = {
            "mid": mid,
            "current_mid": 0,
            "series_id": series_id,
            "only_normal": "true",
            "sort": "desc",
            "ps": ps,
            "pn": pn,
            "web_location": "333.1387",
        }
        payload = self._get_json(f"{series_archives_api}?{urlencode(params)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("series archives missing data")
        return data

    def series_meta(self, series_id: int) -> dict[str, Any] | None:
        self.bootstrap()
        params = {"series_id": series_id, "web_location": "333.1387"}
        payload = self._get_json(
            f"{series_meta_api}?{urlencode(params)}", allow_nonzero=True
        )
        if payload.get("code") not in (0, None):
            return None
        data = payload.get("data") or {}
        meta = data.get("meta")
        return meta if isinstance(meta, dict) else None

    def stein_edge(
        self,
        *,
        bvid: str,
        graph_version: int,
        edge_id: int | None = None,
        portal: int = 0,
    ) -> dict[str, Any]:
        """interactive video graph node."""
        self.bootstrap()
        params: dict[str, object] = {
            "bvid": bvid,
            "graph_version": graph_version,
            "portal": portal,
            "screen": 0,
        }
        if edge_id is not None:
            params["edge_id"] = edge_id
        query = enc_wbi(params)
        payload = self._get_json(f"{stein_edge_api}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("stein edgeinfo missing data")
        return data

    def danmaku_xml(self, cid: int) -> str:
        """fetch classic danmaku XML for a cid."""
        self.bootstrap()
        url = danmaku_xml_api.format(cid=cid)
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise bilibili_api_error(f"danmaku fetch failed: {exc}") from exc
        if resp.status_code != 200:
            raise bilibili_api_error(
                f"danmaku HTTP {resp.status_code}", code=resp.status_code
            )
        return resp.text

    def danmaku_seg(
        self,
        cid: int,
        *,
        segment_index: int,
        aid: int | None = None,
    ) -> bytes:
        """fetch one protobuf danmaku segment (6 min pack segment_index>=1)."""
        self.bootstrap()
        params: dict[str, object] = {
            "type": 1,
            "oid": cid,
            "segment_index": max(1, int(segment_index)),
        }
        if aid is not None:
            params["pid"] = int(aid)
        try:
            resp = self.session.get(
                f"{danmaku_seg_api}?{urlencode(params)}",
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise bilibili_api_error(f"danmaku seg fetch failed: {exc}") from exc
        if resp.status_code != 200:
            raise bilibili_api_error(
                f"danmaku seg HTTP {resp.status_code}", code=resp.status_code
            )
        return resp.content

    def fetch_danmaku_entries(
        self,
        cid: int,
        *,
        aid: int | None = None,
        duration: float | int | None = None,
    ) -> list[dict[str, Any]]:
        """prefer segmented protobuf danmaku then fall back to classic XML."""
        chunks: list[list[dict[str, Any]]] = []
        total_segs = segment_count(duration)
        empty_streak = 0
        for idx in range(1, total_segs + 1):
            try:
                raw = self.danmaku_seg(cid, segment_index=idx, aid=aid)
            except bilibili_api_error:
                break
            if not raw:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            try:
                entries = parse_protobuf_danmaku(raw)
            except Exception:  # noqa: BLE001
                break
            if not entries:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            empty_streak = 0
            chunks.append(entries)
            # if duration unknown stop after first empty-ish trailing pack
            if duration is None and idx >= 3 and len(entries) < 10:
                break
        merged = merge_entries(chunks)
        if merged:
            return merged
        # XML fallback (historical / truncated pool)
        try:
            xml_text = self.danmaku_xml(cid)
        except bilibili_api_error:
            return []
        return parse_xml_danmaku(xml_text)

    def audio_info(self, sid: int) -> dict[str, Any]:
        self.bootstrap()
        payload = self._get_json(f"{audio_info_api}?{urlencode({'sid': sid})}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("audio info missing data")
        return data

    def audio_menu_info(self, sid: int) -> dict[str, Any]:
        self.bootstrap()
        payload = self._get_json(f"{audio_menu_info_api}?{urlencode({'sid': sid})}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("audio menu info missing data")
        return data

    def audio_menu_songs(self, sid: int, *, pn: int = 1, ps: int = 100) -> dict[str, Any]:
        self.bootstrap()
        params = {"sid": sid, "pn": pn, "ps": ps}
        payload = self._get_json(f"{audio_menu_list_api}?{urlencode(params)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("audio menu list missing data")
        return data

    def audio_playurl(self, sid: int, *, quality: int = 2) -> dict[str, Any]:
        self.bootstrap()
        params = {"sid": sid, "privilege": 2, "quality": quality}
        payload = self._get_json(f"{audio_play_api}?{urlencode(params)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("audio playurl missing data")
        return data

    # --- account-only lists (require SESSDATA) ---

    def watchlater(
        self,
        *,
        pn: int = 1,
        ps: int = 20,
        keyword: str = "",
    ) -> dict[str, Any]:
        self.require_session("watchlater")
        self.bootstrap()
        params: dict[str, object] = {
            "pn": pn,
            "ps": ps,
            "viewed": 0,
            "key": keyword or "",
            "asc": False,
            "need_split": True,
            "web_location": "333.881",
        }
        query = enc_wbi(params)
        payload = self._get_json(
            f"{api_base}/x/v2/history/toview/web?{query}"
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("watchlater missing data")
        return data

    def history(
        self,
        *,
        pn: int = 1,
        keyword: str = "",
    ) -> dict[str, Any]:
        self.require_session("history")
        self.bootstrap()
        params = {
            "pn": pn,
            "keyword": keyword or "",
            "business": "archive",
            "add_time_start": 0,
            "add_time_end": 0,
            "arc_max_duration": 0,
            "arc_min_duration": 0,
            "device_type": 0,
            "web_location": "333.1391",
        }
        payload = self._get_json(
            f"{api_base}/x/web-interface/history/search?{urlencode(params)}"
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("history missing data")
        return data

    def followed_bangumi(
        self,
        *,
        pn: int = 1,
        ps: int = 24,
        type_: int = 1,
        follow_status: int = 0,
    ) -> dict[str, Any]:
        self.require_session("followed_bangumi")
        self.bootstrap()
        mid = self.mid
        if mid is None:
            raise requires_login("followed_bangumi", detail="missing DedeUserID / mid")
        params: dict[str, object] = {
            "vmid": mid,
            "type": type_,
            "pn": pn,
            "ps": ps,
            "follow_status": follow_status,
            "web_location": "333.1387",
        }
        query = enc_wbi(params)
        payload = self._get_json(
            f"{api_base}/x/space/bangumi/follow/list?{query}"
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("followed bangumi missing data")
        return data

    def subscribed_collections(
        self,
        *,
        pn: int = 1,
        ps: int = 50,
    ) -> dict[str, Any]:
        self.require_session("subscribed_collections")
        self.bootstrap()
        mid = self.mid
        if mid is None:
            raise requires_login(
                "subscribed_collections", detail="missing DedeUserID / mid"
            )
        params = {
            "pn": pn,
            "ps": ps,
            "up_mid": mid,
            "platform": "web",
            "web_location": "333.1387",
        }
        payload = self._get_json(
            f"{api_base}/x/v3/fav/folder/collected/list?{urlencode(params)}"
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("subscribed collections missing data")
        return data

    def favorite_folders(self) -> dict[str, Any]:
        self.require_session("favorite_folders")
        self.bootstrap()
        mid = self.mid
        if mid is None:
            raise requires_login("favorite_folders", detail="missing DedeUserID / mid")
        payload = self._get_json(
            f"{api_base}/x/v3/fav/folder/created/list-all?up_mid={mid}"
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise bilibili_api_error("favorite folders missing data")
        return data

    def get_bytes(self, url: str) -> bytes:
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise bilibili_api_error(f"HTTP request failed: {exc}") from exc
        if resp.status_code != 200:
            raise bilibili_api_error(f"HTTP {resp.status_code} for {url}")
        return resp.content

    def get_json_url(self, url: str) -> dict[str, Any]:
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise bilibili_api_error(f"HTTP request failed: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise bilibili_api_error("non-JSON response") from exc
        if not isinstance(payload, dict):
            raise bilibili_api_error("unexpected JSON payload")
        return payload

    def _get_json(self, url: str, *, allow_nonzero: bool = False) -> dict[str, Any]:
        try:
            resp: Response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise bilibili_api_error(f"HTTP request failed: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise bilibili_api_error(
                f"non-JSON response (HTTP {resp.status_code})"
            ) from exc
        if not isinstance(payload, dict):
            raise bilibili_api_error("unexpected JSON payload")
        code = payload.get("code")
        if allow_nonzero:
            return payload
        if code not in (0, None):
            message = str(payload.get("message") or payload.get("msg") or "api error")
            raise bilibili_api_error(
                self._friendly_message(int(code), message),
                code=int(code),
            )
        return payload

    @staticmethod
    def _friendly_message(code: int, message: str) -> str:
        if code == code_region_lock:
            return (
                f"region-locked content (code {code}): {message} "
                "— udl does not bypass geo restrictions"
            )
        if code == code_paywall:
            return (
                f"paid / paywalled content (code {code}): {message} "
                "— udl does not bypass paywalls"
            )
        if "登录" in message or "login" in message.lower():
            return (
                f"login required (code {code}): {message} "
                "— use QR/Cookie login (udl bili login) or --sessdata"
            )
        if "会员" in message or "大會員" in message or "大会员" in message:
            return (
                f"VIP-only content (code {code}): {message} "
                "— udl only uses streams the current account is entitled to"
            )
        return f"bilibili API error {code}: {message}"
