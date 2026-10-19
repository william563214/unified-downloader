"""download session helpers: history queue duplicates around extractors."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from udl.events import emitter
from udl.extractors.base import extract_result
from udl.metadata import item_metadata
from udl.store.db import connect
from udl.store.duplicates import duplicate_mode, resolve_duplicate
from udl.store.hashutil import sha256_file
from udl.store.history import record_file_done, record_job_finished, record_job_started
from udl.store.queue import queue_job, save_queue_job, update_queue_status


def open_store(db_path: str | Path | None = None) -> sqlite3.Connection:
    return connect(db_path)


def prepare_download(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    url: str,
    output_dir: Path,
    result: extract_result,
    on_duplicate: duplicate_mode = "skip",
    emit: emitter | None = None,
    quality: str | None = None,
    part: str | None = None,
    cloudflare_bypass: bool = False,
    workers: int | None = None,
    ffmpeg: str | None = None,
) -> tuple[set[int], dict[int, Path], bool]:
    """record job start apply duplicate policy. returns skip set dest overrides all-skipped."""
    meta = result.metadata or item_metadata(site=result.site, title=result.title)
    if meta.canonical_url is None:
        meta.canonical_url = url
    if meta.title is None:
        meta.title = result.title

    save_queue_job(
        conn,
        queue_job(
            id=job_id,
            url=url,
            output_dir=str(output_dir),
            status="running",
            site=result.site,
            title=result.title,
            quality=quality,
            part=part,
            cloudflare_bypass=cloudflare_bypass,
            workers=workers,
            ffmpeg=ffmpeg,
            on_duplicate=on_duplicate,
            metadata=meta.to_dict(omit_empty=True),
        ),
        metadata=meta,
    )
    record_job_started(
        conn,
        job_id=job_id,
        site=result.site,
        source_url=url,
        output_dir=output_dir,
        metadata=meta,
        status="running",
    )

    skip_indexes: set[int] = set()
    overrides: dict[int, Path] = {}
    for idx, f in enumerate(result.files):
        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = dest_dir / f.name
        file_meta = f.metadata or meta
        action = resolve_duplicate(
            conn,
            site=result.site,
            dest=dest,
            mode=on_duplicate,
            metadata=file_meta,
            item_id=file_meta.item_id if file_meta else None,
            canonical_url=file_meta.canonical_url if file_meta else None,
            size=f.size,
        )
        if action.dest is not None and action.dest != dest:
            overrides[idx] = action.dest
            dest = action.dest
        should_emit = (
            action.hit is not None
            or action.already_downloaded
            or action.action == "rename"
        )
        if emit and should_emit:
            emit.duplicate(
                job=job_id,
                file=f"{job_id}:{idx}",
                action=action.action,
                message=action.message,
                path=str(dest),
                matched_by=action.hit.matched_by if action.hit else None,
                already_downloaded=action.already_downloaded,
                metadata=file_meta.to_dict(omit_empty=True) if file_meta else None,
            )
        if action.action == "skip" and action.already_downloaded:
            skip_indexes.add(idx)
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=action.hit.path if action.hit and action.hit.path else dest,
                status="skipped",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                size=f.size,
                metadata=file_meta,
                compute_hash=False,
            )

    all_skipped = bool(result.files) and len(skip_indexes) == len(result.files)
    return skip_indexes, overrides, all_skipped


def finalize_download(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    result: extract_result,
    output_dir: Path,
    ok: int,
    failed: int,
    cancelled: bool = False,
    error_message: str | None = None,
    skip_indexes: set[int] | None = None,
    file_dest_overrides: dict[int, Path] | None = None,
) -> None:
    skip_indexes = skip_indexes or set()
    file_dest_overrides = file_dest_overrides or {}
    for idx, f in enumerate(result.files):
        if idx in skip_indexes:
            continue
        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = file_dest_overrides.get(idx, dest_dir / f.name)
        file_meta = f.metadata or result.metadata
        if dest.exists() and dest.is_file():
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=dest,
                status="done",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                metadata=file_meta,
                compute_hash=True,
            )
        elif failed > 0:
            record_file_done(
                conn,
                job_id=job_id,
                site=result.site,
                file_id=f"{job_id}:{idx}",
                name=f.name,
                path=dest,
                status="error",
                item_id=file_meta.item_id if file_meta else None,
                canonical_url=file_meta.canonical_url if file_meta else None,
                metadata=file_meta,
                compute_hash=False,
            )

    if cancelled:
        status = "cancelled"
    elif failed == 0:
        status = "done" if ok > 0 else "skipped"
    else:
        status = "error"
    record_job_finished(
        conn,
        job_id=job_id,
        status=status,
        ok=ok,
        failed=failed,
        error_message=error_message,
    )
    update_queue_status(
        conn,
        job_id,
        status,
        error_message=error_message,
        title=result.title,
        site=result.site,
    )


def file_digest(path: Path) -> tuple[int | None, str | None]:
    if not path.is_file():
        return None, None
    return path.stat().st_size, sha256_file(path)


def metadata_payload(result: extract_result) -> dict[str, Any] | None:
    if result.metadata is None:
        return None
    return result.metadata.to_dict(omit_empty=True)
