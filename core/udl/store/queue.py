"""persistent download queue stored in SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from udl.metadata import item_metadata
from udl.store.db import connect
from udl.store.schedule import default_queue_name

# unfinished statuses auto-resumed on GUI restart
unfinished_statuses = frozenset({"queued", "running", "paused"})


@dataclass
class queue_job:
    id: str
    url: str
    output_dir: str
    status: str
    site: str | None = None
    title: str | None = None
    quality: str | None = None
    part: str | None = None
    cloudflare_bypass: bool = False
    workers: int | None = None
    ffmpeg: str | None = None
    on_duplicate: str = "skip"
    queue_name: str = default_queue_name
    metadata: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "output_dir": self.output_dir,
            "status": self.status,
            "site": self.site,
            "title": self.title,
            "quality": self.quality,
            "part": self.part,
            "cloudflare_bypass": self.cloudflare_bypass,
            "workers": self.workers,
            "ffmpeg": self.ffmpeg,
            "on_duplicate": self.on_duplicate,
            "queue_name": self.queue_name or default_queue_name,
            "metadata": self.metadata,
            "options": self.options,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _row_to_job(row: sqlite3.Row) -> queue_job:
    meta = json.loads(row["metadata_json"] or "{}")
    opts = json.loads(row["options_json"] or "{}")
    keys = set(row.keys())
    queue_name = (
        row["queue_name"]
        if "queue_name" in keys and row["queue_name"]
        else default_queue_name
    )
    return queue_job(
        id=row["id"],
        url=row["url"],
        output_dir=row["output_dir"],
        status=row["status"],
        site=row["site"],
        title=row["title"],
        quality=row["quality"],
        part=row["part"],
        cloudflare_bypass=bool(row["cloudflare_bypass"]),
        workers=row["workers"],
        ffmpeg=row["ffmpeg"],
        on_duplicate=row["on_duplicate"] or "skip",
        queue_name=queue_name,
        metadata=meta if isinstance(meta, dict) else {},
        options=opts if isinstance(opts, dict) else {},
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def save_queue_job(
    conn: sqlite3.Connection,
    job: queue_job,
    *,
    metadata: item_metadata | dict[str, Any] | None = None,
) -> None:
    if isinstance(metadata, item_metadata):
        meta_dict = metadata.to_dict(omit_empty=True)
    elif isinstance(metadata, dict):
        meta_dict = metadata
    else:
        meta_dict = job.metadata
    conn.execute(
        """
        insert into queue_jobs (
          id, url, site, title, output_dir, status, quality, part,
          cloudflare_bypass, workers, ffmpeg, on_duplicate, queue_name,
          metadata_json, options_json, error_message, updated_at, started_at
        ) values (
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'),
          case when ? = 'running' then datetime('now') else null end
        )
        on conflict(id) do update set
          url=excluded.url,
          site=coalesce(excluded.site, queue_jobs.site),
          title=coalesce(excluded.title, queue_jobs.title),
          output_dir=excluded.output_dir,
          status=excluded.status,
          quality=excluded.quality,
          part=excluded.part,
          cloudflare_bypass=excluded.cloudflare_bypass,
          workers=excluded.workers,
          ffmpeg=excluded.ffmpeg,
          on_duplicate=excluded.on_duplicate,
          queue_name=excluded.queue_name,
          metadata_json=excluded.metadata_json,
          options_json=excluded.options_json,
          error_message=excluded.error_message,
          updated_at=datetime('now'),
          started_at=case
            when excluded.status = 'running' and queue_jobs.started_at is null
              then datetime('now')
            else queue_jobs.started_at
          end,
          finished_at=case
            when excluded.status in ('done','error','cancelled','skipped')
              then datetime('now')
            else null
          end
        """,
        (
            job.id,
            job.url,
            job.site,
            job.title,
            job.output_dir,
            job.status,
            job.quality,
            job.part,
            1 if job.cloudflare_bypass else 0,
            job.workers,
            job.ffmpeg,
            job.on_duplicate,
            job.queue_name or default_queue_name,
            json.dumps(meta_dict, ensure_ascii=False),
            json.dumps(job.options, ensure_ascii=False),
            job.error_message,
            job.status,
        ),
    )
    conn.commit()


def update_queue_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    *,
    error_message: str | None = None,
    title: str | None = None,
    site: str | None = None,
) -> None:
    finished = status in {"done", "error", "cancelled", "skipped"}
    conn.execute(
        """
        update queue_jobs
        set status = ?,
            error_message = coalesce(?, error_message),
            title = coalesce(?, title),
            site = coalesce(?, site),
            updated_at = datetime('now'),
            started_at = case
              when ? = 'running' and started_at is null then datetime('now')
              else started_at
            end,
            finished_at = case when ? then datetime('now') else finished_at end
        where id = ?
        """,
        (
            status,
            error_message,
            title,
            site,
            status,
            1 if finished else 0,
            job_id,
        ),
    )
    conn.commit()


def load_queue_job(conn: sqlite3.Connection, job_id: str) -> queue_job | None:
    row = conn.execute(
        "select * from queue_jobs where id = ?",
        (job_id,),
    ).fetchone()
    return _row_to_job(row) if row else None


def list_queue(
    conn: sqlite3.Connection | None = None,
    *,
    status: str | None = None,
    unfinished_only: bool = False,
    queue_name: str | None = None,
    limit: int = 200,
    db_path: str | Path | None = None,
) -> list[queue_job]:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if unfinished_only:
            placeholders = ",".join("?" for _ in unfinished_statuses)
            clauses.append(f"status in ({placeholders})")
            params.extend(sorted(unfinished_statuses))
        elif status:
            clauses.append("status = ?")
            params.append(status)
        if queue_name:
            clauses.append("queue_name = ?")
            params.append(queue_name)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            select * from queue_jobs
            {where}
            order by updated_at desc
            limit ?
            """,
            [*params, max(1, min(limit, 1000))],
        ).fetchall()
        return [_row_to_job(row) for row in rows]
    finally:
        if own:
            conn.close()


def delete_queue_job(conn: sqlite3.Connection, job_ids: list[str]) -> int:
    removed = 0
    for job_id in job_ids:
        cur = conn.execute("delete from queue_jobs where id = ?", (job_id,))
        removed += cur.rowcount
    conn.commit()
    return removed
