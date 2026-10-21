"""run NFO / cover postprocess for downloaded media and record history artifacts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from udl.events import emitter
from udl.extractors.base import extract_result, extracted_file
from udl.metadata import item_metadata
from udl.postprocess.cover import download_cover, embed_cover, video_exts
from udl.postprocess.nfo import write_nfo
from udl.postprocess.options import postprocess_options
from udl.store.history import record_file_done, set_job_cover_path

# lowercase constants per project convention
role_nfo = "nfo"
role_cover = "cover"
media_video_exts = video_exts | {".avi", ".flv", ".ts", ".mpeg", ".mpg"}


@dataclass
class artifact_result:
    role: str
    path: Path
    name: str
    file_id: str
    media_file_id: str | None = None
    embedded: bool = False


def _media_candidates(
    result: extract_result,
    output_dir: Path,
    *,
    skip_indexes: set[int] | None = None,
    file_dest_overrides: dict[int, Path] | None = None,
) -> list[tuple[int, extracted_file, Path]]:
    skip_indexes = skip_indexes or set()
    file_dest_overrides = file_dest_overrides or {}
    out: list[tuple[int, extracted_file, Path]] = []
    for idx, f in enumerate(result.files):
        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = file_dest_overrides.get(idx, dest_dir / f.name)
        if not dest.is_file():
            continue
        # prefer video containers; also allow single non-video if that is the only file
        if dest.suffix.lower() in media_video_exts or len(result.files) == 1:
            out.append((idx, f, dest))
    if out:
        return out
    # fall back: any existing downloaded file
    for idx, f in enumerate(result.files):
        if idx in skip_indexes:
            # still allow postprocess when skipped file exists on disk
            pass
        dest_dir = output_dir / f.relative_dir if f.relative_dir else output_dir
        dest = file_dest_overrides.get(idx, dest_dir / f.name)
        if dest.is_file():
            out.append((idx, f, dest))
    return out


def run_postprocess(
    result: extract_result,
    output_dir: Path,
    opts: postprocess_options,
    *,
    job_id: str,
    emit: emitter | None = None,
    conn: sqlite3.Connection | None = None,
    cancel: Event | None = None,
    skip_indexes: set[int] | None = None,
    exclude_indexes: set[int] | None = None,
    file_dest_overrides: dict[int, Path] | None = None,
    kind_hint: str | None = None,
) -> list[artifact_result]:
    """generate NFO / cover for downloaded files. emits events and records history."""
    if not opts.any_enabled():
        return []

    site = result.site
    artifacts: list[artifact_result] = []
    exclude_indexes = exclude_indexes or set()
    media_list = [
        row
        for row in _media_candidates(
            result,
            output_dir,
            skip_indexes=skip_indexes,
            file_dest_overrides=file_dest_overrides,
        )
        if row[0] not in exclude_indexes
    ]
    if not media_list:
        if emit:
            emit.postprocess(
                job=job_id,
                step="skip",
                message="no media files available for postprocess",
                ok=True,
            )
        return []

    job_cover: Path | None = None
    hint = kind_hint or result.kind
    # folder.jpg is shared per directory write at most once to avoid overwrite races
    folder_cover_dirs: set[str] = set()

    for idx, extracted, media_path in media_list:
        file_meta = extracted.metadata or result.metadata or item_metadata(site=site)
        media_file_id = f"{job_id}:{idx}"

        if opts.nfo_enabled_for(site):
            try:
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=media_file_id,
                        step="nfo",
                        path=str(media_path.with_suffix(".nfo")),
                        message="writing nfo",
                    )
                nfo_path = write_nfo(file_meta, media_path, kind_hint=hint)
                art = artifact_result(
                    role=role_nfo,
                    path=nfo_path,
                    name=nfo_path.name,
                    file_id=f"{media_file_id}:nfo",
                    media_file_id=media_file_id,
                )
                artifacts.append(art)
                _emit_and_record(
                    emit=emit,
                    conn=conn,
                    job_id=job_id,
                    site=site,
                    art=art,
                    metadata=file_meta,
                )
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=art.file_id,
                        step="nfo",
                        path=str(nfo_path),
                        role=role_nfo,
                        message="nfo written",
                        ok=True,
                    )
            except Exception as exc:  # noqa: BLE001
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=media_file_id,
                        step="nfo",
                        message=str(exc),
                        ok=False,
                        code=getattr(exc, "code", None) or "nfo_failed",
                    )
                    emit.error(
                        job=job_id,
                        file=media_file_id,
                        message=f"nfo failed: {exc}",
                        code=getattr(exc, "code", None) or "nfo_failed",
                    )

        cover_path: Path | None = None
        if opts.cover_enabled_for(site):
            try:
                naming = opts.cover_naming
                parent_key = str(media_path.parent.resolve())
                skip_folder_write = (
                    naming == "folder" and parent_key in folder_cover_dirs
                )
                if skip_folder_write:
                    existing = media_path.parent / "folder.jpg"
                    if existing.is_file():
                        cover_path = existing
                        if emit:
                            emit.postprocess(
                                job=job_id,
                                file=media_file_id,
                                step="cover",
                                path=str(existing),
                                message="reusing folder.jpg already written for this directory",
                                ok=True,
                            )
                    elif emit:
                        emit.postprocess(
                            job=job_id,
                            file=media_file_id,
                            step="cover",
                            message="skip folder.jpg already handled for this directory",
                            ok=True,
                        )
                else:
                    if emit:
                        emit.postprocess(
                            job=job_id,
                            file=media_file_id,
                            step="cover",
                            message="downloading cover",
                        )
                    cover_path = download_cover(
                        file_meta,
                        media_path,
                        naming=naming,
                        ffmpeg=opts.ffmpeg,
                        job_id=job_id,
                        file_id=f"{media_file_id}:cover",
                        emit=emit,
                        cancel=cancel,
                    )
                    if naming == "folder" and cover_path is not None:
                        folder_cover_dirs.add(parent_key)
                    if cover_path is None:
                        if emit:
                            emit.postprocess(
                                job=job_id,
                                file=media_file_id,
                                step="cover",
                                message="no thumbnail/cover url in metadata",
                                ok=True,
                            )
                    else:
                        art = artifact_result(
                            role=role_cover,
                            path=cover_path,
                            name=cover_path.name,
                            file_id=f"{media_file_id}:cover",
                            media_file_id=media_file_id,
                        )
                        artifacts.append(art)
                        job_cover = job_cover or cover_path
                        _emit_and_record(
                            emit=emit,
                            conn=conn,
                            job_id=job_id,
                            site=site,
                            art=art,
                            metadata=file_meta,
                        )
                        if emit:
                            emit.postprocess(
                                job=job_id,
                                file=art.file_id,
                                step="cover",
                                path=str(cover_path),
                                role=role_cover,
                                message="cover saved",
                                ok=True,
                            )
            except Exception as exc:  # noqa: BLE001
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=media_file_id,
                        step="cover",
                        message=str(exc),
                        ok=False,
                        code=getattr(exc, "code", None) or "cover_failed",
                    )
                    emit.error(
                        job=job_id,
                        file=media_file_id,
                        message=f"cover failed: {exc}",
                        code=getattr(exc, "code", None) or "cover_failed",
                    )

        if opts.embed_cover and cover_path is not None and media_path.suffix.lower() in video_exts:
            try:
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=media_file_id,
                        step="embed_cover",
                        path=str(media_path),
                        message="embedding cover into media",
                    )
                embed_cover(media_path, cover_path, ffmpeg=opts.ffmpeg)
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=media_file_id,
                        step="embed_cover",
                        path=str(media_path),
                        role=role_cover,
                        message="cover embedded",
                        ok=True,
                    )
            except Exception as exc:  # noqa: BLE001
                if emit:
                    emit.postprocess(
                        job=job_id,
                        file=media_file_id,
                        step="embed_cover",
                        message=str(exc),
                        ok=False,
                        code=getattr(exc, "code", None) or "cover_failed",
                    )
                    emit.error(
                        job=job_id,
                        file=media_file_id,
                        message=f"embed cover failed: {exc}",
                        code=getattr(exc, "code", None) or "cover_failed",
                    )

        if cancel is not None and cancel.is_set():
            break

    if conn is not None and job_cover is not None:
        set_job_cover_path(conn, job_id=job_id, cover_path=job_cover)

    return artifacts


def _emit_and_record(
    *,
    emit: emitter | None,
    conn: sqlite3.Connection | None,
    job_id: str,
    site: str,
    art: artifact_result,
    metadata: item_metadata,
) -> None:
    meta_dict: dict[str, Any] = metadata.to_dict(omit_empty=True)
    meta_dict["role"] = art.role
    if emit:
        emit.file(
            job=job_id,
            id=art.file_id,
            name=art.name,
            path=str(art.path),
            role=art.role,
            metadata=meta_dict,
        )
        emit.done(
            job=job_id,
            file=art.file_id,
            path=str(art.path),
            metadata=meta_dict,
            size=art.path.stat().st_size if art.path.is_file() else None,
        )
    if conn is not None:
        record_file_done(
            conn,
            job_id=job_id,
            site=site,
            file_id=art.file_id,
            name=art.name,
            path=art.path,
            status="done",
            item_id=metadata.item_id,
            canonical_url=metadata.canonical_url,
            metadata=meta_dict,
            role=art.role,
            compute_hash=True,
        )
