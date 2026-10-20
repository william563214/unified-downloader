"""MEGA JSON API client (g.api.mega.co.nz/cs).

Sequence ids, error codes, retries/backoff. No SmartProxy / IP rotation.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from requests import Session

from udl.extractors.mega.crypto import (
    base64_to_a32,
    base64_url_decode,
    bytes_to_a32,
    decrypt_attr,
    decrypt_key,
    decrypt_rsa_sid,
    derive_file_key_parts,
    pbkdf2_v2_password,
    prepare_key,
    stringhash,
)
from udl.extractors.mega.errors import mega_api_error, mega_auth_error, mega_quota_error
from udl.network import apply_to_session, default_user_agent

api_base = "https://g.api.mega.co.nz"
default_timeout = 30.0
default_retries = 8
# well-known MEGA API integer errors
api_eagain = -3
api_eaccess = -11
api_eexpired = -15
api_etempunavail = -18


@dataclass
class mega_node:
    handle: str
    parent: str | None
    kind: int  # 0 file 1 folder 2 inbox 3 root 4 rubbish
    name: str
    size: int
    key_a32: tuple[int, ...] | None = None
    iv_a32: tuple[int, ...] | None = None
    meta_mac: tuple[int, int] | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    share_key: tuple[int, ...] | None = None

    @property
    def is_file(self) -> bool:
        return self.kind == 0

    @property
    def is_folder(self) -> bool:
        return self.kind == 1


@dataclass
class public_file_info:
    handle: str
    name: str
    size: int
    key_a32: tuple[int, int, int, int]
    iv_a32: tuple[int, int, int, int]
    meta_mac: tuple[int, int]
    download_url: str
    attrs: dict[str, Any]


class mega_client:
    """thin MEGA API wrapper with session + public-link helpers."""

    def __init__(
        self,
        *,
        session: Session | None = None,
        sid: str | None = None,
        master_key: tuple[int, ...] | None = None,
        timeout: float = default_timeout,
        user_agent: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.sid = sid
        self.master_key = master_key
        self.sequence = random.randint(0, 0xFFFFFFFF)
        self._own_session = session is None
        self.session = session or Session()
        if session is None:
            apply_to_session(self.session)
        ua = user_agent or default_user_agent
        self.session.headers.setdefault("User-Agent", ua)
        self.email: str | None = None

    def close(self) -> None:
        if self._own_session:
            self.session.close()

    def request(
        self,
        payload: dict[str, Any] | list[dict[str, Any]],
        *,
        retries: int = default_retries,
    ) -> Any:
        data = payload if isinstance(payload, list) else [payload]
        last_err: Exception | None = None
        for attempt in range(retries):
            params: dict[str, Any] = {"id": self.sequence % 0xFFFFFFFF}
            self.sequence += 1
            if self.sid:
                params["sid"] = self.sid
            try:
                resp = self.session.post(
                    f"{api_base}/cs",
                    params=params,
                    data=json.dumps(data),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(min(2**attempt, 30))
                continue

            if resp.status_code == 509:
                wait = _parse_retry_after(resp)
                raise mega_quota_error(
                    "MEGA transfer quota exceeded (HTTP 509). "
                    "Please wait for the quota to reset, or use a MEGA account "
                    "with remaining transfer quota. "
                    "Quota-bypass / SmartProxy / IP rotation are not supported."
                    + (f" Suggested wait: ~{wait}s." if wait else ""),
                    wait_seconds=wait,
                )
            if resp.status_code >= 500:
                time.sleep(min(2**attempt, 30))
                continue

            try:
                body = resp.json()
            except ValueError as exc:
                last_err = exc
                time.sleep(min(2**attempt, 30))
                continue

            code = _api_int(body)
            if code is not None:
                if code == 0:
                    return code
                if code == api_eagain:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise mega_api_error(code, _api_message(code))
            if isinstance(body, list) and body:
                return body[0]
            return body

        raise mega_api_error(-1, f"MEGA API request failed after retries: {last_err}")

    # --- auth ---

    def login(self, email: str, password: str) -> dict[str, Any]:
        email_l = email.strip().lower()
        salt_resp = self.request({"a": "us0", "user": email_l})
        if isinstance(salt_resp, dict) and "s" in salt_resp:
            salt = base64_to_a32(str(salt_resp["s"]))
            password_aes, user_hash = pbkdf2_v2_password(password, salt)
        else:
            password_aes = prepare_key(bytes_to_a32(password.encode("utf-8")))
            user_hash = stringhash(email_l, password_aes)

        resp = self.request({"a": "us", "user": email_l, "uh": user_hash})
        if not isinstance(resp, dict):
            raise mega_auth_error("unexpected login response")
        self._login_process(resp, password_aes)
        self.email = email_l
        return {"email": email_l, "sid": self.sid}

    def _login_process(self, resp: dict[str, Any], password_aes: tuple[int, ...]) -> None:
        encrypted_master = base64_to_a32(str(resp["k"]))
        self.master_key = decrypt_key(encrypted_master, password_aes)
        if "tsid" in resp:
            self.sid = str(resp["tsid"])
        elif "csid" in resp:
            privk = base64_to_a32(str(resp["privk"]))
            self.sid = decrypt_rsa_sid(str(resp["csid"]), privk, self.master_key)
        else:
            raise mega_auth_error("login response missing session id")

    def logout(self) -> None:
        if self.sid:
            try:
                self.request({"a": "sml"})
            except mega_api_error:
                pass
        self.sid = None
        self.master_key = None
        self.email = None

    def whoami(self) -> dict[str, Any]:
        if not self.sid:
            raise mega_auth_error("not logged in")
        user = self.request({"a": "ug"})
        if not isinstance(user, dict):
            raise mega_auth_error("failed to fetch user")
        return {
            "email": user.get("email") or self.email,
            "name": user.get("name"),
            "sid_present": bool(self.sid),
        }

    # --- public links ---

    def get_public_file(self, handle: str, key: str) -> public_file_info:
        file_key = base64_to_a32(key)
        k, iv, meta_mac = derive_file_key_parts(file_key)
        data = self.request({"a": "g", "g": 1, "p": handle})
        if not isinstance(data, dict) or "g" not in data:
            raise mega_api_error(-9, "public file not accessible")
        attrs_raw = base64_url_decode(str(data["at"]))
        attrs = decrypt_attr(attrs_raw, k) or {}
        name = str(attrs.get("n") or handle)
        return public_file_info(
            handle=handle,
            name=name,
            size=int(data["s"]),
            key_a32=k,
            iv_a32=iv,
            meta_mac=meta_mac,
            download_url=str(data["g"]),
            attrs=attrs,
        )

    def get_node_download(self, handle: str, key_a32: tuple[int, ...]) -> public_file_info:
        if len(key_a32) >= 8:
            k, iv, meta_mac = derive_file_key_parts(key_a32)
        else:
            k = (key_a32[0], key_a32[1], key_a32[2], key_a32[3])
            iv = (0, 0, 0, 0)
            meta_mac = (0, 0)
        data = self.request({"a": "g", "g": 1, "n": handle})
        if not isinstance(data, dict) or "g" not in data:
            raise mega_api_error(-9, "file not accessible")
        attrs_raw = base64_url_decode(str(data.get("at") or ""))
        attrs = decrypt_attr(attrs_raw, k) or {}
        name = str(attrs.get("n") or handle)
        return public_file_info(
            handle=handle,
            name=name,
            size=int(data["s"]),
            key_a32=k,
            iv_a32=iv,
            meta_mac=meta_mac,
            download_url=str(data["g"]),
            attrs=attrs,
        )

    def list_public_folder(
        self,
        handle: str,
        key: str,
        *,
        sub_handle: str | None = None,
    ) -> list[mega_node]:
        folder_key = base64_to_a32(key)
        data = self.request({"a": "f", "c": 1, "r": 1, "ca": 1, "p": handle})
        if not isinstance(data, dict):
            raise mega_api_error(-9, "public folder not accessible")
        nodes_raw = data.get("f") or []
        share_keys: dict[str, tuple[int, ...]] = {handle: folder_key}
        # shared keys in response
        for sk in data.get("ok") or []:
            if isinstance(sk, dict) and "h" in sk and "k" in sk:
                try:
                    share_keys[str(sk["h"])] = decrypt_key(
                        base64_to_a32(str(sk["k"])), folder_key
                    )
                except Exception:  # noqa: BLE001
                    pass

        nodes: list[mega_node] = []
        by_handle: dict[str, mega_node] = {}
        for raw in nodes_raw:
            if not isinstance(raw, dict):
                continue
            h = str(raw.get("h") or "")
            parent = str(raw.get("p") or "") or None
            kind = int(raw.get("t") or 0)
            size = int(raw.get("s") or 0)
            key_a32 = None
            iv_a32 = None
            meta_mac = None
            attrs: dict[str, Any] = {}
            k_field = str(raw.get("k") or "")
            if ":" in k_field:
                share_h, enc = k_field.split(":", 1)
                sk = share_keys.get(share_h) or folder_key
                try:
                    dec = decrypt_key(base64_to_a32(enc), sk)
                    if kind == 0 and len(dec) >= 8:
                        k, iv_a32, meta_mac = derive_file_key_parts(dec)
                        key_a32 = dec
                        # store full for download; aes key for attrs
                        attrs_raw = base64_url_decode(str(raw.get("a") or ""))
                        attrs = decrypt_attr(attrs_raw, k) or {}
                    else:
                        key_a32 = dec
                        attrs_raw = base64_url_decode(str(raw.get("a") or ""))
                        attrs = decrypt_attr(attrs_raw, dec[:4] if len(dec) >= 4 else dec) or {}
                        if kind == 1:
                            share_keys[h] = dec
                except Exception:  # noqa: BLE001
                    attrs = {}
            name = str(attrs.get("n") or h)
            node = mega_node(
                handle=h,
                parent=parent,
                kind=kind,
                name=name,
                size=size,
                key_a32=key_a32,
                iv_a32=iv_a32,
                meta_mac=meta_mac,
                attrs=attrs,
                share_key=share_keys.get(h),
            )
            nodes.append(node)
            by_handle[h] = node

        if sub_handle and sub_handle in by_handle:
            allowed = _subtree_handles(by_handle, sub_handle)
            nodes = [n for n in nodes if n.handle in allowed]
        return nodes

    def list_cloud_drive(self) -> list[mega_node]:
        if not self.sid or not self.master_key:
            raise mega_auth_error("login required for cloud drive")
        data = self.request({"a": "f", "c": 1})
        if not isinstance(data, dict):
            raise mega_api_error(-1, "failed to list cloud drive")
        master = self.master_key
        nodes: list[mega_node] = []
        for raw in data.get("f") or []:
            if not isinstance(raw, dict):
                continue
            h = str(raw.get("h") or "")
            parent = str(raw.get("p") or "") or None
            kind = int(raw.get("t") or 0)
            size = int(raw.get("s") or 0)
            key_a32 = None
            iv_a32 = None
            meta_mac = None
            attrs: dict[str, Any] = {}
            k_field = str(raw.get("k") or "")
            if ":" in k_field:
                _owner, enc = k_field.split(":", 1)
                try:
                    dec = decrypt_key(base64_to_a32(enc), master)
                    if kind == 0 and len(dec) >= 8:
                        k, iv_a32, meta_mac = derive_file_key_parts(dec)
                        key_a32 = dec
                        attrs = decrypt_attr(
                            base64_url_decode(str(raw.get("a") or "")), k
                        ) or {}
                    else:
                        key_a32 = dec
                        attrs = decrypt_attr(
                            base64_url_decode(str(raw.get("a") or "")),
                            dec[:4] if len(dec) >= 4 else dec,
                        ) or {}
                except Exception:  # noqa: BLE001
                    pass
            name = str(attrs.get("n") or h)
            nodes.append(
                mega_node(
                    handle=h,
                    parent=parent,
                    kind=kind,
                    name=name,
                    size=size,
                    key_a32=key_a32,
                    iv_a32=iv_a32,
                    meta_mac=meta_mac,
                    attrs=attrs,
                )
            )
        return nodes

    def refresh_download_url(self, info: public_file_info, *, public: bool) -> str:
        if public:
            data = self.request({"a": "g", "g": 1, "p": info.handle})
        else:
            data = self.request({"a": "g", "g": 1, "n": info.handle})
        if not isinstance(data, dict) or "g" not in data:
            raise mega_api_error(-9, "download URL unavailable")
        return str(data["g"])


def build_folder_paths(nodes: list[mega_node]) -> dict[str, str]:
    """map handle -> relative posix path including filename."""
    by_h = {n.handle: n for n in nodes}
    cache: dict[str, str] = {}

    def path_of(h: str) -> str:
        if h in cache:
            return cache[h]
        n = by_h.get(h)
        if n is None:
            return ""
        parent_path = ""
        if n.parent and n.parent in by_h and by_h[n.parent].kind == 1:
            parent_path = path_of(n.parent)
        parts = [p for p in (parent_path, n.name) if p]
        result = "/".join(parts)
        cache[h] = result
        return result

    return {n.handle: path_of(n.handle) for n in nodes}


def _subtree_handles(by_handle: dict[str, mega_node], root: str) -> set[str]:
    allowed = {root}
    changed = True
    while changed:
        changed = False
        for h, n in by_handle.items():
            if h not in allowed and n.parent in allowed:
                allowed.add(h)
                changed = True
    return allowed


def _api_int(body: Any) -> int | None:
    if isinstance(body, int):
        return body
    if isinstance(body, list) and body and isinstance(body[0], int) and len(body) == 1:
        return body[0]
    return None


def _api_message(code: int) -> str:
    messages = {
        -1: "EINTERNAL",
        -2: "EARGS",
        -3: "EAGAIN",
        -4: "ERATELIMIT",
        -5: "EFAILED",
        -6: "ETOOMANY",
        -7: "ERANGE",
        -8: "EEXPIRED",
        -9: "ENOENT",
        -10: "ECIRCULAR",
        -11: "EACCESS",
        -12: "EEXIST",
        -13: "EINCOMPLETE",
        -14: "EKEY",
        -15: "ESID",
        -16: "EBLOCKED",
        -17: "EOVERQUOTA",
        -18: "ETEMPUNAVAIL",
    }
    name = messages.get(code, "UNKNOWN")
    if code == -17:
        return (
            f"MEGA storage/transfer over quota ({name}). "
            "Wait for quota reset or use an account with remaining quota. "
            "Bypass features are not supported."
        )
    return f"MEGA API {name} ({code})"


def _parse_retry_after(resp: requests.Response) -> int | None:
    raw = resp.headers.get("Retry-After") or resp.headers.get("X-Retry-After")
    if raw and str(raw).isdigit():
        return int(raw)
    # MEGA sometimes embeds wait in body text
    text = (resp.text or "").strip()
    if text.isdigit():
        return int(text)
    return None


def session_from_secrets(payload: dict[str, Any]) -> mega_client:
    sid = str(payload.get("sid") or "") or None
    master = payload.get("master_key")
    master_key = tuple(int(x) for x in master) if isinstance(master, list) else None
    client = mega_client(sid=sid, master_key=master_key)
    client.email = str(payload.get("email") or "") or None
    return client


def session_to_secrets(client: mega_client) -> dict[str, Any]:
    return {
        "email": client.email,
        "sid": client.sid,
        "master_key": list(client.master_key) if client.master_key else None,
    }
