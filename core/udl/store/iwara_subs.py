"""Iwara local subscriptions and custom home sections in SQLite.

Local author/playlist subscriptions plus optional import of account
following (see ``import_following_authors``).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from udl.extractors.iwara import iwara_origin
from udl.extractors.iwara_browse import annotate_local_status, iwara_browser
from udl.extractors.iwara_filters import apply_filters, browse_filters, filters_from_args
from udl.store.queue import queue_job, save_queue_job


@dataclass
class subscription:
    id: int
    kind: str
    key: str
    title: str
    remote_id: str | None = None
    enabled: bool = True
    last_checked_at: str | None = None
    last_seen_video_id: str | None = None
    filter_json: dict[str, Any] = field(default_factory=dict)
    new_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key,
            "title": self.title,
            "remote_id": self.remote_id,
            "enabled": self.enabled,
            "last_checked_at": self.last_checked_at,
            "last_seen_video_id": self.last_seen_video_id,
            "filters": self.filter_json,
            "new_count": self.new_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_url": (
                f"{iwara_origin}/profile/{self.key}"
                if self.kind == "author"
                else f"{iwara_origin}/playlist/{self.key}"
            ),
        }


@dataclass
class home_section:
    id: int
    name: str
    query_kind: str
    query_value: str
    sort: str = "date"
    rating: str | None = None
    enabled: bool = True
    position: int = 0
    filter_json: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "query_kind": self.query_kind,
            "query_value": self.query_value,
            "sort": self.sort,
            "rating": self.rating,
            "enabled": self.enabled,
            "position": self.position,
            "filters": self.filter_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_sub(row: sqlite3.Row) -> subscription:
    raw = row["filter_json"] or "{}"
    try:
        filters = json.loads(raw)
    except json.JSONDecodeError:
        filters = {}
    return subscription(
        id=int(row["id"]),
        kind=row["kind"],
        key=row["key"],
        title=row["title"] or row["key"],
        remote_id=row["remote_id"],
        enabled=bool(row["enabled"]),
        last_checked_at=row["last_checked_at"],
        last_seen_video_id=row["last_seen_video_id"],
        filter_json=filters if isinstance(filters, dict) else {},
        new_count=int(row["new_count"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_section(row: sqlite3.Row) -> home_section:
    raw = row["filter_json"] or "{}"
    try:
        filters = json.loads(raw)
    except json.JSONDecodeError:
        filters = {}
    return home_section(
        id=int(row["id"]),
        name=row["name"],
        query_kind=row["query_kind"],
        query_value=row["query_value"] or "",
        sort=row["sort"] or "date",
        rating=row["rating"],
        enabled=bool(row["enabled"]),
        position=int(row["position"] or 0),
        filter_json=filters if isinstance(filters, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def add_subscription(
    conn: sqlite3.Connection,
    *,
    kind: str,
    key: str,
    title: str | None = None,
    remote_id: str | None = None,
    filters: dict[str, Any] | None = None,
) -> subscription:
    kind = kind.strip().lower()
    if kind in {"user", "profile"}:
        kind = "author"
    if kind not in {"author", "playlist"}:
        raise ValueError(f"unsupported subscription kind: {kind}")
    key = key.strip().lstrip("@").strip("/")
    if not key:
        raise ValueError("empty subscription key")
    title = (title or key).strip() or key
    conn.execute(
        """
        insert into iwara_subscriptions (
          kind, key, title, remote_id, enabled, filter_json, updated_at
        ) values (?, ?, ?, ?, 1, ?, datetime('now'))
        on conflict(kind, key) do update set
          title=excluded.title,
          remote_id=coalesce(excluded.remote_id, iwara_subscriptions.remote_id),
          enabled=1,
          filter_json=coalesce(excluded.filter_json, iwara_subscriptions.filter_json),
          updated_at=datetime('now')
        """,
        (
            kind,
            key,
            title,
            remote_id,
            json.dumps(filters or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    row = conn.execute(
        "select * from iwara_subscriptions where kind = ? and key = ?",
        (kind, key),
    ).fetchone()
    return _row_sub(row)


def list_subscriptions(
    conn: sqlite3.Connection,
    *,
    enabled_only: bool = False,
) -> list[subscription]:
    sql = "select * from iwara_subscriptions"
    if enabled_only:
        sql += " where enabled = 1"
    sql += " order by updated_at desc, id desc"
    return [_row_sub(row) for row in conn.execute(sql).fetchall()]


def import_following_authors(
    conn: sqlite3.Connection,
    *,
    use_cloudflare_bypass: bool = False,
    max_pages: int = 100,
) -> dict[str, Any]:
    """import the logged-in account's following list into local subscriptions."""
    from udl.extractors.iwara_auth import (
        iwara_auth_error,
        list_following,
        normalize_following_entry,
        whoami,
    )

    account = whoami(use_cloudflare_bypass=use_cloudflare_bypass)
    if not account.logged_in or not account.user_id:
        raise iwara_auth_error("Login required to import following")

    rows = list_following(
        account.user_id,
        max_pages=max_pages,
        use_cloudflare_bypass=use_cloudflare_bypass,
    )
    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        entry = normalize_following_entry(row)
        if entry is None:
            skipped += 1
            continue
        existing = conn.execute(
            "select id from iwara_subscriptions where kind = ? and key = ?",
            ("author", entry["username"]),
        ).fetchone()
        sub = add_subscription(
            conn,
            kind="author",
            key=entry["username"],
            title=entry["name"],
            remote_id=entry["user_id"] or None,
        )
        payload = sub.to_dict()
        if existing is None:
            added.append(payload)
        else:
            updated.append(payload)
    return {
        "account": account.to_dict(),
        "fetched": len(rows),
        "added": len(added),
        "updated": len(updated),
        "skipped": skipped,
        "subscriptions_added": added,
        "subscriptions_updated": updated,
    }


def remove_subscription(conn: sqlite3.Connection, sub_id: int) -> bool:
    cur = conn.execute("delete from iwara_subscriptions where id = ?", (sub_id,))
    conn.execute("delete from iwara_subscription_items where source_id = ?", (sub_id,))
    conn.commit()
    return cur.rowcount > 0


def set_subscription_enabled(
    conn: sqlite3.Connection,
    sub_id: int,
    enabled: bool,
) -> subscription | None:
    conn.execute(
        """
        update iwara_subscriptions
        set enabled = ?, updated_at = datetime('now')
        where id = ?
        """,
        (1 if enabled else 0, sub_id),
    )
    conn.commit()
    row = conn.execute(
        "select * from iwara_subscriptions where id = ?", (sub_id,)
    ).fetchone()
    return _row_sub(row) if row else None


def list_new_items(
    conn: sqlite3.Connection,
    *,
    source_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["seen = 0"]
    params: list[Any] = []
    if source_id is not None:
        clauses.append("source_id = ?")
        params.append(source_id)
    where = " and ".join(clauses)
    rows = conn.execute(
        f"""
        select * from iwara_subscription_items
        where {where}
        order by discovered_at desc
        limit ?
        """,
        [*params, max(1, min(limit, 500))],
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        item = {
            "id": row["id"],
            "source_id": row["source_id"],
            "video_id": row["video_id"],
            "title": row["title"],
            "discovered_at": row["discovered_at"],
            "seen": bool(row["seen"]),
            "enqueued": bool(row["enqueued"]),
            **(payload if isinstance(payload, dict) else {}),
        }
        items.append(item)
    return annotate_local_status(conn, items)


def mark_items_seen(
    conn: sqlite3.Connection,
    *,
    source_id: int | None = None,
    video_ids: list[str] | None = None,
) -> int:
    if video_ids:
        placeholders = ",".join("?" for _ in video_ids)
        cur = conn.execute(
            f"""
            update iwara_subscription_items
            set seen = 1
            where video_id in ({placeholders})
            """,
            video_ids,
        )
    elif source_id is not None:
        cur = conn.execute(
            "update iwara_subscription_items set seen = 1 where source_id = ?",
            (source_id,),
        )
    else:
        cur = conn.execute("update iwara_subscription_items set seen = 1 where seen = 0")
    # refresh new_count
    if source_id is not None:
        conn.execute(
            """
            update iwara_subscriptions
            set new_count = (
              select count(*) from iwara_subscription_items
              where source_id = ? and seen = 0
            ),
            updated_at = datetime('now')
            where id = ?
            """,
            (source_id, source_id),
        )
    else:
        conn.execute(
            """
            update iwara_subscriptions
            set new_count = (
              select count(*) from iwara_subscription_items
              where source_id = iwara_subscriptions.id and seen = 0
            ),
            updated_at = datetime('now')
            """
        )
    conn.commit()
    return cur.rowcount


def _filters_from_stored(raw: dict[str, Any] | None) -> browse_filters | None:
    if not raw:
        return None
    return filters_from_args(
        min_likes=raw.get("min_likes"),
        max_likes=raw.get("max_likes"),
        min_views=raw.get("min_views"),
        max_views=raw.get("max_views"),
        date_from=raw.get("date_from"),
        date_to=raw.get("date_to"),
        include_tags=",".join(raw.get("include_tags") or [])
        if isinstance(raw.get("include_tags"), list)
        else raw.get("include_tags"),
        exclude_tags=",".join(raw.get("exclude_tags") or [])
        if isinstance(raw.get("exclude_tags"), list)
        else raw.get("exclude_tags"),
        title=",".join(raw.get("title_keywords") or [])
        if isinstance(raw.get("title_keywords"), list)
        else raw.get("title") or raw.get("title_keywords"),
        title_mode=str(raw.get("title_mode") or "all"),
        rating=raw.get("rating"),
    )


def sync_subscription(
    conn: sqlite3.Connection,
    browser: iwara_browser,
    sub: subscription,
    *,
    output_dir: str,
    enqueue: bool = True,
    max_pages: int = 5,
    cloudflare_bypass: bool = False,
    quality: str | None = None,
) -> dict[str, Any]:
    """fetch new videos since last_seen and optionally enqueue downloads."""
    filters = _filters_from_stored(sub.filter_json)
    new_items: list[dict[str, Any]] = []
    stop_id = (sub.last_seen_video_id or "").strip()
    newest_id: str | None = None
    error: str | None = None

    for page in range(max(1, max_pages)):
        if sub.kind == "author":
            result = browser.author(sub.key, page=page, sort="date")
        else:
            result = browser.playlist(sub.key, page=page)
        if result.error:
            error = result.error
            break
        page_items = apply_filters(result.items, filters) if filters else list(result.items)
        if not result.items:
            break
        hit_stop = False
        for item in page_items:
            vid = str(item.get("video_id") or "")
            if not vid:
                continue
            if newest_id is None:
                newest_id = vid
            if stop_id and vid == stop_id:
                hit_stop = True
                break
            # skip already known items
            exists = conn.execute(
                """
                select 1 from iwara_subscription_items
                where source_id = ? and video_id = ?
                """,
                (sub.id, vid),
            ).fetchone()
            if exists:
                continue
            new_items.append(item)
        if hit_stop or not result.has_more:
            break

    annotate_local_status(conn, new_items)
    enqueued = 0
    for item in new_items:
        vid = str(item.get("video_id") or "")
        conn.execute(
            """
            insert or ignore into iwara_subscription_items (
              source_id, video_id, title, payload_json, seen, enqueued
            ) values (?, ?, ?, ?, 0, 0)
            """,
            (
                sub.id,
                vid,
                item.get("title"),
                json.dumps(item, ensure_ascii=False),
            ),
        )
        if enqueue and item.get("local_status") != "downloaded":
            job_id = str(uuid.uuid4())
            url = str(item.get("canonical_url") or f"{iwara_origin}/video/{vid}")
            save_queue_job(
                conn,
                queue_job(
                    id=job_id,
                    url=url,
                    output_dir=output_dir,
                    status="queued",
                    site="iwara",
                    title=str(item.get("title") or vid),
                    quality=quality,
                    cloudflare_bypass=cloudflare_bypass,
                    metadata={
                        "item_id": vid,
                        "site": "iwara",
                        "title": item.get("title"),
                        "author": item.get("author"),
                        "canonical_url": url,
                        "thumbnail_url": item.get("thumbnail_url"),
                    },
                    options={"source": "iwara_subscription", "subscription_id": sub.id},
                ),
            )
            conn.execute(
                """
                update iwara_subscription_items
                set enqueued = 1 where source_id = ? and video_id = ?
                """,
                (sub.id, vid),
            )
            enqueued += 1
            item["local_status"] = "queued"

    conn.execute(
        """
        update iwara_subscriptions
        set last_checked_at = datetime('now'),
            last_seen_video_id = coalesce(?, last_seen_video_id),
            new_count = (
              select count(*) from iwara_subscription_items
              where source_id = ? and seen = 0
            ),
            updated_at = datetime('now')
        where id = ?
        """,
        (newest_id, sub.id, sub.id),
    )
    conn.commit()
    return {
        "subscription_id": sub.id,
        "kind": sub.kind,
        "key": sub.key,
        "new_count": len(new_items),
        "enqueued": enqueued,
        "newest_video_id": newest_id,
        "error": error,
        "items": new_items,
    }


def sync_all_subscriptions(
    conn: sqlite3.Connection,
    browser: iwara_browser,
    *,
    output_dir: str,
    enqueue: bool = True,
    max_pages: int = 5,
    cloudflare_bypass: bool = False,
    quality: str | None = None,
) -> dict[str, Any]:
    subs = list_subscriptions(conn, enabled_only=True)
    results = []
    for sub in subs:
        results.append(
            sync_subscription(
                conn,
                browser,
                sub,
                output_dir=output_dir,
                enqueue=enqueue,
                max_pages=max_pages,
                cloudflare_bypass=cloudflare_bypass,
                quality=quality,
            )
        )
    return {
        "subscriptions": len(subs),
        "new_total": sum(int(r.get("new_count") or 0) for r in results),
        "enqueued_total": sum(int(r.get("enqueued") or 0) for r in results),
        "results": results,
    }


def enqueue_videos(
    conn: sqlite3.Connection,
    items: list[dict[str, Any]],
    *,
    output_dir: str,
    cloudflare_bypass: bool = False,
    quality: str | None = None,
    skip_downloaded: bool = True,
) -> dict[str, Any]:
    annotate_local_status(conn, items)
    enqueued = 0
    skipped = 0
    jobs: list[dict[str, Any]] = []
    for item in items:
        vid = str(item.get("video_id") or item.get("item_id") or "")
        if not vid:
            continue
        if skip_downloaded and item.get("local_status") == "downloaded":
            skipped += 1
            continue
        if item.get("local_status") == "queued":
            skipped += 1
            continue
        job_id = str(uuid.uuid4())
        url = str(item.get("canonical_url") or f"{iwara_origin}/video/{vid}")
        job = queue_job(
            id=job_id,
            url=url,
            output_dir=output_dir,
            status="queued",
            site="iwara",
            title=str(item.get("title") or vid),
            quality=quality,
            cloudflare_bypass=cloudflare_bypass,
            metadata={
                "item_id": vid,
                "site": "iwara",
                "title": item.get("title"),
                "author": item.get("author"),
                "canonical_url": url,
                "thumbnail_url": item.get("thumbnail_url"),
            },
            options={"source": "iwara_browse"},
        )
        save_queue_job(conn, job)
        enqueued += 1
        item["local_status"] = "queued"
        jobs.append(job.to_dict())
    return {"enqueued": enqueued, "skipped": skipped, "jobs": jobs}


# ── custom home sections ─────────────────────────────────────────────────────


def add_home_section(
    conn: sqlite3.Connection,
    *,
    name: str,
    query_kind: str,
    query_value: str = "",
    sort: str = "date",
    rating: str | None = None,
    filters: dict[str, Any] | None = None,
    position: int | None = None,
) -> home_section:
    kind = query_kind.strip().lower()
    if kind not in {"home", "search", "tag", "author", "playlist"}:
        raise ValueError(f"unsupported home section kind: {kind}")
    name = (name or "").strip() or f"{kind}:{query_value or sort}"
    if position is None:
        row = conn.execute(
            "select coalesce(max(position), -1) + 1 from iwara_home_sections"
        ).fetchone()
        position = int(row[0] if row else 0)
    cur = conn.execute(
        """
        insert into iwara_home_sections (
          name, query_kind, query_value, sort, rating, enabled, position, filter_json
        ) values (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            name,
            kind,
            query_value or "",
            sort or "date",
            rating,
            position,
            json.dumps(filters or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    row = conn.execute(
        "select * from iwara_home_sections where id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _row_section(row)


def list_home_sections(
    conn: sqlite3.Connection,
    *,
    enabled_only: bool = False,
) -> list[home_section]:
    sql = "select * from iwara_home_sections"
    if enabled_only:
        sql += " where enabled = 1"
    sql += " order by position asc, id asc"
    return [_row_section(row) for row in conn.execute(sql).fetchall()]


def remove_home_section(conn: sqlite3.Connection, section_id: int) -> bool:
    cur = conn.execute("delete from iwara_home_sections where id = ?", (section_id,))
    conn.commit()
    return cur.rowcount > 0


def fetch_home_section(
    browser: iwara_browser,
    section: home_section,
    *,
    page: int = 0,
    limit: int = 32,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    filters = _filters_from_stored(section.filter_json)
    rating = section.rating
    kind = section.query_kind
    if kind == "home":
        result = browser.home(
            sort=section.sort,
            page=page,
            limit=limit,
            rating=rating,
            filters=filters,
        )
    elif kind == "search":
        result = browser.search(
            section.query_value,
            page=page,
            limit=limit,
            sort=(
                section.sort
                if section.sort in {"relevance", "date", "views", "likes"}
                else "relevance"
            ),
            rating=rating,
            filters=filters,
        )
    elif kind == "tag":
        result = browser.tag(
            section.query_value,
            page=page,
            limit=limit,
            sort=section.sort,
            rating=rating,
            filters=filters,
        )
    elif kind == "author":
        result = browser.author(
            section.query_value,
            page=page,
            limit=limit,
            sort=section.sort,
            rating=rating,
            filters=filters,
        )
    elif kind == "playlist":
        result = browser.playlist(
            section.query_value,
            page=page,
            limit=limit,
            filters=filters,
        )
    else:
        return {"section": section.to_dict(), "error": f"unknown kind {kind}"}
    payload = result.to_dict()
    if conn is not None and payload.get("items"):
        annotate_local_status(conn, payload["items"])
    payload["section"] = section.to_dict()
    return payload
