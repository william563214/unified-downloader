"""Bilibili login session QR Cookie SMS and keychain storage.

Adapted from ScottSloan/Bili23-Downloader auth helpers under GPL-3.0.
Credentials live only in udl.secrets (OS keychain / 0600 fallback) — never logged.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from requests import Session

from udl.download.http import default_timeout
from udl.network import active_network, apply_to_session, default_user_agent
from udl.secrets import (
    bilibili_session_key,
    delete_secret,
    get_secret,
    set_secret,
)

logger = logging.getLogger(__name__)

passport_base = "https://passport.bilibili.com"
qr_generate_api = f"{passport_base}/x/passport-login/web/qrcode/generate"
qr_poll_api = f"{passport_base}/x/passport-login/web/qrcode/poll"
captcha_api = f"{passport_base}/x/passport-login/captcha"
sms_send_api = f"{passport_base}/x/passport-login/web/sms/send"
sms_login_api = f"{passport_base}/x/passport-login/web/login/sms"
cookie_refresh_api = f"{passport_base}/x/passport-login/web/cookie/refresh"
logout_api = f"{passport_base}/login/exit/v2"
nav_api = "https://api.bilibili.com/x/web-interface/nav"

login_cookie_keys = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")
optional_session_keys = ("refresh_token", "buvid3", "buvid4")

# QR poll data.code values
qr_code_success = 0
qr_code_expired = 86038
qr_code_unconfirmed = 86090
qr_code_unscanned = 86101

login_required_kinds = frozenset(
    {
        "watchlater",
        "history",
        "followed_bangumi",
        "subscribed_collections",
        "favorite_folders",
    }
)

login_note = (
    "Bilibili account session via QR / Cookie (full) or SMS (best-effort; "
    "GeeTest captcha needs a browser). Stored in udl.secrets key bilibili.session."
)

# keep alias for older imports during transition
login_stub_note = login_note

sms_geetest_note = (
    "SMS login requires GeeTest captcha completed by the user (no auto-solve). "
    "GUI opens a Tauri webview with gt/challenge; CLI: `udl bili login sms-captcha` "
    "then pass validate/seccode to sms-send. Prefer QR or Cookie when a display "
    "or geetest CDN is unavailable."
)


class requires_login(RuntimeError):
    """raised for account-only endpoints without a valid session."""

    code = "requires_login"

    def __init__(self, kind: str, *, detail: str | None = None) -> None:
        message = f"bilibili '{kind}' requires login (QR/Cookie/SMS via udl.secrets)"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.kind = kind


class login_error(RuntimeError):
    """login / session management failure."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class account_profile:
    """public-safe account status (no cookie values)."""

    logged_in: bool
    uname: str = ""
    mid: int | None = None
    face: str = ""
    vip_type: int = 0
    vip_status: int = 0
    vip_label: str = ""
    level: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "logged_in": self.logged_in,
            "uname": self.uname,
            "mid": self.mid,
            "face": self.face,
            "vip_type": self.vip_type,
            "vip_status": self.vip_status,
            "vip_label": self.vip_label,
            "level": self.level,
            "message": self.message,
        }


@dataclass
class qr_challenge:
    url: str
    qrcode_key: str
    image_png_base64: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url, "qrcode_key": self.qrcode_key}
        if self.image_png_base64:
            out["image_png_base64"] = self.image_png_base64
        return out


@dataclass
class qr_poll_result:
    code: int
    message: str
    refresh_token: str = ""
    cookies: dict[str, str] | None = None
    profile: account_profile | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "status": _qr_status_label(self.code),
        }
        if self.refresh_token:
            out["has_refresh_token"] = True
        if self.cookies is not None:
            out["logged_in"] = bool(self.cookies.get("SESSDATA"))
        if self.profile is not None:
            out["profile"] = self.profile.to_dict()
        return out


def ensure_not_login_only(kind: str) -> None:
    if kind in login_required_kinds:
        raise requires_login(kind)


def parse_cookie_text(text: str) -> dict[str, str]:
    """parse Cookie header JSON or semicolon/newline key=value paste."""
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.lower().startswith("cookie:"):
        raw = raw[len("cookie:") :].strip()

    cookies: dict[str, str] = {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(key, str) and value is not None:
                    cookies[key] = str(value)
            return cookies
    except (ValueError, TypeError):
        pass

    for part in raw.replace("\n", ";").split(";"):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip().strip('"')
        if sep and key and not any(ch.isspace() for ch in key):
            cookies[key] = value
    return cookies


def session_cookies_from_store() -> dict[str, str] | None:
    """load bilibili session cookies from keychain. never logs values."""
    raw = get_secret(bilibili_session_key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("bilibili.session secret is not valid JSON; ignoring")
        return None
    if not isinstance(data, dict):
        return None
    cookies = {str(k): str(v) for k, v in data.items() if v}
    if not cookies.get("SESSDATA"):
        return None
    return cookies


def save_session_cookies(
    cookies: dict[str, str],
    *,
    refresh_token: str | None = None,
) -> None:
    """persist login cookies to keychain (JSON blob)."""
    payload: dict[str, str] = {}
    for key in login_cookie_keys:
        value = cookies.get(key)
        if value:
            payload[key] = value
    for key in optional_session_keys:
        value = cookies.get(key)
        if value:
            payload[key] = value
    if refresh_token:
        payload["refresh_token"] = refresh_token
    if not payload.get("SESSDATA"):
        raise login_error("cannot save session without SESSDATA")
    set_secret(bilibili_session_key, json.dumps(payload, ensure_ascii=False))


def clear_session_cookies() -> bool:
    return delete_secret(bilibili_session_key)


def _auth_session(*, cookies: dict[str, str] | None = None) -> Session:
    session = Session()
    ua = active_network().user_agent or default_user_agent
    session.headers.update(
        {
            "User-Agent": ua,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    apply_to_session(session)
    session.headers["User-Agent"] = ua
    if cookies:
        for name, value in cookies.items():
            if value:
                session.cookies.set(str(name), str(value), domain=".bilibili.com")
    return session


def _extract_set_cookies(resp: requests.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    # requests CookieJar after redirect / set-cookie
    for cookie in resp.cookies:
        out[cookie.name] = cookie.value
    # also parse raw headers when jar miss (some proxies)
    raw = resp.headers.get("Set-Cookie") or ""
    if raw and "SESSDATA=" in raw:
        for part in raw.replace(",", ";").split(";"):
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            name = name.strip()
            if name in login_cookie_keys or name in optional_session_keys:
                out[name] = value.strip()
    return out


def _cookies_from_session(session: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (*login_cookie_keys, *optional_session_keys):
        value = session.cookies.get(key)
        if value:
            out[key] = value
    return out


def profile_from_nav(data: dict[str, Any]) -> account_profile:
    if not data.get("isLogin"):
        return account_profile(logged_in=False, message="not logged in")
    vip = data.get("vip") or {}
    label = ""
    if isinstance(vip, dict):
        label_obj = vip.get("label") or {}
        if isinstance(label_obj, dict):
            label = str(label_obj.get("text") or "")
        if not label and vip.get("vipType"):
            label = "大会员" if int(vip.get("vipType") or 0) > 0 else ""
    level_info = data.get("level_info") or {}
    mid_raw = data.get("mid")
    mid = int(mid_raw) if mid_raw is not None else None
    return account_profile(
        logged_in=True,
        uname=str(data.get("uname") or ""),
        mid=mid,
        face=str(data.get("face") or ""),
        vip_type=int(vip.get("vipType") or data.get("vipType") or 0)
        if isinstance(vip, dict)
        else int(data.get("vipType") or 0),
        vip_status=int(vip.get("vipStatus") or data.get("vipStatus") or 0)
        if isinstance(vip, dict)
        else int(data.get("vipStatus") or 0),
        vip_label=label,
        level=int(level_info.get("current_level") or 0)
        if isinstance(level_info, dict)
        else 0,
        message="ok",
    )


def validate_session(
    cookies: dict[str, str] | None = None,
    *,
    persist: bool = False,
) -> account_profile:
    """hit nav to validate cookies. optionally re-save when valid."""
    store = cookies or session_cookies_from_store()
    if not store or not store.get("SESSDATA"):
        return account_profile(logged_in=False, message="no stored session")
    session = _auth_session(cookies=store)
    try:
        resp = session.get(nav_api, timeout=default_timeout)
        payload = resp.json()
        jar_cookies = _cookies_from_session(session)
    except (requests.RequestException, ValueError) as exc:
        raise login_error(f"session validate failed: {exc}") from exc
    finally:
        session.close()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise login_error(
            "nav response missing data",
            code=payload.get("code") if isinstance(payload, dict) else None,
        )
    profile = profile_from_nav(data)
    if profile.logged_in and persist:
        merged = dict(store)
        merged.update(jar_cookies)
        save_session_cookies(merged, refresh_token=store.get("refresh_token"))
    if not profile.logged_in:
        profile.message = "session expired or invalid"
    return profile


def refresh_session() -> account_profile:
    """validate and optionally rotate cookies via refresh_token."""
    store = session_cookies_from_store()
    if not store or not store.get("SESSDATA"):
        return account_profile(logged_in=False, message="no stored session")
    refresh_token = store.get("refresh_token") or ""
    if refresh_token and store.get("bili_jct"):
        session = _auth_session(cookies=store)
        try:
            resp = session.post(
                cookie_refresh_api,
                data={
                    "csrf": store["bili_jct"],
                    "refresh_csrf": "",
                    "source": "main_web",
                    "refresh_token": refresh_token,
                },
                timeout=default_timeout,
            )
            # success path updates Set-Cookie; failure is non-fatal — fall back to validate
            if resp.ok:
                rotated = _extract_set_cookies(resp)
                if rotated.get("SESSDATA"):
                    merged = dict(store)
                    merged.update(rotated)
                    try:
                        body = resp.json()
                        new_rt = ((body.get("data") or {}) if isinstance(body, dict) else {}).get(
                            "refresh_token"
                        )
                        if new_rt:
                            merged["refresh_token"] = str(new_rt)
                    except ValueError:
                        pass
                    save_session_cookies(merged)
        except requests.RequestException:
            logger.debug("cookie refresh request failed; falling back to validate", exc_info=False)
        finally:
            session.close()
    return validate_session(persist=True)


def logout_session() -> account_profile:
    store = session_cookies_from_store()
    if store and store.get("bili_jct"):
        session = _auth_session(cookies=store)
        try:
            session.post(
                logout_api,
                data={"biliCSRF": store["bili_jct"]},
                timeout=default_timeout,
            )
        except requests.RequestException:
            logger.debug("remote logout failed; clearing local session anyway", exc_info=False)
        finally:
            session.close()
    clear_session_cookies()
    return account_profile(logged_in=False, message="logged out")


def login_with_cookies(text: str) -> account_profile:
    cookies = parse_cookie_text(text)
    if not cookies:
        raise login_error("cookie text could not be parsed")
    if not cookies.get("SESSDATA"):
        raise login_error("cookie paste missing SESSDATA")
    profile = validate_session(cookies, persist=False)
    if not profile.logged_in:
        raise login_error("cookie invalid or expired (nav isLogin=false)")
    save_session_cookies(cookies)
    return profile


def qr_generate(*, with_image: bool = True) -> qr_challenge:
    session = _auth_session()
    params = {
        "source": "main-fe-header",
        "go_url": "https://www.bilibili.com/",
        "web_location": "333.1007",
    }
    try:
        resp = session.get(f"{qr_generate_api}?{urlencode(params)}", timeout=default_timeout)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise login_error(f"QR generate failed: {exc}") from exc
    finally:
        session.close()
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        raise login_error(
            str(payload.get("message") if isinstance(payload, dict) else "QR generate error"),
            code=payload.get("code") if isinstance(payload, dict) else None,
        )
    data = payload.get("data") or {}
    url = str(data.get("url") or "")
    key = str(data.get("qrcode_key") or "")
    if not url or not key:
        raise login_error("QR generate response missing url/qrcode_key")
    image_b64 = _qr_png_base64(url) if with_image else None
    return qr_challenge(url=url, qrcode_key=key, image_png_base64=image_b64)


def qr_poll(qrcode_key: str, *, save_on_success: bool = True) -> qr_poll_result:
    if not qrcode_key:
        raise login_error("qrcode_key required")
    session = _auth_session()
    try:
        resp = session.get(
            f"{qr_poll_api}?{urlencode({'qrcode_key': qrcode_key})}",
            timeout=default_timeout,
        )
        payload = resp.json()
        set_cookies = _extract_set_cookies(resp)
        # follow cross-domain ticket URL when cookies not yet set
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and int(data.get("code") or -1) == qr_code_success:
            if not set_cookies.get("SESSDATA"):
                ticket_url = str(data.get("url") or "")
                if ticket_url.startswith("http"):
                    try:
                        confirm = session.get(ticket_url, timeout=default_timeout)
                        set_cookies.update(_extract_set_cookies(confirm))
                    except requests.RequestException:
                        logger.debug("QR ticket confirm failed", exc_info=False)
            set_cookies.update(_cookies_from_session(session))
    except (requests.RequestException, ValueError) as exc:
        raise login_error(f"QR poll failed: {exc}") from exc
    finally:
        session.close()

    if not isinstance(payload, dict):
        raise login_error("QR poll non-object response")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise login_error("QR poll missing data")
    code = int(data.get("code") or -1)
    message = str(data.get("message") or _qr_status_label(code))
    refresh_token = str(data.get("refresh_token") or "")
    if code != qr_code_success:
        return qr_poll_result(code=code, message=message, refresh_token=refresh_token)

    if not set_cookies.get("SESSDATA"):
        raise login_error(
            "QR confirmed but SESSDATA missing from response cookies "
            "(cross-domain ticket may require a browser)"
        )
    if save_on_success:
        save_session_cookies(set_cookies, refresh_token=refresh_token or None)
        profile = validate_session(persist=True)
    else:
        profile = validate_session(set_cookies, persist=False)
    return qr_poll_result(
        code=code,
        message=message,
        refresh_token=refresh_token,
        cookies={k: set_cookies[k] for k in login_cookie_keys if set_cookies.get(k)},
        profile=profile,
    )


def sms_captcha_init() -> dict[str, Any]:
    """fetch GeeTest challenge. browser still required to solve it."""
    session = _auth_session()
    try:
        resp = session.get(
            f"{captcha_api}?{urlencode({'source': 'main-fe-header', 't': '0.1'})}",
            timeout=default_timeout,
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise login_error(f"captcha init failed: {exc}") from exc
    finally:
        session.close()
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        raise login_error(
            str(payload.get("message") if isinstance(payload, dict) else "captcha error"),
            code=payload.get("code") if isinstance(payload, dict) else None,
        )
    data = payload.get("data") or {}
    geetest = data.get("geetest") or {}
    return {
        "token": data.get("token"),
        "gt": geetest.get("gt"),
        "challenge": geetest.get("challenge"),
        "note": sms_geetest_note,
        "best_effort": True,
    }


def sms_send(
    *,
    tel: str,
    cid: str = "86",
    token: str,
    challenge: str,
    validate: str,
    seccode: str,
) -> dict[str, Any]:
    """best-effort SMS send after external GeeTest solve."""
    if not (tel and token and challenge and validate and seccode):
        raise login_error("sms-send requires tel token challenge validate seccode")
    session = _auth_session()
    params = {
        "cid": cid,
        "tel": tel,
        "source": "main-fe-header",
        "token": token,
        "challenge": challenge,
        "validate": validate,
        "seccode": seccode,
    }
    try:
        resp = session.post(sms_send_api, data=params, timeout=default_timeout)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise login_error(f"sms send failed: {exc}") from exc
    finally:
        session.close()
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        raise login_error(
            str(payload.get("message") if isinstance(payload, dict) else "sms send error"),
            code=payload.get("code") if isinstance(payload, dict) else None,
        )
    data = payload.get("data") or {}
    return {
        "captcha_key": data.get("captcha_key"),
        "best_effort": True,
        "note": sms_geetest_note,
    }


def sms_login(
    *,
    tel: str,
    code: str,
    captcha_key: str,
    cid: str = "86",
) -> account_profile:
    """best-effort SMS login after sms_send."""
    if not (tel and code and captcha_key):
        raise login_error("sms login requires tel code captcha_key")
    session = _auth_session()
    params = {
        "cid": cid,
        "tel": tel,
        "code": code,
        "source": "main-fe-header",
        "captcha_key": captcha_key,
        "go_url": "https://www.bilibili.com/",
    }
    try:
        resp = session.post(sms_login_api, data=params, timeout=default_timeout)
        payload = resp.json()
        cookies = _extract_set_cookies(resp)
        cookies.update(_cookies_from_session(session))
    except (requests.RequestException, ValueError) as exc:
        raise login_error(f"sms login failed: {exc}") from exc
    finally:
        session.close()
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        raise login_error(
            str(payload.get("message") if isinstance(payload, dict) else "sms login error"),
            code=payload.get("code") if isinstance(payload, dict) else None,
        )
    if not cookies.get("SESSDATA"):
        raise login_error("sms login succeeded but SESSDATA missing")
    save_session_cookies(cookies)
    return validate_session(persist=True)


def _qr_png_base64(data: str) -> str | None:
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _qr_status_label(code: int) -> str:
    return {
        qr_code_success: "success",
        qr_code_expired: "expired",
        qr_code_unconfirmed: "scanned",
        qr_code_unscanned: "unscanned",
    }.get(code, f"code_{code}")
