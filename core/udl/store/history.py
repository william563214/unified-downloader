"""download history records in SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from udl.metadata import item_metadata
from udl.store.db import connect
from udl.store.hashutil import sha256_file


@dataclass
class history_entry:
    id: str
    site: str
    status: str
    title: str | None = None
    item_id: str | None = None
    canonical_url: str | None = None
    author: str | None = None
    upload_date: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    duration: float | None = None
    resolution: str | None = None
    thumbnail_url: str | None = None
    cover_url: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    collection: str | None = None
    collection_id: str | None = None
    extra: dict[str, Any] | None = None
    source_url: str | None = None
    output_dir: str | None = None
    ok: int = 0
    failed: int = 0
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None
    files: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "site": self.site,
            "status": self.status,
            "title": self.title,
            "item_id": self.item_id,
            "canonical_url": self.canonical_url,
            "author": self.author,
            "upload_date": self.upload_date,
            "tags": self.tags or [],
            "description": self.description,
            "duration": self.duration,
            "resolution": self.resolution,
            "thumbnail_url": self.thumbnail_url,
            "cover_url": self.cover_url,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "collection": self.collection,
            "collection_id": self.collection_id,
            "extra": self.extra or {},
            "source_url": self.source_url,
            "output_dir": self.output_dir,
            "ok": self.ok,
            "failed": self.failed,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "files": self.files or [],
        }


def _row_to_entry(row: sqlite3.Row, *, files: list[dict[str, Any]] | None = None) -> history_entry:
    tags = json.loads(row["tags_json"] or "[]")
    extra = json.loads(row["extra_json"] or "{}")
    return history_entry(
        id=row["id"],
        site=row["site"],
        status=row["status"],
        title=row["title"],
        item_id=row["item_id"],
        canonical_url=row["canonical_url"],
        author=row["author"],
        upload_date=row["upload_date"],
        tags=tags if isinstance(tags, list) else [],
        description=row["description"],
        duration=row["duration"],
        resolution=row["resolution"],
        thumbnail_url=row["thumbnail_url"],
        cover_url=row["cover_url"],
        view_count=row["view_count"],
        like_count=row["like_count"],
        collection=row["collection"],
        collection_id=row["collection_id"],
        extra=extra if isinstance(extra, dict) else {},
        source_url=row["source_url"],
        output_dir=row["output_dir"],
        ok=int(row["ok"] or 0),
        failed=int(row["failed"] or 0),
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
        files=files,
    )


def record_job_started(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    site: str,
    source_url: str,
    output_dir: str | Path,
    metadata: item_metadata | None = None,
    status: str = "running",
) -> None:
    meta = metadata or item_metadata(site=site)
    conn.execute(
        """
        insert into history_jobs (
          id, site, item_id, canonical_url, title, author, upload_date,
          tags_json, description, duration, resolution, thumbnail_url, cover_url,
          view_count, like_count, collection, collection_id, extra_json,
          source_url, output_dir, status, updated_at
        ) values (
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now')
        )
        on conflict(id) do update set
          site=excluded.site,
          item_id=coalesce(excluded.item_id, history_jobs.item_id),
          canonical_url=coalesce(excluded.canonical_url, history_jobs.canonical_url),
          title=coalesce(excluded.title, history_jobs.title),
          author=coalesce(excluded.author, history_jobs.author),
          upload_date=coalesce(excluded.upload_date, history_jobs.upload_date),
          tags_json=excluded.tags_json,
          description=coalesce(excluded.description, history_jobs.description),
          duration=coalesce(excluded.duration, history_jobs.duration),
          resolution=coalesce(excluded.resolution, history_jobs.resolution),
          thumbnail_url=coalesce(excluded.thumbnail_url, history_jobs.thumbnail_url),
          cover_url=coalesce(excluded.cover_url, history_jobs.cover_url),
          view_count=coalesce(excluded.view_count, history_jobs.view_count),
          like_count=coalesce(excluded.like_count, history_jobs.like_count),
          collection=coalesce(excluded.collection, history_jobs.collection),
          collection_id=coalesce(excluded.collection_id, history_jobs.collection_id),
          extra_json=excluded.extra_json,
          source_url=excluded.source_url,
          output_dir=excluded.output_dir,
          status=excluded.status,
          updated_at=datetime('now')
        """,
        (
            job_id,
            site,
            meta.item_id,
            meta.canonical_url or source_url,
            meta.title,
            meta.author,
            meta.upload_date,
            json.dumps(meta.tags, ensure_ascii=False),
            meta.description,
            meta.duration,
            meta.resolution,
            meta.thumbnail_url,
            meta.cover_url,
            meta.view_count,
            meta.like_count,
            meta.collection,
            meta.collection_id,
            json.dumps(meta.extra, ensure_ascii=False),
            source_url,
            str(output_dir),
            status,
        ),
    )
    conn.commit()


def record_file_done(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    site: str,
    file_id: str | None,
    name: str | None,
    path: str | Path | None,
    status: str = "done",
    item_id: str | None = None,
    canonical_url: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
    metadata: item_metadata | dict[str, Any] | None = None,
    compute_hash: bool = True,
) -> None:
    path_str = str(path) if path is not None else None
    file_size = size
    digest = sha256
    if path_str and Path(path_str).is_file():
        if file_size is None:
            file_size = Path(path_str).stat().st_size
        if digest is None and compute_hash and status == "done":
            digest = sha256_file(path_str)
    meta_dict: dict[str, Any]
    if isinstance(metadata, item_metadata):
        meta_dict = metadata.to_dict(omit_empty=True)
        item_id = item_id or metadata.item_id
        canonical_url = canonical_url or metadata.canonical_url
    elif isinstance(metadata, dict):
        meta_dict = metadata
    else:
        meta_dict = {}
    conn.execute(
        """
        insert into history_files (
          job_id, file_id, site, item_id, canonical_url, name, path,
          size, sha256, status, metadata_json, updated_at
        ) values (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
        on conflict(job_id, file_id) do update set
          site=excluded.site,
          item_id=coalesce(excluded.item_id, history_files.item_id),
          canonical_url=coalesce(excluded.canonical_url, history_files.canonical_url),
          name=coalesce(excluded.name, history_files.name),
          path=coalesce(excluded.path, history_files.path),
          size=coalesce(excluded.size, history_files.size),
          sha256=coalesce(excluded.sha256, history_files.sha256),
          status=excluded.status,
          metadata_json=excluded.metadata_json,
          updated_at=datetime('now')
        """,
        (
            job_id,
            file_id,
            site,
            item_id,
            canonical_url,
            name,
            path_str,
            file_size,
            digest,
            status,
            json.dumps(meta_dict, ensure_ascii=False),
        ),
    )
    conn.commit()


def record_job_finished(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    status: str,
    ok: int = 0,
    failed: int = 0,
    error_message: str | None = None,
) -> None:
    finished = status in {"done", "error", "cancelled", "skipped"}
    conn.execute(
        """
        update history_jobs
        set status = ?,
            ok = ?,
            failed = ?,
            error_message = coalesce(?, error_message),
            updated_at = datetime('now'),
            finished_at = case when ? then datetime('now') else finished_at end
        where id = ?
        """,
        (status, ok, failed, error_message, 1 if finished else 0, job_id),
    )
    conn.commit()


def _load_files(conn: sqlite3.Connection, job_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not job_ids:
        return {}
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"""
        select * from history_files
        where job_id in ({placeholders})
        order by id asc
        """,
        job_ids,
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {jid: [] for jid in job_ids}
    for row in rows:
        meta = json.loads(row["metadata_json"] or "{}")
        out.setdefault(row["job_id"], []).append(
            {
                "id": row["id"],
                "file_id": row["file_id"],
                "site": row["site"],
                "item_id": row["item_id"],
                "canonical_url": row["canonical_url"],
                "name": row["name"],
                "path": row["path"],
                "size": row["size"],
                "sha256": row["sha256"],
                "status": row["status"],
                "metadata": meta if isinstance(meta, dict) else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return out


def list_history(
    conn: sqlite3.Connection | None = None,
    *,
    site: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    query: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    include_files: bool = True,
    db_path: str | Path | None = None,
) -> list[history_entry]:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        return search_history(
            conn,
            site=site,
            status=status,
            tag=tag,
            date_from=date_from,
            date_to=date_to,
            query=query,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
            include_files=include_files,
        )
    finally:
        if own:
            conn.close()


def search_history(
    conn: sqlite3.Connection,
    *,
    site: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    query: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    include_files: bool = True,
) -> list[history_entry]:
    allowed_sort = {
        "created_at",
        "updated_at",
        "finished_at",
        "title",
        "site",
        "status",
    }
    sort_col = sort if sort in allowed_sort else "created_at"
    order_sql = "asc" if str(order).lower() == "asc" else "desc"
    clauses: list[str] = []
    params: list[Any] = []
    if site:
        clauses.append("site = ?")
        params.append(site)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)
    if tag:
        clauses.append("tags_json like ?")
        params.append(f'%"{tag}"%')
    if query:
        clauses.append(
            "(title like ? or author like ? or canonical_url like ? "
            "or source_url like ? or item_id like ? or description like ?)"
        )
        like = f"%{query}%"
        params.extend([like, like, like, like, like, like])
    where = f"where {' and '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        select * from history_jobs
        {where}
        order by {sort_col} {order_sql}
        limit ? offset ?
        """,
        [*params, max(1, min(limit, 1000)), max(0, offset)],
    ).fetchall()
    files_map = _load_files(conn, [r["id"] for r in rows]) if include_files else {}
    return [
        _row_to_entry(row, files=files_map.get(row["id"], []) if include_files else None)
        for row in rows
    ]


def get_history(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    include_files: bool = True,
) -> history_entry | None:
    row = conn.execute(
        "select * from history_jobs where id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    files = _load_files(conn, [job_id]).get(job_id, []) if include_files else None
    return _row_to_entry(row, files=files)


def delete_history(
    conn: sqlite3.Connection,
    job_ids: list[str],
    *,
    delete_files: bool = False,
) -> dict[str, Any]:
    removed_entries = 0
    removed_paths: list[str] = []
    missing_paths: list[str] = []
    for job_id in job_ids:
        if delete_files:
            paths = conn.execute(
                "select path from history_files where job_id = ?",
                (job_id,),
            ).fetchall()
            for row in paths:
                path = row["path"]
                if not path:
                    continue
                target = Path(path)
                if target.is_file():
                    try:
                        target.unlink()
                        removed_paths.append(path)
                    except OSError:
                        missing_paths.append(path)
                else:
                    missing_paths.append(path)
        cur = conn.execute("delete from history_jobs where id = ?", (job_id,))
        removed_entries += cur.rowcount
    conn.commit()
    return {
        "removed_entries": removed_entries,
        "removed_files": removed_paths,
        "missing_files": missing_paths,
    }
