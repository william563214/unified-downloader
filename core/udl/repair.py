"""repair center: compare history DB with filesystem and apply fixes."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from udl.store.hashutil import hash_file, hash_file_for_history, sha256_file
from udl.store.history import delete_history, get_history
from udl.store.queue import queue_job, save_queue_job

issue_kind = Literal[
    "missing_file",
    "partial",
    "size_mismatch",
    "hash_mismatch",
    "checksum_mismatch",
    "extract_failed",
    "needs_password",
    "orphan_file",
    "stale_record",
]

# only flag orphans that look like udl media / sidecar outputs
orphan_known_extensions = {
    ".mp4",
    ".m4v",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".flv",
    ".ts",
    ".mpeg",
    ".mpg",
    ".mp3",
    ".m4a",
    ".flac",
    ".wav",
    ".aac",
    ".opus",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".nfo",
    ".xml",
    ".ass",
    ".ssa",
    ".srt",
    ".lrc",
    ".txt",
    ".json",
    ".part",
}
orphan_ignore_names = {
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    ".localized",
    "folder.jpg",
    "albumart.jpg",
    "cover.jpg",
}


def _is_candidate_orphan(
    path: Path,
    *,
    allowed_exts: set[str],
    ignore_names: set[str],
) -> bool:
    """restrict orphan reporting to likely udl outputs."""
    name = path.name
    lower = name.lower()
    if lower in ignore_names:
        return False
    if lower.startswith("~$"):
        return False
    if lower.startswith(".") and not name.endswith(".part"):
        return False
    if name.endswith(".part"):
        return True
    return path.suffix.lower() in allowed_exts


@dataclass
class repair_issue:
    id: str
    kind: str
    job_id: str | None = None
    file_row_id: int | None = None
    file_id: str | None = None
    path: str | None = None
    expected_size: int | None = None
    actual_size: int | None = None
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    site: str | None = None
    source_url: str | None = None
    title: str | None = None
    message: str | None = None
    suggested_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "job_id": self.job_id,
            "file_row_id": self.file_row_id,
            "file_id": self.file_id,
            "path": self.path,
            "expected_size": self.expected_size,
            "actual_size": self.actual_size,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "site": self.site,
            "source_url": self.source_url,
            "title": self.title,
            "message": self.message,
            "suggested_actions": list(self.suggested_actions),
        }


@dataclass
class repair_scan_result:
    issues: list[repair_issue] = field(default_factory=list)
    scanned_jobs: int = 0
    scanned_files: int = 0
    scanned_dirs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_jobs": self.scanned_jobs,
            "scanned_files": self.scanned_files,
            "scanned_dirs": list(self.scanned_dirs),
            "issue_count": len(self.issues),
            "issues": [i.to_dict() for i in self.issues],
        }


def _issue_id(*parts: object) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:16]


def scan_repair(
    conn: sqlite3.Connection,
    *,
    output_dirs: list[str | Path] | None = None,
    verify_hash: bool = False,
    include_orphans: bool = True,
    orphan_extensions: set[str] | None = None,
    orphan_ignore: set[str] | None = None,
) -> repair_scan_result:
    """scan history records versus filesystem."""
    result = repair_scan_result()
    jobs = conn.execute("select * from history_jobs").fetchall()
    result.scanned_jobs = len(jobs)
    job_urls = {row["id"]: row["source_url"] or row["canonical_url"] for row in jobs}
    job_titles = {row["id"]: row["title"] for row in jobs}
    job_sites = {row["id"]: row["site"] for row in jobs}
    tracked_paths: set[str] = set()

    files = conn.execute("select * from history_files").fetchall()
    result.scanned_files = len(files)

    for row in files:
        path_str = row["path"]
        job_id = row["job_id"]
        if path_str:
            path_obj = Path(path_str)
            tracked = str(path_obj.resolve()) if path_obj.exists() else path_str
            tracked_paths.add(tracked)
        if not path_str:
            result.issues.append(
                repair_issue(
                    id=_issue_id("stale", job_id, row["id"]),
                    kind="stale_record",
                    job_id=job_id,
                    file_row_id=int(row["id"]),
                    file_id=row["file_id"],
                    site=row["site"] or job_sites.get(job_id),
                    source_url=job_urls.get(job_id),
                    title=job_titles.get(job_id),
                    message="history file row has empty path",
                    suggested_actions=["remove_stale"],
                )
            )
            continue

        path = Path(path_str)
        part_path = Path(str(path) + ".part")
        if part_path.is_file():
            result.issues.append(
                repair_issue(
                    id=_issue_id("partial", job_id, row["id"], part_path),
                    kind="partial",
                    job_id=job_id,
                    file_row_id=int(row["id"]),
                    file_id=row["file_id"],
                    path=str(part_path),
                    actual_size=part_path.stat().st_size,
                    site=row["site"] or job_sites.get(job_id),
                    source_url=job_urls.get(job_id),
                    title=job_titles.get(job_id),
                    message="leftover .part file",
                    suggested_actions=["cleanup_partials", "requeue"],
                )
            )

        if not path.is_file():
            # also look for sibling .part only path
            result.issues.append(
                repair_issue(
                    id=_issue_id("missing", job_id, row["id"], path_str),
                    kind="missing_file",
                    job_id=job_id,
                    file_row_id=int(row["id"]),
                    file_id=row["file_id"],
                    path=path_str,
                    expected_size=row["size"],
                    expected_sha256=row["sha256"],
                    site=row["site"] or job_sites.get(job_id),
                    source_url=job_urls.get(job_id),
                    title=job_titles.get(job_id),
                    message="file missing on disk",
                    suggested_actions=["requeue", "relink", "remove_stale"],
                )
            )
            continue

        actual_size = path.stat().st_size
        expected_size = row["size"]
        if expected_size is not None and int(expected_size) != actual_size:
            result.issues.append(
                repair_issue(
                    id=_issue_id("size", job_id, row["id"], path_str),
                    kind="size_mismatch",
                    job_id=job_id,
                    file_row_id=int(row["id"]),
                    file_id=row["file_id"],
                    path=path_str,
                    expected_size=int(expected_size),
                    actual_size=actual_size,
                    expected_sha256=row["sha256"],
                    site=row["site"] or job_sites.get(job_id),
                    source_url=job_urls.get(job_id),
                    title=job_titles.get(job_id),
                    message="size differs from history",
                    suggested_actions=["requeue", "remove_stale"],
                )
            )

        expected_hash = row["sha256"]
        if verify_hash and expected_hash:
            actual_hash = sha256_file(path)
            if actual_hash and actual_hash.lower() != str(expected_hash).lower():
                result.issues.append(
                    repair_issue(
                        id=_issue_id("hash", job_id, row["id"], path_str),
                        kind="hash_mismatch",
                        job_id=job_id,
                        file_row_id=int(row["id"]),
                        file_id=row["file_id"],
                        path=path_str,
                        expected_size=expected_size,
                        actual_size=actual_size,
                        expected_sha256=str(expected_hash),
                        actual_sha256=actual_hash,
                        site=row["site"] or job_sites.get(job_id),
                        source_url=job_urls.get(job_id),
                        title=job_titles.get(job_id),
                        message="sha256 differs from history",
                        suggested_actions=["requeue", "remove_stale"],
                    )
                )

        # checksum / extract failures recorded in metadata.extra
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        extra = meta.get("extra") if isinstance(meta, dict) else None
        checksum = extra.get("checksum") if isinstance(extra, dict) else None
        if isinstance(checksum, dict) and checksum.get("ok") is False:
            algo = str(checksum.get("algo") or "sha256")
            expected = checksum.get("expected")
            actual = checksum.get("actual")
            if expected and not actual and path.is_file():
                actual = hash_file(path, algo)
            result.issues.append(
                repair_issue(
                    id=_issue_id("checksum", job_id, row["id"], path_str),
                    kind="checksum_mismatch",
                    job_id=job_id,
                    file_row_id=int(row["id"]),
                    file_id=row["file_id"],
                    path=path_str,
                    expected_size=expected_size,
                    actual_size=actual_size,
                    expected_sha256=str(expected) if expected else None,
                    actual_sha256=str(actual) if actual else None,
                    site=row["site"] or job_sites.get(job_id),
                    source_url=job_urls.get(job_id),
                    title=job_titles.get(job_id),
                    message=str(checksum.get("message") or f"{algo} checksum mismatch"),
                    suggested_actions=["requeue", "remove_stale"],
                )
            )
        extract = extra.get("extract") if isinstance(extra, dict) else None
        if isinstance(extract, dict) and extract.get("ok") is False:
            code = str(extract.get("code") or "extract_failed")
            kind = "needs_password" if code == "needs_password" else "extract_failed"
            archive_path = str(extract.get("path") or path_str or "")
            result.issues.append(
                repair_issue(
                    id=_issue_id("extract", job_id, row["id"], archive_path),
                    kind=kind,
                    job_id=job_id,
                    file_row_id=int(row["id"]),
                    file_id=row["file_id"],
                    path=archive_path or path_str,
                    expected_size=expected_size,
                    actual_size=actual_size,
                    site=row["site"] or job_sites.get(job_id),
                    source_url=job_urls.get(job_id),
                    title=job_titles.get(job_id),
                    message=str(extract.get("message") or code),
                    suggested_actions=["retry_extract", "requeue", "remove_stale"],
                )
            )

    # orphan scan: files under known output dirs not referenced in DB
    dirs: list[Path] = []
    if output_dirs:
        dirs = [Path(d).expanduser() for d in output_dirs]
    else:
        for row in jobs:
            if row["output_dir"]:
                dirs.append(Path(row["output_dir"]))
    # unique existing dirs
    seen: set[str] = set()
    unique_dirs: list[Path] = []
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key in seen:
            continue
        seen.add(key)
        unique_dirs.append(d)
    result.scanned_dirs = [str(d) for d in unique_dirs]

    if include_orphans:
        allowed_exts = {
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in (orphan_extensions or orphan_known_extensions)
        }
        ignore_names = {
            n.lower() for n in (orphan_ignore or orphan_ignore_names)
        }
        # history output roots only never scan arbitrary trees outside them
        db_paths = {
            str(Path(r["path"]).resolve())
            for r in files
            if r["path"] and Path(r["path"]).exists()
        }
        db_paths |= {r["path"] for r in files if r["path"]}
        for directory in unique_dirs:
            if not directory.is_dir():
                continue
            for found in directory.rglob("*"):
                if not found.is_file():
                    continue
                if not _is_candidate_orphan(
                    found, allowed_exts=allowed_exts, ignore_names=ignore_names
                ):
                    continue
                name = found.name
                if name.endswith(".part"):
                    # partials already handled when matching history still report orphans
                    resolved = str(found.resolve())
                    if resolved not in tracked_paths and str(found) not in tracked_paths:
                        result.issues.append(
                            repair_issue(
                                id=_issue_id("orphan-part", resolved),
                                kind="partial",
                                path=str(found),
                                actual_size=found.stat().st_size,
                                message="orphan .part file not linked to history",
                                suggested_actions=["cleanup_partials"],
                            )
                        )
                    continue
                resolved = str(found.resolve())
                if resolved in db_paths or str(found) in db_paths:
                    continue
                result.issues.append(
                    repair_issue(
                        id=_issue_id("orphan", resolved),
                        kind="orphan_file",
                        path=str(found),
                        actual_size=found.stat().st_size,
                        message="file on disk not present in history DB",
                        suggested_actions=["relink"],
                    )
                )

    return result


def update_file_path(
    conn: sqlite3.Connection,
    *,
    file_row_id: int,
    new_path: str | Path,
    recompute_hash: bool = True,
) -> dict[str, Any]:
    path = Path(new_path)
    size = path.stat().st_size if path.is_file() else None
    digest = (
        hash_file_for_history(path) if recompute_hash and path.is_file() else None
    )
    conn.execute(
        """
        update history_files
        set path = ?,
            size = coalesce(?, size),
            sha256 = coalesce(?, sha256),
            name = coalesce(?, name),
            status = case when ? then 'done' else status end,
            updated_at = datetime('now')
        where id = ?
        """,
        (
            str(path),
            size,
            digest,
            path.name if path.name else None,
            1 if path.is_file() else 0,
            file_row_id,
        ),
    )
    conn.commit()
    return {
        "file_row_id": file_row_id,
        "path": str(path),
        "size": size,
        "sha256": digest,
    }


def remove_file_record(conn: sqlite3.Connection, file_row_id: int) -> bool:
    cur = conn.execute("delete from history_files where id = ?", (file_row_id,))
    conn.commit()
    return cur.rowcount > 0


def cleanup_partial_files(paths: list[str | Path]) -> dict[str, Any]:
    removed: list[str] = []
    missing: list[str] = []
    errors: list[dict[str, str]] = []
    for raw in paths:
        path = Path(raw)
        targets = [path]
        if not str(path).endswith(".part"):
            targets.append(Path(str(path) + ".part"))
        for target in targets:
            if not target.is_file():
                if target == path:
                    missing.append(str(target))
                continue
            if not target.name.endswith(".part"):
                continue
            try:
                target.unlink()
                removed.append(str(target))
            except OSError as exc:
                errors.append({"path": str(target), "error": str(exc)})
    return {"removed": removed, "missing": missing, "errors": errors}


def requeue_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    output_dir: str | None = None,
) -> dict[str, Any] | None:
    entry = get_history(conn, job_id)
    if entry is None:
        return None
    url = entry.source_url or entry.canonical_url
    if not url:
        return None
    new_id = str(uuid.uuid4())
    out = output_dir or entry.output_dir or "."
    job = queue_job(
        id=new_id,
        url=url,
        output_dir=str(out),
        status="queued",
        site=entry.site,
        title=entry.title,
        metadata=entry.to_dict(),
    )
    save_queue_job(conn, job)
    return job.to_dict()


def apply_repair_actions(
    conn: sqlite3.Connection,
    *,
    action: str,
    issues: list[dict[str, Any]] | None = None,
    job_ids: list[str] | None = None,
    file_row_ids: list[int] | None = None,
    relink_path: str | None = None,
    output_dir: str | None = None,
    extract_password: str | None = None,
    add_password_to_list: bool = False,
    password_label: str = "",
) -> dict[str, Any]:
    """apply one repair action to selected issues / ids."""
    action = action.replace("-", "_")
    issues = issues or []
    results: dict[str, Any] = {"action": action, "ok": [], "errors": []}

    if action == "retry_extract":
        from udl.postprocess import zip_passwords as zp
        from udl.postprocess.zip_extract import extract_options, retry_extract_path

        targets: list[dict[str, Any]] = []
        for issue in issues:
            if issue.get("path"):
                targets.append(issue)
        if not targets and file_row_ids:
            for rid in file_row_ids:
                row = conn.execute(
                    "select path, metadata_json, job_id, file_id from history_files where id = ?",
                    (rid,),
                ).fetchone()
                if row:
                    targets.append(
                        {
                            "path": row["path"],
                            "file_row_id": rid,
                            "job_id": row["job_id"],
                            "file_id": row["file_id"],
                            "metadata_json": row["metadata_json"],
                        }
                    )
        for issue in targets:
            archive = issue.get("path")
            if not archive:
                results["errors"].append({"error": "missing archive path"})
                continue
            try:
                meta = json.loads(issue.get("metadata_json") or "{}")
            except json.JSONDecodeError:
                meta = {}
            if not meta and issue.get("file_row_id") is not None:
                row = conn.execute(
                    "select metadata_json from history_files where id = ?",
                    (int(issue["file_row_id"]),),
                ).fetchone()
                if row:
                    try:
                        meta = json.loads(row["metadata_json"] or "{}")
                    except json.JSONDecodeError:
                        meta = {}
            extra = meta.get("extra") if isinstance(meta, dict) else {}
            prev = extra.get("extract") if isinstance(extra, dict) else {}
            dest = "subfolder"
            if isinstance(prev, dict) and prev.get("dest"):
                # infer destination mode from previous dest vs archive parent/stem
                archive_path = Path(str(archive))
                prev_dest = Path(str(prev["dest"]))
                if prev_dest == archive_path.parent:
                    dest = "same_folder"
            # one-off password tried first then global list (list mode prepend)
            opts = extract_options(
                enabled=True,
                override="on",
                destination=dest,  # type: ignore[arg-type]
                password=extract_password,
                password_mode="list",
                use_secret_password=True,
            )
            extracted = retry_extract_path(
                archive,
                opts,
                password=extract_password,
                password_mode="list",
                job=str(issue["job_id"]) if issue.get("job_id") else None,
                file=str(issue["file_id"]) if issue.get("file_id") else None,
            )
            if issue.get("file_row_id") is not None:
                if not isinstance(meta, dict):
                    meta = {}
                extra = dict(meta.get("extra") or {})
                # strip any accidental password fields before persistence
                extract_payload = extracted.to_dict()
                extract_payload.pop("password", None)
                extra["extract"] = extract_payload
                meta["extra"] = extra
                conn.execute(
                    """
                    update history_files
                    set metadata_json = ?,
                        status = ?,
                        updated_at = datetime('now')
                    where id = ?
                    """,
                    (
                        json.dumps(meta, ensure_ascii=False),
                        "done" if extracted.ok else "error",
                        int(issue["file_row_id"]),
                    ),
                )
                conn.commit()
            if extracted.ok:
                added = None
                if add_password_to_list and extract_password:
                    try:
                        added = zp.add_password(
                            extract_password,
                            label=password_label or "",
                        )
                    except ValueError as exc:
                        results["errors"].append(
                            {"path": archive, "error": str(exc), "code": "password_add"}
                        )
                results["ok"].append(
                    {
                        "path": archive,
                        "extract": extracted.to_dict(),
                        "password_added": added,
                    }
                )
            else:
                results["errors"].append(
                    {
                        "path": archive,
                        "error": extracted.message or extracted.code or "extract failed",
                        "code": extracted.code,
                    }
                )
        return results

    if action == "requeue":
        ids = list(job_ids or [])
        for issue in issues:
            if issue.get("job_id"):
                ids.append(str(issue["job_id"]))
        seen: set[str] = set()
        for jid in ids:
            if jid in seen:
                continue
            seen.add(jid)
            queued = requeue_job(conn, job_id=jid, output_dir=output_dir)
            if queued:
                results["ok"].append({"job_id": jid, "queue": queued})
            else:
                results["errors"].append({"job_id": jid, "error": "not found or no url"})
        return results

    if action == "relink":
        if not relink_path:
            results["errors"].append({"error": "relink_path required"})
            return results
        row_ids = list(file_row_ids or [])
        for issue in issues:
            if issue.get("file_row_id") is not None:
                row_ids.append(int(issue["file_row_id"]))
        if not row_ids:
            results["errors"].append({"error": "no file_row_id for relink"})
            return results
        for rid in row_ids:
            try:
                results["ok"].append(update_file_path(conn, file_row_id=rid, new_path=relink_path))
            except OSError as exc:
                results["errors"].append({"file_row_id": rid, "error": str(exc)})
        return results

    if action == "remove_stale":
        row_ids = list(file_row_ids or [])
        job_delete = list(job_ids or [])
        for issue in issues:
            if issue.get("file_row_id") is not None:
                row_ids.append(int(issue["file_row_id"]))
            elif issue.get("job_id") and issue.get("kind") == "stale_record":
                job_delete.append(str(issue["job_id"]))
        for rid in row_ids:
            if remove_file_record(conn, rid):
                results["ok"].append({"file_row_id": rid, "removed": True})
            else:
                results["errors"].append({"file_row_id": rid, "error": "not found"})
        if job_delete:
            results["ok"].append(delete_history(conn, list(dict.fromkeys(job_delete))))
        return results

    if action == "cleanup_partials":
        paths: list[str] = []
        for issue in issues:
            if issue.get("path"):
                paths.append(str(issue["path"]))
        # also accept explicit paths via job scan of .part next to missing
        for issue in issues:
            if issue.get("kind") == "missing_file" and issue.get("path"):
                paths.append(str(issue["path"]) + ".part")
        results["ok"].append(cleanup_partial_files(paths))
        return results

    results["errors"].append({"error": f"unknown action: {action}"})
    return results
