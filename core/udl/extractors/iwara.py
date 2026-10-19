"""Iwara extractor.

Ported / adapted from Moeary/IwaraTool
https://github.com/Moeary/IwaraTool
commit 1e98c96c422a169699c0d16bcab49b8a7f5238d5
License: MIT

Decoupled from PySide6 / QSettings. Public content works without login.
Optional Bearer token is accepted in-memory only (not persisted).
Cloudflare challenge bypass (cloudscraper) is opt-in and off by default.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from requests import Response, Session

from udl.extractors.base import extract_result, extracted_file, extractor
from udl.extractors.iwara_xversion import (
    iter_x_versions,
    iwara_xversion_error,
    merge_salts,
    x_version_rotate_hint,
)
from udl.metadata import item_metadata

# api hosts from upstream endpoints.py
iwara_api = "https://api.iwara.tv"
iwara_api_alt = "https://apiq.iwara.tv"
iwara_origin = "https://www.iwara.tv"
iwara_timeout = 30.0
iwara_max_pages = 100
iwara_page_limit = 32
# quality ladder matches upstream VideoApiMixin._pick_download_quality
iwara_qualities = ("Source", "540", "360")
iwara_default_quality = "Source"
iwara_user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
default_api_headers = {
    "Accept": "application/json, text/plain, */*",
    "Origin": iwara_origin,
    "Referer": f"{iwara_origin}/",
    "X-Site": "www.iwara.tv",
    "User-Agent": iwara_user_agent,
}
invalid_chars = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    cleaned = invalid_chars.sub("_", name).strip().rstrip(".")
    return cleaned or "download.bin"


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "iwara.tv" in host or "iwara.ai" in host


def parse_iwara_url(url: str) -> tuple[str, str] | None:
    """return ``(kind, value)`` for video / playlist / user profile urls.

    kind is one of ``video`` ``playlist`` ``user``. Search/tag listing urls
    are out of v0.2 scope.
    """
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if "iwara.tv" not in host and "iwara.ai" not in host:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    lower = [p.lower() for p in parts]

    def _next_after(key: str) -> str | None:
        try:
            idx = lower.index(key)
        except ValueError:
            return None
        if idx + 1 >= len(parts):
            return None
        return parts[idx + 1]

    video_id = _next_after("video")
    if video_id:
        return "video", video_id
    playlist_id = _next_after("playlist")
    if playlist_id:
        return "playlist", playlist_id
    username = _next_after("user") or _next_after("profile")
    if username:
        return "user", username
    return None


def _make_session(*, use_cloudflare_bypass: bool) -> Session:
    """build HTTP session. cloudscraper only when explicitly enabled."""
    if use_cloudflare_bypass:
        try:
            import cloudscraper
        except ImportError as exc:
            raise RuntimeError(
                "Iwara Cloudflare bypass requires optional dependency "
                "'cloudscraper' (uv pip install cloudscraper). "
                "Bypass stays off by default."
            ) from exc
        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    else:
        session = Session()
    session.headers.update(default_api_headers)
    return session


def _response_looks_like_challenge(resp: Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        return False
    text = (resp.text or "")[:200].lower()
    return (
        "just a moment" in text
        or "cf-challenge" in text
        or "cloudflare" in text
        or text.lstrip().startswith("<!doctype html")
        or text.lstrip().startswith("<html")
    )


def _cloudflare_hint(use_cloudflare_bypass: bool) -> str:
    if use_cloudflare_bypass:
        return (
            "Cloudflare challenge still blocked the request even with "
            "bypass enabled. Try another network or proxy."
        )
    return (
        "Response looks like a Cloudflare challenge page. "
        "Public API access from this network may be blocked. "
        "Opt-in bypass: pass --cloudflare-bypass (requires cloudscraper) "
        "or set use_cloudflare_bypass on the extractor. Disabled by default."
    )


class iwara_extractor(extractor):
    site = "iwara"
    label = "Iwara"

    def __init__(
        self,
        *,
        preferred_quality: str = iwara_default_quality,
        token: str | None = None,
        use_cloudflare_bypass: bool = False,
        extra_salts: Sequence[str] | None = None,
        max_pages: int = iwara_max_pages,
    ) -> None:
        self.preferred_quality = preferred_quality or iwara_default_quality
        # optional JWT — kept in memory only never written to disk
        self.token = (token or "").strip() or None
        self.use_cloudflare_bypass = bool(use_cloudflare_bypass)
        self.extra_salts = tuple(extra_salts or ())
        self.max_pages = max(1, int(max_pages))
        self._session = _make_session(use_cloudflare_bypass=self.use_cloudflare_bypass)

    def configure(
        self,
        *,
        quality: str | None = None,
        token: str | None = None,
        cloudflare_bypass: bool | None = None,
    ) -> None:
        """apply CLI / GUI options without rebuilding the extractor."""
        if quality:
            self.preferred_quality = quality
        if token is not None:
            self.token = token.strip() or None
        if cloudflare_bypass is not None and cloudflare_bypass != self.use_cloudflare_bypass:
            self.use_cloudflare_bypass = bool(cloudflare_bypass)
            self._session = _make_session(use_cloudflare_bypass=self.use_cloudflare_bypass)

    def resolve(
        self,
        url: str,
        *,
        password: str | None = None,
    ) -> extract_result:
        del password
        parsed = parse_iwara_url(url)
        if parsed is None:
            raise ValueError(
                f"unsupported Iwara URL (need /video/ /playlist/ or /profile/): {url}"
            )
        kind, value = parsed
        if kind == "video":
            return self._resolve_video(value)
        if kind == "playlist":
            return self._resolve_playlist(value)
        if kind == "user":
            return self._resolve_user(value)
        raise ValueError(f"unsupported Iwara URL kind: {kind}")

    # ── auth / HTTP ──────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        resp = self._session.get(
            url,
            params=params,
            headers={**self._auth_headers()},
            timeout=iwara_timeout,
        )
        try:
            if _response_looks_like_challenge(resp):
                raise RuntimeError(_cloudflare_hint(self.use_cloudflare_bypass))
            if resp.status_code >= 400:
                preview = " ".join((resp.text or "").strip().split())[:200]
                raise RuntimeError(f"HTTP {resp.status_code}: {preview or resp.reason}")
            try:
                return resp.json()
            except ValueError as exc:
                if _response_looks_like_challenge(resp):
                    raise RuntimeError(_cloudflare_hint(self.use_cloudflare_bypass)) from exc
                raise RuntimeError(
                    f"Iwara API did not return JSON (HTTP {resp.status_code})"
                ) from exc
        finally:
            resp.close()

    # ── listing helpers ──────────────────────────────────────────────────────

    def _get_user_id(self, username: str) -> str:
        data = self._get_json(f"{iwara_api}/profile/{username}")
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected profile response for {username}")
        uid = (data.get("user") or {}).get("id") if isinstance(data.get("user"), dict) else None
        if not uid:
            raise RuntimeError(f"profile did not return user.id for {username}")
        return str(uid)

    def _list_user_videos(self, user_id: str) -> list[dict[str, Any]]:
        videos: list[dict[str, Any]] = []
        for page in range(self.max_pages + 1):
            data = self._get_json(
                f"{iwara_api}/videos",
                params={
                    "page": page,
                    "sort": "date",
                    "user": user_id,
                    "limit": iwara_page_limit,
                },
            )
            results = data.get("results", []) if isinstance(data, dict) else []
            if not isinstance(results, list) or not results:
                break
            videos.extend(item for item in results if isinstance(item, dict))
            if len(results) < iwara_page_limit:
                break
        return videos

    def _list_playlist_videos(self, playlist_id: str) -> tuple[str, list[dict[str, Any]]]:
        videos: list[dict[str, Any]] = []
        title = playlist_id
        for page in range(self.max_pages + 1):
            data = self._get_json(
                f"{iwara_api}/playlist/{playlist_id}",
                params={"page": page, "limit": iwara_page_limit},
            )
            if not isinstance(data, dict):
                break
            if page == 0:
                title = str(data.get("title") or data.get("name") or playlist_id)
            results = data.get("results", [])
            if not isinstance(results, list) or not results:
                break
            videos.extend(item for item in results if isinstance(item, dict))
            if len(results) < iwara_page_limit:
                break
        return title, videos

    def _get_video_info(self, video_id: str) -> dict[str, Any]:
        last_error = ""
        for root in (iwara_api, iwara_api_alt):
            try:
                data = self._get_json(f"{root}/video/{video_id}")
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if "404" in last_error.lower() or "not found" in last_error.lower():
                    raise RuntimeError(
                        "video not found or not visible without login "
                        f"(id={video_id})"
                    ) from exc
                continue
            if isinstance(data, str) and data.startswith("<"):
                raise RuntimeError(_cloudflare_hint(self.use_cloudflare_bypass))
            if not isinstance(data, dict):
                raise RuntimeError(f"unexpected video response type: {type(data).__name__}")
            return data
        raise RuntimeError(last_error or f"failed to fetch video {video_id}")

    # ── quality / download URL ───────────────────────────────────────────────

    def _fetch_file_sources(self, file_url: str) -> list[dict[str, Any]]:
        salts = merge_salts(self.extra_salts)
        last_error = ""
        saw_reject = False
        for _salt, x_version in iter_x_versions(file_url, salts):
            resp = None
            try:
                resp = self._session.get(
                    file_url,
                    headers={**self._auth_headers(), "X-Version": x_version},
                    timeout=iwara_timeout,
                )
                if _response_looks_like_challenge(resp):
                    raise RuntimeError(_cloudflare_hint(self.use_cloudflare_bypass))
                if resp.status_code in (401, 403):
                    saw_reject = True
                    last_error = f"HTTP {resp.status_code}"
                    continue
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    last_error = "file source response was not JSON"
                    saw_reject = True
                    continue
                if isinstance(data, list) and data:
                    return [item for item in data if isinstance(item, dict)]
                last_error = f"empty or non-list file sources: {data!r}"
            except iwara_xversion_error:
                raise
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            finally:
                if resp is not None:
                    resp.close()
        if saw_reject:
            raise iwara_xversion_error(x_version_rotate_hint)
        raise RuntimeError(
            f"failed to get Iwara file sources: {last_error or 'unknown error'}"
        )

    def _pick_download_url(
        self,
        sources: list[dict[str, Any]],
        preferred: str,
    ) -> tuple[str, str]:
        sources_map = {
            str(s.get("name", "")).lower(): s for s in sources if isinstance(s, dict)
        }
        try:
            start = iwara_qualities.index(preferred)
        except ValueError:
            start = 0
        order = list(iwara_qualities[start:])
        for quality in order:
            entry = sources_map.get(quality.lower())
            if not entry:
                continue
            src = entry.get("src")
            raw = src.get("download", "") if isinstance(src, dict) else ""
            if not raw:
                continue
            dl_url = f"https:{raw}" if str(raw).startswith("//") else str(raw)
            return dl_url, str(entry.get("name") or quality)
        names = [s.get("name", "?") for s in sources]
        raise RuntimeError(
            f"no usable quality (tried {order}; server returned {names})"
        )

    def _download_headers(self) -> dict[str, str]:
        headers = {
            "Referer": f"{iwara_origin}/",
            "Origin": iwara_origin,
            "User-Agent": iwara_user_agent,
        }
        headers.update(self._auth_headers())
        return headers

    def _metadata_from_video_info(
        self,
        video_info: dict[str, Any],
        *,
        quality: str | None = None,
        collection: str | None = None,
        collection_id: str | None = None,
    ) -> item_metadata:
        video_id = str(video_info.get("id") or video_info.get("video_id") or "")
        title = str(video_info.get("title") or video_id or "video")
        user = video_info.get("user") if isinstance(video_info.get("user"), dict) else {}
        author = None
        if isinstance(user, dict):
            author = str(user.get("name") or user.get("username") or "") or None
        tags: list[str] = []
        raw_tags = video_info.get("tags") or []
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                if isinstance(tag, dict):
                    label = tag.get("id") or tag.get("name") or tag.get("label")
                    if label:
                        tags.append(str(label))
                elif tag:
                    tags.append(str(tag))
        thumb = None
        file_obj = video_info.get("file") if isinstance(video_info.get("file"), dict) else {}
        if isinstance(file_obj, dict):
            thumb = file_obj.get("id")
        thumbnail_url = None
        if thumb:
            thumbnail_url = f"https://i.iwara.tv/image/thumbnail/{thumb}/thumbnail-00.jpg"
        created = video_info.get("createdAt") or video_info.get("updatedAt")
        duration = video_info.get("file", {}).get("duration") if isinstance(
            video_info.get("file"), dict
        ) else video_info.get("duration")
        return item_metadata(
            site=self.site,
            item_id=video_id or None,
            canonical_url=f"{iwara_origin}/video/{video_id}" if video_id else None,
            title=title,
            author=author,
            upload_date=str(created) if created else None,
            tags=tags,
            description=str(video_info.get("body") or "") or None,
            duration=float(duration) if duration is not None else None,
            resolution=quality,
            thumbnail_url=thumbnail_url,
            view_count=_int_or_none(video_info.get("numViews")),
            like_count=_int_or_none(video_info.get("numLikes")),
            collection=collection,
            collection_id=collection_id,
            extra={"rating": video_info.get("rating"), "private": video_info.get("private")},
        )

    def _file_from_video_info(
        self,
        video_info: dict[str, Any],
        *,
        collection: str | None = None,
        collection_id: str | None = None,
    ) -> extracted_file:
        video_id = str(video_info.get("id") or video_info.get("video_id") or "video")
        title = str(video_info.get("title") or video_id)
        unavailable = self._unavailable_reason(video_info)
        if unavailable:
            raise RuntimeError(unavailable)
        file_url = str(video_info.get("fileUrl") or "")
        sources = self._fetch_file_sources(file_url)
        dl_url, quality = self._pick_download_url(sources, self.preferred_quality)
        name = sanitize_filename(f"{title}_{video_id}.mp4")
        return extracted_file(
            url=dl_url,
            name=name,
            relative_dir="",
            headers=self._download_headers(),
            meta={"video_id": video_id, "quality": quality, "title": title},
            metadata=self._metadata_from_video_info(
                video_info,
                quality=quality,
                collection=collection,
                collection_id=collection_id,
            ),
        )

    @staticmethod
    def _unavailable_reason(video_info: dict[str, Any]) -> str | None:
        file_url = str(video_info.get("fileUrl") or "")
        if file_url:
            return None
        embed = str(video_info.get("embedUrl") or "")
        message = str(video_info.get("message") or "")
        private = bool(video_info.get("private"))
        if "youtube" in embed or "youtu.be" in embed:
            return f"external embed cannot be downloaded: {embed}"
        if message == "errors.privateVideo" or private:
            return "private video — login token required (optional token stub only)"
        return "API did not return fileUrl (hidden processing or no permission)"

    def _stub_id(self, item: dict[str, Any]) -> str | None:
        vid = item.get("id") or item.get("video_id")
        return str(vid) if vid else None

    # ── resolve entrypoints ──────────────────────────────────────────────────

    def _resolve_video(self, video_id: str) -> extract_result:
        info = self._get_video_info(video_id)
        file = self._file_from_video_info(info)
        title = str(info.get("title") or video_id)
        return extract_result(
            site=self.site,
            title=title,
            files=[file],
            kind="video",
            meta={"video_id": video_id, "quality": file.meta.get("quality")},
            metadata=file.metadata,
        )

    def _resolve_playlist(self, playlist_id: str) -> extract_result:
        title, stubs = self._list_playlist_videos(playlist_id)
        files = self._files_from_stubs(
            stubs,
            relative_dir=sanitize_filename(title),
            collection=title,
            collection_id=playlist_id,
        )
        if not files:
            raise RuntimeError(f"playlist {playlist_id} has no downloadable videos")
        return extract_result(
            site=self.site,
            title=title,
            files=files,
            kind="playlist",
            meta={"playlist_id": playlist_id},
            metadata=item_metadata(
                site=self.site,
                item_id=playlist_id,
                canonical_url=f"{iwara_origin}/playlist/{playlist_id}",
                title=title,
                collection=title,
                collection_id=playlist_id,
                extra={"file_count": len(files)},
            ),
        )

    def _resolve_user(self, username: str) -> extract_result:
        user_id = self._get_user_id(username)
        stubs = self._list_user_videos(user_id)
        folder = sanitize_filename(username)
        files = self._files_from_stubs(
            stubs,
            relative_dir=folder,
            collection=username,
            collection_id=user_id,
        )
        if not files:
            raise RuntimeError(f"user {username} has no downloadable public videos")
        return extract_result(
            site=self.site,
            title=username,
            files=files,
            kind="profile",
            meta={"username": username, "user_id": user_id},
            metadata=item_metadata(
                site=self.site,
                item_id=user_id,
                canonical_url=f"{iwara_origin}/profile/{username}",
                title=username,
                author=username,
                collection=username,
                collection_id=user_id,
                extra={"file_count": len(files)},
            ),
        )

    def _files_from_stubs(
        self,
        stubs: list[dict[str, Any]],
        *,
        relative_dir: str,
        collection: str | None = None,
        collection_id: str | None = None,
    ) -> list[extracted_file]:
        files: list[extracted_file] = []
        errors: list[str] = []
        for stub in stubs:
            vid = self._stub_id(stub)
            if not vid:
                continue
            try:
                info = self._get_video_info(vid)
                extracted = self._file_from_video_info(
                    info,
                    collection=collection,
                    collection_id=collection_id,
                )
                extracted.relative_dir = relative_dir
                files.append(extracted)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{vid}: {exc}")
                continue
        if not files and errors:
            raise RuntimeError(
                "no downloadable videos; first errors: " + "; ".join(errors[:3])
            )
        return files


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
