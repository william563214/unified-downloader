"""duplicate detection against download history."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from udl.metadata import item_metadata
from udl.store.hashutil import hash_file_for_history

duplicate_mode = Literal["skip", "overwrite", "rename"]


@dataclass
class duplicate_hit:
    matched_by: str
    job_id: str | None
    file_id: str | None
    path: str | None
    site: str | None
    item_id: str | None
    status: str | None
    size: int | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_by": self.matched_by,
            "job_id": self.job_id,
            "file_id": self.file_id,
            "path": self.path,
            "site": self.site,
            "item_id": self.item_id,
            "status": self.status,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass
class duplicate_action:
    action: duplicate_mode
    hit: duplicate_hit | None = None
    dest: Path | None = None
    already_downloaded: bool = False
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "already_downloaded": self.already_downloaded,
            "message": self.message,
            "dest": str(self.dest) if self.dest else None,
            "hit": self.hit.to_dict() if self.hit else None,
        }


def find_duplicate(
    conn: sqlite3.Connection,
    *,
    site: str,
    item_id: str | None = None,
    canonical_url: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
    path: str | Path | None = None,
) -> duplicate_hit | None:
    """prefer (site, item_id) then URL then size+hash then existing path.

    MEGA uses node handle as item_id and requires size as well so folder-level
    handles do not falsely skip sibling files of different sizes.
    """
    if site and item_id:
        # mega: match node handle + size when size is known
        if site == "mega" and size is not None and size > 0:
            row = conn.execute(
                """
                select job_id, file_id, path, site, item_id, status, size, sha256
                from history_files
                where site = ? and item_id = ? and size = ?
                  and status in ('done', 'skipped')
                order by updated_at desc
                limit 1
                """,
                (site, item_id, size),
            ).fetchone()
            if row is not None:
                return _hit("mega_handle_size", row)
        else:
            row = conn.execute(
                """
                select job_id, file_id, path, site, item_id, status, size, sha256
                from history_files
                where site = ? and item_id = ? and status in ('done', 'skipped')
                order by updated_at desc
                limit 1
                """,
                (site, item_id),
            ).fetchone()
            if row is None and site != "mega":
                row = conn.execute(
                    """
                    select id as job_id, null as file_id, output_dir as path,
                           site, item_id, status, null as size, null as sha256
                    from history_jobs
                    where site = ? and item_id = ? and status in ('done', 'skipped')
                    order by updated_at desc
                    limit 1
                    """,
                    (site, item_id),
                ).fetchone()
            if row is not None:
                return _hit("site_item_id", row)

    if canonical_url:
        row = conn.execute(
            """
            select job_id, file_id, path, site, item_id, status, size, sha256
            from history_files
            where canonical_url = ? and status in ('done', 'skipped')
            order by updated_at desc
            limit 1
            """,
            (canonical_url,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                select id as job_id, null as file_id, output_dir as path,
                       site, item_id, status, null as size, null as sha256
                from history_jobs
                where (canonical_url = ? or source_url = ?)
                  and status in ('done', 'skipped')
                order by updated_at desc
                limit 1
                """,
                (canonical_url, canonical_url),
            ).fetchone()
        if row is not None:
            return _hit("url", row)

    digest = sha256
    if digest is None and path is not None and Path(path).is_file():
        digest = hash_file_for_history(path)
    if digest:
        row = conn.execute(
            """
            select job_id, file_id, path, site, item_id, status, size, sha256
            from history_files
            where sha256 = ? and status in ('done', 'skipped')
            order by updated_at desc
            limit 1
            """,
            (digest,),
        ).fetchone()
        if row is not None:
            return _hit("hash", row)

    if size is not None and size > 0 and digest:
        row = conn.execute(
            """
            select job_id, file_id, path, site, item_id, status, size, sha256
            from history_files
            where size = ? and sha256 = ? and status in ('done', 'skipped')
            order by updated_at desc
            limit 1
            """,
            (size, digest),
        ).fetchone()
        if row is not None:
            return _hit("size_hash", row)

    if path is not None and Path(path).is_file() and Path(path).stat().st_size > 0:
        return duplicate_hit(
            matched_by="path",
            job_id=None,
            file_id=None,
            path=str(path),
            site=site,
            item_id=item_id,
            status="done",
            size=Path(path).stat().st_size,
            sha256=digest,
        )
    return None


def resolve_duplicate(
    conn: sqlite3.Connection,
    *,
    site: str,
    dest: Path,
    mode: duplicate_mode = "skip",
    metadata: item_metadata | None = None,
    item_id: str | None = None,
    canonical_url: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
) -> duplicate_action:
    meta = metadata
    hit = find_duplicate(
        conn,
        site=site,
        item_id=item_id or (meta.item_id if meta else None),
        canonical_url=canonical_url or (meta.canonical_url if meta else None),
        size=size,
        sha256=sha256,
        path=dest,
    )
    if hit is None and not (dest.exists() and dest.stat().st_size > 0):
        return duplicate_action(action=mode, dest=dest, already_downloaded=False)

    if hit is None and dest.exists():
        hit = duplicate_hit(
            matched_by="path",
            job_id=None,
            file_id=None,
            path=str(dest),
            site=site,
            item_id=item_id or (meta.item_id if meta else None),
            status="done",
            size=dest.stat().st_size,
        )

    if mode == "skip":
        return duplicate_action(
            action="skip",
            hit=hit,
            dest=dest,
            already_downloaded=True,
            message="already downloaded",
        )
    if mode == "overwrite":
        if dest.exists():
            dest.unlink(missing_ok=True)
        part = dest.with_name(dest.name + ".part")
        if part.exists():
            part.unlink(missing_ok=True)
        return duplicate_action(
            action="overwrite",
            hit=hit,
            dest=dest,
            already_downloaded=False,
            message="overwriting existing download",
        )
    # rename
    new_dest = _unique_path(dest)
    return duplicate_action(
        action="rename",
        hit=hit,
        dest=new_dest,
        already_downloaded=False,
        message=f"renamed to avoid overwrite: {new_dest.name}",
    )


def _unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _hit(matched_by: str, row: sqlite3.Row) -> duplicate_hit:
    return duplicate_hit(
        matched_by=matched_by,
        job_id=row["job_id"],
        file_id=row["file_id"] if "file_id" in row.keys() else None,
        path=row["path"],
        site=row["site"],
        item_id=row["item_id"],
        status=row["status"],
        size=row["size"] if "size" in row.keys() else None,
        sha256=row["sha256"] if "sha256" in row.keys() else None,
    )
