"""Gofile extractor.

Ported / adapted from ltsdw/gofile-downloader
https://github.com/ltsdw/gofile-downloader
commit 48349ba3d017d4531bc3bc3d150e365bdd98f0d6
License: GPL-3.0

Guest token creation for public links follows upstream. The X-Website-Token
salt is reverse-engineered anti-bot; it may rotate and fail — surface errors.
"""

from __future__ import annotations

import hashlib
import time
from itertools import count
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import requests
from requests import Session

from udl.extractors.base import extract_result, extracted_file, extractor

# upstream-compatible defaults
gofile_api = "https://api.gofile.io"
gofile_origin = "https://gofile.io"
# salt from upstream generate_website_token — may rotate
gofile_token_salt = "12af056dacea0b"
gofile_user_agent = "Mozilla/5.0"
gofile_timeout = 15.0
gofile_retries = 5


def generate_website_token(user_agent: str, account_token: str) -> str:
    """replicate upstream X-Website-Token (sha256 of UA::locale::token::slot::salt)."""
    time_slot = int(time.time()) // 14400
    raw = f"{user_agent}::en-US::{account_token}::{time_slot}::{gofile_token_salt}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _content_id(url: str) -> str:
    parts = url.rstrip("/").split("/")
    if len(parts) < 2 or parts[-2] != "d":
        raise ValueError(f"not a gofile content URL: {url}")
    return parts[-1]


class gofile_extractor(extractor):
    site = "gofile"
    label = "Gofile"

    def __init__(self) -> None:
        self._session = Session()
        self._session.headers.update(
            {
                "Accept-Encoding": "gzip",
                "User-Agent": gofile_user_agent,
                "Connection": "keep-alive",
                "Accept": "*/*",
                "Origin": gofile_origin,
                "Referer": f"{gofile_origin}/",
            }
        )
        self._token: str | None = None

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        content_id = _content_id(url)
        self._ensure_guest_token()
        pw_hash = (
            hashlib.sha256(password.encode()).hexdigest() if password else None
        )
        files: list[extracted_file] = []
        pathing: dict[str, int] = {}
        title_holder: list[str] = [content_id]
        self._walk(
            parent_rel="",
            content_id=content_id,
            password=pw_hash,
            pathing=pathing,
            files=files,
            file_index=count(0),
            title_holder=title_holder,
            is_root=True,
        )
        if not files:
            raise RuntimeError(f"no downloadable files for {url}")
        # downloads require the guest Bearer token (upstream reuses Session)
        auth_headers = {
            "Authorization": f"Bearer {self._token}",
            "Cookie": f"accountToken={self._token}",
            "Origin": gofile_origin,
            "Referer": f"{gofile_origin}/",
        }
        for f in files:
            merged = dict(auth_headers)
            merged.update(f.headers)
            f.headers = merged
        return extract_result(
            site=self.site,
            title=title_holder[0],
            files=files,
            kind="folder" if len(files) > 1 else "file",
            meta={"content_id": content_id},
        )

    def _ensure_guest_token(self) -> None:
        """create a guest account like upstream when GF_TOKEN is unset.

        Isolated here and documented in NOTICE — public-link access only.
        """
        if self._token:
            return
        user_agent = str(self._session.headers.get("User-Agent", gofile_user_agent))
        wt = generate_website_token(user_agent, "")
        response: dict[str, Any] = {}
        last_err: Exception | None = None
        for _ in range(gofile_retries):
            try:
                response = self._session.post(
                    f"{gofile_api}/accounts",
                    headers={"X-Website-Token": wt, "X-BL": "en-US"},
                    timeout=gofile_timeout,
                ).json()
                break
            except requests.RequestException as exc:
                last_err = exc
                continue
        if not response or response.get("status") != "ok":
            raise RuntimeError(f"gofile guest account failed: {last_err or response}")
        token = response["data"]["token"]
        self._token = token
        self._session.cookies.set("accountToken", token)
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _get_json(self, url: str) -> dict[str, Any]:
        user_agent = str(self._session.headers.get("User-Agent", gofile_user_agent))
        auth = str(self._session.headers.get("Authorization", ""))
        account_token = auth.replace("Bearer ", "") if auth else ""
        wt = generate_website_token(user_agent, account_token)
        last_err: Exception | None = None
        for _ in range(gofile_retries):
            try:
                resp = self._session.get(
                    url,
                    headers={"X-Website-Token": wt, "X-BL": "en-US"},
                    timeout=gofile_timeout,
                )
                return resp.json()
            except requests.RequestException as exc:
                last_err = exc
                continue
        raise RuntimeError(f"gofile API request failed: {last_err}")

    def _walk(
        self,
        *,
        parent_rel: str,
        content_id: str,
        password: str | None,
        pathing: dict[str, int],
        files: list[extracted_file],
        file_index: count,
        title_holder: list[str],
        is_root: bool,
    ) -> None:
        api_url = (
            f"{gofile_api}/contents/{content_id}"
            f"?cache=true&sortField=createTime&sortDirection=1"
        )
        if password:
            api_url = f"{api_url}&password={password}"

        payload = self._get_json(api_url)
        if payload.get("status") != "ok":
            raise RuntimeError(f"gofile contents failed for {content_id}: {payload}")

        data = payload["data"]
        if "password" in data and data.get("passwordStatus") != "passwordOk":
            raise RuntimeError("password protected link — pass --password")

        if data["type"] != "folder":
            name = self._unique_name(pathing, parent_rel, data["name"])
            files.append(
                extracted_file(
                    url=data["link"],
                    name=name,
                    relative_dir=parent_rel,
                    size=data.get("size"),
                    meta={"id": data.get("id")},
                )
            )
            next(file_index)
            return

        folder_name = data["name"]
        if is_root:
            title_holder[0] = folder_name
            # mirror upstream: root folder under content_id dir
            rel = content_id
            if folder_name != content_id:
                rel = f"{content_id}/{folder_name}"
        else:
            unique = self._unique_name(pathing, parent_rel, folder_name, is_dir=True)
            rel = f"{parent_rel}/{unique}" if parent_rel else unique

        for child in data.get("children", {}).values():
            if child["type"] == "folder":
                self._walk(
                    parent_rel=rel,
                    content_id=child["id"],
                    password=password,
                    pathing=pathing,
                    files=files,
                    file_index=file_index,
                    title_holder=title_holder,
                    is_root=False,
                )
            else:
                name = self._unique_name(pathing, rel, child["name"])
                files.append(
                    extracted_file(
                        url=child["link"],
                        name=name,
                        relative_dir=rel,
                        size=child.get("size"),
                        meta={"id": child.get("id")},
                    )
                )
                next(file_index)

    @staticmethod
    def _unique_name(
        pathing: dict[str, int],
        parent_rel: str,
        child_name: str,
        *,
        is_dir: bool = False,
    ) -> str:
        key = f"{parent_rel}/{child_name}" if parent_rel else child_name
        n = pathing.get(key, 0)
        pathing[key] = n + 1
        if n == 0:
            return child_name
        if is_dir:
            return f"{child_name}({n})"
        stem = PurePosixPath(child_name).stem
        suffix = PurePosixPath(child_name).suffix
        return f"{stem}({n}){suffix}"


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if "gofile.io" not in host:
        return False
    try:
        _content_id(url)
        return True
    except ValueError:
        return False
