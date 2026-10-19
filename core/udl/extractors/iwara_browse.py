"""Iwara browse / search / home listing helpers.

Ported patterns from Moeary/IwaraTool
https://github.com/Moeary/IwaraTool
commit 1e98c96c422a169699c0d16bcab49b8a7f5238d5
License: MIT

Uses the same HTTP session as ``iwara_extractor``. When an account session
exists in ``udl.secrets``, browse/search results reflect that account
(including visibility of private／friends-only items the account can access).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from udl.extractors.iwara import (
    iwara_api,
    iwara_extractor,
    iwara_origin,
    iwara_page_limit,
)
from udl.extractors.iwara_filters import apply_filters, browse_filters

# listing sorts accepted by GET /videos
iwara_list_sorts = ("date", "trending", "popularity", "views", "likes")
# text search sorts by type (users/playlists reject views/likes)
iwara_search_sorts: dict[str, tuple[str, ...]] = {
    "videos": ("relevance", "date", "views", "likes"),
    "users": ("relevance", "date"),
    "playlists": ("relevance", "date"),
}
iwara_ratings = ("all", "ecchi", "general")


@dataclass
class browse_page:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None
    has_more: bool = False
    page: int = 0
    limit: int = iwara_page_limit
    sort: str | None = None
    rating: str | None = None
    query: str | None = None
    error: str | None = None
    kind: str = "videos"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "items": self.items,
            "total": self.total,
            "has_more": self.has_more,
            "page": self.page,
            "limit": self.limit,
            "sort": self.sort,
            "rating": self.rating,
            "query": self.query,
            "error": self.error,
            "count": len(self.items),
        }


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _thumbnail_url(stub: dict[str, Any]) -> str | None:
    for key in ("customThumbnail", "thumbnailUrl", "thumbnail_url"):
        val = stub.get(key)
        if isinstance(val, str) and val.startswith(("http://", "https://")):
            return val
        if isinstance(val, dict):
            vid = val.get("id")
            if vid:
                return f"https://i.iwara.tv/image/thumbnail/{quote(str(vid))}/{quote(str(vid))}.jpg"
    file_obj = stub.get("file") if isinstance(stub.get("file"), dict) else {}
    file_id = file_obj.get("id") if isinstance(file_obj, dict) else None
    if not file_id:
        return None
    index = max(0, _as_int(stub.get("thumbnail"), 0))
    return f"https://i.iwara.tv/image/thumbnail/{quote(str(file_id))}/thumbnail-{index:02d}.jpg"


def _tags_from_stub(stub: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    raw = stub.get("tags") or []
    if isinstance(raw, list):
        for tag in raw:
            if isinstance(tag, dict):
                label = tag.get("id") or tag.get("name") or tag.get("label")
                if label:
                    tags.append(str(label))
            elif tag:
                tags.append(str(tag))
    return tags


def normalize_video_stub(stub: dict[str, Any]) -> dict[str, Any] | None:
    video_id = stub.get("id") or stub.get("video_id")
    if not video_id:
        return None
    video_id = str(video_id)
    user = stub.get("user") if isinstance(stub.get("user"), dict) else {}
    author = ""
    username = ""
    user_id = ""
    if isinstance(user, dict):
        author = str(user.get("name") or user.get("username") or "")
        username = str(user.get("username") or "")
        user_id = str(user.get("id") or "")
    title = str(stub.get("title") or video_id)
    return {
        "video_id": video_id,
        "item_id": video_id,
        "title": title,
        "author": author or username or None,
        "author_username": username or None,
        "author_id": user_id or None,
        "upload_date": stub.get("createdAt") or stub.get("updatedAt"),
        "like_count": _as_int(stub.get("numLikes", stub.get("likes", 0))),
        "view_count": _as_int(stub.get("numViews", stub.get("views", 0))),
        "duration": (
            float(stub.get("file", {}).get("duration"))
            if isinstance(stub.get("file"), dict)
            and stub.get("file", {}).get("duration") is not None
            else None
        ),
        "rating": stub.get("rating"),
        "tags": _tags_from_stub(stub),
        "thumbnail_url": _thumbnail_url(stub),
        "canonical_url": f"{iwara_origin}/video/{video_id}",
        "site": "iwara",
        "local_status": "not_downloaded",
    }


def normalize_user_stub(stub: dict[str, Any]) -> dict[str, Any] | None:
    user = stub.get("user") if isinstance(stub.get("user"), dict) else stub
    if not isinstance(user, dict):
        return None
    user_id = user.get("id")
    username = str(user.get("username") or "")
    if not user_id and not username:
        return None
    return {
        "user_id": str(user_id) if user_id else None,
        "username": username,
        "name": str(user.get("name") or username),
        "canonical_url": f"{iwara_origin}/profile/{username}" if username else None,
        "site": "iwara",
        "kind": "user",
    }


def normalize_playlist_stub(stub: dict[str, Any]) -> dict[str, Any] | None:
    playlist_id = stub.get("id") or stub.get("playlist_id")
    if not playlist_id:
        return None
    playlist_id = str(playlist_id)
    return {
        "playlist_id": playlist_id,
        "title": str(stub.get("title") or stub.get("name") or playlist_id),
        "video_count": _as_int(stub.get("numVideos", stub.get("videoCount", 0))),
        "canonical_url": f"{iwara_origin}/playlist/{playlist_id}",
        "site": "iwara",
        "kind": "playlist",
    }


class iwara_browser:
    """public browse client; reuses iwara_extractor HTTP session."""

    def __init__(
        self,
        *,
        token: str | None = None,
        use_cloudflare_bypass: bool = False,
        extractor: iwara_extractor | None = None,
    ) -> None:
        self._ext = extractor or iwara_extractor(
            token=token,
            use_cloudflare_bypass=use_cloudflare_bypass,
        )

    @property
    def extractor(self) -> iwara_extractor:
        return self._ext

    def close(self) -> None:
        close = getattr(self._ext, "close", None)
        if callable(close):
            close()
        session = getattr(self._ext, "_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    def _page_params(
        self,
        base: dict[str, Any],
        *,
        page: int,
        limit: int,
        sort: str | None,
        rating: str | None,
    ) -> dict[str, Any]:
        params = {k: v for k, v in base.items() if v is not None and str(v).strip() != ""}
        params["page"] = max(0, int(page))
        params["limit"] = max(1, min(100, int(limit)))
        if sort:
            params["sort"] = sort
        if rating and rating not in {"all", "*"}:
            params["rating"] = rating
        return params

    def _parse_page(
        self,
        data: Any,
        *,
        page: int,
        limit: int,
        endpoint: str,
        kind: str,
        sort: str | None,
        rating: str | None,
        query: str | None,
        normalize,
    ) -> browse_page:
        if not isinstance(data, dict):
            return browse_page(
                page=page,
                limit=limit,
                sort=sort,
                rating=rating,
                query=query,
                kind=kind,
                error=f"unexpected response type: {type(data).__name__}",
            )
        results = data.get("results", data.get("data"))
        if isinstance(results, dict):
            results = results.get("results") or results.get("items") or []
        if not isinstance(results, list):
            return browse_page(
                page=page,
                limit=limit,
                sort=sort,
                rating=rating,
                query=query,
                kind=kind,
                error="missing results list",
            )
        items: list[dict[str, Any]] = []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            normalized = normalize(raw)
            if normalized:
                items.append(normalized)
        total_raw = data.get("count", data.get("total", data.get("totalCount")))
        try:
            total = int(total_raw) if total_raw is not None else None
        except (TypeError, ValueError):
            total = None
        # iwara count on /videos is often a next-page sentinel
        count_is_sentinel = (
            endpoint in {"/videos", "/images"}
            and total is not None
            and len(items) >= limit
            and total == (page + 1) * limit + 1
        )
        if count_is_sentinel:
            has_more = True
            total = None
        elif total is not None:
            has_more = (page + 1) * limit < total
        else:
            has_more = len(items) >= limit
        if not items:
            has_more = False
        return browse_page(
            items=items,
            total=total,
            has_more=has_more,
            page=page,
            limit=limit,
            sort=sort,
            rating=rating,
            query=query,
            kind=kind,
        )

    def search(
        self,
        query: str,
        *,
        search_type: str = "videos",
        page: int = 0,
        limit: int = iwara_page_limit,
        sort: str = "relevance",
        rating: str | None = None,
        filters: browse_filters | None = None,
    ) -> browse_page:
        stype = (search_type or "videos").strip().lower()
        if stype in {"author", "authors", "user"}:
            stype = "users"
        if stype not in iwara_search_sorts:
            return browse_page(error=f"unsupported search type: {stype}", kind=stype)
        q = (query or "").strip()
        if not q:
            return browse_page(error="empty search query", kind=stype)
        allowed = iwara_search_sorts[stype]
        sort_key = (sort or "relevance").strip().lower()
        if sort_key not in allowed:
            sort_key = "relevance"
        params = self._page_params(
            {"type": stype, "query": q},
            page=page,
            limit=limit,
            sort=sort_key,
            rating=rating if stype == "videos" else None,
        )
        try:
            data = self._ext._get_json(f"{iwara_api}/search", params=params)
        except Exception as exc:  # noqa: BLE001
            return browse_page(
                page=page,
                limit=limit,
                sort=sort_key,
                rating=rating,
                query=q,
                kind=stype,
                error=str(exc),
            )
        if stype == "users":
            normalize = normalize_user_stub
        elif stype == "playlists":
            normalize = normalize_playlist_stub
        else:
            normalize = normalize_video_stub
        result = self._parse_page(
            data,
            page=page,
            limit=limit,
            endpoint="/search",
            kind=stype,
            sort=sort_key,
            rating=rating,
            query=q,
            normalize=normalize,
        )
        if stype == "videos" and filters is not None:
            result.items = apply_filters(result.items, filters)
        return result

    def list_videos(
        self,
        *,
        tags: str | None = None,
        user: str | None = None,
        page: int = 0,
        limit: int = iwara_page_limit,
        sort: str = "date",
        rating: str | None = None,
        filters: browse_filters | None = None,
    ) -> browse_page:
        sort_key = (sort or "date").strip().lower()
        if sort_key not in iwara_list_sorts:
            sort_key = "date"
        base: dict[str, Any] = {}
        if tags:
            base["tags"] = tags.strip().lstrip("#")
        if user:
            base["user"] = user
        params = self._page_params(
            base,
            page=page,
            limit=limit,
            sort=sort_key,
            rating=rating,
        )
        try:
            data = self._ext._get_json(f"{iwara_api}/videos", params=params)
        except Exception as exc:  # noqa: BLE001
            return browse_page(
                page=page,
                limit=limit,
                sort=sort_key,
                rating=rating,
                query=tags or user,
                kind="videos",
                error=str(exc),
            )
        result = self._parse_page(
            data,
            page=page,
            limit=limit,
            endpoint="/videos",
            kind="videos",
            sort=sort_key,
            rating=rating,
            query=tags or user,
            normalize=normalize_video_stub,
        )
        if filters is not None:
            result.items = apply_filters(result.items, filters)
        return result

    def tag(
        self,
        tag: str,
        *,
        page: int = 0,
        limit: int = iwara_page_limit,
        sort: str = "date",
        rating: str | None = None,
        filters: browse_filters | None = None,
    ) -> browse_page:
        return self.list_videos(
            tags=tag,
            page=page,
            limit=limit,
            sort=sort,
            rating=rating,
            filters=filters,
        )

    def author(
        self,
        username: str,
        *,
        page: int = 0,
        limit: int = iwara_page_limit,
        sort: str = "date",
        rating: str | None = None,
        filters: browse_filters | None = None,
    ) -> browse_page:
        username = (username or "").strip().lstrip("@")
        if not username:
            return browse_page(error="empty author username", kind="videos")
        try:
            user_id = self._ext._get_user_id(username)
        except Exception as exc:  # noqa: BLE001
            return browse_page(error=str(exc), kind="videos", query=username)
        result = self.list_videos(
            user=user_id,
            page=page,
            limit=limit,
            sort=sort,
            rating=rating,
            filters=filters,
        )
        result.query = username
        return result

    def playlist(
        self,
        playlist_id: str,
        *,
        page: int = 0,
        limit: int = iwara_page_limit,
        filters: browse_filters | None = None,
    ) -> browse_page:
        playlist_id = (playlist_id or "").strip().strip("/")
        if not playlist_id:
            return browse_page(error="empty playlist id", kind="videos")
        params = {"page": max(0, int(page)), "limit": max(1, min(100, int(limit)))}
        try:
            data = self._ext._get_json(f"{iwara_api}/playlist/{playlist_id}", params=params)
        except Exception as exc:  # noqa: BLE001
            return browse_page(
                page=page,
                limit=limit,
                query=playlist_id,
                kind="videos",
                error=str(exc),
            )
        title = None
        if isinstance(data, dict):
            title = data.get("title") or data.get("name")
        result = self._parse_page(
            data if isinstance(data, dict) else {},
            page=page,
            limit=limit,
            endpoint="/playlist",
            kind="videos",
            sort=None,
            rating=None,
            query=str(title or playlist_id),
            normalize=normalize_video_stub,
        )
        if filters is not None:
            result.items = apply_filters(result.items, filters)
        return result

    def home(
        self,
        *,
        sort: str = "date",
        page: int = 0,
        limit: int = iwara_page_limit,
        rating: str | None = None,
        filters: browse_filters | None = None,
    ) -> browse_page:
        """home / recommendation list via /videos with sort."""
        return self.list_videos(
            page=page,
            limit=limit,
            sort=sort,
            rating=rating,
            filters=filters,
        )

    def preview_stream(self, video_id: str) -> dict[str, Any]:
        """resolve an HTML5-playable stream URL when the API returns file sources."""
        video_id = (video_id or "").strip()
        if not video_id:
            return {"ok": False, "error": "empty video id"}
        try:
            info = self._ext._get_video_info(video_id)
            unavailable = self._ext._unavailable_reason(info)
            if unavailable:
                return {"ok": False, "error": unavailable, "video_id": video_id}
            file_url = str(info.get("fileUrl") or "")
            sources = self._ext._fetch_file_sources(file_url)
            stream_url, quality = self._pick_stream_url(sources)
            title = str(info.get("title") or video_id)
            return {
                "ok": True,
                "video_id": video_id,
                "title": title,
                "stream_url": stream_url,
                "quality": quality,
                "canonical_url": f"{iwara_origin}/video/{video_id}",
                "headers": self._ext._download_headers(),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "video_id": video_id}

    def _pick_stream_url(
        self,
        sources: list[dict[str, Any]],
    ) -> tuple[str, str]:
        sources_map = {
            str(s.get("name", "")).lower(): s for s in sources if isinstance(s, dict)
        }
        for quality in ("Source", "540", "360"):
            entry = sources_map.get(quality.lower())
            if not entry:
                continue
            src = entry.get("src") if isinstance(entry.get("src"), dict) else {}
            raw = ""
            if isinstance(src, dict):
                raw = str(src.get("view") or src.get("download") or "")
            if not raw:
                continue
            url = f"https:{raw}" if raw.startswith("//") else raw
            return url, str(entry.get("name") or quality)
        raise RuntimeError("no streamable quality in file sources")


def annotate_local_status(
    conn: sqlite3.Connection,
    items: list[dict[str, Any]],
    *,
    site: str = "iwara",
) -> list[dict[str, Any]]:
    """mark each video item downloaded / queued / not_downloaded from SQLite."""
    video_ids = [
        str(item.get("video_id") or item.get("item_id") or "")
        for item in items
        if item.get("video_id") or item.get("item_id")
    ]
    video_ids = [vid for vid in video_ids if vid]
    if not video_ids:
        return items
    downloaded: set[str] = set()
    placeholders = ",".join("?" for _ in video_ids)
    rows = conn.execute(
        f"""
        select distinct item_id from history_jobs
        where site = ? and item_id in ({placeholders})
          and status in ('done', 'skipped')
        """,
        [site, *video_ids],
    ).fetchall()
    for row in rows:
        if row[0]:
            downloaded.add(str(row[0]))
    rows = conn.execute(
        f"""
        select distinct item_id from history_files
        where site = ? and item_id in ({placeholders})
          and status in ('done', 'skipped')
        """,
        [site, *video_ids],
    ).fetchall()
    for row in rows:
        if row[0]:
            downloaded.add(str(row[0]))

    queued: set[str] = set()
    qrows = conn.execute(
        """
        select url, metadata_json from queue_jobs
        where status in ('queued', 'running', 'paused')
        """
    ).fetchall()
    for row in qrows:
        url = str(row[0] or "")
        meta_raw = row[1] or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
        except json.JSONDecodeError:
            meta = {}
        mid = str(meta.get("item_id") or "") if isinstance(meta, dict) else ""
        if mid and mid in video_ids:
            queued.add(mid)
            continue
        for vid in video_ids:
            if f"/video/{vid}" in url:
                queued.add(vid)
                break

    for item in items:
        vid = str(item.get("video_id") or item.get("item_id") or "")
        if not vid:
            continue
        if vid in downloaded:
            item["local_status"] = "downloaded"
        elif vid in queued:
            item["local_status"] = "queued"
        else:
            item["local_status"] = "not_downloaded"
    return items
