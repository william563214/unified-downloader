"""Anonymous Bilibili HTTP client (no VIP/paywall/region bypass)."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode

import requests
from requests import Response, Session

from udl.download.http import default_timeout, default_user_agent
from udl.extractors.bilibili.wbi import enc_wbi, store_wbi_keys

api_base = "https://api.bilibili.com"
nav_api = f"{api_base}/x/web-interface/nav"
spi_api = f"{api_base}/x/frontend/finger/spi"
view_api = f"{api_base}/x/web-interface/wbi/view"
playurl_api = f"{api_base}/x/player/wbi/playurl"
bangumi_season_api = f"{api_base}/pgc/view/web/season"
bangumi_media_api = f"{api_base}/pgc/review/user"
bangumi_playurl_api = f"{api_base}/pgc/player/web/playurl"
referer = "https://www.bilibili.com/"

# playurl fnval flags (DASH + extras) from Bili23
video_fnval = 4048
bangumi_fnval = 143312

# API codes we surface without retry/bypass
code_region_lock = -10403
code_paywall = 87008


class bilibili_api_error(RuntimeError):
    """remote API error with numeric code when available."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class bilibili_client:
    """session-backed client for view / playurl / bangumi."""

    def __init__(
        self,
        *,
        user_agent: str = default_user_agent,
        timeout: float = default_timeout,
        session: Session | None = None,
    ) -> None:
        self.timeout = timeout
        self._own_session = session is None
        self.session = session or Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Referer": referer,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://www.bilibili.com",
            }
        )
        self._bootstrapped = False

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
            # spi failure is non-fatal; view may still work
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

    def view(self, bvid: str) -> dict[str, Any]:
        self.bootstrap()
        query = enc_wbi({"bvid": bvid})
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
                "— QR/cookie login is stubbed for a later release"
            )
        if "会员" in message or "大會員" in message or "大会员" in message:
            return (
                f"VIP-only content (code {code}): {message} "
                "— udl does not bypass VIP restrictions"
            )
        return f"bilibili API error {code}: {message}"
