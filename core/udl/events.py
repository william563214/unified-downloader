"""JSON-lines progress event protocol for CLI and GUI sidecar."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, TextIO

from udl.metadata import metadata_schema

# event type names (lowercase per project naming)
ev_job = "job"
ev_file = "file"
ev_progress = "progress"
ev_merging = "merging"
ev_postprocess = "postprocess"
ev_checksum = "checksum"
ev_hook = "hook"
ev_extracting = "extracting"
ev_extracted = "extracted"
ev_failed = "failed"
ev_page = "page"
ev_done = "done"
ev_error = "error"
ev_cancel = "cancel"
ev_resolve = "resolve"
ev_probe = "probe"
ev_duplicate = "duplicate"
ev_expand = "expand"


@dataclass
class event:
    """one JSON-lines event object."""

    ev: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"ev": self.ev}
        out.update(self.data)
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class emitter:
    """writes one JSON object per line to a stream."""

    def __init__(self, stream: TextIO | None = None, *, enabled: bool = True) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._enabled = enabled

    def emit(self, ev: str, **fields: Any) -> None:
        if not self._enabled:
            return
        line = event(ev=ev, data=fields).to_json()
        self._stream.write(line + "\n")
        self._stream.flush()

    def job(
        self,
        *,
        id: str,
        site: str,
        title: str | None = None,
        url: str | None = None,
        files: int | None = None,
        metadata: dict[str, Any] | None = None,
        already_downloaded: bool | None = None,
    ) -> None:
        payload: dict[str, Any] = {"id": id, "site": site}
        if title is not None:
            payload["title"] = title
        if url is not None:
            payload["url"] = url
        if files is not None:
            payload["files"] = files
        if metadata is not None:
            payload["metadata"] = metadata
        if already_downloaded is not None:
            payload["already_downloaded"] = already_downloaded
        self.emit(ev_job, **payload)

    def file(
        self,
        *,
        job: str,
        id: str,
        name: str,
        total: int | None = None,
        path: str | None = None,
        index: int | None = None,
        metadata: dict[str, Any] | None = None,
        already_downloaded: bool | None = None,
        role: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job, "id": id, "name": name}
        if total is not None:
            payload["total"] = total
        if path is not None:
            payload["path"] = path
        if index is not None:
            payload["index"] = index
        if metadata is not None:
            payload["metadata"] = metadata
        if already_downloaded is not None:
            payload["already_downloaded"] = already_downloaded
        if role is not None:
            payload["role"] = role
        self.emit(ev_file, **payload)

    def progress(
        self,
        *,
        job: str,
        file: str,
        bytes: int,
        total: int | None = None,
        bps: float | None = None,
        segments: list[dict[str, Any]] | None = None,
        connections: int | None = None,
        fragment_index: int | None = None,
        fragment_count: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job, "file": file, "bytes": bytes}
        if total is not None:
            payload["total"] = total
        if bps is not None:
            payload["bps"] = round(bps, 1)
        if segments is not None:
            payload["segments"] = segments
        if connections is not None:
            payload["connections"] = connections
        if fragment_index is not None:
            payload["fragment_index"] = fragment_index
        if fragment_count is not None:
            payload["fragment_count"] = fragment_count
        self.emit(ev_progress, **payload)

    def merging(
        self,
        *,
        job: str,
        file: str | None = None,
        path: str | None = None,
        message: str | None = None,
    ) -> None:
        """DASH / postprocess merge phase (e.g. FFmpeg video+audio)."""
        payload: dict[str, Any] = {"job": job}
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if message is not None:
            payload["message"] = message
        self.emit(ev_merging, **payload)

    def postprocess(
        self,
        *,
        job: str,
        step: str,
        file: str | None = None,
        path: str | None = None,
        role: str | None = None,
        message: str | None = None,
        ok: bool | None = None,
        code: str | None = None,
    ) -> None:
        """NFO / cover / embed and other post-download artifact steps."""
        payload: dict[str, Any] = {"job": job, "step": step}
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if role is not None:
            payload["role"] = role
        if message is not None:
            payload["message"] = message
        if ok is not None:
            payload["ok"] = ok
        if code is not None:
            payload["code"] = code
        self.emit(ev_postprocess, **payload)

    def checksum(
        self,
        *,
        job: str,
        file: str | None = None,
        ok: bool,
        algo: str,
        expected: str,
        actual: str | None = None,
        source: str | None = None,
        path: str | None = None,
        message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "job": job,
            "ok": ok,
            "algo": algo,
            "expected": expected,
        }
        if file is not None:
            payload["file"] = file
        if actual is not None:
            payload["actual"] = actual
        if source is not None:
            payload["source"] = source
        if path is not None:
            payload["path"] = path
        if message is not None:
            payload["message"] = message
        self.emit(ev_checksum, **payload)

    def extracting(
        self,
        *,
        job: str,
        file: str | None = None,
        path: str | None = None,
        dest: str | None = None,
        message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job}
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if dest is not None:
            payload["dest"] = dest
        if message is not None:
            payload["message"] = message
        self.emit(ev_extracting, **payload)

    def extracted(
        self,
        *,
        job: str,
        file: str | None = None,
        path: str | None = None,
        dest: str | None = None,
        members: int | None = None,
        deleted_archive: bool | None = None,
        message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job}
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if dest is not None:
            payload["dest"] = dest
        if members is not None:
            payload["members"] = members
        if deleted_archive is not None:
            payload["deleted_archive"] = deleted_archive
        if message is not None:
            payload["message"] = message
        self.emit(ev_extracted, **payload)

    def failed(
        self,
        *,
        job: str | None = None,
        file: str | None = None,
        path: str | None = None,
        dest: str | None = None,
        code: str | None = None,
        message: str | None = None,
        phase: str | None = None,
    ) -> None:
        """feature-specific failure (e.g. phase=extract). also emit error for GUI."""
        payload: dict[str, Any] = {}
        if job is not None:
            payload["job"] = job
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if dest is not None:
            payload["dest"] = dest
        if code is not None:
            payload["code"] = code
        if message is not None:
            payload["message"] = message
        if phase is not None:
            payload["phase"] = phase
        self.emit(ev_failed, **payload)

    def page(self, **fields: Any) -> None:
        """page scan result for Download All."""
        self.emit(ev_page, **fields)

    def probe(self, **fields: Any) -> None:
        """rich metadata for GUI selectors (parts / qualities)."""
        self.emit(ev_probe, **fields)

    def expand(
        self,
        *,
        phase: str,
        current: int,
        total: int,
        message: str | None = None,
    ) -> None:
        """list pagination / nested expand progress for bilibili one-click."""
        payload: dict[str, Any] = {
            "phase": phase,
            "current": current,
            "total": total,
        }
        if message is not None:
            payload["message"] = message
        self.emit(ev_expand, **payload)

    def duplicate(
        self,
        *,
        job: str,
        file: str | None = None,
        action: str,
        message: str | None = None,
        path: str | None = None,
        matched_by: str | None = None,
        already_downloaded: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "job": job,
            "action": action,
            "already_downloaded": already_downloaded,
        }
        if file is not None:
            payload["file"] = file
        if message is not None:
            payload["message"] = message
        if path is not None:
            payload["path"] = path
        if matched_by is not None:
            payload["matched_by"] = matched_by
        if metadata is not None:
            payload["metadata"] = metadata
        self.emit(ev_duplicate, **payload)

    def done(
        self,
        *,
        job: str,
        file: str | None = None,
        path: str | None = None,
        ok: int | None = None,
        failed: int | None = None,
        skipped: bool | None = None,
        already_downloaded: bool | None = None,
        metadata: dict[str, Any] | None = None,
        sha256: str | None = None,
        size: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job": job}
        if file is not None:
            payload["file"] = file
        if path is not None:
            payload["path"] = path
        if ok is not None:
            payload["ok"] = ok
        if failed is not None:
            payload["failed"] = failed
        if skipped is not None:
            payload["skipped"] = skipped
        if already_downloaded is not None:
            payload["already_downloaded"] = already_downloaded
        if metadata is not None:
            payload["metadata"] = metadata
        if sha256 is not None:
            payload["sha256"] = sha256
        if size is not None:
            payload["size"] = size
        self.emit(ev_done, **payload)

    def error(
        self,
        *,
        job: str | None = None,
        file: str | None = None,
        message: str,
        code: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"message": message}
        if job is not None:
            payload["job"] = job
        if file is not None:
            payload["file"] = file
        if code is not None:
            payload["code"] = code
        self.emit(ev_error, **payload)

    def cancel(self, *, job: str) -> None:
        self.emit(ev_cancel, job=job)

    def resolve(
        self,
        *,
        site: str | None,
        kind: str | None,
        url: str,
        supported: bool,
        message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "site": site,
            "kind": kind,
            "url": url,
            "supported": supported,
        }
        if message is not None:
            payload["message"] = message
        self.emit(ev_resolve, **payload)


def dump_schema() -> dict[str, Any]:
    """return a machine-readable overview of event fields."""
    meta = metadata_schema()
    return {
        "format": "newline-delimited JSON (JSON-lines) on stdout",
        "metadata": meta,
        "events": {
            ev_resolve: {
                "site": "str|null",
                "kind": "str|null",
                "url": "str",
                "supported": "bool",
                "message": "str?",
            },
            ev_job: {
                "id": "str",
                "site": "str",
                "title": "str?",
                "url": "str?",
                "files": "int?",
                "metadata": "object?",
                "already_downloaded": "bool?",
            },
            ev_file: {
                "job": "str",
                "id": "str",
                "name": "str",
                "total": "int?",
                "path": "str?",
                "index": "int?",
                "metadata": "object?",
                "already_downloaded": "bool?",
                "role": "str? (media|nfo|cover)",
            },
            ev_progress: {
                "job": "str",
                "file": "str",
                "bytes": "int",
                "total": "int?",
                "bps": "float?",
                "segments": "list? (id/start/end/current/downloaded/total/done)",
                "connections": "int?",
                "fragment_index": "int? (HLS/DASH fragment progress)",
                "fragment_count": "int?",
            },
            ev_merging: {
                "job": "str",
                "file": "str?",
                "path": "str?",
                "message": "str?",
            },
            ev_postprocess: {
                "job": "str",
                "step": "str (nfo|cover|embed_cover|hook|extracting|extracted|failed|skip)",
                "file": "str?",
                "path": "str?",
                "role": "str? (nfo|cover|hook|extract)",
                "message": "str?",
                "ok": "bool?",
                "code": "str?",
            },
            ev_checksum: {
                "job": "str",
                "file": "str?",
                "ok": "bool",
                "algo": "md5|sha1|sha256",
                "expected": "str",
                "actual": "str?",
                "source": "str?",
                "path": "str?",
                "message": "str?",
            },
            ev_hook: {
                "job": "str?",
                "file": "str?",
                "path": "str?",
                "ok": "bool",
                "command": "str",
                "exit_code": "int?",
                "duration_ms": "int?",
                "timed_out": "bool?",
                "message": "str?",
                "stdout": "str?",
                "stderr": "str?",
            },
            ev_extracting: {
                "job": "str",
                "file": "str?",
                "path": "str?",
                "dest": "str?",
                "message": "str?",
            },
            ev_extracted: {
                "job": "str",
                "file": "str?",
                "path": "str?",
                "dest": "str?",
                "members": "int?",
                "deleted_archive": "bool?",
                "message": "str?",
            },
            ev_failed: {
                "job": "str?",
                "file": "str?",
                "path": "str?",
                "dest": "str?",
                "code": "str?",
                "message": "str?",
                "phase": "str? (extract)",
            },
            ev_page: {
                "url": "str",
                "title": "str?",
                "count": "int",
                "candidates": "list",
                "message": "str?",
            },
            ev_duplicate: {
                "job": "str",
                "file": "str?",
                "action": "skip|overwrite|rename",
                "already_downloaded": "bool",
                "message": "str?",
                "path": "str?",
                "matched_by": "str?",
                "metadata": "object?",
            },
            ev_probe: {
                "site": "str",
                "kind": "str",
                "title": "str",
                "url": "str",
                "parts": "list?",
                "qualities": "list?",
                "default_part": "int?",
                "default_quality": "int?",
                "message": "str?",
                "metadata": "object?",
            },
            ev_expand: {
                "phase": "str",
                "current": "int",
                "total": "int",
                "message": "str?",
            },
            ev_done: {
                "job": "str",
                "file": "str?",
                "path": "str?",
                "ok": "int?",
                "failed": "int?",
                "skipped": "bool?",
                "already_downloaded": "bool?",
                "metadata": "object?",
                "sha256": "str?",
                "size": "int?",
            },
            ev_error: {
                "job": "str?",
                "file": "str?",
                "message": "str",
                "code": "str?",
            },
            ev_cancel: {"job": "str"},
        },
    }


# silence unused import lint for asdict when schema helpers grow
_ = asdict
