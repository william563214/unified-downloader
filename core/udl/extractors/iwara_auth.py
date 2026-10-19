"""Iwara account session: login, token refresh, keychain storage.

Ported / adapted from Moeary/IwaraTool ``app/core/api/client.py`` login
and ``users.py`` following endpoints (MIT; see root NOTICE).

Credentials and tokens live only in ``udl.secrets`` (OS keychain / secure
file fallback). Never log email, password, or token values.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

from requests import Session

from udl.extractors.iwara import (
    _make_session,
    default_api_headers,
    iwara_api,
    iwara_api_alt,
    iwara_timeout,
)
from udl.secrets import delete_secret, get_secret, set_secret

# well-known keychain keys (lowercase per project convention)
iwara_email_key = "iwara.email"
iwara_password_key = "iwara.password"
iwara_user_token_key = "iwara.user_token"
iwara_access_token_key = "iwara.access_token"

# refresh a bit before JWT exp to avoid race with in-flight requests
_token_skew_seconds = 120


class iwara_auth_error(RuntimeError):
    """login / token / session failures (message safe to show; no secrets)."""


@dataclass
class iwara_account:
    """safe public profile for whoami / GUI (no tokens)."""

    user_id: str | None = None
    username: str | None = None
    name: str | None = None
    email: str | None = None
    logged_in: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "logged_in": self.logged_in,
            "user_id": self.user_id,
            "username": self.username,
            "name": self.name,
            "email": self.email,
        }


def _b64url_json(segment: str) -> dict[str, Any] | None:
    raw = segment.encode("ascii")
    pad = b"=" * ((4 - len(raw) % 4) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def jwt_payload(token: str) -> dict[str, Any] | None:
    parts = (token or "").strip().split(".")
    if len(parts) < 2:
        return None
    return _b64url_json(parts[1])


def token_expired(token: str | None, *, skew: int = _token_skew_seconds) -> bool:
    if not token:
        return True
    payload = jwt_payload(token)
    if not payload:
        # opaque token without exp — treat as usable until API rejects
        return False
    exp = payload.get("exp")
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    return exp_i <= int(time.time()) - int(skew)


def _api_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("message", "error", "msg"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _friendly_login_failure(message: str, status: int) -> str:
    compact = (message or "").lower()
    if message == "errors.invalidLogin" or "invalidlogin" in compact:
        return "Invalid username/email or password"
    if "too many" in compact or status == 429:
        return "Too many login attempts; wait and retry"
    if status >= 400:
        return f"Login rejected (HTTP {status})"
    return message or "Login failed"


def stored_email() -> str | None:
    value = get_secret(iwara_email_key)
    return value.strip() if value and value.strip() else None


def clear_iwara_secrets() -> None:
    for key in (
        iwara_email_key,
        iwara_password_key,
        iwara_user_token_key,
        iwara_access_token_key,
    ):
        delete_secret(key)


def _store_tokens(*, email: str, password: str, user_token: str, access_token: str) -> None:
    set_secret(iwara_email_key, email)
    set_secret(iwara_password_key, password)
    set_secret(iwara_user_token_key, user_token)
    set_secret(iwara_access_token_key, access_token)


def _post_json(
    session: Session,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    bearer: str | None = None,
) -> tuple[int, Any]:
    headers = dict(default_api_headers)
    headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    resp = session.post(
        url,
        json=body if body is not None else {},
        headers=headers,
        timeout=iwara_timeout,
    )
    try:
        try:
            data: Any = resp.json()
        except ValueError:
            data = None
        return resp.status_code, data
    finally:
        resp.close()


def _get_json(
    session: Session,
    url: str,
    *,
    bearer: str | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    headers = dict(default_api_headers)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    resp = session.get(
        url,
        params=params,
        headers=headers,
        timeout=iwara_timeout,
    )
    try:
        try:
            data: Any = resp.json()
        except ValueError:
            data = None
        return resp.status_code, data
    finally:
        resp.close()


def login(
    email: str,
    password: str,
    *,
    use_cloudflare_bypass: bool = False,
    session: Session | None = None,
) -> iwara_account:
    """email/password → user token → access token; persist via udl.secrets."""
    credential = (email or "").strip()
    secret = password or ""
    if not credential or not secret:
        raise iwara_auth_error("Email/username and password are required")

    owns = session is None
    sess = session or _make_session(use_cloudflare_bypass=use_cloudflare_bypass)
    try:
        status, data = _post_json(
            sess,
            f"{iwara_api}/user/login",
            body={"email": credential, "password": secret},
        )
        if not isinstance(data, dict):
            raise iwara_auth_error(_friendly_login_failure("", status))
        user_token = data.get("token")
        if not (200 <= status < 300 and isinstance(user_token, str) and user_token.strip()):
            raise iwara_auth_error(
                _friendly_login_failure(_api_message(data), status)
            )
        user_token = user_token.strip()
        access = refresh_access_token(
            user_token,
            session=sess,
            use_cloudflare_bypass=use_cloudflare_bypass,
        )
        _store_tokens(
            email=credential,
            password=secret,
            user_token=user_token,
            access_token=access,
        )
        return whoami(
            access_token=access,
            session=sess,
            use_cloudflare_bypass=use_cloudflare_bypass,
        )
    finally:
        if owns:
            sess.close()


def refresh_access_token(
    user_token: str,
    *,
    session: Session | None = None,
    use_cloudflare_bypass: bool = False,
) -> str:
    """POST /user/token with user Bearer → accessToken (media/API bearer)."""
    token = (user_token or "").strip()
    if not token:
        raise iwara_auth_error("Missing user token")
    owns = session is None
    sess = session or _make_session(use_cloudflare_bypass=use_cloudflare_bypass)
    try:
        status, data = _post_json(
            sess,
            f"{iwara_api}/user/token",
            body={},
            bearer=token,
        )
        if not isinstance(data, dict):
            raise iwara_auth_error(f"Token refresh failed (HTTP {status})")
        access = data.get("accessToken") or data.get("token")
        if not (200 <= status < 300 and isinstance(access, str) and access.strip()):
            msg = _api_message(data) or f"HTTP {status}"
            raise iwara_auth_error(f"Token refresh failed: {msg}")
        access = access.strip()
        set_secret(iwara_access_token_key, access)
        set_secret(iwara_user_token_key, token)
        return access
    finally:
        if owns:
            sess.close()


def logout() -> None:
    clear_iwara_secrets()


def ensure_access_token(
    *,
    use_cloudflare_bypass: bool = False,
    session: Session | None = None,
    allow_relogin: bool = True,
) -> str | None:
    """return a usable access token from keychain, refreshing / re-login as needed."""
    access = get_secret(iwara_access_token_key)
    if access and not token_expired(access):
        return access.strip()

    owns = session is None
    sess = session or _make_session(use_cloudflare_bypass=use_cloudflare_bypass)
    try:
        user_token = get_secret(iwara_user_token_key)
        if user_token and not token_expired(user_token):
            try:
                return refresh_access_token(
                    user_token,
                    session=sess,
                    use_cloudflare_bypass=use_cloudflare_bypass,
                )
            except iwara_auth_error:
                pass

        if not allow_relogin:
            return None
        email = get_secret(iwara_email_key)
        password = get_secret(iwara_password_key)
        if email and password:
            account = login(
                email,
                password,
                use_cloudflare_bypass=use_cloudflare_bypass,
                session=sess,
            )
            del account
            access = get_secret(iwara_access_token_key)
            return access.strip() if access else None
        return None
    finally:
        if owns:
            sess.close()


def whoami(
    *,
    access_token: str | None = None,
    session: Session | None = None,
    use_cloudflare_bypass: bool = False,
) -> iwara_account:
    """validate session and return a safe account summary."""
    owns = session is None
    sess = session or _make_session(use_cloudflare_bypass=use_cloudflare_bypass)
    try:
        token = (access_token or "").strip() or ensure_access_token(
            use_cloudflare_bypass=use_cloudflare_bypass,
            session=sess,
        )
        email = stored_email()
        if not token:
            return iwara_account(logged_in=False, email=email)

        status, data = _get_json(sess, f"{iwara_api}/user", bearer=token)
        if status == 401:
            # try one refresh cycle then fail soft
            refreshed = ensure_access_token(
                use_cloudflare_bypass=use_cloudflare_bypass,
                session=sess,
                allow_relogin=True,
            )
            if refreshed and refreshed != token:
                status, data = _get_json(sess, f"{iwara_api}/user", bearer=refreshed)
                token = refreshed
        if not (200 <= status < 300) or not isinstance(data, dict):
            # fall back to JWT claims when /user is unavailable
            payload = jwt_payload(token) or {}
            user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
            username = (
                str(user.get("username") or payload.get("username") or "").strip()
                or None
            )
            if username or email:
                return iwara_account(
                    logged_in=True,
                    user_id=str(user.get("id") or payload.get("sub") or "") or None,
                    username=username,
                    name=str(user.get("name") or "").strip() or username,
                    email=email,
                )
            raise iwara_auth_error(
                f"Session invalid (HTTP {status}); try login again"
            )

        user_obj = data.get("user") if isinstance(data.get("user"), dict) else data
        if not isinstance(user_obj, dict):
            user_obj = {}
        username = str(
            user_obj.get("username") or data.get("username") or ""
        ).strip() or None
        name = str(user_obj.get("name") or data.get("name") or "").strip() or username
        user_id = str(user_obj.get("id") or data.get("id") or "").strip() or None
        return iwara_account(
            logged_in=True,
            user_id=user_id,
            username=username,
            name=name,
            email=email,
        )
    finally:
        if owns:
            sess.close()


def list_following(
    user_id: str,
    *,
    access_token: str | None = None,
    max_pages: int = 100,
    use_cloudflare_bypass: bool = False,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """fetch users followed by ``user_id`` (requires login)."""
    uid = (user_id or "").strip()
    if not uid:
        raise iwara_auth_error("Missing user id")
    owns = session is None
    sess = session or _make_session(use_cloudflare_bypass=use_cloudflare_bypass)
    try:
        token = (access_token or "").strip() or ensure_access_token(
            use_cloudflare_bypass=use_cloudflare_bypass,
            session=sess,
        )
        if not token:
            raise iwara_auth_error("Login required to list following")

        users: list[dict[str, Any]] = []
        last_error = ""
        for root in (iwara_api, iwara_api_alt):
            users = []
            had_response = False
            for page in range(max(1, int(max_pages))):
                try:
                    status, data = _get_json(
                        sess,
                        f"{root}/user/{uid}/following",
                        bearer=token,
                        params={"page": page, "limit": 50},
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    users = []
                    break
                had_response = True
                if status == 401:
                    token = ensure_access_token(
                        use_cloudflare_bypass=use_cloudflare_bypass,
                        session=sess,
                    ) or token
                    status, data = _get_json(
                        sess,
                        f"{root}/user/{uid}/following",
                        bearer=token,
                        params={"page": page, "limit": 50},
                    )
                if not (200 <= status < 300) or not isinstance(data, dict):
                    last_error = f"{root}: HTTP {status}"
                    users = []
                    break
                results = data.get("results", [])
                if not isinstance(results, list) or not results:
                    break
                for item in results:
                    if isinstance(item, dict):
                        users.append(item)
                if len(results) < 50:
                    break
            if had_response and not last_error:
                return users
            if had_response and users:
                return users
        if users:
            return users
        raise iwara_auth_error(last_error or "Failed to fetch following users")
    finally:
        if owns:
            sess.close()


def normalize_following_entry(item: dict[str, Any]) -> dict[str, str] | None:
    """map a following API row to author username / id / display name."""
    user = item.get("user") if isinstance(item.get("user"), dict) else item
    if not isinstance(user, dict):
        return None
    username = str(user.get("username") or "").strip()
    if not username:
        return None
    name = str(user.get("name") or username).strip() or username
    user_id = str(user.get("id") or "").strip()
    return {
        "username": username,
        "name": name,
        "user_id": user_id,
    }
